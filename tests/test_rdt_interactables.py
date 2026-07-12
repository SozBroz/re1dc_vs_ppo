"""RDT interactables spatial encoding tests."""

from __future__ import annotations

import numpy as np

from re1_rl.rdt_interactables import dedupe_interactable_rows, load_rdt_interactables
from re1_rl.spatial_encoder import INTERACTABLE_SLOTS, SPATIAL_DIM, SpatialEncoder


def test_dedupe_interactable_rows_collapses_clock_triggers() -> None:
    rows = [
        {"x": 2900, "z": 8100, "kind": "trigger"},
        {"x": 2900, "z": 8100, "kind": "trigger"},
        {"x": 2900, "z": 8100, "kind": "trigger"},
        {"x": 12500, "z": 3300, "kind": "message"},
    ]
    out = dedupe_interactable_rows(rows)
    assert len(out) == 2
    assert out[0]["kind"] == "trigger"
    assert out[1]["kind"] == "message"


def test_load_rdt_interactables_dedupes_room_105_triggers() -> None:
    load_rdt_interactables.cache_clear()
    rows = load_rdt_interactables()["105"]
    triggers = [r for r in rows if r["kind"] == "trigger"]
    assert len(triggers) == 1
    assert int(triggers[0]["x"]) == 2900
    assert int(triggers[0]["z"]) == 8100


def test_spatial_encodes_nearest_interactable() -> None:
    enc = SpatialEncoder(
        interactables={
            "100": [
                {"x": 7200.0, "z": 2800.0, "kind": "item_box"},
                {"x": 3100.0, "z": 9750.0, "kind": "typewriter"},
            ],
        },
    )
    state = {
        "room_id": "100",
        "x": 7200,
        "z": 2800,
        "facing": 0,
        "enemies": [],
    }
    v = enc.encode(state)
    assert v.shape == (SPATIAL_DIM,)
    # interactables_here at index after items+enemies+exits
    # 1 + 8*8 + 1 + 5*8 + 1 + 4*3 = 119
    assert v[119] == min(2, 8) / 8.0
    # nearest slot is item_box at player position
    assert v[119 + 1 + 3] > 0.0  # kind_id for item_box


def test_spatial_room_105_one_trigger_not_four() -> None:
    enc = SpatialEncoder()
    state = {
        "room_id": "105",
        "x": 15000,
        "z": 8100,
        "facing": 0,
        "enemies": [],
    }
    v = enc.encode(state)
    assert v[119] == 1 / 8.0  # one deduped trigger, not 4/8
