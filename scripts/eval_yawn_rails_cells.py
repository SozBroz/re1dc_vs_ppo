"""Equal held-out per-cell evaluation for Yawn rails atomic resets.

Without BizHawk, use --dry-run to print the equal-weight schedule and exit 0.

Examples:
  python scripts/eval_yawn_rails_cells.py --dry-run
  python scripts/eval_yawn_rails_cells.py --dry-run --episodes-per-cell 8
  set RE1_YAWN_EVAL_INTERVAL_UPDATES=5   # learner side-job equal-weight log
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.yawn_rails import load_manifest, validate_manifest_cells  # noqa: E402
from re1_rl.yawn_rails_eval import (  # noqa: E402
    append_eval_report,
    build_dry_run_plan,
    equal_weight_eval_schedule,
    eval_jsonl_path,
    list_eval_cells,
    reset_options_for_eval_slot,
)


DEFAULT_CURRICULUM = PROJECT_ROOT / "curriculum" / "yawn_rails_one_leg.json"


def load_stage(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def emu_available() -> bool:
    try:
        from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: F401
    except ImportError:
        return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Equal per-cell held-out eval for Yawn rails"
    )
    p.add_argument(
        "--curriculum",
        type=Path,
        default=DEFAULT_CURRICULUM,
        help="Curriculum JSON (default: yawn_rails_one_leg)",
    )
    p.add_argument("--ckpt", default=None, help="Optional policy zip for live eval")
    p.add_argument(
        "--episodes-per-cell",
        type=int,
        default=4,
        help="Episodes per atomic cell (equal weight)",
    )
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON report path (default: logs/yawn_rails_cell_eval_<ts>.json)",
    )
    p.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Append report to this JSONL (default: logs/yawn_rails_cell_eval.jsonl)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print equal-weight plan + validate manifest; no BizHawk",
    )
    p.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate manifest/cp03 wiring; exit non-zero on errors",
    )
    return p.parse_args(argv)


def run_live_eval(
    stage: dict[str, Any],
    schedule: list[dict[str, Any]],
    *,
    ckpt: str | None,
    seed: int,
) -> dict[str, Any]:
    """Live equal-weight rollouts — requires BizHawk + policy wiring."""
    raise NotImplementedError(
        "Live yawn cell eval requires BizHawk + MaskablePPO rollout wiring. "
        "Use --dry-run for schedule/manifest validation; worker/learner can tag "
        "held_out_eval episodes via reset options from equal_weight_eval_schedule."
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    curriculum = (
        args.curriculum
        if args.curriculum.is_absolute()
        else PROJECT_ROOT / args.curriculum
    )
    stage = load_stage(curriculum)

    errors = validate_manifest_cells(PROJECT_ROOT, stage, require_contiguous_prefix=5)
    if args.validate_only:
        payload = {
            "ok": not errors,
            "errors": errors,
            "manifest": stage.get("cells_manifest"),
            "cells": [
                {
                    "checkpoint_index": r.get("checkpoint_index"),
                    "checkpoint_id": r.get("checkpoint_id"),
                    "state_path": r.get("state_path"),
                }
                for r in load_manifest(PROJECT_ROOT, stage).get("cells", [])
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0 if not errors else 2

    if errors:
        print("WARNING: manifest validation issues:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)

    if args.dry_run:
        plan = build_dry_run_plan(
            PROJECT_ROOT,
            stage,
            episodes_per_cell=int(args.episodes_per_cell),
            ckpt=args.ckpt,
            seed=int(args.seed),
        )
        plan["manifest_errors"] = errors
        print(json.dumps(plan, indent=2))
        return 0 if not errors else 2

    if not emu_available():
        print(
            "ERROR: BizHawk bridge not importable. Use --dry-run or --validate-only.",
            file=sys.stderr,
        )
        return 2

    cells = list_eval_cells(PROJECT_ROOT, stage)
    schedule = equal_weight_eval_schedule(
        cells, episodes_per_cell=int(args.episodes_per_cell)
    )
    # Expose first-slot options for callers / future live path.
    _ = reset_options_for_eval_slot(schedule[0]) if schedule else None
    try:
        report = run_live_eval(
            stage, schedule, ckpt=args.ckpt, seed=int(args.seed)
        )
    except NotImplementedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out = args.out
    if out is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = PROJECT_ROOT / "logs" / f"yawn_rails_cell_eval_{ts}.json"
    out = out if out.is_absolute() else PROJECT_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    jsonl = args.jsonl or eval_jsonl_path(PROJECT_ROOT)
    jsonl = jsonl if jsonl.is_absolute() else PROJECT_ROOT / jsonl
    append_eval_report(jsonl, report)
    print(json.dumps({"out": str(out), "jsonl": str(jsonl), **{
        k: report.get(k)
        for k in ("macro_avg_success", "worst_decile_success", "n_cells")
    }}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
