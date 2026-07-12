"""Episode cutscene milestone ledger obs (north star B5)."""

from __future__ import annotations

import numpy as np

# Mansion milestones in ``room:cam`` form. Same-room sequenced keys
# (``room:cam:sN`` from cutscene_reward) also light the matching milestone.
CUTSCENE_MILESTONE_KEYS: tuple[str, ...] = (
    "105:0",
    "105:1",
    "105:2",
    "106:1",
    "104:0",
    "104:1",
    "104:2",
    "107:0",
    "107:1",
    "10F:0",
    "10F:1",
    "117:0",
    "203:0",
    "203:1",
    "100:0",
    "211:0",
)

CUTSCENE_LEDGER_DIM = len(CUTSCENE_MILESTONE_KEYS)


def _milestone_seen(milestone: str, seen: set[str] | frozenset[str]) -> bool:
    if milestone in seen:
        return True
    prefix = milestone + ":s"
    return any(str(k).startswith(prefix) for k in seen)


def encode_cutscene_ledger(rewarded_cutscenes: set[str] | frozenset[str] | None) -> np.ndarray:
    """One float per milestone: 1.0 if this episode already saw that cutscene."""
    v = np.zeros(CUTSCENE_LEDGER_DIM, dtype=np.float32)
    seen = rewarded_cutscenes or set()
    for i, key in enumerate(CUTSCENE_MILESTONE_KEYS):
        if _milestone_seen(key, seen):
            v[i] = 1.0
    return v
