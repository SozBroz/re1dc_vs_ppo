"""Cutscene reward policy: one 450-frame duration gate plus explicit exclusions."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.cutscene_reward import (
    ILLEGAL_MAIN_HALL_FAILURE_REASON,
    MIN_CUTSCENE_SKIP_FRAMES,
    cutscene_disqualify_reason,
    format_cutscene_gate_panel,
    illegal_main_hall_before_kenneth_transition,
    kenneth_cutscene_seen,
    qualify_cutscene_reward,
)
from re1_rl.memory_map import (
    OPENING_NARRATION_GAME_MODE,
    PAUSE_MENU_GAME_MODE,
)
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    DEATH_PENALTY,
    MAIN_HALL_BEFORE_KENNETH_PENALTY,
    NEW_CUTSCENE_BONUS,
    compute_reward,
)
from tests.test_scaffolding import make_planner, make_state

AFTER_KENNETH = frozenset({"104:0:s0"})


def _qualify(
    prev,
    cur,
    *,
    skip_frames: int = MIN_CUTSCENE_SKIP_FRAMES,
    start_hp: int = 96,
    rewarded_cutscenes=None,
    blocked_room: str | None = None,
):
    return qualify_cutscene_reward(
        skip_frames=skip_frames,
        prev_state=prev,
        new_state=cur,
        episode_start_hp=start_hp,
        rewarded_cutscenes=rewarded_cutscenes,
        cutscene_blocked_after_pickup_room=blocked_room,
    )


def test_cutscene_duration_threshold_is_450_frames() -> None:
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="105", cam_id=0, hp=96)
    assert MIN_CUTSCENE_SKIP_FRAMES == 450
    assert _qualify(prev, cur, skip_frames=449) is None
    assert _qualify(prev, cur, skip_frames=450) == "105:0:s0"
    assert (
        cutscene_disqualify_reason(
            skip_frames=449,
            prev_state=prev,
            new_state=cur,
            episode_start_hp=96,
        )
        == "skip_frames=449 < 450"
    )


def test_long_idle_freeze_pays_without_scene_or_peak_evidence() -> None:
    prev = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0)
    cur = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80, msg_flag=0)
    assert _qualify(prev, cur, skip_frames=450) == "105:2:s0"


def test_short_examine_and_short_door_do_not_pay() -> None:
    examine_prev = make_state(room="107", cam_id=2, hp=96)
    examine_cur = make_state(room="107", cam_id=2, hp=96)
    assert _qualify(examine_prev, examine_cur, skip_frames=120) is None

    door_prev = make_state(room="105", cam_id=2, hp=96)
    door_cur = make_state(room="104", cam_id=0, hp=96)
    assert _qualify(door_prev, door_cur, skip_frames=239) is None


def test_long_door_load_can_pay_by_duration() -> None:
    prev = make_state(room="105", cam_id=2, hp=96)
    cur = make_state(room="104", cam_id=0, hp=96)
    assert _qualify(prev, cur, skip_frames=450) == "105:2"


def test_kenneth_long_idle_freeze_pays_without_peak_latch() -> None:
    prev = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0)
    cur = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0)
    key = _qualify(prev, cur, skip_frames=450, rewarded_cutscenes={"105:0:s0"})
    assert key == "104:0:s0"
    assert kenneth_cutscene_seen({key})


def test_pause_menu_is_excluded() -> None:
    prev = make_state(
        room="105",
        cam_id=1,
        hp=96,
        game_mode=PAUSE_MENU_GAME_MODE,
        game_state=0x40808104,
    )
    cur = make_state(room="105", cam_id=1, hp=96)
    assert _qualify(prev, cur, skip_frames=600) is None


def test_successful_story_use_may_escape_menu_exclusion() -> None:
    prev = make_state(
        room="10F",
        cam_id=1,
        hp=96,
        game_mode=PAUSE_MENU_GAME_MODE,
        game_state=0x40808104,
    )
    cur = make_state(room="10F", cam_id=1, hp=96)
    cur["story_use_success"] = "music_notes@10F_piano"
    assert _qualify(prev, cur, skip_frames=450, rewarded_cutscenes=AFTER_KENNETH) == "10F:1:s0"


def test_pickup_and_post_pickup_room_are_excluded() -> None:
    prev = make_state(
        room="105", cam_id=2, hp=96, inventory=["knife", "beretta"]
    )
    acquired = make_state(
        room="105", cam_id=2, hp=96, inventory=["knife", "beretta", "emblem"]
    )
    assert _qualify(prev, acquired, skip_frames=600) is None

    settled = make_state(
        room="105", cam_id=2, hp=96, inventory=["knife", "beretta", "emblem"]
    )
    assert _qualify(acquired, settled, skip_frames=600, blocked_room="105") is None
    assert _qualify(acquired, settled, skip_frames=600, blocked_room=None) == "105:2:s0"


def test_death_and_opening_are_excluded() -> None:
    dead_prev = make_state(room="104", cam_id=2, hp=0, scene_flag=0x90)
    dead_cur = make_state(room="104", cam_id=2, hp=0, scene_flag=0x80)
    assert _qualify(dead_prev, dead_cur, skip_frames=600) is None

    opening_prev = make_state(
        room="100",
        cam_id=0,
        hp=0,
        game_mode=OPENING_NARRATION_GAME_MODE,
        game_state=0,
    )
    opening_cur = dict(opening_prev)
    assert _qualify(opening_prev, opening_cur, skip_frames=600, start_hp=0) is None


def test_pre_kenneth_hall_is_excluded_but_post_kenneth_hall_pays() -> None:
    prev = make_state(room="106", cam_id=1, hp=96)
    cur = make_state(room="106", cam_id=1, hp=96)
    assert _qualify(prev, cur, skip_frames=600, rewarded_cutscenes=set()) is None
    assert (
        _qualify(
            prev,
            cur,
            skip_frames=600,
            rewarded_cutscenes=AFTER_KENNETH,
        )
        == "106:1:s0"
    )


def test_same_camera_key_cap_and_reward_deduplication() -> None:
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="105", cam_id=0, hp=96)
    assert _qualify(prev, cur, rewarded_cutscenes=set()) == "105:0:s0"
    assert _qualify(prev, cur, rewarded_cutscenes={"105:0:s0"}) == "105:0:s1"
    assert _qualify(
        prev, cur, rewarded_cutscenes={"105:0:s0", "105:0:s1"}
    ) is None

    progress = ProgressTracker()
    cur["cutscene_key"] = "105:0:s0"
    _, first = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    _, duplicate = compute_reward(
        cur, cur, make_planner(), progress=progress, return_breakdown=True
    )
    assert first["new_cutscene"] == NEW_CUTSCENE_BONUS
    assert duplicate["new_cutscene"] == 0.0


def test_illegal_main_hall_gate_irreversibly_disables_positive_rewards() -> None:
    assert illegal_main_hall_before_kenneth_transition(
        "105", "106", rewarded_cutscenes=set()
    )
    assert not illegal_main_hall_before_kenneth_transition(
        "105", "106", rewarded_cutscenes=AFTER_KENNETH
    )
    assert ILLEGAL_MAIN_HALL_FAILURE_REASON == "main_hall_before_kenneth"

    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="106", cam_id=1, hp=96)
    _, bd = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    assert bd[ILLEGAL_MAIN_HALL_FAILURE_REASON] == -0.05
    assert bd["new_room"] == 0.0
    assert "106" not in progress.visited_rooms
    assert progress.kenneth_gate_breached

    # The penalty is one-shot, but the positive-reward poison is permanent.
    _, repeated = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    assert repeated[ILLEGAL_MAIN_HALL_FAILURE_REASON] == 0.0
    progress.rewarded_cutscenes.add("104:0:s0")
    _, legal = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    assert legal["new_room"] == 0.0

    kenneth = make_state(room="104", cam_id=0, hp=96)
    kenneth["cutscene_key"] = "104:0:s0"
    progress.rewarded_cutscenes.clear()
    _, poisoned = compute_reward(
        cur, kenneth, make_planner(), progress=progress, return_breakdown=True
    )
    assert poisoned["new_cutscene"] == 0.0
    assert "104:0:s0" not in progress.rewarded_cutscenes
    assert all(value <= 0.0 for value in poisoned.values())


def test_cutscene_panel_explains_terminal_kenneth_gate() -> None:
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="105", cam_id=0, hp=96)
    panel = format_cutscene_gate_panel(
        skip_frames=600,
        prev_state=prev,
        new_state=cur,
        positive_rewards_disabled=True,
        qualified_key="105:0:s0",
        breakdown={"new_cutscene": 0.0},
    )
    assert "unpaid_reason: terminal Kenneth gate breach" in panel


def test_real_death_owns_penalty_not_kenneth_gate() -> None:
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", cam_id=0, hp=96)
    cur = make_state(room="106", cam_id=1, hp=0, dead=True)
    _, bd = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    assert bd["death"] == DEATH_PENALTY
    assert bd[ILLEGAL_MAIN_HALL_FAILURE_REASON] == 0.0
    assert MAIN_HALL_BEFORE_KENNETH_PENALTY == -0.05
