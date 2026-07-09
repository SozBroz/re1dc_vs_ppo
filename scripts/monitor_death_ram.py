"""Live RAM monitor — poll until Jill dies; log screen/session bytes on change.

Launch EmuHawk, load jill_control_fresh, then YOU play until death.
Prints whenever hp/mode/game_state/room/outside_gameplay changes.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\monitor_death_ram.py --port 7777 --no-launch
  (EmuHawk / play_human must use --socket_port=7777; only one Python server per port.)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"

from re1_rl.game_session import outside_gameplay_reason  # noqa: E402
from re1_rl.memory_map import (  # noqa: E402
    CHARACTER_ID,
    GAME_MODE,
    GAME_STATE,
    MESSAGE_FLAG,
    PLAYER_HP,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
)


def _poll(bridge) -> dict[str, int]:
    raw = bridge.read_ram(
        [
            ("game_state", GAME_STATE, "u32"),
            ("game_mode", GAME_MODE, "u8"),
            ("scene_flag", SCENE_FLAG, "u8"),
            ("msg_flag", MESSAGE_FLAG, "u8"),
            ("stage_id", STAGE_ID, "u8"),
            ("room_id", ROOM_ID, "u8"),
            ("character_id", CHARACTER_ID, "u8"),
            ("player_hp", PLAYER_HP, "u16"),
        ]
    )
    return {k: int(raw[k]) for k in raw}


def _fmt(ram: dict[str, int], *, outside: str | None) -> str:
    gs = ram["game_state"]
    return (
        f"hp={ram['player_hp']:3d} room={ram['room_id']:2d} "
        f"gs=0x{gs:08X} mode=0x{ram['game_mode']:02X} "
        f"scene=0x{ram['scene_flag']:02X} msg=0x{ram['msg_flag']:02X} "
        f"outside={outside!r}"
    )


def main() -> int:
    from re1_rl.bizhawk_bridge import BizHawkClient

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--speed", type=int, default=100, help="EmuHawk speedmode (100=normal)")
    ap.add_argument("--poll-ms", type=int, default=250)
    ap.add_argument("--no-launch", action="store_true", help="only listen; you start EmuHawk")
    ap.add_argument("--no-state", action="store_true", help="do not load savestate")
    args = ap.parse_args()

    shot = str(ROOT / "data" / f"_frame_{args.port}.png")
    bridge = BizHawkClient(port=args.port, timeout=300.0, screenshot_path=shot)
    bridge.start_server()
    print(f"[death_monitor] listening on port {args.port}", flush=True)

    proc: subprocess.Popen | None = None
    if not args.no_launch:
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
        print(f"[death_monitor] launched EmuHawk pid={proc.pid}", flush=True)

    def _accept_and_prime() -> None:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))
        if not args.no_state:
            bridge.load_savestate(str(STATE))
            bridge.frameadvance(2)
            print(f"[death_monitor] loaded {STATE.name}", flush=True)

    try:
        _accept_and_prime()

        print(
            "[death_monitor] logging RAM on every change (continues after death)",
            flush=True,
        )
        print("[death_monitor] Ctrl+C to stop", flush=True)

        episode_start_hp = 96
        prev: dict[str, int] | None = None
        prev_outside: str | None = None
        death_seen = False
        shot_idx = 0

        while True:
            try:
                ram = _poll(bridge)
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"[death_monitor] disconnected: {exc}", flush=True)
                print("[death_monitor] waiting for BizHawk reconnect...", flush=True)
                try:
                    bridge.close()
                except Exception:
                    pass
                bridge.start_server()
                prev = None
                prev_outside = None
                _accept_and_prime()
                print("[death_monitor] reconnected — resuming poll", flush=True)
                continue

            probe = {
                "player_hp": ram["player_hp"],
                "stage_id": ram["stage_id"],
                "room_id": ram["room_id"],
                "character_id": ram["character_id"],
                "game_mode": ram["game_mode"],
                "game_state": ram["game_state"],
                "msg_flag": ram["msg_flag"],
                "scene_flag": ram["scene_flag"],
            }
            outside = outside_gameplay_reason(probe, episode_start_hp=episode_start_hp)

            changed = prev is None or ram != prev or outside != prev_outside
            if changed:
                ts = time.strftime("%H:%M:%S")
                tag = "POST_DEATH" if death_seen else "LIVE"
                print(f"[{ts}] [{tag}] {_fmt(ram, outside=outside)}", flush=True)
                if ram["player_hp"] > 0 and not death_seen:
                    episode_start_hp = ram["player_hp"]
                if ram["player_hp"] == 0 and episode_start_hp > 0 and not death_seen:
                    death_seen = True
                    bridge.screenshot(str(ROOT / "data" / "death_monitor_capture.png"))
                    print(
                        "[death_monitor] *** DEATH (hp=0) — screenshot "
                        "data/death_monitor_capture.png — still monitoring",
                        flush=True,
                    )
                if death_seen and changed:
                    shot_idx += 1
                    path = ROOT / "data" / f"death_monitor_post_{shot_idx:03d}.png"
                    bridge.screenshot(str(path))
                    print(f"[death_monitor] post-death shot {path.name}", flush=True)
                prev = dict(ram)
                prev_outside = outside

            time.sleep(max(args.poll_ms, 50) / 1000.0)
    except KeyboardInterrupt:
        print("[death_monitor] stopped", flush=True)
        return 0
    finally:
        try:
            bridge.close()
        except Exception:
            pass
        if proc is not None:
            proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
