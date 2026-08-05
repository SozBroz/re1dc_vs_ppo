"""Surgical tests for independent memlog topology, control, and telemetry."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.memlog_runtime import MemlogControl, MemlogTelemetry, _atomic_json, _atomic_json_best_effort
from scripts.distributed_train_parallel import _maybe_start_grid_tiler, parse_actor_ranks


class _Bridge:
    def __init__(self) -> None:
        self.speeds: list[int] = []

    def set_speed(self, speed: int) -> None:
        self.speeds.append(int(speed))


class _Skipper:
    def __init__(self) -> None:
        self.training_speed = 0
        self.cutscene_speed = 0


def _write_control(path: Path, **values) -> None:
    path.write_text(json.dumps(values), encoding="utf-8")


def test_parse_actor_ranks_supports_commas_and_ranges() -> None:
    assert parse_actor_ranks("0-3,5-19") == [0, 1, 2, 3, *range(5, 20)]
    assert parse_actor_ranks("4") == [4]
    with pytest.raises(Exception):
        parse_actor_ranks("3-1")
    with pytest.raises(Exception):
        parse_actor_ranks("1,1")


def test_sparse_actor_grid_uses_logical_port_span(monkeypatch) -> None:
    start = MagicMock(return_value=(MagicMock(), MagicMock()))
    monkeypatch.setattr("re1_rl.window_grid.start_grid_tiler", start)
    args = SimpleNamespace(
        tile_windows=True,
        actor_ranks=[0, 1, 2, 3, *range(5, 20)],
        n_envs=19,
        grid_cols=5,
        grid_rows=4,
        grid_gap=8,
        grid_monitor="all",
        machine_name="pking",
        base_port=5755,
    )
    _maybe_start_grid_tiler(args)
    assert start.call_args.kwargs["expected"] == 20


def test_pking_launchers_reserve_and_preserve_memlog_port() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in (
        "run_distributed_worker_pking.cmd",
        "run_distributed_worker_pking_visible.cmd",
        "run_distributed_worker_pking_capture_canary.cmd",
    ):
        text = (root / "fleet" / "local" / name).read_text(encoding="utf-8")
        assert "N_ENVS=19" in text
        assert "ACTOR_RANKS=0-3,5-19" in text
        assert "--actor-ranks %ACTOR_RANKS%" in text

    memlog = (root / "fleet" / "local" / "run_memlog_agent.cmd").read_text(
        encoding="utf-8"
    )
    assert "--worker-id pking-memlog" in memlog
    assert "--actor-ranks 4" in memlog
    assert "--tile-windows" in memlog
    assert "RE1_YAWN_RESET_LATEST_ONLY=1" in memlog

    for name in (
        "start_worker_detached_pking.cmd",
        "start_worker_detached_pking_visible.cmd",
        "start_worker_detached_pking_capture_canary.cmd",
    ):
        text = (root / "fleet" / "local" / name).read_text(encoding="utf-8")
        assert "worker-id pking-memlog" in text
        assert "$_ -ne 5759" in text


def test_control_is_run_protected_and_updates_pause_speed(tmp_path: Path) -> None:
    bridge = _Bridge()
    skipper = _Skipper()
    control = MemlogControl(
        tmp_path,
        bridge=bridge,
        ram_skipper=skipper,
        initial_speed=6400,
        rank=4,
        run_id="current",
    )
    assert bridge.speeds == [6400]
    assert skipper.training_speed == 6400
    assert skipper.cutscene_speed == 6400

    _write_control(
        control.path,
        run_id="stale",
        paused=True,
        speed_pct=100,
        shutdown=True,
    )
    state = control.poll()
    assert state.run_id == "current"
    assert not state.paused
    assert not state.shutdown
    assert bridge.speeds == [6400]

    _write_control(
        control.path,
        run_id="current",
        paused=True,
        speed_pct=800,
        shutdown=False,
    )
    state = control.poll()
    assert state.paused
    assert bridge.speeds[-1] == 0
    assert skipper.training_speed == 800
    assert skipper.cutscene_speed == 800

    _write_control(
        control.path,
        run_id="current",
        paused=False,
        speed_pct=1200,
        shutdown=False,
    )
    state = control.wait_until_runnable()
    assert not state.paused
    assert bridge.speeds[-1] == 1200
    assert skipper.training_speed == 1200
    assert skipper.cutscene_speed == 1200


def test_telemetry_latest_schema_and_sparse_reward_events(tmp_path: Path) -> None:
    bridge = _Bridge()
    control = MemlogControl(
        tmp_path,
        bridge=bridge,
        ram_skipper=_Skipper(),
        initial_speed=6400,
        rank=4,
        run_id="run-4",
    )
    from re1_rl.async_fleet import DISTRIBUTED_EPOCH_HYPERPARAMS

    n_steps = int(DISTRIBUTED_EPOCH_HYPERPARAMS["n_steps"])
    telemetry = MemlogTelemetry(
        tmp_path,
        run_id=control.state.run_id,
        rank=4,
        n_steps=n_steps,
    )
    frame = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
    obs = {
        "frame": frame,
        "proprio": np.array([1.0, 2.0], dtype=np.float32),
    }
    telemetry.publish_step(
        obs=obs,
        action_mask=np.array([True, False, True]),
        action=2,
        value=0.75,
        logprob=-0.2,
        policy_version=9,
        raw_logits=np.array([0.1, 0.2, 0.3], dtype=np.float32),
        masked_probs=np.array([0.4, 0.0, 0.6], dtype=np.float32),
        reward=12.0,
        info={
            "room_id": "104",
            "action_name": "forward",
            "reward_breakdown": {
                "step": -0.001,
                "softlock": -0.002,
                "new_room": 12.0,
                "enemy_damage": 0.0,
            },
            "episode_reset_frames_left": 1234,
            "episode_idle_frames_left": 1234,
            "state": {"large": "excluded"},
        },
        done=False,
        horizon_step=7,
        control=control.state,
    )

    latest = json.loads(telemetry.latest_path.read_text(encoding="utf-8"))
    assert latest["run_id"] == "run-4"
    assert latest["rank"] == 4
    assert latest["horizon_step"] == 7
    assert latest["n_steps"] == n_steps
    assert set(latest["pre_step"]["observation"]) == set(obs)
    encoded_frame = latest["pre_step"]["observation"]["frame"]
    assert encoded_frame["shape"] == [2, 3, 4]
    assert base64.b64decode(encoded_frame["data"]) == frame.tobytes()
    assert latest["pre_step"]["policy_version"] == 9
    assert latest["episode_index"] == 1
    assert latest["episode_return"] == pytest.approx(12.0)
    assert latest["post_step"]["reward_breakdown"]["new_room"] == 12.0
    assert latest["post_step"]["info"]["episode_reset_frames_left"] == 1234
    assert "state" not in latest["post_step"]["info"]

    events = telemetry.events_path.read_text(encoding="utf-8").splitlines()
    assert len(events) == 1
    event = json.loads(events[0])
    assert event["reward_breakdown"] == {"new_room": 12.0}

    telemetry.publish_step(
        obs=obs,
        action_mask=np.array([True, True, True]),
        action=0,
        value=0.0,
        logprob=0.0,
        policy_version=9,
        raw_logits=None,
        masked_probs=None,
        reward=-0.003,
        info={"reward_breakdown": {"step": -0.001, "softlock": -0.002}},
        done=True,
        horizon_step=8,
        control=control.state,
    )
    latest = json.loads(telemetry.latest_path.read_text(encoding="utf-8"))
    assert latest["episode_index"] == 1
    assert latest["episode_return"] == pytest.approx(11.997)
    assert telemetry._episode_index == 2
    assert telemetry._episode_return == 0.0
    assert len(telemetry.events_path.read_text(encoding="utf-8").splitlines()) == 1
    assert not list(tmp_path.glob(".latest.json.*.tmp"))


def test_atomic_json_retries_transient_permission_error(monkeypatch, tmp_path: Path) -> None:
  target = tmp_path / "latest.json"
  calls = {"n": 0}
  real_replace = os.replace

  def flaky_replace(src: str | os.PathLike, dst: str | os.PathLike) -> None:
      calls["n"] += 1
      if calls["n"] == 1:
          raise PermissionError(5, "Access is denied")
      real_replace(src, dst)

  monkeypatch.setattr("re1_rl.memlog_runtime.os.replace", flaky_replace)
  monkeypatch.setattr("re1_rl.memlog_runtime.time.sleep", lambda _s: None)

  _atomic_json(target, {"ok": True})
  assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
  assert calls["n"] == 2
  assert not list(tmp_path.glob(".latest.json.*.tmp"))


def test_atomic_json_best_effort_does_not_raise_on_persistent_sharing_error(
  monkeypatch, tmp_path: Path
) -> None:
  target = tmp_path / "latest.json"

  def always_fail(_src: str | os.PathLike, _dst: str | os.PathLike) -> None:
      raise PermissionError(5, "Access is denied")

  monkeypatch.setattr("re1_rl.memlog_runtime.os.replace", always_fail)
  monkeypatch.setattr("re1_rl.memlog_runtime.time.sleep", lambda _s: None)

  assert _atomic_json_best_effort(target, {"lost": True}) is False
  assert not target.exists()
  assert not list(tmp_path.glob(".latest.json.*.tmp"))
