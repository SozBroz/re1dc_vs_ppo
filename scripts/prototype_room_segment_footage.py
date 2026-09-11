#!/usr/bin/env python3
"""Prototype room-segment chaining for dining emblem→tea and L hallway bullets.

Does not launch the fleet. Validates segment resolution, shared budgets, and
additive S_room bookkeeping. Optionally prints BizHawk replay hints for the
two prototype segments.

Usage:
  python scripts/prototype_room_segment_footage.py
  python scripts/prototype_room_segment_footage.py --enable-env-check
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--enable-env-check",
        action="store_true",
        help="Set RE1_PLANNER_ROOM_SEGMENTS=1 in this process and re-check flags",
    )
    ap.add_argument(
        "--simulate-scores",
        action="store_true",
        help="Write a fake best S_room for both prototype segments (dry demo)",
    )
    args = ap.parse_args()

    if args.enable_env_check:
        os.environ["RE1_PLANNER_ROOM_SEGMENTS"] = "1"
        os.environ["RE1_PLANNER_ROOM_SEGMENTS_PROTOTYPE_ONLY"] = "1"

    from re1_rl.planner_room_segments import (
        RoomSegmentTracker,
        clear_room_segments_cache,
        is_segment_midhop,
        load_room_segments,
        record_best_s_room,
        room_segments_enabled,
        segment_budget_frames,
        segment_covering_slot,
        segment_for_tip_slot,
    )

    clear_room_segments_cache()
    data = load_room_segments(ROOT, force=True)
    protos = [s for s in (data.get("segments") or []) if s.get("prototype")]
    print(f"segments_file={ROOT / 'data' / 'planner_loyal_room_segments.json'}")
    print(f"room_segments_enabled={room_segments_enabled()}")
    print(f"prototype_count={len(protos)}")
    for seg in protos:
        budget = segment_budget_frames(seg, ROOT)
        print(
            f"  - {seg['id']}: pl{int(seg['pl_first']):02d}-pl{int(seg['pl_last']):02d} "
            f"budget_frames={budget} ({budget / 3600:.1f} min) finish={seg.get('finish')}"
        )

    # Tip resolution for the two prototypes.
    cases = [
        ("opening tip pl00", 0, "opening_emblem_to_tea"),
        ("L tip pl21", 21, "l_hallway_bullets"),
    ]
    for label, tip, expect in cases:
        hit = segment_for_tip_slot(tip, ROOT)
        ok = hit is not None and hit.get("id") == expect
        print(f"tip_resolve {label} -> {hit.get('id') if hit else None} {'OK' if ok else 'FAIL'}")
        if not ok and room_segments_enabled():
            return 1

    # Midhop flags with explicit slot_index steps (opening remint style).
    open_steps = [
        {"slot_index": 1, "op": "acquire", "beat_id": "emblem_105"},
        {"slot_index": 2, "op": "traverse", "beat_id": "kenneth_104"},
    ]
    l_steps = [
        {"slot_index": 22, "op": "traverse"},
        {"slot_index": 23, "op": "acquire"},
        {"slot_index": 24, "op": "traverse"},
    ]
    if room_segments_enabled():
        assert is_segment_midhop(0, ROOT, steps=open_steps)
        assert not is_segment_midhop(1, ROOT, steps=open_steps)
        assert is_segment_midhop(0, ROOT, steps=l_steps)
        assert is_segment_midhop(1, ROOT, steps=l_steps)
        assert not is_segment_midhop(2, ROOT, steps=l_steps)
        print("midhop_flags OK")
    else:
        print("midhop_flags SKIPPED (set RE1_PLANNER_ROOM_SEGMENTS=1)")

    if args.simulate_scores:
        for seg in protos:
            tr = RoomSegmentTracker.begin(seg, project_root=ROOT)
            # Fake descending hop scores.
            n = int(seg["pl_last"]) - int(seg["pl_first"]) + 1
            for i in range(n):
                tr.note_hop_success({"S": 0.55 - 0.05 * i}, 0.55 - 0.05 * i)
            rec = record_best_s_room(tr, project_root=ROOT)
            print(f"simulate {seg['id']}: S_room={tr.s_room:.4f} recorded={rec is not None}")

    # BizHawk footage hints (no emulator launch).
    print()
    print("BizHawk footage path (A/V not available on RE1-C plugin):")
    print("  1. Train/chain on recomp with RE1_PLANNER_ROOM_SEGMENTS=1")
    print("  2. Keep best S_room in data/planner_loyal_room_segment_best.json")
    print("  3. Replay champion tape / F1 live policy on BizHawk twin:")
    print("       python scripts/replay_leg.py  # restore --record-av before batch")
    print("  See docs/mansion_exit_av_verdict.md")

    # Presence check for live tips needed by prototypes.
    for slot in (0, 21):
        pst = ROOT / "states" / "planner_loyal" / "cells" / f"pl{slot:02d}" / "cell.pst"
        print(f"tip_cell pl{slot:02d} pst={'yes' if pst.is_file() else 'MISSING'}")

    biz_root = ROOT / "backups" / "planner_loyal_bizhawk_20260904"
    print(f"bizhawk_backup={'yes' if biz_root.is_dir() else 'MISSING'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
