"""Episode cutscene milestone ledger obs (north star B5)."""

from __future__ import annotations

import numpy as np

# Mansion milestones are normally ``room:cam`` keys. Same-room sequenced keys
# (``room:cam:sN`` from cutscene_reward) also light the matching milestone.
# The Wesker bit is synthetic because the terminal gate intentionally prevents
# that pre-Kenneth cutscene from entering the rewarded-cutscene set.
CUTSCENE_WESKER_PRE_KENNETH_KEY = "wesker_pre_kenneth"

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
    CUTSCENE_WESKER_PRE_KENNETH_KEY,
    "211:0",
)

CUTSCENE_LEDGER_DIM = len(CUTSCENE_MILESTONE_KEYS)


def _milestone_seen(milestone: str, seen: set[str] | frozenset[str]) -> bool:
    if milestone in seen:
        return True
    prefix = milestone + ":s"
    return any(str(k).startswith(prefix) for k in seen)


def encode_cutscene_ledger(
    rewarded_cutscenes: set[str] | frozenset[str] | None,
    *,
    wesker_pre_kenneth: bool = False,
) -> np.ndarray:
    """One float per milestone: 1.0 if this episode already saw that cutscene."""
    v = np.zeros(CUTSCENE_LEDGER_DIM, dtype=np.float32)
    seen = rewarded_cutscenes or set()
    for i, key in enumerate(CUTSCENE_MILESTONE_KEYS):
        if _milestone_seen(key, seen):
            v[i] = 1.0
    if wesker_pre_kenneth:
        v[CUTSCENE_MILESTONE_KEYS.index(CUTSCENE_WESKER_PRE_KENNETH_KEY)] = 1.0
    return v
