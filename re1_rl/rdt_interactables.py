"""RDT interactable table for spatial obs (north star B6)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "rdt_interactables.json"

# B6: typewriter / box / trigger only (skip message plaques).
OBS_INTERACTABLE_KINDS: tuple[str, ...] = ("item_box", "typewriter", "trigger")
_KIND_TO_ID = {k: (i + 1) / len(OBS_INTERACTABLE_KINDS) for i, k in enumerate(OBS_INTERACTABLE_KINDS)}


def dedupe_interactable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse RDT rows that share the same world (x, z) and kind.

    Room scripts often register multiple trigger slots at one coordinate (e.g.
    dining clock at 2900,8100). Obs only exposes nearest-N bearings — duplicates
    inflate ``interactables_here`` and waste both interactable slots on one point.
    """
    seen: set[tuple[int, int, str]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        kind = str(row.get("kind", ""))
        try:
            key = (int(row["x"]), int(row["z"]), kind)
        except (KeyError, TypeError, ValueError):
            out.append(row)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


@lru_cache(maxsize=1)
def load_rdt_interactables(path: str = str(_DEFAULT_PATH)) -> dict[str, list[dict[str, Any]]]:
    p = Path(path)
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[str, list[dict[str, Any]]] = {}
    for room_id, rows in raw.items():
        if room_id.startswith("_") or not isinstance(rows, list):
            continue
        filtered = [
            r for r in rows
            if str(r.get("kind", "")) in OBS_INTERACTABLE_KINDS
            and "x" in r and "z" in r
        ]
        if filtered:
            out[str(room_id)] = dedupe_interactable_rows(filtered)
    return out


def kind_id(kind: str) -> float:
    return float(_KIND_TO_ID.get(str(kind), 0.0))
