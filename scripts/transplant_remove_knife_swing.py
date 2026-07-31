"""Shrink PPO action head 46 -> 45 by dropping knife_swing (old index 8).

Uses full RE1 policy obs spaces (via load_async_learner / make_re1_policy_spaces).

Usage:
    python scripts/transplant_remove_knife_swing.py
    python scripts/transplant_remove_knife_swing.py \\
        --src data/checkpoints/reward_tune_1040k/ppo_re1_181716050_steps.zip \\
        --out data/checkpoints/reward_tune_1040k/ppo_re1_181716050_act45
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

OLD_N_ACTIONS = 46
NEW_N_ACTIONS = 45
ROW_MAP = list(range(8)) + list(range(9, 46))


@torch.no_grad()
def narrow_action_head(old_policy, new_policy) -> None:
    old_sd = old_policy.state_dict()
    new_sd = new_policy.state_dict()

    for k, old_t in old_sd.items():
        if k.startswith("action_net."):
            continue
        if k in new_sd and old_t.shape == new_sd[k].shape:
            new_sd[k].copy_(old_t)

    ow = old_sd["action_net.weight"]
    ob = old_sd["action_net.bias"]
    nw = new_sd["action_net.weight"]
    nb = new_sd["action_net.bias"]
    for new_i, old_i in enumerate(ROW_MAP):
        nw[new_i].copy_(ow[old_i])
        nb[new_i].copy_(ob[old_i])

    new_policy.load_state_dict(new_sd)


@torch.no_grad()
def policy_logits(policy, obs_tensor: torch.Tensor) -> torch.Tensor:
    feats = policy.extract_features(obs_tensor)
    latent = policy.mlp_extractor.forward_actor(feats)
    return policy.action_net(latent)


@torch.no_grad()
def verify(old_model, new_model, n_batches: int = 4, batch: int = 8) -> float:
    from stable_baselines3.common.utils import obs_as_tensor

    from re1_rl.distributed.spaces import make_re1_policy_spaces

    obs_space, _ = make_re1_policy_spaces()
    worst = 0.0
    for _ in range(n_batches):
        obs = {
            k: np.stack([obs_space[k].sample() for _ in range(batch)])
            for k in obs_space.spaces
        }
        for model in (old_model, new_model):
            model.policy.set_training_mode(False)
        t = obs_as_tensor(obs, old_model.device)
        logits_old = policy_logits(old_model.policy, t)
        logits_new = policy_logits(new_model.policy, t)
        for new_i, old_i in enumerate(ROW_MAP):
            worst = max(
                worst,
                (logits_old[:, old_i] - logits_new[:, new_i]).abs().max().item(),
            )
        v_old = old_model.policy.predict_values(t)
        v_new = new_model.policy.predict_values(t)
        worst = max(worst, (v_old - v_new).abs().max().item())
    return worst


def main() -> int:
    from sb3_contrib import MaskablePPO

    from re1_rl.checkpoint_io import find_latest_checkpoint
    from re1_rl.distributed.spaces import make_re1_policy_spaces
    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.policy_config import POLICY_KWARGS

    ap = argparse.ArgumentParser()
    default_src = find_latest_checkpoint(PROJECT_ROOT / "data" / "checkpoints")
    if default_src is None:
        default_src = PROJECT_ROOT / "data" / "ppo_re1_final.zip"
    ap.add_argument("--src", default=str(default_src))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data" / "ppo_re1_act45"))
    ap.add_argument("--backup-src", action="store_true")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    if len(__import__("re1_rl.env", fromlist=["ACTION_NAMES"]).ACTION_NAMES) != NEW_N_ACTIONS:
        print(
            f"[transplant] expected {NEW_N_ACTIONS} actions in env, "
            f"got {len(__import__('re1_rl.env', fromlist=['ACTION_NAMES']).ACTION_NAMES)}",
            flush=True,
        )
        return 1

    src = Path(args.src)
    if not src.is_file():
        print(f"[transplant] missing {src}", flush=True)
        return 1

    print(f"[transplant] loading {src}", flush=True)
    old_model = MaskablePPO.load(str(src), device=str(args.device))
    old_n = int(old_model.action_space.n)
    if old_n != OLD_N_ACTIONS:
        print(f"[transplant] expected {OLD_N_ACTIONS} actions in ckpt, got {old_n}", flush=True)
        return 1
    print(
        f"[transplant] old steps={old_model.num_timesteps:,} actions={old_n}",
        flush=True,
    )

    policy_obs, act_space = make_re1_policy_spaces()
    if int(act_space.n) != NEW_N_ACTIONS:
        print(f"[transplant] policy act_space n={act_space.n} != {NEW_N_ACTIONS}", flush=True)
        return 1

    new_model = MaskablePPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs, act_space),
        policy_kwargs=POLICY_KWARGS,
        n_steps=256,
        batch_size=512,
        n_epochs=4,
        learning_rate=3e-4,
        gamma=0.99,
        ent_coef=0.01,
        device=str(args.device),
    )
    narrow_action_head(old_model.policy, new_model.policy)
    new_model.num_timesteps = old_model.num_timesteps

    worst = verify(old_model, new_model)
    print(f"[transplant] max logit/value drift on kept rows: {worst:.3e}", flush=True)
    if worst > 1e-4:
        print("[transplant] FAIL: narrowed net does not reproduce kept logits", flush=True)
        return 1

    if args.backup_src:
        bak = src.with_name(src.stem + "_pre_act45.zip")
        shutil.copy2(src, bak)
        print(f"[transplant] backed up src -> {bak}", flush=True)

    out_base = Path(str(args.out))
    if Path(str(args.out) + ".zip").is_file():
        shutil.copy2(Path(str(args.out) + ".zip"), Path(str(args.out) + "_pre_act45.zip"))

    new_model.save(str(out_base))
    print(f"[transplant] saved {out_base}.zip", flush=True)
    print("TRANSPLANT_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
