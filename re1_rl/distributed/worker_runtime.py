"""Worker rollout loop for local and remote machines."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any

from stable_baselines3.common.vec_env import SubprocVecEnv, VecEnv

from re1_rl.async_fleet import DEFAULT_SYNC_INTERVAL_S
from re1_rl.distributed.async_worker_runtime import _flush_remote_epoch
from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.log_util import log
from re1_rl.distributed.rollout_collect import collect_rollout
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.distributed.worker_client import WorkerClient
from re1_rl.training_progress import TrainingProgressTracker


def _local_weight_sync_loop(
    weight_store: WeightStore,
    policy: InferencePolicy,
    *,
    machine_name: str,
    stop_event: threading.Event,
) -> None:
    local_version = 0
    while not stop_event.is_set():
        version = weight_store.policy_version
        if version > local_version:
            state_dict = weight_store.get_state_dict()
            if state_dict is not None:
                policy.load_from_state_dict(state_dict, version)
                local_version = version
                log(machine_name, f"local weight sync -> policy_version={version}")
        time.sleep(0.25)


def _remote_weight_sync_loop(
    client: WorkerClient,
    policy: InferencePolicy,
    *,
    machine_name: str,
    stop_event: threading.Event,
    poll_s: float = 360.0,
) -> None:
    """Pull full weights at most once per ``poll_s`` (default 6 min).

    This is the only remote full-weight download path. Rollout boundaries must
    not call ``GET /weights`` — that caused multi-MB syncs every actor finish.
    """
    local_version = 0
    while not stop_event.is_set():
        try:
            version, data = client.fetch_weights(min_version=local_version + 1)
            if version > local_version and data:
                policy.load_from_bytes(data, version)
                local_version = version
                log(
                    machine_name,
                    f"remote weight sync -> policy_version={version} "
                    f"(poll_s={poll_s:.0f})",
                )
        except Exception as exc:
            log(machine_name, f"weight sync error: {exc}")
        # Sleep after the attempt so the first post-warmup pull waits poll_s
        # (warmup already loaded current weights).
        stop_event.wait(poll_s)


def warmup_local_policy(
    weight_store: WeightStore,
    policy: InferencePolicy,
    *,
    machine_name: str,
    timeout_s: float,
) -> int:
    log(machine_name, "waiting for initial learner weights (in-process)")
    version = weight_store.wait_for_version(1, timeout=timeout_s)
    state_dict = weight_store.get_state_dict()
    if state_dict is None:
        raise RuntimeError("learner published version without state_dict")
    policy.load_from_state_dict(state_dict, version)
    log(machine_name, f"warmup complete at policy_version={version}")
    return version


def warmup_remote_policy(
    client: WorkerClient,
    policy: InferencePolicy,
    *,
    machine_name: str,
    timeout_s: float,
) -> int:
    log(machine_name, f"waiting for learner at {client.base}")
    client.wait_for_learner(timeout_s)
    version, data = client.fetch_weights()
    if not data:
        raise RuntimeError("learner returned empty policy_bytes during warmup")
    policy.load_from_bytes(data, version)
    log(machine_name, f"warmup complete at policy_version={version}")
    return version


def _try_sync_remote_weights(
    client: WorkerClient,
    policy: InferencePolicy,
    *,
    machine_name: str,
    local_version: int,
) -> int:
    """Pull newer weights once (rollout boundary); returns updated local version."""
    try:
        version, data = client.fetch_weights(min_version=local_version + 1)
        if version > local_version and data:
            policy.load_from_bytes(data, version)
            log(machine_name, f"remote weight sync -> policy_version={version}")
            return version
    except Exception as exc:
        log(machine_name, f"weight sync error: {exc}")
    return local_version


def run_worker_loop(
    vec_env: VecEnv,
    policy: InferencePolicy,
    *,
    machine_name: str,
    worker_id: str,
    n_steps: int,
    stop_event: threading.Event,
    rollout_sink: queue.Queue | WorkerClient,
    is_local: bool,
) -> None:
    """Legacy SubprocVecEnv loop (unused by distributed entry; kept for tests).

    Remote full weight sync is background-only via ``_remote_weight_sync_loop``.
    Resets the vec env at the start of every horizon (legacy behavior).
    """
    log(machine_name, f"worker loop started ({worker_id}, {vec_env.num_envs} envs)")
    while not stop_event.is_set():
        if policy.policy_version <= 0:
            time.sleep(0.1)
            continue
        rollout, _obs = collect_rollout(
            vec_env,
            policy,
            n_steps=n_steps,
            worker_id=worker_id,
            obs=None,
        )
        if is_local:
            assert isinstance(rollout_sink, queue.Queue)
            rollout_sink.put(rollout)
            log(
                machine_name,
                f"delivered rollout v{rollout.policy_version} "
                f"({rollout.num_timesteps()} steps) to learner queue",
            )
        else:
            assert isinstance(rollout_sink, WorkerClient)
            accepted = rollout_sink.upload_rollout(rollout)
            if accepted:
                log(
                    machine_name,
                    f"uploaded rollout v{rollout.policy_version} "
                    f"({rollout.num_timesteps()} steps)",
                )
    log(machine_name, "worker loop stopped")


def make_synced_vec_env(
    *,
    n_envs: int,
    curriculum: str,
    base_port: int,
    training_speed: int,
    skip_chunk: int,
    capture_checkpoints: bool,
    headless: bool,
    screenshot_mmf: bool | None,
) -> SubprocVecEnv:
    """Build lockstep SubprocVecEnv with the same make_env knobs as desync actors."""
    from scripts.train_parallel import make_env

    return SubprocVecEnv(
        [
            make_env(
                rank,
                curriculum,
                base_port,
                capture_checkpoints=capture_checkpoints,
                training_speed=training_speed,
                skip_chunk=skip_chunk,
                async_cutscene_skip=True,
                headless=headless,
                screenshot_mmf=screenshot_mmf,
            )
            for rank in range(n_envs)
        ]
    )


def run_synced_worker_loop(
    policy: InferencePolicy,
    *,
    machine_name: str,
    worker_id: str,
    n_envs: int,
    n_steps: int,
    curriculum: str,
    base_port: int,
    training_speed: int,
    skip_chunk: int,
    capture_checkpoints: bool,
    stop_event: threading.Event,
    client: WorkerClient,
    sync_interval_s: float = DEFAULT_SYNC_INTERVAL_S,
    heartbeat_s: float = 30.0,
    project_root: Path | None = None,
    headless: bool = True,
    screenshot_mmf: bool | None = None,
) -> None:
    """Remote worker: lockstep SubprocVecEnv + same epoch flush/weight pull as async.

    Episodes continue across horizons (no reset every ``n_steps``). Heartbeat and
    ``sync_interval_s`` flush match ``run_async_worker_loop`` so the learner epoch
    barrier stays healthy.
    """
    log(
        machine_name,
        f"synced SubprocVecEnv worker starting ({worker_id}, {n_envs} lockstep envs, "
        f"n_steps={n_steps}, sync_interval_s={sync_interval_s:.0f}, "
        f"headless={headless}, screenshot_mmf={screenshot_mmf})",
    )
    root = Path(project_root) if project_root else Path.cwd()
    best_log = root / "data" / "logs" / f"best_rooms_{machine_name}.jsonl"
    progress = TrainingProgressTracker(
        prefix=f"progress:{machine_name}",
        machine_name=machine_name,
        best_log_path=best_log,
    )
    buffered: list[WorkerRollout] = []
    local_steps = 0
    epoch_t0 = time.monotonic()
    hb_stop = threading.Event()
    obs: dict[str, Any] | None = None
    vec_env: SubprocVecEnv | None = None

    def _heartbeat_loop() -> None:
        while not hb_stop.is_set() and not stop_event.is_set():
            try:
                client.heartbeat(worker_id, n_envs)
            except Exception as exc:
                log(machine_name, f"heartbeat error: {exc}")
            hb_stop.wait(heartbeat_s)

    hb_thread = threading.Thread(
        target=_heartbeat_loop, name="synced-worker-heartbeat", daemon=True
    )

    try:
        client.register(worker_id, n_envs, is_local=False)
        client.heartbeat(worker_id, n_envs)
        hb_thread.start()

        vec_env = make_synced_vec_env(
            n_envs=n_envs,
            curriculum=curriculum,
            base_port=base_port,
            training_speed=training_speed,
            skip_chunk=skip_chunk,
            capture_checkpoints=capture_checkpoints,
            headless=headless,
            screenshot_mmf=screenshot_mmf,
        )
        log(machine_name, f"synced SubprocVecEnv fleet ready ({n_envs} envs)")

        while not stop_event.is_set():
            if policy.policy_version <= 0:
                time.sleep(0.1)
                continue

            if (time.monotonic() - epoch_t0) >= sync_interval_s:
                epoch_infos = [
                    info for r in buffered for info in (r.episode_infos or [])
                ]
                buffered = _flush_remote_epoch(
                    buffered,
                    client=client,
                    policy=policy,
                    machine_name=machine_name,
                    worker_id=worker_id,
                )
                if epoch_infos:
                    progress.log_rollout_end(
                        None,
                        num_timesteps=local_steps,
                        episode_infos=epoch_infos,
                    )
                epoch_t0 = time.monotonic()

            rollout, obs = collect_rollout(
                vec_env,
                policy,
                n_steps=n_steps,
                worker_id=worker_id,
                obs=obs,
            )
            local_steps += int(rollout.num_timesteps())
            progress.consume_infos(
                rollout.episode_infos,
                num_timesteps=local_steps,
            )
            buffered.append(rollout)

        if buffered:
            buffered = _flush_remote_epoch(
                buffered,
                client=client,
                policy=policy,
                machine_name=machine_name,
                worker_id=worker_id,
            )
            if buffered:
                log(
                    machine_name,
                    f"shutdown flush retained {len(buffered)} rollouts undelivered",
                )
    finally:
        hb_stop.set()
        try:
            client.unregister(worker_id)
        except Exception:
            pass
        if vec_env is not None:
            try:
                vec_env.close()
            except Exception:
                pass
        log(machine_name, "synced SubprocVecEnv worker loop stopped")
