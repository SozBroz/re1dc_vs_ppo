"""Validate enemy RAM hooks + NN-facing signals on the last N QuickSaves.

Usage:
  python scripts/validate_enemy_obs_quicksaves.py
  python scripts/validate_enemy_obs_quicksaves.py --slots 1 2 3 4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import ATTACK_ACTION, KNIFE_SWING_ACTION, action_mask
from re1_rl.attack_log_context import room_display_name, room_roster_summary
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.enemy_combat import alive_enemy_count, combat_enemy_count, format_enemy_table
from re1_rl.env import ACTION_NAMES
from re1_rl.memory_map import (
    ENEMY_SLOT_STRIDE,
    ENEMY_TABLE_BASE,
    ENEMY_TABLE_SLOTS,
    PLAYER_X,
    PLAYER_Z,
    ROOM_ID,
    STAGE_ID,
    decode_enemy_table,
    enemy_table_fields,
)
from re1_rl.obs_encoder import PROPRIO_FIELDS, ObsEncoder
from re1_rl.room_graph import RoomGraph
from re1_rl.room_signature import RoomEnemyRoster
from re1_rl.spatial_encoder import SpatialEncoder, StaticEnemySpawns

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"


def u16_at(data: list[int], off: int) -> int:
    if off + 1 >= len(data):
        return 0
    return data[off] | (data[off + 1] << 8)


def s16_at(data: list[int], off: int) -> int:
    raw = u16_at(data, off)
    return raw - 0x10000 if raw & 0x8000 else raw


def plausible_coord(v: int) -> bool:
    if v < 0:
        v = -v
    return 1000 <= v <= 33000


def probe_raw_slots(client: BizHawkClient) -> list[dict]:
    rows: list[dict] = []
    base = int(ENEMY_TABLE_BASE or 0)
    for slot in range(ENEMY_TABLE_SLOTS):
        addr = base + slot * ENEMY_SLOT_STRIDE
        raw = client.read_block(addr, ENEMY_SLOT_STRIDE)
        hp0 = u16_at(raw, 0)
        rows.append({
            "slot": slot,
            "addr": f"0x{addr:08X}",
            "hp@0": hp0,
            "s16@2": s16_at(raw, 2),
            "s16@4": s16_at(raw, 4),
            "s16@6": s16_at(raw, 6),
            "u16@8": u16_at(raw, 8),
            "coord_ok": any(
                plausible_coord(s16_at(raw, o)) for o in (2, 4, 6, 8, 10, 12)
            ),
        })
    return rows


def newest_quicksaves(n: int) -> list[Path]:
    states = sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Drop .bak siblings if glob ever picks them (it shouldn't).
    states = [p for p in states if not p.name.endswith(".bak")]
    return list(reversed(states[:n]))


def spatial_enemy_count(spatial_vec: np.ndarray, spatial_enc: SpatialEncoder) -> float:
    from re1_rl.spatial_encoder import SPATIAL_FIELDS

    idx = {name: i for i, (name, _) in enumerate(SPATIAL_FIELDS)}
    return float(spatial_vec[idx["enemy_count"]])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7794)
    ap.add_argument(
        "--slots",
        type=int,
        nargs="*",
        default=None,
        help="QuickSave indices (default: 4 newest by mtime, oldest first)",
    )
    args = ap.parse_args()

    if args.slots:
        paths = [
            STATE_DIR / f"Resident Evil - Director's Cut (USA).Nymashock.QuickSave{i}.State"
            for i in args.slots
        ]
    else:
        paths = newest_quicksaves(4)

    missing = [p for p in paths if not p.is_file()]
    if missing:
        print("Missing states:", [str(p) for p in missing], file=sys.stderr)
        return 1

    roster = RoomEnemyRoster(ROOT / "data" / "room_enemies.json")
    static = StaticEnemySpawns(ROOT / "data" / "room_enemies.json")
    spatial_enc = SpatialEncoder(
        None,
        RoomGraph(ROOT / "data" / "doors_empirical.json"),
        static_enemies=static,
    )
    obs_enc = ObsEncoder(ROOT / "data" / "rooms.json", spatial_enc.graph)

    bridge = BizHawkClient(
        port=args.port,
        timeout=120.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / f"_enemy_val_{args.port}.png"),
        screenshot_mmf=True,
    )
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={args.port}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(8)
        bridge.wait_for_client()
        results: list[dict] = []
        for i, path in enumerate(paths, start=1):
            bridge.load_savestate(str(path))
            bridge.frameadvance(3)
            ram = bridge.read_ram(
                [("stage_id", STAGE_ID, "u8"), ("room_byte", ROOM_ID, "u8")]
                + enemy_table_fields()
            )
            stage = int(ram.get("stage_id", 0))
            room_byte = int(ram.get("room_byte", 0))
            room_id = f"{stage + 1}{room_byte:02X}"
            enemies = decode_enemy_table(ram)
            state = {
                "room_id": room_id,
                "x": int(bridge.read_ram([("x", PLAYER_X, "s16")])["x"]),
                "z": int(bridge.read_ram([("z", PLAYER_Z, "s16")])["z"]),
                "facing": 0,
                "hp": 96,
                "cam_id": 0,
                "in_control": True,
                "enemies": enemies,
                "character_id": 1,
                "inventory": [],
            }
            proprio = obs_enc.encode_proprio(state, prev_hp=96)
            p_idx = {n: i for i, (n, _) in enumerate(PROPRIO_FIELDS)}
            spatial = spatial_enc.encode(state)
            room_enemies_obs = roster.encode(room_id)
            static_spawns = static.for_room(room_id)
            mask = action_mask(
                len(ACTION_NAMES),
                None,
                equipped_weapon_id=0x01,
                alive_enemies_in_room=combat_enemy_count(enemies),
                mask_combat_without_enemies=True,
            )
            raw_slots = probe_raw_slots(bridge)
            living_raw = [s for s in raw_slots if 0 < s["hp@0"] <= 2000]
            coord_living = [s for s in living_raw if s["coord_ok"]]

            row = {
                "index": i,
                "file": path.name,
                "mtime": time.ctime(path.stat().st_mtime),
                "room_id": room_id,
                "room_name": room_display_name(room_id),
                "static_roster": room_roster_summary(room_id),
                "static_roster_total": int(round(float(room_enemies_obs[0]) * 8)),
                "static_spawn_coords": len(static_spawns),
                "ram_decode_count": len(enemies),
                "ram_decode_table": format_enemy_table(enemies),
                "raw_hp_slots": len(living_raw),
                "raw_coord_valid_slots": len(coord_living),
                "proprio_enemy_count": float(proprio[p_idx["enemy_count"]]),
                "spatial_enemy_count": spatial_enemy_count(spatial, spatial_enc),
                "room_enemies_obs0": float(room_enemies_obs[0]),
                "knife_legal": bool(mask[KNIFE_SWING_ACTION]),
                "attack_legal": bool(mask[ATTACK_ACTION]),
                "raw_slots": raw_slots,
            }
            results.append(row)
            print(json.dumps(row, indent=2), flush=True)
            print("---", flush=True)

        out = ROOT / "data" / "enemy_obs_quicksave_validation.json"
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Wrote {out}", flush=True)
        return 0
    finally:
        try:
            bridge.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
