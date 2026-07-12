"""Transplant resume zip into current obs spaces (84x77 frame after pillar prune).

Copies compatible tensors; NatureCNN linear reinits for flatten 3136->2688.
Does not start training.

Usage (on learner host, after code sync):
  python scripts/transplant_frame_84x77.py
  python scripts/transplant_frame_84x77.py --src data/checkpoints/reward_tune_1040k/ppo_re1_11160000_steps.zip
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from re1_rl.async_fleet import load_async_learner
    from re1_rl.checkpoint_io import (
        atomic_model_save,
        find_latest_checkpoint,
        is_valid_checkpoint,
        write_latest_pointer,
    )
    from re1_rl.env import FRAME_SHAPE, FRAME_SHAPE_CHW

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--src",
        type=Path,
        default=None,
        help="source checkpoint zip (default: newest numbered under reward_tune_1040k)",
    )
    ap.add_argument(
        "--run-dir",
        type=Path,
        default=ROOT / "data" / "checkpoints" / "reward_tune_1040k",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output zip path (default: <run-dir>/ppo_re1_frame_84x77.zip)",
    )
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    run_dir = args.run_dir
    src = args.src
    if src is None:
        src = find_latest_checkpoint(run_dir)
    if src is None or not is_valid_checkpoint(src):
        # fall back to final alias
        for cand in (
            ROOT / "data" / "ppo_re1_final_reward_tune_1040k.zip",
            ROOT / "data" / "ppo_re1_final.zip",
        ):
            if is_valid_checkpoint(cand):
                src = cand
                break
    if src is None or not is_valid_checkpoint(src):
        print(f"[transplant] FAIL: no valid source under {run_dir}", flush=True)
        return 2

    out = args.out or (run_dir / "ppo_re1_frame_84x77.zip")
    print(
        f"[transplant] src={src} -> out={out} "
        f"frame_hwc={FRAME_SHAPE} frame_chw={FRAME_SHAPE_CHW}",
        flush=True,
    )

    if not args.no_backup:
        bak = src.with_name(src.stem + "_pre_frame_84x77.zip")
        if not bak.exists():
            shutil.copy2(src, bak)
            print(f"[transplant] backup={bak}", flush=True)

    model = load_async_learner(device=args.device, resume=Path(src), tb_log=None)
    frame_space = model.observation_space.spaces["frame"]
    print(
        f"[transplant] loaded timesteps={model.num_timesteps} "
        f"frame_space={frame_space.shape} expect_chw={FRAME_SHAPE_CHW}",
        flush=True,
    )
    if tuple(frame_space.shape) not in (FRAME_SHAPE_CHW, FRAME_SHAPE):
        print(
            f"[transplant] WARN: unexpected frame shape {frame_space.shape}",
            flush=True,
        )

    saved = atomic_model_save(model, out)
    # Also refresh final alias used by some launchers / latest.json
    final_alias = ROOT / "data" / "ppo_re1_final_reward_tune_1040k.zip"
    shutil.copy2(saved, final_alias)
    write_latest_pointer(run_dir, saved, steps=int(model.num_timesteps))
    write_latest_pointer(ROOT / "data" / "checkpoints", final_alias, steps=int(model.num_timesteps))
    print(
        f"[transplant] PASS saved={saved} alias={final_alias} "
        f"timesteps={model.num_timesteps}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
