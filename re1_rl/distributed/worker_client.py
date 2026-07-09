"""HTTP client for remote rollout workers."""

from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.request
from typing import Any

from re1_rl.distributed.log_util import log
from re1_rl.distributed.rollout_codec import encode_rollout
from re1_rl.distributed.rollout_types import WorkerRollout


class WorkerClient:
    def __init__(self, host: str, port: int, *, machine_name: str, timeout: float = 60.0) -> None:
        self.base = f"http://{host}:{port}"
        self.machine_name = machine_name
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, bytes]:
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def health(self) -> bool:
        code, _ = self._request("GET", "/health")
        return code == 200

    def fetch_weights(self, min_version: int = 0) -> tuple[int, bytes]:
        code, body = self._request("GET", f"/weights?min_version={min_version}")
        if code == 204:
            version, _ = self.fetch_version()
            return version, b""
        if code != 200:
            raise RuntimeError(f"GET /weights failed with HTTP {code}")
        payload = json.loads(body.decode("utf-8"))
        version = int(payload["policy_version"])
        data = base64.b64decode(payload["policy_bytes"])
        return version, data

    def fetch_version(self) -> tuple[int, bytes]:
        code, body = self._request("GET", "/weights/version")
        if code != 200:
            raise RuntimeError(f"GET /weights/version failed with HTTP {code}")
        payload = json.loads(body.decode("utf-8"))
        return int(payload["policy_version"]), b""

    def register(self, worker_id: str, n_envs: int, *, is_local: bool = False) -> None:
        payload = {
            "worker_id": worker_id,
            "n_envs": n_envs,
            "hostname": socket.gethostname(),
            "is_local": is_local,
        }
        code, _ = self._request(
            "POST",
            "/register",
            data=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )
        if code != 200:
            raise RuntimeError(f"POST /register failed with HTTP {code}")

    def heartbeat(self, worker_id: str, n_envs: int) -> None:
        payload = {
            "worker_id": worker_id,
            "n_envs": n_envs,
            "hostname": socket.gethostname(),
        }
        code, _ = self._request(
            "POST",
            "/heartbeat",
            data=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )
        if code != 200:
            raise RuntimeError(f"POST /heartbeat failed with HTTP {code}")

    def unregister(self, worker_id: str) -> None:
        payload = {"worker_id": worker_id}
        try:
            self._request(
                "POST",
                "/unregister",
                data=json.dumps(payload).encode("utf-8"),
                content_type="application/json",
            )
        except Exception:
            pass

    def upload_rollout(self, rollout: WorkerRollout) -> bool:
        body = encode_rollout(rollout)
        code, resp = self._request(
            "POST",
            "/rollout",
            data=body,
            content_type="application/octet-stream",
        )
        if code == 200:
            return True
        if code == 409:
            log(self.machine_name, "rollout rejected as stale by learner")
            return False
        raise RuntimeError(f"POST /rollout failed with HTTP {code}: {resp[:200]!r}")

    def wait_for_learner(self, timeout_s: float, poll_s: float = 2.0) -> None:
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.health():
                return
            time.sleep(poll_s)
        raise TimeoutError(f"learner at {self.base} not reachable within {timeout_s}s")
