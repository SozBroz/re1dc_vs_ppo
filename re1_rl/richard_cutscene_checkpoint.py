"""Surgical cp84 (``richard_cutscene_20D``) support for Richard's cinema.

The real event is a long scripted session that starts and settles in room 20D.
Jill only reaches 204 afterward through the ordinary walkable door.  The value
at ``LAB_TIMER`` is already nonzero before the event, so it is not evidence.
"""

from __future__ import annotations

from typing import Any

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES

RICHARD_CUTSCENE_CHECKPOINT_ID = "richard_cutscene_20D"
RICHARD_PILLAR_ROOM = "20D"
# Post-cutscene scripted dump observed in memlog (pillar 20D → C passage 204).
RICHARD_SCRIPTED_EXIT_ROOM = "204"
RICHARD_CUTSCENE_KEY = "20D:richard"


def richard_scene_flag_shows_script(scene_flag: int) -> bool:
    """True for the observed Richard script family (0x91/0x93)."""
    return (int(scene_flag) & 0xF1) == 0x91


def _on_richard_cutscene_leg(planner: Any, planner_loyal_queue: Any = None) -> bool:
    obj = planner.current_objective() or {} if planner is not None else {}
    if str(obj.get("checkpoint_id") or "") == RICHARD_CUTSCENE_CHECKPOINT_ID:
        return True
    step = getattr(planner_loyal_queue, "current", None) or {}
    if not isinstance(step, dict):
        return False
    return (
        str(step.get("beat_id") or "") == "richard_bleedout"
        or str(step.get("site_id") or "") == RICHARD_CUTSCENE_KEY
    )


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


def note_richard_cutscene_room_transition(
    planner: Any,
    progress: Any,
    prev_room: str,
    room: str,
    state: dict[str, Any] | None = None,
    planner_loyal_queue: Any = None,
) -> None:
    """Confirm a 20D→204 crossing only when its skip saw the 20D script."""
    if progress is None or not _on_richard_cutscene_leg(planner, planner_loyal_queue):
        return
    if str(prev_room or "").upper() != RICHARD_PILLAR_ROOM:
        return
    if str(room or "").upper() != RICHARD_SCRIPTED_EXIT_ROOM:
        return
    snap = state or {}
    if not richard_scene_flag_shows_script(
        int(snap.get("_skip_peak_scene_flag", 0) or 0)
    ):
        return
    progress.observe_cutscene(RICHARD_CUTSCENE_KEY)
    snap["richard_cutscene_confirmed"] = True
    snap["richard_cutscene_scripted_exit"] = True


def richard_cutscene_skip_settled(
    planner: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    skip_frames: int,
    peak_scene_flag: int | None = None,
    planner_loyal_queue: Any = None,
) -> bool:
    """True only for Richard's long scripted session wholly inside 20D."""
    if not _on_richard_cutscene_leg(planner, planner_loyal_queue):
        return False
    entry = entry_prev or {}
    if str(entry.get("room_id") or "").upper() != RICHARD_PILLAR_ROOM:
        return False
    new_r = str((new_state or {}).get("room_id") or "")
    if new_r.upper() not in (RICHARD_PILLAR_ROOM, RICHARD_SCRIPTED_EXIT_ROOM):
        return False
    if int(skip_frames) < MIN_CUTSCENE_SKIP_FRAMES:
        return False
    return any(
        richard_scene_flag_shows_script(flag)
        for flag in (
            int(entry.get("scene_flag", 0) or 0),
            int((new_state or {}).get("scene_flag", 0) or 0),
            int(peak_scene_flag or 0),
        )
    )


def note_richard_cutscene_skip_settle(
    planner: Any,
    progress: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any],
    *,
    skip_frames: int,
    peak_scene_flag: int | None = None,
    planner_loyal_queue: Any = None,
) -> None:
    """Record ``20D:richard`` after the genuine same-room scripted session."""
    if progress is None:
        return
    if not richard_cutscene_skip_settled(
        planner,
        entry_prev,
        new_state,
        skip_frames=skip_frames,
        peak_scene_flag=peak_scene_flag,
        planner_loyal_queue=planner_loyal_queue,
    ):
        return
    progress.observe_cutscene(RICHARD_CUTSCENE_KEY)
    new_state["richard_cutscene_confirmed"] = True
    if str(new_state.get("room_id") or "").upper() == RICHARD_SCRIPTED_EXIT_ROOM:
        new_state["richard_cutscene_scripted_exit"] = True


def richard_cutscene_capture_room_ok(
    completed_cid: str,
    room_id: str,
    expected_room: str,
    state: dict[str, Any] | None = None,
    progress: Any = None,
) -> bool:
    """Allow 204 only after script evidence confirmed the merged skip session."""
    if completed_cid != RICHARD_CUTSCENE_CHECKPOINT_ID:
        return False
    if str(room_id or "").upper() != RICHARD_SCRIPTED_EXIT_ROOM:
        return False
    return bool(
        (state or {}).get("richard_cutscene_confirmed")
        or richard_cutscene_seen(progress)
    )


def should_suppress_wrong_room(
    planner: Any,
    prev_room: str,
    room: str,
    state: dict[str, Any] | None,
    planner_loyal_queue: Any = None,
) -> bool:
    """Suppress wrong-room only on the script-confirmed merged 20D→204 skip."""
    return bool(
        _on_richard_cutscene_leg(planner, planner_loyal_queue)
        and str(prev_room or "").upper() == RICHARD_PILLAR_ROOM
        and str(room or "").upper() == RICHARD_SCRIPTED_EXIT_ROOM
        and (state or {}).get("richard_cutscene_confirmed")
    )
