"""Unit tests for OPTIONS dismiss macro (no BizHawk)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.memory_map import (
    OPTIONS_MENU_GAME_MODE,
    OPTIONS_MENU_GAME_STATE,
    PLAYER_X,
    PLAYER_Z,
)
from re1_rl.options_menu_macro import dismiss_options_menu, still_trapped_in_menu
from re1_rl.ram_skip import pause_menu_tree_from_ram
from re1_rl.game_session import options_menu_from_ram, pause_or_options_menu_from_ram


def test_still_trapped_detects_options_and_pause() -> None:
    opt = {
        "game_state": OPTIONS_MENU_GAME_STATE,
        "game_mode": OPTIONS_MENU_GAME_MODE,
        "msg_flag": 0,
        "scene_flag": 0,
    }
    assert options_menu_from_ram(opt)
    assert still_trapped_in_menu(opt)

    pause = {
        "game_state": 0x40808000,
        "game_mode": 0x40,
        "msg_flag": 0,
        "scene_flag": 0x80,
    }
    assert pause_menu_tree_from_ram(pause)
    assert still_trapped_in_menu(pause, episode_start_hp=96)

    legacy = {
        "game_state": 0x00000080,
        "game_mode": 0x80,
        "msg_flag": 0,
        "scene_flag": 0x80,
    }
    assert pause_or_options_menu_from_ram(legacy)
    assert still_trapped_in_menu(legacy, episode_start_hp=96)

    play = {
        "game_state": 0x80800000,
        "game_mode": 0x80,
        "msg_flag": 0,
        "scene_flag": 0x80,
    }
    assert not still_trapped_in_menu(play, episode_start_hp=96)


def test_dismiss_start_pause_up_cross_to_gameplay() -> None:
    """Mock client: OPTIONS -> pause after Start -> play after Up+Cross."""
    client = MagicMock()
    phase = {"n": 0, "x": 100, "z": 200}

    def read_ram(fields):
        names = [f[0] for f in fields]
        if names == ["player_hp"]:
            return {"player_hp": 96}
        if set(names) == {"x", "z"}:
            return {"x": phase["x"], "z": phase["z"]}

        n = phase["n"]
        if n < 2:
            gs, mode = OPTIONS_MENU_GAME_STATE, OPTIONS_MENU_GAME_MODE
        elif n < 6:
            gs, mode = 0x40808000, 0x40
        else:
            gs, mode = 0x80800000, 0x80
        phase["n"] += 1
        return {
            "player_hp": 96,
            "stage_id": 1,
            "room_id": 17,
            "character_id": 1,
            "game_mode": mode,
            "game_state": gs,
            "msg_flag": 0,
            "scene_flag": 0x80,
        }

    def step(buttons=None, n=1):
        buttons = buttons or {}
        if buttons.get("start"):
            phase["n"] = max(phase["n"], 2)
        elif buttons.get("cross"):
            phase["n"] = max(phase["n"], 6)
            phase["x"] += 8
        return None, False

    client.read_ram.side_effect = read_ram
    client.step.side_effect = step

    still, frames, report = dismiss_options_menu(
        client, prev_hp=96, episode_start_hp=96, max_attempts=1
    )
    assert still is False
    assert report["cleared"] is True
    assert "start" in report["sequence"] and "cross" in report["sequence"]
    assert frames > 0
