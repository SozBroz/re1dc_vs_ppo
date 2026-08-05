"""Named-state packing: verified fields only, safe defaults when absent."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.memory_map import IN_CONTROL_MASK, INTERACTION_PROMPT, SCENE_FLAG_MASK
from re1_rl.named_state import NAMED_STATE_DIM, NAMED_STATE_FIELDS, encode_named_state


def test_named_state_dim_and_no_interaction_prompt() -> None:
    assert NAMED_STATE_DIM == 64
    assert NAMED_STATE_DIM == len(NAMED_STATE_FIELDS)
    names = {n for n, _ in NAMED_STATE_FIELDS}
    assert "interaction_prompt" not in names
    assert INTERACTION_PROMPT is None


def test_encode_named_state_defaults_when_absent() -> None:
    v = encode_named_state(None)
    assert v.shape == (NAMED_STATE_DIM,)
    assert np.all(v == 0.0)
    v2 = encode_named_state({})
    assert np.all(v2 == 0.0)
    # Poison channel forced off while RAM is untrusted.
    v3 = encode_named_state({"poisoned": True})
    assert v3[-1] == 0.0
    assert np.all(v3 == 0.0)


def test_encode_named_state_verified_bits() -> None:
    state = {
        "door_flags": 0b101,
        "game_mode": IN_CONTROL_MASK | 0x01,
        "scene_flag": SCENE_FLAG_MASK,
        "msg_flag": 0x80,
        "game_timer": 4294967295,
        "lab_timer": 65535,
        "gallery_progress": 3,
        "gallery_confirm": 128,
        "poisoned": True,
        # Must be ignored — not a verified named_state channel.
        "interaction_prompt": True,
    }
    v = encode_named_state(state)
    assert v[0] == 1.0 and v[2] == 1.0 and v[1] == 0.0
    assert v[32] == 1.0  # game_mode bit0
    assert v[32 + 7] == 1.0  # in-control bit in mode byte
    off = 32 + 8 + 8 + 8
    assert v[off] == 1.0  # in_control derived
    assert v[off + 1] == 1.0  # scene_scripted
    assert v[off + 2] == 1.0  # game_timer
    assert v[off + 3] == 1.0  # lab_timer
    assert abs(v[off + 4] - 0.5) < 1e-5
    assert abs(v[off + 5] - (128 / 255.0)) < 1e-5
    assert v[off + 6] == 0.0  # dining_statue_knocked
    assert v[off + 7] == 0.0  # poisoned disabled
    # No extra channel for interaction_prompt (8 trailing scalars).
    assert v.shape[0] == off + 8
    assert v.shape[0] == NAMED_STATE_DIM
