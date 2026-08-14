"""Surgical cp02 (``barry_return_105``) fail path after Kenneth.

CP02 success is the existing cutscene ledger: tea-room Kenneth
(``104:*:sN`` via ``kenneth_cutscene_seen``), plus spray and exact-15
beretta. A 104→105 door bounce must not complete or fail the cell.
"""

from __future__ import annotations

from typing import Any

from re1_rl.cutscene_reward import kenneth_cutscene_seen

BARRY_RETURN_CHECKPOINT_ID = "barry_return_105"
BARRY_RETURN_ROOM = "105"
BARRY_RETURN_FROM_ROOM = "104"


def _on_barry_return_leg(planner: Any) -> bool:
    obj = planner.current_objective() or {}
    return str(obj.get("checkpoint_id") or "") == BARRY_RETURN_CHECKPOINT_ID


def fail_barry_return_if_unmet(
    planner: Any,
    progress: Any,
    breakdown: dict[str, float],
    penalty: float,
    *,
    room_id: str = "",
) -> bool:
    """If Kenneth played and dining return cannot capture, fail the episode."""
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
    ledgers = set(progress.observed_cutscenes or ()) | set(
        progress.rewarded_cutscenes or ()
    )
    if not kenneth_cutscene_seen(ledgers):
        return False
    if not progress.breach_capture_ineligible():
        return False
    breakdown["checkpoint_capture_ineligible"] = float(penalty)
    return True
