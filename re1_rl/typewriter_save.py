"""Detect in-game typewriter saves (ink ribbon consumption) for PB capture.

Detector (no dedicated save-UI RAM yet): ink_ribbon qty drop in any RDT
typewriter room fires capture on that step. Champion replace / score gates
still decide whether the sidecar is kept.
"""

from __future__ import annotations

import math
from typing import Any

from re1_rl.item_todo import canonical_item

MAIN_HALL_ROOM = "106"
TYPEWRITER_SAVE_MILESTONE = "typewriter_save:106"

# RDT interactable for Main Hall typewriter (data/rdt_interactables.json).
TYPEWRITER_106_XZ: tuple[float, float] = (14000.0, 17000.0)
TYPEWRITER_PROXIMITY = 4000.0  # world units; generous for fixed-cam approach

# First PB only: visited rooms must be a subset of prologue path.
# Kept for helpers / legacy callers; capture gates no longer use this.
PROLOGUE_ROOM_ALLOWLIST: frozenset[str] = frozenset({"105", "104", "106"})

_FALLBACK_TYPEWRITER_ROOMS: frozenset[str] = frozenset(
    {"100", "106", "118", "307", "30E", "403", "50E", "600", "606", "618"}
)


def _load_typewriter_rooms() -> frozenset[str]:
    try:
        from re1_rl.rdt_interactables import load_rdt_interactables

        table = load_rdt_interactables()
        rooms = {
            str(room_id)
            for room_id, rows in table.items()
            if any(str(r.get("kind", "")) == "typewriter" for r in (rows or ()))
        }
        if rooms:
            return frozenset(rooms)
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return _FALLBACK_TYPEWRITER_ROOMS


TYPEWRITER_ROOMS: frozenset[str] = _load_typewriter_rooms()


def typewriter_rooms() -> frozenset[str]:
    """Room ids that contain an RDT typewriter (fallback if table missing)."""
    return TYPEWRITER_ROOMS


def _slots(state: dict[str, Any] | None) -> list[tuple[str, int]]:
    if not state:
        return []
    raw = state.get("inventory_slots")
    if raw is None:
        # Fallback: name-only list (qty unknown → treat as 1 for named ribbons).
        return [(canonical_item(str(n)), 1) for n in (state.get("inventory") or []) if n]
    out: list[tuple[str, int]] = []
    for entry in raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            out.append((canonical_item(str(entry[0])), int(entry[1])))
        elif isinstance(entry, dict):
            out.append(
                (
                    canonical_item(str(entry.get("name") or entry.get("item") or "")),
                    int(entry.get("qty", 1) or 0),
                )
            )
    return out


def count_ink_ribbons(state: dict[str, Any] | None) -> int:
    total = 0
    for name, qty in _slots(state):
        if name == "ink_ribbon":
            total += max(0, int(qty))
    return total


def near_main_hall_typewriter(state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "") or "") != MAIN_HALL_ROOM:
        return False
    try:
        x = float(state.get("x", 0))
        z = float(state.get("z", 0))
    except (TypeError, ValueError):
        return False
    tx, tz = TYPEWRITER_106_XZ
    return math.hypot(x - tx, z - tz) <= TYPEWRITER_PROXIMITY


def ink_ribbon_consumed(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> bool:
    return count_ink_ribbons(prev_state) > count_ink_ribbons(state)


def visited_rooms_allow_prologue_pb(visited: set[str] | frozenset[str] | None) -> bool:
    """True when every visited room is in dining / tea / main hall only."""
    rooms = {str(r) for r in (visited or ())}
    if not rooms:
        return False
    return rooms.issubset(PROLOGUE_ROOM_ALLOWLIST)


def typewriter_save_cutscene_disqualified(
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
) -> bool:
    """True when this settle looks like a typewriter save (ribbon drop in any TW room)."""
    room = str((new_state or {}).get("room_id", "") or "") or str(
        (prev_state or {}).get("room_id", "") or ""
    )
    if room not in TYPEWRITER_ROOMS:
        return False
    return ink_ribbon_consumed(prev_state, new_state)


# After a PB/sidecar episode start, ignore ribbon edges until Jill has been
# stably in control with an unchanged ink-ribbon count. Load settle can flicker
# inventory and must not look like a fresh typewriter save.
_SIDECAR_HOLDOFF_CONTROL_STREAK = 8


class TypewriterSaveDetector:
    """Fire on ink-ribbon drop in a typewriter room (same step).

    Champion score / ``try_replace_champion`` still decide whether the captured
    sidecar replaces the machine-local slot. Sidecar episode starts begin in
    holdoff so load settle cannot look like a completed save.
    """

    def __init__(self) -> None:
        self._sidecar_holdoff = False
        self._holdoff_ribbon_baseline: int | None = None
        self._holdoff_stable_ctrl = 0
        self.armed_room: str | None = None
        self.completed_room: str | None = None
        self.last_room: str | None = None

    def reset(self) -> None:
        self._sidecar_holdoff = False
        self._holdoff_ribbon_baseline = None
        self._holdoff_stable_ctrl = 0
        self.armed_room = None
        self.completed_room = None
        self.last_room = None

    def begin_episode(
        self,
        *,
        from_sidecar: bool,
        state: dict[str, Any] | None = None,
    ) -> None:
        """Reset detector; arm sidecar holdoff when the episode loaded a PB."""
        self.reset()
        if not from_sidecar:
            return
        self._sidecar_holdoff = True
        self._holdoff_ribbon_baseline = count_ink_ribbons(state)
        self._holdoff_stable_ctrl = 0
        from re1_rl.typewriter_save_log import log_typewriter_save, state_fields

        log_typewriter_save(
            "holdoff_begin",
            baseline_ribbons=self._holdoff_ribbon_baseline,
            **state_fields(state),
        )

    @property
    def sidecar_holdoff(self) -> bool:
        return bool(self._sidecar_holdoff)

    def _tick_sidecar_holdoff(self, state: dict[str, Any]) -> None:
        ribbons = count_ink_ribbons(state)
        if self._holdoff_ribbon_baseline is None:
            self._holdoff_ribbon_baseline = ribbons
        in_control = bool(state.get("in_control", False))
        if in_control and ribbons == self._holdoff_ribbon_baseline:
            self._holdoff_stable_ctrl += 1
        else:
            self._holdoff_stable_ctrl = 0
            if in_control:
                self._holdoff_ribbon_baseline = ribbons
        self.armed_room = None
        if self._holdoff_stable_ctrl >= _SIDECAR_HOLDOFF_CONTROL_STREAK:
            from re1_rl.typewriter_save_log import log_typewriter_save

            log_typewriter_save(
                "holdoff_clear",
                baseline_ribbons=self._holdoff_ribbon_baseline,
            )
            self._sidecar_holdoff = False

    def update(
        self,
        prev_state: dict[str, Any] | None,
        state: dict[str, Any] | None,
    ) -> bool:
        """Return True on the step that should trigger typewriter PB capture."""
        if state is None:
            return False
        if self._sidecar_holdoff:
            self._tick_sidecar_holdoff(state)
            return False
        room = str(state.get("room_id", "") or "")
        if not (ink_ribbon_consumed(prev_state, state) and room in TYPEWRITER_ROOMS):
            return False

        from re1_rl.typewriter_save_log import log_typewriter_save, state_fields

        ribbons_before = count_ink_ribbons(prev_state)
        ribbons_after = count_ink_ribbons(state)
        log_typewriter_save(
            "armed",
            room=room,
            ribbons_before=ribbons_before,
            ribbons_after=ribbons_after,
            **state_fields(state),
        )
        log_typewriter_save(
            "complete",
            room=room,
            reason="ribbon_drop",
            ribbons_before=ribbons_before,
            ribbons_after=ribbons_after,
            **state_fields(state),
        )
        self.armed_room = room
        self.completed_room = room
        self.last_room = room
        return True
