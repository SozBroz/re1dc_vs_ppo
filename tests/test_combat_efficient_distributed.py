"""Masked logprob parity + merge/train-with-aux smoke for combat-efficient PPO.

Keeps A's post-step-info target alignment and mixed-rollout empty-fill behavior.
Does not use obs[t+1] target generation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.combat_efficient_extractor import PARAM_HARD_CAP
from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION, ATTACK_UP_ACTION
from re1_rl.combat_ppo import CombatEfficientPPO
from re1_rl.combat_targets import (
    COMBAT_TARGET_DIM,
    WORLD_EVENT_DIM,
    empty_combat_target,
    empty_world_event_mask,
    empty_world_event_target,
    pack_combat_target,
)
from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.learner_train import merge_rollouts, train_on_rollouts
from re1_rl.distributed.obs_preprocess import prepare_obs_for_policy
from re1_rl.distributed.rollout_codec import decode_rollout, encode_rollout
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.spaces import make_re1_policy_spaces
from re1_rl.distributed.weights import (
    _SpaceHolderEnv,
    export_policy_state_dict,
    load_policy_weights,
    policy_bytes_from_state_dict,
    state_dict_from_policy_bytes,
)
from re1_rl.env import ACTION_NAMES
from re1_rl.policy_config import AUX_COEF, POLICY_KWARGS, USE_GROUPED_ENTROPY

N_ACTIONS = len(ACTION_NAMES)


def _fake_rollout(
    n_steps: int = 8,
    n_envs: int = 2,
    version: int = 1,
    *,
    with_aux: bool = True,
) -> WorkerRollout:
    obs_space, _ = make_re1_policy_spaces()
    obs = {
        key: np.zeros((n_steps, n_envs, *space.shape), dtype=space.dtype)
        for key, space in obs_space.spaces.items()
    }
    obs["frame"] = np.random.randint(
        0, 255, (n_steps, n_envs, *obs_space["frame"].shape), dtype=np.uint8
    )
    masks = np.ones((n_steps, n_envs, N_ACTIONS), dtype=np.bool_)
    masks[..., N_ACTIONS // 2 :] = False
    actions = np.random.randint(0, N_ACTIONS // 2, (n_steps, n_envs), dtype=np.int64)
    combat = None
    world = None
    wmask = None
    if with_aux:
        combat = np.zeros((n_steps, n_envs, COMBAT_TARGET_DIM), dtype=np.float32)
        world = np.zeros((n_steps, n_envs, WORLD_EVENT_DIM), dtype=np.float32)
        wmask = np.zeros((n_steps, n_envs, WORLD_EVENT_DIM), dtype=np.float32)
        for t in range(n_steps):
            for e in range(n_envs):
                if int(actions[t, e]) in (
                    ATTACK_UP_ACTION,
                    ATTACK_ACTION,
                    ATTACK_DOWN_ACTION,
                ):
                    combat[t, e] = pack_combat_target(
                        action_id=int(actions[t, e]),
                        hit=True,
                        damage=8,
                        ammo_spent=1,
                    )
                else:
                    combat[t, e] = empty_combat_target()
                world[t, e] = empty_world_event_target()
                wmask[t, e] = empty_world_event_mask()
    return WorkerRollout(
        worker_id="w",
        policy_version=version,
        n_envs=n_envs,
        n_steps=n_steps,
        obs=obs,
        actions=actions,
        rewards=np.random.randn(n_steps, n_envs).astype(np.float32),
        dones=np.zeros((n_steps, n_envs), dtype=np.bool_),
        values=np.random.randn(n_steps, n_envs).astype(np.float32),
        log_probs=np.random.randn(n_steps, n_envs).astype(np.float32) * 0.01,
        last_values=np.random.randn(n_envs).astype(np.float32),
        action_masks=masks,
        combat_targets=combat,
        world_event_targets=world,
        world_event_masks=wmask,
    )


def _tiny_combat_model() -> CombatEfficientPPO:
    obs_space, act_space = make_re1_policy_spaces()
    return CombatEfficientPPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(obs_space, act_space),
        policy_kwargs=POLICY_KWARGS,
        n_steps=8,
        batch_size=8,
        n_epochs=1,
        device="cpu",
        verbose=0,
        aux_coef=AUX_COEF,
        use_grouped_entropy=USE_GROUPED_ENTROPY,
        gae_lambda=1.0,
    )


def test_codec_roundtrip_with_aux() -> None:
    original = _fake_rollout()
    restored = decode_rollout(encode_rollout(original))
    assert restored.combat_targets is not None
    assert np.allclose(restored.combat_targets, original.combat_targets)
    assert restored.world_event_masks is not None
    assert np.array_equal(restored.world_event_masks, original.world_event_masks)


def test_merge_and_train_with_aux() -> None:
    model = _tiny_combat_model()
    before = sum(p.numel() for p in model.policy.parameters())
    assert before <= PARAM_HARD_CAP
    steps = train_on_rollouts(model, [_fake_rollout()])
    assert steps == 16
    assert sum(p.numel() for p in model.policy.parameters()) == before
    for p in model.policy.parameters():
        assert torch.isfinite(p).all()


def test_merge_mixed_rollouts_empty_fills_missing_aux() -> None:
    """Rollouts without aux arrays get empty targets; merge stays finite."""
    with_aux = _fake_rollout(n_envs=1, with_aux=True)
    without = _fake_rollout(n_envs=2, with_aux=False)
    merged = merge_rollouts([with_aux, without])
    assert merged["combat_targets"].shape == (8, 3, COMBAT_TARGET_DIM)
    assert merged["world_event_targets"].shape == (8, 3, WORLD_EVENT_DIM)
    # Env 0 came from with_aux; envs 1-2 from empty fill (mask channel 0).
    assert float(merged["combat_targets"][:, 1:, -1].max()) == 0.0


def test_state_dict_serialization_parity() -> None:
    model = _tiny_combat_model()
    blob = policy_bytes_from_state_dict(export_policy_state_dict(model))
    other = _tiny_combat_model()
    load_policy_weights(other, state_dict_from_policy_bytes(blob))
    a = export_policy_state_dict(model)
    b = export_policy_state_dict(other)
    assert a.keys() == b.keys()
    for k in a:
        assert torch.equal(a[k], b[k])


def test_masked_logprob_inference_train_parity() -> None:
    model = _tiny_combat_model()
    obs_space, act_space = make_re1_policy_spaces()
    policy = InferencePolicy(obs_space, act_space, "cpu")
    policy.load_from_state_dict(export_policy_state_dict(model), policy_version=1)

    n_envs = 2
    obs = {
        k: np.zeros((n_envs, *space.shape), dtype=space.dtype)
        for k, space in obs_space.spaces.items()
    }
    obs["frame"] = np.random.randint(
        0, 255, (n_envs, *obs_space["frame"].shape), dtype=np.uint8
    )
    masks = np.ones((n_envs, N_ACTIONS), dtype=bool)
    masks[:, N_ACTIONS // 2 :] = False
    actions, _values, log_probs = policy.predict_masked_batch(obs, masks)

    prepared = prepare_obs_for_policy(obs, model.observation_space)
    obs_t = {k: torch.as_tensor(v) for k, v in prepared.items()}
    act_t = torch.as_tensor(actions)
    mask_t = torch.as_tensor(masks)
    _v, lp, _e = model.policy.evaluate_actions(obs_t, act_t, action_masks=mask_t)
    assert np.allclose(log_probs, lp.detach().cpu().numpy(), atol=1e-5)
