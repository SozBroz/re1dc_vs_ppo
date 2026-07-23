"""Scalpel a checkpoint into the current (63x84) obs layout via compatible transplant.

Finds the newest ``ppo_re1_*_steps.zip`` (or ``--src``), loads through
``load_async_learner`` (auto-transplant on space mismatch), and writes a single
survivor zip + ``latest.json``. Optionally deletes other run checkpoints.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\scalpel_frame_obs_checkpoint.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\scalpel_frame_obs_checkpoint.py --wipe-others
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


def _newest_zip(run_dir: Path) -> Path:
    zips = list(run_dir.glob("ppo_re1_*_steps.zip"))
    zips += list(run_dir.glob("ppo_re1_*_cnn_graft.zip"))
    if not zips:
        raise FileNotFoundError(f"no checkpoints under {run_dir}")
    return max(zips, key=lambda p: p.stat().st_mtime)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=None)
    ap.add_argument(
        "--out-name",
        default="ppo_re1_0_steps.zip",
        help="survivor filename under reward_tune_1040k/",
    )
    ap.add_argument("--wipe-others", action="store_true")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    from re1_rl.async_fleet import load_async_learner
    from re1_rl.env import FRAME_H, FRAME_W

    src = args.src.resolve() if args.src else _newest_zip(CKPT_DIR)
    if not src.is_file():
        print(f"missing {src}", file=sys.stderr)
        return 2

    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    out_zip = CKPT_DIR / args.out_name
    print(f"[scalpel] frame target {FRAME_H}x{FRAME_W}", flush=True)
    print(f"[scalpel] src={src}", flush=True)

    model = load_async_learner(device=str(args.device), resume=src, tb_log=None)
    # SB3 save without .zip suffix
    out_base = out_zip.with_suffix("")
    if out_zip.is_file() and out_zip.resolve() != src.resolve():
        out_zip.unlink()
    model.save(str(out_base))
    if not out_zip.is_file():
        # sb3 may write path.zip from base
        candidate = Path(str(out_base) + ".zip")
        if candidate.is_file():
            candidate.replace(out_zip)
    if not out_zip.is_file():
        print(f"[scalpel] FAIL: expected {out_zip}", file=sys.stderr)
        return 3

    steps = int(getattr(model, "num_timesteps", 0) or 0)
    meta = {
        "path": str(out_zip.relative_to(ROOT)).replace("\\", "/"),
        "steps": steps,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bytes": out_zip.stat().st_size,
        "scalpel": {
            "src": str(src),
            "frame": [FRAME_H, FRAME_W],
            "reason": "63x84 frame obs transplant",
        },
    }
    for latest in (
        CKPT_DIR / "latest.json",
        ROOT / "data" / "checkpoints" / "latest.json",
    ):
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(f"[scalpel] wrote {latest}", flush=True)

    print(
        f"[scalpel] saved {out_zip} steps={steps} bytes={out_zip.stat().st_size}",
        flush=True,
    )

    if args.wipe_others:
        kept = {out_zip.resolve(), (CKPT_DIR / "latest.json").resolve()}
        top_latest = (ROOT / "data" / "checkpoints" / "latest.json").resolve()
        kept.add(top_latest)
        removed = 0
        for p in CKPT_DIR.iterdir():
            if p.resolve() in kept:
                continue
            if p.is_file():
                p.unlink()
                removed += 1
                print(f"[scalpel] removed {p.name}", flush=True)
        # Also clear sibling run dirs under data/checkpoints except reward_tune_1040k
        parent = CKPT_DIR.parent
        for child in parent.iterdir():
            if child.resolve() == CKPT_DIR.resolve():
                continue
            if child.is_dir():
                import shutil

                shutil.rmtree(child)
                removed += 1
                print(f"[scalpel] removed dir {child.name}", flush=True)
            elif child.is_file() and child.resolve() not in kept:
                child.unlink()
                removed += 1
        for final in (ROOT / "data").glob("ppo_re1_final*.zip"):
            final.unlink()
            removed += 1
            print(f"[scalpel] removed {final.name}", flush=True)
        print(f"[scalpel] wipe removed={removed}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
