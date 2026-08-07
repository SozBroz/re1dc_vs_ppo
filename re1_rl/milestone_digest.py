"""Milestone digest for Go-Explore v2 cell keys (all mansion rooms).

Digest tokens:
  carry:<item> | got:<item> | use:<site> | weapon:<name> |
  event:kenneth_done | event:dining_statue_down | gallery:*
"""

from __future__ import annotations

from typing import Any, Iterable

from re1_rl.cutscene_reward import kenneth_cutscene_seen
from re1_rl.item_todo import canonical_item
from re1_rl.pb_milestones import KEY_ITEM_MILESTONES, STORY_USE_MILESTONES
from re1_rl.progress import ProgressTracker

# Legacy analytics / optional frontier filter — not used for capture admission.
YAWN_PATH_ROOMS: frozenset[str] = frozenset(
    {
        "105",
        "104",
        "106",
        "107",
        "10F",
        "117",
        "118",
        "10C",
        "10D",
        "102",
        "116",
        "202",
        "203",
        "205",
        "209",
        "20E",
        "210",
    }
)

VERIFIED_STORY_USES: frozenset[str] = frozenset(STORY_USE_MILESTONES)

# Re-export pb key-item set for digest callers.
__all__ = (
    "YAWN_PATH_ROOMS",
    "KEY_ITEM_MILESTONES",
    "VERIFIED_STORY_USES",
    "DEFAULT_TILE_SPAN",
    "gallery_token",
    "compute_digest",
    "cell_key_v2",
    "parse_cell_key_v2",
)

DEFAULT_TILE_SPAN = 4096


def gallery_token(progress: ProgressTracker) -> str:
    """Single gallery:* token from ProgressTracker gallery_* fields."""
    if bool(progress.gallery_completed):
        return "gallery:complete"
    if bool(progress.gallery_needs_reentry):
        return "gallery:retry_required"
    step = int(progress.gallery_step_index or 0)
    if step > 0:
        return f"gallery:step:{step}"
    return "gallery:idle"


def _inventory_names(state: dict[str, Any] | None) -> set[str]:
    if not state:
        return set()
    out: set[str] = set()
    raw_slots = state.get("inventory_slots")
    if raw_slots is not None:
        for entry in raw_slots:
            if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                name = canonical_item(str(entry[0]))
                qty = int(entry[1]) if len(entry) >= 2 else 1
                if name and qty > 0:
                    out.add(name)
            elif isinstance(entry, dict):
                name = canonical_item(
                    str(entry.get("name") or entry.get("item") or "")
                )
                qty = int(entry.get("qty", 1) or 0)
                if name and qty > 0:
                    out.add(name)
        return out
    for name in state.get("inventory") or ():
        if name:
            out.add(canonical_item(str(name)))
    return out


def compute_digest(
    state: dict[str, Any] | None,
    progress_tracker: ProgressTracker,
    *,
    ever_held: set[str] | frozenset[str] | Iterable[str],
) -> str:
    """Stable pipe-joined digest for Go-Explore cells.

    Example:
      ``carry:emblem|got:lockpick|use:emblem@10F_alcove|weapon:bazooka_acid|event:kenneth_done|gallery:idle``
    """
    held = {canonical_item(str(n)) for n in (ever_held or ()) if n}
    inv = _inventory_names(state)

    tokens: list[str] = []
    for item in sorted(inv & KEY_ITEM_MILESTONES):
        tokens.append(f"carry:{item}")
    for item in sorted(held & KEY_ITEM_MILESTONES):
        tokens.append(f"got:{item}")

    uses = {
        str(s)
        for s in (progress_tracker.rewarded_story_uses or ())
        if str(s) in VERIFIED_STORY_USES
    }
    for site in sorted(uses):
        tokens.append(f"use:{site}")

    for weapon in sorted(str(w) for w in (progress_tracker.weapons_progressed or ()) if w):
        name = canonical_item(weapon)
        if name:
            tokens.append(f"weapon:{name}")

    if kenneth_cutscene_seen(progress_tracker.rewarded_cutscenes):
        tokens.append("event:kenneth_done")

    tokens.append(gallery_token(progress_tracker))
    if bool(progress_tracker.dining_statue_rewarded) or dining_statue_knocked_from_progress(
        state, progress_tracker
    ):
        tokens.append("event:dining_statue_down")
    return "|".join(tokens)


def dining_statue_knocked_from_progress(
    state: dict[str, Any] | None,
    progress_tracker: ProgressTracker,
) -> bool:
    from re1_rl.dining_statue_puzzle import dining_statue_knocked_from_state

    if dining_statue_knocked_from_state(state):
        return True
    return bool(progress_tracker.dining_statue_rewarded)


def cell_key_v2(
    room_id: str | int,
    x: int,
    z: int,
    digest: str,
    tile_span: int = DEFAULT_TILE_SPAN,
) -> str:
    """``v2|r=<ROOM>|x=<floor(x/span)>|z=<floor(z/span)>|m=<digest>``."""
    span = max(1, int(tile_span))
    room = str(room_id).strip().upper()
    if room.lower().startswith("0x"):
        room = room[2:].upper()
    tx = int(x) // span
    tz = int(z) // span
    return f"v2|r={room}|x={tx}|z={tz}|m={digest}"


def parse_cell_key_v2(key: str) -> dict[str, Any]:
    """Parse a v2 cell key into room_id, tile bins, and digest.

    Digest may contain ``|`` (token join), so ``m=`` is always the final
    field and consumes the remainder of the string.
    """
    s = str(key)
    prefix = "v2|r="
    if not s.startswith(prefix):
        raise ValueError(f"not a v2 cell key: {key!r}")
    rest = s[len(prefix) :]
    # room until |x=
    if "|x=" not in rest:
        raise ValueError(f"malformed v2 cell key (missing x=): {key!r}")
    room, rest = rest.split("|x=", 1)
    if "|z=" not in rest:
        raise ValueError(f"malformed v2 cell key (missing z=): {key!r}")
    x_s, rest = rest.split("|z=", 1)
    if "|m=" not in rest:
        raise ValueError(f"malformed v2 cell key (missing m=): {key!r}")
    z_s, digest = rest.split("|m=", 1)
    if not room:
        raise ValueError(f"incomplete v2 cell key: {key!r}")
    return {
        "room_id": str(room).upper(),
        "tile_x": int(x_s),
        "tile_z": int(z_s),
        "tile_bin": (int(x_s), int(z_s)),
        "milestone_digest": digest,
        "cell_key": s,
    }
