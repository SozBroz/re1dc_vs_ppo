"""Item affordances obs tests — Evil Resource use/unlock encoding."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from re1_rl.item_affordances import (
    AFFORDANCES_DIM,
    AFFORDANCE_SLOTS,
    KEY_HINTS_DIM,
    KEY_HINTS_PER_KEY,
    encode_affordances,
    encode_key_hints,
    load_affordances,
)
from re1_rl.key_items import KEY_ITEM_NAMES, KEYS_HELD_DIM


def test_load_shield_key_is_attic_door_not_pickup_bag() -> None:
    data = load_affordances()
    sk = data["shield_key"]
    assert sk["use_rooms"] == ["20E"]
    assert sk["door_edges"][0]["from_room"] == "20E"
    assert sk["door_edges"][0]["to_room"] == "210"
    # Pickup dining room must not be a use site.
    assert "105" not in sk["use_rooms"]
    assert "105" in sk.get("pickup_rooms", [])


def test_lockpick_has_doors_and_desk_use_rooms() -> None:
    data = load_affordances()
    lp = data["lockpick"]
    assert "102" in lp["use_rooms"]
    edge_pairs = {(e["from_room"], e["to_room"]) for e in lp["door_edges"]}
    assert ("104", "10F") in edge_pairs  # Tea Room → Bar
    assert ("107", "108") in edge_pairs  # Art Room → L Passage


def test_encode_shield_key_affordant_at_attic_entry() -> None:
    room_index = {"20E": 5, "210": 9, "105": 3}
    load_affordances.cache_clear()
    v = encode_affordances(
        ever_held={"shield_key"},
        inventory_slots=[("shield_key", 1)],
        current_room="20E",
        room_index=room_index,
    )
    assert v.shape == (AFFORDANCES_DIM,)
    assert v.dtype == np.float32
    # slot 0: primary USE = 20E, affordant=1, unlock/hint = 210, in inv
    assert abs(v[1] - 5 / 128.0) < 1e-6
    assert v[2] == 1.0
    assert abs(v[3] - 9 / 128.0) < 1e-6
    assert v[4] == 1.0


def test_encode_shield_key_not_affordant_in_dining_pickup_room() -> None:
    room_index = {"20E": 5, "210": 9, "105": 3}
    load_affordances.cache_clear()
    v = encode_affordances(
        ever_held={"shield_key"},
        inventory_slots=[("shield_key", 1)],
        current_room="105",
        room_index=room_index,
    )
    assert v[2] == 0.0  # dining is pickup, not use
    assert abs(v[1] - 5 / 128.0) < 1e-6  # primary still attic entry


def test_encode_lockpick_affordant_at_tea_room_door() -> None:
    room_index = {"104": 4, "10F": 7, "102": 2}
    load_affordances.cache_clear()
    v = encode_affordances(
        ever_held={"lockpick"},
        inventory_slots=[("lockpick", 1)],
        current_room="104",
        room_index=room_index,
    )
    assert v[2] == 1.0
    assert abs(v[3] - 7 / 128.0) < 1e-6  # unlocks bar


def test_encode_gold_emblem_use_dining_fireplace() -> None:
    room_index = {"105": 3, "10F": 7}
    load_affordances.cache_clear()
    v = encode_affordances(
        ever_held={"gold_emblem"},
        inventory_slots=None,
        current_room="105",
        room_index=room_index,
    )
    assert abs(v[1] - 3 / 128.0) < 1e-6
    assert v[2] == 1.0


def test_affordances_json_schema_override(tmp_path: Path) -> None:
    path = tmp_path / "item_affordances.json"
    path.write_text(
        json.dumps(
            {
                "lockpick": {
                    "use_rooms": ["102"],
                    "door_edges": [
                        {
                            "from_room": "104",
                            "to_room": "10F",
                            "door_id": "104->10F",
                        }
                    ],
                    "pickup_rooms": ["106"],
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
        room_index={"104": 1, "10F": 2, "102": 3},
        affordances_path=str(path),
    )
    load_affordances.cache_clear()
    assert v[2] == 1.0
    assert abs(v[3] - 2 / 128.0) < 1e-6
    assert AFFORDANCE_SLOTS == 8
    assert AFFORDANCES_DIM == 40


def test_key_hints_dim_matches_key_catalog() -> None:
    assert KEY_HINTS_PER_KEY == 3
    assert KEY_HINTS_DIM == KEYS_HELD_DIM * 3 == 105


def test_encode_key_hints_shield_pickup_pending() -> None:
    load_affordances.cache_clear()
    v = encode_key_hints(
        ever_held=set(),
        inventory_names=set(),
        current_room="105",
    )
    assert v.shape == (KEY_HINTS_DIM,)
    i = KEY_ITEM_NAMES.index("shield_key")
    assert v[i * 3] == 1.0  # pickup pending in dining
    assert v[i * 3 + 1] == 0.0
    assert v[i * 3 + 2] == 0.0


def test_encode_key_hints_shield_use_pending_and_affordant() -> None:
    load_affordances.cache_clear()
    v = encode_key_hints(
        ever_held={"shield_key"},
        inventory_names={"shield_key"},
        current_room="20E",
    )
    i = KEY_ITEM_NAMES.index("shield_key")
    assert v[i * 3] == 0.0
    assert v[i * 3 + 1] == 1.0
    assert v[i * 3 + 2] == 1.0
