"""Cutscene ledger obs tests."""

from __future__ import annotations

import numpy as np

from re1_rl.cutscene_ledger import (
    CUTSCENE_LEDGER_DIM,
    CUTSCENE_MILESTONE_KEYS,
    encode_cutscene_ledger,
)


def test_cutscene_ledger_dim_matches_keys() -> None:
    assert CUTSCENE_LEDGER_DIM == len(CUTSCENE_MILESTONE_KEYS)


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
