"""Pking pin march: 4x-faster + 15 min dwell, stop at the L-passage dogs."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from re1_rl.go_explore_merge import CELL_META_NAME, CELL_REPLAY_NAME, CELL_STATE_NAME
from re1_rl.yawn_pin_march import (
    DEFAULT_STOP_PIN,
    maybe_advance_pin,
    speed_gate_ok,
    write_pin_index,
)
from re1_rl.yawn_rails import sample_one_leg_options
from tests.test_yawn_rails import _write_cell


def _timeouts(human_s: str) -> dict:
    return {
        "fps": 60,
        "cells": {
            "5": {"time": human_s},
            "6": {"time": "20.00"},
            "19": {"time": "1:07.18"},
        },
    }


def _write_hunted_cell(
    root: Path,
    idx: int,
    *,
    policy_frames: int,
    state: bool = True,
) -> None:
    slot = root / "states" / "yawn_rails" / "cells" / f"cp{idx:02d}"
    slot.mkdir(parents=True, exist_ok=True)
    if state:
        (slot / CELL_STATE_NAME).write_bytes(b"state")
    (slot / CELL_META_NAME).write_text(
        json.dumps({"quality": [96, 45, 100, 4, 1, 0, -30, -int(policy_frames)]}),
        encoding="utf-8",
    )
    (slot / CELL_REPLAY_NAME).write_text(
        json.dumps({"policy_leg_frames": int(policy_frames)}),
        encoding="utf-8",
    )


def _pin_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, index: int) -> Path:
    pin = tmp_path / "data" / "yawn_reset_pin.env"
    pin.parent.mkdir(parents=True)
    pin.write_text(
        (
            f"RE1_YAWN_RESET_PIN_INDEX={index}\n"
            "RE1_YAWN_PIN_MARCH=1\n"
            "RE1_YAWN_PIN_MARCH_START=4\n"
            "RE1_YAWN_PIN_MARCH_STOP=18\n"
            "RE1_YAWN_PIN_MARCH_RATIO=0.25\n"
            "RE1_YAWN_PIN_MARCH_MIN_DWELL_S=900\n"
            "RE1_YAWN_PIN_MARCH_MAX_DWELL_S=7200\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "yawn_cell_timeouts.json").write_text(
        json.dumps(_timeouts("16.00")),
        encoding="utf-8",
    )
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_FILE", str(pin))
    monkeypatch.setenv("RE1_YAWN_PIN_MARCH_STATE", str(tmp_path / "data" / "march.json"))
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INDEX", raising=False)
    return pin


def test_speed_gate_is_four_times_faster() -> None:
    assert speed_gate_ok(4.0, 16.0, ratio=0.25) is True
    assert speed_gate_ok(4.01, 16.0, ratio=0.25) is False
    assert speed_gate_ok(None, 16.0, ratio=0.25) is False
    assert speed_gate_ok(4.0, None, ratio=0.25) is False


def test_write_pin_index_keeps_other_keys(tmp_path: Path) -> None:
    pin = tmp_path / "yawn_reset_pin.env"
    pin.write_text(
        "# keep\nRE1_YAWN_RESET_PIN_INDEX=4\nRE1_YAWN_PIN_MARCH=1\n",
        encoding="utf-8",
    )
    write_pin_index(pin, 5)
    text = pin.read_text(encoding="utf-8")
    assert "RE1_YAWN_RESET_PIN_INDEX=5" in text
    assert "RE1_YAWN_PIN_MARCH=1" in text
    assert "# keep" in text


def test_dwell_blocks_even_when_already_4x(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = _pin_project(tmp_path, monkeypatch, index=4)
    _write_hunted_cell(tmp_path, 5, policy_frames=60)  # 1.0s vs 16s human
    tick = maybe_advance_pin(tmp_path, now=1_000.0)
    assert tick is not None
    assert tick.advanced is False
    assert tick.reason == "dwell"
    assert tick.speed_ok is True
    assert pin.read_text(encoding="utf-8").count("RE1_YAWN_RESET_PIN_INDEX=4") == 1


def test_advances_after_15min_and_4x(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = _pin_project(tmp_path, monkeypatch, index=4)
    _write_hunted_cell(tmp_path, 5, policy_frames=240)  # 4.0s == 16/4
    first = maybe_advance_pin(tmp_path, now=1_000.0)
    assert first is not None and first.reason == "dwell"
    tick = maybe_advance_pin(tmp_path, now=1_000.0 + 900.0)
    assert tick is not None
    assert tick.advanced is True
    assert tick.reason == "advanced"
    assert tick.pin_index == 5
    assert "RE1_YAWN_RESET_PIN_INDEX=5" in pin.read_text(encoding="utf-8")


def test_stays_when_capture_is_slower_than_4x(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = _pin_project(tmp_path, monkeypatch, index=4)
    _write_hunted_cell(tmp_path, 5, policy_frames=241)  # 4.016s > 4.0s
    maybe_advance_pin(tmp_path, now=1_000.0)
    tick = maybe_advance_pin(tmp_path, now=1_000.0 + 900.0)
    assert tick is not None
    assert tick.advanced is False
    assert tick.reason == "speed"
    assert "RE1_YAWN_RESET_PIN_INDEX=4" in pin.read_text(encoding="utf-8")


def test_max_dwell_advances_slow_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = _pin_project(tmp_path, monkeypatch, index=4)
    _write_hunted_cell(tmp_path, 5, policy_frames=241)  # slower than 4x
    maybe_advance_pin(tmp_path, now=1_000.0)
    mid = maybe_advance_pin(tmp_path, now=1_000.0 + 900.0)
    assert mid is not None
    assert mid.advanced is False
    assert mid.reason == "speed"
    tick = maybe_advance_pin(tmp_path, now=1_000.0 + 7200.0)
    assert tick is not None
    assert tick.advanced is True
    assert tick.reason == "max_dwell"
    assert tick.pin_index == 5
    assert "RE1_YAWN_RESET_PIN_INDEX=5" in pin.read_text(encoding="utf-8")


def test_max_dwell_without_cell_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = _pin_project(tmp_path, monkeypatch, index=4)
    maybe_advance_pin(tmp_path, now=1_000.0)
    tick = maybe_advance_pin(tmp_path, now=1_000.0 + 7200.0)
    assert tick is not None
    assert tick.advanced is False
    assert tick.reason == "speed"
    assert "RE1_YAWN_RESET_PIN_INDEX=4" in pin.read_text(encoding="utf-8")


def test_stop_pin_never_advances_past_dog_fight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = _pin_project(tmp_path, monkeypatch, index=DEFAULT_STOP_PIN)
    _write_hunted_cell(tmp_path, 19, policy_frames=1)
    (tmp_path / "data" / "yawn_cell_timeouts.json").write_text(
        json.dumps(_timeouts("1:07.18")),
        encoding="utf-8",
    )
    maybe_advance_pin(tmp_path, now=1_000.0)
    tick = maybe_advance_pin(tmp_path, now=1_000.0 + 10_000.0)
    assert tick is not None
    assert tick.advanced is False
    assert tick.at_stop is True
    assert tick.reason == "hold_dog_fight"
    assert tick.pin_index == 18
    assert "RE1_YAWN_RESET_PIN_INDEX=18" in pin.read_text(encoding="utf-8")


def test_clamps_below_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pin = _pin_project(tmp_path, monkeypatch, index=2)
    tick = maybe_advance_pin(tmp_path, now=50.0)
    assert tick is not None
    assert tick.pin_index == 4
    assert tick.reason == "clamped_to_start"
    assert "RE1_YAWN_RESET_PIN_INDEX=4" in pin.read_text(encoding="utf-8")


def test_disabled_without_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = tmp_path / "data" / "yawn_reset_pin.env"
    pin.parent.mkdir(parents=True)
    pin.write_text("RE1_YAWN_RESET_PIN_INDEX=4\n", encoding="utf-8")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_FILE", str(pin))
    monkeypatch.delenv("RE1_YAWN_PIN_MARCH", raising=False)
    assert maybe_advance_pin(tmp_path, now=1.0) is None


def test_sample_uses_advanced_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = _pin_project(tmp_path, monkeypatch, index=4)
    _write_hunted_cell(tmp_path, 5, policy_frames=60)
    maybe_advance_pin(tmp_path, now=1_000.0)
    maybe_advance_pin(tmp_path, now=1_000.0 + 900.0)
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in (4, 5)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 20)),
    }
    opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(0))
    assert opts["route_start_index"] == 6
    assert opts["reset_source"] == "route_cell_pin"
