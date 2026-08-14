"""Surgical cp02 (``barry_return_105``): Kenneth flag, then dining.

CP02 success is the tea-room Kenneth ledger (``104:*:sN``) plus a 104→105
return. Entering dining without that flag fails the episode. Timeout is the
other fail. Spray, ammo, and Barry-dialogue mints are not part of this cell.
"""

from __future__ import annotations

from typing import Any

from re1_rl.cutscene_reward import (
    MAX_SAME_ROOM_CUTSCENE_INDEX,
    TEA_ROOM,
    kenneth_cutscene_seen,
    same_room_cutscene_index,
    scene_flag_shows_script,
)

BARRY_RETURN_CHECKPOINT_ID = "barry_return_105"
BARRY_RETURN_ROOM = "105"
BARRY_RETURN_FROM_ROOM = "104"


def _on_barry_return_leg(planner: Any) -> bool:
    obj = planner.current_objective() or {}
    return str(obj.get("checkpoint_id") or "") == BARRY_RETURN_CHECKPOINT_ID


def _ledgers(progress: Any) -> set[str]:
    return set(progress.observed_cutscenes or ()) | set(
        progress.rewarded_cutscenes or ()
    )


def kenneth_skip_settled(
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    skip_frames: int,
    peak_scene_flag: int | None = None,
) -> bool:
    """True when the tea-room Kenneth script beat is visible.

    Turbo Kenneth often returns to idle ``0x80`` at both Python endpoints;
    ``0x84`` only exists mid-skip. Duration is not required when that script
    peak is present. A long 104 settle without script is not Kenneth.
    """
    del skip_frames
    if str((new_state or {}).get("room_id") or "") != TEA_ROOM:
        return False
    del entry_prev
    settle_sf = int((new_state or {}).get("scene_flag", 0) or 0)
    if scene_flag_shows_script(settle_sf):
        return True
    return scene_flag_shows_script(int(peak_scene_flag or 0))


def note_kenneth_live_scene(progress: Any, state: dict[str, Any] | None) -> str | None:
    """Write the flag as soon as room 104 shows a scripted scene bit."""
    return note_kenneth_cutscene_skip_settle(
        progress,
        None,
        state,
        skip_frames=0,
        peak_scene_flag=int((state or {}).get("scene_flag", 0) or 0),
    )


def note_kenneth_cutscene_skip_settle(
    progress: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    skip_frames: int,
    peak_scene_flag: int | None = None,
) -> str | None:
    """Write the Kenneth flag ID (``104:{settle_cam}:sN``) when the cinema plays."""
    if progress is None:
        return None
    ledgers = _ledgers(progress)
    if kenneth_cutscene_seen(ledgers):
        return None
    if not kenneth_skip_settled(
        entry_prev,
        new_state,
        skip_frames=skip_frames,
        peak_scene_flag=peak_scene_flag,
    ):
        return None
    cam = int((new_state or {}).get("cam_id", 0) or 0)
    n = same_room_cutscene_index(TEA_ROOM, cam, ledgers)
    if n >= MAX_SAME_ROOM_CUTSCENE_INDEX:
        return None
    key = f"{TEA_ROOM}:{cam}:s{n}"
    progress.observe_cutscene(key)
    print(f"[kenneth] flag {key}", flush=True)
    return key


def fail_barry_return_if_unmet(
    planner: Any,
    progress: Any,
    breakdown: dict[str, float],
    penalty: float,
    *,
    room_id: str = "",
) -> bool:
    """Dining from the tea room without the Kenneth flag kills the episode."""
    if progress is None or planner is None:
        return False
    if not _on_barry_return_leg(planner):
        return False
    if float(breakdown.get("checkpoint_success", 0.0)) > 0.0:
        return False
    if str(room_id or "") != BARRY_RETURN_ROOM:
        return False
    if not progress.leg_entered_from(BARRY_RETURN_ROOM, {BARRY_RETURN_FROM_ROOM}):
        return False
    if kenneth_cutscene_seen(_ledgers(progress)):
        return False
    if not progress.breach_capture_ineligible():
        return False
    breakdown["checkpoint_capture_ineligible"] = float(penalty)
    return True
