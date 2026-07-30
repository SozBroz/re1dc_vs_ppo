"""Transplant a pre–enemy-motion Doc04 MaskablePPO checkpoint into widened obs.

Old schema: SPATIAL_DIM without enemy world_vx/vz (+10), PROPRIO_DIM without
player_world_vx/vz (+2). New schema matches current spatial_encoder / obs_encoder.

Copies all matching weight tensors; remaps ``spatial_mlp.0`` input columns so
per-enemy slots keep their first 8 fields and zero-inits the new world_vx/vz
slots; remaps ``control_mlp.0`` so prior proprio scalars + room embedding copy
and the new player velocity columns stay zero.

Uses ``build_stub_env`` from transplant_widen.py for the holder env pattern
(observation_space sampling), then builds the Doc04 policy on full policy spaces.

Usage:
    python scripts/transplant_enemy_motion.py \\
        --src data/checkpoints/reward_tune_1040k/ppo_re1_102654913_steps.zip \\
        --out data/checkpoints/reward_tune_1040k/ppo_re1_enemy_motion_graft
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from transplant_widen import build_stub_env  # noqa: E402

OLD_ENEMY_SLOT_FIELDS = 8
NEW_ENEMY_SLOT_FIELDS = 10
OLD_SPATIAL_DELTA = 10  # ENEMY_SLOTS * 2
OLD_PROPRIO_DELTA = 2


def build_new_env():
    """Full Doc04 policy spaces (widen stub alone omits spatial / inventory / …)."""
    from re1_rl.distributed.spaces import make_re1_policy_spaces
    from re1_rl.distributed.weights import _SpaceHolderEnv

    # Touch widen stub so the import stays live and shapes stay importable.
    _ = build_stub_env()
    obs_space, act_space = make_re1_policy_spaces()
    return _SpaceHolderEnv(obs_space, act_space)


def _enemies_start_index() -> int:
    from re1_rl.spatial_encoder import ITEM_SLOTS

    # items_obtainable_here + ITEM_SLOTS * 8 + enemy_count
    return 1 + ITEM_SLOTS * 8 + 1


def remap_spatial_weight(old_w: torch.Tensor, new_w: torch.Tensor) -> None:
    """Copy old spatial columns into new layout; zero new world_vx/vz slots."""
    from re1_rl.spatial_encoder import ENEMY_SLOTS, SPATIAL_DIM

    old_dim = old_w.shape[1]
    new_dim = new_w.shape[1]
    expect_old = SPATIAL_DIM - OLD_SPATIAL_DELTA
    if old_dim != expect_old or new_dim != SPATIAL_DIM:
        raise RuntimeError(
            f"spatial_mlp.0.weight unexpected shapes "
            f"{tuple(old_w.shape)} -> {tuple(new_w.shape)} "
            f"(expected in={expect_old}, out={SPATIAL_DIM})"
        )

    new_w.zero_()
    start = _enemies_start_index()
    # Prefix through enemy_count (inclusive of count itself via start index).
    new_w[:, :start].copy_(old_w[:, :start])

    for slot in range(ENEMY_SLOTS):
        o0 = start + slot * OLD_ENEMY_SLOT_FIELDS
        n0 = start + slot * NEW_ENEMY_SLOT_FIELDS
        new_w[:, n0 : n0 + OLD_ENEMY_SLOT_FIELDS].copy_(
            old_w[:, o0 : o0 + OLD_ENEMY_SLOT_FIELDS]
        )
        # n0+8, n0+9 remain zero (world_vx / world_vz)

    old_suffix = start + ENEMY_SLOTS * OLD_ENEMY_SLOT_FIELDS
    new_suffix = start + ENEMY_SLOTS * NEW_ENEMY_SLOT_FIELDS
    new_w[:, new_suffix:].copy_(old_w[:, old_suffix:])


def remap_control_weight(old_w: torch.Tensor, new_w: torch.Tensor) -> None:
    """Copy control_mlp inputs; zero trailing player_world_vx/vz scalar cols."""
    from re1_rl.doc04_medium_extractor import ROOM_EMBED_DIM
    from re1_rl.obs_encoder import PROPRIO_DIM

    old_in = old_w.shape[1]
    new_in = new_w.shape[1]
    expect_old = (PROPRIO_DIM - OLD_PROPRIO_DELTA) - 1 + ROOM_EMBED_DIM
    expect_new = PROPRIO_DIM - 1 + ROOM_EMBED_DIM
    if old_in != expect_old or new_in != expect_new:
        raise RuntimeError(
            f"control_mlp.0.weight unexpected shapes "
            f"{tuple(old_w.shape)} -> {tuple(new_w.shape)} "
            f"(expected in={expect_old}, out={expect_new})"
        )

    old_scalar = expect_old - ROOM_EMBED_DIM
    new_scalar = expect_new - ROOM_EMBED_DIM
    new_w.zero_()
    new_w[:, :old_scalar].copy_(old_w[:, :old_scalar])
    # new_w[:, old_scalar:new_scalar] stays zero (player_world_vx/vz)
    new_w[:, new_scalar:].copy_(old_w[:, old_scalar:])


def _extractor_prefixes(sd: dict) -> list[str]:
    prefixes = ["features_extractor."]
    for p in ("pi_features_extractor.", "vf_features_extractor."):
        if any(k.startswith(p) for k in sd):
            prefixes.append(p)
    return prefixes


@torch.no_grad()
def transplant(old_sd: dict, new_policy) -> dict[str, list[str]]:
    report: dict[str, list[str]] = {"copied": [], "remapped": [], "zeroed": [], "skipped": []}
    new_sd = new_policy.state_dict()

    for key, new_t in new_sd.items():
        if key not in old_sd:
            report["skipped"].append(f"missing in old: {key}")
            continue
        old_t = old_sd[key]
        if old_t.shape == new_t.shape:
            new_t.copy_(old_t)
            report["copied"].append(key)
            continue

        handled = False
        for prefix in _extractor_prefixes(old_sd):
            if not key.startswith(prefix):
                continue
            suffix = key[len(prefix) :]
            if suffix == "spatial_mlp.0.weight":
                remap_spatial_weight(old_t, new_t)
                report["remapped"].append(key)
                report["zeroed"].append(f"{key} enemy world_vx/vz cols")
                handled = True
            elif suffix == "control_mlp.0.weight":
                remap_control_weight(old_t, new_t)
                report["remapped"].append(key)
                report["zeroed"].append(f"{key} player_world_vx/vz cols")
                handled = True
            elif suffix in ("spatial_mlp.0.bias", "control_mlp.0.bias"):
                new_t.copy_(old_t)
                report["copied"].append(key)
                handled = True
        if not handled:
            report["skipped"].append(
                f"{key} shape {tuple(old_t.shape)} -> {tuple(new_t.shape)}"
            )

    new_policy.load_state_dict(new_sd)
    return report


def load_old_state_dict(src: Path) -> tuple[dict, int]:
    with zipfile.ZipFile(src) as zf:
        raw_sd = torch.load(io.BytesIO(zf.read("policy.pth")), map_location="cpu")
        meta = json.loads(zf.read("data"))
    return raw_sd, int(meta.get("num_timesteps", 0))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Widen Doc04 checkpoint for enemy/player world velocity obs"
    )
    ap.add_argument(
        "--src",
        default=str(
            PROJECT_ROOT
            / "data"
            / "checkpoints"
            / "reward_tune_1040k"
            / "ppo_re1_102654913_steps.zip"
        ),
    )
    ap.add_argument(
        "--out",
        default=str(
            PROJECT_ROOT
            / "data"
            / "checkpoints"
            / "reward_tune_1040k"
            / "ppo_re1_enemy_motion_graft"
        ),
    )
    args = ap.parse_args()

    from sb3_contrib import MaskablePPO

    from re1_rl.obs_encoder import PROPRIO_DIM
    from re1_rl.policy_config import POLICY_KWARGS
    from re1_rl.spatial_encoder import SPATIAL_DIM

    src = Path(args.src)
    if not src.is_file():
        print(f"[transplant] missing source checkpoint: {src}", flush=True)
        return 1

    print(f"[transplant] loading old weights from {src}", flush=True)
    old_sd, old_steps = load_old_state_dict(src)
    print(
        f"[transplant] old steps={old_steps:,} "
        f"(spatial {SPATIAL_DIM - OLD_SPATIAL_DELTA} -> {SPATIAL_DIM}, "
        f"proprio {PROPRIO_DIM - OLD_PROPRIO_DELTA} -> {PROPRIO_DIM})",
        flush=True,
    )

    env = build_new_env()
    new_model = MaskablePPO(
        "MultiInputPolicy",
        env,
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
    new_sd_probe = new_model.policy.state_dict()
    old_n = sum(
        int(t.numel())
        for k, t in old_sd.items()
        if torch.is_tensor(t) and k in new_sd_probe
    )
    new_n = sum(int(t.numel()) for t in new_sd_probe.values())
    print(f"[transplant] params {old_n:,} -> {new_n:,}", flush=True)

    report = transplant(old_sd, new_model.policy)
    new_model.num_timesteps = old_steps

    print(f"[transplant] copied={len(report['copied'])} "
          f"remapped={len(report['remapped'])} "
          f"zeroed={len(report['zeroed'])} "
          f"skipped={len(report['skipped'])}", flush=True)
    for line in report["remapped"]:
        print(f"  ~ {line}", flush=True)
    for line in report["zeroed"]:
        print(f"  0 {line}", flush=True)
    if report["skipped"]:
        # Missing pi_/vf_ keys when share_features_extractor can be noisy; show a few.
        for line in report["skipped"][:12]:
            print(f"  ? {line}", flush=True)
        if len(report["skipped"]) > 12:
            print(f"  ? ... ({len(report['skipped']) - 12} more)", flush=True)

    if not report["remapped"]:
        print("[transplant] FAIL: no spatial/control remaps applied", flush=True)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new_model.save(str(out))
    out_zip = out if out.suffix == ".zip" else Path(str(out) + ".zip")
    print(f"[transplant] saved {out_zip}", flush=True)
    print("TRANSPLANT_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
