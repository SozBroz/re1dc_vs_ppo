"""HTTP learner surface for remote workers."""

from __future__ import annotations

import base64
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from re1_rl.distributed.log_util import log
from re1_rl.distributed.relevance_gate import DEFAULT_RELEVANCE_MAX_AGE
from re1_rl.distributed.rollout_codec import decode_rollout
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.weight_store import WeightStore


def base_worker_id(worker_id: str) -> str:
    """Strip ``:actor_N`` suffix so contribution is per machine."""
    return str(worker_id).split(":", 1)[0]


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
    ) -> None:
        self.weight_store = weight_store
        self.rollout_queue = rollout_queue
        self.machine_name = machine_name
        self.max_staleness = max_staleness
        self.worker_liveness_s = float(worker_liveness_s)
        # Soft-accept stale (version behind max_staleness) up to relevance_max_age;
        # train_on_rollouts applies the π_new/π_old ownership gate.
        self.relevance_gate = bool(relevance_gate)
        self.relevance_max_age = int(
            relevance_max_age
            if relevance_max_age is not None
            else max(int(max_staleness), DEFAULT_RELEVANCE_MAX_AGE)
        )
        self.current_policy_version = 0
        self.lock = threading.Lock()
        # worker_id -> {n_envs, hostname, last_seen, is_local}
        self.workers: dict[str, dict[str, Any]] = {}
        self.rollouts_accepted = 0
        self.rollouts_rejected = 0
        self.rollouts_stale_queued = 0  # accepted for train-time relevance gate
        self.relevance_kept = 0
        self.relevance_dropped = 0
        # Env-step accounting for pitch % (ingest + relevance gate).
        self.steps_accepted = 0
        self.steps_rejected_ingest = 0
        self.steps_stale_queued = 0
        self.steps_relevance_kept = 0
        self.steps_relevance_dropped = 0
        self.epoch_id = 0
        self.epoch_contributors: set[str] = set()
        self.epoch_expected: set[str] = set()
        self.rollouts_rejected_duplicate = 0

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
            live = self._prune_and_list_live_unlocked()
            self.epoch_expected = set(live.keys())
            return self.epoch_id, sorted(self.epoch_expected)

    def accept_rollout(self, rollout: WorkerRollout) -> tuple[bool, str]:
        wid = base_worker_id(rollout.worker_id)
        steps = int(rollout.num_timesteps())
        with self.lock:
            min_ok = self.current_policy_version - self.max_staleness
            if rollout.policy_version < min_ok:
                if self.relevance_gate:
                    min_gated = self.current_policy_version - self.relevance_max_age
                    if rollout.policy_version >= min_gated:
                        self.rollouts_accepted += 1
                        self.rollouts_stale_queued += 1
                        self.steps_accepted += steps
                        self.steps_stale_queued += steps
                        self.epoch_contributors.add(wid)
                        self.rollout_queue.put(rollout)
                        return True, "stale_queued_for_relevance_gate"
                self.rollouts_rejected += 1
                self.steps_rejected_ingest += steps
                return False, "stale_policy_version"
            self.rollouts_accepted += 1
            self.steps_accepted += steps
            self.epoch_contributors.add(wid)
        self.rollout_queue.put(rollout)
        return True, "ok"

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
                "steps_relevance_kept": int(self.steps_relevance_kept),
                "steps_relevance_dropped": gate_drop,
                "steps_pitched": pitched,
                "pitch_pct": (100.0 * pitched / denom) if denom > 0 else 0.0,
                "rollouts_accepted": int(self.rollouts_accepted),
                "rollouts_rejected": int(self.rollouts_rejected),
                "rollouts_stale_queued": int(self.rollouts_stale_queued),
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

    def put(self, rollout: WorkerRollout) -> bool:
        ok, reason = self._state.accept_rollout(rollout)
        if not ok:
            log(
                self._state.machine_name,
                f"local rollout not queued ({reason}) from {rollout.worker_id} "
                f"(+{rollout.num_timesteps()})",
            )
        elif reason == "stale_queued_for_relevance_gate":
            log(
                self._state.machine_name,
                f"local rollout soft-queued ({reason}) from {rollout.worker_id} "
                f"v{rollout.policy_version} (+{rollout.num_timesteps()})",
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
                    "relevance_gate": self.state.relevance_gate,
                    "relevance_max_age": self.state.relevance_max_age,
                    "relevance_kept": self.state.relevance_kept,
                    "relevance_dropped": self.state.relevance_dropped,
                    "pitch": pitch,
                    "epoch": epoch,
                }
            self._send_json(200, payload)
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
