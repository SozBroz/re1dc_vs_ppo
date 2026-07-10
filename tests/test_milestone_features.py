"""Derived milestone obs tests."""

from __future__ import annotations

import numpy as np

from re1_rl.episode_history import EpisodeHistory
from re1_rl.milestone_features import MILESTONE_DIM, encode_milestones


def test_encode_milestones_upstairs_then_down() -> None:
    hist = EpisodeHistory()
    hist.reset("105", step=0)
    hist.room_deque.record("203", step=10)
    hist.room_deque.record("106", step=20)
    v = encode_milestones(
        current_room="106",
        episode_history=hist,
        cutscene_ledger=np.zeros(16, dtype=np.float32),
        ever_held=set(),
    )
    assert v.shape == (MILESTONE_DIM,)
    assert v[1] == 1.0  # visited_2f
    assert v[8] == 1.0  # upstairs_then_down


def test_encode_milestones_lockpick() -> None:
    hist = EpisodeHistory()
    hist.reset("105", step=0)
    v = encode_milestones(
        current_room="105",
        episode_history=hist,
        cutscene_ledger=np.zeros(16, dtype=np.float32),
        ever_held={"lockpick"},
    )
    assert v[5] == 1.0
