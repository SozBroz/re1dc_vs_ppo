"""Detect in-game typewriter saves (ink ribbon consumption) for PB capture.

Detector (no dedicated save-UI RAM yet): ink_ribbon qty drop in any RDT
typewriter room, then control restored after the save cinema.
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


# Env macro-steps of continuous in_control after the save cinema (frame_skip≈8).
_POST_SAVE_CONTROL_STREAK = 2

# After a PB/sidecar episode start, ignore ribbon/control edges until Jill has
# been stably in control with an unchanged ink-ribbon count. Load settle can
# look like save cinema (uncontrolled → control) and inventory can flicker.
_SIDECAR_HOLDOFF_CONTROL_STREAK = 8


class TypewriterSaveDetector:
    """Latch ribbon drop → require save cinema (uncontrolled) → stable control.

    Must not fire on the ribbon-drop step itself (still mid save-UI / pre-cinema).
    Capture only after the engine seizes control for the save sequence and then
    returns Jill to playable control in the same typewriter room.

    Sidecar / PB episode starts begin in holdoff so a load settle cannot look
    like a completed save (reward + capture stay silent until holdoff clears).
    """

    def __init__(self) -> None:
        self._pending = False
        self._pending_room: str | None = None
        self._saw_uncontrolled = False
        self._control_streak = 0
        self._sidecar_holdoff = False
        self._holdoff_ribbon_baseline: int | None = None
        self._holdoff_stable_ctrl = 0
        self.armed_room: str | None = None
        self.completed_room: str | None = None
        self.last_room: str | None = None

    def reset(self) -> None:
        self._pending = False
        self._pending_room = None
        self._saw_uncontrolled = False
        self._control_streak = 0
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

    @property
    def sidecar_holdoff(self) -> bool:
        return bool(self._sidecar_holdoff)

    def _clear_pending(self) -> None:
        self._pending = False
        self._pending_room = None
        self._saw_uncontrolled = False
        self._control_streak = 0

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
        self._clear_pending()
        self.armed_room = None
        if self._holdoff_stable_ctrl >= _SIDECAR_HOLDOFF_CONTROL_STREAK:
            self._sidecar_holdoff = False

    def update(
        self,
        prev_state: dict[str, Any] | None,
        state: dict[str, Any] | None,
    ) -> bool:
        """Return True on the step a completed typewriter save should capture."""
        if state is None:
            return False
        if self._sidecar_holdoff:
            self._tick_sidecar_holdoff(state)
            return False
        room = str(state.get("room_id", "") or "")
        in_control = bool(state.get("in_control", False))

        if ink_ribbon_consumed(prev_state, state) and room in TYPEWRITER_ROOMS:
            # Ribbon drop arms the latch; never fire this same step.
            self._pending = True
            self._pending_room = room
            self.armed_room = room
            self._saw_uncontrolled = not in_control
            self._control_streak = 0
            return False

        if not self._pending:
            return False

        if room != self._pending_room:
            self._clear_pending()
            self.armed_room = None
            return False

        if not in_control:
            self._saw_uncontrolled = True
            self._control_streak = 0
            return False

        if not self._saw_uncontrolled:
            # Still waiting for the save cinema / UI to take control.
            return False

        self._control_streak += 1
        if self._control_streak < _POST_SAVE_CONTROL_STREAK:
            return False

        fired = self._pending_room or room
        self._clear_pending()
        self.armed_room = fired
        self.completed_room = fired
        self.last_room = fired
        return True
