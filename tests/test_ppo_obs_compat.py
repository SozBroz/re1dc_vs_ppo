"""SB3 PPO must consume the new frame/proprio/goal Dict obs.

Uses a stub env with the exact observation/action spaces from RE1Env --
no emulator, ~20s on CPU. Guards against obs-layout changes breaking the
training stack silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gymnasium as gym
import numpy as np
from gymnasium import spaces

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.env import ACTION_NAMES, FRAME_SHAPE
from re1_rl.episode_history import ACQUISITION_LOG_DIM, ROOM_HISTORY_DIM
from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, INVENTORY_OBS_DIM, PROPRIO_DIM, ROOM_VISITED_DIM
from re1_rl.policy_config import POLICY_KWARGS
from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM
from re1_rl.item_affordances import AFFORDANCES_DIM
from re1_rl.key_items import KEYS_HELD_DIM
from re1_rl.maps_files import MAPS_FILES_DIM
from re1_rl.milestone_features import MILESTONE_DIM
from re1_rl.room_signature import ENEMY_ROSTER_DIM
from re1_rl.spatial_encoder import SPATIAL_DIM, VISITED_SHAPE


class StubRE1Env(gym.Env):
    def __init__(self) -> None:
        super().__init__()
        self.observation_space = spaces.Dict(
            {
                "frame": spaces.Box(0, 255, shape=FRAME_SHAPE, dtype=np.uint8),
                "proprio": spaces.Box(-1.0, 1.0, shape=(PROPRIO_DIM,), dtype=np.float32),
                "goal": spaces.Box(-2.0, 2.0, shape=(GOAL_DIM,), dtype=np.float32),
                "spatial": spaces.Box(-2.0, 2.0, shape=(SPATIAL_DIM,), dtype=np.float32),
                "visited": spaces.Box(0.0, 1.0, shape=VISITED_SHAPE, dtype=np.float32),
                "rooms_visited": spaces.Box(0.0, 1.0, shape=(ROOM_VISITED_DIM,), dtype=np.float32),
                "box": spaces.Box(0.0, 2.0, shape=(BOX_DIM,), dtype=np.float32),
                "inventory": spaces.Box(0.0, 1.0, shape=(INVENTORY_OBS_DIM,), dtype=np.float32),
                "history": spaces.Box(0.0, 1.0, shape=(ROOM_HISTORY_DIM,), dtype=np.float32),
                "acquisitions": spaces.Box(0.0, 1.0, shape=(ACQUISITION_LOG_DIM,), dtype=np.float32),
                "room_enemies": spaces.Box(0.0, 1.0, shape=(ENEMY_ROSTER_DIM,), dtype=np.float32),
                "keys_held": spaces.Box(0.0, 1.0, shape=(KEYS_HELD_DIM,), dtype=np.float32),
                "affordances": spaces.Box(0.0, 1.0, shape=(AFFORDANCES_DIM,), dtype=np.float32),
                "cutscene_ledger": spaces.Box(
                    0.0, 1.0, shape=(CUTSCENE_LEDGER_DIM,), dtype=np.float32
                ),
                "milestones": spaces.Box(0.0, 1.0, shape=(MILESTONE_DIM,), dtype=np.float32),
                "maps_files": spaces.Box(0.0, 1.0, shape=(MAPS_FILES_DIM,), dtype=np.float32),
            }
        )
        self.action_space = spaces.Discrete(len(ACTION_NAMES))
        self._n = 0

    def _obs(self):
        return {key: space.sample() for key, space in self.observation_space.items()}

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._n = 0
        return self._obs(), {}

    def step(self, action):
        self._n += 1
        return self._obs(), 0.0, False, self._n >= 32, {}


def test_ppo_learns_on_dict_obs():
    from stable_baselines3 import PPO

    model = PPO("MultiInputPolicy", StubRE1Env(), policy_kwargs=POLICY_KWARGS,
                n_steps=64, batch_size=32, n_epochs=1, device="cpu", verbose=0)
    model.learn(total_timesteps=64)
    obs, _ = StubRE1Env().reset()
    action, _ = model.predict(obs)
    assert 0 <= int(action) < len(ACTION_NAMES)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
