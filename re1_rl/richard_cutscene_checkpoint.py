"""Surgical cp84 (``richard_cutscene_20D``) support for the cross-room Richard cutscene.

The Pillar Passage script starts in room 20D and dumps Jill into room 204 (C
Passage). Only this checkpoint id is special-cased — same pattern as Barry
``barry_rescue_checkpoint`` (trap 115 → exit 109).
"""

from __future__ import annotations

from typing import Any

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES

RICHARD_CUTSCENE_CHECKPOINT_ID = "richard_cutscene_20D"
RICHARD_PILLAR_ROOM = "20D"
# Post-cutscene scripted dump observed in memlog (pillar 20D → C passage 204).
RICHARD_SCRIPTED_EXIT_ROOM = "204"
RICHARD_CUTSCENE_KEY = "20D:richard"


def _on_richard_cutscene_leg(planner: Any) -> bool:
    obj = planner.current_objective() or {}
    return str(obj.get("checkpoint_id") or "") == RICHARD_CUTSCENE_CHECKPOINT_ID


def richard_cutscene_skip_settled(
    planner: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    skip_frames: int,
) -> bool:
    """True when a skip settle should mint the Richard cutscene ledger key."""
    if not _on_richard_cutscene_leg(planner):
        return False
    entry = entry_prev or {}
    if str(entry.get("room_id") or "").upper() != RICHARD_PILLAR_ROOM:
        return False
    prev_r = str(entry.get("room_id") or "")
    new_r = str((new_state or {}).get("room_id") or "")
    if new_r and new_r != prev_r:
        return new_r.upper() == RICHARD_SCRIPTED_EXIT_ROOM.upper()
    return int(skip_frames) >= int(MIN_CUTSCENE_SKIP_FRAMES)


def note_richard_cutscene_skip_settle(
    planner: Any,
    progress: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any],
    *,
    skip_frames: int,
) -> None:
    """Record ``20D:richard`` on skip settle; flag scripted 20D→204 exit."""
    if progress is None:
        return
    if not richard_cutscene_skip_settled(
        planner, entry_prev, new_state, skip_frames=skip_frames
    ):
        return
    progress.observe_cutscene(RICHARD_CUTSCENE_KEY)
    if str((entry_prev or {}).get("room_id") or "").upper() == RICHARD_PILLAR_ROOM:
        new_r = str(new_state.get("room_id") or "")
        if new_r.upper() == RICHARD_SCRIPTED_EXIT_ROOM.upper():
            new_state["richard_cutscene_scripted_exit"] = True


def richard_cutscene_capture_room_ok(
    completed_cid: str,
    room_id: str,
    expected_room: str,
) -> bool:
    """Allow cp84 capture in scripted exit room 204 (pillar room is 20D)."""
    if completed_cid != RICHARD_CUTSCENE_CHECKPOINT_ID:
        return False
    rid = str(room_id or "").upper()
    if rid == RICHARD_SCRIPTED_EXIT_ROOM.upper():
        return True
    exp = str(expected_room or "").upper()
    return bool(exp and rid == exp)


def should_suppress_wrong_room(
    planner: Any,
    prev_room: str,
    room: str,
    state: dict[str, Any] | None,
) -> bool:
    """Skip terminal wrong_room for the known Richard cutscene 20D→204 teleport."""
    if not _on_richard_cutscene_leg(planner):
        return False
    if str(prev_room).upper() != RICHARD_PILLAR_ROOM:
        return False
    if str(room).upper() == RICHARD_SCRIPTED_EXIT_ROOM.upper():
        return True
    return bool((state or {}).get("richard_cutscene_scripted_exit"))
