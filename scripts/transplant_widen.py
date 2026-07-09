"""Transplant an old (cnn 256, trunk 2x64) PPO checkpoint into the widened
architecture (cnn 512, trunk 2x256) from re1_rl/policy_config.py.

Net2Net-style function-preserving widening:
  - NatureCNN convs: identical shapes, copied as-is.
  - CNN output linear (3136->256 vs 3136->512): old weights fill the first
    256 rows; the new 256 rows keep their fresh init (their downstream
    influence is zeroed, see below).
  - MLP trunk layer 1 (300->64 vs 556->256): old weights land in the first
    64 rows, with input columns remapped per obs key (frame slice grew
    256->512); columns for the NEW cnn features are zeroed.
  - MLP trunk layer 2 (64->64 vs 256->256): old block top-left; columns
    from new layer-1 units zeroed.
  - action_net / value_net: old columns copied, new columns zeroed.

Zeroed cross-terms make the widened net compute exactly the old policy and
value at load time, while zero weights still receive gradients, so the new
capacity trains normally. Verified numerically at the end of the run.

Usage:
    python scripts/transplant_widen.py                       # data/ppo_re1_final.zip
    python scripts/transplant_widen.py --src data/checkpoints/ppo_re1_999960_steps.zip
    python scripts/transplant_widen.py --out data/ppo_re1_widened
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def build_stub_env():
    import gymnasium as gym
    from gymnasium import spaces

    from re1_rl.env import ACTION_NAMES
    from re1_rl.obs_encoder import GOAL_DIM, PROPRIO_DIM

    class StubRE1Env(gym.Env):
        observation_space = spaces.Dict(
            {
                "frame": spaces.Box(0, 255, shape=(84, 84, 4), dtype=np.uint8),
                "proprio": spaces.Box(-1.0, 1.0, shape=(PROPRIO_DIM,), dtype=np.float32),
                "goal": spaces.Box(-2.0, 2.0, shape=(GOAL_DIM,), dtype=np.float32),
            }
        )
        action_space = spaces.Discrete(len(ACTION_NAMES))

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            return self.observation_space.sample(), {}

        def step(self, action):
            return self.observation_space.sample(), 0.0, False, False, {}

    return StubRE1Env()


def feature_slices(extractor) -> dict[str, slice]:
    """Per-obs-key slices of the concatenated feature vector, in the exact
    order CombinedExtractor.forward concatenates them."""
    slices: dict[str, slice] = {}
    start = 0
    for key, sub in extractor.extractors.items():
        if hasattr(sub, "_features_dim") and sub._features_dim:
            width = sub._features_dim
        else:  # nn.Flatten for box vectors
            width = int(np.prod(extractor._observation_space[key].shape))
        slices[key] = slice(start, start + width)
        start += width
    return slices


@torch.no_grad()
def transplant(old_policy, new_policy) -> None:
    old_sd = old_policy.state_dict()
    new_sd = new_policy.state_dict()

    # --- features extractor: convs copy 1:1, output linear widens in rows ---
    for k, old_t in old_sd.items():
        if "features_extractor" not in k:
            continue
        new_t = new_sd[k]
        if old_t.shape == new_t.shape:
            new_t.copy_(old_t)
        elif old_t.dim() == 2:  # cnn linear (256,3136) -> (512,3136)
            new_t[: old_t.shape[0], : old_t.shape[1]].copy_(old_t)
        elif old_t.dim() == 1:  # its bias
            new_t[: old_t.shape[0]].copy_(old_t)
        else:
            raise RuntimeError(f"unexpected shape for {k}: {old_t.shape} -> {new_t.shape}")

    # --- input-column remap for trunk layer 1 (frame slice grew) ---
    old_slices = feature_slices(old_policy.features_extractor)
    new_slices = feature_slices(new_policy.features_extractor)
    assert list(old_slices) == list(new_slices), "obs key order changed"

    def widen_layer1(old_w: torch.Tensor, new_w: torch.Tensor, old_b, new_b) -> None:
        n_old_out = old_w.shape[0]
        new_w[:n_old_out, :].zero_()  # kill fresh-init cols for NEW cnn features
        for key in old_slices:
            o, n = old_slices[key], new_slices[key]
            width = o.stop - o.start
            new_w[:n_old_out, n.start : n.start + width].copy_(old_w[:, o])
        new_b[:n_old_out].copy_(old_b)

    def widen_hidden(old_w, new_w, old_b, new_b) -> None:
        n_out, n_in = old_w.shape
        new_w[:n_out, n_in:].zero_()  # inputs from new layer-1 units
        new_w[:n_out, :n_in].copy_(old_w)
        new_b[:n_out].copy_(old_b)

    def widen_head(old_w, new_w, old_b, new_b) -> None:
        n_in = old_w.shape[1]
        new_w[:, n_in:].zero_()
        new_w[:, :n_in].copy_(old_w)
        new_b.copy_(old_b)

    for net in ("policy_net", "value_net"):
        widen_layer1(
            old_sd[f"mlp_extractor.{net}.0.weight"], new_sd[f"mlp_extractor.{net}.0.weight"],
            old_sd[f"mlp_extractor.{net}.0.bias"], new_sd[f"mlp_extractor.{net}.0.bias"],
        )
        widen_hidden(
            old_sd[f"mlp_extractor.{net}.2.weight"], new_sd[f"mlp_extractor.{net}.2.weight"],
            old_sd[f"mlp_extractor.{net}.2.bias"], new_sd[f"mlp_extractor.{net}.2.bias"],
        )
    widen_head(old_sd["action_net.weight"], new_sd["action_net.weight"],
               old_sd["action_net.bias"], new_sd["action_net.bias"])
    widen_head(old_sd["value_net.weight"], new_sd["value_net.weight"],
               old_sd["value_net.bias"], new_sd["value_net.bias"])

    new_policy.load_state_dict(new_sd)


@torch.no_grad()
def verify(old_model, new_model, env, n_batches: int = 8, batch: int = 16) -> float:
    """Max abs deviation of logits/values between old and widened policy."""
    from stable_baselines3.common.utils import obs_as_tensor

    worst = 0.0
    for _ in range(n_batches):
        obs = {
            k: np.stack([env.observation_space[k].sample() for _ in range(batch)])
            for k in env.observation_space.spaces
        }
        # SB3 wraps the env in VecTransposeImage: policy wants channels-first
        obs["frame"] = obs["frame"].transpose(0, 3, 1, 2)
        for model in (old_model, new_model):
            model.policy.set_training_mode(False)
        t_old = obs_as_tensor(obs, old_model.device)
        t_new = obs_as_tensor(obs, new_model.device)
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
    ap.add_argument("--src", default=str(PROJECT_ROOT / "data" / "ppo_re1_final.zip"))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data" / "ppo_re1_widened"))
    args = ap.parse_args()

    from stable_baselines3 import PPO

    from re1_rl.policy_config import POLICY_KWARGS

    env = build_stub_env()
    print(f"[transplant] loading old checkpoint {args.src}", flush=True)
    old_model = PPO.load(args.src, env=env, device="cpu")
    old_n = sum(p.numel() for p in old_model.policy.parameters())

    new_model = PPO(
        "MultiInputPolicy", env, policy_kwargs=POLICY_KWARGS,
        n_steps=256, batch_size=512, n_epochs=4, learning_rate=3e-4,
        gamma=0.99, ent_coef=0.01, device="cpu",
    )
    new_n = sum(p.numel() for p in new_model.policy.parameters())
    print(f"[transplant] params {old_n:,} -> {new_n:,}", flush=True)

    transplant(old_model.policy, new_model.policy)

    worst = verify(old_model, new_model, env)
    print(f"[transplant] max |old - new| over logits/values: {worst:.3e}", flush=True)
    if worst > 1e-4:
        print("[transplant] FAIL: widened net does not reproduce old outputs", flush=True)
        return 1

    new_model.save(args.out)
    print(f"[transplant] saved {args.out}.zip", flush=True)
    print("TRANSPLANT_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
