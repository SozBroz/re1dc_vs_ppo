"""Unit tests for static room enemy roster obs (A4)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from re1_rl.room_signature import ENEMY_ROSTER_DIM, ENEMY_ROSTER_TYPES, RoomEnemyRoster

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_roster_dim() -> None:
    assert ENEMY_ROSTER_DIM == 1 + len(ENEMY_ROSTER_TYPES)


def test_roster_loads_mansion_data() -> None:
    roster = RoomEnemyRoster(PROJECT_ROOT / "data" / "room_enemies.json")
    assert roster.loaded


def test_tea_room_zombie_count() -> None:
    roster = RoomEnemyRoster(PROJECT_ROOT / "data" / "room_enemies.json")
    v = roster.encode("104")
    assert v.shape == (ENEMY_ROSTER_DIM,)
    assert v.dtype == np.float32
    assert v[0] > 0  # total_norm
    zombie_idx = 1 + ENEMY_ROSTER_TYPES.index("zombie")
    assert v[zombie_idx] > 0


def test_unknown_room_is_zeros() -> None:
    roster = RoomEnemyRoster(PROJECT_ROOT / "data" / "room_enemies.json")
    v = roster.encode("99999")
    assert np.all(v == 0.0)
