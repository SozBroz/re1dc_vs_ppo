"""Thread-safe versioned policy weights for learner and local workers."""

from __future__ import annotations

import threading
from typing import Any

from re1_rl.distributed.weights import policy_bytes_from_state_dict


class WeightStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._policy_version = 0
        self._policy_bytes = b""
        self._state_dict: dict[str, Any] | None = None

    @property
    def policy_version(self) -> int:
        with self._lock:
            return self._policy_version

    def publish(self, state_dict: dict[str, Any]) -> int:
        data = policy_bytes_from_state_dict(state_dict)
        with self._cond:
            self._policy_version += 1
            self._state_dict = state_dict
            self._policy_bytes = data
            self._cond.notify_all()
            return self._policy_version

    def wait_for_version(self, min_version: int = 1, timeout: float | None = None) -> int:
        with self._cond:
            if self._policy_version < min_version:
                self._cond.wait(timeout=timeout)
            if self._policy_version < min_version:
                raise TimeoutError(
                    f"policy_version {self._policy_version} < required {min_version}"
                )
            return self._policy_version

    def get_weights(self, min_version: int = 0) -> tuple[int, bytes]:
        with self._lock:
            if self._policy_version < min_version:
                return self._policy_version, b""
            return self._policy_version, self._policy_bytes

    def get_state_dict(self) -> dict[str, Any] | None:
        with self._lock:
            return self._state_dict

    def snapshot(self) -> tuple[int, bytes]:
        with self._lock:
            return self._policy_version, self._policy_bytes
