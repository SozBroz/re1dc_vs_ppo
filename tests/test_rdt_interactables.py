"""RDT interactables spatial encoding tests."""

from __future__ import annotations

import numpy as np

from re1_rl.spatial_encoder import INTERACTABLE_SLOTS, SPATIAL_DIM, SpatialEncoder


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
