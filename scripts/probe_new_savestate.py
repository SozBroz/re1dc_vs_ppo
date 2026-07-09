"""Load newest QuickSave states in live BizHawk and dump session RAM."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.fresh_spawn import format_spawn_summary
from re1_rl.game_session import (
    death_ui_from_ram,
    episode_failure_reason,
    opening_phase_from_ram,
    outside_gameplay_reason,
)
from re1_rl.memory_map import DEFAULT_RAM_FIELDS, MESSAGE_FLAG, SCENE_FLAG

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"


def probe_state(client: BizHawkClient, path: Path) -> dict:
    client.load_savestate(str(path.resolve()))
    client.frameadvance(4)
    fields = list(DEFAULT_RAM_FIELDS) + [
        ("msg_flag", MESSAGE_FLAG, "u8"),
        ("scene_flag", SCENE_FLAG, "u8"),
    ]
    ram = client.read_ram(fields)
    shot = ROOT / "data" / f"probe_{path.stem}.png"
    client.screenshot(str(shot))
    gs = int(ram["game_state"])
    mode = int(ram["game_mode"])
    row = {
        "file": path.name,
        "player_hp": int(ram["player_hp"]),
        "stage_id": int(ram["stage_id"]),
        "room_id": int(ram["room_id"]),
        "character_id": int(ram["character_id"]),
        "game_state": gs,
        "game_state_hex": f"0x{gs:08X}",
        "game_mode": mode,
        "game_mode_hex": f"0x{mode:02X}",
        "scene_flag": int(ram.get("scene_flag", 0)),
        "msg_flag": int(ram.get("msg_flag", 0)),
        "death_ui": death_ui_from_ram(ram),
        "opening": opening_phase_from_ram(ram, had_mansion_hp=True),
        "outside": outside_gameplay_reason(ram, episode_start_hp=96),
        "episode_failure": episode_failure_reason(
            ram, episode_start_hp=96, prev_hp=96
        ),
        "screenshot": str(shot),
    }
    print(f"\n=== {path.name} ===", flush=True)
    print(format_spawn_summary(ram), flush=True)
    print(
        f"gs={row['game_state_hex']} mode={row['game_mode_hex']} "
        f"scene=0x{row['scene_flag']:02X} msg=0x{row['msg_flag']:02X}",
        flush=True,
    )
    print(f"death_ui={row['death_ui']}", flush=True)
    print(f"episode_failure={row['episode_failure']!r}", flush=True)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5818)
    ap.add_argument(
        "--slots",
        nargs="*",
        default=["0", "3"],
        help="QuickSave slot numbers (default: 0 and 3, the two newest)",
    )
    args = ap.parse_args()

    states = []
    for slot in args.slots:
        p = STATE_DIR / (
            "Resident Evil - Director's Cut (USA).Nymashock."
            f"QuickSave{slot}.State"
        )
        if p.is_file():
            states.append(p)
        else:
            print(f"missing: {p}", flush=True)

    if not states:
        print("no savestates found", file=sys.stderr)
        return 1

    client = BizHawkClient(port=args.port, timeout=300.0, connect_timeout=120.0)
    client.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={args.port}",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    rows = []
    try:
        client.wait_for_client()
        for path in states:
            rows.append(probe_state(client, path))
        out = ROOT / "data" / "new_death_screen_probe.json"
        import json

        out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\n[probe] wrote {out}", flush=True)
        return 0
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
