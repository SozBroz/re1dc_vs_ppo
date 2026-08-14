"""Fire-and-forget Yawn CP ingest so captures do not wait for the 6m rollout flush.

Never blocks ``env.step``. One background thread, queue depth 1: if a POST is
already in flight, drop and let the rollout hitchhiker deliver. The learner
quality-gate rejects duplicates.
"""

from __future__ import annotations

import os
import queue
import threading
from typing import Any

from re1_rl.yawn_rails_sync import yawn_rails_sync_enabled

_QUEUE: queue.Queue[dict[str, Any]] | None = None
_STARTED = False
_LOCK = threading.Lock()
_MAX_QUEUED = 1
_POST_TIMEOUT_S = 8.0


def _learner_host() -> str:
    return (
        os.environ.get("RE1_LEARNER_HOST", "").strip()
        or os.environ.get("LEARNER_HOST", "").strip()
        or os.environ.get("FLEET_LEARNER_HOST", "").strip()
    )


def _learner_port() -> int:
    raw = (
        os.environ.get("RE1_LEARNER_PORT", "").strip()
        or os.environ.get("FLEET_LEARNER_PORT", "").strip()
        or "8765"
    )
    try:
        return int(raw)
    except ValueError:
        return 8765


def _ensure_worker() -> queue.Queue[dict[str, Any]] | None:
    global _QUEUE, _STARTED
    if not yawn_rails_sync_enabled():
        return None
    if not _learner_host():
        return None
    with _LOCK:
        if _QUEUE is None:
            _QUEUE = queue.Queue(maxsize=_MAX_QUEUED)
        if not _STARTED:
            threading.Thread(
                target=_sender_loop, name="yawn-cp-ingest", daemon=True
            ).start()
            _STARTED = True
        return _QUEUE


def offer_immediate_yawn_ingest(proposal: dict[str, Any] | None) -> bool:
    """Queue a capture for HTTP ingest. True if queued. Never waits."""
    if not isinstance(proposal, dict):
        return False
    q = _ensure_worker()
    if q is None:
        return False
    try:
        q.put_nowait(proposal)
        return True
    except queue.Full:
        return False


def _sender_loop() -> None:
    from re1_rl.distributed.worker_client import WorkerClient

    host = _learner_host()
    if not host:
        return
    machine = os.environ.get("MACHINE_NAME", "worker").strip() or "worker"
    client = WorkerClient(
        host, _learner_port(), machine_name=machine, timeout=_POST_TIMEOUT_S
    )
    q = _QUEUE
    if q is None:
        return
    while True:
        prop = q.get()
        try:
            result = client.ingest_yawn_rails_proposals([prop])
            accepted = result.get("accepted") or []
            idx = int(prop.get("checkpoint_index", -1))
            print(
                f"[yawn_ingest] immediate cp{idx:02d} accepted={accepted}",
                flush=True,
            )
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            print(f"[yawn_ingest] immediate post failed: {exc!r}", flush=True)
        finally:
            q.task_done()
