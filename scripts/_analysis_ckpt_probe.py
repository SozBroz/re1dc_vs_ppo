"""One-shot regression analysis probe: checkpoint metadata, policy entropy,
goal-field sensitivity, across pre-widen / widened / late post-widen ckpts."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.transplant_widen import build_stub_env  # noqa: E402
from stable_baselines3 import PPO  # noqa: E402
from stable_baselines3.common.utils import obs_as_tensor  # noqa: E402

CKPTS = [
    ("pre-widen final (PPO_8 ~513k)", PROJECT_ROOT / "data" / "ppo_re1_final.zip"),
    ("widened transplant (step 0)", PROJECT_ROOT / "data" / "ppo_re1_widened.zip"),
    ("post-widen 150k", PROJECT_ROOT / "data" / "checkpoints" / "ppo_re1_149976_steps.zip"),
    ("post-widen 500k", PROJECT_ROOT / "data" / "checkpoints" / "ppo_re1_499920_steps.zip"),
    ("post-widen 1.05M", PROJECT_ROOT / "data" / "checkpoints" / "ppo_re1_1049832_steps.zip"),
    ("post-widen 1.55M", PROJECT_ROOT / "data" / "checkpoints" / "ppo_re1_1549752_steps.zip"),
    ("latest (resumed run) 1.8M", PROJECT_ROOT / "data" / "checkpoints" / "ppo_re1_1799712_steps.zip"),
]

env = build_stub_env()
rng = np.random.default_rng(0)
BATCH = 64

def sample_obs():
    obs = {
        k: np.stack([env.observation_space[k].sample() for _ in range(BATCH)])
        for k in env.observation_space.spaces
    }
    obs["frame"] = obs["frame"].transpose(0, 3, 1, 2)
    return obs

base_obs = sample_obs()

@torch.no_grad()
def probe(model):
    model.policy.set_training_mode(False)
    t = obs_as_tensor({k: v.copy() for k, v in base_obs.items()}, model.device)
    dist = model.policy.get_distribution(t)
    ent = dist.distribution.entropy().mean().item()
    logits = dist.distribution.logits
    probs = logits.softmax(-1).mean(0)
    values = model.policy.predict_values(t)

    # goal sensitivity: zero the goal vector, measure logit delta
    t2 = obs_as_tensor({k: v.copy() for k, v in base_obs.items()}, model.device)
    t2["goal"] = torch.zeros_like(t2["goal"])
    logits2 = model.policy.get_distribution(t2).distribution.logits
    goal_sens = (logits - logits2).abs().mean().item()

    # frame sensitivity for comparison
    t3 = obs_as_tensor({k: v.copy() for k, v in base_obs.items()}, model.device)
    t3["frame"] = torch.zeros_like(t3["frame"])
    logits3 = model.policy.get_distribution(t3).distribution.logits
    frame_sens = (logits - logits3).abs().mean().item()

    return ent, probs.cpu().numpy(), values.mean().item(), values.std().item(), goal_sens, frame_sens

from re1_rl.env import ACTION_NAMES  # noqa: E402

print(f"{'checkpoint':<32} {'steps':>9} {'params':>10} {'entropy':>7} {'V mean':>8} {'V std':>7} {'d|goal':>8} {'d|frame':>8}")
for name, path in CKPTS:
    if not path.is_file():
        print(f"{name:<32} MISSING: {path}")
        continue
    m = PPO.load(str(path), env=env, device="cpu")
    n = sum(p.numel() for p in m.policy.parameters())
    ent, probs, vmean, vstd, gs, fs = probe(m)
    print(f"{name:<32} {m.num_timesteps:>9,} {n:>10,} {ent:>7.3f} {vmean:>8.3f} {vstd:>7.3f} {gs:>8.4f} {fs:>8.4f}")
    top = np.argsort(probs)[::-1][:4]
    print("    mean action probs: " + ", ".join(f"{ACTION_NAMES[i]}={probs[i]:.3f}" for i in top))
print("PROBE_DONE")
