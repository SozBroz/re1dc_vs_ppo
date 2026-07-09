"""Empirical door-coordinate + pickup logger (library + passive CLI).

For unified doors/pickups/hunts in one playthrough, run capture_session.py.
This module exports helpers used by capture_session and tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import DEFAULT_RAM_FIELDS, IN_CONTROL_MASK, decode_inventory

DOORS_PATH = PROJECT_ROOT / "data" / "doors_empirical.json"
PICKUPS_PATH = PROJECT_ROOT / "data" / "pickups_empirical.json"

STACK_PICKUP_ALIAS: dict[str, str] = {
    "beretta": "clip",
}


def inventory_qty(ram: dict) -> dict[str, int]:
    totals: dict[str, int] = {}
    for name, qty in decode_inventory(ram):
        cname = canonical_item(name)
        totals[cname] = totals.get(cname, 0) + int(qty)
    return totals


def detect_pickups(
    prev_qty: dict[str, int],
    inv: dict[str, int],
    ever_held: set[str],
) -> list[dict]:
    events: list[dict] = []
    new_items = set(inv) - ever_held
    for item in sorted(new_items):
        events.append({"item": item, "kind": "new_item", "qty_after": inv[item]})
    for item, qty in sorted(inv.items()):
        if item in new_items or item not in prev_qty:
            continue
        prev = prev_qty[item]
        if qty > prev:
            ev: dict = {
                "item": item,
                "kind": "ammo_stack",
                "qty_before": prev,
                "qty_after": qty,
                "qty_delta": qty - prev,
            }
            if item in STACK_PICKUP_ALIAS:
                ev["ground_item"] = STACK_PICKUP_ALIAS[item]
            events.append(ev)
    return events


def read_pose(bridge: BizHawkClient) -> dict:
    ram = bridge.read_ram(DEFAULT_RAM_FIELDS)
    return {
        "room": f"{int(ram['stage_id']) + 1}{int(ram['room_id']):02X}",
        "x": int(ram["player_x"]),
        "z": int(ram["player_z"]),
        "facing": int(ram["player_facing"]),
        "cam_id": int(ram["cam_id"]),
        "in_control": bool(int(ram["game_mode"]) & IN_CONTROL_MASK),
        "inventory": inventory_qty(ram),
    }


def save_pickup(pickups: list, event: dict, pose: dict) -> None:
    row = {
        "item": event["item"],
        "room": pose["room"],
        "x": pose["x"],
        "z": pose["z"],
    }
    if event.get("kind"):
        row["kind"] = event["kind"]
    for key in ("qty_before", "qty_after", "qty_delta", "ground_item"):
        if key in event:
            row[key] = event[key]
    pickups.append(row)
    with PICKUPS_PATH.open("w", encoding="utf-8") as f:
        json.dump(pickups, f, indent=2)


def load_doors() -> dict:
    if DOORS_PATH.is_file():
        with DOORS_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_door(doors: dict, exit_pose: dict, entry_pose: dict) -> str:
    key = f"{exit_pose['room']}->{entry_pose['room']}"
    doors[key] = {
        "from_room": exit_pose["room"],
        "to_room": entry_pose["room"],
        "door_x": exit_pose["x"],
        "door_z": exit_pose["z"],
        "door_facing": exit_pose["facing"],
        "door_cam_id": exit_pose["cam_id"],
        "entry_x": entry_pose["x"],
        "entry_z": entry_pose["z"],
        "notes": "logged by capture_session.py",
    }
    with DOORS_PATH.open("w", encoding="utf-8") as f:
        json.dump(doors, f, indent=2)
    return key


def main_passive() -> None:
    """Passive-only loop (doors + pickups). Prefer capture_session.py."""
    doors = load_doors()
    print(f"[doors] {len([k for k in doors if not k.startswith('_')])} transitions known")
    bridge = BizHawkClient()
    bridge.start_server()
    bridge.wait_for_client()
    bridge.set_speed(100)

    last_control_pose = None
    prev_room = None
    pending_exit = None
    pickups: list = []
    if PICKUPS_PATH.is_file():
        with PICKUPS_PATH.open(encoding="utf-8") as f:
            pickups = json.load(f)
    ever_held: set[str] = set()
    prev_qty: dict[str, int] = {}
    first_read = True

    try:
        while True:
            bridge.frameadvance(12)
            pose = read_pose(bridge)
            inv = pose["inventory"]
            if prev_room is not None and pose["room"] != prev_room:
                pending_exit = last_control_pose
            if pending_exit is not None and pose["in_control"] \
                    and pose["room"] != pending_exit["room"]:
                save_door(doors, pending_exit, pose)
                pending_exit = None
            if not first_read:
                grab_pose = pose if pose["in_control"] else (last_control_pose or pose)
                for event in detect_pickups(prev_qty, inv, ever_held):
                    save_pickup(pickups, event, grab_pose)
            ever_held |= set(inv)
            prev_qty = dict(inv)
            first_read = False
            if pose["in_control"]:
                last_control_pose = pose
            prev_room = pose["room"]
    except KeyboardInterrupt:
        print(f"\n[doors] stopped. pickups={len(pickups)}")


if __name__ == "__main__":
    from capture_session import main as unified_main
    unified_main()
