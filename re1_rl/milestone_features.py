"""Derived episode milestone features (north star B8).

Compact booleans derived from history deque, cutscene ledger, and keys_held —
no new RAM hunts.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from re1_rl.cutscene_ledger import CUTSCENE_MILESTONE_KEYS
from re1_rl.episode_history import EpisodeHistory
from re1_rl.item_todo import canonical_item

MILESTONE_FEATURE_NAMES: tuple[str, ...] = (
    "on_2f",
    "visited_2f",
    "kenneth_seen",
    "barry_dining_seen",
    "barry_2f_seen",
    "has_lockpick",
    "has_emblem",
    "has_shield_key",
    "upstairs_then_down",
    "dining_revisit",
    "rooms_in_deque_frac",
    "main_hall_in_history",
)

MILESTONE_DIM = len(MILESTONE_FEATURE_NAMES)

_FLOOR2_PREFIX = "2"
_DINING = "105"
_MAIN_HALL = "106"


def _room_on_2f(room_id: str) -> bool:
    rid = str(room_id)
    return len(rid) >= 3 and rid[0] == _FLOOR2_PREFIX


def _ledger_bit(ledger: np.ndarray, key: str) -> float:
    try:
        idx = CUTSCENE_MILESTONE_KEYS.index(key)
    except ValueError:
        return 0.0
    if idx < len(ledger):
        return float(ledger[idx])
    return 0.0


def _held(ever_held: set[str] | frozenset[str] | None, *names: str) -> float:
    held = {canonical_item(n) for n in (ever_held or ())}
    for name in names:
        if canonical_item(name) in held:
            return 1.0
    return 0.0


def encode_milestones(
    *,
    current_room: str,
    episode_history: EpisodeHistory,
    cutscene_ledger: np.ndarray,
    ever_held: set[str] | frozenset[str] | None,
) -> np.ndarray:
    v = np.zeros(MILESTONE_DIM, dtype=np.float32)
    rooms = [rid for rid, _ in episode_history.room_deque.entries]

    v[0] = 1.0 if _room_on_2f(current_room) else 0.0
    v[1] = 1.0 if any(_room_on_2f(r) for r in rooms) else 0.0
    v[2] = _ledger_bit(cutscene_ledger, "104:0")
    v[3] = _ledger_bit(cutscene_ledger, "106:1")
    v[4] = max(_ledger_bit(cutscene_ledger, "203:0"), _ledger_bit(cutscene_ledger, "203:1"))
    v[5] = _held(ever_held, "lockpick")
    v[6] = max(_held(ever_held, "emblem"), _held(ever_held, "gold_emblem"))
    v[7] = _held(ever_held, "shield_key")

    saw_2f = False
    upstairs_then_down = False
    for rid in rooms:
        if _room_on_2f(rid):
            saw_2f = True
        elif saw_2f and rid and rid[0] == "1":
            upstairs_then_down = True
            break
    v[8] = 1.0 if upstairs_then_down else 0.0

    dining_count = sum(1 for r in rooms if r == _DINING)
    v[9] = 1.0 if dining_count >= 2 else 0.0

    cap = float(episode_history.room_deque.capacity)
    v[10] = min(len(rooms), int(cap)) / cap
    v[11] = 1.0 if _MAIN_HALL in rooms else 0.0
    return v
