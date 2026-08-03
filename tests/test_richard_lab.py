"""Tests for post-Richard lab countdown trap detection and clear."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.memory_map import (
    IN_CONTROL_MASK,
    PAUSE_MENU_GAME_MODE,
    RICHARD_LAB_COUNTDOWN_STATUS_GAME_STATE,
)
from re1_rl.milestone_digest import compute_digest
from re1_rl.progress import ProgressTracker
from re1_rl.richard_lab import (
    richard_lab_countdown_screen_from_ram,
    richard_lab_timer_active,
)


def test_richard_lab_countdown_screen_detects_qs8_signature() -> None:
    ram = {
        "game_mode": PAUSE_MENU_GAME_MODE,
        "game_state": RICHARD_LAB_COUNTDOWN_STATUS_GAME_STATE,
        "lab_timer": 31829,
    }
    assert richard_lab_countdown_screen_from_ram(ram)
    assert richard_lab_timer_active(ram)


def test_richard_lab_countdown_screen_rejects_normal_status() -> None:
    ram = {
        "game_mode": PAUSE_MENU_GAME_MODE,
        "game_state": 0x40808004,
        "lab_timer": 100,
    }
    assert not richard_lab_countdown_screen_from_ram(ram)


def test_milestone_digest_richard_lab_done() -> None:
    progress = ProgressTracker()
    progress.mark_richard_lab_cleared()
    digest = compute_digest({"room_id": "204"}, progress, ever_held=set())
    assert "event:richard_lab_done" in digest


def test_progress_mark_richard_lab_cleared_once() -> None:
    progress = ProgressTracker()
    assert progress.mark_richard_lab_cleared() is True
    assert progress.mark_richard_lab_cleared() is False
    assert progress.richard_lab_cleared is True


def test_in_control_play_not_richard_trap() -> None:
    ram = {
        "game_mode": IN_CONTROL_MASK,
        "game_state": 0x80800004,
        "lab_timer": 0,
    }
    assert not richard_lab_countdown_screen_from_ram(ram)
    assert not richard_lab_timer_active(ram)
