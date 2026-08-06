"""Add a modest action-head bias prior to a PPO zip (in place or --out).

Default: neutral ``attack`` += log(2) ≈ +0.69 on the resume checkpoint.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RUN = "reward_tune_1040k"
CKPT_DIR = ROOT / "data" / "checkpoints" / RUN
_STEPS_RE = re.compile(r"ppo_re1_(\d+)_steps\.zip$", re.I)


def _newest_steps_zip(run_dir: Path) -> Path:
    zips = [p for p in run_dir.glob("ppo_re1_*_steps.zip") if _STEPS_RE.search(p.name)]
    if not zips:
        raise FileNotFoundError(f"no ppo_re1_*_steps.zip under {run_dir}")
    return max(zips, key=lambda p: int(_STEPS_RE.search(p.name).group(1)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None, help="Default: overwrite --src")
    ap.add_argument("--device", default="cpu")
    ap.add_argument(
        "--boost-actions",
        default="attack",
        help="Comma-separated ACTION_NAMES (default: attack).",
    )
    ap.add_argument(
        "--boost-factor",
        type=float,
        default=2.0,
        help="Add log(factor) to bias (default 2 ≈ +0.69).",
    )
    ap.add_argument(
        "--update-latest",
        action="store_true",
        help="Rewrite data/checkpoints/*/latest.json to point at the output zip.",
    )
    args = ap.parse_args()

    from re1_rl.action_head_surgery import boost_action_logits, format_boost_report
    from re1_rl.async_fleet import load_async_learner

    names = [s.strip() for s in str(args.boost_actions).split(",") if s.strip()]
    if not names:
        print("no --boost-actions", file=sys.stderr)
        return 2

    src = args.src.resolve() if args.src else _newest_steps_zip(CKPT_DIR)
    out = args.out.resolve() if args.out else src
    if not src.is_file():
        print(f"missing {src}", file=sys.stderr)
        return 2

    print(f"[boost] src={src}", flush=True)
    print(f"[boost] out={out}", flush=True)
    print(f"[boost] actions={names} factor={args.boost_factor}", flush=True)

    model = load_async_learner(device=str(args.device), resume=src, tb_log=None)
    report = boost_action_logits(
        model, actions=names, factor=float(args.boost_factor)
    )
    for line in format_boost_report(report):
        print(f"[boost] {line}", flush=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    out_base = out.with_suffix("")
    if out.is_file() and out.resolve() != src.resolve():
        out.unlink()
    model.save(str(out_base))
    if not out.is_file():
        candidate = Path(str(out_base) + ".zip")
        if candidate.is_file():
            candidate.replace(out)
    if not out.is_file():
        print(f"[boost] FAIL: expected {out}", file=sys.stderr)
        return 3

    steps = int(getattr(model, "num_timesteps", 0) or 0)
    print(f"[boost] saved {out} steps={steps} bytes={out.stat().st_size}", flush=True)

    if args.update_latest:
        try:
            rel = str(out.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            rel = str(out).replace("\\", "/")
        meta = {
            "path": rel,
            "steps": steps,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "bytes": out.stat().st_size,
            "action_bias_boost": report,
        }
        for latest in (
            CKPT_DIR / "latest.json",
            ROOT / "data" / "checkpoints" / "latest.json",
        ):
            latest.parent.mkdir(parents=True, exist_ok=True)
            latest.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            print(f"[boost] wrote {latest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
