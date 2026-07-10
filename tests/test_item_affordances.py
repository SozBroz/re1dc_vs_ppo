"""Item affordances obs tests."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from re1_rl.item_affordances import (
    AFFORDANCES_DIM,
    AFFORDANCE_SLOTS,
    encode_affordances,
    load_affordances,
)


def test_load_affordances_has_shield_key() -> None:
    data = load_affordances()
    assert "shield_key" in data
    assert "105" in data["shield_key"]["rooms"]


def test_encode_affordances_marks_current_room() -> None:
    room_index = {"105": 3, "10D": 8}
    v = encode_affordances(
        ever_held={"shield_key", "emblem"},
        inventory_slots=[("shield_key", 1)],
        current_room="105",
        room_index=room_index,
    )
    assert v.shape == (AFFORDANCES_DIM,)
    assert v.dtype == np.float32
    # At least one held key affordant in dining room 105.
    affordant_bits = [v[i * 5 + 2] for i in range(2)]
    assert max(affordant_bits) == 1.0
    inv_bits = [v[i * 5 + 4] for i in range(2)]
    assert max(inv_bits) == 1.0


def test_affordances_json_schema(tmp_path: Path) -> None:
    path = tmp_path / "item_affordances.json"
    path.write_text(
        json.dumps(
            {
                "lockpick": {
                    "rooms": ["104", "102"],
                    "door_edges": [],
                    "notes": "test",
                }
            }
        ),
        encoding="utf-8",
    )
    load_affordances.cache_clear()
    v = encode_affordances(
        ever_held={"lockpick"},
        inventory_slots=None,
        current_room="104",
        room_index={"104": 1, "102": 2},
    )
    load_affordances.cache_clear()
    assert v[2] == 1.0
    assert AFFORDANCE_SLOTS == 8
