"""Dining/tea corridor follows the same four-second cutscene duration rule."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES, qualify_cutscene_reward
from re1_rl.progress import ProgressTracker
from re1_rl.reward import NEW_CUTSCENE_BONUS, NEW_ROOM_BONUS, compute_reward
from tests.test_scaffolding import make_planner, make_state


def _reward_crossing(skip_frames: int) -> dict[str, float]:
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", cam_id=2, hp=96)
    cur = make_state(room="104", cam_id=0, hp=96)
    cur["cutscene_key"] = qualify_cutscene_reward(
        skip_frames=skip_frames,
        prev_state=prev,
        new_state=cur,
        episode_start_hp=96,
        rewarded_cutscenes=progress.rewarded_cutscenes,
    )
    _, bd = compute_reward(
        prev, cur, make_planner(), progress=progress, return_breakdown=True
    )
    return bd


def test_short_dining_tea_crossing_pays_room_only() -> None:
    bd = _reward_crossing(MIN_CUTSCENE_SKIP_FRAMES - 1)
    assert bd["new_room"] == NEW_ROOM_BONUS
    assert bd["new_cutscene"] == 0.0


def test_long_dining_tea_crossing_may_pay_both_channels() -> None:
    bd = _reward_crossing(MIN_CUTSCENE_SKIP_FRAMES)
    assert bd["new_room"] == NEW_ROOM_BONUS
    assert bd["new_cutscene"] == NEW_CUTSCENE_BONUS


def test_long_tea_idle_freeze_unlocks_kenneth_without_peak_evidence() -> None:
    prev = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0)
    cur = make_state(room="104", cam_id=0, hp=96, scene_flag=0x80, msg_flag=0)
    assert (
        qualify_cutscene_reward(
            skip_frames=MIN_CUTSCENE_SKIP_FRAMES,
            prev_state=prev,
            new_state=cur,
            episode_start_hp=96,
            rewarded_cutscenes={"105:2"},
        )
        == "104:0:s0"
    )
