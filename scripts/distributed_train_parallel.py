"""Distributed PPO training: one learner, many rollout workers.

Same training hyperparameters as ``train_parallel.py``, but rollouts may come
from the learner host's local worker fleet and from optional remote workers.
Workers never load policy weights from local disk.

Usage (learner host — learner + local BizHawk fleet):
    python scripts/distributed_train_parallel.py --role learner --machine-name workhorse1

Usage (remote worker only):
    python scripts/distributed_train_parallel.py --role worker --machine-name pc-b \\
        --learner-host 192.168.0.160 --learner-port 8765

Single-machine dev (learner + local worker, no remote workers):
    python scripts/distributed_train_parallel.py --role both --machine-name devbox
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.train_parallel import make_env  # noqa: E402
from re1_rl.async_fleet import PPO_HYPERPARAMS  # noqa: E402
from re1_rl.distributed.learner_server import LearnerState, start_learner_server  # noqa: E402
from re1_rl.distributed.learner_train import train_on_rollouts  # noqa: E402
from re1_rl.distributed.log_util import log  # noqa: E402
from re1_rl.distributed.spaces import make_re1_policy_spaces, make_re1_spaces  # noqa: E402
from re1_rl.distributed.weight_store import WeightStore  # noqa: E402
from re1_rl.distributed.weights import export_policy_state_dict  # noqa: E402
from re1_rl.distributed.worker_client import WorkerClient  # noqa: E402
from re1_rl.distributed.worker_runtime import (  # noqa: E402
    _local_weight_sync_loop,
    _remote_weight_sync_loop,
    run_worker_loop,
    warmup_local_policy,
    warmup_remote_policy,
)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Distributed PPO learner / worker training")
    ap.add_argument(
        "--role",
        choices=("learner", "worker", "both"),
        default="both",
        help="learner=learner+local worker; worker=remote only; both=same as learner",
    )
    ap.add_argument("--machine-name", required=True, help="hostname label for all log lines")
    ap.add_argument("--worker-id", default=None, help="unique worker id (default: machine-name)")
    ap.add_argument("--learner-host", default="127.0.0.1", help="learner HTTP host for remote workers")
    ap.add_argument("--learner-port", type=int, default=8765)
    ap.add_argument("--bind-host", default="0.0.0.0", help="learner HTTP bind address")
    ap.add_argument("--batch-threshold", type=int, default=20480,
                    help="timesteps queued before learner train() (default ~6.5 rollouts @ 12 envs)")
    ap.add_argument("--max-staleness", type=int, default=5,
                    help="reject rollouts older than current_version - K")
    ap.add_argument("--warmup-timeout", type=float, default=600.0,
                    help="seconds to wait for learner weights on worker start")
    ap.add_argument("--weight-sync-poll-s", type=float, default=360.0,
                    help="seconds between remote weight polls (default 6 min)")
    ap.add_argument("--no-local-worker", action="store_true",
                    help="learner role without co-located BizHawk fleet")
    ap.add_argument(
        "--n-steps",
        type=int,
        default=int(PPO_HYPERPARAMS["n_steps"]),
        help="per-env rollout horizon (default: async_fleet.PPO_HYPERPARAMS)",
    )

    ap.add_argument("--n-envs", type=int, default=12)
    ap.add_argument("--total-steps", type=int, default=2_000_000,
                    help="training timesteps (0 = no limit, run until interrupted)")
    ap.add_argument("--curriculum", default="curriculum/m0_dining_to_main_hall.json")
    ap.add_argument("--resume", default=None, help="checkpoint .zip to continue from (learner only)")
    ap.add_argument("--base-port", type=int, default=5555,
                    help="first TCP/EmuHawk port; offset per concurrent run")
    ap.add_argument("--run-name", default=None,
                    help="isolate checkpoints/tb/final save under this name (A/B runs)")
    ap.add_argument("--capture-checkpoints", action="store_true",
                    help="save a savestate + PB note when any env reaches a new waypoint")
    ap.add_argument("--training-speed", type=int, default=3200,
                    help="BizHawk speedmode %% for fleet training (default 3200)")
    ap.add_argument("--skip-chunk", type=int, default=600,
                    help="max frames per Lua fast_forward round-trip (default 600)")
    return ap


def _make_vec_env(args: argparse.Namespace):
    from stable_baselines3.common.vec_env import SubprocVecEnv

    return SubprocVecEnv(
        [
            make_env(
                i,
                args.curriculum,
                args.base_port,
                args.capture_checkpoints,
                training_speed=int(args.training_speed),
                skip_chunk=int(args.skip_chunk),
                async_cutscene_skip=True,
            )
            for i in range(args.n_envs)
        ],
        start_method="spawn",
    )


def _build_learner_model(args: argparse.Namespace, device: str):
    import gymnasium as gym
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    from re1_rl.checkpoint_io import resolve_resume_path
    from re1_rl.policy_config import POLICY_KWARGS

    obs_space, act_space = make_re1_spaces()

    class _StubEnv(gym.Env):
        def __init__(self) -> None:
            super().__init__()
            self.observation_space = obs_space
            self.action_space = act_space

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            obs = {k: space.sample() for k, space in self.observation_space.items()}
            return obs, {}

        def step(self, action):
            obs, _ = self.reset()
            return obs, 0.0, False, False, {}

    env = DummyVecEnv([lambda: _StubEnv()])

    ckpt_dir = PROJECT_ROOT / "data" / "checkpoints"
    if args.run_name:
        ckpt_dir = ckpt_dir / args.run_name
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    resume_path = resolve_resume_path(
        args.resume, project_root=PROJECT_ROOT, ckpt_dir=ckpt_dir,
    )
    if args.resume and resume_path is None:
        raise RuntimeError(f"no valid checkpoint for --resume {args.resume!r}")

    tb_log = str(PROJECT_ROOT / "logs" / "tb")
    hp = {
        **PPO_HYPERPARAMS,
        "n_steps": int(args.n_steps),
        "verbose": 1,
        "device": device,
        "policy_kwargs": POLICY_KWARGS,
        "tensorboard_log": tb_log,
    }

    if resume_path:
        model = PPO.load(str(resume_path), env=env, device=device)
        model.tensorboard_log = tb_log
        log(args.machine_name, f"resumed learner from {resume_path}")
    else:
        model = PPO("MultiInputPolicy", env, **hp)
    return model, ckpt_dir


def _run_local_worker(
    args: argparse.Namespace,
    *,
    weight_store: WeightStore,
    rollout_queue: queue.Queue,
    stop_event: threading.Event,
    device: str,
) -> None:
    from re1_rl.distributed.inference_policy import InferencePolicy

    obs_space, act_space = make_re1_policy_spaces()
    policy = InferencePolicy(obs_space, act_space, device)
    worker_id = args.worker_id or args.machine_name

    sync_stop = threading.Event()

    def _warmup_then_run() -> None:
        try:
            warmup_local_policy(
                weight_store,
                policy,
                machine_name=args.machine_name,
                timeout_s=args.warmup_timeout,
            )
        except Exception as exc:
            log(args.machine_name, f"local worker warmup failed: {exc}")
            stop_event.set()
            return

        sync_thread = threading.Thread(
            target=_local_weight_sync_loop,
            args=(weight_store, policy),
            kwargs={"machine_name": args.machine_name, "stop_event": sync_stop},
            name="local-weight-sync",
            daemon=True,
        )
        sync_thread.start()

        vec_env = _make_vec_env(args)
        try:
            run_worker_loop(
                vec_env,
                policy,
                machine_name=args.machine_name,
                worker_id=worker_id,
                n_steps=args.n_steps,
                stop_event=stop_event,
                rollout_sink=rollout_queue,
                is_local=True,
            )
        finally:
            sync_stop.set()
            vec_env.close()

    threading.Thread(target=_warmup_then_run, name="local-worker", daemon=True).start()


def _run_remote_worker(args: argparse.Namespace, *, device: str) -> int:
    from re1_rl.distributed.inference_policy import InferencePolicy

    obs_space, act_space = make_re1_policy_spaces()
    policy = InferencePolicy(obs_space, act_space, device)
    worker_id = args.worker_id or args.machine_name
    client = WorkerClient(
        args.learner_host,
        args.learner_port,
        machine_name=args.machine_name,
    )
    stop_event = threading.Event()

    try:
        warmup_remote_policy(
            client,
            policy,
            machine_name=args.machine_name,
            timeout_s=args.warmup_timeout,
        )
    except Exception as exc:
        log(args.machine_name, f"remote worker warmup failed: {exc}")
        return 1

    client.register(worker_id, args.n_envs)

    sync_stop = threading.Event()
    sync_thread = threading.Thread(
        target=_remote_weight_sync_loop,
        args=(client, policy),
        kwargs={
            "machine_name": args.machine_name,
            "stop_event": sync_stop,
            "poll_s": float(args.weight_sync_poll_s),
        },
        name="remote-weight-sync",
        daemon=True,
    )
    sync_thread.start()

    vec_env = _make_vec_env(args)
    try:
        run_worker_loop(
            vec_env,
            policy,
            machine_name=args.machine_name,
            worker_id=worker_id,
            n_steps=args.n_steps,
            stop_event=stop_event,
            rollout_sink=client,
            is_local=False,
        )
    except KeyboardInterrupt:
        log(args.machine_name, "remote worker interrupted")
    finally:
        stop_event.set()
        sync_stop.set()
        vec_env.close()
    return 0


def _run_learner(args: argparse.Namespace) -> int:
    import torch
    from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback

    from re1_rl.checkpoint_io import (
        atomic_model_save,
        checkpoint_save_freq_vec_env,
        write_latest_pointer,
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    train_steps = args.total_steps if args.total_steps > 0 else 2**62
    step_label = str(args.total_steps) if args.total_steps > 0 else "unlimited"
    log(
        args.machine_name,
        f"learner starting: batch_threshold={args.batch_threshold} "
        f"total_steps={step_label} cuda={torch.cuda.is_available()}",
    )

    model, ckpt_dir = _build_learner_model(args, device)
    from re1_rl.training_metrics_log import (
        TrainingMetricsJsonlCallback,
        configure_training_logger,
        training_metrics_jsonl_path,
    )

    tb_run_dir = PROJECT_ROOT / "logs" / "tb" / (args.run_name or "distributed")
    configure_training_logger(model, log_dir=tb_run_dir)
    metrics_jsonl = training_metrics_jsonl_path(PROJECT_ROOT, run_name=args.run_name)
    metrics_cb = TrainingMetricsJsonlCallback(metrics_jsonl)
    log(args.machine_name, f"metrics jsonl -> {metrics_jsonl}")
    weight_store = WeightStore()
    rollout_queue: queue.Queue = queue.Queue()
    learner_state = LearnerState(
        weight_store,
        rollout_queue,
        machine_name=args.machine_name,
        max_staleness=args.max_staleness,
    )

    http_server, _http_thread = start_learner_server(
        learner_state,
        host=args.bind_host,
        port=args.learner_port,
    )
    log(args.machine_name, f"HTTP learner listening on {args.bind_host}:{args.learner_port}")

    initial_version = weight_store.publish(export_policy_state_dict(model))
    learner_state.set_current_version(initial_version)
    log(args.machine_name, f"published initial policy_version={initial_version}")

    stop_event = threading.Event()
    run_local = not args.no_local_worker
    if run_local:
        _run_local_worker(
            args,
            weight_store=weight_store,
            rollout_queue=rollout_queue,
            stop_event=stop_event,
            device=device,
        )
    else:
        log(args.machine_name, "local worker disabled (--no-local-worker)")

    class AtomicCheckpointCallback(CheckpointCallback):
        def _on_step(self) -> bool:
            if self.n_calls % self.save_freq != 0:
                return True
            from re1_rl.checkpoint_io import atomic_model_save, write_latest_pointer

            model_path = self._checkpoint_path(extension="zip")
            saved = atomic_model_save(self.model, model_path)
            write_latest_pointer(self.save_path, saved)
            if self.verbose >= 2:
                log(args.machine_name, f"checkpoint saved {saved}")
            return True

    class ProgressCallback(BaseCallback):
        def __init__(self) -> None:
            super().__init__()
            self.best_waypoint = 0
            self.episodes = 0

        def _on_step(self) -> bool:
            return True

        def _on_rollout_end(self) -> None:
            ep_rew = self.model.ep_info_buffer
            mean_rew = (sum(e["r"] for e in ep_rew) / len(ep_rew)) if ep_rew else float("nan")
            log(
                args.machine_name,
                f"rollout steps={self.num_timesteps} ep_rew={mean_rew:.3f} "
                f"best_wp={self.best_waypoint}",
            )

    callbacks = [
        AtomicCheckpointCallback(
            save_freq=checkpoint_save_freq_vec_env(args.n_envs),
            save_path=str(ckpt_dir),
            name_prefix="ppo_re1",
        ),
        ProgressCallback(),
        metrics_cb.get_callback(),
    ]
    for cb in callbacks:
        cb.init_callback(model)

    pending: list = []
    pending_steps = 0

    try:
        while model.num_timesteps < train_steps and not stop_event.is_set():
            try:
                rollout = rollout_queue.get(timeout=5.0)
                pending.append(rollout)
                pending_steps += rollout.num_timesteps()
                log(
                    args.machine_name,
                    f"queued rollout from {rollout.worker_id} v{rollout.policy_version} "
                    f"(+{rollout.num_timesteps()}, pending={pending_steps})",
                )
            except queue.Empty:
                continue

            if pending_steps < args.batch_threshold:
                continue

            trained = train_on_rollouts(model, pending)
            version = weight_store.publish(export_policy_state_dict(model))
            learner_state.set_current_version(version)
            log(
                args.machine_name,
                f"trained {trained} steps from {len(pending)} rollouts -> "
                f"policy_version={version} total={model.num_timesteps}",
            )
            for cb in callbacks:
                cb.on_rollout_end()
                cb.on_step()
            pending.clear()
            pending_steps = 0

    except KeyboardInterrupt:
        log(args.machine_name, "learner interrupted")
    finally:
        stop_event.set()
        http_server.shutdown()
        suffix = f"_{args.run_name}" if args.run_name else ""
        from re1_rl.checkpoint_io import (
            atomic_copy_checkpoint,
            find_latest_checkpoint,
            is_valid_checkpoint,
            zip_path,
            write_latest_pointer,
        )

        final_alias = zip_path(PROJECT_ROOT / "data" / f"ppo_re1_final{suffix}")
        try:
            saved = atomic_model_save(model, final_alias)
            write_latest_pointer(ckpt_dir, saved)
            log(args.machine_name, f"saved {saved}")
        except OSError as exc:
            log(args.machine_name, f"final save failed: {exc}")
            latest = find_latest_checkpoint(ckpt_dir)
            if latest is not None and is_valid_checkpoint(latest):
                atomic_copy_checkpoint(latest, final_alias)

    log(args.machine_name, "TRAIN_DONE")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    role = args.role
    if role == "both":
        role = "learner"

    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"

    if role == "worker":
        return _run_remote_worker(args, device=device)

    return _run_learner(args)


if __name__ == "__main__":
    raise SystemExit(main())
