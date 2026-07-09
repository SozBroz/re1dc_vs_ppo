"""Benchmark synced SubprocVecEnv PPO vs desync central-policy actors.

Trains for real (PPO updates) so both modes advance a checkpoint.

Usage:
    python scripts/benchmark_sync_vs_async.py --mode sync --total-steps 600000
    python scripts/benchmark_sync_vs_async.py --mode async --total-steps 600000
    python scripts/benchmark_sync_vs_async.py --mode both --total-steps 600000

Default total-steps = 50_000 * 12 envs = 600_000 SB3 timesteps.

Warmup fairness:
  Both modes time fleet spawn/stagger/BizHawk-connect separately as warmup_seconds.
  The benchmark clock (wall_seconds) starts only after all 12 emulators are connected.
  The first curriculum reset is inside the timed region for both (SB3 learn() reset
  for sync; actor reset on start signal for async).
  Pass --include-warmup to fold warmup into wall_seconds for an all-in number.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from dataclasses import asdict, dataclass
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_N_ENVS = 12
DEFAULT_PER_ENV = 50_000
DEFAULT_TOTAL = DEFAULT_PER_ENV * DEFAULT_N_ENVS
DEFAULT_RESUME = "data/ppo_re1_knife11.zip"
RESULTS_DIR = PROJECT_ROOT / "data" / "benchmark_results"


@dataclass
class BenchmarkResult:
    mode: str
    n_envs: int
    total_steps_target: int
    total_steps_achieved: int
    n_ppo_updates: int
    warmup_seconds: float
    wall_seconds: float
    predict_seconds: float
    train_seconds: float
    collect_seconds: float
    steps_per_second: float
    resume: str
    base_port: int
    checkpoint_out: str
    include_warmup_in_wall: bool


def _ppo_hyperparams() -> dict[str, Any]:
    return dict(
        n_steps=256,
        batch_size=512,
        n_epochs=4,
        learning_rate=3e-4,
        gamma=0.99,
        ent_coef=0.01,
    )


def _load_sync_model(env, *, device: str, resume: Path | None):
    from sb3_contrib import MaskablePPO

    from scripts.train_parallel import _build_model

    model = _build_model(
        MaskablePPO,
        env,
        device=device,
        resume_path=resume,
        tb_log=str(PROJECT_ROOT / "logs" / "tb" / "benchmark"),
    )
    model.verbose = 0
    return model


def _load_policy_for_inference(*, device: str, resume: Path | None):
    """Async learner mirror: weights only (no ActionMasker on inference path)."""
    from stable_baselines3 import PPO

    from re1_rl.distributed.spaces import make_re1_spaces
    from re1_rl.policy_config import POLICY_KWARGS

    hp = _ppo_hyperparams()
    if resume is not None and resume.is_file():
        try:
            return PPO.load(str(resume), device=device)
        except (TypeError, ValueError, RuntimeError):
            pass
        try:
            from sb3_contrib import MaskablePPO

            from re1_rl.distributed.weights import _SpaceHolderEnv

            maskable = MaskablePPO.load(str(resume), device=device)
            model = PPO(
                "MultiInputPolicy",
                _SpaceHolderEnv(maskable.observation_space, maskable.action_space),
                policy_kwargs=POLICY_KWARGS,
                verbose=0,
                device=device,
                tensorboard_log=None,
                **hp,
            )
            model.policy.load_state_dict(maskable.policy.state_dict())
            model.num_timesteps = int(maskable.num_timesteps)
            print(
                f"[benchmark:async] resumed MaskablePPO weights into PPO from {resume}",
                flush=True,
            )
            return model
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"failed to load resume checkpoint {resume}") from exc
    obs_space, act_space = make_re1_spaces()
    return PPO(
        "MultiInputPolicy",
        env=None,
        policy_kwargs=POLICY_KWARGS,
        verbose=0,
        device=device,
        tensorboard_log=None,
        observation_space=obs_space,
        action_space=act_space,
        **hp,
    )


def _save_model(model, path: Path) -> None:
    from re1_rl.checkpoint_io import atomic_model_save

    atomic_model_save(model, path)


def run_sync_benchmark(
    *,
    n_envs: int,
    total_steps: int,
    resume: Path | None,
    base_port: int,
    curriculum: str,
    training_speed: int,
    skip_chunk: int,
    checkpoint_out: Path,
    include_warmup_in_wall: bool,
) -> BenchmarkResult:
    import torch
    from stable_baselines3.common.vec_env import SubprocVecEnv

    from scripts.train_parallel import make_env

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[benchmark:sync] {n_envs} envs, {total_steps} steps, device={device}", flush=True)

    warmup_t0 = time.perf_counter()
    print("[benchmark:sync] warming up fleet (spawn + BizHawk connect)...", flush=True)
    env = SubprocVecEnv(
        [
            make_env(
                i,
                curriculum,
                base_port,
                False,
                training_speed=training_speed,
                skip_chunk=skip_chunk,
            )
            for i in range(n_envs)
        ],
        start_method="spawn",
    )
    warmup_seconds = time.perf_counter() - warmup_t0
    print(f"[benchmark:sync] fleet ready in {warmup_seconds:.1f}s", flush=True)

    model = _load_sync_model(env, device=device, resume=resume)

    from stable_baselines3.common.callbacks import BaseCallback

    class SyncProgressCallback(BaseCallback):
        def _on_step(self) -> bool:
            return True

        def _on_rollout_end(self) -> bool:
            steps = self.num_timesteps
            pct = 100.0 * steps / max(total_steps, 1)
            print(
                f"[benchmark:sync] progress {steps}/{total_steps} ({pct:.1f}%)",
                flush=True,
            )
            return True

    updates_before = model.num_timesteps // (model.n_steps * n_envs)
    t0 = time.perf_counter()
    try:
        model.learn(
            total_timesteps=total_steps,
            progress_bar=False,
            callback=SyncProgressCallback(),
        )
    finally:
        train_wall = time.perf_counter() - t0
        achieved = int(model.num_timesteps)
        _save_model(model, checkpoint_out)
        env.close()

    updates = achieved // (model.n_steps * n_envs) - updates_before
    wall = warmup_seconds + train_wall if include_warmup_in_wall else train_wall
    return BenchmarkResult(
        mode="sync",
        n_envs=n_envs,
        total_steps_target=total_steps,
        total_steps_achieved=achieved,
        n_ppo_updates=max(updates, 0),
        warmup_seconds=warmup_seconds,
        wall_seconds=wall,
        predict_seconds=0.0,
        train_seconds=0.0,
        collect_seconds=train_wall,
        steps_per_second=achieved / wall if wall > 0 else 0.0,
        resume=str(resume) if resume else "",
        base_port=base_port,
        checkpoint_out=str(checkpoint_out),
        include_warmup_in_wall=include_warmup_in_wall,
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
) -> None:
    from scripts.train_parallel import make_env

    env = make_env(
        rank,
        curriculum,
        base_port,
        False,
        training_speed=training_speed,
        skip_chunk=skip_chunk,
    )()
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
    step_i = 0

    def _reset_bufs() -> None:
        nonlocal obs_bufs, step_i
        obs_bufs = {
            k: np.zeros((n_steps, *env.observation_space[k].shape), dtype=env.observation_space[k].dtype)
            for k in env.observation_space.spaces
        }
        step_i = 0

    _reset_bufs()

    try:
        while not stop_flag.value:
            conn.send({"t": "need", "rank": rank, "obs": obs})
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

            obs, rew, done, trunc, _info = env.step(action)
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
    label: str,
    timeout_s: float = 600.0,
) -> None:
    """Block until every actor reports BizHawk connected (make_env finished)."""
    spawned: set[int] = set()
    deadline = time.perf_counter() + timeout_s
    while len(spawned) < n_envs:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            missing = sorted(set(range(n_envs)) - spawned)
            raise TimeoutError(f"{label}: timed out waiting for actors {missing}")
        ready = wait(conns, timeout=min(1.0, remaining))
        for conn in ready:
            if not conn.poll():
                continue
            msg = conn.recv()
            if msg.get("t") == "spawned":
                spawned.add(int(msg["rank"]))
    print(f"[benchmark:{label}] all {n_envs} actors connected", flush=True)


def _signal_actors_start(conns: list[Connection]) -> None:
    for conn in conns:
        conn.send({"t": "start"})


def _obs_batch_for_one(obs: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: np.expand_dims(v, 0) for k, v in obs.items()}


def run_async_benchmark(
    *,
    n_envs: int,
    total_steps: int,
    resume: Path | None,
    base_port: int,
    curriculum: str,
    training_speed: int,
    skip_chunk: int,
    checkpoint_out: Path,
    include_warmup_in_wall: bool,
) -> BenchmarkResult:
    import torch
    from stable_baselines3 import PPO

    from re1_rl.distributed.inference_policy import InferencePolicy
    from re1_rl.distributed.learner_train import train_on_rollouts
    from re1_rl.distributed.rollout_types import WorkerRollout
    from re1_rl.distributed.weights import export_policy_state_dict

    hp = _ppo_hyperparams()
    n_steps = int(hp["n_steps"])
    batch_threshold = n_steps * n_envs
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(
        f"[benchmark:async] {n_envs} desync actors, {total_steps} steps, "
        f"batch_threshold={batch_threshold}, device={device}",
        flush=True,
    )

    model = _load_policy_for_inference(device=device, resume=resume)
    policy = InferencePolicy(model.observation_space, model.action_space, device)
    policy_version = 1
    policy.load_from_state_dict(export_policy_state_dict(model), policy_version)

    stop_flag = mp.Value("b", False)
    ctx = mp.get_context("spawn")
    processes: list[mp.Process] = []
    parent_conns: list[Connection] = []

    warmup_t0 = time.perf_counter()
    print("[benchmark:async] warming up fleet (spawn + BizHawk connect)...", flush=True)
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
            },
            name=f"async-actor-{rank}",
        )
        proc.start()
        child_conn.close()
        processes.append(proc)
        parent_conns.append(parent_conn)

    _wait_for_actor_spawn(parent_conns, n_envs, label="async")
    warmup_seconds = time.perf_counter() - warmup_t0
    print(f"[benchmark:async] fleet ready in {warmup_seconds:.1f}s", flush=True)

    # Match sync: first curriculum reset happens at timed-region start.
    _signal_actors_start(parent_conns)

    pending: list[WorkerRollout] = []
    pending_steps = 0
    n_updates = 0
    predict_seconds = 0.0
    train_seconds = 0.0
    collect_seconds = 0.0
    t0 = time.perf_counter()

    try:
        while model.num_timesteps < total_steps and not stop_flag.value:
            ready = wait(parent_conns, timeout=1.0)
            if not ready:
                if not any(p.is_alive() for p in processes):
                    break
                continue

            t_collect = time.perf_counter()
            for conn in ready:
                if not conn.poll():
                    continue
                msg = conn.recv()
                if msg["t"] == "need":
                    tp = time.perf_counter()
                    act, val, lp = policy.predict_batch(_obs_batch_for_one(msg["obs"]))
                    predict_seconds += time.perf_counter() - tp
                    conn.send(
                        {
                            "t": "act",
                            "action": int(act[0]),
                            "value": float(val[0]),
                            "logprob": float(lp[0]),
                        }
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
                        )
                    )
                    pending_steps += n_steps
            collect_seconds += time.perf_counter() - t_collect

            if pending_steps < batch_threshold:
                continue

            tt = time.perf_counter()
            train_on_rollouts(model, pending)
            train_seconds += time.perf_counter() - tt
            policy_version += 1
            policy.load_from_state_dict(export_policy_state_dict(model), policy_version)
            n_updates += 1
            pending.clear()
            pending_steps = 0
            print(
                f"[benchmark:async] update {n_updates} "
                f"timesteps={model.num_timesteps}/{total_steps}",
                flush=True,
            )
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

    train_wall = time.perf_counter() - t0
    achieved = int(model.num_timesteps)
    _save_model(model, checkpoint_out)

    wall = warmup_seconds + train_wall if include_warmup_in_wall else train_wall
    return BenchmarkResult(
        mode="async",
        n_envs=n_envs,
        total_steps_target=total_steps,
        total_steps_achieved=achieved,
        n_ppo_updates=n_updates,
        warmup_seconds=warmup_seconds,
        wall_seconds=wall,
        predict_seconds=predict_seconds,
        train_seconds=train_seconds,
        collect_seconds=collect_seconds,
        steps_per_second=achieved / wall if wall > 0 else 0.0,
        resume=str(resume) if resume else "",
        base_port=base_port,
        checkpoint_out=str(checkpoint_out),
        include_warmup_in_wall=include_warmup_in_wall,
    )


def _write_result(result: BenchmarkResult) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{result.mode}_{stamp}.json"
    path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return path


def _print_result(result: BenchmarkResult) -> None:
    print("\n=== BENCHMARK RESULT ===", flush=True)
    for k, v in asdict(result).items():
        if k.endswith("_seconds") and isinstance(v, float):
            print(f"  {k}: {v:.2f}s", flush=True)
        elif k == "steps_per_second":
            print(f"  {k}: {v:.2f}", flush=True)
        else:
            print(f"  {k}: {v}", flush=True)
    train_only = result.wall_seconds - result.warmup_seconds if result.include_warmup_in_wall else result.wall_seconds
    print(f"  train_only_seconds: {train_only:.2f}s", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync vs async PPO collection benchmark")
    ap.add_argument("--mode", choices=("sync", "async", "both"), default="both")
    ap.add_argument("--n-envs", type=int, default=DEFAULT_N_ENVS)
    ap.add_argument("--per-env-steps", type=int, default=DEFAULT_PER_ENV,
                    help="target env-steps per agent; total = per-env * n-envs")
    ap.add_argument("--total-steps", type=int, default=None,
                    help="override total SB3 timesteps (default per-env * n-envs)")
    ap.add_argument("--resume", default=DEFAULT_RESUME)
    ap.add_argument("--base-port", type=int, default=5700,
                    help="first BizHawk port (offset per env)")
    ap.add_argument("--curriculum", default="curriculum/m0_dining_to_main_hall.json")
    ap.add_argument("--training-speed", type=int, default=3200)
    ap.add_argument("--skip-chunk", type=int, default=600)
    ap.add_argument(
        "--include-warmup",
        action="store_true",
        help="add fleet warmup (BizHawk spawn/stagger/connect) into wall_seconds; "
             "default excludes warmup so sync/async compare steady-state throughput",
    )
    args = ap.parse_args()

    total_steps = args.total_steps if args.total_steps is not None else args.per_env_steps * args.n_envs
    resume = Path(args.resume)
    if not resume.is_absolute():
        resume = PROJECT_ROOT / resume

    modes = ("sync", "async") if args.mode == "both" else (args.mode,)
    all_results: list[BenchmarkResult] = []

    for mode in modes:
        ckpt_out = PROJECT_ROOT / "data" / f"benchmark_{mode}.zip"
        runner = run_sync_benchmark if mode == "sync" else run_async_benchmark
        result = runner(
            n_envs=args.n_envs,
            total_steps=total_steps,
            resume=resume if resume.is_file() else None,
            base_port=args.base_port,
            curriculum=args.curriculum,
            training_speed=args.training_speed,
            skip_chunk=args.skip_chunk,
            checkpoint_out=ckpt_out,
            include_warmup_in_wall=args.include_warmup,
        )
        out = _write_result(result)
        _print_result(result)
        print(f"  results_json: {out}", flush=True)
        all_results.append(result)

    if len(all_results) == 2:
        sync_r, async_r = all_results
        ratio = async_r.wall_seconds / sync_r.wall_seconds if sync_r.wall_seconds > 0 else float("nan")
        print("\n=== COMPARISON ===", flush=True)
        print(f"  warmup sync:  {sync_r.warmup_seconds:.1f}s", flush=True)
        print(f"  warmup async: {async_r.warmup_seconds:.1f}s", flush=True)
        print(f"  wall sync:    {sync_r.wall_seconds:.1f}s  ({sync_r.steps_per_second:.2f} steps/s)", flush=True)
        print(f"  wall async:   {async_r.wall_seconds:.1f}s  ({async_r.steps_per_second:.2f} steps/s)", flush=True)
        print(f"  async/sync:   {ratio:.3f}x", flush=True)
        if ratio < 1.0:
            print("  async faster", flush=True)
        else:
            print("  sync faster", flush=True)

    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
