"""Locked-door interact spam must not farm cutscene exploration rewards."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES, qualify_cutscene_reward
from re1_rl.progress import ProgressTracker
from re1_rl.reward import NEW_CUTSCENE_BONUS, compute_reward
from tests.test_scaffolding import make_planner, make_state


def test_short_locked_door_spam_no_cutscene_reward():
    planner = make_planner()
    progress = ProgressTracker()
    progress.first_visit("107")
    prev = make_state(room="107", cam_id=2, hp=96, scene_flag=0x80)
    total_cutscene = 0.0
    for i in range(1, 6):
        cur = make_state(room="107", cam_id=2, hp=96, scene_flag=0x80, step=i)
        cur["cutscene_key"] = qualify_cutscene_reward(
            skip_frames=MIN_CUTSCENE_SKIP_FRAMES - 1,
            prev_state=prev,
            new_state=cur,
            episode_start_hp=96,
            rewarded_cutscenes=progress.rewarded_cutscenes,
        )
        _, bd = compute_reward(
            prev, cur, planner, progress=progress, return_breakdown=True
        )
        total_cutscene += bd["new_cutscene"]
        prev = cur
    assert total_cutscene == 0.0
