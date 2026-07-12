"""Unit tests for OPTIONS dismiss macro (no BizHawk)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.memory_map import OPTIONS_MENU_GAME_MODE, OPTIONS_MENU_GAME_STATE
from re1_rl.options_menu_macro import dismiss_options_menu, still_trapped_in_menu
from re1_rl.ram_skip import pause_menu_tree_from_ram
from re1_rl.game_session import options_menu_from_ram


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
    assert still_trapped_in_menu(pause)

    play = {
        "game_state": 0x80800000,
        "game_mode": 0x80,
        "msg_flag": 0,
        "scene_flag": 0x80,
    }
    assert not still_trapped_in_menu(play)


def test_dismiss_sequence_right_right_cross_then_start() -> None:
    """Mock client: OPTIONS -> pause after RRX -> play after Start."""
    client = MagicMock()
    # read_ram returns evolve across calls
    phase = {"n": 0}

    def read_ram(fields):
        n = phase["n"]
        if n < 4:
            # still on options for initial reads + during first taps
            gs, mode = OPTIONS_MENU_GAME_STATE, OPTIONS_MENU_GAME_MODE
        elif n < 8:
            gs, mode = 0x40808000, 0x40
        else:
            gs, mode = 0x80800000, 0x80
        phase["n"] += 1
        vals = {
            "player_hp": 96,
            "stage_id": 1,
            "room_id": 17,
            "character_id": 1,
            "game_mode": mode,
            "game_state": gs,
            "msg_flag": 0,
            "scene_flag": 0x80,
        }
        # support both full field list and hp-only
        if len(fields) == 1 and fields[0][0] == "player_hp":
            return {"player_hp": 96}
        return vals

    def step(buttons=None, n=1):
        # advance phase roughly when we see start
        buttons = buttons or {}
        if buttons.get("start"):
            phase["n"] = max(phase["n"], 8)
        elif buttons.get("cross") and phase["n"] < 8:
            phase["n"] = max(phase["n"], 4)
        return None, False

    client.read_ram.side_effect = read_ram
    client.step.side_effect = step

    still, frames, report = dismiss_options_menu(
        client, prev_hp=96, episode_start_hp=96, max_attempts=2
    )
    assert still is False
    assert report["cleared"] is True
    assert "right" in report["sequence"] and "cross" in report["sequence"]
    assert "start" in report["sequence"]
    assert frames > 0
