"""Unit tests for ever-held key bitmask obs (A7)."""

from __future__ import annotations

import numpy as np

from re1_rl.key_items import KEY_ITEM_NAMES, KEYS_HELD_DIM, encode_keys_held


def test_keys_held_dim_matches_catalog() -> None:
    assert KEYS_HELD_DIM == len(KEY_ITEM_NAMES)
    assert KEYS_HELD_DIM >= 30


def test_empty_ever_held_is_zeros() -> None:
    v = encode_keys_held(set())
    assert v.shape == (KEYS_HELD_DIM,)
    assert np.all(v == 0.0)


def test_encodes_known_key_items() -> None:
    v = encode_keys_held({"shield_key", "emblem", "lockpick"})
    for name in ("shield_key", "emblem", "lockpick"):
        i = KEY_ITEM_NAMES.index(name)
        assert v[i] == 1.0
    assert v.sum() == 3.0


def test_canonical_alias_wooden_emblem() -> None:
    v = encode_keys_held({"wooden_emblem"})
    i = KEY_ITEM_NAMES.index("emblem")
    assert v[i] == 1.0
