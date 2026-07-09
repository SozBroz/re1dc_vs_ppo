"""Build a fresh widened PPO checkpoint whose action-head bias encodes the
door-harvest random-walk distribution (the empirically best explorer here),
instead of a uniform prior. Trunk/value weights keep their normal fresh init.

    python scripts/make_action_prior_init.py   ->  data/ppo_re1_actionprior.zip
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.transplant_widen import build_stub_env  # noqa: E402

# harvest_doors.py walk that discovered 106->203 / 106->107, with a small
# floor so no action is strictly impossible
PRIOR = {
    "noop": 0.01,
    "forward": 0.14,
    "back": 0.02,
    "turn_left": 0.12,
    "turn_right": 0.12,
    "run_forward": 0.33,
    "quickturn": 0.04,
    "interact": 0.18,
    "knife_swing": 0.02,
}


def main() -> int:
    from stable_baselines3 import PPO

    from re1_rl.env import ACTION_NAMES
    from re1_rl.policy_config import POLICY_KWARGS

    probs = np.array([PRIOR[a] for a in ACTION_NAMES], dtype=np.float64)
    probs /= probs.sum()

    env = build_stub_env()
    model = PPO(
        "MultiInputPolicy", env, policy_kwargs=POLICY_KWARGS,
        n_steps=256, batch_size=512, n_epochs=4, learning_rate=3e-4,
        gamma=0.99, ent_coef=0.01, device="cpu",
        tensorboard_log=str(PROJECT_ROOT / "logs" / "tb"),
    )
    with torch.no_grad():
        # fresh action_net weights are ~0 (orthogonal gain 0.01), so the bias
        # dominates the initial logits -> initial policy ~= PRIOR
        model.policy.action_net.bias.copy_(
            torch.tensor(np.log(probs), dtype=torch.float32))

    # verify on a random batch
    from stable_baselines3.common.utils import obs_as_tensor
    obs = {k: np.stack([env.observation_space[k].sample() for _ in range(32)])
           for k in env.observation_space.spaces}
    obs["frame"] = obs["frame"].transpose(0, 3, 1, 2)
    dist = model.policy.get_distribution(obs_as_tensor(obs, model.device))
    mean_p = dist.distribution.probs.mean(0).detach().numpy()
    print("action        target  actual")
    for name, t, a in zip(ACTION_NAMES, probs, mean_p):
        print(f"{name:<13} {t:.3f}   {a:.3f}")
    err = float(np.abs(mean_p - probs).max())
    print(f"max |target-actual| = {err:.4f}")
    if err > 0.03:
        print("ACTION_PRIOR_FAIL")
        return 1

    out = PROJECT_ROOT / "data" / "ppo_re1_actionprior"
    model.save(str(out))
    print(f"saved {out}.zip")
    print("ACTION_PRIOR_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
