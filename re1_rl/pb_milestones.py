"""Hand-crafted PB milestone taxonomy.

v1 (typewriter PB): only Main Hall ink-ribbon saves with prologue room allowlist.
Key/room/story helpers remain for later ladder stages but are disabled in
``detect_milestone_triggers`` until ``RE1_PB_V1_TYPEWRITER_ONLY=0``.
"""

from __future__ import annotations

import os
from typing import Any

from re1_rl.cutscene_reward import kenneth_cutscene_seen
from re1_rl.item_todo import canonical_item
from re1_rl.typewriter_save import (
    PROLOGUE_ROOM_ALLOWLIST,
    TYPEWRITER_SAVE_MILESTONE,
    visited_rooms_allow_prologue_pb,
)

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
    """Default on: only typewriter_save:106 is active."""
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


def typewriter_save_gates_ok(
    state: dict[str, Any],
    *,
    visited_rooms: set[str] | frozenset[str] | None,
    rewarded_cutscenes: set[str] | frozenset[str] | None,
    kenneth_gate_breached: bool,
) -> bool:
    """Kenneth + room allowlist gates for the first typewriter champion."""
    if kenneth_gate_breached:
        return False
    if str(state.get("room_id", "") or "") != "106":
        return False
    if not kenneth_cutscene_seen(rewarded_cutscenes):
        return False
    if not visited_rooms_allow_prologue_pb(visited_rooms):
        return False
    # Prefer having actually walked dining + tea + hall.
    rooms = {str(r) for r in (visited_rooms or ())}
    if not PROLOGUE_ROOM_ALLOWLIST.issubset(rooms):
        return False
    return True


def detect_milestone_triggers(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    breakdown: dict[str, float],
    *,
    already_captured: set[str] | frozenset[str] | None = None,
    typewriter_save_complete: bool = False,
    visited_rooms: set[str] | frozenset[str] | None = None,
    rewarded_cutscenes: set[str] | frozenset[str] | None = None,
    kenneth_gate_breached: bool = False,
) -> list[str]:
    """Return trigger ids newly earned this step."""
    seen = set(already_captured or ())
    out: list[str] = []

    if typewriter_save_complete and TYPEWRITER_SAVE_MILESTONE not in seen:
        if typewriter_save_gates_ok(
            state,
            visited_rooms=visited_rooms,
            rewarded_cutscenes=rewarded_cutscenes,
            kenneth_gate_breached=kenneth_gate_breached,
        ):
            out.append(TYPEWRITER_SAVE_MILESTONE)
            seen.add(TYPEWRITER_SAVE_MILESTONE)

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
