"""HTTP learner surface for remote workers."""

from __future__ import annotations

import base64
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from re1_rl.distributed.log_util import log
from re1_rl.distributed.rollout_codec import decode_rollout
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.weight_store import WeightStore


class LearnerState:
    def __init__(
        self,
        weight_store: WeightStore,
        rollout_queue: queue.Queue[WorkerRollout],
        *,
        machine_name: str,
        max_staleness: int,
    ) -> None:
        self.weight_store = weight_store
        self.rollout_queue = rollout_queue
        self.machine_name = machine_name
        self.max_staleness = max_staleness
        self.current_policy_version = 0
        self.lock = threading.Lock()
        self.workers: dict[str, dict[str, Any]] = {}
        self.rollouts_accepted = 0
        self.rollouts_rejected = 0

    def set_current_version(self, version: int) -> None:
        with self.lock:
            self.current_policy_version = version

    def accept_rollout(self, rollout: WorkerRollout) -> bool:
        with self.lock:
            min_ok = self.current_policy_version - self.max_staleness
            if rollout.policy_version < min_ok:
                self.rollouts_rejected += 1
                return False
            self.rollouts_accepted += 1
        self.rollout_queue.put(rollout)
        return True


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
            with self.state.lock:
                payload = {
                    "policy_version": version,
                    "current_policy_version": self.state.current_policy_version,
                    "queue_depth": self.state.rollout_queue.qsize(),
                    "workers": dict(self.state.workers),
                    "rollouts_accepted": self.state.rollouts_accepted,
                    "rollouts_rejected": self.state.rollouts_rejected,
                }
            self._send_json(200, payload)
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/register":
            try:
                payload = json.loads(self._read_body().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(400, {"error": "invalid json"})
                return
            worker_id = str(payload.get("worker_id", "unknown"))
            with self.state.lock:
                self.state.workers[worker_id] = {
                    "n_envs": payload.get("n_envs"),
                    "hostname": payload.get("hostname"),
                }
            log(self.state.machine_name, f"worker registered: {worker_id}")
            self._send_json(200, {"ok": True})
            return

        if path == "/rollout":
            raw = self._read_body()
            try:
                rollout = decode_rollout(raw)
            except (ValueError, KeyError, OSError) as exc:
                self._send_json(400, {"error": f"bad rollout: {exc}"})
                return
            if self.state.accept_rollout(rollout):
                self._send_json(200, {"accepted": True})
            else:
                self._send_json(409, {"accepted": False, "reason": "stale policy_version"})
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
