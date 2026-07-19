"""Unit checks for move/Cross filter and non-step reward formatting."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_PLAY = ROOT / "scripts" / "play_human.py"
_spec = importlib.util.spec_from_file_location("play_human_mod", _PLAY)
assert _spec and _spec.loader
play = importlib.util.module_from_spec(_spec)
sys.modules["play_human_mod"] = play
_spec.loader.exec_module(play)


def test_filter_move_cross_strips_combat_and_maps_triangle() -> None:
    got = play.filter_move_cross_only(
        {
            "up": True,
            "r1": True,
            "cross": True,
            "triangle": True,
            "circle": True,
        }
    )
    assert got == {"up": True, "cross": True}


def test_format_non_step_skips_step_channel() -> None:
    line = play.format_non_step_reward_line(
        {"step": -0.0002, "new_room": 1.0, "pbrs_graph": 0.01},
        reward=0.9998,
        state={
            "room_id": "105",
            "hp": 96,
            "x": 100,
            "z": 200,
            "cam_id": 0,
            "in_control": True,
        },
    )
    assert line is not None
    assert "new_room=+1.0000" in line
    assert "step=" not in line
    assert "room=105" in line
    assert "hp=96" in line


def test_format_non_step_silent_when_only_tiny_step() -> None:
    assert (
        play.format_non_step_reward_line(
            {"step": -0.0002},
            reward=-0.0002,
            state={"room_id": "105", "hp": 96, "x": 0, "z": 0, "cam_id": 0},
        )
        is None
    )


def test_skip_session_follows_needs_skip_not_in_control() -> None:
    """Barry dialogue keeps in_control; session must stay open like the agent."""
    assert play.skip_session_active(True) is True
    assert play.skip_session_active(False) is False


def test_human_cutscene_gate_matches_four_second_agent_floor() -> None:
    from re1_rl.cutscene_reward import (
        MIN_CUTSCENE_SKIP_FRAMES,
        qualify_cutscene_reward,
    )

    prev = {
        "room_id": "105",
        "cam_id": 2,
        "hp": 96,
        "scene_flag": 0x80,
        "msg_flag": 0x00,
        "x": 31203,
        "z": 6892,
        "room_byte": 5,
        "stage_id": 0,
    }
    cur = dict(prev)
    assert (
        qualify_cutscene_reward(
            skip_frames=150,
            prev_state=prev,
            new_state=cur,
            visited_rooms={"105"},
        )
        is None
    )
    assert (
        qualify_cutscene_reward(
            skip_frames=MIN_CUTSCENE_SKIP_FRAMES,
            prev_state=prev,
            new_state=cur,
            visited_rooms={"105"},
        )
        == "105:2:s0"
    )
    assert (
        qualify_cutscene_reward(
            skip_frames=1221,
            prev_state=prev,
            new_state=cur,
            visited_rooms={"105"},
        )
        == "105:2:s0"
    )
