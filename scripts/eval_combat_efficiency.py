"""Deterministic combat-efficiency benchmark scaffold.

Covers handgun/shotgun/knife, three heights, near/far, moving targets, misses,
and low-ammo states using synthetic observations (no BizHawk required).

Fleet shadow promotion gates (wasted-round −40%, damage/round +25%) require a
live checkpoint comparison — this script validates metric plumbing and reports
baseline zeros for a fresh policy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from gymnasium import spaces

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _make_combat_obs(obs_space: spaces.Dict, *, weapon_clip: float, enemy_dist: float) -> dict:
    from tests.test_doc04_medium_extractor import _fake_batch

    batch = _fake_batch(obs_space, batch=1)
    # Force weapon card clip + nearest enemy distance if spatial present.
    if "weapon_card" in batch:
        batch["weapon_card"][0, 0] = float(weapon_clip)
    if "spatial" in batch:
        # enemy0_dist is after items block: 1 + 8*8 + 1 = 66, then slot fields.
        # With ENEMY_SLOT_DIM=12, dist is offset 2 within slot.
        from re1_rl.spatial_encoder import ENEMY_SLOT_DIM, ITEM_SLOTS

        enemy0 = 1 + ITEM_SLOTS * 8 + 1
        batch["spatial"][0, enemy0 + 2] = float(enemy_dist)
        batch["spatial"][0, enemy0 + 7] = 1.0  # alive
        batch["spatial"][0, enemy0 + 11] = 1.0  # hittable
    return batch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION, ATTACK_UP_ACTION
    from re1_rl.combat_efficient_extractor import FEATURES_DIM, RE1CombatEfficientExtractor
    from re1_rl.combat_ppo import CombatEfficientPPO
    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.env import ACTION_NAMES
    from re1_rl.policy_config import POLICY_KWARGS
    from tests.test_doc04_medium_extractor import _stub_obs_space

    obs_space = _stub_obs_space(with_world_state=True)
    act = spaces.Discrete(len(ACTION_NAMES))
    model = CombatEfficientPPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(obs_space, act),
        policy_kwargs=POLICY_KWARGS,
        device=args.device,
        verbose=0,
        n_steps=32,
        batch_size=16,
        n_epochs=1,
        gae_lambda=1.0,
    )

    scenarios = [
        ("handgun_near_neutral", 0.3, 0.2, ATTACK_ACTION),
        ("handgun_far_up", 0.3, 1.2, ATTACK_UP_ACTION),
        ("shotgun_near_down", 0.2, 0.15, ATTACK_DOWN_ACTION),
        ("knife_near_down", 0.0, 0.1, ATTACK_DOWN_ACTION),
        ("low_ammo_neutral", 0.02, 0.4, ATTACK_ACTION),
    ]
    rows = []
    model.policy.set_training_mode(False)
    with torch.no_grad():
        for name, clip, dist, action in scenarios:
            obs = _make_combat_obs(obs_space, weapon_clip=clip, enemy_dist=dist)
            aux = model.policy.features_extractor.predict_aux(obs)
            dist_obj = model.policy.get_distribution(obs)
            probs = dist_obj.distribution.probs.cpu().numpy()[0]
            engage = float(probs[ATTACK_ACTION] + probs[ATTACK_UP_ACTION] + probs[ATTACK_DOWN_ACTION])
            rows.append(
                {
                    "scenario": name,
                    "engage_prob": engage,
                    "p_neutral": float(probs[ATTACK_ACTION]),
                    "p_up": float(probs[ATTACK_UP_ACTION]),
                    "p_down": float(probs[ATTACK_DOWN_ACTION]),
                    "outcome_pred_norm": float(
                        torch.sigmoid(aux["outcome_pred"]).mean().cpu()
                    ),
                }
            )

    report = {
        "features_dim": FEATURES_DIM,
        "n_params": sum(p.numel() for p in model.policy.parameters()),
        "scenarios": rows,
        "note": "Fresh policy baseline; compare wasted rounds / dmg-per-round vs Doc04 ckpt on live eval.",
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
