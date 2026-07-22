"""PB episode sidecar: serialize/restore episode Python memory alongside BizHawk .State.

North star: episode-side trackers (visited rooms, anti-farm claim sets, history
deques, ever-held keys) — never route waypoint index / compass / next room.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from re1_rl.episode_history import AcquisitionLog, EpisodeHistory, RoomTransitionDeque
from re1_rl.item_todo import ItemTracker
from re1_rl.progress import ProgressTracker
from re1_rl.reward import SOFTLOCK_EXTENSION_FRAMES

SIDECAR_SCHEMA_VERSION = 1


class SidecarSchemaError(ValueError):
    """Raised when sidecar JSON does not match the expected schema version."""


@dataclass
class EpisodeSidecarParts:
    """Direct tracker bundle for unit tests (no BizHawk env)."""

    progress: ProgressTracker
    items: ItemTracker
    episode_history: EpisodeHistory
    box_cache: list[tuple[int, int]] | None = None


def _sorted_list(values: set[str] | frozenset[str]) -> list[str]:
    return sorted(str(v) for v in values)


def _as_set(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, (set, frozenset)):
        return {str(v) for v in values}
    return {str(v) for v in values}


def _check_schema_version(data: dict[str, Any]) -> None:
    version = data.get("schema_version")
    if version != SIDECAR_SCHEMA_VERSION:
        raise SidecarSchemaError(
            f"episode sidecar schema_version {version!r} != "
            f"expected {SIDECAR_SCHEMA_VERSION}"
        )


def progress_to_sidecar(progress: ProgressTracker) -> dict[str, Any]:
    """Serialize anti-farm / gallery / softlock fields from ``ProgressTracker``."""
    return {
        "visited_rooms": _sorted_list(progress.visited_rooms),
        "rewarded_cutscenes": _sorted_list(progress.rewarded_cutscenes),
        "rewarded_story_uses": _sorted_list(progress.rewarded_story_uses),
        "rewarded_document_rooms": _sorted_list(progress.rewarded_document_rooms),
        "cutscene_blocked_after_pickup_room": progress.cutscene_blocked_after_pickup_room,
        "kenneth_gate_breached": bool(progress.kenneth_gate_breached),
        "spawn_room_id": progress.spawn_room_id,
        "spawn_room_bonus_paid": bool(progress._spawn_room_bonus_paid),
        "weapons_progressed": _sorted_list(progress.weapons_progressed),
        "softlock_cap_frames": int(progress.softlock_cap_frames),
        "stagnation_frames": int(progress.stagnation_frames),
        "gallery_step_index": int(progress.gallery_step_index),
        "gallery_pending_reward": float(progress.gallery_pending_reward),
        "gallery_completed": bool(progress.gallery_completed),
        "gallery_needs_reentry": bool(progress.gallery_needs_reentry),
    }


def apply_progress_sidecar(progress: ProgressTracker, data: dict[str, Any]) -> None:
    """Restore ``ProgressTracker`` fields from a progress sidecar dict."""
    progress.visited_rooms = _as_set(data.get("visited_rooms"))
    progress.rewarded_cutscenes = _as_set(data.get("rewarded_cutscenes"))
    progress.rewarded_story_uses = _as_set(data.get("rewarded_story_uses"))
    progress.rewarded_document_rooms = _as_set(data.get("rewarded_document_rooms"))
    blocked = data.get("cutscene_blocked_after_pickup_room")
    progress.cutscene_blocked_after_pickup_room = str(blocked) if blocked else None
    progress.kenneth_gate_breached = bool(data.get("kenneth_gate_breached", False))
    spawn = data.get("spawn_room_id")
    progress.spawn_room_id = str(spawn) if spawn else None
    progress._spawn_room_bonus_paid = bool(data.get("spawn_room_bonus_paid", False))
    progress.weapons_progressed = _as_set(data.get("weapons_progressed"))
    progress.softlock_cap_frames = int(data.get("softlock_cap_frames", 0))
    progress._stagnation_frames = int(data.get("stagnation_frames", 0))
    progress.gallery_step_index = int(data.get("gallery_step_index", 0))
    progress.gallery_pending_reward = float(data.get("gallery_pending_reward", 0.0))
    progress.gallery_completed = bool(data.get("gallery_completed", False))
    progress.gallery_needs_reentry = bool(data.get("gallery_needs_reentry", False))


def item_tracker_to_sidecar(items: ItemTracker) -> dict[str, Any]:
    """Serialize ``ItemTracker.ever_held``."""
    return {"ever_held": _sorted_list(items.ever_held)}


def apply_item_tracker_sidecar(items: ItemTracker, data: dict[str, Any]) -> None:
    """Restore ``ItemTracker.ever_held``."""
    items.ever_held = _as_set(data.get("ever_held"))


def history_to_sidecar(history: EpisodeHistory) -> dict[str, Any]:
    """Serialize room deque + acquisition log entries."""
    return {
        "room_entries": [
            [str(room_id), int(step)]
            for room_id, step in history.room_deque.entries
        ],
        "acquisition_entries": [
            [int(item_id), str(room_id)]
            for item_id, room_id in history.acquisitions.entries
        ],
    }


def apply_history_sidecar(history: EpisodeHistory, data: dict[str, Any]) -> None:
    """Restore ``EpisodeHistory`` room deque and acquisition log."""
    room_entries = data.get("room_entries") or []
    history.room_deque.entries = deque(
        (str(room_id), int(step)) for room_id, step in room_entries
    )
    acq_entries = data.get("acquisition_entries") or []
    history.acquisitions.entries = deque(
        (int(item_id), str(room_id)) for item_id, room_id in acq_entries
    )


def _encode_box_cache(box_cache: list[tuple[int, int]] | None) -> list[list[int]] | None:
    if box_cache is None:
        return None
    return [[int(slot), int(qty)] for slot, qty in box_cache]


def _decode_box_cache(raw: Any) -> list[tuple[int, int]] | None:
    if raw is None:
        return None
    return [(int(pair[0]), int(pair[1])) for pair in raw]


def _resolve_parts(env_or_parts: Any) -> EpisodeSidecarParts:
    if isinstance(env_or_parts, EpisodeSidecarParts):
        return env_or_parts
    if isinstance(env_or_parts, dict):
        return EpisodeSidecarParts(
            progress=env_or_parts["progress"],
            items=env_or_parts["items"],
            episode_history=env_or_parts["episode_history"],
            box_cache=env_or_parts.get("box_cache"),
        )
    return EpisodeSidecarParts(
        progress=env_or_parts._progress,
        items=env_or_parts._items,
        episode_history=env_or_parts._episode_history,
        box_cache=getattr(env_or_parts, "_box_cache", None),
    )


def dump_episode_sidecar(
    env_or_parts: Any,
    *,
    captured_room_id: str | None = None,
    captured_at_iso: str | None = None,
) -> dict[str, Any]:
    """Dump episode-side Python memory for a PB bundle JSON sidecar."""
    parts = _resolve_parts(env_or_parts)
    out: dict[str, Any] = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "progress": progress_to_sidecar(parts.progress),
        **item_tracker_to_sidecar(parts.items),
        "episode_history": history_to_sidecar(parts.episode_history),
    }
    box = _encode_box_cache(parts.box_cache)
    if box is not None:
        out["box_cache"] = box
    if captured_room_id is not None:
        out["captured_room_id"] = str(captured_room_id)
    if captured_at_iso is not None:
        out["captured_at_iso"] = str(captured_at_iso)
    return out


def apply_episode_sidecar(
    env_or_parts: Any,
    data: dict[str, Any],
    *,
    reset_softlock: bool = True,
) -> None:
    """Restore episode-side trackers from a PB sidecar dict.

    When *reset_softlock* is True (default for curriculum PB starts), do **not**
    restore capture-time stagnation / softlock_cap — grant a fresh 12-minute
    progress budget every time.
    """
    _check_schema_version(data)
    parts = _resolve_parts(env_or_parts)
    apply_progress_sidecar(parts.progress, data.get("progress") or {})
    if reset_softlock:
        parts.progress._stagnation_frames = 0
        parts.progress.softlock_cap_frames = int(SOFTLOCK_EXTENSION_FRAMES)
    apply_item_tracker_sidecar(parts.items, data)
    apply_history_sidecar(parts.episode_history, data.get("episode_history") or {})
    if "box_cache" in data:
        parts.box_cache = _decode_box_cache(data.get("box_cache"))
        if isinstance(env_or_parts, EpisodeSidecarParts):
            env_or_parts.box_cache = parts.box_cache
        elif not isinstance(env_or_parts, dict):
            env_or_parts._box_cache = parts.box_cache


def utc_now_iso() -> str:
    """ISO-8601 timestamp for optional ``captured_at_iso`` metadata."""
    return datetime.now(timezone.utc).isoformat()
