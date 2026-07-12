"""Cutscene exploration reward gating."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.cutscene_reward import (
    MIN_CUTSCENE_SKIP_FRAMES,
    OPENING_PHASES_NO_REWARD,
    qualify_cutscene_reward,
)
from re1_rl.game_session import opening_phase_from_ram
from re1_rl.memory_map import (
    OPENING_GAMEPLAY_TEASER_GAME_MODE,
    OPENING_GAMEPLAY_TEASER_GAME_STATE,
    OPENING_NARRATION_GAME_MODE,
    PAUSE_MENU_GAME_MODE,
)
from re1_rl.progress import ProgressTracker
from re1_rl.reward import NEW_CUTSCENE_BONUS, compute_reward
from tests.test_scaffolding import make_planner, make_state


def _qualify(
    prev,
    cur,
    *,
    skip_frames: int = MIN_CUTSCENE_SKIP_FRAMES,
    start_hp: int = 96,
    rewarded_cutscenes=None,
):
    return qualify_cutscene_reward(
        skip_frames=skip_frames,
        prev_state=prev,
        new_state=cur,
        episode_start_hp=start_hp,
        rewarded_cutscenes=rewarded_cutscenes,
    )


def test_min_skip_frames():
    prev = make_state(room="105", cam_id=1, hp=96)
    cur = make_state(room="105", cam_id=1, hp=96)
    assert _qualify(prev, cur, skip_frames=19) is None
    assert _qualify(prev, cur, skip_frames=20) == "105:1:s0"


def test_damage_disqualifies():
    prev = make_state(room="105", cam_id=1, hp=96)
    cur = make_state(room="105", cam_id=1, hp=80)
    assert _qualify(prev, cur) is None


def test_dog_death_fade_at_zero_hp_disqualifies():
    """White fade after dog kill: skip runs at hp==0 — no cutscene farm."""
    prev = make_state(
        room="104",
        cam_id=2,
        hp=0,
        scene_flag=0x90,
        game_mode=0x80,
        game_state=0x80800000,
    )
    cur = make_state(room="104", cam_id=2, hp=0, scene_flag=0x80)
    assert _qualify(prev, cur, skip_frames=120) is None


def test_dog_death_fade_at_invalid_hp_disqualifies():
    """Live RAM during dog kill: HP reads 0xFFFF — still no cutscene farm."""
    prev = make_state(
        room="108",
        cam_id=2,
        hp=65535,
        scene_flag=0x80,
        game_mode=0x80,
        game_state=0x80800000,
    )
    cur = make_state(
        room="108",
        cam_id=2,
        hp=65535,
        scene_flag=0x04,
        game_mode=0x80,
        game_state=0x80040000,
    )
    assert _qualify(prev, cur, skip_frames=120, start_hp=16) is None


def test_barry_scene_at_full_hp_still_pays():
    prev = make_state(
        room="106",
        hp=96,
        scene_flag=0x90,
        game_mode=0x80,
        game_state=0x80800000,
        cam_id=1,
    )
    cur = make_state(room="106", hp=96, cam_id=2, scene_flag=0x80)
    assert _qualify(prev, cur, skip_frames=120) == "106:1:s0"


def test_same_room_second_cutscene_pays_new_sequence():
    """Barry talk then Barry zombie on return: same room:cam, still pays once each."""
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="105", cam_id=0, hp=96)
    assert _qualify(prev, cur) == "105:0:s0"
    assert _qualify(prev, cur, rewarded_cutscenes={"105:0:s0"}) == "105:0:s1"

    planner = make_planner()
    progress = ProgressTracker()
    cur0 = dict(cur)
    cur0["cutscene_key"] = "105:0:s0"
    _, bd0 = compute_reward(prev, cur0, planner, progress=progress, return_breakdown=True)
    assert bd0["new_cutscene"] == NEW_CUTSCENE_BONUS

    cur1 = dict(cur)
    cur1["cutscene_key"] = _qualify(prev, cur, rewarded_cutscenes=progress.rewarded_cutscenes)
    assert cur1["cutscene_key"] == "105:0:s1"
    _, bd1 = compute_reward(cur0, cur1, planner, progress=progress, return_breakdown=True)
    assert bd1["new_cutscene"] == NEW_CUTSCENE_BONUS


def test_opening_gameplay_teaser_excluded():
    prev = make_state(
        room="106",
        hp=140,
        stage_id=0,
        room_byte=6,
        character_id=1,
        game_mode=OPENING_GAMEPLAY_TEASER_GAME_MODE,
        game_state=OPENING_GAMEPLAY_TEASER_GAME_STATE,
        scene_flag=0,
        msg_flag=0,
        cam_id=0,
    )
    cur = make_state(room="106", hp=140, cam_id=0)
    phase = opening_phase_from_ram(
        {
            "room_id": 6,
            "stage_id": 0,
            "player_hp": 140,
            "character_id": 1,
            "game_mode": OPENING_GAMEPLAY_TEASER_GAME_MODE,
            "game_state": OPENING_GAMEPLAY_TEASER_GAME_STATE,
            "scene_flag": 0,
            "msg_flag": 0,
        },
        had_mansion_hp=True,
    )
    assert phase == "opening_gameplay_teaser"
    assert phase in OPENING_PHASES_NO_REWARD
    assert _qualify(prev, cur, start_hp=140) is None


def test_opening_narration_excluded():
    prev = make_state(
        room="100",
        hp=0,
        stage_id=0,
        room_byte=0,
        game_mode=OPENING_NARRATION_GAME_MODE,
        game_state=0,
        cam_id=0,
    )
    cur = make_state(room="100", hp=0, cam_id=0)
    assert _qualify(prev, cur, start_hp=0) is None


def test_mansion_intro_cutscene_pays_after_spawn():
    prev = make_state(
        room="105",
        hp=96,
        stage_id=0,
        room_byte=5,
        character_id=1,
        game_mode=0x62,
        game_state=0x62800000,
        scene_flag=0x80,
        msg_flag=0,
        cam_id=2,
    )
    cur = make_state(room="104", hp=96, cam_id=0)
    assert _qualify(prev, cur, skip_frames=120, start_hp=96) == "105:2"


def test_pause_menu_cutscene_reward_disqualified():
    prev = make_state(
        room="105",
        cam_id=1,
        hp=96,
        game_mode=PAUSE_MENU_GAME_MODE,
        game_state=0x40808104,
    )
    cur = make_state(room="105", cam_id=1, hp=96)
    assert _qualify(prev, cur) is None


def test_unique_key_blocks_door_spam():
    planner = make_planner()
    progress = ProgressTracker()
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="106", cam_id=1, hp=96)
    key = _qualify(prev, cur)
    assert key == "105:0"
    cur["cutscene_key"] = key
    _, bd0 = compute_reward(
        prev, cur, planner, progress=progress, return_breakdown=True,
    )
    assert bd0["new_cutscene"] == NEW_CUTSCENE_BONUS

    prev2 = make_state(room="106", cam_id=1, hp=96)
    cur2 = make_state(room="105", cam_id=0, hp=96)
    key2 = _qualify(prev2, cur2)
    assert key2 == "106:1"
    cur2["cutscene_key"] = key2
    _, bd1 = compute_reward(
        prev2, cur2, planner, progress=progress, return_breakdown=True,
    )
    assert bd1["new_cutscene"] == NEW_CUTSCENE_BONUS

    cur2b = make_state(room="105", cam_id=0, hp=96, cutscene_key="105:0")
    _, bd2 = compute_reward(
        cur2, cur2b, planner, progress=progress, return_breakdown=True,
    )
    assert bd2["new_cutscene"] == 0.0

    cur3 = make_state(room="106", cam_id=1, hp=96, cutscene_key="106:1")
    _, bd3 = compute_reward(
        cur2b, cur3, planner, progress=progress, return_breakdown=True,
    )
    assert bd3["new_cutscene"] == 0.0
