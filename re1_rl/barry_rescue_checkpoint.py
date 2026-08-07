"""Surgical cp25 (``barry_rescue_115``) support for the cross-room Barry trap cutscene.

The rescue script starts in room 115, often uses message-box dialogue (which
disqualifies generic ``cutscene_key`` qualification), and can dump Jill into
room 109. Only this checkpoint id is special-cased — all other CP logic stays
on the generic observed_cutscene + wrong_room paths.
"""

from __future__ import annotations

from typing import Any

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES
from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import ITEM_IDS

BARRY_RESCUE_CHECKPOINT_ID = "barry_rescue_115"
BARRY_TRAP_ROOM = "115"
# Post-rescue scripted dump observed in memlog (trap 115 → winding 109).
BARRY_RESCUE_SCRIPTED_EXIT_ROOM = "109"
BARRY_RESCUE_CUTSCENE_KEY = "115:barry_rescue"


def _on_barry_rescue_leg(planner: Any) -> bool:
    obj = planner.current_objective() or {}
    return str(obj.get("checkpoint_id") or "") == BARRY_RESCUE_CHECKPOINT_ID


def _inventory_item_names(src: dict[str, Any] | None) -> set[str]:
    names: set[str] = set()
    for x in (src or {}).get("inventory", []) or []:
        if isinstance(x, tuple) and x:
            iid = int(x[0])
            if iid:
                names.add(canonical_item(ITEM_IDS.get(iid, str(iid))))
        elif x:
            names.add(canonical_item(str(x)))
    return names


def _entry_had_shotgun(
    entry: dict[str, Any] | None,
    new_state: dict[str, Any] | None = None,
) -> bool:
    for src in (entry, new_state):
        if src and "shotgun" in _inventory_item_names(src):
            return True
    return False


def barry_rescue_skip_settled(
    planner: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    skip_frames: int,
) -> bool:
    """True when a skip settle should mint the Barry rescue ledger key."""
    if not _on_barry_rescue_leg(planner):
        return False
    entry = entry_prev or {}
    if str(entry.get("room_id") or "") != BARRY_TRAP_ROOM:
        return False
    if not _entry_had_shotgun(entry, new_state):
        return False
    prev_r = str(entry.get("room_id") or "")
    new_r = str((new_state or {}).get("room_id") or "")
    if new_r and new_r != prev_r:
        return new_r == BARRY_RESCUE_SCRIPTED_EXIT_ROOM
    return int(skip_frames) >= int(MIN_CUTSCENE_SKIP_FRAMES)


def note_barry_rescue_skip_settle(
    planner: Any,
    progress: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any],
    *,
    skip_frames: int,
) -> None:
    """Record ``115:barry_rescue`` on skip settle; flag scripted 115→109 exit."""
    if progress is None:
        return
    if not barry_rescue_skip_settled(
        planner, entry_prev, new_state, skip_frames=skip_frames
    ):
        return
    progress.observe_cutscene(BARRY_RESCUE_CUTSCENE_KEY)
    if str((entry_prev or {}).get("room_id") or "") == BARRY_TRAP_ROOM:
        new_r = str(new_state.get("room_id") or "")
        if new_r == BARRY_RESCUE_SCRIPTED_EXIT_ROOM:
            new_state["barry_rescue_scripted_exit"] = True


def barry_rescue_capture_room_ok(
    completed_cid: str,
    room_id: str,
    expected_room: str,
) -> bool:
    """Allow cp25 capture in scripted exit room 109 (trap room is 115)."""
    if completed_cid != BARRY_RESCUE_CHECKPOINT_ID:
        return False
    rid = str(room_id or "").upper()
    if rid == BARRY_RESCUE_SCRIPTED_EXIT_ROOM.upper():
        return True
    exp = str(expected_room or "").upper()
    return bool(exp and rid == exp)


def should_suppress_wrong_room(
    planner: Any,
    prev_room: str,
    room: str,
    state: dict[str, Any] | None,
) -> bool:
    """Skip terminal wrong_room for the known Barry rescue 115→109 teleport."""
    if not _on_barry_rescue_leg(planner):
        return False
    if str(prev_room) != BARRY_TRAP_ROOM:
        return False
    if str(room) == BARRY_RESCUE_SCRIPTED_EXIT_ROOM:
        return True
    return bool((state or {}).get("barry_rescue_scripted_exit"))
