"""Surgical cp84 (``richard_cutscene_20D``) support for the Pillar Passage cutscene.

The Pillar Passage script starts in room 20D and dumps Jill into room 204 (C
Passage) with the lab countdown armed. Room ``20D→204`` is also a normal walkable
door (entry ``x=12500,z=2800,facing=3072``) — bare room-change mint was installing
walk-outs as cp84. Mint ``20D:richard`` only when post-Richard lab evidence is
visible (``lab_timer > 0`` or the STATUS countdown overlay).
"""

from __future__ import annotations

from typing import Any

from re1_rl.richard_lab import (
    richard_lab_countdown_screen_from_ram,
    richard_lab_timer_active,
)

RICHARD_CUTSCENE_CHECKPOINT_ID = "richard_cutscene_20D"
RICHARD_PILLAR_ROOM = "20D"
# Post-cutscene scripted dump observed in memlog (pillar 20D → C passage 204).
RICHARD_SCRIPTED_EXIT_ROOM = "204"
RICHARD_CUTSCENE_KEY = "20D:richard"


def _on_richard_cutscene_leg(planner: Any) -> bool:
    obj = planner.current_objective() or {}
    return str(obj.get("checkpoint_id") or "") == RICHARD_CUTSCENE_CHECKPOINT_ID


def richard_cutscene_seen(progress: Any) -> bool:
    """True once the Pillar Passage Richard beat minted ``20D:richard``.

    Generic ``20D:0:s0`` camera keys do **not** count — those fired on room
    enter and were minting cp84 before cp83 existed.
    """
    if progress is None:
        return False
    keys = set(getattr(progress, "observed_cutscenes", None) or ())
    keys |= set(getattr(progress, "rewarded_cutscenes", None) or ())
    keys |= set(getattr(progress, "leg_observed_cutscenes", None) or ())
    return RICHARD_CUTSCENE_KEY in keys


def richard_cutscene_lab_evidence(state: dict[str, Any] | None) -> bool:
    """True when RAM shows the post-Richard lab countdown is armed."""
    if not state:
        return False
    return bool(
        richard_lab_timer_active(state)
        or richard_lab_countdown_screen_from_ram(state)
    )


def note_richard_cutscene_room_transition(
    planner: Any,
    progress: Any,
    prev_room: str,
    room: str,
    state: dict[str, Any] | None = None,
) -> None:
    """Mint ``20D:richard`` only on 20D→204 with lab-countdown evidence."""
    if progress is None or not _on_richard_cutscene_leg(planner):
        return
    if str(prev_room or "").upper() != RICHARD_PILLAR_ROOM:
        return
    if str(room or "").upper() != RICHARD_SCRIPTED_EXIT_ROOM.upper():
        return
    if not richard_cutscene_lab_evidence(state):
        return
    progress.observe_cutscene(RICHARD_CUTSCENE_KEY)
    if state is not None:
        state["richard_cutscene_scripted_exit"] = True


def richard_cutscene_skip_settled(
    planner: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    skip_frames: int,
) -> bool:
    """True when a skip settle shows the real 20D→204 dump + lab evidence.

    Same-room long skips must not mint — interact/cutscene spam in 20D is not
    the Richard beat. ``skip_frames`` is kept for call-site compatibility.
    """
    del skip_frames  # duration alone is not evidence of this cinema
    if not _on_richard_cutscene_leg(planner):
        return False
    entry = entry_prev or {}
    if str(entry.get("room_id") or "").upper() != RICHARD_PILLAR_ROOM:
        return False
    new_r = str((new_state or {}).get("room_id") or "")
    if new_r.upper() != RICHARD_SCRIPTED_EXIT_ROOM.upper():
        return False
    return richard_cutscene_lab_evidence(new_state)


def note_richard_cutscene_skip_settle(
    planner: Any,
    progress: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any],
    *,
    skip_frames: int,
) -> None:
    """Record ``20D:richard`` on skip settle when lab evidence confirms the dump."""
    if progress is None:
        return
    if not richard_cutscene_skip_settled(
        planner, entry_prev, new_state, skip_frames=skip_frames
    ):
        return
    progress.observe_cutscene(RICHARD_CUTSCENE_KEY)
    new_state["richard_cutscene_scripted_exit"] = True


def richard_cutscene_capture_room_ok(
    completed_cid: str,
    room_id: str,
    expected_room: str,
    state: dict[str, Any] | None = None,
) -> bool:
    """Allow cp84 capture in scripted exit room 204 only with lab evidence."""
    if completed_cid != RICHARD_CUTSCENE_CHECKPOINT_ID:
        return False
    rid = str(room_id or "").upper()
    if rid == RICHARD_SCRIPTED_EXIT_ROOM.upper():
        return richard_cutscene_lab_evidence(state)
    exp = str(expected_room or "").upper()
    return bool(exp and rid == exp and richard_cutscene_lab_evidence(state))


def should_suppress_wrong_room(
    planner: Any,
    prev_room: str,
    room: str,
    state: dict[str, Any] | None,
) -> bool:
    """Skip terminal wrong_room only after a confirmed Richard dump mint."""
    if not _on_richard_cutscene_leg(planner):
        return False
    if str(prev_room).upper() != RICHARD_PILLAR_ROOM:
        return False
    # Walk-out 20D→204 is a real door — do not suppress without the mint flag.
    return bool((state or {}).get("richard_cutscene_scripted_exit"))
