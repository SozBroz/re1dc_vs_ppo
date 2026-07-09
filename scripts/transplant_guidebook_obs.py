"""Transplant a pre-guidebook PPO checkpoint into the widened obs dict.

Old (1104-d fusion): frame + proprio + goal + spatial + visited + rooms_visited + box
New (1243-d fusion): above + inventory + history + acquisitions + room_enemies + keys_held

Shared obs keys copy column-for-column into the MLP trunk; new keys get zero
cross-terms so logits/values match when new channels are zero at load time.

Usage:
    python scripts/transplant_guidebook_obs.py \\
        --src data/checkpoints/reward_tune_1040k/ppo_re1_6760000_steps.zip \\
        --out data/checkpoints/reward_tune_1040k/ppo_re1_guidebook_resume
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def build_old_env():
    import gymnasium as gym
    from gymnasium import spaces

    from re1_rl.env import ACTION_NAMES
    from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, PROPRIO_DIM, ROOM_VISITED_DIM
    from re1_rl.spatial_encoder import SPATIAL_DIM, VISITED_SHAPE

    class OldRE1Env(gym.Env):
        observation_space = spaces.Dict(
            {
                "frame": spaces.Box(0, 255, shape=(84, 84, 4), dtype=np.uint8),
                "proprio": spaces.Box(-1.0, 1.0, shape=(PROPRIO_DIM,), dtype=np.float32),
                "goal": spaces.Box(-2.0, 2.0, shape=(GOAL_DIM,), dtype=np.float32),
                "spatial": spaces.Box(-2.0, 2.0, shape=(SPATIAL_DIM,), dtype=np.float32),
                "visited": spaces.Box(0.0, 1.0, shape=VISITED_SHAPE, dtype=np.float32),
                "rooms_visited": spaces.Box(0.0, 1.0, shape=(ROOM_VISITED_DIM,), dtype=np.float32),
                "box": spaces.Box(0.0, 2.0, shape=(BOX_DIM,), dtype=np.float32),
            }
        )
        action_space = spaces.Discrete(len(ACTION_NAMES))

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            return self.observation_space.sample(), {}

        def step(self, action):
            return self.observation_space.sample(), 0.0, False, False, {}

    return OldRE1Env()


def build_new_env():
    from tests.test_ppo_obs_compat import StubRE1Env

    return StubRE1Env()


def feature_slices(extractor) -> dict[str, slice]:
    slices: dict[str, slice] = {}
    start = 0
    for key, sub in extractor.extractors.items():
        if hasattr(sub, "_features_dim") and sub._features_dim:
            width = sub._features_dim
        else:
            width = int(np.prod(extractor._observation_space[key].shape))
        slices[key] = slice(start, start + width)
        start += width
    return slices


@torch.no_grad()
def transplant(old_policy, new_policy) -> None:
    old_sd = old_policy.state_dict()
    new_sd = new_policy.state_dict()

    for k, old_t in old_sd.items():
        if "features_extractor" not in k:
            continue
        new_t = new_sd[k]
        if old_t.shape == new_t.shape:
            new_t.copy_(old_t)
        else:
            raise RuntimeError(f"unexpected features_extractor shape {k}: {old_t.shape} -> {new_t.shape}")

    old_slices = feature_slices(old_policy.features_extractor)
    new_slices = feature_slices(new_policy.features_extractor)
    print(f"[transplant] old slices: {old_slices}", flush=True)
    print(f"[transplant] new slices: {new_slices}", flush=True)

    def remap_layer1(old_w, new_w, old_b, new_b) -> None:
        n_old_out = old_w.shape[0]
        new_w[:n_old_out, :].zero_()
        for key in old_slices:
            o, n = old_slices[key], new_slices[key]
            width = o.stop - o.start
            new_w[:n_old_out, n.start : n.start + width].copy_(old_w[:, o])
        new_b[:n_old_out].copy_(old_b)

    def copy_hidden(old_w, new_w, old_b, new_b) -> None:
        if old_w.shape == new_w.shape:
            new_w.copy_(old_w)
            new_b.copy_(old_b)
            return
        n_out, n_in = old_w.shape
        new_w[:n_out, :n_in].copy_(old_w)
        new_w[:n_out, n_in:].zero_()
        new_b[:n_out].copy_(old_b)

    def copy_head(old_w, new_w, old_b, new_b) -> None:
        n_in = old_w.shape[1]
        new_w[:, :n_in].copy_(old_w)
        new_w[:, n_in:].zero_()
        new_b.copy_(old_b)

    for net in ("policy_net", "value_net"):
        remap_layer1(
            old_sd[f"mlp_extractor.{net}.0.weight"], new_sd[f"mlp_extractor.{net}.0.weight"],
            old_sd[f"mlp_extractor.{net}.0.bias"], new_sd[f"mlp_extractor.{net}.0.bias"],
        )
        copy_hidden(
            old_sd[f"mlp_extractor.{net}.2.weight"], new_sd[f"mlp_extractor.{net}.2.weight"],
            old_sd[f"mlp_extractor.{net}.2.bias"], new_sd[f"mlp_extractor.{net}.2.bias"],
        )
    copy_head(old_sd["action_net.weight"], new_sd["action_net.weight"],
              old_sd["action_net.bias"], new_sd["action_net.bias"])
    copy_head(old_sd["value_net.weight"], new_sd["value_net.weight"],
              old_sd["value_net.bias"], new_sd["value_net.bias"])

    new_policy.load_state_dict(new_sd)


@torch.no_grad()
def verify(old_model, new_model, n_batches: int = 8, batch: int = 16) -> float:
    from stable_baselines3.common.utils import obs_as_tensor

    old_env = build_old_env()
    new_keys = set(build_new_env().observation_space.spaces) - set(old_env.observation_space.spaces)
    worst = 0.0
    for _ in range(n_batches):
        old_obs = {
            k: np.stack([old_env.observation_space[k].sample() for _ in range(batch)])
            for k in old_env.observation_space.spaces
        }
        old_obs["frame"] = old_obs["frame"].transpose(0, 3, 1, 2)
        new_obs = dict(old_obs)
        for key in new_keys:
            space = build_new_env().observation_space[key]
            new_obs[key] = np.zeros((batch, *space.shape), dtype=space.dtype)
        for model in (old_model, new_model):
            model.policy.set_training_mode(False)
        t_old = obs_as_tensor(old_obs, old_model.device)
        t_new = obs_as_tensor(new_obs, new_model.device)
        logits_old = old_model.policy.get_distribution(t_old).distribution.logits
        logits_new = new_model.policy.get_distribution(t_new).distribution.logits
        v_old = old_model.policy.predict_values(t_old)
        v_new = new_model.policy.predict_values(t_new)
        worst = max(
            worst,
            (logits_old - logits_new).abs().max().item(),
            (v_old - v_new).abs().max().item(),
        )
    return worst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--src",
        default=str(PROJECT_ROOT / "data" / "checkpoints" / "reward_tune_1040k" / "ppo_re1_6760000_steps.zip"),
    )
    ap.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "data" / "checkpoints" / "reward_tune_1040k" / "ppo_re1_guidebook_resume"),
    )
    ap.add_argument("--backup-src", action="store_true")
    args = ap.parse_args()

    from stable_baselines3 import PPO

    from re1_rl.policy_config import POLICY_KWARGS

    src = Path(args.src)
    if not src.is_file():
        print(f"[transplant] missing {src}", flush=True)
        return 1

    print(f"[transplant] loading old checkpoint {src}", flush=True)
    old_model = PPO.load(str(src), env=build_old_env(), device="cpu")
    old_n = sum(p.numel() for p in old_model.policy.parameters())
    print(f"[transplant] old steps={old_model.num_timesteps:,} params={old_n:,}", flush=True)

    new_model = PPO(
        "MultiInputPolicy",
        build_new_env(),
        policy_kwargs=POLICY_KWARGS,
        n_steps=256,
        batch_size=512,
        n_epochs=4,
        learning_rate=3e-4,
        gamma=0.99,
        ent_coef=0.01,
        device="cpu",
    )
    new_n = sum(p.numel() for p in new_model.policy.parameters())
    print(f"[transplant] new params={new_n:,}", flush=True)

    transplant(old_model.policy, new_model.policy)
    new_model.num_timesteps = old_model.num_timesteps

    worst = verify(old_model, new_model)
    print(f"[transplant] max |old - new| over logits/values: {worst:.3e}", flush=True)
    if worst > 1e-4:
        print("[transplant] FAIL: new net does not reproduce old outputs", flush=True)
        return 1

    if args.backup_src:
        import shutil
        bak = src.with_name(src.stem + "_pre_guidebook" + src.suffix)
        shutil.copy2(src, bak)
        print(f"[transplant] backed up src -> {bak}", flush=True)

    new_model.save(args.out)
    print(f"[transplant] saved {args.out}.zip", flush=True)
    print("TRANSPLANT_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
