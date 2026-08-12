#!/usr/bin/env python3
"""Repair cp57 curated cell: remove handgun box pollution, bank boss gear.

Loads ``states/yawn_rails/cells/cp57/cell.State``, fixes the global item box
and on-person inventory to the canonical post-save-room layout, then writes
``cell.State`` + refreshed ``cell.sidecar.json`` box_cache.

Curation uses direct RAM writes (north-star box exception) — not live training.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.bizhawk_paths import assert_rom_present, emuhawk_argv
from re1_rl.item_box import (
    box_pollution_reason,
    read_box,
    read_box_live,
    read_inventory,
    write_inventory_box_curation,
)
from re1_rl.memory_map import ITEM_IDS
from re1_rl.weapon_equip import policy_inventory

PORT = 5998
CELL_DIR = ROOT / "states/yawn_rails/cells/cp57"
STATE_PATH = CELL_DIR / "cell.State"
SIDECAR_PATH = CELL_DIR / "cell.sidecar.json"


def item_name(iid: int) -> str:
    return ITEM_IDS.get(int(iid), f"0x{iid:02X}")


def fmt(slots: list[tuple[int, int]], n: int) -> str:
    parts = []
    for i in range(n):
        iid, qty = slots[i]
        if iid:
            parts.append(f"{i}:{item_name(iid)}x{qty}")
    return ", ".join(parts) if parts else "(empty)"


def load_sidecar() -> dict:
    return json.loads(SIDECAR_PATH.read_text(encoding="utf-8"))


def save_sidecar(sidecar: dict, box_live: list[tuple[int, int]]) -> None:
    sidecar["box_cache"] = [[int(iid), int(qty)] for iid, qty in box_live]
    SIDECAR_PATH.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")


def repair_layout(
    inv: list[tuple[int, int]],
    box: list[tuple[int, int]],
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Canonical post-room-100 bank for cp57 continuation."""
    new_inv = list(inv)
    new_box = list(box)
    while len(new_inv) < 8:
        new_inv.append((0, 0))
    while len(new_box) < 16:
        new_box.append((0, 0))

    # Strip handgun bullets from modeled box slots.
    for i in range(16):
        if int(new_box[i][0]) == 0x0B:
            new_box[i] = (0, 0)

    # Ensure acid rounds + bazooka live in box, not on person.
    acid_qty = 0
    baz_qty = 0
    for i, (iid, qty) in enumerate(new_inv):
        if int(iid) == 0x11:
            acid_qty = max(acid_qty, int(qty) if int(qty) > 0 else 6)
            new_inv[i] = (0, 0)
        elif int(iid) == 0x07:
            baz_qty = max(baz_qty, int(qty) if int(qty) > 0 else 6)
            new_inv[i] = (0, 0)

    if acid_qty <= 0:
        acid_qty = 6
    if baz_qty <= 0:
        baz_qty = 6

    # Place into first empty slots: acid then bazooka (matches boss bank order).
    for item_id, qty in ((0x11, acid_qty), (0x07, baz_qty)):
        if any(int(new_box[i][0]) == int(item_id) for i in range(16)):
            continue
        empty = next(i for i in range(16) if int(new_box[i][0]) == 0)
        new_box[empty] = (int(item_id), int(qty))

    return new_inv[:8], new_box[:16]


def main() -> int:
    if not STATE_PATH.is_file():
        print(f"missing {STATE_PATH}", file=sys.stderr)
        return 1

    assert_rom_present()
    proc = subprocess.Popen(emuhawk_argv(port=PORT), cwd=str(ROOT))
    try:
        bridge = BizHawkClient(port=PORT, timeout=60, connect_timeout=90)
        bridge.start_server()
        bridge.wait_for_client()
        bridge.load_savestate(str(STATE_PATH))
        bridge.frameadvance(8)

        inv0 = policy_inventory(read_inventory(bridge))
        box0 = read_box(bridge)
        box_live0 = read_box_live(bridge)
        print("=== BEFORE ===")
        print(f"inv: {fmt(inv0, 8)}")
        print(f"box[0:16]: {fmt(box0, 16)}")
        pollution_before = box_pollution_reason(box_live0)
        print(f"pollution: {pollution_before!r}")

        inv1, box1 = repair_layout(inv0, box0)
        write_inventory_box_curation(bridge, inv1, box1)
        bridge.frameadvance(4)

        inv2 = policy_inventory(read_inventory(bridge))
        box2 = read_box(bridge)
        box_live2 = read_box_live(bridge)
        print("=== AFTER RAM FIX ===")
        print(f"inv: {fmt(inv2, 8)}")
        print(f"box[0:16]: {fmt(box2, 16)}")
        pollution_after = box_pollution_reason(box_live2)
        print(f"pollution: {pollution_after!r}")
        free = sum(1 for iid, _q in inv2 if int(iid) == 0)
        print(f"free inv slots: {free}")

        if pollution_after:
            print("FAIL: box still polluted", file=sys.stderr)
            return 1
        if free < 2:
            print("WARN: fewer than 2 free inventory slots", file=sys.stderr)

        bridge.save_savestate(str(STATE_PATH))
        sidecar = load_sidecar()
        save_sidecar(sidecar, box_live2)
        print(f"wrote {STATE_PATH}")
        print(f"updated {SIDECAR_PATH} box_cache")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=15)


if __name__ == "__main__":
    raise SystemExit(main())
