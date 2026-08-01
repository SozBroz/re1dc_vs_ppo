"""Verified persistent runtime-state vector for the named-state tower.

Only bits confirmed in ``memory_map`` and already read into env state are packed.
No guessed SCD/puzzle flags, no route/waypoint labels, and no interaction_prompt
(still unmapped: ``INTERACTION_PROMPT is None``).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from re1_rl.memory_map import IN_CONTROL_MASK, SCENE_FLAG_MASK

# Packed layout (shrinks if fields are removed; do not pad unknowns).
# Verified sources: DOOR_FLAGS / GAME_TIMER / LAB_TIMER / GAME_MODE in
# DEFAULT_RAM_FIELDS; SCENE_FLAG / MESSAGE_FLAG / PLAYER_POISON in env reads;
# gallery_* in DEFAULT_RAM_FIELDS. interaction_prompt intentionally omitted.
NAMED_STATE_FIELDS: list[tuple[str, str]] = (
    [(f"door_flag_{i}", f"door_flags bit {i}") for i in range(32)]
    + [(f"game_mode_b{i}", f"game_mode bit {i}") for i in range(8)]
    + [(f"scene_flag_b{i}", f"scene_flag bit {i}") for i in range(8)]
    + [(f"msg_flag_b{i}", f"msg_flag bit {i}") for i in range(8)]
    + [
        ("in_control", "GAME_MODE in-control bit"),
        ("scene_scripted", "SCENE_FLAG scripted-scene bit"),
        ("game_timer_norm", "game_timer / 2^32"),
        ("lab_timer_norm", "lab_timer / 65535"),
        ("gallery_progress", "gallery_progress / 6"),
        ("gallery_confirm", "gallery_confirm / 255"),
        ("poisoned", "player poisoned"),
    ]
)
NAMED_STATE_DIM = len(NAMED_STATE_FIELDS)  # 63 verified scalars


def encode_named_state(state: dict[str, Any] | None) -> np.ndarray:
    """Pack verified runtime bits from an env state dict.

    Missing keys default to zero (safe for stub envs / partial states).
    """
    v = np.zeros(NAMED_STATE_DIM, dtype=np.float32)
    if not state:
        return v
    door = int(state.get("door_flags", 0) or 0) & 0xFFFFFFFF
    for i in range(32):
        v[i] = 1.0 if (door >> i) & 1 else 0.0
    mode = int(state.get("game_mode", 0) or 0) & 0xFF
    scene = int(state.get("scene_flag", 0) or 0) & 0xFF
    msg = int(state.get("msg_flag", 0) or 0) & 0xFF
    off = 32
    for i in range(8):
        v[off + i] = 1.0 if (mode >> i) & 1 else 0.0
    off += 8
    for i in range(8):
        v[off + i] = 1.0 if (scene >> i) & 1 else 0.0
    off += 8
    for i in range(8):
        v[off + i] = 1.0 if (msg >> i) & 1 else 0.0
    off += 8
    v[off] = 1.0 if (mode & IN_CONTROL_MASK) else 0.0
    v[off + 1] = 1.0 if (scene & SCENE_FLAG_MASK) else 0.0
    v[off + 2] = float(int(state.get("game_timer", 0) or 0) & 0xFFFFFFFF) / 4294967295.0
    v[off + 3] = float(int(state.get("lab_timer", 0) or 0) & 0xFFFF) / 65535.0
    v[off + 4] = float(np.clip(float(state.get("gallery_progress", 0) or 0) / 6.0, 0.0, 1.0))
    v[off + 5] = float(np.clip(float(state.get("gallery_confirm", 0) or 0) / 255.0, 0.0, 1.0))
    v[off + 6] = 1.0 if state.get("poisoned") else 0.0
    return v
