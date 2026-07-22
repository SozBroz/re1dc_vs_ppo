"""Hand-crafted PB milestone taxonomy.

Typewriter PB: any RDT typewriter room save (shared capture gates; prologue
allowlist lifted). Key/room/story helpers remain for later ladder stages but
are disabled in ``detect_milestone_triggers`` until ``RE1_PB_V1_TYPEWRITER_ONLY=0``.
"""

from __future__ import annotations

import os
from typing import Any

from re1_rl.item_todo import canonical_item

try:
    from re1_rl.pb_champion import typewriter_milestone_id
except ImportError:

    def typewriter_milestone_id(room: str) -> str:
        return f"typewriter_save:{room}"


# Later ladder (disabled while v1 typewriter-only is on).
KEY_ITEM_MILESTONES: frozenset[str] = frozenset(
    {
        "lockpick",
        "emblem",
        "music_notes",
        "gold_emblem",
        "shield_key",
        "armor_key",
        "wind_crest",
        "sun_crest",
        "moon_crest",
        "star_crest",
    }
)

ROOM_MILESTONES: frozenset[str] = frozenset({"20E", "210"})

STORY_USE_MILESTONES: frozenset[str] = frozenset(
    {
        "music_notes@10F_piano",
        "emblem@10F_alcove",
        "gold_emblem@105_fireplace",
    }
)


def typewriter_v1_only() -> bool:
    """Default on: suppress KEY/ROOM/STORY milestones (typewriter still multi-room)."""
    raw = os.environ.get("RE1_PB_V1_TYPEWRITER_ONLY", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def milestone_id_for_new_key(name: str) -> str | None:
    item = canonical_item(str(name))
    if item not in KEY_ITEM_MILESTONES:
        return None
    return f"key:{item}"


def milestone_id_for_room(room_id: str) -> str | None:
    room = str(room_id or "").strip().upper()
    if room not in ROOM_MILESTONES:
        return None
    return f"room:{room}"


def milestone_id_for_story_use(site_id: str) -> str | None:
    site = str(site_id or "").strip()
    if site not in STORY_USE_MILESTONES:
        return None
    return f"story_use:{site}"


def is_key_item_milestone(name: str) -> bool:
    return canonical_item(str(name)) in KEY_ITEM_MILESTONES


def typewriter_save_capture_ok(
    state: dict[str, Any],
    *,
    room: str,
    kenneth_gate_breached: bool,
) -> bool:
    """Shared capture gates: still in room ``r`` and Kenneth gate not breached."""
    if kenneth_gate_breached:
        return False
    if not room:
        return False
    return str(state.get("room_id", "") or "") == str(room)


def detect_milestone_triggers(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    breakdown: dict[str, float],
    *,
    already_captured: set[str] | frozenset[str] | None = None,
    typewriter_save_complete: bool = False,
    typewriter_save_room: str | None = None,
    visited_rooms: set[str] | frozenset[str] | None = None,
    rewarded_cutscenes: set[str] | frozenset[str] | None = None,
    kenneth_gate_breached: bool = False,
) -> list[str]:
    """Return trigger ids newly earned this step.

    ``visited_rooms`` / ``rewarded_cutscenes`` retained for call-site compat;
    typewriter capture no longer gates on prologue allowlist or Kenneth cinema.
    """
    _ = visited_rooms, rewarded_cutscenes
    seen = set(already_captured or ())
    out: list[str] = []

    if typewriter_save_complete:
        room = str(typewriter_save_room or state.get("room_id", "") or "")
        if room:
            trigger = typewriter_milestone_id(room)
            if trigger not in seen and typewriter_save_capture_ok(
                state,
                room=room,
                kenneth_gate_breached=kenneth_gate_breached,
            ):
                out.append(trigger)
                seen.add(trigger)

    if typewriter_v1_only():
        return out

    if float(breakdown.get("key_item", 0.0) or 0.0) > 0.0:
        for raw in state.get("new_items") or ():
            trigger = milestone_id_for_new_key(str(raw))
            if trigger and trigger not in seen:
                out.append(trigger)
                seen.add(trigger)

    if float(breakdown.get("new_room", 0.0) or 0.0) > 0.0:
        room = str(state.get("room_id", "") or "")
        if room != str(prev_state.get("room_id", "") or ""):
            trigger = milestone_id_for_room(room)
            if trigger and trigger not in seen:
                out.append(trigger)
                seen.add(trigger)

    if float(breakdown.get("story_use", 0.0) or 0.0) > 0.0:
        site = state.get("story_use_success")
        if site:
            trigger = milestone_id_for_story_use(str(site))
            if trigger and trigger not in seen:
                out.append(trigger)
                seen.add(trigger)

    return out
