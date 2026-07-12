"""Measure masked vs unmasked logprob delta (poison gate for cutover).

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_masked_logprob_delta.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_masked_logprob_delta.py --resume path.zip

Pass criteria (Phase 1):
  max|logπ_masked_collect − logπ_masked_train| < 1e-4
  mean|logπ_masked_collect − logπ_unmasked_train| is large on illegal-heavy masks
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _build_batch(n_envs: int = 8, n_actions: int | None = None):
    from re1_rl.distributed.inference_policy import InferencePolicy
    from re1_rl.distributed.spaces import make_re1_policy_spaces
    from re1_rl.env import ACTION_NAMES
    from re1_rl.async_fleet import load_async_learner

    n_actions = n_actions or len(ACTION_NAMES)
    obs_space, act_space = make_re1_policy_spaces()
    policy = InferencePolicy(obs_space, act_space, "cpu")
    model = load_async_learner(device="cpu", resume=None, tb_log=None)

    # Copy weights so collect/train share parameters.
    policy.load_from_state_dict(
        {k: v.detach().cpu().clone() for k, v in model.policy.state_dict().items()},
        policy_version=1,
    )

    obs = {k: np.stack([space.sample() for _ in range(n_envs)], axis=0)
           for k, space in obs_space.spaces.items()}
    # Illegal-heavy masks: only a few actions legal (skew poison if unmasked).
    masks = np.zeros((n_envs, n_actions), dtype=bool)
    for i in range(n_envs):
        legal = np.random.choice(n_actions, size=max(2, n_actions // 8), replace=False)
        masks[i, legal] = True
        masks[i, 0] = True

    actions, _values, log_collect = policy.predict_masked_batch(obs, masks)

    from re1_rl.distributed.obs_preprocess import prepare_obs_for_policy
    from stable_baselines3.common.utils import obs_as_tensor

    obs_t = obs_as_tensor(prepare_obs_for_policy(obs, model.observation_space), "cpu")
    act_t = torch.as_tensor(actions, dtype=torch.int64)

    # Masked train path (MaskablePPO.evaluate_actions)
    _v_m, log_train_m, _e = model.policy.evaluate_actions(
        obs_t, act_t, action_masks=masks
    )
    log_train_m = log_train_m.detach().cpu().numpy()

    # Unmasked train path (poison)
    dist = model.policy.get_distribution(obs_t)
    log_train_u = dist.log_prob(act_t).detach().cpu().numpy()

    return log_collect, log_train_m, log_train_u


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--resume", default=None, help="optional checkpoint (unused for synth probe)")
    ap.add_argument("--n-envs", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tol", type=float, default=1e-4)
    args = ap.parse_args()
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    log_c, log_m, log_u = _build_batch(n_envs=args.n_envs)
    delta_good = np.abs(log_c - log_m)
    delta_poison = np.abs(log_c - log_u)
    print(f"masked_train  max|d|={delta_good.max():.6e} mean|d|={delta_good.mean():.6e}")
    print(f"unmasked_train max|d|={delta_poison.max():.6e} mean|d|={delta_poison.mean():.6e}")
    ok = bool(delta_good.max() < args.tol)
    print(f"PASS={ok} (need masked max|d| < {args.tol})")
    if not ok:
        return 1
    if delta_poison.mean() <= delta_good.mean():
        print("WARN: unmasked delta not larger than masked (masks may be too permissive)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
