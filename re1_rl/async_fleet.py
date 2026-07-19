"""Desync actor fleet + central inference learner (default training path)."""

from __future__ import annotations

import multiprocessing as mp
import time
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from re1_rl.reward import RL_GAMMA

PPO_HYPERPARAMS: dict[str, Any] = dict(
    n_steps=1024,
    batch_size=512,
    n_epochs=4,
    learning_rate=3e-4,
    gamma=RL_GAMMA,
    ent_coef=0.02,
)

# Distributed 6-minute sync epochs: larger on-policy batches, gentler updates.
# Used only by ``scripts/distributed_train_parallel.py`` (not monolithic async).
DISTRIBUTED_EPOCH_HYPERPARAMS: dict[str, Any] = dict(
    n_steps=1024,
    batch_size=8192,  # burn VRAM on ~200k-step fleet epochs (keep LR 1e-4)
    n_epochs=4,
    learning_rate=1e-4,
    gamma=RL_GAMMA,
    ent_coef=0.02,
)
DEFAULT_SYNC_INTERVAL_S = 360.0


def _obs_batch_for_one(obs: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: np.expand_dims(v, 0) for k, v in obs.items()}


def _obs_batch_for_many(need_msgs: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    """Stack per-env obs dicts into one batch (n_envs, ...)."""
    if not need_msgs:
        raise ValueError("empty need_msgs")
    parts = [_obs_batch_for_one(msg["obs"]) for msg in need_msgs]
    return {key: np.concatenate([part[key] for part in parts], axis=0) for key in parts[0]}


def _serve_needs_batch(
    pairs: list[tuple[Connection, dict[str, Any]]],
    policy: Any,
    *,
    max_batch: int = 32,
) -> None:
    """Answer one or more actor ``need`` messages with batched inference."""
    if not pairs:
        return
    chunk_size = max(1, int(max_batch))
    for start in range(0, len(pairs), chunk_size):
        chunk = pairs[start : start + chunk_size]
        msgs = [msg for _, msg in chunk]
        obs_batch = _obs_batch_for_many(msgs)
        masks_list = [msg.get("action_masks") for msg in msgs]
        if any(m is None for m in masks_list):
            for conn, msg in chunk:
                obs_one = _obs_batch_for_one(msg["obs"])
                masks = msg.get("action_masks")
                policy_version = int(getattr(policy, "policy_version", 0) or 0)
                if masks is not None:
                    act, val, lp = policy.predict_masked(
                        obs_one, np.asarray(masks, dtype=bool)
                    )
                else:
                    act_a, val_a, lp_a = policy.predict_batch(obs_one)
                    act, val, lp = int(act_a[0]), float(val_a[0]), float(lp_a[0])
                conn.send(
                    {
                        "t": "act",
                        "action": act,
                        "value": val,
                        "logprob": lp,
                        "policy_version": policy_version,
                    }
                )
            continue
        masks = np.asarray(masks_list, dtype=bool)
        actions, values, log_probs = policy.predict_masked_batch(obs_batch, masks)
        policy_version = int(getattr(policy, "policy_version", 0) or 0)
        for i, (conn, _) in enumerate(chunk):
            conn.send(
                {
                    "t": "act",
                    "action": int(actions[i]),
                    "value": float(values[i]),
                    "logprob": float(log_probs[i]),
                    "policy_version": policy_version,
                }
            )


def _drain_actor_messages(
    ready: list[Connection],
    all_conns: list[Connection],
    *,
    max_need_batch: int,
    batch_window_s: float = 0.002,
) -> tuple[list[tuple[Connection, dict[str, Any]]], list[tuple[Connection, dict[str, Any]]]]:
    """Collect ``need`` / ``rollout`` messages; briefly coalesce stray needs."""
    needs: list[tuple[Connection, dict[str, Any]]] = []
    rollouts: list[tuple[Connection, dict[str, Any]]] = []

    def _take(conn: Connection) -> None:
        while conn.poll():
            msg = conn.recv()
            kind = msg.get("t")
            if kind == "need":
                needs.append((conn, msg))
            elif kind == "rollout":
                rollouts.append((conn, msg))

    for conn in ready:
        _take(conn)

    if (
        needs
        and batch_window_s > 0
        and len(needs) < max(1, int(max_need_batch))
    ):
        deadline = time.monotonic() + batch_window_s
        while time.monotonic() < deadline and len(needs) < max(1, int(max_need_batch)):
            got = False
            for conn in all_conns:
                if conn.poll():
                    got = True
                    msg = conn.recv()
                    kind = msg.get("t")
                    if kind == "need":
                        needs.append((conn, msg))
                    elif kind == "rollout":
                        rollouts.append((conn, msg))
            if not got:
                time.sleep(0.0002)

    return needs, rollouts

def _policy_obs_and_act_spaces():
    from re1_rl.distributed.spaces import make_re1_policy_spaces

    return make_re1_policy_spaces()


def _checkpoint_spaces_compatible(model) -> bool:
    policy_obs, act_space = _policy_obs_and_act_spaces()
    loaded_keys = set(model.observation_space.spaces.keys())
    current_keys = set(policy_obs.spaces.keys())
    if loaded_keys != current_keys:
        return False
    if int(model.action_space.n) != int(act_space.n):
        return False
    for key, space in policy_obs.spaces.items():
        if tuple(model.observation_space.spaces[key].shape) != tuple(space.shape):
            return False
    return True


def _copy_compatible_policy_weights(src_policy, dst_policy) -> int:
    """Copy compatible tensors, expanding a legacy action head safely.

    ``strict=False`` still errors on shape mismatches for shared keys; filter first.
    New action rows inherit ``attack`` semantics with a 100x lower prior.
    """
    from re1_rl.action_mask import ATTACK_ACTION

    src = src_policy.state_dict()
    dst = dst_policy.state_dict()
    filtered = {
        k: v for k, v in src.items()
        if k in dst and tuple(dst[k].shape) == tuple(v.shape)
    }
    for key in ("action_net.weight", "action_net.bias"):
        if key not in src or key not in dst:
            continue
        old = src[key]
        new = dst[key]
        if old.ndim != new.ndim or old.shape[0] >= new.shape[0]:
            continue
        if old.ndim == 2 and old.shape[1:] != new.shape[1:]:
            continue
        expanded = new.clone()
        expanded[: old.shape[0]] = old
        expanded[old.shape[0] :] = old[ATTACK_ACTION]
        if old.ndim == 1:
            expanded[old.shape[0] :] -= float(np.log(100.0))
        filtered[key] = expanded
    dst_policy.load_state_dict(filtered, strict=False)
    return len(filtered)


def _transplant_into_current_spaces(model, *, tb_log: str | None, hp: dict):
    from sb3_contrib import MaskablePPO

    from re1_rl.distributed.weights import _SpaceHolderEnv

    policy_obs, act_space = _policy_obs_and_act_spaces()
    fresh = MaskablePPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs, act_space),
        **hp,
    )
    n_copied = _copy_compatible_policy_weights(model.policy, fresh.policy)
    fresh.num_timesteps = int(model.num_timesteps)
    print(
        "[train:async] checkpoint obs/action layout mismatch; "
        f"transplanted {n_copied} compatible tensors into current architecture",
        flush=True,
    )
    return fresh


def _reload_world_catalog_buffers_if_needed(model) -> None:
    from re1_rl.features_extractor import RE1WorldAwareExtractor, reload_world_catalog_buffers

    extractor = model.policy.features_extractor
    if isinstance(extractor, RE1WorldAwareExtractor):
        reload_world_catalog_buffers(model.policy)
        print("[train:async] reloaded world catalog buffers from data files", flush=True)


def load_async_learner(*, device: str, resume: Path | None, tb_log: str | None):
    """MaskablePPO learner shell; accepts PPO or MaskablePPO checkpoint zips."""
    from sb3_contrib import MaskablePPO
    from stable_baselines3 import PPO

    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.policy_config import POLICY_KWARGS

    hp = {**PPO_HYPERPARAMS, "verbose": 1, "device": device, "policy_kwargs": POLICY_KWARGS}
    if tb_log:
        hp["tensorboard_log"] = tb_log

    def _fresh_maskable(obs_space=None, act_space=None):
        policy_obs_space, default_act = _policy_obs_and_act_spaces()
        return MaskablePPO(
            "MultiInputPolicy",
            _SpaceHolderEnv(
                obs_space if obs_space is not None else policy_obs_space,
                act_space if act_space is not None else default_act,
            ),
            **hp,
        )

    if resume is not None and resume.is_file():
        loaded = None
        load_kind = "MaskablePPO"
        try:
            loaded = MaskablePPO.load(str(resume), device=device)
            load_kind = "MaskablePPO"
        except (TypeError, ValueError, RuntimeError):
            try:
                plain = PPO.load(str(resume), device=device)
                loaded = _fresh_maskable(plain.observation_space, plain.action_space)
                _copy_compatible_policy_weights(plain.policy, loaded.policy)
                loaded.num_timesteps = int(plain.num_timesteps)
                load_kind = "PPO"
            except (TypeError, ValueError, RuntimeError) as exc:
                raise RuntimeError(f"failed to load resume checkpoint {resume}") from exc

        if tb_log:
            loaded.tensorboard_log = tb_log
        print(
            f"[train:async] resumed {load_kind} into MaskablePPO from {resume} "
            f"(num_timesteps={loaded.num_timesteps})",
            flush=True,
        )
        if not _checkpoint_spaces_compatible(loaded):
            loaded = _transplant_into_current_spaces(
                loaded, tb_log=tb_log, hp=hp,
            )
        _reload_world_catalog_buffers_if_needed(loaded)
        return loaded

    return _fresh_maskable()


def _actor_process(
    rank: int,
    conn: Connection,
    *,
    curriculum: str,
    base_port: int,
    training_speed: int,
    skip_chunk: int,
    n_steps: int,
    stop_flag: mp.synchronize.Synchronized,
    capture_checkpoints: bool,
    headless: bool = True,
    screenshot_mmf: bool | None = None,
) -> None:
    from scripts.train_parallel import make_env
    from re1_rl.training_progress import slim_progress_info

    try:
        env = make_env(
            rank,
            curriculum,
            base_port,
            capture_checkpoints,
            training_speed=training_speed,
            skip_chunk=skip_chunk,
            async_cutscene_skip=True,
            headless=headless,
            screenshot_mmf=screenshot_mmf,
            spawn_progress=lambda phase: conn.send(
                {"t": "spawn_progress", "rank": rank, "phase": phase}
            ),
        )()
    except Exception as exc:
        conn.send({"t": "spawn_error", "rank": rank, "error": repr(exc)})
        raise
    conn.send({"t": "spawned", "rank": rank})

    msg = conn.recv()
    if msg.get("t") == "stop":
        env.close()
        return
    if msg.get("t") != "start":
        env.close()
        return

    obs, _ = env.reset()

    obs_bufs: dict[str, np.ndarray] | None = None
    mask_bufs: np.ndarray | None = None
    actions = np.zeros(n_steps, dtype=np.int64)
    rewards = np.zeros(n_steps, dtype=np.float32)
    dones = np.zeros(n_steps, dtype=np.bool_)
    values = np.zeros(n_steps, dtype=np.float32)
    log_probs = np.zeros(n_steps, dtype=np.float32)
    episode_infos: list[dict[str, Any]] = []
    step_i = 0
    horizon_policy_version = 0

    def _reset_bufs() -> None:
        nonlocal obs_bufs, mask_bufs, step_i, episode_infos, horizon_policy_version
        obs_bufs = {
            k: np.zeros((n_steps, *env.observation_space[k].shape), dtype=env.observation_space[k].dtype)
            for k in env.observation_space.spaces
        }
        n_actions = int(env.action_space.n)
        mask_bufs = np.zeros((n_steps, n_actions), dtype=np.bool_)
        step_i = 0
        episode_infos = []
        horizon_policy_version = 0

    def _emit_rollout(n: int) -> None:
        assert obs_bufs is not None and mask_bufs is not None
        conn.send(
            {
                "t": "rollout",
                "rank": rank,
                "n_steps": int(n),
                "obs": {k: v[:n].copy() for k, v in obs_bufs.items()},
                "actions": actions[:n].copy(),
                "rewards": rewards[:n].copy(),
                "dones": dones[:n].copy(),
                "values": values[:n].copy(),
                "log_probs": log_probs[:n].copy(),
                "action_masks": mask_bufs[:n].copy(),
                "policy_version": horizon_policy_version,
                "last_obs": obs,
                "episode_infos": episode_infos,
            }
        )

    _reset_bufs()

    try:
        while not stop_flag.value:
            req: dict[str, Any] = {"t": "need", "rank": rank, "obs": obs}
            masks_now = None
            if hasattr(env, "action_masks"):
                masks_now = np.asarray(env.action_masks(), dtype=bool)
                req["action_masks"] = masks_now
            conn.send(req)
            msg = conn.recv()
            if msg.get("t") == "stop":
                break
            if msg.get("t") != "act":
                continue

            action = int(msg["action"])
            value = float(msg["value"])
            logprob = float(msg["logprob"])
            if step_i == 0:
                horizon_policy_version = int(msg.get("policy_version", 0) or 0)

            obs_before = obs
            masks_before = masks_now
            # Top-right memlog (RE1_STEP_DIAG_PORT): stash critic V for this step.
            try:
                _diag = getattr(getattr(env, "unwrapped", env), "_step_diag", None)
                if _diag is not None:
                    _diag.note_value(value)
            except (AttributeError, TypeError, ValueError):
                pass
            obs, rew, done, trunc, info = env.step(action)
            if info:
                episode_infos.append(slim_progress_info(info))

            # Exclude pure cutscene-skip ticks from the PPO buffer (zero reward,
            # frozen obs). Post-skip credit lands on the next live control step.
            if info.get("cutscene_skip") and not (done or trunc):
                continue

            assert obs_bufs is not None and mask_bufs is not None
            for key in obs_bufs:
                obs_bufs[key][step_i] = obs_before[key]
            if masks_before is None:
                masks_before = np.ones(int(env.action_space.n), dtype=bool)
            mask_bufs[step_i] = masks_before
            actions[step_i] = action
            values[step_i] = value
            log_probs[step_i] = logprob
            rewards[step_i] = float(rew)
            dones[step_i] = bool(done or trunc)
            step_i += 1

            if done or trunc:
                if step_i > 0:
                    _emit_rollout(step_i)
                    _reset_bufs()
                obs, _ = env.reset()
            elif step_i >= n_steps:
                _emit_rollout(n_steps)
                _reset_bufs()
    finally:
        try:
            env.close()
        except Exception:
            pass


def _wait_for_actor_spawn(
    conns: list[Connection],
    n_envs: int,
    *,
    processes: list[mp.Process] | None = None,
    timeout_s: float = 600.0,
) -> None:
    spawned: set[int] = set()
    errors: dict[int, str] = {}
    deadline = time.perf_counter() + timeout_s
    last_report = 0.0
    while len(spawned) < n_envs:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            missing = sorted(set(range(n_envs)) - spawned)
            err_lines = [f"  rank {r}: {errors[r]}" for r in sorted(errors)]
            detail = "\n".join(err_lines) if err_lines else ""
            raise TimeoutError(
                f"timed out waiting for actors {missing}"
                + (f"\n{detail}" if detail else "")
            )
        if processes and time.perf_counter() - last_report >= 10.0:
            alive = sum(1 for p in processes if p.is_alive())
            dead = [i for i, p in enumerate(processes) if not p.is_alive() and i not in spawned]
            print(
                f"[train:async] warmup {len(spawned)}/{n_envs} spawned, "
                f"{alive} actors alive"
                + (f", dead ranks {dead}" if dead else ""),
                flush=True,
            )
            last_report = time.perf_counter()
        if processes:
            for i, proc in enumerate(processes):
                if i in spawned or proc.is_alive():
                    continue
                proc.join(timeout=0)
                raise RuntimeError(
                    f"actor {i} died during warmup (exit={proc.exitcode}); "
                    f"see [actor {i}] lines above if printed"
                )
        ready = wait(conns, timeout=min(1.0, remaining))
        for conn in ready:
            if not conn.poll():
                continue
            msg = conn.recv()
            if msg.get("t") == "spawned":
                spawned.add(int(msg["rank"]))
            elif msg.get("t") == "spawn_error":
                r = int(msg["rank"])
                errors[r] = str(msg.get("error", "unknown"))
                print(f"[train:async] actor {r} spawn failed: {errors[r]}", flush=True)
            elif msg.get("t") == "spawn_progress":
                print(
                    f"[train:async] actor {int(msg['rank'])}: {msg.get('phase', '')}",
                    flush=True,
                )
    print(f"[train:async] all {n_envs} actors connected", flush=True)


def run_async_fleet_training(
    *,
    n_envs: int,
    train_steps: int,
    curriculum: str,
    base_port: int,
    training_speed: int,
    skip_chunk: int,
    capture_checkpoints: bool,
    resume_path: Path | None,
    ckpt_dir: Path,
    run_name: str | None,
    device: str,
    tb_log: str,
    headless: bool = True,
    screenshot_mmf: bool | None = None,
    inference_batch_max: int = 32,
) -> int:
    from re1_rl.checkpoint_io import (
        atomic_model_save,
        checkpoint_timestep_interval,
        write_latest_pointer,
    )
    from re1_rl.distributed.inference_policy import InferencePolicy
    from re1_rl.distributed.learner_train import train_on_rollouts
    from re1_rl.distributed.rollout_types import WorkerRollout
    from re1_rl.distributed.weights import export_policy_state_dict
    from re1_rl.training_metrics_log import (
        append_training_record,
        build_update_record,
        configure_training_logger,
        log_update_line,
        rollout_batch_reward_stats,
        training_metrics_jsonl_path,
    )
    from re1_rl.training_progress import TrainingProgressTracker

    n_steps = int(PPO_HYPERPARAMS["n_steps"])
    batch_threshold = n_steps * n_envs
    save_interval = checkpoint_timestep_interval(n_envs)
    model = load_async_learner(device=device, resume=resume_path, tb_log=tb_log)
    next_save = (model.num_timesteps // save_interval + 1) * save_interval

    tb_run_dir = Path(tb_log) / (run_name or "async")
    configure_training_logger(model, log_dir=tb_run_dir)
    metrics_jsonl = training_metrics_jsonl_path(PROJECT_ROOT, run_name=run_name)
    print(f"[train:async] metrics jsonl -> {metrics_jsonl}", flush=True)
    print(f"[train:async] tensorboard/csv -> {tb_run_dir}", flush=True)

    policy = InferencePolicy(model.observation_space, model.action_space, device)
    policy_version = 1
    policy.load_from_state_dict(export_policy_state_dict(model), policy_version)

    print(
        f"[train:async] {n_envs} desync actors, target={train_steps} steps, "
        f"batch_threshold={batch_threshold}, "
        f"checkpoint_every={save_interval} steps, headless={headless}, "
        f"screenshot_mmf={screenshot_mmf}, inference_batch_max={inference_batch_max}",
        flush=True,
    )

    stop_flag = mp.Value("b", False)
    ctx = mp.get_context("spawn")
    processes: list[mp.Process] = []
    parent_conns: list[Connection] = []

    warmup_t0 = time.perf_counter()
    print("[train:async] warming up fleet (spawn + BizHawk connect)...", flush=True)
    for rank in range(n_envs):
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=_actor_process,
            args=(rank, child_conn),
            kwargs={
                "curriculum": curriculum,
                "base_port": base_port,
                "training_speed": training_speed,
                "skip_chunk": skip_chunk,
                "n_steps": n_steps,
                "stop_flag": stop_flag,
                "capture_checkpoints": capture_checkpoints,
                "headless": headless,
                "screenshot_mmf": screenshot_mmf,
            },
            name=f"async-actor-{rank}",
        )
        proc.start()
        child_conn.close()
        processes.append(proc)
        parent_conns.append(parent_conn)

    _wait_for_actor_spawn(parent_conns, n_envs, processes=processes)
    print(f"[train:async] fleet ready in {time.perf_counter() - warmup_t0:.1f}s", flush=True)
    for conn in parent_conns:
        conn.send({"t": "start"})

    pending: list[WorkerRollout] = []
    pending_steps = 0
    n_updates = 0
    t0 = time.perf_counter()
    progress = TrainingProgressTracker(prefix="progress")

    try:
        while model.num_timesteps < train_steps and not stop_flag.value:
            ready = wait(parent_conns, timeout=1.0)
            if not ready:
                if not any(p.is_alive() for p in processes):
                    break
                continue

            needs, rollouts = _drain_actor_messages(
                ready,
                parent_conns,
                max_need_batch=inference_batch_max,
            )
            if needs:
                _serve_needs_batch(needs, policy, max_batch=inference_batch_max)
            for conn, msg in rollouts:
                rank = int(msg["rank"])
                last_values = policy.predict_values(_obs_batch_for_one(msg["last_obs"]))
                obs = {k: np.expand_dims(v, axis=1) for k, v in msg["obs"].items()}
                pending.append(
                    WorkerRollout(
                        worker_id=f"actor_{rank}",
                        policy_version=int(msg.get("policy_version", policy_version)),
                        n_envs=1,
                        n_steps=n_steps,
                        obs=obs,
                        actions=np.expand_dims(msg["actions"], 1),
                        rewards=np.expand_dims(msg["rewards"], 1),
                        dones=np.expand_dims(msg["dones"], 1),
                        values=np.expand_dims(msg["values"], 1),
                        log_probs=np.expand_dims(msg["log_probs"], 1),
                        last_values=last_values,
                        action_masks=np.expand_dims(
                            np.asarray(msg["action_masks"], dtype=np.bool_), 1
                        ),
                        episode_infos=list(msg.get("episode_infos") or []),
                    )
                )
                pending_steps += n_steps

            if pending_steps < batch_threshold:
                continue

            batch_rollouts = list(pending)
            batch_infos: list[dict[str, Any]] = []
            for rollout in batch_rollouts:
                batch_infos.extend(rollout.episode_infos)
            train_on_rollouts(model, batch_rollouts)
            progress.consume_infos(batch_infos, num_timesteps=int(model.num_timesteps))
            progress.log_rollout_end(
                model,
                num_timesteps=int(model.num_timesteps),
                episode_infos=batch_infos,
            )
            policy_version += 1
            policy.load_from_state_dict(export_policy_state_dict(model), policy_version)
            n_updates += 1
            pending.clear()
            pending_steps = 0

            elapsed = time.perf_counter() - t0
            rate = model.num_timesteps / elapsed if elapsed > 0 else 0.0
            record = build_update_record(
                model,
                update=n_updates,
                policy_version=policy_version,
                rate_steps_s=rate,
                extra={
                    "n_envs": n_envs,
                    "n_rollouts": len(batch_rollouts),
                    **rollout_batch_reward_stats(batch_rollouts),
                },
            )
            append_training_record(metrics_jsonl, record)
            log_update_line(record)

            while model.num_timesteps >= next_save:
                ckpt_path = ckpt_dir / f"ppo_re1_{next_save}_steps.zip"
                saved = atomic_model_save(model, ckpt_path)
                write_latest_pointer(ckpt_dir, saved)
                print(f"[train:async] checkpoint {saved}", flush=True)
                next_save += save_interval

    except KeyboardInterrupt:
        print("[train:async] interrupted", flush=True)
    finally:
        stop_flag.value = True
        for conn in parent_conns:
            try:
                conn.send({"t": "stop"})
            except (BrokenPipeError, OSError):
                pass
            conn.close()
        for proc in processes:
            proc.join(timeout=30)
            if proc.is_alive():
                proc.terminate()

        suffix = f"_{run_name}" if run_name else ""
        final_alias = PROJECT_ROOT / "data" / f"ppo_re1_final{suffix}.zip"
        try:
            from re1_rl.checkpoint_io import zip_path

            saved = atomic_model_save(model, zip_path(final_alias))
            write_latest_pointer(ckpt_dir, saved)
            print(f"[train:async] saved {saved}", flush=True)
        except OSError as exc:
            print(f"[train:async] WARNING: final save failed: {exc}", flush=True)

    return int(model.num_timesteps)
