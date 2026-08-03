"""Progress events that justify a Go-Explore cell capture.

Policy (room-first archive):
  - At least one cell per reached room (``coverage:<room>``).
  - One cell per ``(room, milestone_digest)`` bucket; a new bucket captures on
    room entry or in-room progress (key item, weapon, cutscene, …).
  - Revisiting the same bucket may replace the incumbent when quality improves.

Never capture on ordinary movement alone.
"""

from __future__ import annotations

from typing import Any

from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS
from re1_rl.pb_milestones import (
    KEY_ITEM_MILESTONES,
    milestone_id_for_new_key,
    milestone_id_for_story_use,
)

_WEAPON_NAMES: frozenset[str] = frozenset(
    ITEM_IDS[i] for i in WEAPON_ITEM_IDS if i in ITEM_IDS
)


def detect_go_explore_progress_events(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    breakdown: dict[str, float],
    *,
    already: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Return progress trigger ids newly earned this reward step."""
    seen = set(already or ())
    out: list[str] = []

    def _add(trigger: str) -> None:
        if trigger and trigger not in seen:
            out.append(trigger)
            seen.add(trigger)

    if float(breakdown.get("new_room", 0.0) or 0.0) > 0.0:
        room = str(state.get("room_id", "") or "").strip().upper()
        prev = str(prev_state.get("room_id", "") or "").strip().upper()
        if room and room != prev:
            _add(f"room:{room}")

    if float(breakdown.get("key_item", 0.0) or 0.0) > 0.0:
        for raw in state.get("new_items") or ():
            trigger = milestone_id_for_new_key(str(raw))
            if trigger:
                _add(trigger)
            else:
                item = canonical_item(str(raw))
                if item in KEY_ITEM_MILESTONES:
                    _add(f"key:{item}")

    if float(breakdown.get("new_weapon", 0.0) or 0.0) > 0.0:
        for raw in state.get("new_items") or ():
            name = canonical_item(str(raw))
            if name in _WEAPON_NAMES:
                _add(f"weapon:{name}")

    if float(breakdown.get("cutscene", 0.0) or 0.0) > 0.0:
        key = str(state.get("cutscene_key") or "").strip()
        if key:
            _add(f"cutscene:{key}")

    if float(breakdown.get("story_use", 0.0) or 0.0) > 0.0:
        site = state.get("story_use_success")
        if site:
            trigger = milestone_id_for_story_use(str(site))
            _add(trigger or f"story_use:{site}")
        else:
            _add("story_use:unknown")

    if float(breakdown.get("dining_statue", 0.0) or 0.0) > 0.0:
        _add("dining_statue")

    if float(breakdown.get("gallery", 0.0) or 0.0) > 0.0:
        _add("gallery:step")

    if float(breakdown.get("document_examine", 0.0) or 0.0) > 0.0:
        room = str(state.get("room_id", "") or "").strip().upper()
        if room:
            _add(f"document:{room}")

    return out


def coverage_reason(room: str) -> str:
    """Archive has no cell for ``room`` yet — admit on next eligible step."""
    return f"coverage:{str(room).strip().upper()}"


def bucket_new_reason(room: str, digest: str) -> str:
    """Archive has no cell for this ``(room, milestone_digest)`` bucket yet."""
    room_u = str(room).strip().upper()
    return f"bucket_new:{room_u}:{digest}"


def quality_improve_reason(cell_key: str) -> str:
    return f"quality_improve:{cell_key}"
