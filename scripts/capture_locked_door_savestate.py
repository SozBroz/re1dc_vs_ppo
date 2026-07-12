"""Capture Jill at a lockpick-gated door (no lockpick) for reward/regression tests.

Usage (free bridge port, EmuHawk on same port):
  python scripts/capture_locked_door_savestate.py --port 7788

Writes:
  states/jill_locked_door_107_108.State
  states/jill_locked_door_107_108.meta.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import (
    CAM_ID,
    GAME_MODE,
    GAME_STATE,
    IN_CONTROL_MASK,
    MESSAGE_FLAG,
    PLAYER_FACING,
    PLAYER_HP,
    PLAYER_X,
    PLAYER_Y,
    PLAYER_Z,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
)

_IN_CONTROL_GAME_STATE = 0x80800004
# 107->108 sword door (lockpick); empirical harvest pose.
_DOOR_POSE = {
    "stage_id": 0,
    "room_byte": 0x07,
    "x": 15089,
    "z": 2647,
    "facing": 3472,
    "cam_id": 2,
    "y": 0,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7788)
    ap.add_argument("--hp", type=int, default=96)
    ap.add_argument(
        "--out",
        default="states/jill_locked_door_107_108.State",
        help="savestate path relative to repo root",
    )
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)

    bridge = BizHawkClient(port=args.port, timeout=120.0)
    bridge.start_server()
    print(f"listening on {args.port}; launch EmuHawk on that port", flush=True)
    bridge.wait_for_client()
    bridge.set_speed(100)

    init = root / "states/jill_control_fresh.State"
    if not init.is_file():
        print(f"ERROR: missing {init}", file=sys.stderr)
        return 1
    bridge.load_savestate(str(init))
    bridge.frameadvance(30)

    pose = _DOOR_POSE
    bridge.write_ram(
        [
            ("stage_id", STAGE_ID, "u8", int(pose["stage_id"])),
            ("room_id", ROOM_ID, "u8", int(pose["room_byte"])),
            ("cam_id", CAM_ID, "u8", int(pose["cam_id"])),
            ("player_x", PLAYER_X, "s16", int(pose["x"])),
            ("player_y", PLAYER_Y, "s16", int(pose["y"])),
            ("player_z", PLAYER_Z, "s16", int(pose["z"])),
            ("player_facing", PLAYER_FACING, "u16", int(pose["facing"])),
            ("player_hp", PLAYER_HP, "u16", int(args.hp)),
            ("game_mode", GAME_MODE, "u8", IN_CONTROL_MASK),
            ("game_state", GAME_STATE, "u32", _IN_CONTROL_GAME_STATE),
            ("scene_flag", SCENE_FLAG, "u8", 0x80),
            ("msg_flag", MESSAGE_FLAG, "u8", 0),
        ]
    )
    bridge.frameadvance(45)

    bridge.save_savestate(str(out))
    meta = {
        "file": str(out.relative_to(root)).replace("\\", "/"),
        "room_id": "107",
        "notes": (
            "Jill at 107->108 lockpick door without lockpick; "
            "spam Cross = locked text only (no cutscene reward after fix)."
        ),
        "door_edge": "107->108",
        "pose": pose,
    }
    meta_path = out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    ram = bridge.read_ram(
        [
            ("room_id", ROOM_ID, "u8"),
            ("player_x", PLAYER_X, "s16"),
            ("player_z", PLAYER_Z, "s16"),
            ("player_hp", PLAYER_HP, "u16"),
        ]
    )
    print(f"saved {out}", flush=True)
    print(
        f"room_byte={ram['room_id']} pos=({ram['player_x']},{ram['player_z']}) "
        f"hp={ram['player_hp']}",
        flush=True,
    )
    bridge.quit()
    bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
