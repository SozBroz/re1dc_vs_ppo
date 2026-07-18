"""Nightly wing evaluation harness (East / Gallery / Combat).

Loads the world-almanac graft (or --ckpt) and rolls out fixed wing scenarios.
Without BizHawk, use --dry-run to print the eval plan and exit 0.

Nightly JSON shape (written to --out):
  {
    "wing": "east",
    "ckpt": "...",
    "episodes": 20,
    "pass_rate": 0.15,
    "passes": 3,
    "criteria": {...},
    "episodes_detail": [...]
  }
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_GRAFT = PROJECT_ROOT / "data" / "ppo_re1_world_almanac_graft.zip"
DEFAULT_OUT_DIR = PROJECT_ROOT / "logs" / "wing_eval"

WINGS = ("east", "gallery", "combat")


@dataclass(frozen=True)
class WingSpec:
    name: str
    curriculum: str
    init_savestate: str
    success: dict[str, Any]
    max_steps: int
    episodes_default: int


WING_SPECS: dict[str, WingSpec] = {
    "east": WingSpec(
        name="east",
        curriculum="curriculum/m0_dining_to_main_hall.json",
        init_savestate="states/jill_control_fresh.State",
        success={
            "reach_room": "106",
            "kenneth_gate_respected": True,
            "min_waypoint_index": 2,
        },
        max_steps=12_000,
        episodes_default=20,
    ),
    "gallery": WingSpec(
        name="gallery",
        curriculum="curriculum/m0_dining_to_main_hall.json",
        init_savestate="states/checkpoints/wp_gallery_117.State",
        success={
            "reach_room": "117",
            "gallery_switches": 6,
            "star_crest_acquired": True,
        },
        max_steps=18_000,
        episodes_default=20,
    ),
    "combat": WingSpec(
        name="combat",
        curriculum="curriculum/m0_dining_to_main_hall.json",
        init_savestate="states/checkpoints/wp_combat_zombie.State",
        success={
            "enemy_kills_min": 1,
            "hp_floor": 1,
            "combat_action_required": True,
        },
        max_steps=6_000,
        episodes_default=20,
    ),
}


def resolve_ckpt(ckpt: str | None, graft: Path) -> Path:
    if ckpt:
        p = Path(ckpt)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p
    return graft


def emu_available() -> bool:
    try:
        from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: F401
    except ImportError:
        return False
    return True


def build_plan(
    wing: str,
    ckpt_path: Path,
    episodes: int,
    seed: int,
    dry_run: bool,
) -> dict[str, Any]:
    spec = WING_SPECS[wing]
    return {
        "schema": "re1_wing_eval_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "wing": wing,
        "ckpt": str(ckpt_path),
        "ckpt_exists": ckpt_path.is_file(),
        "episodes": episodes,
        "seed": seed,
        "dry_run": dry_run,
        "emu_required": not dry_run,
        "spec": {
            "curriculum": spec.curriculum,
            "init_savestate": spec.init_savestate,
            "max_steps": spec.max_steps,
            "success": spec.success,
        },
        "steps": [
            f"load MaskablePPO from {ckpt_path.name}",
            f"reset env with {spec.init_savestate}",
            f"roll out {episodes} episodes (max {spec.max_steps} steps each)",
            f"score against success criteria: {spec.success}",
            "write pass_rate + per-episode detail JSON",
        ],
    }


def run_eval(
    wing: str,
    ckpt_path: Path,
    episodes: int,
    seed: int,
) -> dict[str, Any]:
    """Live eval stub — raises until BizHawk rollout wiring lands."""
    raise NotImplementedError(
        "Live wing eval requires BizHawk + RE1Env rollout wiring. "
        "Use --dry-run until savestates and emu bridge are attached."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Nightly RE1 wing evaluation harness")
    p.add_argument(
        "--wing",
        choices=WINGS,
        required=True,
        help="Evaluation wing: east (1F), gallery (117), or combat",
    )
    p.add_argument(
        "--ckpt",
        default=None,
        help="Checkpoint zip (default: data/ppo_re1_world_almanac_graft.zip)",
    )
    p.add_argument(
        "--graft",
        type=Path,
        default=DEFAULT_GRAFT,
        help="Default graft zip when --ckpt is omitted",
    )
    p.add_argument("--episodes", type=int, default=None, help="Rollout count")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON output path (default: logs/wing_eval/<wing>_<ts>.json)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print eval plan without BizHawk; exit 0",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    spec = WING_SPECS[args.wing]
    episodes = args.episodes if args.episodes is not None else spec.episodes_default
    ckpt_path = resolve_ckpt(args.ckpt, args.graft)

    if args.dry_run:
        plan = build_plan(args.wing, ckpt_path, episodes, args.seed, dry_run=True)
        print(json.dumps(plan, indent=2))
        return 0

    if not emu_available():
        print(
            "ERROR: BizHawk bridge not importable. Use --dry-run or install emu deps.",
            file=sys.stderr,
        )
        return 2

    if not ckpt_path.is_file():
        print(f"ERROR: checkpoint not found: {ckpt_path}", file=sys.stderr)
        return 2

    try:
        result = run_eval(args.wing, ckpt_path, episodes, args.seed)
    except NotImplementedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out = args.out
    if out is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = DEFAULT_OUT_DIR / f"{args.wing}_{ts}.json"
    out = out if out.is_absolute() else PROJECT_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    print(f"pass_rate={result.get('pass_rate', 0.0):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
