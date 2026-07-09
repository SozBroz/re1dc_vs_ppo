"""Hunt RAM signature for in-game OPTIONS -> controller EDIT screen.

RE1 DC pause: CONTINUE, MAP, FILE, CONFIG, EXIT (CONFIG = 3x down).
CONFIG -> KEY CONFIG (button assignment) -> TYPE row -> EDIT.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\hunt_controller_config_screen.py --port 5800
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"
OUT = ROOT / "data" / "controller_config_screen.json"
SHOT_DIR = ROOT / "data" / "controller_config_hunt"

from re1_rl.memory_map import (  # noqa: E402
    CAM_ID,
    CHARACTER_ID,
    GAME_MODE,
    GAME_STATE,
    MESSAGE_FLAG,
    PLAYER_HP,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
)
from re1_rl.game_session import outside_gameplay_reason  # noqa: E402

POLL = [
    ("game_state", GAME_STATE, "u32"),
    ("game_mode", GAME_MODE, "u8"),
    ("scene_flag", SCENE_FLAG, "u8"),
    ("msg_flag", MESSAGE_FLAG, "u8"),
    ("stage_id", STAGE_ID, "u8"),
    ("room_id", ROOM_ID, "u8"),
    ("cam_id", CAM_ID, "u8"),
    ("character_id", CHARACTER_ID, "u8"),
    ("player_hp", PLAYER_HP, "u16"),
]

SCAN_WINDOWS = (
    (0x800C3000, 64),
    (0x800C8660, 32),
)


def _fmt(ram: dict[str, int]) -> str:
    gs = int(ram["game_state"])
    return (
        f"gs=0x{gs:08X} mode=0x{int(ram['game_mode']):02X} "
        f"scene=0x{int(ram['scene_flag']):02X} msg=0x{int(ram['msg_flag']):02X} "
        f"stage={int(ram['stage_id'])} room={int(ram['room_id'])} "
        f"cam={int(ram['cam_id'])} hp={int(ram['player_hp'])}"
    )


def _tap(bridge, buttons: dict[str, bool], frames: int = 4) -> None:
    bridge.step(buttons=buttons, n=frames)


def _scan_windows(bridge) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for base, count in SCAN_WINDOWS:
        fields = [(f"b{base + i}", base + i, "u8") for i in range(count)]
        raw = bridge.read_ram(fields)
        out[f"0x{base:08X}"] = [int(raw[f"b{base + i}"]) for i in range(count)]
    return out


def _row(ram: dict[str, int], label: str, scans: dict[str, list[int]]) -> dict:
    gs = int(ram["game_state"])
    probe = {
        "player_hp": int(ram["player_hp"]),
        "stage_id": int(ram["stage_id"]),
        "room_id": int(ram["room_id"]),
        "character_id": int(ram["character_id"]),
        "game_mode": int(ram["game_mode"]),
        "game_state": gs,
        "msg_flag": int(ram["msg_flag"]),
        "scene_flag": int(ram["scene_flag"]),
    }
    return {
        "label": label,
        "game_state_hex": f"0x{gs:08X}",
        "game_mode": int(ram["game_mode"]),
        "scene_flag": int(ram["scene_flag"]),
        "msg_flag": int(ram["msg_flag"]),
        "outside_gameplay": outside_gameplay_reason(probe, episode_start_hp=96),
        "scan": scans,
    }


def main() -> int:
    from re1_rl.bizhawk_bridge import BizHawkClient

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5800)
    ap.add_argument("--speed", type=int, default=200)
    args = ap.parse_args()

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    shot = str(SHOT_DIR / f"port{args.port}.png")
    bridge = BizHawkClient(port=args.port, timeout=300.0, screenshot_path=shot)
    bridge.start_server()

    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={args.port}",
        ],
        cwd=str(ROOT),
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))
        bridge.load_savestate(str(STATE))
        bridge.frameadvance(4)

        trace: list[dict] = []

        def snap(label: str) -> None:
            ram = bridge.read_ram(POLL)
            scans = _scan_windows(bridge)
            row = _row(ram, label, scans)
            trace.append(row)
            print(
                f"[hunt] {label}: {_fmt(ram)} "
                f"outside={row['outside_gameplay']!r}",
                flush=True,
            )
            bridge.screenshot(str(SHOT_DIR / f"{label}.png"))

        snap("idle_control")

        _tap(bridge, {"start": True}, 12)
        _tap(bridge, {}, 30)
        snap("pause_menu")

        # CONFIG row (CONTINUE, MAP, FILE, CONFIG)
        for _ in range(3):
            _tap(bridge, {"down": True}, 10)
            _tap(bridge, {}, 8)
        snap("pause_config_row")
        _tap(bridge, {"cross": True}, 20)
        _tap(bridge, {}, 40)
        snap("config_root")

        # KEY CONFIG is usually first entry
        _tap(bridge, {"cross": True}, 20)
        _tap(bridge, {}, 50)
        snap("key_config_or_options")

        # TYPE A B C EDIT EXIT — move right to EDIT (3 steps from TYPE)
        for i in range(1, 4):
            _tap(bridge, {"right": True}, 10)
            _tap(bridge, {}, 8)
            snap(f"options_right_{i}")

        _tap(bridge, {"cross": True}, 20)
        _tap(bridge, {}, 50)
        snap("controller_button_edit")

        # Diff vs idle for hunt summary
        idle = trace[0]["scan"]
        edit = trace[-1]["scan"]
        diff: dict[str, list[dict]] = {}
        for key in idle:
            for i, (a, b) in enumerate(zip(idle[key], edit[key])):
                if a != b:
                    diff.setdefault(key, []).append(
                        {"offset": i, "addr": f"0x{int(key, 16) + i:08X}", "idle": a, "edit": b}
                    )

        payload = {
            "target_screen": "OPTIONS controller TYPE/EDIT button assignment",
            "trace": trace,
            "idle_vs_edit_diff": diff,
            "stop_signature": {
                "game_state": trace[-1]["game_state_hex"],
                "game_mode": trace[-1]["game_mode"],
                "outside_gameplay_reason": trace[-1]["outside_gameplay"],
            },
        }
        OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[hunt] wrote {OUT}", flush=True)
        print(f"[hunt] stop_signature={payload['stop_signature']}", flush=True)
        return 0
    finally:
        try:
            bridge.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
