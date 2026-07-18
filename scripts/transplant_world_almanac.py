"""Transplant a pre–world-aware MaskablePPO checkpoint into RE1WorldAwareExtractor.

Old CombinedExtractor fusion (1335-d): frame CNN 512 + flattened privileged obs.
New fusion (1523-d): CNN 512 + flattened obs + world_context 64 from world_mlp.

Copies matching CNN / MLP / head weights; remaps MLP layer-1 input columns per
obs-key slice (spatial 128→140, acquisitions 9→121, etc.); zero-inits world_mlp
and world_context cross-terms.

Usage:
    python scripts/transplant_world_almanac.py \\
        --src backups/pre_world_catalog_2026-07-17/ppo_re1_126602090_steps.zip \\
        --out data/checkpoints/reward_tune_1040k/ppo_re1_world_almanac_graft
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

OLD_POLICY_KWARGS: dict = dict(
    net_arch=dict(pi=[256, 256], vf=[256, 256]),
    features_extractor_kwargs=dict(cnn_output_dim=512),
)

# Pre-world-catalog checkpoint obs layout (from zip metadata).
OLD_SPATIAL_DIM = 128
OLD_ACQUISITIONS_DIM = 9
OLD_KEYS_HELD_DIM = 35
OLD_MILESTONE_DIM = 13
OLD_ACTION_DIM = 45


def build_old_env():
    import gymnasium as gym
    from gymnasium import spaces

    from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM
    from re1_rl.env import FRAME_SHAPE_CHW
    from re1_rl.episode_history import ROOM_HISTORY_DIM
    from re1_rl.item_affordances import AFFORDANCES_DIM
    from re1_rl.maps_files import MAPS_FILES_DIM
    from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, INVENTORY_OBS_DIM, PROPRIO_DIM, ROOM_VISITED_DIM
    from re1_rl.room_signature import ENEMY_ROSTER_DIM
    from re1_rl.spatial_encoder import VISITED_SHAPE

    class OldRE1Env(gym.Env):
        observation_space = spaces.Dict(
            {
                "frame": spaces.Box(0, 255, shape=FRAME_SHAPE_CHW, dtype=np.uint8),
                "proprio": spaces.Box(-1.0, 1.0, shape=(PROPRIO_DIM,), dtype=np.float32),
                "goal": spaces.Box(-2.0, 2.0, shape=(GOAL_DIM,), dtype=np.float32),
                "spatial": spaces.Box(-2.0, 2.0, shape=(OLD_SPATIAL_DIM,), dtype=np.float32),
                "visited": spaces.Box(0.0, 1.0, shape=VISITED_SHAPE, dtype=np.float32),
                "rooms_visited": spaces.Box(0.0, 1.0, shape=(ROOM_VISITED_DIM,), dtype=np.float32),
                "box": spaces.Box(0.0, 2.0, shape=(BOX_DIM,), dtype=np.float32),
                "inventory": spaces.Box(0.0, 1.0, shape=(INVENTORY_OBS_DIM,), dtype=np.float32),
                "history": spaces.Box(0.0, 1.0, shape=(ROOM_HISTORY_DIM,), dtype=np.float32),
                "acquisitions": spaces.Box(
                    0.0, 1.0, shape=(OLD_ACQUISITIONS_DIM,), dtype=np.float32
                ),
                "room_enemies": spaces.Box(0.0, 1.0, shape=(ENEMY_ROSTER_DIM,), dtype=np.float32),
                "keys_held": spaces.Box(0.0, 1.0, shape=(OLD_KEYS_HELD_DIM,), dtype=np.float32),
                "affordances": spaces.Box(0.0, 1.0, shape=(AFFORDANCES_DIM,), dtype=np.float32),
                "cutscene_ledger": spaces.Box(
                    0.0, 1.0, shape=(CUTSCENE_LEDGER_DIM,), dtype=np.float32
                ),
                "milestones": spaces.Box(0.0, 1.0, shape=(OLD_MILESTONE_DIM,), dtype=np.float32),
                "maps_files": spaces.Box(0.0, 1.0, shape=(MAPS_FILES_DIM,), dtype=np.float32),
            }
        )
        action_space = spaces.Discrete(OLD_ACTION_DIM)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            return self.observation_space.sample(), {}

        def step(self, action):
            return self.observation_space.sample(), 0.0, False, False, {}

    return OldRE1Env()


def build_new_env():
    import gymnasium as gym

    from re1_rl.distributed.spaces import make_re1_policy_spaces
    from re1_rl.distributed.weights import _SpaceHolderEnv

    obs_space, act_space = make_re1_policy_spaces()
    return _SpaceHolderEnv(obs_space, act_space)


def combined_feature_slices(extractor) -> dict[str, slice]:
    """Per-obs-key slices for SB3 CombinedExtractor concat order."""
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


def world_aware_feature_slices(extractor) -> dict[str, slice]:
    """Per-obs-key slices for RE1WorldAwareExtractor fusion vector."""
    from stable_baselines3.common.preprocessing import get_flattened_obs_dim

    from re1_rl.features_extractor import WORLD_CONTEXT_DIM

    slices: dict[str, slice] = {}
    start = 0
    slices["frame"] = slice(start, start + extractor._cnn_output_dim)
    start += extractor._cnn_output_dim
    obs_space = getattr(extractor, "observation_space", extractor._observation_space)
    for key in extractor._flatten_keys:
        width = int(get_flattened_obs_dim(obs_space[key]))
        slices[key] = slice(start, start + width)
        start += width
    slices["world_context"] = slice(start, start + WORLD_CONTEXT_DIM)
    return slices


def _policy_trainable_state_dict(policy) -> dict[str, torch.Tensor]:
    """Drop pi_/vf_ duplicate extractor keys; keep primary policy tensors."""
    sd = policy.state_dict()
    return {
        k: v
        for k, v in sd.items()
        if not k.startswith(("pi_features_extractor.", "vf_features_extractor."))
    }


def load_old_model(src: Path):
    from sb3_contrib import MaskablePPO

    env = build_old_env()
    model = MaskablePPO(
        "MultiInputPolicy",
        env,
        policy_kwargs=OLD_POLICY_KWARGS,
        n_steps=256,
        batch_size=512,
        n_epochs=4,
        learning_rate=3e-4,
        gamma=0.99,
        ent_coef=0.01,
        device="cpu",
        verbose=0,
    )
    with zipfile.ZipFile(src) as zf:
        raw_sd = torch.load(io.BytesIO(zf.read("policy.pth")), map_location="cpu")
        meta = json.loads(zf.read("data"))
    trainable = {
        k: v
        for k, v in raw_sd.items()
        if k.startswith(("features_extractor.", "mlp_extractor.", "value_net."))
    }
    missing, unexpected = model.policy.load_state_dict(trainable, strict=False)
    if unexpected:
        raise RuntimeError(f"unexpected keys loading old policy: {unexpected}")
    if missing:
        print(f"[transplant] warn: missing old keys (fresh init): {missing}", flush=True)
    model.num_timesteps = int(meta["num_timesteps"])
    return model


@torch.no_grad()
def transplant(old_policy, new_policy) -> dict[str, list[str]]:
    from re1_rl.features_extractor import WORLD_CONTEXT_DIM, reload_world_catalog_buffers

    report: dict[str, list[str]] = {"copied": [], "zeroed": [], "skipped": []}

    old_sd = _policy_trainable_state_dict(old_policy)
    new_sd = _policy_trainable_state_dict(new_policy)

    cnn_prefix_old = "features_extractor.extractors.frame."
    cnn_prefix_new = "features_extractor.cnn_extractor."
    for k, old_t in old_sd.items():
        if not k.startswith(cnn_prefix_old):
            continue
        nk = cnn_prefix_new + k[len(cnn_prefix_old) :]
        if nk not in new_sd:
            report["skipped"].append(k)
            continue
        new_t = new_sd[nk]
        if old_t.shape == new_t.shape:
            new_t.copy_(old_t)
            report["copied"].append(nk)
        else:
            raise RuntimeError(f"unexpected CNN shape {k}: {old_t.shape} -> {new_t.shape}")

    for k, old_t in old_sd.items():
        if k.startswith("features_extractor."):
            continue
        if k not in new_sd:
            report["skipped"].append(k)
            continue
        new_t = new_sd[k]
        if old_t.shape == new_t.shape:
            new_t.copy_(old_t)
            report["copied"].append(k)
        elif k.endswith(".0.weight") and k.startswith("mlp_extractor."):
            pass  # layer-1 handled below
        elif k.endswith(".0.bias") and k.startswith("mlp_extractor."):
            pass
        else:
            report["skipped"].append(f"{k} shape {tuple(old_t.shape)} -> {tuple(new_t.shape)}")

    old_slices = combined_feature_slices(old_policy.features_extractor)
    new_slices = world_aware_feature_slices(new_policy.features_extractor)
    print(f"[transplant] old slices: {old_slices}", flush=True)
    print(f"[transplant] new slices: {new_slices}", flush=True)

    def remap_layer1(net: str, old_w, new_w, old_b, new_b) -> None:
        n_old_out = old_w.shape[0]
        new_w[:n_old_out, :].zero_()
        for key, o in old_slices.items():
            if key not in new_slices:
                report["skipped"].append(f"mlp.{net}.0 missing new slice for {key}")
                continue
            n = new_slices[key]
            o_width = o.stop - o.start
            n_width = n.stop - n.start
            copy_width = min(o_width, n_width)
            new_w[:n_old_out, n.start : n.start + copy_width].copy_(
                old_w[:, o.start : o.start + copy_width]
            )
            report["copied"].append(
                f"mlp_extractor.{net}.0.{key} cols [{n.start}:{n.start + copy_width}]"
            )
            if n_width > copy_width:
                report["zeroed"].append(
                    f"mlp_extractor.{net}.0.{key} cols [{n.start + copy_width}:{n.stop}]"
                )
            if o_width > copy_width:
                report["skipped"].append(
                    f"mlp_extractor.{net}.0.{key} dropped old cols [{o.start + copy_width}:{o.stop}]"
                )
        wc = new_slices["world_context"]
        report["zeroed"].append(f"mlp_extractor.{net}.0.world_context cols [{wc.start}:{wc.stop}]")
        new_b[:n_old_out].copy_(old_b)
        report["copied"].append(f"mlp_extractor.{net}.0.bias")

    for net in ("policy_net", "value_net"):
        remap_layer1(
            net,
            old_sd[f"mlp_extractor.{net}.0.weight"],
            new_sd[f"mlp_extractor.{net}.0.weight"],
            old_sd[f"mlp_extractor.{net}.0.bias"],
            new_sd[f"mlp_extractor.{net}.0.bias"],
        )

    from re1_rl.action_mask import ATTACK_ACTION

    def expand_action_head(key: str) -> None:
        old_t = old_sd[key]
        new_t = new_sd[key]
        if old_t.shape == new_t.shape:
            new_t.copy_(old_t)
            report["copied"].append(key)
            return
        if old_t.ndim == 2 and old_t.shape[0] >= new_t.shape[0]:
            report["skipped"].append(f"{key} unexpected shrink {tuple(old_t.shape)} -> {tuple(new_t.shape)}")
            return
        expanded = new_t.clone()
        expanded[: old_t.shape[0]] = old_t
        if old_t.ndim == 2:
            expanded[old_t.shape[0] :] = old_t[ATTACK_ACTION]
        else:
            expanded[old_t.shape[0] :] = old_t[ATTACK_ACTION]
            expanded[old_t.shape[0] :] -= float(np.log(100.0))
        new_t.copy_(expanded)
        report["copied"].append(f"{key} rows [0:{old_t.shape[0]}]")
        report["zeroed"].append(
            f"{key} row {old_t.shape[0]}: cloned attack action (logit -log 100)"
        )

    for key in ("action_net.weight", "action_net.bias"):
        if key in old_sd and key in new_sd:
            expand_action_head(key)

    for name, module in new_policy.features_extractor.world_mlp.named_parameters():
        report["zeroed"].append(f"features_extractor.world_mlp.{name}")

    reload_world_catalog_buffers(new_policy)
    report["copied"].append("reload_world_catalog_buffers()")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--src",
        default=str(
            PROJECT_ROOT
            / "backups"
            / "pre_world_catalog_2026-07-17"
            / "ppo_re1_126602090_steps.zip"
        ),
    )
    ap.add_argument(
        "--out",
        default=str(
            PROJECT_ROOT
            / "data"
            / "checkpoints"
            / "reward_tune_1040k"
            / "ppo_re1_world_almanac_graft"
        ),
    )
    ap.add_argument(
        "--alias",
        default=str(PROJECT_ROOT / "data" / "ppo_re1_world_almanac_graft.zip"),
        help="Second copy of the graft zip (default: data/ppo_re1_world_almanac_graft.zip)",
    )
    args = ap.parse_args()

    from sb3_contrib import MaskablePPO

    from re1_rl.policy_config import POLICY_KWARGS

    src = Path(args.src)
    if not src.is_file():
        print(f"[transplant] missing source checkpoint: {src}", flush=True)
        return 1

    print(f"[transplant] loading old weights from {src}", flush=True)
    old_model = load_old_model(src)
    old_n = sum(p.numel() for p in old_model.policy.parameters())
    print(
        f"[transplant] old steps={old_model.num_timesteps:,} "
        f"features_dim={old_model.policy.features_dim:,} params={old_n:,}",
        flush=True,
    )

    new_model = MaskablePPO(
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
        verbose=0,
    )
    new_n = sum(p.numel() for p in new_model.policy.parameters())
    print(
        f"[transplant] new features_dim={new_model.policy.features_dim:,} params={new_n:,}",
        flush=True,
    )

    report = transplant(old_model.policy, new_model.policy)
    new_model.num_timesteps = old_model.num_timesteps

    print("[transplant] copied:", flush=True)
    for line in report["copied"]:
        print(f"  + {line}", flush=True)
    print("[transplant] zero-initialized:", flush=True)
    for line in report["zeroed"]:
        print(f"  0 {line}", flush=True)
    if report["skipped"]:
        print("[transplant] skipped:", flush=True)
        for line in report["skipped"]:
            print(f"  ? {line}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new_model.save(str(out))
    out_zip = out if out.suffix == ".zip" else Path(str(out) + ".zip")
    print(f"[transplant] saved {out_zip}", flush=True)

    alias = Path(args.alias)
    alias.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out_zip, alias)
    print(f"[transplant] alias {alias}", flush=True)
    print("TRANSPLANT_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
