"""Desync actor fleet + central inference learner (default training path)."""

from __future__ import annotations

import multiprocessing as mp
import time
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PPO_HYPERPARAMS: dict[str, Any] = dict(
    n_steps=256,
    batch_size=512,
    n_epochs=4,
    learning_rate=3e-4,
    gamma=0.99,
    ent_coef=0.01,
)

# Distributed 6-minute sync epochs: larger on-policy batches, gentler updates.
# Used only by ``scripts/distributed_train_parallel.py`` (not monolithic async).
DISTRIBUTED_EPOCH_HYPERPARAMS: dict[str, Any] = dict(
    n_steps=256,
    batch_size=2048,
    n_epochs=2,
    learning_rate=1e-4,
    gamma=0.99,
    ent_coef=0.01,
)
DEFAULT_SYNC_INTERVAL_S = 360.0


def _obs_batch_for_one(obs: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: np.expand_dims(v, 0) for k, v in obs.items()}


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
    """Copy tensors that exist in both policies with identical shapes.

    ``strict=False`` still errors on shape mismatches for shared keys; filter first.
    """
    src = src_policy.state_dict()
    dst = dst_policy.state_dict()
    filtered = {
        k: v for k, v in src.items()
        if k in dst and tuple(dst[k].shape) == tuple(v.shape)
    }
    dst_policy.load_state_dict(filtered, strict=False)
    return len(filtered)


def _transplant_into_current_spaces(model, *, tb_log: str | None, hp: dict):
    from stable_baselines3 import PPO

    from re1_rl.distributed.weights import _SpaceHolderEnv

    policy_obs, act_space = _policy_obs_and_act_spaces()
    fresh = PPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs, act_space),
        tensorboard_log=tb_log,
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


def load_async_learner(*, device: str, resume: Path | None, tb_log: str | None):
    """PPO learner shell; accepts PPO or MaskablePPO checkpoint zips."""
    from stable_baselines3 import PPO

    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.policy_config import POLICY_KWARGS

    hp = {**PPO_HYPERPARAMS, "verbose": 1, "device": device, "policy_kwargs": POLICY_KWARGS}
    if tb_log:
        hp["tensorboard_log"] = tb_log

    if resume is not None and resume.is_file():
        loaded = None
        try:
            loaded = PPO.load(str(resume), device=device)
            load_kind = "PPO"
        except (TypeError, ValueError, RuntimeError):
            try:
                from sb3_contrib import MaskablePPO

                maskable = MaskablePPO.load(str(resume), device=device)
                loaded = PPO(
                    "MultiInputPolicy",
                    _SpaceHolderEnv(maskable.observation_space, maskable.action_space),
                    tensorboard_log=tb_log,
                    **hp,
                )
                _copy_compatible_policy_weights(maskable.policy, loaded.policy)
                loaded.num_timesteps = int(maskable.num_timesteps)
                load_kind = "MaskablePPO"
            except (TypeError, ValueError, RuntimeError) as exc:
                raise RuntimeError(f"failed to load resume checkpoint {resume}") from exc

        if tb_log:
            loaded.tensorboard_log = tb_log
        print(
            f"[train:async] resumed {load_kind} from {resume} "
            f"(num_timesteps={loaded.num_timesteps})",
            flush=True,
        )
        if not _checkpoint_spaces_compatible(loaded):
            loaded = _transplant_into_current_spaces(
                loaded, tb_log=tb_log, hp=hp,
            )
        return loaded

    policy_obs_space, act_space = _policy_obs_and_act_spaces()
    return PPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs_space, act_space),
        **hp,
    )


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
    actions = np.zeros(n_steps, dtype=np.int64)
    rewards = np.zeros(n_steps, dtype=np.float32)
    dones = np.zeros(n_steps, dtype=np.bool_)
    values = np.zeros(n_steps, dtype=np.float32)
    log_probs = np.zeros(n_steps, dtype=np.float32)
    episode_infos: list[dict[str, Any]] = []
    step_i = 0

    def _reset_bufs() -> None:
        nonlocal obs_bufs, step_i, episode_infos
        obs_bufs = {
            k: np.zeros((n_steps, *env.observation_space[k].shape), dtype=env.observation_space[k].dtype)
            for k in env.observation_space.spaces
        }
        step_i = 0
        episode_infos = []

    _reset_bufs()

    try:
        while not stop_flag.value:
            req: dict[str, Any] = {"t": "need", "rank": rank, "obs": obs}
            if hasattr(env, "action_masks"):
                req["action_masks"] = env.action_masks()
            conn.send(req)
            msg = conn.recv()
            if msg.get("t") == "stop":
                break
            if msg.get("t") != "act":
                continue

            action = int(msg["action"])
            assert obs_bufs is not None
            for key in obs_bufs:
                obs_bufs[key][step_i] = obs[key]
            actions[step_i] = action
            values[step_i] = float(msg["value"])
            log_probs[step_i] = float(msg["logprob"])

            obs, rew, done, trunc, info = env.step(action)
            if info:
                episode_infos.append(slim_progress_info(info))
            rewards[step_i] = float(rew)
            dones[step_i] = bool(done or trunc)
            step_i += 1

            if done or trunc:
                obs, _ = env.reset()

            if step_i >= n_steps:
                conn.send(
                    {
                        "t": "rollout",
                        "rank": rank,
                        "obs": {k: v.copy() for k, v in obs_bufs.items()},
                        "actions": actions.copy(),
                        "rewards": rewards.copy(),
                        "dones": dones.copy(),
                        "values": values.copy(),
                        "log_probs": log_probs.copy(),
                        "last_obs": obs,
                        "episode_infos": episode_infos,
                    }
                )
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
        f"checkpoint_every={save_interval} steps",
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

            for conn in ready:
                if not conn.poll():
                    continue
                msg = conn.recv()
                if msg["t"] == "need":
                    obs_batch = _obs_batch_for_one(msg["obs"])
                    masks = msg.get("action_masks")
                    if masks is not None:
                        act, val, lp = policy.predict_masked(
                            obs_batch, np.asarray(masks, dtype=bool)
                        )
                    else:
                        act, val, lp = policy.predict_batch(obs_batch)
                        act, val, lp = int(act[0]), float(val[0]), float(lp[0])
                    conn.send(
                        {"t": "act", "action": act, "value": val, "logprob": lp}
                    )
                elif msg["t"] == "rollout":
                    rank = int(msg["rank"])
                    last_values = policy.predict_values(_obs_batch_for_one(msg["last_obs"]))
                    obs = {k: np.expand_dims(v, axis=1) for k, v in msg["obs"].items()}
                    pending.append(
                        WorkerRollout(
                            worker_id=f"actor_{rank}",
                            policy_version=policy_version,
                            n_envs=1,
                            n_steps=n_steps,
                            obs=obs,
                            actions=np.expand_dims(msg["actions"], 1),
                            rewards=np.expand_dims(msg["rewards"], 1),
                            dones=np.expand_dims(msg["dones"], 1),
                            values=np.expand_dims(msg["values"], 1),
                            log_probs=np.expand_dims(msg["log_probs"], 1),
                            last_values=last_values,
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
