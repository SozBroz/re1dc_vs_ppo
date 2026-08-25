"""Section 3 modality diagnostics / FiLM / ModDrop unit tests."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.combat_efficient_extractor import (
    FEATURES_DIM,
    GOAL_TOWER_DIM,
    RE1CombatEfficientExtractor,
    TOWER_OUT_DIM,
)
from re1_rl.modality_ablations import (
    MOD_DROP_BRANCHES,
    MOD_DROP_DIM,
    IdentityFiLM,
    ModDropEpisodeState,
    apply_mod_drop_to_parts,
    build_discriminative_param_groups,
    freeze_modules_for_n_updates,
    full_keep_mask,
    resolve_extractor_modules,
    sample_mod_drop_mask,
    tower_slices,
)
from re1_rl.modality_diagnostics import (
    activation_rms,
    compute_tower_diagnostics,
    effective_rank,
    relu_dormant_fraction,
    run_modality_diagnostics,
)
from tests.test_doc04_medium_extractor import _fake_batch, _stub_obs_space


def test_diagnostic_compute_on_dummy_batch() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1CombatEfficientExtractor(
        obs_space, project_root=PROJECT_ROOT, goal_film=False, mod_drop=False
    )
    batch = _fake_batch(obs_space, batch=8)
    stats = compute_tower_diagnostics(extractor, batch)
    assert "modality/goal_rms" in stats
    assert "modality/vision_eff_rank" in stats
    assert "modality/world_dormant" in stats
    assert stats["modality/goal_rms"] >= 0.0
    assert 0.0 <= stats["modality/vision_dormant"] <= 1.0
    assert effective_rank(torch.randn(8, 48)) > 0.0
    assert activation_rms(torch.ones(2, 4)) > 0.0
    assert relu_dormant_fraction(torch.tensor([[-1.0, 1.0]])) == 0.5


def test_tower_slices_cover_concat() -> None:
    slices = tower_slices(persistent_enabled=True)
    assert max(s.stop for s in slices.values()) == TOWER_OUT_DIM
    assert "goal" in slices
    goal = slices["goal"]
    assert goal.stop - goal.start == GOAL_TOWER_DIM


def test_film_identity_init_near_zero_policy_kl() -> None:
    """Identity FiLM at init must leave features (hence logits) unchanged vs pre-FiLM."""
    obs_space = _stub_obs_space(with_world_state=True)
    batch = _fake_batch(obs_space, batch=4)

    base = RE1CombatEfficientExtractor(
        obs_space, project_root=PROJECT_ROOT, goal_film=False, mod_drop=False
    )
    film = RE1CombatEfficientExtractor(
        obs_space, project_root=PROJECT_ROOT, goal_film=True, mod_drop=False
    )
    # Copy shared weights so only FiLM modules differ (identity → no-op).
    base_sd = base.state_dict()
    film_sd = film.state_dict()
    shared = {k: v for k, v in base_sd.items() if k in film_sd and "goal_film" not in k}
    film.load_state_dict({**film_sd, **shared}, strict=False)

    with torch.no_grad():
        h0 = base(batch)
        h1 = film(batch)
        # Build a tiny shared head to turn features into logits for KL.
        head = torch.nn.Linear(FEATURES_DIM, 45)
        logits0 = head(h0)
        logits1 = head(h1)
        log_p = F.log_softmax(logits0, dim=-1)
        log_q = F.log_softmax(logits1, dim=-1)
        kl = (log_p.exp() * (log_p - log_q)).sum(dim=-1).mean().item()
        feat_err = (h0 - h1).abs().max().item()

    assert feat_err < 1e-5, f"FiLM identity feature drift {feat_err}"
    assert kl < 1e-6, f"FiLM identity policy KL {kl}"

    # Direct module identity: γ=1, β=0
    module = IdentityFiLM(48, 16)
    h = torch.randn(3, 16)
    g = torch.randn(3, 48)
    out = module(h, g)
    assert torch.allclose(out, h, atol=1e-6)


def test_mod_drop_mask_consistency_collect_train() -> None:
    """Same stored mask → same features across 'PPO epochs'; drop changes features."""
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1CombatEfficientExtractor(
        obs_space, project_root=PROJECT_ROOT, goal_film=False, mod_drop=True
    )
    batch = _fake_batch(obs_space, batch=4)
    mask = full_keep_mask(4)
    mask[0, MOD_DROP_BRANCHES.index("vision")] = 0.0  # drop vision on row 0
    mask[1, MOD_DROP_BRANCHES.index("history")] = 0.0

    extractor.set_mod_drop_batch(torch.as_tensor(mask))
    with torch.no_grad():
        epoch0 = extractor(batch).clone()
        epoch1 = extractor(batch).clone()  # simulate second PPO epoch, same mask
    assert torch.allclose(epoch0, epoch1, atol=1e-6)

    keep = torch.as_tensor(full_keep_mask(4))
    extractor.set_mod_drop_batch(keep)
    with torch.no_grad():
        full = extractor(batch)
    # Dropped rows must differ from full-input; untouched rows match.
    assert not torch.allclose(epoch0[0], full[0], atol=1e-5)
    assert not torch.allclose(epoch0[1], full[1], atol=1e-5)
    assert torch.allclose(epoch0[2], full[2], atol=1e-5)
    assert torch.allclose(epoch0[3], full[3], atol=1e-5)

    # Goal never appears in droppable set.
    assert "goal" not in MOD_DROP_BRANCHES


def test_mod_drop_sampling_majority_full_and_episode_fixed() -> None:
    rng = np.random.default_rng(0)
    masks = sample_mod_drop_mask(200, rate=0.05, rng=rng)
    assert masks.shape == (200, MOD_DROP_DIM)
    n_dropped_rows = int(np.sum(np.any(masks < 0.5, axis=1)))
    assert 0 < n_dropped_rows < 40  # ~5% with slack
    # At most one branch dropped per row.
    assert int(np.max(np.sum(masks < 0.5, axis=1))) <= 1

    state = ModDropEpisodeState(n_envs=2, rate=1.0, rng=np.random.default_rng(1))
    m0 = state.masks.copy()
    state.on_dones([False, False])
    assert np.array_equal(state.masks, m0)
    state.on_dones([True, False])
    assert not np.array_equal(state.masks[0], m0[0])
    assert np.array_equal(state.masks[1], m0[1])


def test_apply_mod_drop_never_zeros_goal() -> None:
    parts = {
        "vision": torch.ones(2, 4),
        "goal": torch.ones(2, 3) * 3,
        "history": torch.ones(2, 2),
    }
    presence = torch.zeros(2, MOD_DROP_DIM)
    out = apply_mod_drop_to_parts(parts, presence)
    assert torch.equal(out["goal"], parts["goal"])
    assert torch.equal(out["vision"], torch.zeros_like(parts["vision"]))


def test_freeze_and_discriminative_lr_helpers() -> None:
    obs_space = _stub_obs_space(with_world_state=True)
    extractor = RE1CombatEfficientExtractor(
        obs_space, project_root=PROJECT_ROOT, goal_film=False, mod_drop=False
    )
    cnn = resolve_extractor_modules(extractor, ["cnn_extractor"])[0]
    handle = freeze_modules_for_n_updates([cnn], n_updates=2)
    assert all(not p.requires_grad for p in cnn.parameters())
    assert handle.tick(1) is True
    assert handle.tick(1) is False
    assert any(p.requires_grad for p in cnn.parameters())

    class _Pol(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.features_extractor = extractor
            self.head = torch.nn.Linear(8, 4)

    groups = build_discriminative_param_groups(_Pol(), base_lr=3e-4, mature_mult=0.2)
    lrs = sorted({g["lr"] for g in groups})
    assert min(lrs) < max(lrs)


def test_mod_drop_codec_roundtrip() -> None:
    from re1_rl.distributed.rollout_codec import decode_rollout, encode_rollout
    from re1_rl.distributed.rollout_types import WorkerRollout

    masks = np.stack(
        [sample_mod_drop_mask(2, rate=1.0, rng=np.random.default_rng(i)) for i in range(4)],
        axis=0,
    )
    rollout = WorkerRollout(
        worker_id="w0",
        policy_version=1,
        n_envs=2,
        n_steps=4,
        obs={"proprio": np.zeros((4, 2, 28), dtype=np.float32)},
        actions=np.zeros((4, 2), dtype=np.int64),
        rewards=np.zeros((4, 2), dtype=np.float32),
        dones=np.zeros((4, 2), dtype=np.bool_),
        values=np.zeros((4, 2), dtype=np.float32),
        log_probs=np.zeros((4, 2), dtype=np.float32),
        last_values=np.zeros((2,), dtype=np.float32),
        action_masks=np.ones((4, 2, 45), dtype=np.bool_),
        mod_drop_masks=masks,
    )
    decoded = decode_rollout(encode_rollout(rollout))
    assert decoded.mod_drop_masks is not None
    assert np.allclose(decoded.mod_drop_masks, masks)


def test_run_modality_diagnostics_smoke() -> None:
    """End-to-end diagnostic suite on a minimal policy-like wrapper."""
    from re1_rl.combat_ppo import CombatEfficientPPO
    from re1_rl.policy_config import POLICY_KWARGS
    from tests.test_combat_efficient_extractor import _StubEnv

    obs_space = _stub_obs_space(with_world_state=True)
    model = CombatEfficientPPO(
        "MultiInputPolicy",
        _StubEnv(obs_space),
        policy_kwargs=POLICY_KWARGS,
        n_steps=16,
        batch_size=8,
        n_epochs=1,
        device="cpu",
        verbose=0,
        gae_lambda=1.0,
    )
    batch = _fake_batch(obs_space, batch=4)
    # Move to device tensors already
    stats = run_modality_diagnostics(
        model,
        batch,
        counterfactual_towers=["goal", "vision"],
    )
    assert "modality/goal_rms" in stats
    assert "modality/cf_kl_goal" in stats
    assert "modality/grad_rms_fusion_interface" in stats
