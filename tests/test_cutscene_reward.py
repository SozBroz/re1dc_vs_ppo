"""Cutscene exploration reward gating."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.cutscene_reward import (
    MIN_CUTSCENE_SKIP_FRAMES,
    OPENING_PHASES_NO_REWARD,
    qualify_cutscene_reward,
    room_change_cutscene_disqualified,
)
from re1_rl.story_item_use import annotate_story_use_success
from re1_rl.game_session import opening_phase_from_ram
from re1_rl.memory_map import (
    OPENING_GAMEPLAY_TEASER_GAME_MODE,
    OPENING_GAMEPLAY_TEASER_GAME_STATE,
    OPENING_NARRATION_GAME_MODE,
    PAUSE_MENU_GAME_MODE,
)
from re1_rl.progress import ProgressTracker
from re1_rl.reward import NEW_CUTSCENE_BONUS, NEW_ROOM_BONUS, compute_reward
from tests.test_scaffolding import make_planner, make_state

# Cutscene pay is gated until Kenneth (104:*:sN) has paid this episode.
AFTER_KENNETH = frozenset({"104:0:s0"})


def _qualify(
    prev,
    cur,
    *,
    skip_frames: int = MIN_CUTSCENE_SKIP_FRAMES,
    start_hp: int = 96,
    rewarded_cutscenes=None,
    visited_rooms=None,
):
    return qualify_cutscene_reward(
        skip_frames=skip_frames,
        prev_state=prev,
        new_state=cur,
        episode_start_hp=start_hp,
        rewarded_cutscenes=rewarded_cutscenes,
        visited_rooms=visited_rooms,
    )


def test_pre_kenneth_blocks_all_cutscenes_except_kenneth():
    """Imperator: no new_cutscene until Kenneth; Kenneth itself still pays."""
    # Dining Barry / door cam — blocked.
    prev_d = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    cur_d = make_state(room="105", cam_id=0, hp=96, scene_flag=0x93)
    assert _qualify(prev_d, cur_d, skip_frames=120) is None
    # Kenneth tea-room script — pays with empty rewarded set.
    prev_k = make_state(room="104", cam_id=0, hp=96, scene_flag=0x84)
    cur_k = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    assert (
        _qualify(
            prev_k,
            cur_k,
            skip_frames=120,
            visited_rooms={"105", "106", "104"},
        )
        == "104:0:s0"
    )
    # After Kenneth, dining pays again.
    assert (
        _qualify(
            prev_d,
            cur_d,
            skip_frames=120,
            rewarded_cutscenes=AFTER_KENNETH,
            visited_rooms={"105", "106", "104"},
        )
        == "105:0:s0"
    )


def test_min_skip_frames():
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x93)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    assert _qualify(prev, cur, skip_frames=19, rewarded_cutscenes=AFTER_KENNETH) is None
    assert (
        _qualify(prev, cur, skip_frames=20, rewarded_cutscenes=AFTER_KENNETH)
        == "105:0:s0"
    )


def test_examine_locked_text_same_room_does_not_pay():
    prev = make_state(room="107", cam_id=2, hp=96, scene_flag=0x80)
    cur = make_state(room="107", cam_id=2, hp=96, scene_flag=0x80)
    assert _qualify(prev, cur, skip_frames=120) is None


def test_barry_scene_change_same_room_still_pays():
    """Barry pays only after Kenneth (pre-Kenneth dining cutscenes gated)."""
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x93)
    assert _qualify(prev, cur, skip_frames=120) is None
    assert (
        _qualify(prev, cur, skip_frames=120, rewarded_cutscenes=AFTER_KENNETH)
        == "105:0:s0"
    )


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
    assert _qualify(prev, cur, skip_frames=120) is None
    assert _qualify(
        prev,
        cur,
        skip_frames=120,
        rewarded_cutscenes={"104:0:s0"},
        visited_rooms={"104", "106"},
    ) == "106:1:s0"


def test_same_room_second_cutscene_pays_new_sequence():
    """Barry talk then Barry zombie on return: same room:cam, still pays once each."""
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x93)
    assert _qualify(prev, cur, rewarded_cutscenes=AFTER_KENNETH) == "105:0:s0"
    prev2 = make_state(room="105", cam_id=0, hp=96, scene_flag=0x91)
    cur2 = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    assert (
        _qualify(
            prev2,
            cur2,
            rewarded_cutscenes={*AFTER_KENNETH, "105:0:s0"},
        )
        == "105:0:s1"
    )

    planner = make_planner()
    progress = ProgressTracker()
    progress.rewarded_cutscenes.add("104:0:s0")
    cur0 = dict(cur)
    cur0["cutscene_key"] = "105:0:s0"
    _, bd0 = compute_reward(prev, cur0, planner, progress=progress, return_breakdown=True)
    assert bd0["new_cutscene"] == NEW_CUTSCENE_BONUS

    cur1 = dict(cur2)
    cur1["cutscene_key"] = _qualify(
        prev2, cur2, rewarded_cutscenes=progress.rewarded_cutscenes
    )
    assert cur1["cutscene_key"] == "105:0:s1"
    _, bd1 = compute_reward(cur0, cur1, planner, progress=progress, return_breakdown=True)
    assert bd1["new_cutscene"] == NEW_CUTSCENE_BONUS


def test_same_room_cutscene_index_capped_after_s1():
    """scene_flag flicker must not mint unbounded :s2+: cutscene bonuses."""
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x91)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    assert (
        _qualify(
            prev,
            cur,
            rewarded_cutscenes={*AFTER_KENNETH, "105:0:s0", "105:0:s1"},
        )
        is None
    )


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
    assert _qualify(prev, cur, skip_frames=120, start_hp=96) is None


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


def test_story_use_from_menu_still_pays_cutscene():
    prev = make_state(
        room="10F",
        cam_id=1,
        hp=96,
        scene_flag=0x80,
        game_mode=PAUSE_MENU_GAME_MODE,
        game_state=0x40808104,
    )
    cur = make_state(room="10F", cam_id=1, hp=96, scene_flag=0x93)
    cur["story_use_success"] = "music_notes@10F_piano"
    assert (
        _qualify(prev, cur, skip_frames=120, rewarded_cutscenes=AFTER_KENNETH)
        == "10F:1:s0"
    )


def test_story_use_and_cutscene_both_pay_after_menu_skip():
    from re1_rl.memory_map import ITEM_IDS
    from re1_rl.progress import ProgressTracker
    from re1_rl.reward import NEW_CUTSCENE_BONUS, STORY_ITEM_USE_BONUS, compute_reward

    music_id = next(i for i, n in ITEM_IDS.items() if n == "music_notes")
    inv_before = [(0, 0)] * 4 + [(music_id, 0)]
    inv_after = [(0, 0)] * 5
    prev = make_state(
        room="10F",
        cam_id=1,
        hp=96,
        x=9737,
        z=8020,
        scene_flag=0x80,
        game_mode=PAUSE_MENU_GAME_MODE,
        game_state=0x40808104,
    )
    cur = make_state(room="10F", cam_id=1, hp=96, scene_flag=0x93)
    cur = annotate_story_use_success(
        cur,
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
        rewarded_site_ids=set(),
    )
    assert cur.get("story_use_success") == "music_notes@10F_piano"
    cur["cutscene_key"] = _qualify(
        prev, cur, skip_frames=120, rewarded_cutscenes=AFTER_KENNETH
    )
    assert cur["cutscene_key"] == "10F:1:s0"
    progress = ProgressTracker()
    progress.rewarded_cutscenes.add("104:0:s0")
    _, bd = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    assert bd["story_use"] == STORY_ITEM_USE_BONUS
    assert bd["new_cutscene"] == NEW_CUTSCENE_BONUS


def test_successful_music_notes_use_pays_cutscene_despite_idle_scene():
    """Live piano USE skip: same-room idle scene_flag — still pay new_cutscene."""
    from re1_rl.memory_map import ITEM_IDS
    from re1_rl.progress import ProgressTracker
    from re1_rl.reward import NEW_CUTSCENE_BONUS, STORY_ITEM_USE_BONUS, compute_reward

    music_id = next(i for i, n in ITEM_IDS.items() if n == "music_notes")
    inv_before = [(0, 0)] * 4 + [(music_id, 0)]
    inv_after = [(0, 0)] * 5
    prev = make_state(
        room="10F",
        cam_id=1,
        hp=96,
        x=9737,
        z=8020,
        scene_flag=0x80,
        msg_flag=0,
        game_mode=PAUSE_MENU_GAME_MODE,
        game_state=0x40808104,
    )
    # Follow-on cutscene can still read as idle mansion scene (examine-text shape).
    cur = make_state(
        room="10F", cam_id=1, hp=96, scene_flag=0x80, msg_flag=0
    )
    cur = annotate_story_use_success(
        cur,
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
        rewarded_site_ids=set(),
    )
    assert cur.get("story_use_success") == "music_notes@10F_piano"
    cur["cutscene_key"] = _qualify(
        prev, cur, skip_frames=357, rewarded_cutscenes=AFTER_KENNETH
    )
    assert cur["cutscene_key"] == "10F:1:s0"
    progress = ProgressTracker()
    progress.rewarded_cutscenes.add("104:0:s0")
    _, bd = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    assert bd["story_use"] == STORY_ITEM_USE_BONUS
    assert bd["new_cutscene"] == NEW_CUTSCENE_BONUS


def test_failed_story_use_idle_skip_still_blocked_as_examine_text():
    """Failed USE leaves no story_use_success — examine-text gate still applies."""
    prev = make_state(
        room="10F", cam_id=1, hp=96, scene_flag=0x80, msg_flag=0
    )
    cur = make_state(room="10F", cam_id=1, hp=96, scene_flag=0x80, msg_flag=0)
    assert "story_use_success" not in cur
    assert _qualify(prev, cur, skip_frames=120) is None


def test_main_hall_cluster_blocked_before_kenneth():
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="106", cam_id=1, hp=96)
    assert _qualify(prev, cur, visited_rooms=set()) is None

    prev_hall = make_state(
        room="106",
        hp=96,
        scene_flag=0x90,
        game_mode=0x80,
        game_state=0x80800000,
        cam_id=1,
    )
    cur_hall = make_state(room="106", hp=96, cam_id=2, scene_flag=0x80)
    assert _qualify(prev_hall, cur_hall, visited_rooms={"106"}) is None


def test_main_hall_reentry_before_kenneth_disqualified():
    prev = make_state(room="105", cam_id=1, hp=96)
    cur = make_state(room="106", cam_id=1, hp=96)
    assert _qualify(prev, cur, visited_rooms={"105", "106"}) is None

    prev_hall = make_state(room="106", hp=96, cam_id=1, scene_flag=0x91)
    cur_hall = make_state(room="106", hp=96, cam_id=1, scene_flag=0x80)
    assert _qualify(
        prev_hall,
        cur_hall,
        rewarded_cutscenes={"106:1:s0"},
        visited_rooms={"106"},
    ) is None


def test_main_hall_backtrack_allowed_after_kenneth():
    prev = make_state(room="105", cam_id=1, hp=96)
    cur = make_state(room="106", cam_id=1, hp=96)
    assert _qualify(
        prev,
        cur,
        visited_rooms={"105", "106", "104"},
        rewarded_cutscenes={"104:0:s0"},
    ) is None

    prev_hall = make_state(room="106", hp=96, cam_id=1, scene_flag=0x91)
    cur_hall = make_state(room="106", hp=96, cam_id=2, scene_flag=0x80)
    assert _qualify(
        prev_hall,
        cur_hall,
        rewarded_cutscenes={"104:0:s0", "106:1:s0"},
        visited_rooms={"106", "104"},
    ) == "106:1:s1"


def test_room_change_door_skip_does_not_pay_cutscene():
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    cur = make_state(room="106", cam_id=1, hp=96, scene_flag=0x80)
    assert room_change_cutscene_disqualified(prev, cur) is True
    assert _qualify(prev, cur, skip_frames=508) is None


def test_short_door_skip_reports_room_change_not_length():
    """Patched doors burn few frames — unpaid reason must be door, not length."""
    from re1_rl.cutscene_reward import cutscene_disqualify_reason, skip_session_kind

    prev = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80)
    cur = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    assert skip_session_kind(prev, cur) == "door_room_change"
    assert _qualify(prev, cur, skip_frames=4) is None
    assert (
        cutscene_disqualify_reason(
            skip_frames=4, prev_state=prev, new_state=cur, episode_start_hp=96
        )
        == "room-change door skip (same-room scripts only)"
    )


def test_script_dialogue_msg_change_pays():
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x00)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x80)
    assert _qualify(prev, cur, skip_frames=120) is None
    assert (
        _qualify(prev, cur, skip_frames=120, rewarded_cutscenes=AFTER_KENNETH)
        == "105:0:s0"
    )


def test_dining_script_after_hall_blocked_until_kenneth():
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x91, msg_flag=0x00)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x80)
    # Hall visit alone is not enough — Kenneth must have paid.
    assert _qualify(
        prev,
        cur,
        skip_frames=120,
        rewarded_cutscenes={"105:0:s0"},
        visited_rooms={"105", "106"},
    ) is None
    prev_w = make_state(room="105", cam_id=1, hp=96, scene_flag=0x91)
    cur_w = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80)
    assert _qualify(
        prev_w,
        cur_w,
        skip_frames=120,
        rewarded_cutscenes=set(),
        visited_rooms={"105", "106"},
    ) is None
    # Wesker farm without ever visiting main hall (live shape).
    assert _qualify(
        prev_w,
        cur_w,
        skip_frames=120,
        rewarded_cutscenes=set(),
        visited_rooms={"105"},
    ) is None
    assert _qualify(
        prev,
        cur,
        skip_frames=120,
        rewarded_cutscenes={"105:0:s0", "104:0:s0"},
        visited_rooms={"105", "106", "104"},
    ) == "105:0:s1"


def test_barry_cluster_pays_after_hall_visit():
    """Barry cluster pays after Kenneth (hall visit alone insufficient)."""
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x93)
    assert _qualify(
        prev,
        cur,
        skip_frames=120,
        visited_rooms={"105", "106"},
    ) is None
    assert _qualify(
        prev,
        cur,
        skip_frames=120,
        rewarded_cutscenes=AFTER_KENNETH,
        visited_rooms={"105", "106", "104"},
    ) == "105:0:s0"


def test_barry_msg_dialogue_short_skip_still_pays():
    """Msg-only Barry needs SCRIPT_DIALOGUE_MIN; shorter is interact/examine farm."""
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x00)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x80)
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=25,
            rewarded_cutscenes=AFTER_KENNETH,
            visited_rooms={"105", "106", "104"},
        )
        is None
    )
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=60,
            rewarded_cutscenes=AFTER_KENNETH,
            visited_rooms={"105", "106", "104"},
        )
        == "105:0:s0"
    )


def test_barry_walkup_msg_on_cam1_short_skip_pays():
    """Barry walk-up msg on cam 1/2: blocked pre-Kenneth (Wesker door farm).

    Cam 0 msg dialogue still pays; cam 1/2 need Kenneth or scene movement.
    """
    prev = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80, msg_flag=0x00)
    cur = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80, msg_flag=0x80)
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=25,
            visited_rooms={"105"},
        )
        is None
    )
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=60,
            visited_rooms={"105"},
        )
        is None
    )
    prev2 = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0x00)
    cur2 = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0x80)
    assert (
        _qualify(
            prev2,
            cur2,
            skip_frames=25,
            visited_rooms={"105"},
        )
        is None
    )
    assert (
        _qualify(
            prev2,
            cur2,
            skip_frames=60,
            visited_rooms={"105"},
        )
        is None
    )
    # Post-Kenneth: msg dialogue on cam 1/2 pays again.
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=60,
            visited_rooms={"105", "104"},
            rewarded_cutscenes={"104:0:s0"},
        )
        == "105:1:s0"
    )


def test_barry_idle_settle_pays_away_from_hall_door():
    """Barry walk-up idle-settle: blocked pre-Kenneth; pays after Kenneth."""
    from re1_rl.cutscene_reward import STORY_IDLE_SETTLE_MIN_SKIP_FRAMES

    # Mid-dining (tea-door side), not hall-door Wesker zone.
    prev = make_state(
        room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0x00, x=15000, z=10000
    )
    cur = make_state(
        room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0x00, x=15000, z=10000
    )
    assert _qualify(prev, cur, skip_frames=34, visited_rooms={"105"}) is None
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=STORY_IDLE_SETTLE_MIN_SKIP_FRAMES - 1,
            visited_rooms={"105"},
        )
        is None
    )
    assert _qualify(prev, cur, skip_frames=1219, visited_rooms={"105"}) is None
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=1219,
            rewarded_cutscenes=AFTER_KENNETH,
            visited_rooms={"105", "104"},
        )
        == "105:2:s0"
    )
    # Cam0 scene change still pays after Kenneth.
    prev_b = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    cur_b = make_state(room="105", cam_id=0, hp=96, scene_flag=0x93)
    assert _qualify(prev_b, cur_b, skip_frames=1223, visited_rooms={"105"}) is None
    assert (
        _qualify(
            prev_b,
            cur_b,
            skip_frames=1223,
            rewarded_cutscenes=AFTER_KENNETH,
            visited_rooms={"105", "104"},
        )
        == "105:0:s0"
    )


def test_live_wesker_door_farm_pre_kenneth_blocked():
    """Regression: dining→hall door interact spam before Kenneth (never visits 106)."""
    # Default make_state pose is near 105→106 (Wesker zone).
    prev = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0x00)
    cur = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0x00)
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=1223,
            visited_rooms={"105"},
            rewarded_cutscenes=set(),
        )
        is None
    )
    # Explicit door coords (empirical 105→106).
    prev_d = make_state(
        room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0x00, x=30700, z=7200
    )
    cur_d = make_state(
        room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0x00, x=30700, z=7200
    )
    assert _qualify(prev_d, cur_d, skip_frames=1223, visited_rooms={"105"}) is None
    # After Kenneth, same shape at door may still be blocked by examine.
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=1223,
            visited_rooms={"105", "104"},
            rewarded_cutscenes={"104:0:s0"},
        )
        is None
    )


def test_kenneth_pays_with_main_hall_visited():
    prev = make_state(room="104", cam_id=0, hp=96, scene_flag=0x84)
    cur = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    assert _qualify(
        prev,
        cur,
        skip_frames=120,
        visited_rooms={"105", "106", "104"},
        rewarded_cutscenes={"105:0:s0"},
    ) == "104:0:s0"


def test_kenneth_msg_dialogue_short_skip_still_pays():
    """Msg-only dialogue needs SCRIPT_DIALOGUE_MIN frames; scene beats stay short-ok."""
    prev = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x00)
    cur = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x40)
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=25,
            visited_rooms={"105", "106", "104"},
        )
        is None
    )
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=60,
            visited_rooms={"105", "106", "104"},
        )
        == "104:0:s0"
    )
    prev_s = make_state(room="104", cam_id=0, hp=96, scene_flag=0x84, msg_flag=0x00)
    cur_s = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x00)
    assert (
        _qualify(
            prev_s,
            cur_s,
            skip_frames=25,
            visited_rooms={"105", "106", "104"},
        )
        == "104:0:s0"
    )


def test_same_camera_second_dining_beat_pays_after_kenneth():
    """Post-Kenneth: same-room cam changes still pay (Barry sequel + other cams)."""
    prev = make_state(room="105", cam_id=2, hp=96, scene_flag=0x93, msg_flag=0x80)
    cur = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0x00)
    assert _qualify(
        prev,
        cur,
        skip_frames=25,
        rewarded_cutscenes={"105:0:s0", "104:0:s0"},
        visited_rooms={"105", "106", "104"},
    ) == "105:2:s0"
    prev_b = make_state(room="105", cam_id=0, hp=96, scene_flag=0x93, msg_flag=0x80)
    cur_b = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x00)
    assert _qualify(
        prev_b,
        cur_b,
        skip_frames=25,
        rewarded_cutscenes={"105:0:s0", "104:0:s0"},
        visited_rooms={"105", "106", "104"},
    ) == "105:0:s1"


def test_dining_idle_examine_spam_not_exempt():
    """Blanket dining examine exemption must not farm ``105:1:sN``."""
    prev = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80)
    cur = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80)
    assert _qualify(
        prev,
        cur,
        skip_frames=60,
        rewarded_cutscenes={"104:0:s0"},
        visited_rooms={"105", "106", "104"},
    ) is None


def test_barry_cam0_short_interact_msg_does_not_pay():
    """Live farm: interact at dining cam0, skip_frames=34, mint 105:0:s0 — blocked."""
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x00)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x80)
    assert _qualify(prev, cur, skip_frames=34) is None
    assert _qualify(prev, cur, skip_frames=34, visited_rooms={"105", "104"}) is None
    # Pre-Kenneth: long msg still blocked. Post-Kenneth: pays.
    assert _qualify(prev, cur, skip_frames=60) is None
    assert (
        _qualify(prev, cur, skip_frames=60, rewarded_cutscenes=AFTER_KENNETH)
        == "105:0:s0"
    )


def test_same_camera_sequenced_beats_both_pay():
    prev = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x00)
    cur = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0x80)
    assert _qualify(prev, cur, skip_frames=60, rewarded_cutscenes=AFTER_KENNETH) == "105:0:s0"
    assert _qualify(
        prev,
        cur,
        skip_frames=60,
        rewarded_cutscenes={*AFTER_KENNETH, "105:0:s0"},
    ) == "105:0:s1"


def test_wesker_dining_cam_still_blocked_pre_kenneth():
    """Pre-Kenneth Wesker dining cams blocked with or without hall visit."""
    prev = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80, msg_flag=0x80)
    cur = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80, msg_flag=0x00)
    assert _qualify(
        prev,
        cur,
        skip_frames=25,
        visited_rooms={"105", "106"},
    ) is None
    assert _qualify(
        prev,
        cur,
        skip_frames=25,
        visited_rooms={"105"},
    ) is None


def test_dining_return_skip_entry_same_room_blocked_after_hall():
    """Door-crossing split resets skip entry to 105 — Wesker cam still blocked."""
    prev = make_state(room="105", cam_id=1, hp=96, scene_flag=0x90)
    cur = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80)
    assert _qualify(
        prev,
        cur,
        skip_frames=200,
        rewarded_cutscenes=set(),
        visited_rooms={"105", "106"},
    ) is None
    assert _qualify(
        prev,
        cur,
        skip_frames=200,
        rewarded_cutscenes=set(),
        visited_rooms={"105"},
    ) is None


def test_main_hall_new_room_gated_until_kenneth():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="106", cam_id=1, hp=96)
    _, bd = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    assert bd["new_room"] == 0.0
    assert "106" in progress.visited_rooms

    progress2 = ProgressTracker()
    progress2.first_visit("105")
    progress2.rewarded_cutscenes.add("104:0:s0")
    _, bd2 = compute_reward(
        prev, cur, make_planner(), progress=progress2, return_breakdown=True
    )
    assert bd2["new_room"] == NEW_ROOM_BONUS


def test_unique_key_blocks_door_spam():
    planner = make_planner()
    progress = ProgressTracker()
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="106", cam_id=1, hp=96)
    key = _qualify(prev, cur)
    assert key is None

    cur["cutscene_key"] = None
    _, bd0 = compute_reward(
        prev, cur, planner, progress=progress, return_breakdown=True,
    )
    assert bd0["new_cutscene"] == 0.0
    assert bd0["new_room"] == 0.0

    prev2 = make_state(room="106", cam_id=1, hp=96)
    cur2 = make_state(room="105", cam_id=0, hp=96)
    key2 = _qualify(prev2, cur2)
    assert key2 is None
    cur2["cutscene_key"] = key2
    _, bd1 = compute_reward(
        prev2, cur2, planner, progress=progress, return_breakdown=True,
    )
    assert bd1["new_cutscene"] == 0.0

    cur2b = make_state(room="105", cam_id=0, hp=96, cutscene_key="105:0:s0")
    _, bd2 = compute_reward(
        cur2, cur2b, planner, progress=progress, return_breakdown=True,
    )
    assert bd2["new_cutscene"] == NEW_CUTSCENE_BONUS

    cur3 = make_state(room="105", cam_id=0, hp=96, cutscene_key="105:0:s0")
    _, bd3 = compute_reward(
        cur2b, cur3, planner, progress=progress, return_breakdown=True,
    )
    assert bd3["new_cutscene"] == 0.0
