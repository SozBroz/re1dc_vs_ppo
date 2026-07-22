"""Transplant NatureCNN weights from a pre-Doc04 checkpoint into RE1Doc04MediumExtractor.

Copies only CNN tensors with matching shapes into features/pi/vf extractors.
All towers, fusion, and pi/vf trunks start fresh; ``num_timesteps`` resets to 0.

Usage:
    python scripts/transplant_doc04_cnn.py \\
        --src backups/pre_world_catalog_2026-07-17/ppo_re1_world_almanac_graft.zip \\
        --out data/checkpoints/reward_tune_1040k/ppo_re1_0_cnn_graft
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

OLD_FRAME_PREFIX = "features_extractor.extractors.frame."
CNN_SUFFIX_PREFIXES = (
    "features_extractor.cnn_extractor.",
    "pi_features_extractor.cnn_extractor.",
    "vf_features_extractor.cnn_extractor.",
)
DONOR_CNN_PREFIX = "features_extractor.cnn_extractor."


def _build_holder_env(policy_kwargs: dict):
    from sb3_contrib import MaskablePPO

    from re1_rl.async_fleet import _policy_obs_and_act_spaces
    from re1_rl.distributed.weights import _SpaceHolderEnv

    policy_obs, act_space = _policy_obs_and_act_spaces()
    return MaskablePPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs, act_space),
        policy_kwargs=policy_kwargs,
        device="cpu",
        verbose=0,
    )


def _copy_cnn_tensors(old_sd: dict, new_sd: dict) -> tuple[list[str], list[str]]:
    copied: list[str] = []
    skipped: list[str] = []

    def assign(dest_key: str, src_tensor) -> None:
        if dest_key not in new_sd:
            skipped.append(f"missing dest: {dest_key}")
            return
        new_t = new_sd[dest_key]
        if new_t.shape != src_tensor.shape:
            raise RuntimeError(
                f"shape mismatch {dest_key}: {tuple(src_tensor.shape)} -> {tuple(new_t.shape)}"
            )
        new_t.copy_(src_tensor)
        copied.append(dest_key)

    donor_keys = [k for k in old_sd if k.startswith(DONOR_CNN_PREFIX)]
    if donor_keys:
        for key in donor_keys:
            suffix = key[len(DONOR_CNN_PREFIX) :]
            src = old_sd[key]
            for prefix in CNN_SUFFIX_PREFIXES:
                assign(prefix + suffix, src)
        return copied, skipped

    legacy_keys = [k for k in old_sd if k.startswith(OLD_FRAME_PREFIX)]
    for key in legacy_keys:
        suffix = key[len(OLD_FRAME_PREFIX) :]
        src = old_sd[key]
        for prefix in CNN_SUFFIX_PREFIXES:
            assign(prefix + suffix, src)
    return copied, skipped


def _load_donor_state_dict(src_zip: Path) -> tuple[dict, int]:
    import io
    import zipfile

    import torch

    with zipfile.ZipFile(src_zip) as zf:
        raw_sd = torch.load(io.BytesIO(zf.read("policy.pth")), map_location="cpu")
        meta = json.loads(zf.read("data"))
    return raw_sd, int(meta.get("num_timesteps", 0))


def transplant_cnn(src_zip: Path, out_base: Path) -> dict[str, list[str]]:
    from re1_rl.policy_config import POLICY_KWARGS

    print(f"[cnn-graft] loading donor {src_zip}", flush=True)
    old_sd, donor_steps = _load_donor_state_dict(src_zip)
    print(f"[cnn-graft] donor num_timesteps={donor_steps:,}", flush=True)

    fresh = _build_holder_env(POLICY_KWARGS)
    new_sd = fresh.policy.state_dict()
    copied, skipped = _copy_cnn_tensors(old_sd, new_sd)

    if not copied:
        raise RuntimeError("no cnn_extractor tensors copied — check donor architecture")

    fresh.policy.load_state_dict(new_sd, strict=False)
    fresh.num_timesteps = 0

    out_base.parent.mkdir(parents=True, exist_ok=True)
    fresh.save(str(out_base))
    out_zip = out_base.with_suffix(".zip")

    from re1_rl.checkpoint_io import write_latest_pointer

    write_latest_pointer(out_base.parent, out_zip, steps=0)
    top = out_base.parent.parent / "latest.json"
    top.write_text(
        json.dumps(
            {
                "run": out_base.parent.name,
                "path": str(out_zip).replace("\\", "/"),
                "steps": 0,
                "note": "doc04_cnn_graft",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"[cnn-graft] copied {len(copied)} CNN tensors", flush=True)
    print(f"[cnn-graft] saved {out_zip}", flush=True)
    return {"copied": copied, "skipped": skipped, "donor_steps": donor_steps}


def main() -> None:
    parser = argparse.ArgumentParser(description="CNN-only graft into Doc04 Medium policy")
    parser.add_argument(
        "--src",
        type=Path,
        default=PROJECT_ROOT
        / "backups/pre_world_catalog_2026-07-17/ppo_re1_world_almanac_graft.zip",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "data/checkpoints/reward_tune_1040k/ppo_re1_0_cnn_graft",
    )
    args = parser.parse_args()
    if not args.src.is_file():
        raise SystemExit(f"donor checkpoint missing: {args.src}")
    report = transplant_cnn(args.src.resolve(), args.out.resolve())
    print(json.dumps({"copied_count": len(report["copied"]), "donor_steps": report["donor_steps"]}, indent=2))


if __name__ == "__main__":
    main()
