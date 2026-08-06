"""Desync actor fleet for distributed workers (local inference, epoch sync).

Mirrors monolithic ``async_fleet`` collection: each env is an independent actor
process; the worker process serves ``need`` / ``act`` via a local
``InferencePolicy``. Remotes buffer rollouts and touch the network once per
``sync_interval_s`` (upload burst + weight pull). Local workers enqueue
in-process with no HTTP.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any

import numpy as np

from re1_rl.async_fleet import (
    DEFAULT_SYNC_INTERVAL_S,
    _actor_process,
    _drain_actor_messages,
    _obs_batch_for_one,
    _serve_needs_batch,
    _wait_for_actor_spawn,
)
from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.log_util import log
from re1_rl.distributed.rollout_types import WorkerRollout, normalize_curriculum_id
from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION
from re1_rl.distributed.worker_client import WorkerClient
from re1_rl.training_progress import TrainingProgressTracker


def worker_rollout_from_actor_msg(
    msg: dict[str, Any],
    *,
    policy: InferencePolicy,
    worker_id: str,
    n_steps: int,
    curriculum: str = "",
) -> WorkerRollout:
    """Build a 1-env ``WorkerRollout`` from an actor ``rollout`` pipe message."""
    rank = int(msg["rank"])
    actual_steps = int(msg.get("n_steps", n_steps))
    last_values = policy.predict_values(_obs_batch_for_one(msg["last_obs"]))
    obs = {k: np.expand_dims(v[:actual_steps], axis=1) for k, v in msg["obs"].items()}
    masks = msg.get("action_masks")
    if masks is None:
        raise ValueError("actor rollout missing action_masks (fail closed)")
    masks_arr = np.asarray(masks, dtype=np.bool_)[:actual_steps]
    # Prefer version stamped at horizon start (first act), not delivery-time policy.
    policy_version = int(msg.get("policy_version", policy.policy_version))
    mod_drop = msg.get("mod_drop_masks")
    mod_drop_masks = None
    if mod_drop is not None:
        mod_drop_masks = np.expand_dims(
            np.asarray(mod_drop, dtype=np.float32)[:actual_steps], axis=1
        )
    return WorkerRollout(
        worker_id=f"{worker_id}:actor_{rank}",
        policy_version=policy_version,
        n_envs=1,
        n_steps=actual_steps,
        obs=obs,
        actions=np.expand_dims(msg["actions"][:actual_steps], 1),
        rewards=np.expand_dims(msg["rewards"][:actual_steps], 1),
        dones=np.expand_dims(msg["dones"][:actual_steps], 1),
        values=np.expand_dims(msg["values"][:actual_steps], 1),
        log_probs=np.expand_dims(msg["log_probs"][:actual_steps], 1),
        last_values=last_values,
        action_masks=np.expand_dims(masks_arr, 1),
        episode_infos=list(msg.get("episode_infos") or []),
        mod_drop_masks=mod_drop_masks,
        curriculum_id=normalize_curriculum_id(curriculum),
        obs_schema_version=int(OBS_SCHEMA_VERSION),
    )


def pack_rollouts(rollouts: list[WorkerRollout], *, worker_id: str) -> WorkerRollout:
    """Merge same-horizon 1-env rollouts into one multi-env batch for a single POST."""
    if not rollouts:
        raise ValueError("empty rollout list")
    n_steps = rollouts[0].n_steps
    version = rollouts[0].policy_version
    curriculum_id = rollouts[0].curriculum_id
    schema = int(rollouts[0].obs_schema_version)
    for r in rollouts:
        if r.n_steps != n_steps:
            raise ValueError("pack_rollouts requires identical n_steps")
        if r.policy_version != version:
            raise ValueError("pack_rollouts requires identical policy_version")
        if r.curriculum_id != curriculum_id:
            raise ValueError("pack_rollouts requires identical curriculum_id")
        if int(r.obs_schema_version) != schema:
            raise ValueError("pack_rollouts requires identical obs_schema_version")
    total_envs = sum(r.n_envs for r in rollouts)
    obs = {
        key: np.concatenate([r.obs[key] for r in rollouts], axis=1)
        for key in rollouts[0].obs
    }
    mod_drop_masks = None
    if any(r.mod_drop_masks is not None for r in rollouts):
        from re1_rl.modality_ablations import MOD_DROP_DIM

        parts = []
        for r in rollouts:
            if r.mod_drop_masks is None:
                parts.append(
                    np.ones((r.n_steps, r.n_envs, MOD_DROP_DIM), dtype=np.float32)
                )
            else:
                parts.append(np.asarray(r.mod_drop_masks, dtype=np.float32))
        mod_drop_masks = np.concatenate(parts, axis=1)
    return WorkerRollout(
        worker_id=worker_id,
        policy_version=version,
        n_envs=total_envs,
        n_steps=n_steps,
        obs=obs,
        actions=np.concatenate([r.actions for r in rollouts], axis=1),
        rewards=np.concatenate([r.rewards for r in rollouts], axis=1),
        dones=np.concatenate([r.dones for r in rollouts], axis=1),
        values=np.concatenate([r.values for r in rollouts], axis=1),
        log_probs=np.concatenate([r.log_probs for r in rollouts], axis=1),
        last_values=np.concatenate([r.last_values for r in rollouts], axis=0),
        action_masks=np.concatenate([r.action_masks for r in rollouts], axis=1),
        episode_infos=[info for r in rollouts for info in r.episode_infos],
        mod_drop_masks=mod_drop_masks,
        curriculum_id=curriculum_id,
        obs_schema_version=schema,
    )


def _serve_need(
    conn: Connection,
    msg: dict[str, Any],
    policy: InferencePolicy,
) -> None:
    _serve_needs_batch([(conn, msg)], policy, max_batch=1)


def _deliver_local(
    rollout: WorkerRollout,
    *,
    machine_name: str,
    rollout_sink: queue.Queue,
) -> None:
    rollout_sink.put(rollout)
    log(
        machine_name,
        f"delivered rollout v{rollout.policy_version} "
        f"({rollout.num_timesteps()} steps) to learner queue "
        f"[{rollout.worker_id}]",
    )


_UPLOAD_RETRIES = 3
_UPLOAD_RETRY_BACKOFF_S = (2.0, 5.0, 10.0)


def _safe_upload(
    client: WorkerClient,
    machine_name: str,
    rollout: WorkerRollout,
    *,
    retries: int = _UPLOAD_RETRIES,
) -> str:
    """POST one packed rollout.

    Returns ``ok``, ``capacity_full``, ``rejected``, or ``error``.
    Transient network failures are retried; permanent failure -> ``error``.
    """
    import urllib.error

    last_exc: BaseException | None = None
    attempts = max(int(retries), 1)
    for attempt in range(1, attempts + 1):
        try:
            if client.upload_rollout(rollout):
                return "ok"
            reason = str(getattr(client, "last_reject_reason", "") or "rejected")
            return "capacity_full" if reason == "capacity_full" else "rejected"
        except (RuntimeError, TimeoutError, OSError, urllib.error.URLError) as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            delay = _UPLOAD_RETRY_BACKOFF_S[
                min(attempt - 1, len(_UPLOAD_RETRY_BACKOFF_S) - 1)
            ]
            log(
                machine_name,
                f"sync epoch upload error attempt {attempt}/{attempts} "
                f"(retry in {delay:.0f}s): {exc}",
            )
            time.sleep(delay)
    log(
        machine_name,
        f"sync epoch upload failed after {attempts} attempt(s) (will retain): {last_exc}",
    )
    return "error"


def _pack_and_deliver_rollouts(
    group: list[WorkerRollout],
    *,
    worker_id: str,
    pack_max_envs: int,
    deliver,
) -> tuple[int, list[WorkerRollout], bool]:
    """Pack rollouts into POST chunks.

    Returns ``(n_posts, retained, capacity_full)``.
    On ``capacity_full``, remaining undelivered chunks are dropped (not retained).
    """
    chunk: list[WorkerRollout] = []
    chunk_envs = 0
    n_posts = 0
    retained: list[WorkerRollout] = []
    capacity_full = False

    def _flush_chunk() -> None:
        nonlocal chunk, chunk_envs, n_posts, capacity_full
        if not chunk or capacity_full:
            chunk, chunk_envs = [], 0
            return
        packed = pack_rollouts(chunk, worker_id=worker_id)
        result = deliver(packed)
        if result is True or result == "ok":
            n_posts += 1
        elif result == "capacity_full":
            capacity_full = True
            # Drop this packet and stop retaining further chunks.
        else:
            retained.extend(chunk)
        chunk, chunk_envs = [], 0

    for r in group:
        if capacity_full:
            break
        if chunk and (
            chunk_envs + r.n_envs > pack_max_envs
            or chunk[0].n_steps != r.n_steps
        ):
            _flush_chunk()
        chunk.append(r)
        chunk_envs += r.n_envs
    _flush_chunk()
    return n_posts, retained, capacity_full


def _flush_remote_epoch(
    buffered: list[WorkerRollout],
    *,
    client: WorkerClient,
    policy: InferencePolicy,
    machine_name: str,
    worker_id: str,
    pack_max_envs: int = 16,
    flush_reason: str = "timer",
) -> tuple[list[WorkerRollout], bool]:
    """Upload buffered experience (burst), then pull weights once.

    Returns ``(retained, capacity_full)``. Capacity-full packets are dropped.
    """
    retained: list[WorkerRollout] = []
    capacity_full = False
    if not buffered:
        log(
            machine_name,
            f"sync epoch ({flush_reason}): no rollouts buffered; weight pull only",
        )
    else:
        total_steps = sum(r.num_timesteps() for r in buffered)
        by_ver: dict[int, list[WorkerRollout]] = {}
        for r in buffered:
            by_ver.setdefault(r.policy_version, []).append(r)
        n_posts = 0
        for ver, group in by_ver.items():
            if capacity_full:
                break
            ver_posts, ver_retained, ver_cap = _pack_and_deliver_rollouts(
                group,
                worker_id=worker_id,
                pack_max_envs=pack_max_envs,
                deliver=lambda packed: _safe_upload(client, machine_name, packed),
            )
            n_posts += ver_posts
            retained.extend(ver_retained)
            capacity_full = capacity_full or ver_cap
            log(
                machine_name,
                f"sync epoch ({flush_reason}) upload v{ver}: {len(group)} "
                f"actor-rollouts in {ver_posts} POST(s)"
                + (f", retained {len(ver_retained)}" if ver_retained else "")
                + (", capacity_full" if ver_cap else ""),
            )
        delivered = len(buffered) - len(retained)
        # Capacity-dropped packets are neither delivered nor retained.
        log(
            machine_name,
            f"sync epoch ({flush_reason}) flushed {n_posts} POST(s) from "
            f"{len(buffered)} actor-rollouts ({total_steps} steps)"
            + (f"; retained {len(retained)} for retry" if retained else "")
            + ("; capacity backpressure" if capacity_full else ""),
        )

    try:
        version, data = client.fetch_weights(min_version=policy.policy_version + 1)
        if version > policy.policy_version and data:
            policy.load_from_bytes(data, version)
            log(machine_name, f"sync epoch weight pull -> policy_version={version}")
        else:
            version, data = client.fetch_weights(min_version=0)
            if version > policy.policy_version and data:
                policy.load_from_bytes(data, version)
                log(
                    machine_name,
                    f"sync epoch weight pull (refresh) -> policy_version={version}",
                )
            else:
                log(
                    machine_name,
                    f"sync epoch: no newer weights "
                    f"(local=v{policy.policy_version}, remote=v{version})",
                )
    except Exception as exc:
        log(machine_name, f"sync epoch weight pull error: {exc}")
    return retained, capacity_full


def _local_deliver(rollout_sink: Any, rollout: WorkerRollout) -> str:
    put = rollout_sink.put
    result = put(rollout)
    if result is None or result is True:
        return "ok"
    if result is False:
        reason = str(getattr(rollout_sink, "last_reject_reason", "") or "rejected")
        return "capacity_full" if reason == "capacity_full" else "rejected"
    return "ok" if bool(result) else "rejected"


def _flush_local_epoch(
    buffered: list[WorkerRollout],
    *,
    rollout_sink: Any,
    machine_name: str,
    worker_id: str,
    policy: InferencePolicy | None = None,
    weight_store: Any | None = None,
    flush_reason: str = "timer",
) -> tuple[list[WorkerRollout], bool]:
    retained: list[WorkerRollout] = []
    capacity_full = False
    if not buffered:
        log(machine_name, f"sync epoch (local/{flush_reason}): no rollouts buffered")
    else:
        total_steps = sum(r.num_timesteps() for r in buffered)
        by_ver: dict[int, list[WorkerRollout]] = {}
        for r in buffered:
            by_ver.setdefault(r.policy_version, []).append(r)
        n_posts = 0
        deliver = lambda packed: _local_deliver(rollout_sink, packed)
        for ver, group in by_ver.items():
            if capacity_full:
                break
            ver_posts, ver_retained, ver_cap = _pack_and_deliver_rollouts(
                group,
                worker_id=worker_id,
                pack_max_envs=16,
                deliver=deliver,
            )
            n_posts += ver_posts
            retained.extend(ver_retained)
            capacity_full = capacity_full or ver_cap
            log(
                machine_name,
                f"sync epoch (local/{flush_reason}) v{ver}: {len(group)} "
                f"actor-rollouts in {ver_posts} queue put(s)"
                + (f", retained {len(ver_retained)}" if ver_retained else "")
                + (", capacity_full" if ver_cap else ""),
            )
        log(
            machine_name,
            f"sync epoch (local/{flush_reason}) flushed {n_posts} put(s) from "
            f"{len(buffered)} actor-rollouts ({total_steps} steps)"
            + (f"; retained {len(retained)} for retry" if retained else "")
            + ("; capacity backpressure" if capacity_full else ""),
        )

    # Epoch-barrier weight sync only (no mid-horizon hot-swap).
    if policy is not None and weight_store is not None:
        try:
            version = int(weight_store.policy_version)
            if version > int(policy.policy_version):
                state_dict = weight_store.get_state_dict()
                if state_dict is not None:
                    policy.load_from_state_dict(state_dict, version)
                    log(
                        machine_name,
                        f"sync epoch (local) weight pull -> policy_version={version}",
                    )
        except Exception as exc:
            log(machine_name, f"sync epoch (local) weight pull error: {exc}")

    return retained, capacity_full


def _shutdown_actors(
    stop_flag: mp.synchronize.Synchronized,
    parent_conns: list[Connection],
    processes: list[mp.Process],
) -> None:
    stop_flag.value = True
    for conn in parent_conns:
        try:
            conn.send({"t": "stop"})
        except (BrokenPipeError, OSError):
            pass
        try:
            conn.close()
        except OSError:
            pass
    for proc in processes:
        proc.join(timeout=30)
        if proc.is_alive():
            proc.terminate()


def run_async_worker_loop(
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
    rollout_sink: queue.Queue | WorkerClient,
    is_local: bool,
    sync_interval_s: float = DEFAULT_SYNC_INTERVAL_S,
    buffer_flush_steps: int = 0,
    heartbeat_s: float = 30.0,
    project_root: Path | None = None,
    headless: bool = True,
    screenshot_mmf: bool | None = None,
    inference_batch_max: int = 32,
    weight_store: Any | None = None,
    actor_ranks: list[int] | None = None,
    memlog_actor_rank: int | None = None,
) -> None:
    """Spawn desync actors and serve local inference until ``stop_event``.

    Workers buffer rollouts and flush when either ``sync_interval_s`` elapses
    (max wait) or local buffered env-steps reach ``buffer_flush_steps``
    (0 = timer-only). Remotes then pull weights; locals pull from
    ``weight_store`` only at epoch flush (no mid-horizon hot-swap).
    On learner ``capacity_full``, drop the overflow packet and pause
    buffering until a newer policy arrives.
    """
    ranks = (
        list(range(int(n_envs)))
        if actor_ranks is None
        else [int(rank) for rank in actor_ranks]
    )
    if len(ranks) != int(n_envs) or len(set(ranks)) != len(ranks):
        raise ValueError("n_envs must equal the number of unique actor ranks")
    if any(rank < 0 for rank in ranks):
        raise ValueError("actor ranks must be non-negative")
    if memlog_actor_rank is not None and int(memlog_actor_rank) not in ranks:
        raise ValueError("memlog actor rank must be one of actor_ranks")
    actor_count = len(ranks)
    buffer_cap = max(int(buffer_flush_steps), 0)
    log(
        machine_name,
        f"async worker starting ({worker_id}, {actor_count} desync actors "
        f"ranks={ranks}, "
        f"n_steps={n_steps}, sync_interval_s={sync_interval_s:.0f}, "
        f"buffer_flush_steps={buffer_cap or 'off'}, "
        f"headless={headless}, screenshot_mmf={screenshot_mmf}, "
        f"inference_batch_max={inference_batch_max})",
    )
    root = Path(project_root) if project_root else Path.cwd()
    best_log = root / "data" / "logs" / f"best_rooms_{machine_name}.jsonl"
    progress = TrainingProgressTracker(
        prefix=f"progress:{machine_name}",
        machine_name=machine_name,
        best_log_path=best_log,
    )
    local_steps = 0
    stop_flag = mp.Value("b", False)
    ctx = mp.get_context("spawn")
    processes: list[mp.Process] = []
    parent_conns: list[Connection] = []
    buffered: list[WorkerRollout] = []
    epoch_t0 = time.monotonic()
    last_heartbeat = 0.0
    last_manifest_poll = 0.0
    last_yawn_rails_poll = 0.0
    # When set, drop new actor rollouts until policy_version advances past this.
    pause_until_policy_gt: int | None = None
    hb_stop = threading.Event()

    def _heartbeat_loop() -> None:
        nonlocal last_manifest_poll, last_yawn_rails_poll
        if is_local or not isinstance(rollout_sink, WorkerClient):
            return
        while not hb_stop.is_set() and not stop_event.is_set():
            try:
                rollout_sink.heartbeat(worker_id, actor_count)
                from re1_rl.go_explore_capture import go_explore_root
                from re1_rl.go_explore_worker_cache import maybe_poll_manifest
                from re1_rl.yawn_rails_worker_cache import maybe_poll_yawn_rails_manifest

                last_manifest_poll = maybe_poll_manifest(
                    rollout_sink, go_explore_root(root), last_poll_mono=last_manifest_poll
                )
                last_yawn_rails_poll = maybe_poll_yawn_rails_manifest(
                    rollout_sink, root, last_poll_mono=last_yawn_rails_poll
                )
            except Exception as exc:
                log(machine_name, f"heartbeat error: {exc}")
            hb_stop.wait(heartbeat_s)

    hb_thread = threading.Thread(
        target=_heartbeat_loop, name="worker-heartbeat", daemon=True
    )

    try:
        if not is_local and isinstance(rollout_sink, WorkerClient):
            rollout_sink.register(worker_id, actor_count, is_local=False)
            rollout_sink.heartbeat(worker_id, actor_count)
            last_heartbeat = time.monotonic()
            hb_thread.start()

        for rank in ranks:
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
                    "memlog_directory": (
                        str(root / "data" / "memlog")
                        if memlog_actor_rank is not None
                        and rank == int(memlog_actor_rank)
                        else None
                    ),
                },
                name=f"dist-async-actor-{rank}",
            )
            proc.start()
            child_conn.close()
            processes.append(proc)
            parent_conns.append(parent_conn)

        _wait_for_actor_spawn(
            parent_conns,
            actor_count,
            processes=processes,
            actor_ranks=ranks,
        )
        log(machine_name, f"async worker fleet ready ({actor_count} actors)")
        for conn in parent_conns:
            conn.send({"t": "start"})

        def _maybe_pull_local_weights() -> None:
            if policy is None or weight_store is None:
                return
            try:
                version = int(weight_store.policy_version)
                if version > int(policy.policy_version):
                    state_dict = weight_store.get_state_dict()
                    if state_dict is not None:
                        policy.load_from_state_dict(state_dict, version)
                        log(
                            machine_name,
                            f"backpressure weight pull -> policy_version={version}",
                        )
            except Exception as exc:
                log(machine_name, f"backpressure weight pull error: {exc}")

        def _maybe_pull_remote_weights() -> None:
            if not isinstance(rollout_sink, WorkerClient):
                return
            try:
                version, data = rollout_sink.fetch_weights(
                    min_version=policy.policy_version + 1
                )
                if version > policy.policy_version and data:
                    policy.load_from_bytes(data, version)
                    log(
                        machine_name,
                        f"backpressure weight pull -> policy_version={version}",
                    )
            except Exception as exc:
                log(machine_name, f"backpressure weight pull error: {exc}")

        def _flush_buffered(reason: str) -> None:
            nonlocal buffered, pause_until_policy_gt, epoch_t0
            epoch_infos = [
                info for r in buffered for info in (r.episode_infos or [])
            ]
            capacity_full = False
            if is_local:
                buffered, capacity_full = _flush_local_epoch(
                    buffered,
                    rollout_sink=rollout_sink,
                    machine_name=machine_name,
                    worker_id=worker_id,
                    policy=policy,
                    weight_store=weight_store,
                    flush_reason=reason,
                )
            elif isinstance(rollout_sink, WorkerClient):
                buffered, capacity_full = _flush_remote_epoch(
                    buffered,
                    client=rollout_sink,
                    policy=policy,
                    machine_name=machine_name,
                    worker_id=worker_id,
                    flush_reason=reason,
                )
            if epoch_infos:
                progress.log_rollout_end(
                    None,
                    num_timesteps=local_steps,
                    episode_infos=epoch_infos,
                )
            epoch_t0 = time.monotonic()
            if capacity_full:
                pause_until_policy_gt = int(policy.policy_version)
                log(
                    machine_name,
                    f"capacity backpressure: pause buffering until "
                    f"policy_version > {pause_until_policy_gt}",
                )

        while not stop_event.is_set() and not stop_flag.value:
            if policy.policy_version <= 0:
                time.sleep(0.1)
                continue

            if pause_until_policy_gt is not None:
                if int(policy.policy_version) > int(pause_until_policy_gt):
                    log(
                        machine_name,
                        f"capacity backpressure cleared "
                        f"(policy_version={policy.policy_version})",
                    )
                    pause_until_policy_gt = None
                    epoch_t0 = time.monotonic()
                else:
                    if is_local:
                        _maybe_pull_local_weights()
                    else:
                        _maybe_pull_remote_weights()

            buffered_steps = sum(r.num_timesteps() for r in buffered)
            timer_due = (time.monotonic() - epoch_t0) >= sync_interval_s
            buffer_due = buffer_cap > 0 and buffered_steps >= buffer_cap
            if (
                pause_until_policy_gt is None
                and buffered
                and (timer_due or buffer_due)
            ):
                _flush_buffered("buffer_cap" if buffer_due and not timer_due else "timer")
            elif pause_until_policy_gt is None and timer_due and not buffered:
                # Empty timer tick: still pull weights so remotes stay current.
                _flush_buffered("timer")

            ready = wait(parent_conns, timeout=1.0)
            if not ready:
                if not any(p.is_alive() for p in processes):
                    log(machine_name, "all async actors exited")
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
                rollout = worker_rollout_from_actor_msg(
                    msg,
                    policy=policy,
                    worker_id=worker_id,
                    n_steps=n_steps,
                    curriculum=curriculum,
                )
                local_steps += int(rollout.num_timesteps())
                progress.consume_infos(
                    rollout.episode_infos,
                    num_timesteps=local_steps,
                )
                if pause_until_policy_gt is not None:
                    # Safe boundary: finish serving acts, but do not grow RAM.
                    continue
                buffered.append(rollout)

        if buffered:
            if is_local:
                buffered, _ = _flush_local_epoch(
                    buffered,
                    rollout_sink=rollout_sink,
                    machine_name=machine_name,
                    worker_id=worker_id,
                    policy=policy,
                    weight_store=weight_store,
                    flush_reason="shutdown",
                )
            elif isinstance(rollout_sink, WorkerClient):
                buffered, _ = _flush_remote_epoch(
                    buffered,
                    client=rollout_sink,
                    policy=policy,
                    machine_name=machine_name,
                    worker_id=worker_id,
                    flush_reason="shutdown",
                )
            if buffered:
                log(
                    machine_name,
                    f"shutdown flush retained {len(buffered)} actor-rollouts undelivered",
                )
    finally:
        hb_stop.set()
        if not is_local and isinstance(rollout_sink, WorkerClient):
            try:
                rollout_sink.unregister(worker_id)
            except Exception:
                pass
        _shutdown_actors(stop_flag, parent_conns, processes)
        log(machine_name, "async worker loop stopped")
