"""Combat-efficient extractor: shapes, param cap, aux heads, named-state tower."""

from __future__ import annotations

import sys
from pathlib import Path

import gymnasium as gym
import numpy as np
import torch
from gymnasium import spaces

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.combat_efficient_extractor import (
    FEATURES_DIM,
    PARAM_HARD_CAP,
    PERSISTENT_STATE_DIM,
    RE1CombatEfficientExtractor,
    TOWER_OUT_DIM,
    count_extractor_params,
)
from re1_rl.combat_ppo import CombatEfficientPPO, combat_auxiliary_loss, grouped_entropy_from_logits
from re1_rl.combat_targets import (
    COMBAT_OUTCOME_DIM,
    combat_target_to_outcome_vector,
    pack_combat_target,
)
from re1_rl.env import ACTION_NAMES
from re1_rl.named_state import NAMED_STATE_DIM
from re1_rl.obs_encoder import GOAL_BASE_DIM, GOAL_DIM
from re1_rl.policy_config import POLICY_KWARGS
from tests.test_doc04_medium_extractor import _fake_batch, _stub_obs_space


def test_combat_efficient_forward_shape() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1CombatEfficientExtractor(
        obs_space, cnn_output_dim=512, project_root=PROJECT_ROOT
    )
    batch = _fake_batch(obs_space)
    out = extractor(batch)
    assert out.shape == (4, FEATURES_DIM)
    assert extractor.features_dim == FEATURES_DIM


def test_named_state_tower_enabled() -> None:
    assert PERSISTENT_STATE_DIM == NAMED_STATE_DIM == 64
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1CombatEfficientExtractor(obs_space, project_root=PROJECT_ROOT)
    assert extractor.persistent_encoder is not None
    assert TOWER_OUT_DIM == 1776
    assert extractor._tower_out_dim == TOWER_OUT_DIM


def test_named_state_absent_defaults_to_zeros() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1CombatEfficientExtractor(obs_space, project_root=PROJECT_ROOT)
    batch = _fake_batch(obs_space, batch=2)
    del batch["named_state"]
    out = extractor(batch)
    assert out.shape == (2, FEATURES_DIM)
    assert torch.isfinite(out).all()


def test_aux_heads_and_no_nan_grads() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1CombatEfficientExtractor(obs_space, project_root=PROJECT_ROOT)
    batch = _fake_batch(obs_space, batch=8)
    aux = extractor.predict_aux(batch)
    assert aux["outcome_pred"].shape == (8, COMBAT_OUTCOME_DIM)
    assert aux["combat_latent"].shape == (8, 128)
    target = pack_combat_target(
        action_id=8, hit=True, damage=10, kills=0, ammo_spent=1, knife=False
    )
    targets = np.stack([target for _ in range(8)], axis=0)
    loss, stats = combat_auxiliary_loss(aux["outcome_pred"], torch.as_tensor(targets))
    loss.backward()
    grad_norm = 0.0
    for p in extractor.parameters():
        if p.grad is not None:
            assert torch.isfinite(p.grad).all()
            grad_norm += float(p.grad.norm().cpu())
    assert grad_norm > 0.0
    assert "train/aux_loss" in stats


def test_continuous_aux_uses_sigmoid_before_huber() -> None:
    """Huge positive logits for continuous channels must not explode Huber."""
    pred = torch.zeros(2, COMBAT_OUTCOME_DIM, requires_grad=True)
    # Neutral height continuous slots: damage (2), ammo (4)
    with torch.no_grad():
        pred[:, 2] = 50.0
        pred[:, 4] = 50.0
    t = pack_combat_target(action_id=8, hit=True, damage=10, ammo_spent=1)
    targets = np.stack([t, t], axis=0)
    loss, _ = combat_auxiliary_loss(pred, torch.as_tensor(targets))
    assert torch.isfinite(loss)
    assert float(loss) < 10.0


def test_param_cap() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    model = CombatEfficientPPO(
        "MultiInputPolicy",
        _StubEnv(obs_space),
        policy_kwargs=POLICY_KWARGS,
        n_steps=32,
        batch_size=16,
        n_epochs=1,
        device="cpu",
        verbose=0,
        gae_lambda=1.0,
    )
    n = sum(p.numel() for p in model.policy.parameters())
    assert n <= PARAM_HARD_CAP, f"params {n} exceed hard cap {PARAM_HARD_CAP}"
    assert count_extractor_params(model.policy.features_extractor) < n


def test_outcome_mask_only_executed_height() -> None:
    t = pack_combat_target(action_id=6, hit=True, damage=5, ammo_spent=1)  # up
    y, m = combat_target_to_outcome_vector(t)
    assert m[:6].sum() == 0
    assert m[6:12].sum() == 6
    assert m[12:].sum() == 0
    assert y[6] == 1.0  # hit on up


def test_grouped_entropy_finite() -> None:
    logits = torch.randn(16, len(ACTION_NAMES))
    masks = torch.ones(16, len(ACTION_NAMES), dtype=torch.bool)
    masks[:, 0] = False
    ent = grouped_entropy_from_logits(logits, masks)
    assert ent.shape == (16,)
    assert torch.isfinite(ent).all()


def test_consumes_goal_but_ignores_legacy_affordances() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1CombatEfficientExtractor(obs_space, project_root=PROJECT_ROOT)
    a = _fake_batch(obs_space)
    b = {k: v.clone() for k, v in a.items()}
    b["affordances"] = torch.zeros_like(b["affordances"])
    assert torch.allclose(extractor(a), extractor(b))
    c = {k: v.clone() for k, v in a.items()}
    c["goal"] = torch.zeros_like(c["goal"])
    assert not torch.allclose(extractor(a), extractor(c))


def test_goal_tower_consumes_widened_lookahead() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1CombatEfficientExtractor(obs_space, project_root=PROJECT_ROOT)
    assert extractor.goal_mlp[0].in_features == GOAL_BASE_DIM
    a = _fake_batch(obs_space)
    b = {k: v.clone() for k, v in a.items()}
    b["goal"][:, GOAL_BASE_DIM:] = 0.0
    a["goal"][:, GOAL_BASE_DIM:] = 1.0
    with torch.no_grad():
        extractor.goal_lookahead_token[0].weight.fill_(0.1)
        extractor.goal_lookahead_token[0].bias.zero_()
        extractor.goal_lookahead_out.weight.fill_(0.1)
        extractor.goal_lookahead_out.bias.zero_()
    assert not torch.allclose(extractor(a), extractor(b))


class _StubEnv(gym.Env):
    def __init__(self, observation_space: spaces.Dict) -> None:
        super().__init__()
        self.observation_space = observation_space
        self.action_space = spaces.Discrete(len(ACTION_NAMES))

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        return {k: np.asarray(s.sample()) for k, s in self.observation_space.spaces.items()}, {}

    def step(self, action):
        obs = {k: np.asarray(s.sample()) for k, s in self.observation_space.spaces.items()}
        return obs, 0.0, False, False, {}
