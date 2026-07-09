"""Per-frame RAM trace through knife macro to find animation-done hooks.

Steps knife schedule one emulated frame at a time, snapshots the player
entity block (and nearby control bytes), then ranks bytes that:
  - change during the macro vs idle baseline
  - return to baseline (or a stable post-swing value) in the tail

Run (dedicated EmuHawk — avoid training ports 5555+rank):
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\hunt_player_knife_anim.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\hunt_player_knife_anim.py --port 5780 --swings 3

Output: data/hunt_player_knife_anim.json + console ranked candidates.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
DEFAULT_STATE = ROOT / "states" / "jill_control_fresh.State"
OUT_JSON = ROOT / "data" / "hunt_player_knife_anim.json"

# Player struct neighborhood (position @ 0x800C5158).
PLAYER_LO = 0x800C5100
PLAYER_HI = 0x800C5200

from re1_rl.memory_map import (  # noqa: E402
    CAM_ID,
    CHARACTER_ID,
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

EXTRA_FIELDS: list[tuple[str, int, str]] = [
    ("player_hp", PLAYER_HP, "u16"),
    ("player_x", PLAYER_X, "s16"),
    ("player_y", PLAYER_Y, "s16"),
    ("player_z", PLAYER_Z, "s16"),
    ("player_facing", PLAYER_FACING, "u16"),
    ("game_mode", GAME_MODE, "u8"),
    ("game_state", GAME_STATE, "u32"),
    ("scene_flag", SCENE_FLAG, "u8"),
    ("message_flag", MESSAGE_FLAG, "u8"),
    ("stage_id", STAGE_ID, "u8"),
    ("room_id", ROOM_ID, "u8"),
    ("cam_id", CAM_ID, "u8"),
    ("character_id", CHARACTER_ID, "u8"),
]


@dataclass
class FrameSnap:
    phase: str
    frame_idx: int
    block: list[int]
    fields: dict[str, int]


def read_player_block(client) -> list[int]:
    size = PLAYER_HI - PLAYER_LO
    return client.read_block(PLAYER_LO, size)


def read_fields(client) -> dict[str, int]:
    raw = client.read_ram(EXTRA_FIELDS)
    return {k: int(raw[k]) for k, _, _ in EXTRA_FIELDS}


def launch_emu(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def trace_swing(
    client,
    *,
    schedule: list[dict[str, bool]],
    idle_warmup: int,
    tail_frames: int,
    swing_id: int,
) -> list[FrameSnap]:
    snaps: list[FrameSnap] = []
    for i in range(idle_warmup):
        client.step(n=1, sticky={}, frame_buttons=[{}])
        snaps.append(
            FrameSnap("idle_warmup", i, read_player_block(client), read_fields(client))
        )

    for i, btn in enumerate(schedule):
        client.step(n=1, sticky={}, frame_buttons=[btn])
        snaps.append(
            FrameSnap("macro", i, read_player_block(client), read_fields(client))
        )

    for i in range(tail_frames):
        client.step(n=1, sticky={}, frame_buttons=[{}])
        snaps.append(
            FrameSnap("tail", i, read_player_block(client), read_fields(client))
        )
    return snaps


def mode_byte(values: list[int]) -> int:
    counts: dict[int, int] = defaultdict(int)
    for v in values:
        counts[v] += 1
    return max(counts, key=counts.get)


def analyze_traces(all_snaps: list[list[FrameSnap]]) -> dict:
    """Rank block offsets that animate during macro and settle after."""
    n_offsets = PLAYER_HI - PLAYER_LO
    candidates: list[dict] = []

    for off in range(n_offsets):
        addr = PLAYER_LO + off
        per_swing: list[list[int]] = []
        for snaps in all_snaps:
            per_swing.append([s.block[off] for s in snaps])

        # Rebuild phase slices per swing
        swing_stats: list[dict] = []
        for snaps in all_snaps:
            idle = [s.block[off] for s in snaps if s.phase == "idle_warmup"]
            macro = [s.block[off] for s in snaps if s.phase == "macro"]
            tail = [s.block[off] for s in snaps if s.phase == "tail"]
            if not idle or not macro or not tail:
                continue
            idle_mode = mode_byte(idle)
            macro_changed = any(v != idle_mode for v in macro)
            tail_mode = mode_byte(tail[-4:] if len(tail) >= 4 else tail)
            settles = tail_mode == idle_mode
            swing_stats.append(
                {
                    "idle_mode": idle_mode,
                    "macro_min": min(macro),
                    "macro_max": max(macro),
                    "macro_unique": len(set(macro)),
                    "tail_mode": tail_mode,
                    "macro_changed": macro_changed,
                    "settles_to_idle": settles,
                    "macro": macro,
                    "tail": tail,
                }
            )

        if not swing_stats:
            continue
        if not all(s["macro_changed"] for s in swing_stats):
            continue

        n_settle = sum(1 for s in swing_stats if s["settles_to_idle"])
        score = n_settle * 10 + sum(s["macro_unique"] for s in swing_stats)
        candidates.append(
            {
                "addr": f"0x{addr:08X}",
                "offset_from_PLAYER_X": addr - PLAYER_X,
                "score": score,
                "swings": len(swing_stats),
                "settles_all": n_settle == len(swing_stats),
                "settles_count": n_settle,
                "example_macro_unique": swing_stats[0]["macro_unique"],
                "example_idle": swing_stats[0]["idle_mode"],
                "example_tail": swing_stats[0]["tail_mode"],
                "example_macro_range": [
                    swing_stats[0]["macro_min"],
                    swing_stats[0]["macro_max"],
                ],
            }
        )

    candidates.sort(key=lambda c: (-c["score"], c["addr"]))

    # Extra fields: which change during macro but match idle in tail
    field_hits: list[dict] = []
    for fname, _, _ in EXTRA_FIELDS:
        per_swing_idle: list[int] = []
        per_swing_macro_changed: list[bool] = []
        per_swing_tail_match: list[bool] = []
        for snaps in all_snaps:
            idle = [s.fields[fname] for s in snaps if s.phase == "idle_warmup"]
            macro = [s.fields[fname] for s in snaps if s.phase == "macro"]
            tail = [s.fields[fname] for s in snaps if s.phase == "tail"]
            im = mode_byte(idle)
            per_swing_idle.append(im)
            per_swing_macro_changed.append(any(v != im for v in macro))
            per_swing_tail_match.append(mode_byte(tail[-4:]) == im)
        if any(per_swing_macro_changed):
            field_hits.append(
                {
                    "field": fname,
                    "macro_changed": per_swing_macro_changed,
                    "tail_matches_idle": per_swing_tail_match,
                }
            )

    return {"block_candidates": candidates[:40], "field_candidates": field_hits}


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt player knife animation RAM hooks")
    ap.add_argument("--port", type=int, default=5780)
    ap.add_argument("--swings", type=int, default=3, help="knife swings to aggregate")
    ap.add_argument("--idle-warmup", type=int, default=8)
    ap.add_argument("--tail", type=int, default=24, help="noop frames after macro")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.knife_macro import build_knife_frame_buttons

    schedule = build_knife_frame_buttons()
    port = int(args.port)
    bridge = BizHawkClient(
        port=port,
        timeout=300.0,
        screenshot_path=str(ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    proc = launch_emu(port)
    all_snaps: list[list[FrameSnap]] = []

    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        if args.state.is_file():
            bridge.load_savestate(str(args.state))
            bridge.frameadvance(5)

        print(
            f"[hunt] port={port} swings={args.swings} macro_len={len(schedule)} "
            f"block=0x{PLAYER_LO:08X}-0x{PLAYER_HI:08X}",
            flush=True,
        )

        for swing in range(int(args.swings)):
            print(f"[hunt] swing {swing + 1}/{args.swings}", flush=True)
            snaps = trace_swing(
                bridge,
                schedule=schedule,
                idle_warmup=int(args.idle_warmup),
                tail_frames=int(args.tail),
                swing_id=swing,
            )
            gm = [s.fields["game_mode"] for s in snaps]
            ic = [bool(v & IN_CONTROL_MASK) for v in gm]
            print(
                f"  frames={len(snaps)} game_mode range "
                f"0x{min(gm):02X}-0x{max(gm):02X} in_control={all(ic)}",
                flush=True,
            )
            all_snaps.append(snaps)

        report = analyze_traces(all_snaps)
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            "port": port,
            "swings": args.swings,
            "macro_frames": len(schedule),
            "player_block": [f"0x{PLAYER_LO:08X}", f"0x{PLAYER_HI:08X}"],
            "analysis": report,
        }
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[hunt] wrote {OUT_JSON}", flush=True)

        print("\n=== top block candidates (change in macro, settle to idle) ===", flush=True)
        for c in report["block_candidates"][:15]:
            print(
                f"  {c['addr']} off_x={c['offset_from_PLAYER_X']:+d} "
                f"score={c['score']} settles={c['settles_count']}/{c['swings']} "
                f"idle=0x{c['example_idle']:02X} tail=0x{c['example_tail']:02X} "
                f"macro_range={c['example_macro_range']}",
                flush=True,
            )

        if report["field_candidates"]:
            print("\n=== known fields that changed during macro ===", flush=True)
            for f in report["field_candidates"]:
                print(f"  {f['field']}: {f}", flush=True)
        else:
            print(
                "\n=== no EXTRA_FIELDS changed during macro "
                "(hook is likely inside player block) ===",
                flush=True,
            )
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            proc.terminate()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
