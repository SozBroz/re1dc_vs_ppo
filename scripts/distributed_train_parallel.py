"""Distributed PPO training: one learner, many rollout workers.

Same training hyperparameters as ``train_parallel.py``, but rollouts may come
from the learner host's local worker fleet and from optional remote workers.
Workers never load policy weights from local disk.

Usage (learner host — learner + local BizHawk fleet):
    python scripts/distributed_train_parallel.py --role learner --machine-name workhorse1

Usage (remote worker only):
    python scripts/distributed_train_parallel.py --role worker --machine-name pc-b \\
        --learner-host 192.168.0.116 --learner-port 8765

Single-machine dev (learner + local worker, no remote workers):
    python scripts/distributed_train_parallel.py --role both --machine-name devbox
"""

from __future__ import annotations

import argparse
import os
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
from re1_rl.distributed.learner_server import (  # noqa: E402
    LearnerRolloutSink,
    LearnerState,
    start_learner_server,
)
from re1_rl.distributed.learner_train import pull_rollout_queue, train_on_rollouts  # noqa: E402
from re1_rl.distributed.log_util import log  # noqa: E402
from re1_rl.distributed.spaces import make_re1_policy_spaces  # noqa: E402
from re1_rl.distributed.weight_store import WeightStore  # noqa: E402
from re1_rl.distributed.weights import export_policy_state_dict  # noqa: E402
from re1_rl.distributed.worker_client import WorkerClient  # noqa: E402
from re1_rl.distributed.async_worker_runtime import run_async_worker_loop  # noqa: E402
from re1_rl.distributed.worker_runtime import (  # noqa: E402
    run_synced_worker_loop,
    warmup_local_policy,
    warmup_remote_policy,
)


def parse_actor_ranks(value: str) -> list[int]:
    """Parse comma-separated logical ranks and inclusive ranges."""
    ranks: list[int] = []
    seen: set[int] = set()
    for raw_part in str(value).split(","):
        part = raw_part.strip()
        if not part:
            raise argparse.ArgumentTypeError("actor ranks contain an empty item")
        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2 or not all(bound.strip().isdigit() for bound in bounds):
                raise argparse.ArgumentTypeError(f"invalid actor rank range {part!r}")
            first, last = (int(bound.strip()) for bound in bounds)
            if last < first:
                raise argparse.ArgumentTypeError(
                    f"actor rank range must be ascending: {part!r}"
                )
            expanded = range(first, last + 1)
        else:
            if not part.isdigit():
                raise argparse.ArgumentTypeError(f"invalid actor rank {part!r}")
            expanded = (int(part),)
        for rank in expanded:
            if rank in seen:
                raise argparse.ArgumentTypeError(f"duplicate actor rank {rank}")
            seen.add(rank)
            ranks.append(rank)
    if not ranks:
        raise argparse.ArgumentTypeError("at least one actor rank is required")
    return ranks


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
        default=1,
        help=(
            "reject rollouts older than current_version - K "
            "(default 1: current or previous epoch only). "
            "With --relevance-gate, versions in (K, relevance_max_age] are "
            "soft-queued and filtered by π_new ownership at train time."
        ),
    )
    ap.add_argument(
        "--relevance-gate",
        action="store_true",
        help=(
            "soft-accept stale rollouts up to --relevance-max-age and keep only "
            "those where π_new/π_old stays within the ratio clip (train-time gate)"
        ),
    )
    ap.add_argument(
        "--relevance-max-age",
        type=int,
        default=None,
        help=(
            "when --relevance-gate: hard-reject only if version < current - age "
            "(default max(max_staleness, 8))"
        ),
    )
    ap.add_argument(
        "--relevance-ratio-clip",
        type=float,
        default=2.0,
        help="keep transition if π_new/π_old in [1/c, c] (default 2.0)",
    )
    ap.add_argument(
        "--relevance-keep-frac",
        type=float,
        default=0.5,
        help="keep stale rollout if at least this fraction of transitions pass (default 0.5)",
    )
    ap.add_argument(
        "--relevance-prob-floor",
        type=float,
        default=1e-8,
        help="drop transition if π_new(a|s) is below this floor (default 1e-8)",
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
        default=int(DISTRIBUTED_EPOCH_HYPERPARAMS["n_steps"]),
        help=(
            "per-env MC rollout horizon before buffer (default: "
            f"{int(DISTRIBUTED_EPOCH_HYPERPARAMS['n_steps'])} steps; "
            "sync_interval_s is wall clock, not emulated time)"
        ),
    )

    ap.add_argument("--n-envs", type=int, default=12)
    ap.add_argument(
        "--actor-ranks",
        type=parse_actor_ranks,
        default=None,
        help=(
            "logical actor ranks as comma/ranges (for example 0-3,5-19); "
            "ports remain base_port+rank and n-envs becomes the selected count"
        ),
    )
    ap.add_argument(
        "--memlog",
        action="store_true",
        help="enable independent control/telemetry for selected logical rank 4",
    )
    ap.add_argument("--total-steps", type=int, default=2_000_000,
                    help="training timesteps (0 = no limit, run until interrupted)")
    ap.add_argument("--curriculum", default="curriculum/yawn_rails_one_leg.json")
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
        "--screenshot-mmf",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="BizHawk MMF screenshot transport (default: on for Windows)",
    )
    ap.add_argument(
        "--inference-batch-max",
        type=int,
        default=32,
        help="max actor obs per GPU inference batch (default 32)",
    )
    ap.add_argument(
        "--synced-envs",
        action="store_true",
        help=(
            "remote worker only: lockstep SubprocVecEnv instead of desync actors "
            "(experiment; same epoch flush/weights)"
        ),
    )
    ap.add_argument(
        "--tile-windows",
        action="store_true",
        help="tile EmuHawk windows in a monitor grid (headless chromeless or --no-headless)",
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
    explicit_resume = (
        args.resume is not None and str(args.resume).lower() not in ("auto", "")
    )
    if explicit_resume and resume_path is None:
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
    # Ops-only Baseline E probe (default remains None). Example: RE1_TARGET_KL=0.02
    _tk_raw = os.environ.get("RE1_TARGET_KL", "").strip()
    if _tk_raw:
        model.target_kl = float(_tk_raw)
        log(args.machine_name, f"RE1_TARGET_KL override -> target_kl={model.target_kl}")
    if resume_path is not None:
        log(args.machine_name, f"resumed learner from {resume_path}")
    log(
        args.machine_name,
        f"epoch hyperparams lr={DISTRIBUTED_EPOCH_HYPERPARAMS['learning_rate']} "
        f"batch_size={DISTRIBUTED_EPOCH_HYPERPARAMS['batch_size']} "
        f"n_epochs={DISTRIBUTED_EPOCH_HYPERPARAMS['n_epochs']} "
        f"gamma={DISTRIBUTED_EPOCH_HYPERPARAMS['gamma']} "
        f"target_kl={getattr(model, 'target_kl', None)}",
    )
    return model, ckpt_dir


def _maybe_start_grid_tiler(args: argparse.Namespace) -> threading.Event | None:
    if not args.tile_windows:
        return None
    from re1_rl.window_grid import start_grid_tiler

    actor_ranks = getattr(args, "actor_ranks", None)
    expected_slots = (
        max(int(rank) for rank in actor_ranks) + 1
        if actor_ranks
        else int(args.n_envs)
    )
    stop, _thread = start_grid_tiler(
        expected=expected_slots,
        cols=int(args.grid_cols),
        rows=int(args.grid_rows),
        gap=int(args.grid_gap),
        monitor=str(args.grid_monitor),
        log_fn=lambda msg: log(args.machine_name, msg),
        base_port=int(args.base_port),
        project_root=PROJECT_ROOT,
    )
    log(
        args.machine_name,
        f"window grid tiler started ({args.grid_cols}x{args.grid_rows}, "
        f"monitor={args.grid_monitor}, base_port={args.base_port}, "
        f"place-by-port)",
    )
    return stop


def _run_local_worker(
    args: argparse.Namespace,
    *,
    weight_store: WeightStore,
    rollout_sink: LearnerRolloutSink,
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

        # Local weights sync only at epoch flush inside run_async_worker_loop
        # (no mid-horizon _local_weight_sync_loop hot-swap).
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
                rollout_sink=rollout_sink,
                is_local=True,
                weight_store=weight_store,
                sync_interval_s=sync_interval,
                project_root=PROJECT_ROOT,
                headless=bool(args.headless),
                screenshot_mmf=args.screenshot_mmf,
                inference_batch_max=int(args.inference_batch_max),
                actor_ranks=getattr(args, "actor_ranks", None),
                memlog_actor_rank=4 if bool(getattr(args, "memlog", False)) else None,
            )
        finally:
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
        if bool(getattr(args, "synced_envs", False)):
            run_synced_worker_loop(
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
                client=client,
                sync_interval_s=sync_interval,
                project_root=PROJECT_ROOT,
                headless=bool(args.headless),
                screenshot_mmf=args.screenshot_mmf,
            )
        else:
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
                screenshot_mmf=args.screenshot_mmf,
                inference_batch_max=int(args.inference_batch_max),
                actor_ranks=getattr(args, "actor_ranks", None),
                memlog_actor_rank=4 if bool(getattr(args, "memlog", False)) else None,
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
    from stable_baselines3.common.callbacks import BaseCallback

    from re1_rl.checkpoint_io import (
        atomic_model_save,
        checkpoint_timestep_interval,
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
        f"relevance_gate={args.relevance_gate} "
        f"relevance_max_age={args.relevance_max_age} "
        f"total_steps={step_label} cuda={torch.cuda.is_available()}",
    )

    model, ckpt_dir = _build_learner_model(args, device)
    from re1_rl.training_metrics_log import (
        build_fleet_epoch_record,
        configure_training_logger,
        emit_fleet_epoch_metrics,
        policy_version_lag_hist,
        training_metrics_jsonl_path,
    )

    tb_run_dir = PROJECT_ROOT / "logs" / "tb" / (args.run_name or "distributed")
    configure_training_logger(model, log_dir=tb_run_dir)
    metrics_jsonl = training_metrics_jsonl_path(PROJECT_ROOT, run_name=args.run_name)
    # Baseline E: one JSONL / [train:metrics] emit per fleet epoch after packed
    # train (not SB3 on_rollout_end callback — that would duplicate / miss fleet fields).
    log(args.machine_name, f"metrics jsonl -> {metrics_jsonl}")
    from re1_rl.training_progress import TrainingProgressTracker

    progress = TrainingProgressTracker(
        prefix="progress",
        machine_name=args.machine_name,
        best_log_path=PROJECT_ROOT / "data" / "logs" / f"best_rooms_{args.machine_name}.jsonl",
    )
    weight_store = WeightStore()
    rollout_queue: queue.Queue = queue.Queue()
    from re1_rl.distributed.rollout_types import normalize_curriculum_id
    from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION

    learner_state = LearnerState(
        weight_store,
        rollout_queue,
        machine_name=args.machine_name,
        max_staleness=args.max_staleness,
        worker_liveness_s=float(args.worker_liveness_s),
        relevance_gate=bool(args.relevance_gate),
        relevance_max_age=args.relevance_max_age,
        expected_curriculum_id=normalize_curriculum_id(args.curriculum),
        expected_obs_schema_version=int(OBS_SCHEMA_VERSION),
    )
    local_rollout_sink = LearnerRolloutSink(learner_state)

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
            rollout_sink=local_rollout_sink,
            stop_event=stop_event,
            device=device,
            learner_state=learner_state,
        )
    else:
        log(args.machine_name, "local worker disabled (--no-local-worker)")

    class TimestepAtomicCheckpointCallback(BaseCallback):
        """Save numbered zips from ``model.num_timesteps``, not once-per-epoch n_calls."""

        def __init__(
            self,
            *,
            save_timestep_interval: int,
            save_path: str,
            name_prefix: str = "ppo_re1",
            verbose: int = 0,
        ) -> None:
            super().__init__(verbose)
            self.save_timestep_interval = max(int(save_timestep_interval), 1)
            self.save_path = save_path
            self.name_prefix = name_prefix
            self._last_save_timesteps = 0

        def _on_step(self) -> bool:
            assert self.model is not None
            from re1_rl.checkpoint_io import (
                atomic_model_save,
                checkpoint_due,
                write_latest_pointer,
            )

            steps = int(self.model.num_timesteps)
            if not checkpoint_due(steps, self._last_save_timesteps, self.save_timestep_interval):
                return True
            model_path = f"{self.save_path}/{self.name_prefix}_{steps}_steps"
            saved = atomic_model_save(self.model, model_path)
            write_latest_pointer(self.save_path, saved, steps=steps)
            self._last_save_timesteps = steps
            if self.verbose >= 2:
                log(args.machine_name, f"checkpoint saved {saved}")
            return True

    fleet_n_envs = int(args.n_envs)  # local envs; remotes increase throughput but interval scales local
    callbacks = [
        TimestepAtomicCheckpointCallback(
            save_timestep_interval=checkpoint_timestep_interval(fleet_n_envs),
            save_path=str(ckpt_dir),
            name_prefix="ppo_re1",
            verbose=2,
        ),
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

            merged_envs = sum(r.n_envs for r in pending)
            log(
                args.machine_name,
                f"epoch train prep: {len(pending)} rollouts "
                f"pending_steps={pending_steps} merged_envs={merged_envs}",
            )

            batch_infos: list[dict[str, Any]] = []
            for rollout in pending:
                batch_infos.extend(rollout.episode_infos)
            collection_wall_s = time.monotonic() - epoch_t0
            pre_train_version = int(learner_state.current_policy_version)
            lag_hist = policy_version_lag_hist(
                pending, current_policy_version=pre_train_version
            )
            epoch_update = int(status["epoch_id"])
            status_contributors = list(status.get("contributors") or [])
            try:
                from re1_rl.distributed.relevance_gate import RelevanceGateConfig

                relevance_cfg = None
                if args.relevance_gate:
                    relevance_cfg = RelevanceGateConfig(
                        ratio_clip=float(args.relevance_ratio_clip),
                        prob_floor=float(args.relevance_prob_floor),
                        keep_frac=float(args.relevance_keep_frac),
                    )
                fleet_metrics: dict[str, Any] = {}
                train_t0 = time.monotonic()
                trained = train_on_rollouts(
                    model,
                    pending,
                    machine_name=args.machine_name,
                    current_policy_version=pre_train_version,
                    max_staleness=int(args.max_staleness),
                    relevance_gate=bool(args.relevance_gate),
                    relevance_config=relevance_cfg,
                    learner_state=learner_state,
                    fleet_metrics=fleet_metrics,
                )
                train_wall_s = time.monotonic() - train_t0
                version = weight_store.publish(export_policy_state_dict(model))
                learner_state.set_current_version(version)
                log(
                    args.machine_name,
                    f"epoch train {trained} steps from {len(pending)} rollouts "
                    f"merged_envs={merged_envs} contributors={status_contributors} -> "
                    f"policy_version={version} total={model.num_timesteps}",
                )
                pitch = learner_state.pitch_summary()
                log(
                    args.machine_name,
                    "pitch_summary: "
                    f"pitch_pct={pitch['pitch_pct']:.1f}% "
                    f"pitched_steps={pitch['steps_pitched']} "
                    f"(ingest_reject={pitch['steps_rejected_ingest']} "
                    f"+ relevance_drop={pitch['steps_relevance_dropped']}) "
                    f"accepted_steps={pitch['steps_accepted']} "
                    f"stale_queued_steps={pitch['steps_stale_queued']} "
                    f"relevance_kept_steps={pitch['steps_relevance_kept']} "
                    f"packets_rej={pitch['rollouts_rejected']} "
                    f"packets_stale_q={pitch['rollouts_stale_queued']}",
                )
                contributors = list(
                    fleet_metrics.get("contributors") or status_contributors
                )
                record = build_fleet_epoch_record(
                    model,
                    update=epoch_update,
                    policy_version=int(version),
                    accepted_steps=int(
                        fleet_metrics.get("accepted_steps", trained) or 0
                    ),
                    contributors=contributors,
                    curriculum_id=str(
                        fleet_metrics.get("curriculum_id")
                        or learner_state.expected_curriculum_id
                        or ""
                    ),
                    collection_wall_s=collection_wall_s,
                    train_wall_s=train_wall_s,
                    policy_version_lag_hist=lag_hist,
                    policy_version_counts=fleet_metrics.get("policy_version_counts"),
                    relevance_keep_rate=fleet_metrics.get("relevance_keep_rate"),
                    relevance_step_keep_rate=fleet_metrics.get(
                        "relevance_step_keep_rate"
                    ),
                    rate_steps_s=(
                        float(trained) / train_wall_s if train_wall_s > 0 else 0.0
                    ),
                    extra={
                        "logger_scalars": fleet_metrics.get("logger_scalars") or {},
                    },
                )
                emit_fleet_epoch_metrics(metrics_jsonl, record)
                progress.consume_infos(batch_infos, num_timesteps=int(model.num_timesteps))
                progress.log_rollout_end(
                    model,
                    num_timesteps=int(model.num_timesteps),
                    episode_infos=batch_infos,
                )
                try:
                    from re1_rl.yawn_rails_plr import observe_episode_infos, plr_enabled_from_env
                    from re1_rl.yawn_rails_eval import maybe_log_equal_weight_from_infos

                    if plr_enabled_from_env():
                        observe_episode_infos(PROJECT_ROOT, batch_infos)
                    maybe_log_equal_weight_from_infos(
                        PROJECT_ROOT,
                        batch_infos,
                        update=int(learner_state.current_policy_version),
                        policy_version=int(learner_state.current_policy_version),
                        num_timesteps=int(model.num_timesteps),
                        model=model,
                    )
                except Exception as exc:
                    log(args.machine_name, f"yawn rails eval/plr side-job skipped: {exc}")
                for cb in callbacks:
                    cb.on_rollout_end()
                    cb.on_step()
            except Exception as exc:
                log(args.machine_name, f"epoch train failed: {exc}")
                raise
            finally:
                pending.clear()
                pending_steps = 0
                epoch_id, expected = learner_state.begin_epoch()
                epoch_t0 = time.monotonic()
                waiting_for_fleet = False
                pull_rollout_queue(
                    rollout_queue,
                    pending,
                    machine_name=args.machine_name,
                )
                pending_steps = sum(r.num_timesteps() for r in pending)
                log(
                    args.machine_name,
                    f"epoch {epoch_id} started; expected={expected or '(none yet)'} "
                    f"carried_pending_steps={pending_steps}",
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
            from re1_rl.distributed.learner_train import _policy_weights_finite

            if _policy_weights_finite(model):
                saved = atomic_model_save(model, final_alias)
                write_latest_pointer(ckpt_dir, saved)
                log(args.machine_name, f"saved {saved}")
            else:
                log(args.machine_name, "skip final save (non-finite policy weights)")
                latest = find_latest_checkpoint(ckpt_dir)
                if latest is not None and is_valid_checkpoint(latest):
                    atomic_copy_checkpoint(latest, final_alias)
                    log(args.machine_name, f"restored final alias from {latest}")
        except OSError as exc:
            log(args.machine_name, f"final save failed: {exc}")
            latest = find_latest_checkpoint(ckpt_dir)
            if latest is not None and is_valid_checkpoint(latest):
                atomic_copy_checkpoint(latest, final_alias)

    log(args.machine_name, "TRAIN_DONE")
    return 0


def main() -> int:
    # Fleet restart / process start: never leave PB sync locks wedging resets.
    try:
        from re1_rl.pb_bundle_io import clear_all_champion_locks

        n_locks = clear_all_champion_locks(PROJECT_ROOT)
        if n_locks:
            log("pb", f"cleared {n_locks} champion.sync.lock file(s) at startup")
    except Exception:
        pass

    try:
        from re1_rl.pb_sync import warm_pb_champions_for_training

        mix = warm_pb_champions_for_training(PROJECT_ROOT)
        if mix["n_filled"]:
            log(
                "pb",
                f"reset mix ready: N={mix['n_filled']} "
                f"p_fresh={mix['p_fresh']:.3f} p_each_sidecar={mix['p_each_sidecar']:.3f} "
                f"rooms={mix['room_ids']}",
            )
        else:
            log(
                "pb",
                "reset mix: no filled typewriter champions yet — fresh starts only "
                "(PbChampionResetWrapper still active)",
            )
    except Exception as exc:
        log("pb", f"warm_pb_champions_for_training skipped: {exc!r}")

    args = build_parser().parse_args()
    if args.actor_ranks is not None:
        args.n_envs = len(args.actor_ranks)
    if args.memlog:
        if args.synced_envs:
            raise SystemExit("--memlog requires the default async actor runtime")
        if args.actor_ranks is None or 4 not in args.actor_ranks:
            raise SystemExit("--memlog requires --actor-ranks containing logical rank 4")
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
