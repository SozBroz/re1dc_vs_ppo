"""Map / file pickup flags obs (north star C6)."""

from __future__ import annotations

import numpy as np

# u16 bitfield at MAPS_FILES_FLAGS — expose all 16 bits until per-bit semantics hunted.
MAPS_FILES_DIM = 16


def encode_maps_files_flags(raw: int | float | None) -> np.ndarray:
    """One float per bit of the confirmed maps/files u16 RAM field."""
    v = np.zeros(MAPS_FILES_DIM, dtype=np.float32)
    value = int(raw or 0) & 0xFFFF
    for i in range(MAPS_FILES_DIM):
        if (value >> i) & 1:
            v[i] = 1.0
    return v
