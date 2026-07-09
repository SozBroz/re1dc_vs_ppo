"""Death detection helpers (neck-grab / hunter scene / game-over skip)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.memory_map import player_died


def test_player_died_requires_prior_positive_hp():
    assert not player_died(0, prev_hp=0, episode_start_hp=0)
    assert not player_died(96, prev_hp=96, episode_start_hp=96)
    assert player_died(0, prev_hp=96, episode_start_hp=96)
    assert player_died(0, prev_hp=0, episode_start_hp=96)


def test_player_died_negative_hp_treated_as_dead():
    assert player_died(-1, prev_hp=48, episode_start_hp=48)
