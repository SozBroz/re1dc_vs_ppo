"""Independent control and latest-only telemetry for the diagnostic actor."""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np


@dataclass(frozen=True)
class MemlogControlState:
    run_id: str
    paused: bool
    speed_pct: int
    shutdown: bool


def _atomic_json(path: Path, payload: dict[str, Any], *, retries: int = 8) -> None:
    """Atomically replace JSON; retry transient Windows sharing violations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        last_exc: OSError | None = None
        for attempt in range(max(1, int(retries))):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                last_exc = exc
                if attempt + 1 >= retries:
                    break
                time.sleep(0.01 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_json_best_effort(path: Path, payload: dict[str, Any]) -> bool:
    """Publish telemetry without killing the actor on a sharing race."""
    try:
        _atomic_json(path, payload)
        return True
    except OSError:
        return False


class MemlogControl:
    """Poll a run-protected control file and apply emulator speed state."""

    def __init__(
        self,
        directory: Path,
        *,
        bridge: Any,
        ram_skipper: Any,
        initial_speed: int,
        rank: int,
        run_id: str | None = None,
        poll_s: float = 0.1,
    ) -> None:
        self.directory = Path(directory)
        self.path = self.directory / "control.json"
        self.bridge = bridge
        self.ram_skipper = ram_skipper
        self.poll_s = max(float(poll_s), 0.01)
        self.state = MemlogControlState(
            run_id=run_id or uuid.uuid4().hex,
            paused=False,
            speed_pct=max(1, int(initial_speed)),
            shutdown=False,
        )
        self._applied_speed: int | None = None
        self._applied_paused: bool | None = None
        _atomic_json(
            self.path,
            {
                "schema_version": 1,
                "run_id": self.state.run_id,
                "rank": int(rank),
                "paused": False,
                "speed_pct": self.state.speed_pct,
                "shutdown": False,
            },
        )
        self._apply(self.state)

    def _apply(self, state: MemlogControlState) -> None:
        speed_changed = state.speed_pct != self._applied_speed
        if speed_changed:
            self.ram_skipper.training_speed = int(state.speed_pct)
            self.ram_skipper.cutscene_speed = int(state.speed_pct)
        if state.paused:
            if self._applied_paused is not True:
                self.bridge.set_speed(0)
        elif speed_changed or self._applied_paused is not False:
            self.bridge.set_speed(int(state.speed_pct))
        self._applied_speed = int(state.speed_pct)
        self._applied_paused = bool(state.paused)

    def poll(self) -> MemlogControlState:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return self.state
        if not isinstance(payload, dict) or payload.get("run_id") != self.state.run_id:
            return self.state
        try:
            speed = max(1, int(payload.get("speed_pct", self.state.speed_pct)))
        except (TypeError, ValueError):
            speed = self.state.speed_pct
        state = MemlogControlState(
            run_id=self.state.run_id,
            paused=bool(payload.get("paused", self.state.paused)),
            speed_pct=speed,
            shutdown=bool(payload.get("shutdown", self.state.shutdown)),
        )
        self._apply(state)
        self.state = state
        return state

    def wait_until_runnable(
        self,
        *,
        heartbeat: Callable[[MemlogControlState], None] | None = None,
    ) -> MemlogControlState:
        """Return only when runnable or shutting down; pause advances nothing."""
        last_heartbeat = 0.0
        while True:
            state = self.poll()
            now = time.monotonic()
            if heartbeat is not None and (now - last_heartbeat >= 1.0):
                heartbeat(state)
                last_heartbeat = now
            if state.shutdown or not state.paused:
                return state
            time.sleep(self.poll_s)


def _encode_array(value: Any, *, compact: bool = False) -> dict[str, Any]:
    arr = np.asarray(value)
    encoded: dict[str, Any] = {
        "dtype": str(arr.dtype),
        "shape": list(arr.shape),
    }
    if compact:
        encoded["encoding"] = "base64"
        encoded["data"] = base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")
    else:
        encoded["encoding"] = "json"
        encoded["data"] = arr.tolist()
    return encoded


def _json_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


_INFO_KEYS = (
    "room_id",
    "hp",
    "bridge_port",
    "action_name",
    "episode_failure",
    "cutscene_skip",
    "died_during_skip",
    "died_during_step",
    "episode_reset_frames_left",
    "episode_idle_frames_left",
    "episode_step_limit_frames_left",
    "episode_idle_frames_used",
    "episode_idle_frame_limit",
)
_IGNORED_EVENT_CHANNELS = frozenset({"step", "softlock"})


class MemlogTelemetry:
    """Atomically publish one aligned transition and sparse reward events."""

    def __init__(
        self,
        directory: Path,
        *,
        run_id: str,
        rank: int,
        n_steps: int,
        max_event_bytes: int = 2_000_000,
    ) -> None:
        self.directory = Path(directory)
        self.latest_path = self.directory / "latest.json"
        self.events_path = self.directory / "events.jsonl"
        self.run_id = str(run_id)
        self.rank = int(rank)
        self.n_steps = int(n_steps)
        self.max_event_bytes = int(max_event_bytes)
        self._latest: dict[str, Any] = {}
        self._episode_return = 0.0
        self._episode_index = 1

    def heartbeat(self, control: MemlogControlState, *, horizon_step: int) -> None:
        payload = dict(self._latest)
        payload.update(
            {
                "schema_version": 1,
                "run_id": self.run_id,
                "rank": self.rank,
                "heartbeat_unix_s": time.time(),
                "horizon_step": int(horizon_step),
                "n_steps": self.n_steps,
                "control": asdict(control),
            }
        )
        self._latest = payload
        _atomic_json_best_effort(self.latest_path, payload)

    def publish_step(
        self,
        *,
        obs: dict[str, Any],
        action_mask: Any,
        action: int,
        value: float,
        logprob: float,
        policy_version: int,
        raw_logits: Any | None,
        masked_probs: Any | None,
        reward: float,
        info: dict[str, Any] | None,
        done: bool,
        horizon_step: int,
        control: MemlogControlState,
    ) -> None:
        info = info or {}
        breakdown = {
            str(k): float(v)
            for k, v in (info.get("reward_breakdown") or {}).items()
        }
        self._episode_return += float(reward)
        payload = {
            "schema_version": 1,
            "run_id": self.run_id,
            "rank": self.rank,
            "heartbeat_unix_s": time.time(),
            "horizon_step": int(horizon_step),
            "n_steps": self.n_steps,
            "episode_index": self._episode_index,
            "episode_return": self._episode_return,
            "control": asdict(control),
            "pre_step": {
                "observation": {
                    key: _encode_array(value, compact=(key == "frame"))
                    for key, value in obs.items()
                },
                "action_mask": _encode_array(
                    np.asarray(action_mask, dtype=np.bool_), compact=False
                ),
                "action": int(action),
                "value": float(value),
                "logprob": float(logprob),
                "policy_version": int(policy_version),
                "raw_logits": (
                    None if raw_logits is None else _encode_array(raw_logits)
                ),
                "masked_probs": (
                    None if masked_probs is None else _encode_array(masked_probs)
                ),
            },
            "post_step": {
                "reward": float(reward),
                "reward_breakdown": breakdown,
                "done": bool(done),
                "info": {
                    key: _json_value(info[key])
                    for key in _INFO_KEYS
                    if key in info
                },
            },
        }
        self._latest = payload
        _atomic_json_best_effort(self.latest_path, payload)
        if done:
            self._episode_return = 0.0
            self._episode_index += 1
        event_channels = {
            key: value
            for key, value in breakdown.items()
            if key not in _IGNORED_EVENT_CHANNELS and value != 0.0
        }
        if event_channels:
            self._append_event(
                {
                    "run_id": self.run_id,
                    "rank": self.rank,
                    "time_unix_s": payload["heartbeat_unix_s"],
                    "horizon_step": int(horizon_step),
                    "reward": float(reward),
                    "reward_breakdown": event_channels,
                    "room_id": info.get("room_id"),
                    "action": int(action),
                    "action_name": info.get("action_name"),
                }
            )

    def _append_event(self, event: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            if self.events_path.stat().st_size >= self.max_event_bytes:
                rotated = self.events_path.with_suffix(".1.jsonl")
                rotated.unlink(missing_ok=True)
                os.replace(self.events_path, rotated)
        except OSError:
            pass
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n")
