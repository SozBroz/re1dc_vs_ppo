"""One-time graft: Doc04-medium checkpoint → combat-efficient policy.

Copies NatureCNN, room embeddings, and compatible tower weights where shapes
match. Initializes joint combat encoder, outcome/world heads, resized fusion,
pi/vf trunks, and optimizer fresh.

Usage:
    python scripts/transplant_combat_efficient.py \\
        --src data/checkpoints/.../ppo_re1_XXXX_steps.zip \\
        --out data/checkpoints/combat_efficient_graft

Never ``--resume auto`` from the old architecture after grafting — use a new
run name.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

import torch
from sb3_contrib import MaskablePPO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


COMPATIBLE_SUFFIXES = (
    "cnn_extractor.",
    "room_embedding.",
    "flags_mlp.",
)


def _load_policy_state_dict(path: Path) -> dict:
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        pth = next(n for n in names if n.endswith(".pth"))
        with zf.open(pth) as f:
            data = torch.load(io.BytesIO(f.read()), map_location="cpu", weights_only=False)
    if isinstance(data, dict) and "policy" in data:
        return data["policy"]
    if isinstance(data, dict) and any(k.startswith("features_extractor.") for k in data):
        return data
    raise RuntimeError(f"unrecognized checkpoint layout in {path}")


def _build_new_model(device: str = "cpu"):
    from re1_rl.async_fleet import DISTRIBUTED_EPOCH_HYPERPARAMS
    from re1_rl.combat_ppo import CombatEfficientPPO
    from re1_rl.distributed.spaces import make_re1_policy_spaces
    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.policy_config import AUX_COEF, POLICY_KWARGS, USE_GROUPED_ENTROPY

    obs, act = make_re1_policy_spaces()
    return CombatEfficientPPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(obs, act),
        policy_kwargs=POLICY_KWARGS,
        device=device,
        verbose=0,
        aux_coef=AUX_COEF,
        use_grouped_entropy=USE_GROUPED_ENTROPY,
        gae_lambda=1.0,
        **{k: v for k, v in DISTRIBUTED_EPOCH_HYPERPARAMS.items() if k != "learning_rate"},
        learning_rate=DISTRIBUTED_EPOCH_HYPERPARAMS["learning_rate"],
    )


@torch.no_grad()
def transplant(old_sd: dict, new_policy) -> dict[str, list[str]]:
    report: dict[str, list[str]] = {
        "copied": [],
        "skipped_shape": [],
        "skipped_missing": [],
        "fresh": [],
    }
    new_sd = new_policy.state_dict()
    for key, new_t in new_sd.items():
        if not key.startswith("features_extractor."):
            # mlp_extractor / action_net / value_net stay fresh (resized fusion in).
            if key.startswith(("mlp_extractor.", "action_net.", "value_net.")):
                report["fresh"].append(key)
            continue
        suffix = key[len("features_extractor.") :]
        old_key = key
        if old_key not in old_sd:
            # Doc04 used spatial_mlp / inventory_mlp / history_mlp / combat_mlp.
            report["skipped_missing"].append(key)
            continue
        old_t = old_sd[old_key]
        if old_t.shape == new_t.shape:
            new_t.copy_(old_t)
            report["copied"].append(key)
        else:
            report["skipped_shape"].append(f"{key}: {tuple(old_t.shape)} -> {tuple(new_t.shape)}")

    # Explicit CNN / room embedding copy via suffix match for renamed parents.
    for key, new_t in new_sd.items():
        if not key.startswith("features_extractor."):
            continue
        suffix = key[len("features_extractor.") :]
        if not any(suffix.startswith(p) for p in COMPATIBLE_SUFFIXES):
            continue
        if key in report["copied"]:
            continue
        if key in old_sd and old_sd[key].shape == new_t.shape:
            new_t.copy_(old_sd[key])
            report["copied"].append(key)

    # Best-effort: copy Doc04 combat_mlp.* into combat_mlp.* when shapes match.
    for old_k, old_t in old_sd.items():
        if "combat_mlp." not in old_k:
            continue
        new_k = old_k
        if new_k in new_sd and new_sd[new_k].shape == old_t.shape and new_k not in report["copied"]:
            new_sd[new_k].copy_(old_t)
            report["copied"].append(new_k)

    new_policy.load_state_dict(new_sd)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    old_sd = _load_policy_state_dict(args.src)
    model = _build_new_model(device=args.device)
    report = transplant(old_sd, model.policy)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(args.out))
    report_path = Path(str(args.out) + "_transplant_report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    n_params = sum(p.numel() for p in model.policy.parameters())
    print(f"saved {args.out} params={n_params} report={report_path}")
    print(f"copied={len(report['copied'])} shape_skip={len(report['skipped_shape'])}")


if __name__ == "__main__":
    main()
