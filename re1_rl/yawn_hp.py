"""Yawn HP translation: raw RAM pool → wiki-scale logical HP for the NN.

Attic firewatch (2026-07-23): ``hp@0`` starts at **3050** and chips ~11/beretta
shot. Wiki / imperator: attic retreat after **120** damage (cannot die); library
fight **140**. Both encounters are room ``210``.

``logical = max(0, logical_max - (raw_full - hp_raw))`` preserves per-shot
deltas, so combat rewards stay honest while spatial ``hp/255`` stays in-band.
"""

from __future__ import annotations

from typing import Any

# Observed attic spawn / fight start (QuickSave5 firewatch).
YAWN_RAW_FULL = 3050
# Room for both Yawn fights (route_jill_anypct).
YAWN_ROOM = "210"
# Raw pools this high are Yawn (zombies/hunters/tiger/tyrant sit well below).
YAWN_RAW_MIN = 500
# Default logical max = attic retreat budget. Library (140) needs a second
# raw_full probe before we split encounters; until then 120 matches fight 1.
YAWN_LOGICAL_MAX_ATTIC = 120
YAWN_LOGICAL_MAX_LIBRARY = 140
# Use attic until we can detect fight 2 (same room, different flags).
YAWN_LOGICAL_MAX_DEFAULT = YAWN_LOGICAL_MAX_ATTIC


def is_yawn_raw_hp(hp_raw: int, *, room_id: str | None = None) -> bool:
    if room_id is not None and str(room_id).upper() != YAWN_ROOM:
        return False
    return YAWN_RAW_MIN <= int(hp_raw) <= 4000


def yawn_logical_hp(
    hp_raw: int,
    *,
    logical_max: int = YAWN_LOGICAL_MAX_DEFAULT,
    raw_full: int = YAWN_RAW_FULL,
) -> int:
    """Map raw table HP to wiki-scale remaining HP (0 .. logical_max)."""
    damage = max(0, int(raw_full) - int(hp_raw))
    return int(max(0, int(logical_max) - damage))


def apply_yawn_hp_translate(
    enemies: list[dict[str, Any]],
    *,
    room_id: str | None,
    logical_max: int = YAWN_LOGICAL_MAX_DEFAULT,
) -> list[dict[str, Any]]:
    """Rewrite Yawn ``hp`` to logical; keep ``hp_raw`` for debugging."""
    if room_id is None or str(room_id).upper() != YAWN_ROOM:
        return enemies
    out: list[dict[str, Any]] = []
    for ent in enemies:
        e = dict(ent)
        raw = int(e.get("hp", 0))
        if is_yawn_raw_hp(raw, room_id=room_id):
            e["hp_raw"] = raw
            e["hp"] = yawn_logical_hp(raw, logical_max=logical_max)
            e["yawn_translated"] = True
        out.append(e)
    return out
