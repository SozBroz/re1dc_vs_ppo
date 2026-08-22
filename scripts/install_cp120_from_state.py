"""Install cp120 (yawn_cutscene_210) from a post-cinema BizHawk savestate.

Use after you have walked forward, watched the intro cinema settle, and are
standing in the Yawn fight. Copies the given ``.State`` into the curated cell
slot and patches the cp119-derived sidecar with ``210:yawn``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.go_explore_capture import compute_quality
from re1_rl.yawn_cutscene_checkpoint import YAWN_CUTSCENE_KEY
from re1_rl.yawn_rails_sync import (
    CELL_META_NAME,
    CELL_SIDECAR_NAME,
    CELL_STATE_NAME,
    try_install_yawn_cell,
    yawn_rails_root,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Install cp120 from post-cinema .State")
    ap.add_argument(
        "--state",
        type=Path,
        required=True,
        help="BizHawk savestate after Yawn intro cinema (fight start)",
    )
    ap.add_argument(
        "--pred-sidecar",
        type=Path,
        default=ROOT / "states/yawn_rails/cells/cp119/cell.sidecar.json",
    )
    args = ap.parse_args()

    state_path = Path(args.state).resolve()
    if not state_path.is_file():
        print(f"ERROR: missing state {state_path}", file=sys.stderr)
        return 1
    pred_sidecar = Path(args.pred_sidecar).resolve()
    if not pred_sidecar.is_file():
        print(f"ERROR: missing predecessor sidecar {pred_sidecar}", file=sys.stderr)
        return 1

    sidecar = json.loads(pred_sidecar.read_text(encoding="utf-8-sig"))
    progress = sidecar.setdefault("progress", {})
    observed = set(progress.get("observed_cutscenes") or [])
    observed.add(YAWN_CUTSCENE_KEY)
    progress["observed_cutscenes"] = sorted(observed)
    sidecar["captured_room_id"] = "210"

    staging = yawn_rails_root(ROOT) / ".staging" / "cp120_manual"
    if staging.is_dir():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copy2(state_path, staging / CELL_STATE_NAME)
    (staging / CELL_SIDECAR_NAME).write_text(
        json.dumps(sidecar, indent=2) + "\n",
        encoding="utf-8",
    )

    quality = list(
        compute_quality(
            {"room_id": "210", "hp": 84, "inventory": [], "in_control": True},
            ever_held=set(),
        )
    )
    row = {
        "checkpoint_id": "yawn_cutscene_210",
        "checkpoint_index": 120,
        "room_id": "210",
        "next_checkpoint_id": "yawn_moon_210",
        "inventory_feasible": True,
        "inventory_free_slots": 2,
        "next_slots_needed": 1,
        "captured_in_box_room": False,
    }
    (staging / CELL_META_NAME).write_text(
        json.dumps({**row, "quality": quality}, indent=2) + "\n",
        encoding="utf-8",
    )

    ok = try_install_yawn_cell(
        ROOT,
        checkpoint_index=120,
        staged_dir=staging,
        quality=quality,
        row=row,
        force=True,
    )
    print(f"[cp120] install={'OK' if ok else 'FAILED'} from {state_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
