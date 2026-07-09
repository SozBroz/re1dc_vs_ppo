"""Shrink PPO action head 11 -> 9 by dropping aim (8) and fire (9).

Preserves trained logits for actions 0-7 and knife_swing (old 10 -> new 8).
Trunk, value head, and all non-action weights copy verbatim.

Usage:
    python scripts/transplant_remove_aim_fire.py
    python scripts/transplant_remove_aim_fire.py \\
        --src data/checkpoints/ppo_re1_12480000_steps.zip \\
        --out data/ppo_re1_final
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

OLD_N_ACTIONS = 11
NEW_N_ACTIONS = 9
# new row i <- old row ROW_MAP[i]
ROW_MAP = list(range(8)) + [10]


def build_env(n_actions: int):
    import gymnasium as gym
    from gymnasium import spaces

    from re1_rl.obs_encoder import GOAL_DIM, PROPRIO_DIM
    from re1_rl.spatial_encoder import SPATIAL_DIM, VISITED_SHAPE

    class StubRE1Env(gym.Env):
        observation_space = spaces.Dict(
            {
                "frame": spaces.Box(0, 255, shape=(84, 84, 4), dtype=np.uint8),
                "proprio": spaces.Box(-1.0, 1.0, shape=(PROPRIO_DIM,), dtype=np.float32),
                "goal": spaces.Box(-2.0, 2.0, shape=(GOAL_DIM,), dtype=np.float32),
                "spatial": spaces.Box(-2.0, 2.0, shape=(SPATIAL_DIM,), dtype=np.float32),
                "visited": spaces.Box(0.0, 1.0, shape=VISITED_SHAPE, dtype=np.float32),
            }
        )
        action_space = spaces.Discrete(n_actions)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            return self.observation_space.sample(), {}

        def step(self, action):
            return self.observation_space.sample(), 0.0, False, False, {}

    return StubRE1Env()


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
def verify(old_model, new_model, n_batches: int = 8, batch: int = 16) -> float:
    from stable_baselines3.common.utils import obs_as_tensor

    env_old = build_env(OLD_N_ACTIONS)
    worst = 0.0
    for _ in range(n_batches):
        obs = {
            k: np.stack([env_old.observation_space[k].sample() for _ in range(batch)])
            for k in env_old.observation_space.spaces
        }
        obs["frame"] = obs["frame"].transpose(0, 3, 1, 2)
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
    from re1_rl.checkpoint_io import find_latest_checkpoint

    ap = argparse.ArgumentParser()
    default_src = find_latest_checkpoint(PROJECT_ROOT / "data" / "checkpoints")
    if default_src is None:
        default_src = PROJECT_ROOT / "data" / "ppo_re1_final.zip"
    ap.add_argument("--src", default=str(default_src))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data" / "ppo_re1_final"))
    ap.add_argument("--backup-src", action="store_true")
    args = ap.parse_args()

    from stable_baselines3 import PPO

    from re1_rl.env import ACTION_NAMES
    from re1_rl.policy_config import POLICY_KWARGS

    if len(ACTION_NAMES) != NEW_N_ACTIONS:
        print(
            f"[transplant] expected {NEW_N_ACTIONS} actions, got {len(ACTION_NAMES)}",
            flush=True,
        )
        return 1

    src = Path(args.src)
    if not src.is_file():
        print(f"[transplant] missing {src}", flush=True)
        return 1

    print(f"[transplant] loading {src}", flush=True)
    old_model = PPO.load(str(src), env=build_env(OLD_N_ACTIONS), device="cpu")
    print(
        f"[transplant] old steps={old_model.num_timesteps:,} actions={OLD_N_ACTIONS}",
        flush=True,
    )

    new_model = PPO(
        "MultiInputPolicy",
        build_env(NEW_N_ACTIONS),
        policy_kwargs=POLICY_KWARGS,
        n_steps=256,
        batch_size=512,
        n_epochs=4,
        learning_rate=3e-4,
        gamma=0.99,
        ent_coef=0.01,
        device="cpu",
    )
    narrow_action_head(old_model.policy, new_model.policy)
    new_model.num_timesteps = old_model.num_timesteps

    worst = verify(old_model, new_model)
    print(f"[transplant] max logit/value drift on kept rows: {worst:.3e}", flush=True)
    if worst > 1e-4:
        print("[transplant] FAIL: narrowed net does not reproduce kept logits", flush=True)
        return 1

    if args.backup_src:
        bak = src.with_name(src.stem + "_pre_act9.zip")
        shutil.copy2(src, bak)
        print(f"[transplant] backed up src -> {bak}", flush=True)

    out_pre = Path(str(args.out) + "_pre_act9.zip")
    if Path(str(args.out) + ".zip").is_file():
        shutil.copy2(Path(str(args.out) + ".zip"), out_pre)
        print(f"[transplant] backed up prior final -> {out_pre}", flush=True)

    new_model.save(args.out)
    print(f"[transplant] saved {args.out}.zip", flush=True)
    print("TRANSPLANT_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
