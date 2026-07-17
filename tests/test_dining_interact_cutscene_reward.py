"""Examine-text / message interact spam must not farm cutscene rewards (room 105)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES, qualify_cutscene_reward
from re1_rl.progress import ProgressTracker
from re1_rl.reward import compute_reward
from tests.test_scaffolding import make_planner, make_state


def test_dining_message_spam_no_cutscene_reward():
  """Room 105 idle scene: repeated examine skips do not pay."""
  planner = make_planner()
  progress = ProgressTracker()
  progress.first_visit("105")
  prev = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80)
  total_cutscene = 0.0
  for i in range(1, 8):
    cur = make_state(room="105", cam_id=1, hp=96, scene_flag=0x80, step=i)
    cur["cutscene_key"] = qualify_cutscene_reward(
      skip_frames=MIN_CUTSCENE_SKIP_FRAMES + 40,
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
