"""HTTP learner surface for remote workers."""

from __future__ import annotations

import base64
import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from re1_rl.distributed.log_util import log
from re1_rl.distributed.relevance_gate import DEFAULT_RELEVANCE_MAX_AGE
from re1_rl.distributed.rollout_codec import decode_rollout
from re1_rl.distributed.rollout_types import WorkerRollout, normalize_curriculum_id
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.go_explore_merge import GoExploreMerge, extract_proposals_from_infos
from re1_rl.yawn_rails_sync import (
    YawnRailsCellStore,
    extract_yawn_rails_proposals,
    yawn_rails_store_from_env,
)


def base_worker_id(worker_id: str) -> str:
    """Strip ``:actor_N`` suffix so contribution is per machine."""
    return str(worker_id).split(":", 1)[0]


def _go_explore_merge_from_env() -> GoExploreMerge | None:
    """Attach merge when ``RE1_GO_EXPLORE_ARCHIVE`` is set (canonical learner path)."""
    sync = os.environ.get("RE1_GO_EXPLORE_SYNC", "1").strip().lower()
    if sync in {"0", "false", "no", "off"}:
        return None
    raw = os.environ.get("RE1_GO_EXPLORE_ARCHIVE", "").strip()
    if not raw:
        return None
    return GoExploreMerge(Path(raw))


def _yawn_rails_store_from_env() -> YawnRailsCellStore | None:
    """Attach Yawn rails store unless explicitly disabled."""
    from re1_rl.yawn_rails_sync import yawn_rails_sync_enabled

    if not yawn_rails_sync_enabled():
        return None
    root = os.environ.get("RE1_YAWN_RAILS_ROOT", "").strip()
    project = os.environ.get("RE1_PROJECT_ROOT", "").strip()
    if root:
        return YawnRailsCellStore(Path(root))
    if project:
        return yawn_rails_store_from_env(Path(project))
    return yawn_rails_store_from_env()


class LearnerState:
    def __init__(
        self,
        weight_store: WeightStore,
        rollout_queue: queue.Queue[WorkerRollout],
        *,
        machine_name: str,
        max_staleness: int,
        worker_liveness_s: float = 90.0,
        relevance_gate: bool = False,
        relevance_max_age: int | None = None,
        go_explore_merge: GoExploreMerge | None = None,
        yawn_rails_store: YawnRailsCellStore | None = None,
        expected_curriculum_id: str = "",
        expected_obs_schema_version: int | None = None,
        max_pending_steps: int = 0,
    ) -> None:
        self.weight_store = weight_store
        self.rollout_queue = rollout_queue
        self.machine_name = machine_name
        self.max_staleness = max_staleness
        self.worker_liveness_s = float(worker_liveness_s)
        # 0 = unlimited. Otherwise admit at most this many env-steps per epoch
        # (first rollout always accepted even if alone it exceeds the cap).
        self.max_pending_steps = max(int(max_pending_steps), 0)
        # Soft-accept stale (version behind max_staleness) up to relevance_max_age;
        # train_on_rollouts applies the π_new/π_old ownership gate.
        self.relevance_gate = bool(relevance_gate)
        self.relevance_max_age = int(
            relevance_max_age
            if relevance_max_age is not None
            else max(int(max_staleness), DEFAULT_RELEVANCE_MAX_AGE)
        )
        # Empty / None = identity checks disabled (unit tests). Production passes both.
        self.expected_curriculum_id = normalize_curriculum_id(expected_curriculum_id)
        self.expected_obs_schema_version = (
            None
            if expected_obs_schema_version is None
            else int(expected_obs_schema_version)
        )
        self.current_policy_version = 0
        self.lock = threading.Lock()
        # worker_id -> {n_envs, hostname, last_seen, is_local}
        self.workers: dict[str, dict[str, Any]] = {}
        self.rollouts_accepted = 0
        self.rollouts_rejected = 0
        self.rollouts_stale_queued = 0  # accepted for train-time relevance gate
        self.rollouts_rejected_stale = 0
        self.relevance_kept = 0
        self.relevance_dropped = 0
        # Env-step accounting for pitch % (ingest + relevance gate).
        self.steps_accepted = 0
        self.steps_rejected_ingest = 0
        self.steps_stale_queued = 0
        self.steps_rejected_stale = 0
        self.steps_relevance_kept = 0
        self.steps_relevance_dropped = 0
        self.rollouts_rejected_identity = 0
        self.steps_rejected_identity = 0
        self.rollouts_rejected_capacity = 0
        self.steps_rejected_capacity = 0
        self.epoch_admitted_steps = 0
        self.epoch_id = 0
        self.epoch_contributors: set[str] = set()
        self.epoch_expected: set[str] = set()
        self.rollouts_rejected_duplicate = 0
        self.go_explore_merge = (
            go_explore_merge if go_explore_merge is not None else _go_explore_merge_from_env()
        )
        self.go_explore_accepted = 0
        self.go_explore_rejected_semantic = 0
        self.go_explore_evicted = 0
        self.yawn_rails_store = (
            yawn_rails_store
            if yawn_rails_store is not None
            else _yawn_rails_store_from_env()
        )
        self.yawn_rails_accepted = 0
        self.yawn_rails_rejected = 0

    def check_rollout_identity(self, rollout: WorkerRollout) -> tuple[bool, str]:
        """Fail closed when curriculum/schema does not match the learner."""
        if self.expected_curriculum_id:
            cid = normalize_curriculum_id(rollout.curriculum_id)
            if not cid:
                return False, "missing_curriculum_id"
            if cid != self.expected_curriculum_id:
                return False, "curriculum_mismatch"
        if self.expected_obs_schema_version is not None:
            schema = int(rollout.obs_schema_version or 0)
            if schema != int(self.expected_obs_schema_version):
                return False, "obs_schema_mismatch"
        return True, "ok"

    def set_current_version(self, version: int) -> None:
        with self.lock:
            self.current_policy_version = version

    def register_worker(
        self,
        worker_id: str,
        *,
        n_envs: int | None = None,
        hostname: str | None = None,
        is_local: bool = False,
    ) -> None:
        wid = base_worker_id(worker_id)
        now = time.monotonic()
        with self.lock:
            prev = self.workers.get(wid, {})
            self.workers[wid] = {
                "n_envs": n_envs if n_envs is not None else prev.get("n_envs"),
                "hostname": hostname if hostname is not None else prev.get("hostname"),
                "last_seen": now,
                "is_local": bool(is_local or prev.get("is_local", False)),
            }
        log(self.machine_name, f"worker registered: {wid} local={is_local}")

    def heartbeat_worker(
        self,
        worker_id: str,
        *,
        n_envs: int | None = None,
        hostname: str | None = None,
    ) -> None:
        wid = base_worker_id(worker_id)
        now = time.monotonic()
        with self.lock:
            prev = self.workers.get(wid)
            if prev is None:
                self.workers[wid] = {
                    "n_envs": n_envs,
                    "hostname": hostname,
                    "last_seen": now,
                    "is_local": False,
                }
                log(self.machine_name, f"worker heartbeat (auto-register): {wid}")
            else:
                if n_envs is not None:
                    prev["n_envs"] = n_envs
                if hostname is not None:
                    prev["hostname"] = hostname
                prev["last_seen"] = now

    def unregister_worker(self, worker_id: str) -> None:
        wid = base_worker_id(worker_id)
        with self.lock:
            self.workers.pop(wid, None)
            self.epoch_contributors.discard(wid)
        log(self.machine_name, f"worker unregistered: {wid}")

    def _prune_and_list_live_unlocked(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        live: dict[str, dict[str, Any]] = {}
        dead: list[str] = []
        for wid, meta in self.workers.items():
            if meta.get("is_local"):
                live[wid] = dict(meta)
                continue
            age = now - float(meta.get("last_seen", 0.0))
            if age <= self.worker_liveness_s:
                live[wid] = dict(meta)
            else:
                dead.append(wid)
        for wid in dead:
            self.workers.pop(wid, None)
            self.epoch_contributors.discard(wid)
            self.epoch_expected.discard(wid)
        if dead:
            log(
                self.machine_name,
                f"dropped dead workers (no heartbeat >{self.worker_liveness_s:.0f}s): "
                f"{dead}",
            )
        return live

    def live_workers(self) -> dict[str, dict[str, Any]]:
        """Workers with a recent heartbeat (or local, always live while registered)."""
        with self.lock:
            return self._prune_and_list_live_unlocked()

    def mark_contributor(self, worker_id: str) -> None:
        wid = base_worker_id(worker_id)
        with self.lock:
            self.epoch_contributors.add(wid)

    def begin_epoch(self) -> tuple[int, list[str]]:
        """Start a new epoch; snapshot currently live workers as expected set."""
        with self.lock:
            self.epoch_id += 1
            self.epoch_contributors.clear()
            self.epoch_admitted_steps = 0
            live = self._prune_and_list_live_unlocked()
            self.epoch_expected = set(live.keys())
            return self.epoch_id, sorted(self.epoch_expected)

    def cohort_full(self) -> bool:
        """True when this epoch has admitted at least ``max_pending_steps``."""
        with self.lock:
            return (
                self.max_pending_steps > 0
                and self.epoch_admitted_steps >= self.max_pending_steps
            )

    def admitted_steps(self) -> int:
        with self.lock:
            return int(self.epoch_admitted_steps)

    def ingest_go_explore_from_rollout(self, rollout: WorkerRollout) -> list[str]:
        """Merge capture proposals from accepted rollout episode infos."""
        merge = self.go_explore_merge
        if merge is None:
            return []
        proposals = extract_proposals_from_infos(rollout.episode_infos)
        if not proposals:
            return []
        try:
            accepted = merge.ingest_proposals(proposals)
        except Exception as exc:
            log(self.machine_name, f"go_explore merge failed: {exc}")
            return []
        with self.lock:
            if accepted:
                self.go_explore_accepted += len(accepted)
            self.go_explore_rejected_semantic = int(merge.rejected_semantic)
            self.go_explore_evicted = int(merge.evicted)
        if accepted:
            log(
                self.machine_name,
                f"go_explore merged {len(accepted)} cell(s) from {rollout.worker_id}",
            )
        return accepted

    def ingest_yawn_rails_proposals(
        self,
        proposals: list[dict[str, Any]],
        *,
        source: str = "http",
    ) -> list[str]:
        """Admit/replace Yawn rails cells (quality-gated, no training queue)."""
        store = self.yawn_rails_store
        if store is None or not proposals:
            return []
        try:
            accepted = store.ingest_proposals(proposals)
        except Exception as exc:
            log(self.machine_name, f"yawn_rails ingest failed ({source}): {exc}")
            return []
        with self.lock:
            if accepted:
                self.yawn_rails_accepted += len(accepted)
            self.yawn_rails_rejected = int(store.rejected)
        if accepted:
            log(
                self.machine_name,
                f"yawn_rails merged {len(accepted)} cell(s) via {source}",
            )
        return accepted

    def ingest_yawn_rails_from_rollout(self, rollout: WorkerRollout) -> list[str]:
        """Merge Yawn rails checkpoint cells from accepted rollout episode infos."""
        proposals = extract_yawn_rails_proposals(rollout.episode_infos)
        if not proposals:
            return []
        return self.ingest_yawn_rails_proposals(
            proposals,
            source=str(rollout.worker_id),
        )

    def _log_rollout_staleness(
        self,
        rollout: WorkerRollout,
        *,
        reason: str,
    ) -> None:
        lag = int(self.current_policy_version) - int(rollout.policy_version)
        log(
            self.machine_name,
            f"rollout {reason} from {rollout.worker_id} "
            f"v{rollout.policy_version} lag={lag} "
            f"(current={self.current_policy_version}, "
            f"max_staleness={self.max_staleness}, "
            f"relevance_max_age={self.relevance_max_age}) "
            f"+{rollout.num_timesteps()} steps",
        )

    def accept_rollout(self, rollout: WorkerRollout) -> tuple[bool, str]:
        wid = base_worker_id(rollout.worker_id)
        steps = int(rollout.num_timesteps())
        identity_ok, identity_reason = self.check_rollout_identity(rollout)
        if not identity_ok:
            with self.lock:
                self.rollouts_rejected += 1
                self.rollouts_rejected_identity += 1
                self.steps_rejected_ingest += steps
                self.steps_rejected_identity += steps
            return False, identity_reason
        with self.lock:
            # Capacity gate: stop admitting once the epoch cohort is full.
            # Always allow the first rollout of an empty epoch so a single
            # oversized packet can still train (better than stalling forever).
            if (
                self.max_pending_steps > 0
                and self.epoch_admitted_steps > 0
                and self.epoch_admitted_steps + steps > self.max_pending_steps
            ):
                self.rollouts_rejected += 1
                self.rollouts_rejected_capacity += 1
                self.steps_rejected_ingest += steps
                self.steps_rejected_capacity += steps
                return False, "capacity_full"
            min_ok = self.current_policy_version - self.max_staleness
            if rollout.policy_version < min_ok:
                if self.relevance_gate:
                    min_gated = self.current_policy_version - self.relevance_max_age
                    if rollout.policy_version >= min_gated:
                        self.rollouts_accepted += 1
                        self.rollouts_stale_queued += 1
                        self.steps_accepted += steps
                        self.steps_stale_queued += steps
                        self.epoch_admitted_steps += steps
                        self.epoch_contributors.add(wid)
                        self.rollout_queue.put(rollout)
                        reason = "stale_queued_for_relevance_gate"
                    else:
                        self.rollouts_rejected += 1
                        self.rollouts_rejected_stale += 1
                        self.steps_rejected_ingest += steps
                        self.steps_rejected_stale += steps
                        self._log_rollout_staleness(
                            rollout, reason="stale_policy_version (hard reject)"
                        )
                        return False, "stale_policy_version"
                else:
                    self.rollouts_rejected += 1
                    self.rollouts_rejected_stale += 1
                    self.steps_rejected_ingest += steps
                    self.steps_rejected_stale += steps
                    self._log_rollout_staleness(
                        rollout, reason="stale_policy_version (hard reject)"
                    )
                    return False, "stale_policy_version"
            else:
                self.rollouts_accepted += 1
                self.steps_accepted += steps
                self.epoch_admitted_steps += steps
                self.epoch_contributors.add(wid)
                reason = "ok"
        if reason == "ok":
            self.rollout_queue.put(rollout)
        elif reason == "stale_queued_for_relevance_gate":
            self._log_rollout_staleness(
                rollout, reason="stale_queued_for_relevance_gate (soft accept)"
            )
        self.ingest_go_explore_from_rollout(rollout)
        self.ingest_yawn_rails_from_rollout(rollout)
        return True, reason

    def record_relevance_stats(
        self,
        *,
        kept: int,
        dropped: int,
        steps_kept: int = 0,
        steps_dropped: int = 0,
    ) -> None:
        with self.lock:
            self.relevance_kept += int(kept)
            self.relevance_dropped += int(dropped)
            self.steps_relevance_kept += int(steps_kept)
            self.steps_relevance_dropped += int(steps_dropped)

    def pitch_summary(self) -> dict[str, Any]:
        """Cumulative ingest/gate pitch accounting (env-steps)."""
        with self.lock:
            accepted = int(self.steps_accepted)
            ingest_rej = int(self.steps_rejected_ingest)
            gate_drop = int(self.steps_relevance_dropped)
            pitched = ingest_rej + gate_drop
            # Denominator: everything that tried to enter training usefully.
            denom = accepted + ingest_rej
            return {
                "steps_accepted": accepted,
                "steps_rejected_ingest": ingest_rej,
                "steps_stale_queued": int(self.steps_stale_queued),
                "steps_rejected_stale": int(self.steps_rejected_stale),
                "steps_relevance_kept": int(self.steps_relevance_kept),
                "steps_relevance_dropped": gate_drop,
                "steps_pitched": pitched,
                "pitch_pct": (100.0 * pitched / denom) if denom > 0 else 0.0,
                "rollouts_accepted": int(self.rollouts_accepted),
                "rollouts_rejected": int(self.rollouts_rejected),
                "rollouts_stale_queued": int(self.rollouts_stale_queued),
                "rollouts_rejected_stale": int(self.rollouts_rejected_stale),
                "relevance_kept": int(self.relevance_kept),
                "relevance_dropped": int(self.relevance_dropped),
            }

    def epoch_status(self) -> dict[str, Any]:
        with self.lock:
            live = self._prune_and_list_live_unlocked()
            # Drop expected workers that died; keep snapshot otherwise.
            self.epoch_expected &= set(live.keys())
            expected = set(self.epoch_expected)
            contributors = set(self.epoch_contributors) & expected
            missing = sorted(expected - contributors)
            return {
                "epoch_id": self.epoch_id,
                "expected": sorted(expected),
                "contributors": sorted(contributors),
                "missing": missing,
                "ready": len(expected) > 0 and len(missing) == 0,
                "n_live": len(live),
                "n_expected": len(expected),
            }


class LearnerRolloutSink:
    """Local worker deliver() target: same ingest gates as HTTP /rollout."""

    def __init__(self, state: LearnerState) -> None:
        self._state = state
        self.last_reject_reason = ""

    def put(self, rollout: WorkerRollout) -> bool:
        ok, reason = self._state.accept_rollout(rollout)
        self.last_reject_reason = "" if ok else str(reason)
        if not ok:
            log(
                self._state.machine_name,
                f"local rollout not queued ({reason}) from {rollout.worker_id} "
                f"(+{rollout.num_timesteps()})",
            )
        return ok


class _LearnerHandler(BaseHTTPRequestHandler):
    state: LearnerState

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/health":
            self._send_json(200, {"ok": True})
            return

        if path == "/weights/version":
            version, _ = self.state.weight_store.snapshot()
            self._send_json(200, {"policy_version": version})
            return

        if path == "/weights":
            min_version = int(qs.get("min_version", ["0"])[0])
            version, data = self.state.weight_store.get_weights(min_version)
            if version < min_version or not data:
                self.send_response(204)
                self.end_headers()
                return
            self._send_json(
                200,
                {
                    "policy_version": version,
                    "policy_bytes": base64.b64encode(data).decode("ascii"),
                },
            )
            return

        if path == "/status":
            version, _ = self.state.weight_store.snapshot()
            # epoch_status / pitch_summary each take state.lock — must not call
            # them while already holding it (threading.Lock is not re-entrant).
            epoch = self.state.epoch_status()
            pitch = self.state.pitch_summary()
            go_stats: dict[str, Any] | None = None
            merge = self.state.go_explore_merge
            if merge is not None:
                cells = merge.archive.cells
                bytes_total = 0
                for cell in cells.values():
                    meta = cell.meta or {}
                    nbytes = meta.get("bytes")
                    if nbytes is not None:
                        bytes_total += int(nbytes)
                with self.state.lock:
                    go_stats = {
                        "admitted": self.state.go_explore_accepted,
                        "rejected_semantic": self.state.go_explore_rejected_semantic,
                        "evicted": self.state.go_explore_evicted,
                        "cells_total": len(cells),
                        "bytes_total": bytes_total,
                        "archive_version": int(merge.archive_version),
                    }
            with self.state.lock:
                payload = {
                    "policy_version": version,
                    "current_policy_version": self.state.current_policy_version,
                    "queue_depth": self.state.rollout_queue.qsize(),
                    "workers": dict(self.state.workers),
                    "rollouts_accepted": self.state.rollouts_accepted,
                    "rollouts_rejected": self.state.rollouts_rejected,
                    "rollouts_rejected_duplicate": self.state.rollouts_rejected_duplicate,
                    "rollouts_stale_queued": self.state.rollouts_stale_queued,
                    "rollouts_rejected_stale": self.state.rollouts_rejected_stale,
                    "steps_rejected_stale": self.state.steps_rejected_stale,
                    "rollouts_rejected_capacity": self.state.rollouts_rejected_capacity,
                    "steps_rejected_capacity": self.state.steps_rejected_capacity,
                    "max_pending_steps": self.state.max_pending_steps,
                    "epoch_admitted_steps": self.state.epoch_admitted_steps,
                    "cohort_full": (
                        self.state.max_pending_steps > 0
                        and self.state.epoch_admitted_steps >= self.state.max_pending_steps
                    ),
                    "relevance_gate": self.state.relevance_gate,
                    "relevance_max_age": self.state.relevance_max_age,
                    "relevance_kept": self.state.relevance_kept,
                    "relevance_dropped": self.state.relevance_dropped,
                    "go_explore_accepted": self.state.go_explore_accepted,
                    "yawn_rails_accepted": self.state.yawn_rails_accepted,
                    "pitch": pitch,
                    "epoch": epoch,
                }
                if go_stats is not None:
                    payload["go_explore_stats"] = go_stats
                yr_store = self.state.yawn_rails_store
                if yr_store is not None:
                    payload["yawn_rails_stats"] = {
                        "admitted": self.state.yawn_rails_accepted,
                        "rejected": self.state.yawn_rails_rejected,
                        "cells_total": len(yr_store.cells),
                        "archive_version": int(yr_store.archive_version),
                    }
            self._send_json(200, payload)
            return

        if path == "/go_explore/manifest":
            merge = self.state.go_explore_merge
            if merge is None:
                self._send_json(503, {"error": "go_explore merge not configured"})
                return
            since = int(qs.get("since_version", ["0"])[0])
            self._send_json(200, merge.build_manifest(since_version=since))
            return

        if path.startswith("/go_explore/bundle/"):
            merge = self.state.go_explore_merge
            if merge is None:
                self._send_json(503, {"error": "go_explore merge not configured"})
                return
            record_id = path[len("/go_explore/bundle/") :].strip("/")
            if not record_id or "/" in record_id or "\\" in record_id or ".." in record_id:
                self._send_json(400, {"error": "invalid record_id"})
                return
            blob = merge.pack_bundle_zip(record_id)
            if blob is None:
                self._send_json(404, {"error": "bundle not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return

        if path == "/yawn_rails/manifest":
            store = self.state.yawn_rails_store
            if store is None:
                self._send_json(503, {"error": "yawn_rails store not configured"})
                return
            since = int(qs.get("since_version", ["0"])[0])
            self._send_json(200, store.build_manifest(since_version=since))
            return

        if path.startswith("/yawn_rails/bundle/"):
            store = self.state.yawn_rails_store
            if store is None:
                self._send_json(503, {"error": "yawn_rails store not configured"})
                return
            cell_id = path[len("/yawn_rails/bundle/") :].strip("/")
            if not cell_id or "/" in cell_id or "\\" in cell_id or ".." in cell_id:
                self._send_json(400, {"error": "invalid cell_id"})
                return
            blob = store.pack_bundle_zip(cell_id)
            if blob is None:
                self._send_json(404, {"error": "bundle not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/register", "/heartbeat"):
            try:
                payload = json.loads(self._read_body().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(400, {"error": "invalid json"})
                return
            worker_id = str(payload.get("worker_id", "unknown"))
            n_envs = payload.get("n_envs")
            hostname = payload.get("hostname")
            if path == "/register":
                self.state.register_worker(
                    worker_id,
                    n_envs=int(n_envs) if n_envs is not None else None,
                    hostname=str(hostname) if hostname is not None else None,
                    is_local=bool(payload.get("is_local", False)),
                )
            else:
                self.state.heartbeat_worker(
                    worker_id,
                    n_envs=int(n_envs) if n_envs is not None else None,
                    hostname=str(hostname) if hostname is not None else None,
                )
            self._send_json(200, {"ok": True, "epoch_id": self.state.epoch_id})
            return

        if path == "/unregister":
            try:
                payload = json.loads(self._read_body().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(400, {"error": "invalid json"})
                return
            self.state.unregister_worker(str(payload.get("worker_id", "unknown")))
            self._send_json(200, {"ok": True})
            return

        if path == "/rollout":
            raw = self._read_body()
            try:
                rollout = decode_rollout(raw)
            except (ValueError, KeyError, OSError) as exc:
                self._send_json(400, {"error": f"bad rollout: {exc}"})
                return
            accepted, reason = self.state.accept_rollout(rollout)
            if accepted:
                self._send_json(200, {"accepted": True})
            else:
                self._send_json(409, {"accepted": False, "reason": reason})
            return

        if path == "/yawn_rails/ingest":
            store = self.state.yawn_rails_store
            if store is None:
                self._send_json(503, {"error": "yawn_rails store not configured"})
                return
            try:
                payload = json.loads(self._read_body().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(400, {"error": "invalid json"})
                return
            raw_props = payload.get("proposals")
            if raw_props is None and isinstance(payload.get("proposal"), dict):
                raw_props = [payload["proposal"]]
            if not isinstance(raw_props, list) or not raw_props:
                self._send_json(400, {"error": "proposals required"})
                return
            proposals = [p for p in raw_props if isinstance(p, dict)]
            if not proposals:
                self._send_json(400, {"error": "no valid proposals"})
                return
            accepted = self.state.ingest_yawn_rails_proposals(
                proposals,
                source="POST /yawn_rails/ingest",
            )
            self._send_json(
                200,
                {
                    "accepted": accepted,
                    "archive_version": int(store.archive_version),
                    "cell_count": len(store.cells),
                },
            )
            return

        self._send_json(404, {"error": "not found"})


def start_learner_server(
    state: LearnerState,
    *,
    host: str,
    port: int,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = type("BoundLearnerHandler", (_LearnerHandler,), {"state": state})
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, name="learner-http", daemon=True)
    thread.start()
    return server, thread
