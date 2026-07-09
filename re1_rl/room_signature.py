"""Static per-room enemy roster from Evil Resource tables (north star guidebook)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

# Stable column order for roster vector (counts capped per type).
ENEMY_ROSTER_TYPES: tuple[str, ...] = (
    "zombie",
    "zombie_dog",
    "crow",
    "cerberus",
    "hunter",
    "black_tiger",
    "wasp",
    "snake_yawn",
    "plant42",
    "chimera",
    "tyrant",
)

ENEMY_ROSTER_DIM = 1 + len(ENEMY_ROSTER_TYPES)  # total_norm + per-type caps
_PER_TYPE_CAP = 4
_TOTAL_CAP = 8


class RoomEnemyRoster:
    """``data/room_enemies.json`` — type counts only (no spawn positions)."""

    def __init__(self, path: str | Path) -> None:
        self._counts: dict[str, dict[str, int]] = {}
        p = Path(path)
        if not p.is_file():
            return
        with p.open(encoding="utf-8") as f:
            raw = json.load(f)
        for room_id, block in raw.items():
            if room_id.startswith("_") or not isinstance(block, dict):
                continue
            tallies: dict[str, int] = {}
            for row in block.get("enemies", []):
                etype = str(row.get("enemy_type", "")).strip().lower()
                if not etype:
                    continue
                n = int(row.get("count", 1))
                tallies[etype] = tallies.get(etype, 0) + max(n, 1)
            if tallies:
                self._counts[str(room_id)] = tallies

    @property
    def loaded(self) -> bool:
        return bool(self._counts)

    def counts_for_room(self, room_id: str) -> dict[str, int]:
        return dict(self._counts.get(str(room_id), {}))

    def encode(self, room_id: str) -> np.ndarray:
        v = np.zeros(ENEMY_ROSTER_DIM, dtype=np.float32)
        tallies = self.counts_for_room(room_id)
        total = sum(tallies.values())
        v[0] = min(total, _TOTAL_CAP) / float(_TOTAL_CAP)
        for i, etype in enumerate(ENEMY_ROSTER_TYPES):
            c = tallies.get(etype, 0)
            v[1 + i] = min(c, _PER_TYPE_CAP) / float(_PER_TYPE_CAP)
        return v

    def summary(self, room_id: str) -> dict[str, Any]:
        return {"room_id": str(room_id), "counts": self.counts_for_room(room_id)}
