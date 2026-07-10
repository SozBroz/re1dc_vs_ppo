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
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.async_fleet import (  # noqa: E402
    DEFAULT_SYNC_INTERVAL_S,
    DISTRIBUTED_EPOCH_HYPERPARAMS,
    PPO_HYPERPARAMS,
    load_async_learner,
)
from re1_rl.distributed.learner_server import LearnerState, start_learner_server  # noqa: E402
from re1_rl.distributed.learner_train import train_on_rollouts  # noqa: E402
from re1_rl.distributed.log_util import log  # noqa: E402
from re1_rl.distributed.spaces import make_re1_policy_spaces  # noqa: E402
from re1_rl.distributed.weight_store import WeightStore  # noqa: E402
from re1_rl.distributed.weights import export_policy_state_dict  # noqa: E402
from re1_rl.distributed.worker_client import WorkerClient  # noqa: E402
from re1_rl.distributed.async_worker_runtime import run_async_worker_loop  # noqa: E402
from re1_rl.distributed.worker_runtime import (  # noqa: E402
    _local_weight_sync_loop,
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
    ap.add_argument(
        "--sync-interval-s",
        type=float,
        default=DEFAULT_SYNC_INTERVAL_S,
        help=(
            "seconds between remote network epochs: upload buffered experience "
            "then pull weights (default 360). Also learner train cadence."
        ),
    )
    ap.add_argument(
        "--batch-threshold",
        type=int,
        default=0,
        help=(
            "optional min timesteps before a timed train fires "
            "(0 = train on whatever arrived each sync interval)"
        ),
    )
    ap.add_argument(
        "--max-staleness",
        type=int,
        default=2,
        help=(
            "reject rollouts older than current_version - K "
            "(default 2: allow one missed epoch under clock skew)"
        ),
    )
    ap.add_argument("--warmup-timeout", type=float, default=600.0,
                    help="seconds to wait for learner weights on worker start")
    ap.add_argument(
        "--weight-sync-poll-s",
        type=float,
        default=None,
        help="deprecated alias for --sync-interval-s",
    )
    ap.add_argument(
        "--worker-liveness-s",
        type=float,
        default=90.0,
        help="drop remote workers with no heartbeat for this many seconds (default 90)",
    )
    ap.add_argument(
        "--epoch-grace-s",
        type=float,
        default=120.0,
        help=(
            "after sync_interval, wait up to this many extra seconds for all "
            "live workers to contribute before training (default 120)"
        ),
    )
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
    ap.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="EmuHawk --gdi/--chromeless + invisible cutscene skip (default on)",
    )
    ap.add_argument(
        "--tile-windows",
        action="store_true",
        help="tile BizHawk windows in a monitor grid (use with --no-headless)",
    )
    ap.add_argument("--grid-cols", type=int, default=4, help="grid columns per monitor")
    ap.add_argument("--grid-rows", type=int, default=2, help="grid rows per monitor")
    ap.add_argument("--grid-gap", type=int, default=8, help="pixel gap between grid tiles")
    ap.add_argument(
        "--grid-monitor",
        default="all",
        help="tile target monitor: left, center, right, 1-based index, or all",
    )
    return ap


def _build_learner_model(args: argparse.Namespace, device: str):
    """Build learner PPO via monolithic ``load_async_learner`` (transplant + Maskable)."""
    from re1_rl.checkpoint_io import resolve_resume_path

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
    if args.run_name:
        tb_log = str(Path(tb_log) / args.run_name)

    model = load_async_learner(device=device, resume=resume_path, tb_log=tb_log)
    # Distributed train_on_rollouts builds its own buffer from worker n_steps;
    # keep model.n_steps aligned with CLI for any SB3 helpers that read it.
    model.n_steps = int(args.n_steps)
    # Large-batch epoch hyperparams (gentler LR / fewer epochs / bigger minibatches).
    for key, value in DISTRIBUTED_EPOCH_HYPERPARAMS.items():
        if key == "n_steps":
            continue
        if hasattr(model, key):
            setattr(model, key, value)
    if hasattr(model, "lr_schedule"):
        lr = float(DISTRIBUTED_EPOCH_HYPERPARAMS["learning_rate"])
        model.lr_schedule = lambda _progress: lr
        if getattr(model, "policy", None) is not None and hasattr(model.policy, "optimizer"):
            for group in model.policy.optimizer.param_groups:
                group["lr"] = lr
    if resume_path is not None:
        log(args.machine_name, f"resumed learner from {resume_path}")
    log(
        args.machine_name,
        f"epoch hyperparams lr={DISTRIBUTED_EPOCH_HYPERPARAMS['learning_rate']} "
        f"batch_size={DISTRIBUTED_EPOCH_HYPERPARAMS['batch_size']} "
        f"n_epochs={DISTRIBUTED_EPOCH_HYPERPARAMS['n_epochs']}",
    )
    return model, ckpt_dir


def _maybe_start_grid_tiler(args: argparse.Namespace) -> threading.Event | None:
    if not args.tile_windows:
        return None
    from re1_rl.window_grid import start_grid_tiler

    stop, _thread = start_grid_tiler(
        expected=int(args.n_envs),
        cols=int(args.grid_cols),
        rows=int(args.grid_rows),
        gap=int(args.grid_gap),
        monitor=str(args.grid_monitor),
        log_fn=lambda msg: log(args.machine_name, msg),
    )
    log(
        args.machine_name,
        f"window grid tiler started ({args.grid_cols}x{args.grid_rows}, "
        f"monitor={args.grid_monitor})",
    )
    return stop


def _run_local_worker(
    args: argparse.Namespace,
    *,
    weight_store: WeightStore,
    rollout_queue: queue.Queue,
    stop_event: threading.Event,
    device: str,
    learner_state: LearnerState | None = None,
) -> None:
    from re1_rl.distributed.inference_policy import InferencePolicy

    obs_space, act_space = make_re1_policy_spaces()
    policy = InferencePolicy(obs_space, act_space, device)
    worker_id = args.worker_id or args.machine_name
    sync_interval = float(args.sync_interval_s)
    if args.weight_sync_poll_s is not None:
        sync_interval = float(args.weight_sync_poll_s)

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

        if learner_state is not None:
            learner_state.register_worker(
                worker_id,
                n_envs=int(args.n_envs),
                hostname=args.machine_name,
                is_local=True,
            )

        sync_thread = threading.Thread(
            target=_local_weight_sync_loop,
            args=(weight_store, policy),
            kwargs={"machine_name": args.machine_name, "stop_event": sync_stop},
            name="local-weight-sync",
            daemon=True,
        )
        sync_thread.start()

        try:
            run_async_worker_loop(
                policy,
                machine_name=args.machine_name,
                worker_id=worker_id,
                n_envs=int(args.n_envs),
                n_steps=int(args.n_steps),
                curriculum=args.curriculum,
                base_port=int(args.base_port),
                training_speed=int(args.training_speed),
                skip_chunk=int(args.skip_chunk),
                capture_checkpoints=bool(args.capture_checkpoints),
                stop_event=stop_event,
                rollout_sink=rollout_queue,
                is_local=True,
                sync_interval_s=sync_interval,
                project_root=PROJECT_ROOT,
                headless=bool(args.headless),
            )
        finally:
            sync_stop.set()
            if learner_state is not None:
                learner_state.unregister_worker(worker_id)

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
    grid_stop = _maybe_start_grid_tiler(args)

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

    sync_interval = float(args.sync_interval_s)
    if args.weight_sync_poll_s is not None:
        sync_interval = float(args.weight_sync_poll_s)

    try:
        run_async_worker_loop(
            policy,
            machine_name=args.machine_name,
            worker_id=worker_id,
            n_envs=int(args.n_envs),
            n_steps=int(args.n_steps),
            curriculum=args.curriculum,
            base_port=int(args.base_port),
            training_speed=int(args.training_speed),
            skip_chunk=int(args.skip_chunk),
            capture_checkpoints=bool(args.capture_checkpoints),
            stop_event=stop_event,
            rollout_sink=client,
            is_local=False,
            sync_interval_s=sync_interval,
            project_root=PROJECT_ROOT,
            headless=bool(args.headless),
        )
    except KeyboardInterrupt:
        log(args.machine_name, "remote worker interrupted")
    finally:
        stop_event.set()
        if grid_stop is not None:
            grid_stop.set()
    return 0


def _run_learner(args: argparse.Namespace) -> int:
    import torch
    from stable_baselines3.common.callbacks import CheckpointCallback

    from re1_rl.checkpoint_io import (
        atomic_model_save,
        checkpoint_save_freq_vec_env,
        write_latest_pointer,
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    train_steps = args.total_steps if args.total_steps > 0 else 2**62
    step_label = str(args.total_steps) if args.total_steps > 0 else "unlimited"
    sync_interval = float(args.sync_interval_s)
    if args.weight_sync_poll_s is not None:
        sync_interval = float(args.weight_sync_poll_s)
    log(
        args.machine_name,
        f"learner starting: sync_interval_s={sync_interval:.0f} "
        f"batch_threshold={args.batch_threshold} max_staleness={args.max_staleness} "
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
    from re1_rl.training_progress import TrainingProgressTracker

    progress = TrainingProgressTracker(
        prefix="progress",
        machine_name=args.machine_name,
        best_log_path=PROJECT_ROOT / "data" / "logs" / f"best_rooms_{args.machine_name}.jsonl",
    )
    weight_store = WeightStore()
    rollout_queue: queue.Queue = queue.Queue()
    learner_state = LearnerState(
        weight_store,
        rollout_queue,
        machine_name=args.machine_name,
        max_staleness=args.max_staleness,
        worker_liveness_s=float(args.worker_liveness_s),
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
    grid_stop = _maybe_start_grid_tiler(args) if run_local else None
    if run_local:
        _run_local_worker(
            args,
            weight_store=weight_store,
            rollout_queue=rollout_queue,
            stop_event=stop_event,
            device=device,
            learner_state=learner_state,
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

    callbacks = [
        AtomicCheckpointCallback(
            save_freq=checkpoint_save_freq_vec_env(args.n_envs),
            save_path=str(ckpt_dir),
            name_prefix="ppo_re1",
        ),
        metrics_cb.get_callback(),
    ]
    for cb in callbacks:
        cb.init_callback(model)

    pending: list = []
    pending_steps = 0
    epoch_t0 = time.monotonic()
    epoch_grace = float(args.epoch_grace_s)
    waiting_for_fleet = False
    epoch_id, expected = learner_state.begin_epoch()
    log(
        args.machine_name,
        f"epoch {epoch_id} started; waiting for live workers={expected or '(none yet)'}",
    )

    try:
        while model.num_timesteps < train_steps and not stop_event.is_set():
            try:
                rollout = rollout_queue.get(timeout=1.0)
                pending.append(rollout)
                pending_steps += rollout.num_timesteps()
                log(
                    args.machine_name,
                    f"queued rollout from {rollout.worker_id} v{rollout.policy_version} "
                    f"(+{rollout.num_timesteps()}, pending={pending_steps})",
                )
            except queue.Empty:
                pass

            elapsed = time.monotonic() - epoch_t0
            status = learner_state.epoch_status()

            # Before sync_interval: keep collecting.
            if elapsed < sync_interval:
                continue

            # After sync_interval: wait for all currently-expected live workers,
            # but do not block forever if pking disappears (liveness + grace).
            if not waiting_for_fleet:
                waiting_for_fleet = True
                # Refresh expected set once the collect window ends so late
                # joiners (pking) that registered during the window are included.
                if status["n_expected"] == 0 and learner_state.live_workers():
                    epoch_id, expected = learner_state.begin_epoch()
                    status = learner_state.epoch_status()
                    log(
                        args.machine_name,
                        f"epoch {epoch_id} expected refreshed at barrier: {expected}",
                    )
                log(
                    args.machine_name,
                    f"epoch {status['epoch_id']} collect window done; "
                    f"expected={status['expected']} missing={status['missing']}",
                )

            if status["n_expected"] == 0:
                # No live workers yet — do not train; keep waiting for register.
                continue

            if not pending:
                if elapsed >= sync_interval + epoch_grace:
                    epoch_id, expected = learner_state.begin_epoch()
                    epoch_t0 = time.monotonic()
                    waiting_for_fleet = False
                    log(
                        args.machine_name,
                        f"epoch {epoch_id} restart (empty); expected={expected}",
                    )
                continue

            fleet_ready = bool(status["ready"])
            grace_expired = elapsed >= sync_interval + epoch_grace
            if not fleet_ready and not grace_expired:
                continue

            if not fleet_ready and grace_expired:
                log(
                    args.machine_name,
                    f"epoch {status['epoch_id']} grace expired; training without "
                    f"{status['missing']} (live={status['n_live']})",
                )

            batch_infos: list[dict[str, Any]] = []
            for rollout in pending:
                batch_infos.extend(rollout.episode_infos)
            trained = train_on_rollouts(model, pending)
            version = weight_store.publish(export_policy_state_dict(model))
            learner_state.set_current_version(version)
            log(
                args.machine_name,
                f"epoch train {trained} steps from {len(pending)} rollouts "
                f"contributors={status['contributors']} -> "
                f"policy_version={version} total={model.num_timesteps}",
            )
            progress.consume_infos(batch_infos, num_timesteps=int(model.num_timesteps))
            progress.log_rollout_end(
                model,
                num_timesteps=int(model.num_timesteps),
                episode_infos=batch_infos,
            )
            for cb in callbacks:
                cb.on_rollout_end()
                cb.on_step()
            pending.clear()
            pending_steps = 0
            epoch_id, expected = learner_state.begin_epoch()
            epoch_t0 = time.monotonic()
            waiting_for_fleet = False
            log(
                args.machine_name,
                f"epoch {epoch_id} started; expected={expected or '(none yet)'}",
            )

    except KeyboardInterrupt:
        log(args.machine_name, "learner interrupted")
    finally:
        stop_event.set()
        if grid_stop is not None:
            grid_stop.set()
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
