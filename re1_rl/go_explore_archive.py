"""Go-Explore lite archive keyed by (room_id, tile_bin).

Cells partition each room into coarse tiles for archive diversity. BizHawk
``.State`` persistence is optional — callers supply ``save_state`` when wired.

JSON schema (v1):
  {
    "version": 1,
    "cells": {
      "105:3,1": {
        "room_id": "105",
        "tile_bin": [3, 1],
        "score": 1.5,
        "visit_count": 2,
        "state_path": null,
        "meta": {}
      }
    }
  }
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

ARCHIVE_VERSION = 1
DEFAULT_TILE_SPAN = 2048  # ~16 bins across a 4096-unit room axis


def tile_bin(x: int, z: int, *, tile_span: int = DEFAULT_TILE_SPAN) -> tuple[int, int]:
    """Coarse allocentric tile indices inside a room."""
    span = max(1, int(tile_span))
    return (int(x) // span, int(z) // span)


def cell_key(room_id: str, tb: tuple[int, int]) -> str:
    return f"{room_id}:{tb[0]},{tb[1]}"


def parse_cell_key(key: str) -> tuple[str, tuple[int, int]]:
    room, rest = key.split(":", 1)
    tx_s, tz_s = rest.split(",", 1)
    return room, (int(tx_s), int(tz_s))


@dataclass
class ArchiveCell:
    room_id: str
    tile_bin: tuple[int, int]
    score: float = 0.0
    visit_count: int = 0
    state_path: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return cell_key(self.room_id, self.tile_bin)

    def to_json(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "tile_bin": list(self.tile_bin),
            "score": self.score,
            "visit_count": self.visit_count,
            "state_path": self.state_path,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ArchiveCell:
        tb = data.get("tile_bin", [0, 0])
        return cls(
            room_id=str(data["room_id"]),
            tile_bin=(int(tb[0]), int(tb[1])),
            score=float(data.get("score", 0.0)),
            visit_count=int(data.get("visit_count", 0)),
            state_path=data.get("state_path"),
            meta=dict(data.get("meta") or {}),
        )


SaveStateCallback = Callable[[Path, ArchiveCell], str | None]


def _noop_save_state(_archive_path: Path, cell: ArchiveCell) -> None:
    """Stub: archive bookkeeping only; no BizHawk .State written."""
    _ = cell


class GoExploreArchive:
    """In-memory Go-Explore cell store with JSON persistence."""

    def __init__(
        self,
        path: Path | str,
        *,
        tile_span: int = DEFAULT_TILE_SPAN,
        save_state: SaveStateCallback | None = None,
    ) -> None:
        self.path = Path(path)
        self.tile_span = tile_span
        self._save_state = save_state or _noop_save_state
        self.cells: dict[str, ArchiveCell] = {}

    def load(self) -> None:
        if not self.path.is_file():
            self.cells = {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if int(raw.get("version", 0)) != ARCHIVE_VERSION:
            raise ValueError(
                f"unsupported archive version {raw.get('version')!r}; "
                f"expected {ARCHIVE_VERSION}"
            )
        self.cells = {
            key: ArchiveCell.from_json(val)
            for key, val in (raw.get("cells") or {}).items()
        }

    def save(self) -> None:
        payload = {
            "version": ARCHIVE_VERSION,
            "cells": {k: c.to_json() for k, c in sorted(self.cells.items())},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def cell_from_pose(self, room_id: str, x: int, z: int) -> ArchiveCell:
        tb = tile_bin(x, z, tile_span=self.tile_span)
        key = cell_key(room_id, tb)
        cell = self.cells.get(key)
        if cell is None:
            cell = ArchiveCell(room_id=str(room_id), tile_bin=tb)
            self.cells[key] = cell
        return cell

    def record_visit(
        self,
        room_id: str,
        x: int,
        z: int,
        *,
        score: float,
        meta: dict[str, Any] | None = None,
    ) -> ArchiveCell:
        cell = self.cell_from_pose(room_id, x, z)
        cell.visit_count += 1
        if score > cell.score:
            cell.score = float(score)
        if meta:
            cell.meta.update(meta)
        state_path = self._save_state(self.path, cell)
        if state_path:
            cell.state_path = state_path
        return cell

    def select_frontier(
        self,
        *,
        room_ids: Iterable[str] | None = None,
        k: int = 1,
        rng: random.Random | None = None,
    ) -> list[ArchiveCell]:
        """Pick under-visited cells (lowest visit_count, tie-break score)."""
        rng = rng or random.Random()
        pool = list(self.cells.values())
        if room_ids is not None:
            allowed = {str(r) for r in room_ids}
            pool = [c for c in pool if c.room_id in allowed]
        if not pool:
            return []
        pool.sort(key=lambda c: (c.visit_count, -c.score, c.key))
        if k >= len(pool):
            rng.shuffle(pool)
            return pool
        head = pool[: max(k * 4, k)]
        rng.shuffle(head)
        return head[:k]

    def stats(self) -> dict[str, Any]:
        rooms = {c.room_id for c in self.cells.values()}
        return {
            "cell_count": len(self.cells),
            "room_count": len(rooms),
            "rooms": sorted(rooms),
            "with_state": sum(1 for c in self.cells.values() if c.state_path),
        }
