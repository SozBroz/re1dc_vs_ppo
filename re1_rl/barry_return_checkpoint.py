"""Surgical cp02 (``barry_return_105``): Kenneth, clips, heal spray, then dining.

CP02 success is 104→105 after the tea-room Kenneth ledger (``104:*:sN``)
while still holding the Jill start heal spray and both Kenneth-body clips
(30 spare ``handgun_bullets``). Those piles despawn once the Wesker/Barry
hall cinema plays. Entering dining from the tea room before Kenneth, without
the spray, or without both clips, fails the episode.
"""

from __future__ import annotations

from typing import Any

from re1_rl.item_todo import canonical_item
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
BARRY_RETURN_HEAL_SPRAY = "first_aid_spray_alt"
TEA_ROOM_CLIP_ITEM = "handgun_bullets"
TEA_ROOM_CLIP_QTY = 30
TEA_ROOM_CLIP_BEATS = frozenset({"kenneth_104", "barry_return_105"})
MAIN_HALL_BEFORE_TEA_CLIPS = "main_hall_before_tea_clips"
BARRY_RETURN_BEFORE_TEA_CLIPS = "barry_return_before_tea_clips"


def heal_spray_in_inventory(state: dict[str, Any] | None) -> bool:
    """True when Jill still holds the cp01 start heal spray (id 0x41)."""
    inv = {
        canonical_item(str(x))
        for x in (state or {}).get("inventory", []) or []
        if str(x).strip()
    }
    return BARRY_RETURN_HEAL_SPRAY in inv


def tea_room_clips_in_inventory(state: dict[str, Any] | None) -> bool:
    """True when both Kenneth-body clips are held (30 spare handgun rounds)."""
    totals: dict[str, int] = {}
    for entry in (state or {}).get("inventory_slots") or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("item")
            qty = int(entry.get("qty", 0) or 0)
        elif isinstance(entry, (list, tuple)) and entry:
            name = entry[0]
            qty = int(entry[1]) if len(entry) > 1 else 0
        else:
            continue
        if not name:
            continue
        key = canonical_item(str(name))
        totals[key] = totals.get(key, 0) + max(qty, 0)
    return int(totals.get(TEA_ROOM_CLIP_ITEM, 0) or 0) >= TEA_ROOM_CLIP_QTY


def barry_return_capture_inventory_ok(state: dict[str, Any] | None) -> bool:
    return heal_spray_in_inventory(state) and tea_room_clips_in_inventory(state)


def _on_barry_return_leg(planner: Any) -> bool:
    obj = planner.current_objective() or {}
    return str(obj.get("checkpoint_id") or "") == BARRY_RETURN_CHECKPOINT_ID


def _ledgers(progress: Any) -> set[str]:
    return set(progress.observed_cutscenes or ()) | set(
        progress.rewarded_cutscenes or ()
    )


def _leg_kenneth_seen(progress: Any) -> bool:
    return kenneth_cutscene_seen(set(progress.leg_observed_cutscenes or ()))


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
    # Cam 0 is the tea-room door. A door skip can peak 0x84 without the
    # Kenneth cinema; that used to write 104:0:s0 and unlock 104→105.
    if int((new_state or {}).get("cam_id", 0) or 0) == 0:
        return False
    del entry_prev
    settle_sf = int((new_state or {}).get("scene_flag", 0) or 0)
    if scene_flag_shows_script(settle_sf):
        return True
    return scene_flag_shows_script(int(peak_scene_flag or 0))


def note_kenneth_live_scene(progress: Any, state: dict[str, Any] | None) -> str | None:
    """Write the flag as soon as room 104 shows a scripted scene bit.

    C-RE1 turbo skip settles idle ``0x80``; the 0x84 peak only exists mid-skip
    and is stamped on ``_skip_peak_scene_flag``.
    """
    peak = (state or {}).get("_skip_peak_scene_flag")
    if peak is None:
        peak = int((state or {}).get("scene_flag", 0) or 0)
    return note_kenneth_cutscene_skip_settle(
        progress,
        None,
        state,
        skip_frames=0,
        peak_scene_flag=int(peak or 0),
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
    if not kenneth_skip_settled(
        entry_prev,
        new_state,
        skip_frames=skip_frames,
        peak_scene_flag=peak_scene_flag,
    ):
        return None
    ledgers = _ledgers(progress)
    cam = int((new_state or {}).get("cam_id", 0) or 0)
    leg_key = f"{TEA_ROOM}:{cam}:s0"
    progress.note_leg_cutscene(leg_key)
    if kenneth_cutscene_seen(ledgers):
        return None
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
    state: dict[str, Any] | None = None,
) -> bool:
    """Dining from the tea room without Kenneth, clips, or spray kills the episode."""
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
    if _leg_kenneth_seen(progress):
        should_fail = not barry_return_capture_inventory_ok(state)
    else:
        should_fail = True
    if not should_fail:
        return False
    if not progress.breach_capture_ineligible():
        return False
    breakdown["checkpoint_capture_ineligible"] = float(penalty)
    return True
