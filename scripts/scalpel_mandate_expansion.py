"""Scalpel live 1024/[512,512] planner-loyal zip into mandate 2048/[1024]^3 + FiLM.

Net2Net-style function-preserving widen for the actor path; value trunk is
reinitialized (reward targets change under S_core + local combat).

Usage:
  python scripts/scalpel_mandate_expansion.py --src path/to/ppo_re1_N_steps.zip \\
      --out path/to/ppo_re1_N_mandate_expansion
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


@torch.no_grad()
def _copy_widen_2d(old: torch.Tensor, new: torch.Tensor) -> None:
    rows = min(old.shape[0], new.shape[0])
    cols = min(old.shape[1], new.shape[1])
    new.zero_()
    new[:rows, :cols].copy_(old[:rows, :cols])


@torch.no_grad()
def _copy_widen_1d(old: torch.Tensor, new: torch.Tensor) -> None:
    n = min(old.shape[0], new.shape[0])
    new.zero_()
    new[:n].copy_(old[:n])


@torch.no_grad()
def transplant_mandate(old_sd: dict, new_sd: dict) -> dict:
    """Copy compatible tensors; widen fusion/π; leave vf fresh; FiLM stays identity."""
    report = {"copied": 0, "widened": 0, "skipped_vf": 0, "skipped": []}
    for key, new_t in new_sd.items():
        # Fresh critic (mandate: reward targets changed).
        if key.startswith("mlp_extractor.value_net") or key.startswith("value_net"):
            report["skipped_vf"] += 1
            continue
        # New FiLM layers: keep identity init (zeros weight, γ=1 β=0 bias).
        if "goal_film" in key:
            continue
        if key not in old_sd:
            report["skipped"].append(f"missing_old {key}")
            continue
        old_t = old_sd[key]
        if old_t.shape == new_t.shape:
            new_t.copy_(old_t)
            report["copied"] += 1
            continue
        if old_t.dim() == 2 and new_t.dim() == 2:
            _copy_widen_2d(old_t, new_t)
            report["widened"] += 1
            continue
        if old_t.dim() == 1 and new_t.dim() == 1:
            _copy_widen_1d(old_t, new_t)
            report["widened"] += 1
            continue
        report["skipped"].append(f"{key} {tuple(old_t.shape)}->{tuple(new_t.shape)}")

    # Extra π depth: policy_net.4 (third Linear) — identity on inherited 512 ch.
    # SB3 Sequential: 0=Linear, 1=Act, 2=Linear, 3=Act, 4=Linear for 3-layer.
    w4 = new_sd.get("mlp_extractor.policy_net.4.weight")
    b4 = new_sd.get("mlp_extractor.policy_net.4.bias")
    if w4 is not None and b4 is not None:
        w4.zero_()
        b4.zero_()
        n = min(512, w4.shape[0], w4.shape[1])
        for i in range(n):
            w4[i, i] = 1.0
        report["widened"] += 1

    # action_net: zero columns beyond old 512 so new latent channels are inert.
    aw = new_sd.get("action_net.weight")
    if aw is not None and aw.shape[1] > 512:
        aw[:, 512:].zero_()
        report["widened"] += 1

    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    from re1_rl.async_fleet import PPO_HYPERPARAMS, _make_learner_cls, _policy_obs_and_act_spaces
    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.policy_config import POLICY_KWARGS

    src = args.src
    if not src.is_file():
        print(f"missing src {src}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(src) as zf:
        old_sd = torch.load(io.BytesIO(zf.read("policy.pth")), map_location="cpu", weights_only=True)
        meta = json.loads(zf.read("data"))
    donor_steps = int(meta.get("num_timesteps", 0) or 0)

    LearnerCls, extra = _make_learner_cls()
    policy_obs, act_space = _policy_obs_and_act_spaces()
    model = LearnerCls(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs, act_space),
        verbose=0,
        device=args.device,
        policy_kwargs=POLICY_KWARGS,
        **{**PPO_HYPERPARAMS, **extra},
    )
    new_sd = model.policy.state_dict()
    report = transplant_mandate(old_sd, new_sd)
    model.policy.load_state_dict(new_sd, strict=False)
    model.num_timesteps = donor_steps

    out = args.out
    if out.suffix == ".zip":
        out_base = out.with_suffix("")
    else:
        out_base = out
    out_base.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out_base))
    out_zip = out_base if out_base.suffix == ".zip" else Path(str(out_base) + ".zip")

    n = sum(p.numel() for p in model.policy.parameters())
    print(f"[scalpel] src={src.name} steps={donor_steps}")
    print(f"[scalpel] unique_params={n:,}")
    print(f"[scalpel] report={report}")
    print(f"[scalpel] wrote {out_zip}")
    if n > 16_000_000:
        print("[scalpel] FAIL over HARD_CAP", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
