"""Episode-segment Monte Carlo returns on the learner."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.learner_train import (
    _normalize_advantages_safe,
    compute_dual_gamma_mc_returns,
    compute_episode_mc_returns,
)
from re1_rl.reward import SOFTLOCK_GAMMA


def test_mc_single_complete_episode():
    rewards = np.array([[1.0], [1.0], [1.0]], dtype=np.float32)
    dones = np.array([[False], [False], [True]], dtype=np.bool_)
    values = np.zeros_like(rewards)
    last_values = np.array([0.0], dtype=np.float32)
    returns, advantages = compute_episode_mc_returns(
        rewards, dones, values, last_values, gamma=0.9
    )
    assert returns[2, 0] == pytest.approx(1.0)
    assert returns[1, 0] == pytest.approx(1.0 + 0.9 * 1.0)
    assert returns[0, 0] == pytest.approx(1.0 + 0.9 * (1.0 + 0.9 * 1.0))


def test_mc_bootstrap_incomplete_rollout():
    rewards = np.array([[1.0], [1.0]], dtype=np.float32)
    dones = np.array([[False], [False]], dtype=np.bool_)
    values = np.zeros_like(rewards)
    last_values = np.array([5.0], dtype=np.float32)
    returns, _ = compute_episode_mc_returns(
        rewards, dones, values, last_values, gamma=0.5
    )
    assert returns[1, 0] == pytest.approx(1.0 + 0.5 * 5.0)
    assert returns[0, 0] == pytest.approx(1.0 + 0.5 * returns[1, 0])


def test_dual_gamma_softlock_spread_sums_over_horizon():
    """Main channel γ=0.99; softlock spread per step @ γ=1 sums to the lump."""
    n = 3
    lump = -1.0
    per = lump / n
    rewards = np.full((n, 1), per, dtype=np.float32)
    softlock = np.full((n, 1), per, dtype=np.float32)
    dones = np.array([[False], [False], [True]], dtype=np.bool_)
    values = np.zeros_like(rewards)
    last_values = np.array([0.0], dtype=np.float32)
    returns, _ = compute_dual_gamma_mc_returns(
        rewards,
        softlock,
        dones,
        values,
        last_values,
        gamma_main=0.99,
        gamma_softlock=SOFTLOCK_GAMMA,
    )
    assert returns[0, 0] == pytest.approx(lump)
    assert returns[2, 0] == pytest.approx(per)


def test_normalize_advantages_safe_single_element_is_zero():
    adv = np.array([[3.0]], dtype=np.float32)
    out = _normalize_advantages_safe(adv)
    assert out.shape == adv.shape
    assert out[0, 0] == pytest.approx(0.0)


def test_normalize_advantages_safe_population_std():
    adv = np.array([[1.0], [3.0]], dtype=np.float32)
    out = _normalize_advantages_safe(adv)
    assert out[0, 0] == pytest.approx(-1.0)
    assert out[1, 0] == pytest.approx(1.0)
