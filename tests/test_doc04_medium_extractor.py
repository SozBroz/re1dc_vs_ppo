"""RE1Doc04MediumExtractor forward pass and features_dim."""

from __future__ import annotations

import sys
from pathlib import Path

import gymnasium as gym
import numpy as np
import torch
from gymnasium import spaces

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM
from re1_rl.doc04_medium_extractor import (
    FEATURES_DIM,
    RE1Doc04MediumExtractor,
    TOWER_OUT_DIM,
    reload_doc04_world_catalog_buffers,
)
from re1_rl.env import ACTION_NAMES, FRAME_SHAPE_CHW
from re1_rl.episode_history import ACQUISITION_LOG_DIM, ROOM_HISTORY_DIM
from re1_rl.features_extractor import WORLD_STATE_DIM
from re1_rl.item_affordances import AFFORDANCES_DIM, KEY_HINTS_DIM
from re1_rl.key_items import KEYS_HELD_DIM
from re1_rl.maps_files import MAPS_FILES_DIM
from re1_rl.milestone_features import MILESTONE_DIM
from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, INVENTORY_OBS_DIM, PROPRIO_DIM, ROOM_VISITED_DIM
from re1_rl.policy_config import POLICY_KWARGS
from re1_rl.room_signature import ENEMY_ROSTER_DIM
from re1_rl.spatial_encoder import SPATIAL_DIM, VISITED_SHAPE
from re1_rl.weapon_damage import LAST_ATTACK_DIM, WEAPON_CARD_DIM


def _stub_obs_space(*, with_world_state: bool = True, with_key_hints: bool = False) -> spaces.Dict:
    spaces_map: dict = {
        "frame": spaces.Box(0, 255, shape=FRAME_SHAPE_CHW, dtype=np.uint8),
        "proprio": spaces.Box(-1.0, 1.0, shape=(PROPRIO_DIM,), dtype=np.float32),
        "goal": spaces.Box(-2.0, 2.0, shape=(GOAL_DIM,), dtype=np.float32),
        "spatial": spaces.Box(-2.0, 2.0, shape=(SPATIAL_DIM,), dtype=np.float32),
        "visited": spaces.Box(0.0, 1.0, shape=VISITED_SHAPE, dtype=np.float32),
        "rooms_visited": spaces.Box(0.0, 1.0, shape=(ROOM_VISITED_DIM,), dtype=np.float32),
        "box": spaces.Box(0.0, 2.0, shape=(BOX_DIM,), dtype=np.float32),
        "inventory": spaces.Box(0.0, 1.0, shape=(INVENTORY_OBS_DIM,), dtype=np.float32),
        "weapon_card": spaces.Box(0.0, 1.0, shape=(WEAPON_CARD_DIM,), dtype=np.float32),
        "last_attack": spaces.Box(0.0, 1.0, shape=(LAST_ATTACK_DIM,), dtype=np.float32),
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
    if with_world_state:
        spaces_map["world_state"] = spaces.Box(
            0.0, 1.0, shape=(WORLD_STATE_DIM,), dtype=np.float32
        )
    if with_key_hints:
        spaces_map["key_hints"] = spaces.Box(
            0.0, 1.0, shape=(KEY_HINTS_DIM,), dtype=np.float32
        )
    return spaces.Dict(spaces_map)


def _fake_batch(obs_space: spaces.Dict, batch: int = 4) -> dict[str, torch.Tensor]:
    batch_obs: dict[str, torch.Tensor] = {}
    for key, sub in obs_space.spaces.items():
        sample = np.asarray(sub.sample(), dtype=sub.dtype if sub.dtype != object else np.float32)
        stacked = np.stack([sample for _ in range(batch)], axis=0)
        batch_obs[key] = torch.as_tensor(stacked)
    return batch_obs


def test_doc04_medium_extractor_forward_shape() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1Doc04MediumExtractor(obs_space, cnn_output_dim=512, project_root=PROJECT_ROOT)
    batch = _fake_batch(obs_space)
    out = extractor(batch)
    assert out.shape == (4, FEATURES_DIM)
    assert extractor.features_dim == FEATURES_DIM


def test_doc04_medium_tower_concat_width() -> None:
    assert TOWER_OUT_DIM == 1344


def test_doc04_medium_ignores_goal_and_affordances() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1Doc04MediumExtractor(obs_space, project_root=PROJECT_ROOT)
    batch_a = _fake_batch(obs_space)
    batch_b = {k: v.clone() for k, v in batch_a.items()}
    batch_b["goal"] = torch.zeros_like(batch_b["goal"])
    batch_b["affordances"] = torch.zeros_like(batch_b["affordances"])
    out_a = extractor(batch_a)
    out_b = extractor(batch_b)
    assert torch.allclose(out_a, out_b)


def test_reload_doc04_world_catalog_buffers() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1Doc04MediumExtractor(obs_space, project_root=PROJECT_ROOT)
    before = extractor.world_context.map_neighbors.clone()
    reload_doc04_world_catalog_buffers(extractor)
    after = extractor.world_context.map_neighbors
    assert after.shape == before.shape
    assert torch.equal(after.cpu(), before.cpu())


def test_ppo_accepts_doc04_medium_extractor() -> None:
    from stable_baselines3 import PPO

    class StubEnv(gym.Env):
        def __init__(self) -> None:
            super().__init__()
            self.observation_space = _stub_obs_space(with_world_state=True)
            self.action_space = spaces.Discrete(len(ACTION_NAMES))

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            return {k: np.asarray(s.sample()) for k, s in self.observation_space.spaces.items()}, {}

        def step(self, action):
            obs = {k: np.asarray(s.sample()) for k, s in self.observation_space.spaces.items()}
            return obs, 0.0, False, False, {}

    model = PPO(
        "MultiInputPolicy",
        StubEnv(),
        policy_kwargs=POLICY_KWARGS,
        n_steps=32,
        batch_size=16,
        n_epochs=1,
        device="cpu",
        verbose=0,
    )
    assert model.policy.features_dim == FEATURES_DIM
    model.learn(total_timesteps=32)
