"""Surgical ``yawn_cutscene_210``: attic intro cinema, not door-cam ``210:0:s0``.

cp119 already records generic ``210:0:s0`` on room enter. The Yawn spawn
cinema is a later same-room skip after walking forward down the corridor.
Mint ``210:yawn`` only for that long skip so the successor cell starts in
the fight.
"""

from __future__ import annotations

from typing import Any

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES

YAWN_CUTSCENE_CHECKPOINT_ID = "yawn_cutscene_210"
YAWN_CUTSCENE_ROOM = "210"
YAWN_CUTSCENE_KEY = "210:yawn"


def _on_yawn_cutscene_leg(planner: Any) -> bool:
    obj = planner.current_objective() or {}
    return str(obj.get("checkpoint_id") or "") == YAWN_CUTSCENE_CHECKPOINT_ID


def yawn_cutscene_seen(progress: Any) -> bool:
    """True once the attic Yawn intro cinema minted ``210:yawn``."""
    if progress is None:
        return False
    keys = set(getattr(progress, "observed_cutscenes", None) or ())
    keys |= set(getattr(progress, "rewarded_cutscenes", None) or ())
    keys |= set(getattr(progress, "leg_observed_cutscenes", None) or ())
    return YAWN_CUTSCENE_KEY in keys


def yawn_spawn_triggered(
    planner: Any,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    after_skip: bool = False,
) -> bool:
    """True when cp120 observes the Yawn spawn.

    Ordinary steps require an inactive→active edge. Async settlement may use
    active presence because the skip itself proves a scripted session occurred;
    this handles Yawn appearing one sample before settlement without letting
    cp119's already-populated enemy slot complete cp120 on step one.
    """
    if not _on_yawn_cutscene_leg(planner):
        return False
    from re1_rl.yawn_outcome import yawn_contact_edge

    state = new_state or {}
    return yawn_contact_edge(
        state,
        None if after_skip else prev_state,
        enemies=state.get("enemies"),
        prev_enemies=None if after_skip else (prev_state or {}).get("enemies"),
    )


def note_yawn_spawn(
    planner: Any,
    progress: Any,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any],
    *,
    after_skip: bool = False,
) -> None:
    """Mint ``210:yawn`` when the spawn cinema leaves Yawn active."""
    if progress is None or not yawn_spawn_triggered(
        planner,
        prev_state,
        new_state,
        after_skip=after_skip,
    ):
        return
    progress.observe_cutscene(YAWN_CUTSCENE_KEY)
    new_state["yawn_cutscene_confirmed"] = True


def yawn_cutscene_skip_settled(
    planner: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    skip_frames: int,
) -> bool:
    """True for a long same-room skip wholly inside attic 210 on this leg."""
    if not _on_yawn_cutscene_leg(planner):
        return False
    entry = entry_prev or {}
    if str(entry.get("room_id") or "").upper() != YAWN_CUTSCENE_ROOM:
        return False
    if str((new_state or {}).get("room_id") or "").upper() != YAWN_CUTSCENE_ROOM:
        return False
    return int(skip_frames) >= MIN_CUTSCENE_SKIP_FRAMES


def note_yawn_cutscene_skip_settle(
    planner: Any,
    progress: Any,
    entry_prev: dict[str, Any] | None,
    new_state: dict[str, Any],
    *,
    skip_frames: int,
) -> None:
    """Record ``210:yawn`` after the genuine attic intro cinema skip."""
    if progress is None:
        return
    if not yawn_cutscene_skip_settled(
        planner,
        entry_prev,
        new_state,
        skip_frames=skip_frames,
    ):
        return
    progress.observe_cutscene(YAWN_CUTSCENE_KEY)
    new_state["yawn_cutscene_confirmed"] = True
