#!/usr/bin/env python3
"""Regenerate planner_loyal.kills from sidecar enemies_killed_by_room, then
backfill meta.json quality to 11 dims.

Kill regen walks cells in slot order. For each cell with an enemies ledger:
  almanac_total*_ from that sidecar
  almanac_stretch*_ = delta vs previous cell that had a ledger
Paid_* fields are preserved from any existing kills block (not recoverable
from the world ledger alone).

Quality lexicon:
  (hp, total_kills, ammo, healing, slots, poison, -ink, -box, -frames,
   stretch_kills, hop_score_milli)

Unknown new dims use -99999. hop_score_milli stays -99999 unless meta.hop_score
is set. Cells with no enemies_killed_by_room leave kill dims unknown.

Default roots (whichever exist):
  <rl>/states/planner_loyal
  <recomp>/ecosystem/states/recomp_pl

Usage:
  python _tmp/_stitch_planner_loyal_quality_11.py
  python _tmp/_stitch_planner_loyal_quality_11.py --dry-run
  python _tmp/_stitch_planner_loyal_quality_11.py --skip-kill-regen
  python _tmp/_stitch_planner_loyal_quality_11.py --root D:/re1_rl/states/planner_loyal
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.go_explore_merge import CELL_META_NAME, CELL_SIDECAR_NAME
from re1_rl.pb_sidecar import enemies_killed_from_sidecar
from re1_rl.planner_loyal_cells import (
    PLANNER_LOYAL_QUALITY_LEN,
    PLANNER_LOYAL_QUALITY_UNKNOWN,
    _almanac_delta,
    hop_score_to_milli,
    stitch_planner_loyal_quality,
)


def _default_roots() -> list[Path]:
    rl = Path(os.environ.get("RE1_RL_ROOT") or ROOT)
    recomp = Path(
        os.environ.get("RE1_RECOMP_ROOT")
        or (
            "D:/re1_recomp"
            if Path("D:/re1_recomp").is_dir()
            else "C:/re1_recomp"
        )
    )
    candidates = [
        rl / "states" / "planner_loyal",
        recomp / "ecosystem" / "states" / "recomp_pl",
    ]
    return [p for p in candidates if (p / "cells").is_dir()]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _slot_dirs(root: Path) -> list[Path]:
    cells = root / "cells"
    out: list[Path] = []
    for path in cells.iterdir():
        if not path.is_dir() or not path.name.startswith("pl"):
            continue
        try:
            int(path.name[2:])
        except ValueError:
            continue
        out.append(path)
    return sorted(out, key=lambda p: int(p.name[2:]))


def _has_enemies_ledger(side: dict) -> bool:
    if "enemies_killed_by_room" in side:
        return True
    prog = side.get("progress")
    return isinstance(prog, dict) and "enemies_killed_by_room" in prog


def _preserve_paid(old: dict[str, Any] | None) -> dict[str, Any]:
    src = old if isinstance(old, dict) else {}
    out: dict[str, Any] = {
        "paid_stretch": 0,
        "paid_stretch_by_room": {},
        "paid_episode": 0,
        "paid_episode_by_room": {},
    }
    for key in out:
        if key not in src:
            continue
        try:
            if key.endswith("_by_room"):
                raw = src[key] if isinstance(src[key], dict) else {}
                out[key] = {
                    str(room): int(n)
                    for room, n in raw.items()
                    if int(n) > 0
                }
            else:
                out[key] = max(0, int(src[key]))
        except (TypeError, ValueError):
            continue
    return out


def _build_kill_audit(
    almanac_total: dict[str, dict[str, int]],
    almanac_stretch: dict[str, dict[str, int]],
    old: dict[str, Any] | None,
) -> dict[str, Any]:
    paid = _preserve_paid(old)
    return {
        **paid,
        "almanac_stretch": sum(
            sum(types.values()) for types in almanac_stretch.values()
        ),
        "almanac_stretch_by_room": almanac_stretch,
        "almanac_total": sum(sum(types.values()) for types in almanac_total.values()),
        "almanac_total_by_room": almanac_total,
    }


def regen_kills_root(root: Path, *, dry_run: bool) -> dict[str, int]:
    """Rewrite planner_loyal.kills (+ meta.kills) from enemies ledgers."""
    tallies = {
        "regen_wrote": 0,
        "regen_unchanged": 0,
        "regen_skip_no_side": 0,
        "regen_skip_no_ledger": 0,
        "regen_would_write": 0,
    }
    prev_alm: dict[str, dict[str, int]] = {}
    for slot in _slot_dirs(root):
        side_p = slot / CELL_SIDECAR_NAME
        if not side_p.is_file():
            tallies["regen_skip_no_side"] += 1
            continue
        try:
            side = _load_json(side_p)
        except (OSError, json.JSONDecodeError, TypeError):
            tallies["regen_skip_no_side"] += 1
            continue
        if not _has_enemies_ledger(side):
            tallies["regen_skip_no_ledger"] += 1
            continue

        alm = enemies_killed_from_sidecar(side)
        stretch = _almanac_delta(alm, prev_alm)
        prev_alm = alm
        pl = side.get("planner_loyal")
        if not isinstance(pl, dict):
            pl = {}
            side["planner_loyal"] = pl
        old = pl.get("kills") if isinstance(pl.get("kills"), dict) else None
        new_kills = _build_kill_audit(alm, stretch, old)
        meta_p = slot / CELL_META_NAME
        meta: dict[str, Any] | None = None
        if meta_p.is_file():
            try:
                meta = _load_json(meta_p)
            except (OSError, json.JSONDecodeError, TypeError):
                meta = None

        same_side = old == new_kills
        same_meta = True
        if meta is not None:
            same_meta = meta.get("kills") == new_kills
        if same_side and same_meta:
            tallies["regen_unchanged"] += 1
            continue

        if dry_run:
            tallies["regen_would_write"] += 1
            print(
                f"{slot.name}: regen "
                f"total={new_kills['almanac_total']} "
                f"stretch={new_kills['almanac_stretch']} "
                f"(old_total={None if old is None else old.get('almanac_total')}, "
                f"old_stretch={None if old is None else old.get('almanac_stretch')})",
                flush=True,
            )
            continue

        pl["kills"] = new_kills
        _write_json(side_p, side)
        if meta is not None:
            meta["kills"] = new_kills
            _write_json(meta_p, meta)
        tallies["regen_wrote"] += 1
        print(
            f"{slot.name}: regen_wrote "
            f"total={new_kills['almanac_total']} "
            f"stretch={new_kills['almanac_stretch']}",
            flush=True,
        )
    print(
        f"REGEN {root} dry_run={int(dry_run)} "
        f"wrote={tallies['regen_wrote']} would={tallies['regen_would_write']} "
        f"unchanged={tallies['regen_unchanged']} "
        f"skip_ledger={tallies['regen_skip_no_ledger']} "
        f"skip_side={tallies['regen_skip_no_side']}",
        flush=True,
    )
    return tallies


def _kills_block(meta: dict, side: dict) -> dict:
    for src in (meta.get("kills"), (side.get("planner_loyal") or {}).get("kills")):
        if isinstance(src, dict) and src:
            return src
    return {}


def _known_int(block: dict, key: str) -> int | None:
    if key not in block:
        return None
    try:
        return max(0, int(block[key]))
    except (TypeError, ValueError):
        return None


def _known_hop(meta: dict) -> float | None:
    if "hop_score" not in meta:
        return None
    raw = meta.get("hop_score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def stitch_cell(slot: Path, *, dry_run: bool) -> str:
    meta_p = slot / CELL_META_NAME
    if not meta_p.is_file():
        return "skip_no_meta"
    try:
        meta = _load_json(meta_p)
    except (OSError, json.JSONDecodeError, TypeError):
        return "skip_bad_meta"
    raw_q = list(meta.get("quality") or [])
    if not raw_q:
        return "skip_empty_quality"

    side: dict = {}
    side_p = slot / CELL_SIDECAR_NAME
    if side_p.is_file():
        try:
            side = _load_json(side_p)
        except (OSError, json.JSONDecodeError, TypeError):
            side = {}

    kills = _kills_block(meta, side)
    # Only trust almanac dims when an enemies ledger exists (post-regen).
    total = stretch = None
    if _has_enemies_ledger(side) and kills:
        total = _known_int(kills, "almanac_total")
        stretch = _known_int(kills, "almanac_stretch")
    hop = _known_hop(meta)
    new_q = stitch_planner_loyal_quality(
        raw_q,
        total_kills=total,
        stretch_kills=stretch,
        hop_score=hop,
    )
    assert len(new_q) == PLANNER_LOYAL_QUALITY_LEN
    if list(raw_q) == new_q:
        return "unchanged"
    if dry_run:
        return f"would_write {raw_q} -> {new_q}"
    meta["quality"] = new_q
    meta_p.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return f"wrote {new_q}"


def stitch_root(root: Path, *, dry_run: bool) -> dict[str, int]:
    tallies: dict[str, int] = {
        "wrote": 0,
        "unchanged": 0,
        "skip_no_meta": 0,
        "skip_bad_meta": 0,
        "skip_empty_quality": 0,
        "would_write": 0,
    }
    for slot in _slot_dirs(root):
        status = stitch_cell(slot, dry_run=dry_run)
        key = status.split(" ", 1)[0]
        tallies[key] = tallies.get(key, 0) + 1
        if status.startswith("wrote") or status.startswith("would_write"):
            print(f"{slot.name}: {status}", flush=True)
    unk = PLANNER_LOYAL_QUALITY_UNKNOWN
    print(
        f"STITCH {root} dry_run={int(dry_run)} "
        f"wrote={tallies.get('wrote', 0)} would={tallies.get('would_write', 0)} "
        f"unchanged={tallies.get('unchanged', 0)} "
        f"skip_empty={tallies.get('skip_empty_quality', 0)} "
        f"unk_sentinel={unk} hop_milli_unk_example={hop_score_to_milli(None)}",
        flush=True,
    )
    return tallies


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        action="append",
        type=Path,
        default=None,
        help="Planner-loyal root (contains cells/). Repeatable.",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--skip-kill-regen",
        action="store_true",
        help="Do not regenerate kills from enemies_killed_by_room first.",
    )
    args = ap.parse_args()
    roots = list(args.root) if args.root else _default_roots()
    if not roots:
        print("no planner_loyal roots found", file=sys.stderr)
        return 2
    for root in roots:
        resolved = root.resolve()
        if not args.skip_kill_regen:
            regen_kills_root(resolved, dry_run=bool(args.dry_run))
        stitch_root(resolved, dry_run=bool(args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
