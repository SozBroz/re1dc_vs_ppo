"""Human play harness must match fleet exploration reward gating."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES
from re1_rl.env import RE1Env
from re1_rl.progress import ProgressTracker
from re1_rl.reward import NEW_CUTSCENE_BONUS, NEW_ROOM_BONUS, compute_reward
from tests.test_scaffolding import make_planner, make_state


def test_seed_episode_progress_marks_spawn_room_visited():
    env = MagicMock(spec=RE1Env)
    env._progress = ProgressTracker()
    RE1Env._seed_episode_progress(
        env,
        make_state(room="105", hp=96),
    )
    assert env._episode_start_hp == 96
    assert "105" in env._progress.visited_rooms
    _, bd = compute_reward(
        make_state(room="105", hp=96),
        make_state(room="105", hp=96),
        make_planner(),
        progress=env._progress,
        return_breakdown=True,
    )
    assert bd["new_room"] == 0.0


def test_cutscene_reward_needs_accumulated_skip_frames():
    """Per-chunk 20f burns must not pay; 30f+ session total must."""
    planner = make_planner()
    progress = ProgressTracker()
    prev = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80)
    cur = make_state(room="105", cam_id=1, hp=96, scene_flag=0x93)

    cur20 = dict(cur)
    cur20["cutscene_key"] = None
    _, bd_short = compute_reward(prev, cur20, planner, progress=progress, return_breakdown=True)
    assert bd_short["new_cutscene"] == 0.0

    assert MIN_CUTSCENE_SKIP_FRAMES == 20
    cur30 = dict(cur)
    from re1_rl.cutscene_reward import qualify_cutscene_reward

    cur30["cutscene_key"] = qualify_cutscene_reward(
        skip_frames=30,
        prev_state=prev,
        new_state=cur30,
        episode_start_hp=96,
        rewarded_cutscenes=progress.rewarded_cutscenes,
    )
    assert cur30["cutscene_key"] == "105:1:s0"
    _, bd_ok = compute_reward(prev, cur30, planner, progress=progress, return_breakdown=True)
    assert bd_ok["new_cutscene"] == NEW_CUTSCENE_BONUS

    cur30b = dict(cur)
    cur30b["cutscene_key"] = "105:1:s0"
    _, bd_dup = compute_reward(cur30, cur30b, planner, progress=progress, return_breakdown=True)
    assert bd_dup["new_cutscene"] == 0.0


def test_new_room_once_per_episode():
    planner = make_planner()
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", hp=96)
    cur = make_state(room="104", hp=96)
    _, bd0 = compute_reward(prev, cur, planner, progress=progress, return_breakdown=True)
    assert bd0["new_room"] == NEW_ROOM_BONUS
    _, bd2 = compute_reward(cur, prev, planner, progress=progress, return_breakdown=True)
    assert bd2["new_room"] == 0.0
    _, bd3 = compute_reward(prev, cur, planner, progress=progress, return_breakdown=True)
    assert bd3["new_room"] == 0.0


def test_human_advance_combat_fields_match_env_step():
    """play_human must attach enemy_damage/kills before compute_reward."""
    from re1_rl.enemy_combat import apply_combat_step_fields
    from re1_rl.reward import ENEMY_KILL_REWARD

    planner = make_planner()
    progress = ProgressTracker()
    prev = make_state(room="104", hp=96, step=1)
    prev["enemies"] = [{"slot": 0, "hp": 48}]
    cur = make_state(room="104", hp=96, step=2)
    cur["enemies"] = []
    cur = apply_combat_step_fields(prev, cur, attack=True)
    _, bd = compute_reward(prev, cur, planner, progress=progress, return_breakdown=True)
    assert bd["enemy_kill"] == ENEMY_KILL_REWARD
    assert bd["enemy_damage"] > 0.0
