"""Room-exit segment chaining for planner-loyal hop scores.

When ``RE1_PLANNER_ROOM_SEGMENTS=1``, consecutive PLs in a segment share one
episode and one hard timeout. Each hop still settles its own ``S_hop``; the
episode learning target is ``S_room = sum(S_hop)``. Best ``S_room`` per segment
is recorded under ``data/planner_loyal_room_segment_best.json``.

Default (flag off): unchanged one-PL = one-episode live hop score.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from re1_rl.planner_hop_score import PLANNER_DEFAULT_TIMEOUT_FRAMES

_SEGMENTS_REL = Path("data/planner_loyal_room_segments.json")
_BEST_REL = Path("data/planner_loyal_room_segment_best.json")
_cached: dict[str, Any] | None = None
_cached_mtime: float | None = None


def room_segments_enabled() -> bool:
    raw = str(os.environ.get("RE1_PLANNER_ROOM_SEGMENTS", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def room_segments_path(project_root: Path | str | None = None) -> Path:
    root = Path(project_root) if project_root is not None else Path.cwd()
    return root / _SEGMENTS_REL


def best_scores_path(project_root: Path | str | None = None) -> Path:
    root = Path(project_root) if project_root is not None else Path.cwd()
    return root / _BEST_REL


def load_room_segments(
    project_root: Path | str | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Load and cache the segment table."""
    global _cached, _cached_mtime
    path = room_segments_path(project_root)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {"schema_version": 1, "segments": [], "budget_frames_per_hop": int(PLANNER_DEFAULT_TIMEOUT_FRAMES)}
    if (
        not force
        and _cached is not None
        and _cached_mtime is not None
        and mtime == _cached_mtime
    ):
        return _cached
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {"segments": []}
    _cached = data
    _cached_mtime = mtime
    return data


def _slot_for_completed_step(
    completed_step_index: int,
    steps: list[dict[str, Any]] | None = None,
) -> int:
    """Map chunk completed index → plNN slot (opening remint = pl01..pl06)."""
    from re1_rl.planner_loyal_cells import slot_index_for_completed_step

    return int(
        slot_index_for_completed_step(int(completed_step_index), steps=steps)
    )


def _prototype_only_default() -> bool:
    # Default ON: only chain dining+L hallway until the prototype is proven.
    raw = str(
        os.environ.get("RE1_PLANNER_ROOM_SEGMENTS_PROTOTYPE_ONLY", "1") or ""
    ).strip().lower()
    return raw in {"1", "true", "yes", "on", ""}


def segment_covering_slot(
    pl_slot: int,
    project_root: Path | str | None = None,
    *,
    prototype_only: bool = False,
) -> dict[str, Any] | None:
    """Return the segment whose [pl_first, pl_last] contains ``pl_slot``."""
    data = load_room_segments(project_root)
    hits: list[dict[str, Any]] = []
    for raw in data.get("segments") or []:
        if not isinstance(raw, dict):
            continue
        if bool(raw.get("pl_by_pl")):
            continue
        if prototype_only and not bool(raw.get("prototype")):
            continue
        lo = int(raw.get("pl_first", -1))
        hi = int(raw.get("pl_last", -1))
        if lo <= int(pl_slot) <= hi:
            hits.append(dict(raw))
    if not hits:
        return None
    # Prefer prototype, then longest span.
    hits.sort(
        key=lambda s: (
            1 if s.get("prototype") else 0,
            int(s.get("pl_last", 0)) - int(s.get("pl_first", 0)),
        ),
        reverse=True,
    )
    return hits[0]


def segment_for_completed_step(
    completed_step_index: int,
    project_root: Path | str | None = None,
    *,
    steps: list[dict[str, Any]] | None = None,
    prototype_only: bool | None = None,
) -> dict[str, Any] | None:
    if not room_segments_enabled():
        return None
    if prototype_only is None:
        prototype_only = _prototype_only_default()
    slot = _slot_for_completed_step(completed_step_index, steps=steps)
    return segment_covering_slot(
        slot, project_root, prototype_only=bool(prototype_only)
    )


def segment_for_tip_slot(
    tip_pl_slot: int,
    project_root: Path | str | None = None,
    *,
    prototype_only: bool | None = None,
) -> dict[str, Any] | None:
    """Segment covering tip ``plNN`` or the next mint slot ``plNN+1``."""
    if not room_segments_enabled():
        return None
    if prototype_only is None:
        prototype_only = _prototype_only_default()
    # Tip plK seeks the hop that mints plK+1 (K>=6). Cover tip and tip+1.
    for slot in (int(tip_pl_slot), int(tip_pl_slot) + 1):
        seg = segment_covering_slot(
            slot, project_root, prototype_only=bool(prototype_only)
        )
        if seg is not None:
            return seg
    return None


def is_segment_exit(
    completed_step_index: int,
    project_root: Path | str | None = None,
    *,
    steps: list[dict[str, Any]] | None = None,
) -> bool:
    seg = segment_for_completed_step(
        completed_step_index, project_root, steps=steps
    )
    if seg is None:
        return True
    slot = _slot_for_completed_step(completed_step_index, steps=steps)
    return int(slot) >= int(seg.get("pl_last", slot))


def is_segment_midhop(
    completed_step_index: int,
    project_root: Path | str | None = None,
    *,
    steps: list[dict[str, Any]] | None = None,
) -> bool:
    """True when this hop success should continue the episode inside a segment."""
    if not room_segments_enabled():
        return False
    seg = segment_for_completed_step(
        completed_step_index, project_root, steps=steps
    )
    if seg is None:
        return False
    slot = _slot_for_completed_step(completed_step_index, steps=steps)
    return int(slot) < int(seg.get("pl_last", slot))


def segment_hop_count(seg: dict[str, Any]) -> int:
    return max(1, int(seg.get("pl_last", 0)) - int(seg.get("pl_first", 0)) + 1)


def segment_budget_frames(
    seg: dict[str, Any] | None,
    project_root: Path | str | None = None,
) -> int:
    data = load_room_segments(project_root)
    per = int(
        data.get("budget_frames_per_hop") or PLANNER_DEFAULT_TIMEOUT_FRAMES
    )
    if seg is None:
        return int(PLANNER_DEFAULT_TIMEOUT_FRAMES)
    return int(per) * segment_hop_count(seg)


@dataclass
class RoomSegmentTracker:
    """Episode-local additive room score + shared budget bookkeeping."""

    segment_id: str = ""
    pl_first: int = -1
    pl_last: int = -1
    budget_frames: int = PLANNER_DEFAULT_TIMEOUT_FRAMES
    hop_scores: list[float] = field(default_factory=list)
    hop_reports: list[dict[str, Any]] = field(default_factory=list)
    best_s_room: float | None = None

    @classmethod
    def begin(
        cls,
        seg: dict[str, Any],
        *,
        project_root: Path | str | None = None,
    ) -> "RoomSegmentTracker":
        budget = segment_budget_frames(seg, project_root)
        return cls(
            segment_id=str(seg.get("id") or ""),
            pl_first=int(seg.get("pl_first", -1)),
            pl_last=int(seg.get("pl_last", -1)),
            budget_frames=int(budget),
        )

    @property
    def s_room(self) -> float:
        return float(sum(self.hop_scores))

    def note_hop_success(self, report: dict[str, Any] | None, s: float) -> float:
        self.hop_scores.append(float(s))
        if isinstance(report, dict):
            self.hop_reports.append(dict(report))
        return self.s_room

    def note_hop_failure(self, s: float) -> float:
        """Failure ends the segment; S_room is failure score (not partial sum)."""
        self.hop_scores = [float(s)]
        return float(s)

    def as_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "pl_first": self.pl_first,
            "pl_last": self.pl_last,
            "budget_frames": self.budget_frames,
            "hop_scores": [round(float(x), 6) for x in self.hop_scores],
            "S_room": round(self.s_room, 6),
            "hops_done": len(self.hop_scores),
        }


def record_best_s_room(
    tracker: RoomSegmentTracker,
    project_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Persist a new best additive room score. Returns the updated record or None."""
    if not tracker.segment_id or not tracker.hop_scores:
        return None
    path = best_scores_path(project_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    best = data.setdefault("best", {})
    if not isinstance(best, dict):
        best = {}
        data["best"] = best
    prev = best.get(tracker.segment_id)
    prev_s = float(prev.get("S_room")) if isinstance(prev, dict) and "S_room" in prev else None
    s_room = float(tracker.s_room)
    if prev_s is not None and s_room <= prev_s:
        tracker.best_s_room = prev_s
        return None
    rec = {
        "segment_id": tracker.segment_id,
        "S_room": round(s_room, 6),
        "hop_scores": [round(float(x), 6) for x in tracker.hop_scores],
        "pl_first": tracker.pl_first,
        "pl_last": tracker.pl_last,
        "hops_done": len(tracker.hop_scores),
    }
    best[tracker.segment_id] = rec
    data["schema_version"] = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tracker.best_s_room = s_room
    return rec


def clear_room_segments_cache() -> None:
    global _cached, _cached_mtime
    _cached = None
    _cached_mtime = None
