"""Cutscene ledger obs tests."""

from __future__ import annotations

import numpy as np

from re1_rl.cutscene_ledger import (
    CUTSCENE_LEDGER_DIM,
    CUTSCENE_MILESTONE_KEYS,
    CUTSCENE_WESKER_PRE_KENNETH_KEY,
    encode_cutscene_ledger,
)
from re1_rl.env import RE1Env
from re1_rl.progress import ProgressTracker


def test_cutscene_ledger_dim_matches_keys() -> None:
    assert CUTSCENE_LEDGER_DIM == len(CUTSCENE_MILESTONE_KEYS)
    assert CUTSCENE_LEDGER_DIM == 16


def test_encode_cutscene_ledger_sets_seen_bits() -> None:
    seen = {"105:2", "106:1", "not_a_milestone"}
    v = encode_cutscene_ledger(seen)
    assert v.shape == (CUTSCENE_LEDGER_DIM,)
    idx_105_2 = CUTSCENE_MILESTONE_KEYS.index("105:2")
    idx_106_1 = CUTSCENE_MILESTONE_KEYS.index("106:1")
    assert v[idx_105_2] == 1.0
    assert v[idx_106_1] == 1.0
    assert float(v.sum()) == 2.0


def test_encode_cutscene_ledger_matches_same_room_sequence_keys() -> None:
    seen = {"105:2:s0", "105:2:s1"}
    v = encode_cutscene_ledger(seen)
    idx_105_2 = CUTSCENE_MILESTONE_KEYS.index("105:2")
    assert v[idx_105_2] == 1.0
    assert float(v.sum()) == 1.0


def test_encode_cutscene_ledger_marks_pre_kenneth_wesker_without_reward() -> None:
    v = encode_cutscene_ledger(set(), wesker_pre_kenneth=True)
    idx = CUTSCENE_MILESTONE_KEYS.index(CUTSCENE_WESKER_PRE_KENNETH_KEY)
    assert v[idx] == 1.0
    assert float(v.sum()) == 1.0


def test_wesker_mark_terminates_instead_of_truncating() -> None:
    env = RE1Env.__new__(RE1Env)
    env._progress = ProgressTracker()
    env._progress.breach_kenneth_gate()
    env._stage = {"max_steps": 1}
    env._step_count = 1

    terminated, truncated, reason = env._termination_flags({"dead": False})

    assert terminated
    assert not truncated
    assert reason == "main_hall_before_kenneth"
