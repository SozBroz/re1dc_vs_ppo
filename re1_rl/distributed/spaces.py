"""RE1 observation/action spaces without starting BizHawk."""

from __future__ import annotations

import gymnasium as gym
from gymnasium import spaces

from re1_rl.env import ACTION_NAMES, FRAME_SHAPE, FRAME_SHAPE_CHW
from re1_rl.episode_history import ACQUISITION_LOG_DIM, ROOM_HISTORY_DIM
from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, INVENTORY_OBS_DIM, PROPRIO_DIM, ROOM_VISITED_DIM
from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM
from re1_rl.item_affordances import AFFORDANCES_DIM
from re1_rl.world_state_encoder import WORLD_STATE_DIM
from re1_rl.key_items import KEYS_HELD_DIM
from re1_rl.maps_files import MAPS_FILES_DIM
from re1_rl.milestone_features import MILESTONE_DIM
from re1_rl.room_signature import ENEMY_ROSTER_DIM
from re1_rl.spatial_encoder import SPATIAL_DIM, VISITED_SHAPE


def make_re1_spaces() -> tuple[spaces.Dict, spaces.Discrete]:
    observation_space = spaces.Dict(
        {
            "frame": spaces.Box(0, 255, shape=FRAME_SHAPE, dtype="uint8"),
            "proprio": spaces.Box(-1.0, 1.0, shape=(PROPRIO_DIM,), dtype="float32"),
            "goal": spaces.Box(-2.0, 2.0, shape=(GOAL_DIM,), dtype="float32"),
            "spatial": spaces.Box(-2.0, 2.0, shape=(SPATIAL_DIM,), dtype="float32"),
            "visited": spaces.Box(0.0, 1.0, shape=VISITED_SHAPE, dtype="float32"),
            "rooms_visited": spaces.Box(0.0, 1.0, shape=(ROOM_VISITED_DIM,), dtype="float32"),
            "box": spaces.Box(0.0, 2.0, shape=(BOX_DIM,), dtype="float32"),
            "inventory": spaces.Box(0.0, 1.0, shape=(INVENTORY_OBS_DIM,), dtype="float32"),
            "history": spaces.Box(0.0, 1.0, shape=(ROOM_HISTORY_DIM,), dtype="float32"),
            "acquisitions": spaces.Box(0.0, 1.0, shape=(ACQUISITION_LOG_DIM,), dtype="float32"),
            "room_enemies": spaces.Box(0.0, 1.0, shape=(ENEMY_ROSTER_DIM,), dtype="float32"),
            "keys_held": spaces.Box(0.0, 1.0, shape=(KEYS_HELD_DIM,), dtype="float32"),
            "affordances": spaces.Box(0.0, 1.0, shape=(AFFORDANCES_DIM,), dtype="float32"),
            "world_state": spaces.Box(0.0, 8.0, shape=(WORLD_STATE_DIM,), dtype="float32"),
            "cutscene_ledger": spaces.Box(
                0.0, 1.0, shape=(CUTSCENE_LEDGER_DIM,), dtype="float32"
            ),
            "milestones": spaces.Box(0.0, 1.0, shape=(MILESTONE_DIM,), dtype="float32"),
            "maps_files": spaces.Box(0.0, 1.0, shape=(MAPS_FILES_DIM,), dtype="float32"),
        }
    )
    action_space = spaces.Discrete(len(ACTION_NAMES))
    return observation_space, action_space


def make_re1_policy_spaces() -> tuple[spaces.Dict, spaces.Discrete]:
    """Policy-side spaces: frame is CHW (matches SB3 / VecTransposeImage training)."""
    obs_space, act_space = make_re1_spaces()
    frame = obs_space.spaces["frame"]
    chw_frame = frame.__class__(low=0, high=255, shape=FRAME_SHAPE_CHW, dtype=frame.dtype)
    policy_obs_space = obs_space.__class__(
        {**obs_space.spaces, "frame": chw_frame}
    )
    return policy_obs_space, act_space
