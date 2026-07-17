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


def test_dining_same_cam_sn_farm_blocked_after_corridor_known():
    """After corridor known, dining ``:s1+`` at non-Barry cams must not farm."""
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.first_visit("104")
    progress.rewarded_cutscenes.add("104:0:s0")
    progress.rewarded_cutscenes.add("105:2:s0")

    prev = make_state(room="105", cam_id=2, hp=96, scene_flag=0x93)
    cur = make_state(room="105", cam_id=2, hp=96, scene_flag=0x80)
    cur["cutscene_key"] = _qualify(prev, cur, progress)
    assert cur["cutscene_key"] is None
    _, bd = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    assert bd["new_cutscene"] == 0.0


def test_async_post_skip_door_crossing_no_cutscene():
    """Training post-skip sync: 105→104 pays new_room only (harness parity)."""
    from unittest.mock import MagicMock

    from re1_rl.env import RE1Env
    from re1_rl.reward import NEW_CUTSCENE_BONUS, NEW_ROOM_BONUS

    env = RE1Env.__new__(RE1Env)
    env._progress = ProgressTracker()
    env._progress.first_visit("105")
    env._planner = make_planner()
    env.graph = None
    env._stage = {"success_room": None}
    env._episode_start_hp = 96
    env._episode_min_hp = 96
    env._post_skip_reward = 0.0
    env._post_skip_bd = {}
    env._last_skip_frames = 80
    env._inventory_before_skip = None
    env._pending_skip_room_crossings = []
    env._cutscene_skip_entry_prev = make_state(
        room="105", cam_id=2, hp=96, scene_flag=0x80
    )
    env._prev_state = dict(env._cutscene_skip_entry_prev)
    env._read_state = MagicMock(
        return_value=make_state(room="104", cam_id=0, hp=96, scene_flag=0x80)
    )
    env._apply_post_skip_sync()
    assert env._post_skip_bd.get("new_room") == NEW_ROOM_BONUS
    assert env._post_skip_bd.get("new_cutscene", 0.0) == 0.0
    assert env._post_skip_bd.get("new_cutscene", 0.0) != NEW_CUTSCENE_BONUS
