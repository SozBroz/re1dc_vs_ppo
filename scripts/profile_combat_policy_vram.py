"""Compare Doc04-medium vs combat-efficient peak VRAM for one learner train step.

Runs a synthetic batch at batch_size=2048 on CUDA when available; falls back to
parameter/activation estimates on CPU.

Usage:
    python scripts/profile_combat_policy_vram.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
from gymnasium import spaces

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _stub_obs_space():
    from tests.test_doc04_medium_extractor import _stub_obs_space as stub

    return stub(with_world_state=True)


def _count_params(model) -> int:
    return sum(p.numel() for p in model.policy.parameters())


def _profile_one(label: str, model, batch_size: int, n_actions: int) -> dict:
    device = model.device
    obs_space = model.observation_space
    assert isinstance(obs_space, spaces.Dict)
    batch = {}
    for key, sub in obs_space.spaces.items():
        sample = np.asarray(sub.sample())
        stacked = np.stack([sample for _ in range(batch_size)], axis=0)
        batch[key] = torch.as_tensor(stacked, device=device)
        if key == "frame" and batch[key].dtype != torch.float32:
            batch[key] = batch[key].float()

    actions = torch.randint(0, n_actions, (batch_size,), device=device)
    masks = torch.ones(batch_size, n_actions, dtype=torch.bool, device=device)

    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
        torch.cuda.synchronize(device)

    model.policy.set_training_mode(True)
    values, log_prob, entropy = model.policy.evaluate_actions(batch, actions, action_masks=masks)
    loss = values.mean() + log_prob.mean()
    if entropy is not None:
        loss = loss + entropy.mean()
    model.policy.optimizer.zero_grad()
    loss.backward()
    if device.type == "cuda":
        torch.cuda.synchronize(device)
        alloc = torch.cuda.max_memory_allocated(device)
        reserved = torch.cuda.max_memory_reserved(device)
    else:
        alloc = reserved = 0

    return {
        "label": label,
        "params": _count_params(model),
        "device": str(device),
        "batch_size": batch_size,
        "peak_allocated_bytes": int(alloc),
        "peak_reserved_bytes": int(reserved),
        "peak_allocated_mib": alloc / (1024**2),
        "peak_reserved_mib": reserved / (1024**2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=2048)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    from sb3_contrib import MaskablePPO

    from re1_rl.combat_efficient_extractor import FEATURES_DIM as CE_DIM
    from re1_rl.combat_efficient_extractor import PARAM_HARD_CAP, RE1CombatEfficientExtractor
    from re1_rl.combat_ppo import CombatEfficientPPO
    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.doc04_medium_extractor import FEATURES_DIM as DOC_DIM
    from re1_rl.doc04_medium_extractor import RE1Doc04MediumExtractor
    from re1_rl.env import ACTION_NAMES

    obs = _stub_obs_space()
    act = spaces.Discrete(len(ACTION_NAMES))
    env = _SpaceHolderEnv(obs, act)
    device = args.device

    doc = MaskablePPO(
        "MultiInputPolicy",
        env,
        policy_kwargs=dict(
            net_arch=dict(pi=[512, 512], vf=[512, 512]),
            features_extractor_class=RE1Doc04MediumExtractor,
            features_extractor_kwargs=dict(cnn_output_dim=512, features_dim=DOC_DIM),
        ),
        n_steps=64,
        batch_size=64,
        n_epochs=1,
        device=device,
        verbose=0,
    )
    ce = CombatEfficientPPO(
        "MultiInputPolicy",
        env,
        policy_kwargs=dict(
            net_arch=dict(pi=[512, 512], vf=[512, 512]),
            features_extractor_class=RE1CombatEfficientExtractor,
            features_extractor_kwargs=dict(cnn_output_dim=512, features_dim=CE_DIM),
        ),
        n_steps=64,
        batch_size=64,
        n_epochs=1,
        device=device,
        verbose=0,
        gae_lambda=1.0,
    )

    # Smaller synthetic batch if CPU / limited VRAM for smoke; still reports params.
    bs = args.batch_size
    if device == "cpu":
        bs = min(bs, 256)

    r_doc = _profile_one("doc04_medium", doc, bs, len(ACTION_NAMES))
    r_ce = _profile_one("combat_efficient", ce, bs, len(ACTION_NAMES))
    print(r_doc)
    print(r_ce)
    print(f"param_delta={r_ce['params'] - r_doc['params']} hard_cap={PARAM_HARD_CAP}")
    if r_ce["params"] > PARAM_HARD_CAP:
        raise SystemExit(f"FAIL: combat-efficient params {r_ce['params']} > {PARAM_HARD_CAP}")
    if device.startswith("cuda") and r_doc["peak_reserved_bytes"] > 0:
        growth = r_ce["peak_reserved_bytes"] / r_doc["peak_reserved_bytes"]
        print(f"reserved_vram_ratio={growth:.4f}")
        if growth > 1.05:
            print("WARN: reserved VRAM grew >5% vs Doc04; consider shrinking aux/gates")


if __name__ == "__main__":
    main()
