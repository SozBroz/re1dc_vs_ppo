"""Behavioral cloning warm-start for MaskablePPO (scaffold).

Expected demo format (per file under --demo-dir, default data/demos/):
  *.npz with arrays:
    obs/proprio      (T, proprio_dim) float32
    obs/goal         (T, goal_dim) float32
    obs/frame        (T, H, W, C) uint8   — optional if using RAM-only BC
    action           (T,) int64           — env action index
    action_mask      (T, n_actions) bool  — optional; zeros illegal slots
    episode_id       str in npz metadata or filename stem

Record demos via scripts/play_human.py (export pipeline TBD). This script
exits with a clear message when no demos are present; the BC training loop
is stubbed until human trajectories land.

Usage:
    python scripts/train_bc.py --demo-dir data/demos/east_wing
    python scripts/train_bc.py --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DEMO_DIR = PROJECT_ROOT / "data" / "demos"
DEFAULT_OUT = PROJECT_ROOT / "data" / "checkpoints" / "bc_warmstart"


def find_demos(demo_dir: Path) -> list[Path]:
    if not demo_dir.is_dir():
        return []
    return sorted(demo_dir.glob("**/*.npz"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="BC warm-start scaffold for RE1 MaskablePPO",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--demo-dir",
        type=Path,
        default=DEFAULT_DEMO_DIR,
        help="Directory tree of .npz trajectory files",
    )
    p.add_argument(
        "--init-ckpt",
        type=Path,
        default=None,
        help="Optional PPO zip to fine-tune (default: train features from scratch)",
    )
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    return p.parse_args(argv)


def train_bc_loop(
    demo_paths: list[Path],
    *,
    init_ckpt: Path | None,
    epochs: int,
    batch_size: int,
    lr: float,
    out: Path,
    device: str,
) -> None:
    """Stub: load demos → MaskablePPO BC objective → save warm-start zip."""
    raise NotImplementedError(
        "BC training loop not wired yet. Demos found; waiting on export format "
        "validation and MaskablePPO supervised head attachment."
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    demo_dir = args.demo_dir if args.demo_dir.is_absolute() else PROJECT_ROOT / args.demo_dir
    demos = find_demos(demo_dir)

    if not demos:
        print(
            "No BC demos found.\n"
            f"  looked in: {demo_dir}\n"
            "  expected:  *.npz trajectories (see module docstring)\n"
            "  record:    python scripts/play_human.py  (export TBD)\n"
            "  convention: data/demos/east_wing/*.npz once export lands",
            file=sys.stderr,
        )
        return 1

    print(f"found {len(demos)} demo file(s) under {demo_dir}")
    for p in demos[:5]:
        print(f"  - {p.relative_to(PROJECT_ROOT)}")
    if len(demos) > 5:
        print(f"  ... and {len(demos) - 5} more")

    try:
        from sb3_contrib import MaskablePPO  # noqa: F401
    except ImportError:
        print("ERROR: sb3_contrib not installed (MaskablePPO required).", file=sys.stderr)
        return 2

    out = args.out if args.out.is_absolute() else PROJECT_ROOT / args.out
    init_ckpt = None
    if args.init_ckpt:
        init_ckpt = args.init_ckpt if args.init_ckpt.is_absolute() else PROJECT_ROOT / args.init_ckpt

    try:
        train_bc_loop(
            demos,
            init_ckpt=init_ckpt,
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=args.lr,
            out=out,
            device=args.device,
        )
    except NotImplementedError as exc:
        print(f"BC scaffold: {exc}", file=sys.stderr)
        return 2

    print(f"saved BC warm-start to {out}.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
