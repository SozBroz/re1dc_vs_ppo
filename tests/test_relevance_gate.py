"""Tests for stale-rollout π_new/π_old relevance gate."""

from __future__ import annotations

import queue
import sys
from pathlib import Path

import numpy as np
import pytest
import torch
from sb3_contrib import MaskablePPO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.learner_server import LearnerState
from re1_rl.distributed.learner_train import train_on_rollouts
from re1_rl.distributed.relevance_gate import (
    RelevanceGateConfig,
    compute_new_log_probs,
    filter_stale_rollouts,
    is_version_stale_for_gate,
    rollout_passes_relevance_gate,
    transition_relevance_mask,
)
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.spaces import make_re1_policy_spaces, make_re1_spaces
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.distributed.weights import _SpaceHolderEnv
from re1_rl.env import ACTION_NAMES
from re1_rl.policy_config import POLICY_KWARGS

N_ACTIONS = len(ACTION_NAMES)


def _tiny_model() -> MaskablePPO:
    obs_space, act_space = make_re1_policy_spaces()
    return MaskablePPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(obs_space, act_space),
        policy_kwargs=POLICY_KWARGS,
        n_steps=8,
        batch_size=8,
        n_epochs=1,
        device="cpu",
        verbose=0,
    )


def _fake_rollout(
    n_steps: int = 4,
    n_envs: int = 2,
    version: int = 1,
    *,
    log_probs: np.ndarray | None = None,
) -> WorkerRollout:
    obs_space, _ = make_re1_spaces()
    obs = {
        key: np.zeros((n_steps, n_envs, *space.shape), dtype=space.dtype)
        for key, space in obs_space.spaces.items()
    }
    obs["frame"] = np.random.randint(
        0, 255, (n_steps, n_envs, *obs_space["frame"].shape), dtype=np.uint8
    )
    masks = np.ones((n_steps, n_envs, N_ACTIONS), dtype=np.bool_)
    masks[..., N_ACTIONS // 2 :] = False
    lp = (
        log_probs
        if log_probs is not None
        else np.full((n_steps, n_envs), -1.0, dtype=np.float32)
    )
    return WorkerRollout(
        worker_id="w",
        policy_version=version,
        n_envs=n_envs,
        n_steps=n_steps,
        obs=obs,
        actions=np.random.randint(0, N_ACTIONS // 2, (n_steps, n_envs), dtype=np.int64),
        rewards=np.zeros((n_steps, n_envs), dtype=np.float32),
        dones=np.zeros((n_steps, n_envs), dtype=np.bool_),
        values=np.zeros((n_steps, n_envs), dtype=np.float32),
        log_probs=lp,
        last_values=np.zeros((n_envs,), dtype=np.float32),
        action_masks=masks,
    )


def test_transition_relevance_mask_band_and_floor() -> None:
    # ρ = exp(new - old): equal -> 1.0 (keep); far -> drop; NaN -> drop
    old = np.array([-1.0, -1.0, -1.0, np.nan], dtype=np.float64)
    new = np.array([-1.0, -1.0 + np.log(3.0), -40.0, -1.0], dtype=np.float64)
    mask = transition_relevance_mask(old, new, ratio_clip=2.0, prob_floor=1e-8)
    assert mask.tolist() == [True, False, False, False]


def test_is_version_stale_for_gate() -> None:
    assert not is_version_stale_for_gate(5, 5, 1)
    assert not is_version_stale_for_gate(4, 5, 1)
    assert is_version_stale_for_gate(3, 5, 1)


def test_accept_rollout_soft_queues_stale_when_gate_enabled() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(
        store,
        q,
        machine_name="t",
        max_staleness=1,
        relevance_gate=True,
        relevance_max_age=8,
    )
    state.set_current_version(10)
    ok, reason = state.accept_rollout(_fake_rollout(version=5))
    assert ok is True
    assert reason == "stale_queued_for_relevance_gate"
    assert state.rollouts_stale_queued == 1
    assert q.qsize() == 1


def test_accept_rollout_still_hard_rejects_ancient_and_when_gate_off() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(
        store,
        q,
        machine_name="t",
        max_staleness=1,
        relevance_gate=False,
    )
    state.set_current_version(10)
    ok, reason = state.accept_rollout(_fake_rollout(version=5))
    assert ok is False
    assert reason == "stale_policy_version"

    state.relevance_gate = True
    state.relevance_max_age = 2
    ok2, reason2 = state.accept_rollout(_fake_rollout(version=5))
    assert ok2 is False
    assert reason2 == "stale_policy_version"


def test_compute_new_log_probs_finite_with_masks() -> None:
    model = _tiny_model()
    rollout = _fake_rollout()
    # Align old log_probs with current policy so ratios ≈ 1.
    new_lp = compute_new_log_probs(model, rollout, microbatch=4)
    assert new_lp.shape == (rollout.n_steps, rollout.n_envs)
    assert np.isfinite(new_lp).all()


def test_filter_keeps_aligned_stale_drops_garbage() -> None:
    model = _tiny_model()
    good = _fake_rollout(version=1)
    good.log_probs = compute_new_log_probs(model, good)

    bad = _fake_rollout(version=1)
    bad.log_probs = np.full_like(bad.log_probs, -50.0)  # ρ >> clip vs π_new

    fresh = _fake_rollout(version=5)
    fresh.log_probs = np.full_like(fresh.log_probs, -50.0)  # ignored (not stale)

    kept, stats, details = filter_stale_rollouts(
        model,
        [good, bad, fresh],
        current_policy_version=5,
        max_staleness=1,
        config=RelevanceGateConfig(ratio_clip=2.0, keep_frac=0.5, prob_floor=1e-8),
    )
    assert stats.considered == 2
    assert stats.kept == 1
    assert stats.dropped == 1
    assert len(kept) == 2  # good stale + fresh
    kept_ids = {id(r) for r in kept}
    assert id(good) in kept_ids
    assert id(fresh) in kept_ids
    assert id(bad) not in kept_ids
    assert details[0]["keep"] is True
    assert details[1]["keep"] is False


def test_train_on_rollouts_relevance_gate_drops_irrelevant_stale() -> None:
    model = _tiny_model()
    before = model.num_timesteps
    fresh = _fake_rollout(n_steps=4, n_envs=2, version=5)
    # Make fresh log_probs match current policy so train is valid.
    fresh.log_probs = compute_new_log_probs(model, fresh)

    stale_bad = _fake_rollout(n_steps=4, n_envs=2, version=1)
    stale_bad.log_probs = np.full_like(stale_bad.log_probs, -80.0)

    steps = train_on_rollouts(
        model,
        [fresh, stale_bad],
        current_policy_version=5,
        max_staleness=1,
        relevance_gate=True,
        relevance_config=RelevanceGateConfig(ratio_clip=2.0, keep_frac=0.5),
    )
    # Only fresh (8 env-steps) should train; stale_bad dropped.
    assert steps == 8
    assert model.num_timesteps == before + 8


def test_rollout_passes_fails_closed_on_nan_old_logprobs() -> None:
    model = _tiny_model()
    rollout = _fake_rollout()
    rollout.log_probs = np.full_like(rollout.log_probs, np.nan)
    keep, detail = rollout_passes_relevance_gate(
        model,
        rollout,
        config=RelevanceGateConfig(keep_frac=0.5),
    )
    assert keep is False
    assert detail["transitions_pass"] == 0
