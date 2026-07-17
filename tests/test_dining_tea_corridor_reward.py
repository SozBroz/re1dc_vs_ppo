"""Dining (105) ↔ tea room (104) loop must not farm cutscene rewards."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES, qualify_cutscene_reward
from re1_rl.progress import ProgressTracker
from re1_rl.reward import NEW_CUTSCENE_BONUS, NEW_ROOM_BONUS, compute_reward
from tests.test_scaffolding import make_planner, make_state


def _qualify(prev, cur, progress, *, skip_frames=MIN_CUTSCENE_SKIP_FRAMES + 40):
    return qualify_cutscene_reward(
        skip_frames=skip_frames,
        prev_state=prev,
        new_state=cur,
        episode_start_hp=96,
        rewarded_cutscenes=progress.rewarded_cutscenes,
        visited_rooms=progress.visited_rooms,
    )


def _reward_step(progress, prev, cur):
    cur = dict(cur)
    cur["cutscene_key"] = _qualify(prev, cur, progress)
    _, bd = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    return cur, bd


def test_first_dining_tea_pass_still_pays():
    progress = ProgressTracker()
    progress.first_visit("105")

    prev = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80)
    cur = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    _, bd = _reward_step(progress, prev, cur)
    assert bd["new_room"] == NEW_ROOM_BONUS
    assert bd["new_cutscene"] == 0.0

    prev2 = make_state(room="104", cam_id=0, hp=96, scene_flag=0x84)
    cur2 = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    _, bd2 = _reward_step(progress, prev2, cur2)
    assert bd2["new_cutscene"] == NEW_CUTSCENE_BONUS

    prev3 = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    cur3 = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    _, bd3 = _reward_step(progress, prev3, cur3)
    assert bd3["new_cutscene"] == 0.0


def test_dining_tea_loop_does_not_farm_cutscenes():
    """105↔104 repeat: multi-cam doors + Kenneth sN replay pay nothing."""
    progress = ProgressTracker()
    progress.first_visit("105")

    prev = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80)
    cur = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    cur, bd = _reward_step(progress, prev, cur)
    assert bd["new_cutscene"] == 0.0

    prev2 = make_state(room="104", cam_id=0, hp=96, scene_flag=0x84)
    cur2 = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    _, bd2 = _reward_step(progress, prev2, cur2)
    assert bd2["new_cutscene"] == NEW_CUTSCENE_BONUS

    prev3 = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    cur3 = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
    cur3, _ = _reward_step(progress, prev3, cur3)

    total_repeat = 0.0
    for loop in range(3):
        prev4 = make_state(room="105", cam_id=2 + loop, hp=96, scene_flag=0x80)
        cur4 = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
        _, bd4 = _reward_step(progress, prev4, cur4)
        total_repeat += bd4["new_cutscene"]
        assert bd4["new_room"] == 0.0

        prev5 = make_state(room="104", cam_id=0, hp=96, scene_flag=0x84)
        cur5 = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
        _, bd5 = _reward_step(progress, prev5, cur5)
        total_repeat += bd5["new_cutscene"]

        prev6 = make_state(room="104", cam_id=loop, hp=96, scene_flag=0x80)
        cur6 = make_state(room="105", cam_id=0, hp=96, scene_flag=0x80)
        _, bd6 = _reward_step(progress, prev6, cur6)
        total_repeat += bd6["new_cutscene"]

    assert total_repeat == 0.0
