"""Static-stall skip tripwire: unknown modal menus must abort the episode.

pking 2026-09-12: all 20 actors sat ~70 min in the START-menu tree whose
(mode, gs) misses the pause-tree detector. needs_skip stayed True, every
skip burned its full budget, actors kept sending need (worker stale
watchdog silent by design), and no episode ever ended. The tripwire aborts
after N consecutive static no-control skip hits and logs the signature.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.env import _SKIP_STALL_REASON, RE1Env


def _stub_env(ram: dict) -> RE1Env:
    env = RE1Env.__new__(RE1Env)
    env.bridge = MagicMock()
    env.bridge.port = 6500
    env.bridge.read_ram.return_value = dict(ram)
    env._skip_stall_hits = 0
    env._skip_stall_snapshot = None
    env._pending_episode_failure = None
    return env


MENU_RAM = {
    "player_hp": 96,
    "stage_id": 0,
    "room_id": 5,
    "character_id": 1,
    # START/MAP-family screen: control clear, gs outside the pause tree.
    "game_mode": 0x40,
    "game_state": 0x40800300,
    "msg_flag": 0,
    "scene_flag": 0,
}


def test_static_menu_trips(monkeypatch) -> None:
    monkeypatch.setenv("RE1_SKIP_STALL_LIMIT", "4")
    env = _stub_env(MENU_RAM)
    assert env._note_skip_stall(track=True) is False
    assert env._note_skip_stall(track=True) is False
    assert env._note_skip_stall(track=True) is False
    assert env._pending_episode_failure is None
    assert env._note_skip_stall(track=True) is True
    assert env._pending_episode_failure == _SKIP_STALL_REASON


def test_room_change_resets_streak(monkeypatch) -> None:
    monkeypatch.setenv("RE1_SKIP_STALL_LIMIT", "3")
    env = _stub_env(MENU_RAM)
    assert env._note_skip_stall(track=True) is False
    assert env._note_skip_stall(track=True) is False
    moved = dict(MENU_RAM)
    moved["room_id"] = 6  # door crossing: progress, not a stall.
    env.bridge.read_ram.return_value = moved
    assert env._note_skip_stall(track=True) is False
    assert env._pending_episode_failure is None
    # Recount from 1: two more static hits trip (limit 3).
    assert env._note_skip_stall(track=True) is False
    assert env._note_skip_stall(track=True) is True


def test_hp_rise_resets_streak(monkeypatch) -> None:
    monkeypatch.setenv("RE1_SKIP_STALL_LIMIT", "3")
    env = _stub_env(MENU_RAM)
    assert env._note_skip_stall(track=True) is False
    assert env._note_skip_stall(track=True) is False
    healed = dict(MENU_RAM)
    healed["player_hp"] = 120  # healing needs control: not a stall.
    env.bridge.read_ram.return_value = healed
    assert env._note_skip_stall(track=True) is False
    assert env._pending_episode_failure is None


def test_hp_chip_damage_still_static(monkeypatch) -> None:
    """Poison/chip HP loss must not mask a frozen modal."""
    monkeypatch.setenv("RE1_SKIP_STALL_LIMIT", "3")
    env = _stub_env(MENU_RAM)
    assert env._note_skip_stall(track=True) is False
    assert env._note_skip_stall(track=True) is False
    hurt = dict(MENU_RAM)
    hurt["player_hp"] = 90
    env.bridge.read_ram.return_value = hurt
    assert env._note_skip_stall(track=True) is True
    assert env._pending_episode_failure == _SKIP_STALL_REASON


def test_track_false_resets() -> None:
    env = _stub_env(MENU_RAM)
    assert env._note_skip_stall(track=True) is False
    assert env._skip_stall_hits == 1
    assert env._note_skip_stall(track=False) is False
    assert env._skip_stall_hits == 0
    assert env._skip_stall_snapshot is None


def test_ram_read_error_never_trips() -> None:
    env = _stub_env(MENU_RAM)
    env.bridge.read_ram.side_effect = OSError("game gone")
    for _ in range(30):
        assert env._note_skip_stall(track=True) is False
    assert env._pending_episode_failure is None
