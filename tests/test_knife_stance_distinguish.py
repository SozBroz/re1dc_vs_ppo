"""Crouch vs standing knife stance distinguishability (logic + optional live)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.knife_macro import (
    KnifeHookFrame,
    build_knife_frame_buttons,
    build_standing_knife_frame_buttons,
    compare_knife_stances,
    summarize_knife_trace,
)


def _summary_from_labels(labels: list[str]) -> dict:
    frames = [
        KnifeHookFrame(0, 0, 0, label) if label == "idle"
        else KnifeHookFrame(
            {"crouch_aim": 0x12, "swing_recovery": 0x13, "standing_knife": 0x14}[label],
            0x04 if label in ("crouch_aim", "swing_recovery") else 0x00,
            0,
            label,
        )
        for label in labels
    ]
    return summarize_knife_trace(frames)


def test_compare_logic_distinguishes_mock_stances() -> None:
    crouch = _summary_from_labels(["idle", "crouch_aim", "crouch_aim", "swing_recovery"])
    stand = _summary_from_labels(["idle", "standing_knife", "standing_knife"])
    ok, reasons = compare_knife_stances(crouch, stand)
    assert ok, reasons
    assert any("crouch-only" in r for r in reasons)
    assert any("standing-only" in r for r in reasons)


def test_compare_logic_fails_when_standing_has_crouch_aim() -> None:
    crouch = _summary_from_labels(["crouch_aim", "swing_recovery"])
    stand = _summary_from_labels(["crouch_aim", "standing_knife"])
    ok, reasons = compare_knife_stances(crouch, stand)
    assert not ok
    assert any("standing trace saw crouch_aim" in r for r in reasons)


def test_standing_schedule_has_no_down() -> None:
    crouch = build_knife_frame_buttons(aim=1, swing=1, recovery=1, scale=1)
    stand = build_standing_knife_frame_buttons(aim=1, swing=1, recovery=1, scale=1)
    assert any(f.get("down") for f in crouch)
    assert not any(f.get("down") for f in stand)
    assert all(f.get("r1") for f in stand)


def test_live_stance_compare_qa() -> None:
    if os.environ.get("RE1_LIVE_EMU") != "1":
        return
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "knife_stance_compare_qa.py"),
            "--port",
            "5796",
            "--swings",
            "2",
            "--settle",
            "12",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
