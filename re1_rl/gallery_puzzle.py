"""Large Gallery (117) cradle-to-grave puzzle constants and hint encoding."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

GALLERY_ROOM_ID = "117"
GALLERY_STEP_REWARD = 2.0  # large progress ×4 (was 0.5)

# RDT slots: newborn, infant, boy, young man, middle-aged man, old man.
GALLERY_STEP_SLOTS = (3, 5, 6, 4, 2, 7)
GALLERY_STEP_VALUES = (32, 8, 4, 16, 64, 2)
GALLERY_VALUE_TO_COUNT = {
    value: index + 1 for index, value in enumerate(GALLERY_STEP_VALUES)
}

# Target centers from ROOM1170.RDT. Slot 8 is the final "end of life" switch;
# it does not receive #9 reward because the resulting Star Crest pays via #3.
GALLERY_TARGETS = (
    (19500.0, 7200.0),
    (16000.0, 6100.0),
    (10500.0, 6100.0),
    (19500.0, 6100.0),
    (16000.0, 7200.0),
    (7250.0, 6100.0),
    (1850.0, 4100.0),
)
GALLERY_EXIT_TARGET = (3200.0, 11700.0)  # ROOM1170.RDT door to room 10A


def completed_steps(raw_progress: int) -> int:
    """Decode the game's one-hot progress byte to 0..6 completed portraits."""
    return int(GALLERY_VALUE_TO_COUNT.get(int(raw_progress), 0))


def encode_gallery_hint(state: dict[str, Any]) -> np.ndarray:
    """Return next-target bearing sin/cos, distance, and sequence progress."""
    out = np.zeros(4, dtype=np.float32)
    if str(state.get("room_id", "")) != GALLERY_ROOM_ID:
        return out
    inventory = {str(name) for name in state.get("inventory", ())}
    if "star_crest" in inventory:
        return out

    needs_reentry = bool(state.get("gallery_needs_reentry", False))
    count = completed_steps(int(state.get("gallery_progress", 0) or 0))
    tx, tz = GALLERY_EXIT_TARGET if needs_reentry else GALLERY_TARGETS[count]
    dx = tx - float(state.get("x", 0))
    dz = tz - float(state.get("z", 0))
    distance = min(math.hypot(dx, dz) / 4096.0, 2.0)
    target_angle = math.atan2(dz, dx)
    facing = 2.0 * math.pi * float(state.get("facing", 0)) / 4096.0
    bearing = target_angle - facing
    out[:] = (
        math.sin(bearing),
        math.cos(bearing),
        distance,
        -1.0 if needs_reentry else count / len(GALLERY_STEP_VALUES),
    )
    return out
