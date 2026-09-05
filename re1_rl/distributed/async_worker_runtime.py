"""Desync actor fleet for distributed workers (local inference, epoch sync).

Mirrors monolithic ``async_fleet`` collection: each env is an independent actor
process; the worker process serves ``need`` / ``act`` via a local
``InferencePolicy``. Remotes buffer rollouts and touch the network once per
``sync_interval_s`` (upload burst + weight pull). Local workers enqueue
in-process with no HTTP.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import queue
import subprocess
import threading
import time
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any, Callable

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


DEFAULT_ACTOR_STALE_TIMEOUT_S = 720.0
DEFAULT_EMUHAWK_HUNG_S = 30.0
DEFAULT_ACTOR_RECOVER_COOLDOWN_S = 45.0


def _pid_not_responding(pid: int | None) -> bool:
    """True when a Windows process has a visible hung window (Not Responding).

    C-RE1 lockstep blocks the SDL/main thread on pad WaitForSingleObject between
    STEPs, so IsHungAppWindow false-triggers and the watchdog death-spirals
    visible workers. Skip the check on the recomp bridge.
    """
    if os.environ.get("RE1_ECOSYSTEM_BRIDGE", "").strip().lower() == "recomp":
        return False
    if os.name != "nt" or not pid:
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd: int, _lparam: int) -> bool:
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if int(proc_id.value) == int(pid) and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    try:
        user32.EnumWindows(_enum, 0)
    except OSError:
        return False
    if not found:
        return False
    is_hung = user32.IsHungAppWindow
    is_hung.argtypes = [wintypes.HWND]
    is_hung.restype = wintypes.BOOL
    try:
        return any(bool(is_hung(hwnd)) for hwnd in found)
    except OSError:
        return False


def _hung_actor_indices(
    emu_pids: list[int | None],
    hung_since: list[float | None],
    *,
    now: float,
    hung_s: float,
    is_hung: Callable[[int | None], bool] | None = None,
) -> list[int]:
    """Ranks whose EmuHawk has been Not Responding past ``hung_s``.

    Savestate load can freeze the UI for a few seconds; the grace window
    avoids treating that as a dead actor. A frozen hawk can still emit
    ``need`` messages, so silence-only watchdog never fires.
    """
    check = is_hung or _pid_not_responding
    hung: list[int] = []
    deadline = max(0.0, float(hung_s))
    for index, pid in enumerate(emu_pids):
        if pid and check(pid):
            if hung_since[index] is None:
                hung_since[index] = now
            if now - float(hung_since[index]) >= deadline:
                hung.append(index)
        else:
            hung_since[index] = None
    return hung


def _stale_actor_indices(
    processes: list[mp.Process],
    last_activity: list[float],
    *,
    now: float,
    timeout_s: float,
    exempt_indices: set[int] | None = None,
    hung_indices: set[int] | None = None,
) -> list[int]:
    """Return dead ranks immediately and live ranks silent past the deadline."""
    exempt = exempt_indices or set()
    hung = hung_indices or set()
    stale: list[int] = []
    for index, (proc, last) in enumerate(zip(processes, last_activity)):
        if not proc.is_alive():
            stale.append(index)
        elif index in hung:
            stale.append(index)
        elif index not in exempt and now - float(last) >= float(timeout_s):
            stale.append(index)
    return stale


def _startup_rank_batches(
    ranks: list[int], *, batch_size: int
) -> list[list[int]]:
    """Split initial emulator launches into bounded pressure-safe waves."""
    size = int(batch_size)
    if size <= 0:
        raise ValueError("actor startup batch_size must be positive")
    return [ranks[index : index + size] for index in range(0, len(ranks), size)]


def _credit_parent_block(last_activity: list[float], blocked_s: float) -> None:
    """Do not count parent-side stalls as actor silence.

    Watchdog recovery and epoch flush run on the same thread that drains
    ``need`` messages. A 30s respawn would otherwise make every other rank
    look stale and trigger a stampede restart.
    """
    credit = max(0.0, float(blocked_s))
    if credit <= 0.0:
        return
    for index, last in enumerate(last_activity):
        if last == float("-inf"):
            continue
        last_activity[index] = float(last) + credit


def _terminate_actor_process(
    proc: mp.Process,
    *,
    emuhawk_pid: int | None = None,
    timeout_s: float = 5.0,
) -> None:
    """Terminate an actor and its owned EmuHawk process tree."""
    if proc.is_alive() and os.name == "nt" and getattr(proc, "pid", None):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(int(proc.pid)), "/T", "/F"],
                check=False,
                capture_output=True,
                timeout=max(1.0, float(timeout_s)),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    if proc.is_alive():
        try:
            proc.terminate()
        except (OSError, ValueError):
            pass
    proc.join(timeout=max(0.0, float(timeout_s)))
    if proc.is_alive():
        try:
            proc.kill()
        except (AttributeError, OSError, ValueError):
            pass
        proc.join(timeout=max(1.0, float(timeout_s)))
    if os.name == "nt" and emuhawk_pid:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(int(emuhawk_pid)), "/T", "/F"],
                check=False,
                capture_output=True,
                timeout=max(1.0, float(timeout_s)),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


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
    eval_only: bool = False,
) -> tuple[list[WorkerRollout], bool]:
    """Upload buffered experience (burst), then pull weights once.

    Returns ``(retained, capacity_full)``. Capacity-full packets are dropped.
    When ``eval_only``, rollouts are discarded locally and never uploaded.
    """
    retained: list[WorkerRollout] = []
    capacity_full = False
    if eval_only and buffered:
        total_steps = sum(r.num_timesteps() for r in buffered)
        log(
            machine_name,
            f"sync epoch ({flush_reason}) eval-only: discarded "
            f"{len(buffered)} actor-rollouts ({total_steps} steps); weight pull only",
        )
        buffered = []
    elif not buffered:
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
    eval_only: bool = False,
) -> tuple[list[WorkerRollout], bool]:
    retained: list[WorkerRollout] = []
    capacity_full = False
    if eval_only and buffered:
        total_steps = sum(r.num_timesteps() for r in buffered)
        log(
            machine_name,
            f"sync epoch (local/{flush_reason}) eval-only: discarded "
            f"{len(buffered)} actor-rollouts ({total_steps} steps)",
        )
        buffered = []
    elif not buffered:
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


def _kill_local_recomp_exes() -> None:
    """Drop leftover C-RE1 windows on this box (one recomp worker per host)."""
    if os.name != "nt":
        return
    try:
        subprocess.run(
            [
                "taskkill",
                "/IM",
                "Resident_Evil_Director_s_Cut_Recompiled.exe",
                "/F",
            ],
            check=False,
            capture_output=True,
            timeout=15.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _cleanup_worker_port_emuhawks(
    base_port: int,
    n_envs: int,
    *,
    project_root: Path | None = None,
) -> None:
    """Kill orphan EmuHawks still claimed on this worker's TCP port range."""
    if os.name != "nt" or int(n_envs) <= 0:
        return
    from re1_rl.window_grid import port_map_dir

    d = port_map_dir(project_root)
    if not d.is_dir():
        return
    port_lo = int(base_port)
    port_hi = port_lo + int(n_envs) - 1
    for path in list(d.iterdir()):
        if not path.is_file():
            continue
        try:
            pid = int(path.name)
            port = int(path.read_text(encoding="ascii").strip())
        except (ValueError, OSError):
            continue
        if port_lo <= port <= port_hi:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    timeout=5.0,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    if os.environ.get("RE1_ECOSYSTEM_BRIDGE", "").strip().lower() == "recomp":
        _kill_local_recomp_exes()


def _shutdown_actors(
    stop_flag: mp.synchronize.Synchronized,
    parent_conns: list[Connection],
    processes: list[mp.Process],
    emuhawk_pids: list[int | None] | None = None,
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
    deadline = time.monotonic() + 30.0
    for proc in processes:
        proc.join(timeout=max(0.0, deadline - time.monotonic()))
    pids = emuhawk_pids or [None] * len(processes)
    for proc, emu_pid in zip(processes, pids):
        if proc.is_alive() or emu_pid:
            _terminate_actor_process(proc, emuhawk_pid=emu_pid)


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
    eval_only: bool = False,
    health_callback: Callable[[int], None] | None = None,
) -> None:
    """Spawn desync actors and serve local inference until ``stop_event``.

    Workers buffer rollouts and flush when either ``sync_interval_s`` elapses
    (max wait) or local buffered env-steps reach ``buffer_flush_steps``
    (0 = timer-only). Remotes then pull weights; locals pull from
    ``weight_store`` only at epoch flush (no mid-horizon hot-swap).
    On learner ``capacity_full``, drop the overflow packet and pause
    buffering until the learner reopens admission (next cohort) or a
    newer policy arrives.
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
    try:
        startup_batch_size = int(
            os.environ.get("RE1_ACTOR_STARTUP_BATCH_SIZE", "4")
        )
    except ValueError:
        startup_batch_size = 4
    startup_batches = _startup_rank_batches(
        ranks, batch_size=startup_batch_size
    )
    buffer_cap = max(int(buffer_flush_steps), 0)
    log(
        machine_name,
        f"async worker starting ({worker_id}, {actor_count} desync actors "
        f"ranks={ranks}, "
        f"n_steps={n_steps}, sync_interval_s={sync_interval_s:.0f}, "
        f"buffer_flush_steps={buffer_cap or 'off'}, "
        f"headless={headless}, screenshot_mmf={screenshot_mmf}, "
        f"inference_batch_max={inference_batch_max}, "
        f"startup_batch_size={startup_batch_size}, "
        f"eval_only={eval_only})",
    )
    root = Path(project_root) if project_root else Path.cwd()
    _cleanup_worker_port_emuhawks(int(base_port), actor_count, project_root=root)
    best_log = root / "data" / "logs" / f"best_rooms_{machine_name}.jsonl"
    progress = TrainingProgressTracker(
        prefix=f"progress:{machine_name}",
        machine_name=machine_name,
        best_log_path=best_log,
    )
    local_steps = 0
    stop_flag = mp.Value("b", False)
    ctx = mp.get_context("spawn")
    try:
        mp.set_executable(sys.executable)
    except (OSError, AttributeError, RuntimeError):
        pass
    processes: list[mp.Process] = []
    parent_conns: list[Connection] = []
    actor_emu_pids: list[int | None] = []
    buffered: list[WorkerRollout] = []
    epoch_t0 = time.monotonic()
    last_heartbeat = 0.0
    last_manifest_poll = 0.0
    last_yawn_rails_poll = 0.0
    # When set, drop new actor rollouts until policy advances or cohort reopens.
    pause_until_policy_gt: int | None = None
    hb_stop = threading.Event()
    stale_timeout_s = max(
        30.0,
        float(
            os.environ.get(
                "RE1_ACTOR_STALE_TIMEOUT_S",
                str(DEFAULT_ACTOR_STALE_TIMEOUT_S),
            )
        ),
    )
    hung_timeout_s = max(
        5.0,
        float(
            os.environ.get(
                "RE1_EMUHAWK_HUNG_S",
                str(DEFAULT_EMUHAWK_HUNG_S),
            )
        ),
    )
    recover_cooldown_s = max(
        0.0,
        float(
            os.environ.get(
                "RE1_ACTOR_RECOVER_COOLDOWN_S",
                str(DEFAULT_ACTOR_RECOVER_COOLDOWN_S),
            )
        ),
    )
    health_lock = threading.Lock()
    healthy_actor_count = 0

    def _set_healthy_actor_count(value: int) -> None:
        nonlocal healthy_actor_count
        healthy = max(0, int(value))
        with health_lock:
            healthy_actor_count = healthy
        if health_callback is not None:
            try:
                health_callback(healthy)
            except Exception as exc:
                log(machine_name, f"health callback error: {exc}")

    def _get_healthy_actor_count() -> int:
        with health_lock:
            return int(healthy_actor_count)

    def _learner_accepting_rollouts() -> bool:
        """True when the learner can admit more steps (next cohort open)."""
        if is_local:
            state = getattr(rollout_sink, "_state", None)
            if state is not None and hasattr(state, "cohort_full"):
                return not bool(state.cohort_full())
            return True
        if isinstance(rollout_sink, WorkerClient):
            try:
                status = rollout_sink.fetch_status()
                return not bool(status.get("cohort_full", False))
            except Exception:
                return False
        return True

    def _heartbeat_loop() -> None:
        nonlocal last_manifest_poll, last_yawn_rails_poll
        if is_local or not isinstance(rollout_sink, WorkerClient):
            return
        while not hb_stop.is_set() and not stop_event.is_set():
            try:
                rollout_sink.heartbeat(worker_id, _get_healthy_actor_count())
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

    def _spawn_rank(rank: int) -> tuple[mp.Process, Connection]:
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
                    str(
                        root
                        / "data"
                        / (
                            os.environ.get("RE1_MEMLOG_DIRECTORY", "memlog").strip()
                            or "memlog"
                        )
                    )
                    if memlog_actor_rank is not None
                    and rank == int(memlog_actor_rank)
                    else None
                ),
            },
            name=f"dist-async-actor-{rank}",
        )
        proc.start()
        child_conn.close()
        return proc, parent_conn

    try:
        if not is_local and isinstance(rollout_sink, WorkerClient):
            rollout_sink.register(worker_id, 0, is_local=False)
            rollout_sink.heartbeat(worker_id, 0)
            last_heartbeat = time.monotonic()
            hb_thread.start()

        for batch_number, batch_ranks in enumerate(startup_batches, start=1):
            batch_processes: list[mp.Process] = []
            batch_conns: list[Connection] = []
            batch_indices: list[int] = []
            log(
                machine_name,
                f"actor startup batch {batch_number}/{len(startup_batches)} "
                f"ranks={batch_ranks}",
            )
            for rank in batch_ranks:
                proc, parent_conn = _spawn_rank(rank)
                batch_indices.append(len(processes))
                processes.append(proc)
                parent_conns.append(parent_conn)
                actor_emu_pids.append(None)
                batch_processes.append(proc)
                batch_conns.append(parent_conn)
            batch_pids = _wait_for_actor_spawn(
                batch_conns,
                len(batch_ranks),
                processes=batch_processes,
                actor_ranks=batch_ranks,
            )
            for index, rank in zip(batch_indices, batch_ranks):
                actor_emu_pids[index] = batch_pids.get(rank)
            if len(processes) >= 20:
                try:
                    cooldown_s = float(
                        os.environ.get("RE1_ACTOR_STARTUP_BATCH_COOLDOWN_S", "0")
                    )
                except ValueError:
                    cooldown_s = 0.0
                if cooldown_s > 0:
                    time.sleep(cooldown_s)
        log(machine_name, f"async worker fleet ready ({actor_count} actors)")
        for conn in parent_conns:
            conn.send({"t": "start"})
        last_actor_activity = [time.monotonic()] * actor_count
        last_recover_at = [0.0] * actor_count
        hung_since: list[float | None] = [None] * actor_count
        healthy_indices: set[int] = set()
        _set_healthy_actor_count(0)

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
                    eval_only=eval_only,
                )
            elif isinstance(rollout_sink, WorkerClient):
                buffered, capacity_full = _flush_remote_epoch(
                    buffered,
                    client=rollout_sink,
                    policy=policy,
                    machine_name=machine_name,
                    worker_id=worker_id,
                    flush_reason=reason,
                    eval_only=eval_only,
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
                    f"cohort reopens or policy_version > {pause_until_policy_gt}",
                )

        def _recover_actor_indices(indices: list[int]) -> None:
            stale_ranks = [ranks[index] for index in indices]
            for index in indices:
                healthy_indices.discard(index)
            _set_healthy_actor_count(len(healthy_indices))
            log(
                machine_name,
                f"actor watchdog recovering stale ranks={stale_ranks} "
                f"(healthy={len(healthy_indices)}/{actor_count}, "
                f"timeout_s={stale_timeout_s:.0f}, serial=1)",
            )
            for index in indices:
                try:
                    parent_conns[index].close()
                except OSError:
                    pass
                _terminate_actor_process(
                    processes[index], emuhawk_pid=actor_emu_pids[index]
                )
                actor_emu_pids[index] = None
                hung_since[index] = None

            for attempt in range(1, 3):
                replacements: list[tuple[int, mp.Process, Connection]] = []
                try:
                    for index in indices:
                        proc, conn = _spawn_rank(ranks[index])
                        replacements.append((index, proc, conn))
                    replacement_procs = [proc for _, proc, _ in replacements]
                    replacement_conns = [conn for _, _, conn in replacements]
                    replacement_ranks = [
                        ranks[index] for index, _, _ in replacements
                    ]
                    replacement_pids = _wait_for_actor_spawn(
                        replacement_conns,
                        len(replacements),
                        processes=replacement_procs,
                        actor_ranks=replacement_ranks,
                        timeout_s=180.0,
                    )
                    now = time.monotonic()
                    for index, proc, conn in replacements:
                        conn.send({"t": "start"})
                        processes[index] = proc
                        parent_conns[index] = conn
                        actor_emu_pids[index] = replacement_pids.get(ranks[index])
                        last_actor_activity[index] = now
                    log(
                        machine_name,
                        f"actor watchdog restarted ranks={stale_ranks}; "
                        "awaiting first post-reset activity",
                    )
                    return
                except Exception as exc:
                    log(
                        machine_name,
                        f"actor watchdog attempt {attempt}/2 failed for "
                        f"ranks={stale_ranks}: {exc}",
                    )
                    for index, proc, conn in replacements:
                        try:
                            conn.close()
                        except OSError:
                            pass
                        _terminate_actor_process(proc)
                        processes[index] = proc
                        parent_conns[index] = conn
                        actor_emu_pids[index] = None
                    if attempt < 2:
                        time.sleep(5.0 * attempt)
            log(
                machine_name,
                f"actor watchdog left ranks={stale_ranks} degraded; "
                "retrying on the next watchdog pass",
            )

        while not stop_event.is_set() and not stop_flag.value:
            if eval_only:
                try:
                    from re1_rl.fight_eval_episodes import fight_eval_should_stop

                    if fight_eval_should_stop(root):
                        log(
                            machine_name,
                            "fight eval episode cap reached; stopping actors",
                        )
                        stop_flag.value = True
                        break
                except Exception:
                    pass

            now = time.monotonic()
            hung_indices = set(
                _hung_actor_indices(
                    actor_emu_pids,
                    hung_since,
                    now=now,
                    hung_s=hung_timeout_s,
                )
            )
            stale_indices = _stale_actor_indices(
                processes,
                last_actor_activity,
                now=now,
                timeout_s=stale_timeout_s,
                hung_indices=hung_indices,
            )
            if stale_indices:
                # One rank per pass. Recovering N in parallel restampedes
                # EmuHawk and, because this loop also serves inference,
                # makes every other rank look silent.
                ready = [
                    index
                    for index in stale_indices
                    if (now - last_recover_at[index]) >= recover_cooldown_s
                ]
                if ready:
                    target = ready[:1]
                    last_recover_at[target[0]] = now
                    hung_hit = [ranks[index] for index in target if index in hung_indices]
                    if hung_hit:
                        log(
                            machine_name,
                            f"actor watchdog emuhawk hung ranks={hung_hit} "
                            f"(>{hung_timeout_s:.0f}s Not Responding)",
                        )
                    blocked_at = time.monotonic()
                    _recover_actor_indices(target)
                    _credit_parent_block(
                        last_actor_activity, time.monotonic() - blocked_at
                    )
                    continue

            if policy.policy_version <= 0:
                time.sleep(0.1)
                continue

            if pause_until_policy_gt is not None:
                policy_advanced = int(policy.policy_version) > int(pause_until_policy_gt)
                cohort_reopened = _learner_accepting_rollouts()
                if policy_advanced or cohort_reopened:
                    why = "policy" if policy_advanced else "cohort_reopen"
                    log(
                        machine_name,
                        f"capacity backpressure cleared ({why}, "
                        f"policy_version={policy.policy_version})",
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
                blocked_at = time.monotonic()
                _flush_buffered("buffer_cap" if buffer_due and not timer_due else "timer")
                _credit_parent_block(
                    last_actor_activity, time.monotonic() - blocked_at
                )
            elif pause_until_policy_gt is None and timer_due and not buffered:
                # Empty timer tick: still pull weights so remotes stay current.
                blocked_at = time.monotonic()
                _flush_buffered("timer")
                _credit_parent_block(
                    last_actor_activity, time.monotonic() - blocked_at
                )

            ready = wait(parent_conns, timeout=1.0)
            if not ready:
                if not any(p.is_alive() for p in processes):
                    log(machine_name, "all async actors exited")
                    break
                continue

            needs, rollouts, failed_conns = _drain_actor_messages(
                ready,
                parent_conns,
                max_need_batch=inference_batch_max,
            )
            conn_to_index = {id(conn): index for index, conn in enumerate(parent_conns)}
            failed_conn_ids = {id(conn) for conn in failed_conns}
            safe_needs = [
                (conn, msg) for conn, msg in needs if id(conn) not in failed_conn_ids
            ]
            send_failed = (
                _serve_needs_batch(
                    safe_needs, policy, max_batch=inference_batch_max
                )
                if safe_needs
                else []
            )
            all_failed = [*failed_conns, *send_failed]
            all_failed_ids = {id(conn) for conn in all_failed}
            for conn in all_failed:
                index = conn_to_index.get(id(conn))
                if index is not None:
                    last_actor_activity[index] = float("-inf")
                    healthy_indices.discard(index)
            if all_failed:
                _set_healthy_actor_count(len(healthy_indices))
            activity_now = time.monotonic()
            health_changed = False
            for conn, _msg in [*needs, *rollouts]:
                if id(conn) in all_failed_ids:
                    continue
                index = conn_to_index.get(id(conn))
                if index is not None:
                    last_actor_activity[index] = activity_now
                    if index not in healthy_indices:
                        healthy_indices.add(index)
                        health_changed = True
            if health_changed:
                _set_healthy_actor_count(len(healthy_indices))
                log(
                    machine_name,
                    f"actor health {len(healthy_indices)}/{actor_count} "
                    f"ranks={[ranks[index] for index in sorted(healthy_indices)]}",
                )
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
                if not eval_only:
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
                    eval_only=eval_only,
                )
            elif isinstance(rollout_sink, WorkerClient):
                buffered, _ = _flush_remote_epoch(
                    buffered,
                    client=rollout_sink,
                    policy=policy,
                    machine_name=machine_name,
                    worker_id=worker_id,
                    flush_reason="shutdown",
                    eval_only=eval_only,
                )
            if buffered:
                log(
                    machine_name,
                    f"shutdown flush retained {len(buffered)} actor-rollouts undelivered",
                )
    finally:
        hb_stop.set()
        if hb_thread.is_alive():
            hb_thread.join(timeout=max(1.0, min(float(heartbeat_s), 10.0)))
        if not is_local and isinstance(rollout_sink, WorkerClient):
            try:
                rollout_sink.unregister(worker_id)
            except Exception:
                pass
        _shutdown_actors(stop_flag, parent_conns, processes, actor_emu_pids)
        _cleanup_worker_port_emuhawks(
            int(base_port), actor_count, project_root=root
        )
        log(machine_name, "async worker loop stopped")
