"""Episode-local rooms_visited one-hot encoding."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.obs_encoder import ROOM_VISITED_DIM, ObsEncoder
from re1_rl.room_graph import RoomGraph

ROOMS = PROJECT_ROOT / "data" / "rooms.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"


def test_rooms_visited_one_hot_matches_room_table() -> None:
    enc = ObsEncoder(ROOMS, RoomGraph(DOORS))
    v = enc.encode_rooms_visited({"105", "106"})
    assert v.shape == (ROOM_VISITED_DIM,)
    assert v.dtype == np.float32
    assert float(v.sum()) == 2.0
    assert float(v[enc.room_index["105"]]) == 1.0
    assert float(v[enc.room_index["106"]]) == 1.0
    assert float(v[enc.room_index["104"]]) == 0.0


def test_unknown_room_id_is_ignored() -> None:
    enc = ObsEncoder(ROOMS, RoomGraph(DOORS))
    v = enc.encode_rooms_visited({"999", "105"})
    assert float(v.sum()) == 1.0
    assert float(v[enc.room_index["105"]]) == 1.0
