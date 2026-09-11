"""Unit tests for planner-loyal room/solo tape capture (RE1_PLANNER_LEG_REPLAY)."""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from re1_rl import planner_leg_replay as plr
from re1_rl.footage_trace import FootageTraceBuffer
from re1_rl.leg_replay import new_leg_replay_buffer


class _Bridge:
    def __init__(self, packed: str = "") -> None:
        self._packed = packed
        self.enabled_calls: list[bool] = []

    def tape_clear(self) -> None:
        return None

    def tape_enable(self, on: bool = True) -> None:
        self.enabled_calls.append(bool(on))

    def tape_dump_packed(self) -> dict | None:
        if not self._packed:
            return None
        from re1_rl.leg_replay import JOYPAD_PACKED_ENCODING

        return {"packed": self._packed, "encoding": JOYPAD_PACKED_ENCODING, "n": 3}


def _env(tmp_path: Path, *, packed: str = "") -> types.SimpleNamespace:
    buf = new_leg_replay_buffer()
    for _ in range(4):
        buf.append(2, policy_frames=8)
        buf.append_reward(0.1, {"step": 0.1})
    return types.SimpleNamespace(
        _planner_loyal_queue=object(),
        _leg_replay=buf,
        _footage_trace=None,
        bridge=_Bridge(packed),
        action_space=types.SimpleNamespace(n=45),
        frame_skip=8,
        _async_cutscene_skip=False,
        project_root=tmp_path,
        _progress=None,
    )


def test_flag_off_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RE1_PLANNER_LEG_REPLAY", raising=False)
    assert plr.planner_leg_replay_enabled_from_env() is False
    env = _env(tmp_path)
    assert plr.should_write_planner_leg_replay(env) is False
    assert plr.arm_planner_leg_replay(env) is False


def test_build_payload_planner_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_PLANNER_LEG_REPLAY", "1")
    packed = "A" * 9  # 3 frames of packed joypad
    env = _env(tmp_path, packed=packed)
    payload = plr.build_planner_leg_replay_payload(
        env,
        completed_index=1,
        checkpoint_id="opening_to_lockpick_step02",
        slot=2,
        from_slot=0,
        live_state={"room_id": "104", "x": 1, "z": 2, "facing": 3, "hp": 100},
        quality=[1, 2, 3],
        to_state_sha256="abc",
        segment_id="opening_emblem_to_tea",
    )
    assert payload is not None
    assert payload["planner_loyal"] is True
    assert payload["room_segment_id"] == "opening_emblem_to_tea"
    assert payload["to_pl_slot"] == 2
    assert payload["from_pl_slot"] == 0
    assert payload["contract"]["planner_loyal"] is True
    assert payload["joypad_packed"] == packed
    assert payload["leg_steps"] == 4
    assert payload["end"]["room_id"] == "104"


def test_write_tape_and_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_PLANNER_LEG_REPLAY", "1")
    env = _env(tmp_path)
    trace = FootageTraceBuffer()
    import numpy as np

    trace.append(
        action=2,
        action_mask=np.ones(45, dtype=bool),
        masked_probs=np.full(45, 1.0 / 45, dtype=np.float32),
    )
    env._footage_trace = trace
    dest = tmp_path / "pl02"
    dest.mkdir()
    (dest / "cell.pst").write_bytes(b"state")
    tape = plr.maybe_write_planner_capture_tape(
        env,
        dest,
        completed_index=1,
        checkpoint_id="step02",
        slot=2,
        from_slot=0,
        live_state={"room_id": "104"},
        quality=[0] * 8,
        to_state_sha256="abc",
        segment_id="opening_emblem_to_tea",
    )
    assert tape is not None and tape.is_file()
    data = json.loads(tape.read_text(encoding="utf-8"))
    assert data["to_checkpoint_id"] == "step02"
    policy = plr.maybe_write_planner_footage_trace(env, dest)
    assert policy is not None and policy.is_file()


def test_no_write_without_buffer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_PLANNER_LEG_REPLAY", "1")
    env = _env(tmp_path)
    env._leg_replay = new_leg_replay_buffer()  # empty
    dest = tmp_path / "pl03"
    dest.mkdir()
    assert (
        plr.maybe_write_planner_capture_tape(
            env,
            dest,
            completed_index=2,
            checkpoint_id="step03",
            slot=3,
            from_slot=2,
            live_state={},
            quality=[],
            to_state_sha256="",
        )
        is None
    )
