#!/usr/bin/env python3
"""Promote a fat planner-loyal tape cell into ``backups/Crystals_in_time``.

Copies a minted ``states/planner_loyal/cells/plNN/`` dir (state + sidecar +
meta + ``leg_replay.json`` + ``leg_policy.npz``) into
``backups/Crystals_in_time/planner_rooms/<unit>/`` verbatim (live ``cell.pst``
keeps its name — recomp format, not BizHawk ``cell.State``), plus a
``crystal_meta.json`` with SHA chain info.

SHA-chain check: the tape's ``from_state_sha256`` must match the predecessor
live cell's state file (``pl_first-1`` for segments, ``slot-1`` for solos),
unless ``--force``.

Usage:
  venv\\Scripts\\python.exe scripts/crystalize_planner_room_tape.py --slot 24
  venv\\Scripts\\python.exe scripts/crystalize_planner_room_tape.py --segment l_hallway_bullets
  venv\\Scripts\\python.exe scripts/crystalize_planner_room_tape.py --slot 42 --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARCHIVE_REL = Path("backups/Crystals_in_time/planner_rooms")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _state_file(cell_dir: Path) -> Path | None:
    for name in ("cell.pst", "cell.State"):
        p = cell_dir / name
        if p.is_file():
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--slot", type=int, default=None, help="exit plNN slot")
    grp.add_argument("--segment", type=str, default=None, help="room segment id")
    ap.add_argument("--force", action="store_true", help="install despite chain mismatch")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from re1_rl.planner_loyal_cells import cell_slot_dir, planner_loyal_root

    if args.segment:
        from re1_rl.planner_room_segments import clear_room_segments_cache, load_room_segments

        clear_room_segments_cache()
        data = load_room_segments(ROOT, force=True)
        seg = next(
            (s for s in (data.get("segments") or []) if s.get("id") == args.segment),
            None,
        )
        if seg is None:
            print(f"ERROR: unknown segment {args.segment!r}", flush=True)
            return 2
        slot = int(seg["pl_last"])
        from_slot: int | None = int(seg["pl_first"]) - 1
        unit = str(seg["id"])
    else:
        slot = int(args.slot)
        from_slot = slot - 1
        unit = f"pl{slot:02d}_solo"

    cells_root = planner_loyal_root(ROOT)
    src = cell_slot_dir(cells_root, slot)
    state_p = _state_file(src)
    tape_p = src / "leg_replay.json"
    policy_p = src / "leg_policy.npz"
    missing = [n for n, p in (
        ("state", state_p),
        ("leg_replay.json", tape_p if tape_p.is_file() else None),
    ) if p is None]
    if missing:
        print(f"ERROR: {src} missing {missing} — capture the tape first", flush=True)
        return 2

    tape = json.loads(tape_p.read_text(encoding="utf-8"))
    want_from = str(tape.get("from_state_sha256") or "")
    pred_sha = ""
    if from_slot is not None and from_slot >= 0:
        pred_state = _state_file(cell_slot_dir(cells_root, from_slot))
        pred_sha = _sha256(pred_state) if pred_state else ""
    chain_ok = bool(want_from) and bool(pred_sha) and want_from == pred_sha
    print(
        f"[crystalize] unit={unit} slot=pl{slot:02d} from=pl{from_slot:02d} "
        f"chain_ok={chain_ok} policy={'yes' if policy_p.is_file() else 'no'}",
        flush=True,
    )
    if not chain_ok and not args.force:
        print(
            "ERROR: SHA chain mismatch (predecessor state != tape from_state_sha256). "
            "Re-capture from the current predecessor or pass --force.",
            flush=True,
        )
        return 3

    dest = ROOT / ARCHIVE_REL / unit
    if args.dry_run:
        print(f"[crystalize] dry-run → {dest}", flush=True)
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("cell.pst", "cell.State", "cell.sidecar.json", "meta.json",
                 "leg_replay.json", "leg_policy.npz"):
        src_f = src / name
        if src_f.is_file():
            shutil.copy2(src_f, dest / name)
    crystal_meta = {
        "unit": unit,
        "to_pl_slot": slot,
        "from_pl_slot": from_slot,
        "chain_ok": bool(chain_ok),
        "forced": bool(args.force and not chain_ok),
        "to_state_sha256": _sha256(state_p),
        "from_state_sha256": want_from,
        "tape_to_state_sha256": str(tape.get("to_state_sha256") or ""),
        "room_segment_id": tape.get("room_segment_id"),
        "has_policy": policy_p.is_file(),
        "installed_unix": int(time.time()),
    }
    (dest / "crystal_meta.json").write_text(
        json.dumps(crystal_meta, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[crystalize] installed {dest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
