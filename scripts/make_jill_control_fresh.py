"""Capture jill_control from an unbeaten path (not derived from completed-run states).

Modes:
  --new-game   Power-cycle, mash New Game -> Jill -> intro -> first control in
               dining. Does NOT load recon_f1950 / jill_start / jill_control.
               Use a blank PS1 memory card in EmuHawk slot 1 (no clear saves).

  --wait       You load an in-game save to dining room with control in EmuHawk;
               script polls RAM and saves when validation passes.

  --load-slot N  After reboot, mash title menus and load memory-card slot N
               (fragile; prefer --wait if automation misses).

Writes:
  states/jill_control_fresh.State
  data/recon/jill_control_fresh.png
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.fresh_spawn import (
    JILL_ID,
    MENU_ROOM_ID,
    format_spawn_summary,
    inventory_item_ids,
    validate_fresh_dining_spawn,
    FORBIDDEN_ITEM_IDS,
    REQUIRED_STARTER_IDS,
)
from re1_rl.memory_map import PLAYER_X

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"
OUT_STATE = PROJECT_ROOT / "states" / "jill_control_fresh.State"
OUT_SHOT = PROJECT_ROOT / "data" / "recon" / "jill_control_fresh.png"


def tap(client: BizHawkClient, button: str, hold: int = 2, release: int = 8) -> None:
    client.send_buttons({button: True})
    client.frameadvance(hold)
    client.send_buttons({})
    client.frameadvance(release)


def read_pos(client: BizHawkClient) -> list[int]:
    return client.read_block(PLAYER_X, 12)


def probe_control(client: BizHawkClient) -> bool:
    before = read_pos(client)
    client.send_buttons({"up": True})
    client.frameadvance(40)
    client.send_buttons({})
    after = read_pos(client)
    return before != after


def mash_intro(client: BizHawkClient, max_frames: int) -> dict:
    """Mash through FMV / narration until HP initialises."""
    ram: dict = {}
    for step in range(max_frames):
        if step % 4 == 0:
            client.send_buttons({"cross": True})
        elif step % 4 == 2:
            client.send_buttons({"start": True})
        else:
            client.send_buttons({})
        client.frameadvance(1)
        if step % 50 == 0:
            ram = client.read_ram()
            if int(ram.get("player_hp", 0)) not in (0, 65535):
                return ram
    return client.read_ram()


def dining_spawn_ready(ram: dict) -> bool:
    room = int(ram.get("room_id", -1))
    mode = int(ram.get("game_mode", 0))
    if int(ram.get("character_id", -1)) != JILL_ID:
        return False
    if room != 5 or not (mode & 0x80):
        return False
    ids = inventory_item_ids(ram)
    if not REQUIRED_STARTER_IDS.issubset(ids):
        return False
    if ids & FORBIDDEN_ITEM_IDS:
        return False
    return True


def wait_for_dining_control(
    client: BizHawkClient,
    *,
    timeout_frames: int,
    poll_every: int = 50,
) -> tuple[dict | None, bool]:
    """Poll until fresh dining spawn validates and movement probe passes."""
    for frame in range(0, timeout_frames, poll_every):
        client.frameadvance(poll_every)
        ram = client.read_ram()
        if dining_spawn_ready(ram):
            return ram, True
    return None, False


def advance_to_dining_control(
    client: BizHawkClient,
    *,
    max_steps: int = 14000,
) -> tuple[dict | None, bool]:
    """Mash through narration until dining room + movement control (like make_jill_control)."""
    step = 0
    while step < max_steps:
        for i in range(200):
            if i % 4 == 0:
                client.send_buttons({"cross": True})
            elif i % 4 == 2:
                client.send_buttons({"start": True})
            else:
                client.send_buttons({})
            client.frameadvance(1)
        step += 200

        ram = client.read_ram()
        room = int(ram.get("room_id", -1))
        mode = int(ram.get("game_mode", 0))
        in_control = bool(mode & 0x80)
        if dining_spawn_ready(ram):
            return ram, True
        if step % 1000 == 0:
            print(
                f"  mash step={step} {format_spawn_summary(ram)}",
                flush=True,
            )
    return None, False


def mash_to_menu(client: BizHawkClient, max_frames: int = 5000) -> bool:
    """Mash title screens until the menu room byte (27) appears."""
    for step in range(max_frames):
        if step % 4 == 0:
            client.send_buttons({"cross": True})
        elif step % 4 == 2:
            client.send_buttons({"start": True})
        else:
            client.send_buttons({})
        client.frameadvance(1)
        if step % 20 == 0:
            ram = client.read_ram()
            if int(ram.get("room_id", -1)) == MENU_ROOM_ID:
                client.send_buttons({})
                client.frameadvance(40)
                return True
    return False


def select_jill_at_char_select(client: BizHawkClient) -> None:
    """Same sequence as make_jill_start.py (verified Jill path)."""
    shot_dir = PROJECT_ROOT / "data" / "recon"
    shot_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(6):
        tap(client, "cross", hold=2, release=20)
    client.screenshot(str(shot_dir / "fresh_charselect_before.png"))
    tap(client, "right", hold=2, release=20)
    client.screenshot(str(shot_dir / "fresh_charselect_jill.png"))
    tap(client, "cross", hold=2, release=20)


def run_new_game(client: BizHawkClient) -> tuple[dict | None, bool]:
    print("rebooting core (blank memory card recommended)", flush=True)
    client.reboot()
    client.frameadvance(30)

    if not mash_to_menu(client):
        print("FAIL: never reached menu (room 27)", flush=True)
        return None, False

    select_jill_at_char_select(client)

    ram = mash_intro(client, 4500)
    print(f"after intro: {format_spawn_summary(ram)}", flush=True)
    if int(ram.get("character_id", -1)) not in (JILL_ID,):
        print(
            f"WARN: character_id={ram.get('character_id')} after intro "
            f"(want Jill={JILL_ID}); continuing to dining...",
            flush=True,
        )
    client.send_buttons({})
    client.frameadvance(30)

    ram, ok = advance_to_dining_control(client)
    if ok and ram is not None and int(ram.get("character_id", -1)) == JILL_ID:
        return ram, True
    ram2, ok2 = wait_for_dining_control(client, timeout_frames=8000)
    if ok2 and ram2 is not None:
        return ram2, True
    return ram if ok else None, False


def run_load_slot(client: BizHawkClient, slot: int) -> tuple[dict | None, bool]:
    """Best-effort load-game automation from power-on."""
    if slot < 1 or slot > 15:
        raise ValueError("slot must be 1..15")

    print(f"rebooting core; will try LOAD GAME slot {slot}", flush=True)
    client.reboot()
    client.frameadvance(30)

    for _ in range(10):
        tap(client, "cross", hold=2, release=25)

    # Down to LOAD GAME (title default often NEW GAME)
    tap(client, "down", hold=2, release=25)
    tap(client, "cross", hold=2, release=40)

    # Slot list: slot 1 is usually top
    for _ in range(slot - 1):
        tap(client, "down", hold=2, release=20)
    tap(client, "cross", hold=2, release=40)
    # confirm load if prompted
    tap(client, "cross", hold=2, release=60)

    return wait_for_dining_control(client, timeout_frames=20000)


def run_wait_manual(client: BizHawkClient, timeout_s: float) -> tuple[dict | None, bool]:
    print(
        "Load your unbeaten in-game save to the dining room (105) with Jill in control,\n"
        "then leave EmuHawk running — polling RAM...",
        flush=True,
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ram = client.read_ram()
        ok, errs = validate_fresh_dining_spawn(ram, require_jill=True)
        if ok and probe_control(client):
            return ram, True
        if int(ram.get("room_id", -1)) == 5:
            print(f"  dining seen but not ready: {errs}", flush=True)
        time.sleep(2.0)
    return None, False


def launch_emuhawk(port: int) -> subprocess.Popen:
    if not EMUHAWK.is_file():
        raise FileNotFoundError(f"EmuHawk not found: {EMUHAWK}")
    if not ROM.is_file():
        raise FileNotFoundError(f"ROM not found: {ROM}")
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--new-game",
        action="store_true",
        help="power-on new game Jill (default)",
    )
    mode.add_argument(
        "--wait",
        action="store_true",
        help="poll until you load an unbeaten in-game save to dining",
    )
    mode.add_argument(
        "--load-slot",
        type=int,
        metavar="N",
        help="reboot and try to load memory-card slot N",
    )
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument(
        "--launch-emu",
        action="store_true",
        help="spawn EmuHawk automatically (otherwise start it yourself)",
    )
    ap.add_argument("--wait-timeout", type=float, default=600.0)
    ap.add_argument("-o", "--output", type=Path, default=OUT_STATE)
    args = ap.parse_args()

    if not args.wait and not args.load_slot:
        args.new_game = True

    proc: subprocess.Popen | None = None
    connected = False
    client = BizHawkClient(
        port=args.port,
        timeout=600.0,
        connect_timeout=120.0 if args.launch_emu else 600.0,
    )
    client.start_server()
    print(f"listening on port {args.port}", flush=True)

    if args.launch_emu:
        proc = launch_emuhawk(args.port)
        print("EmuHawk launched (waiting for Lua socket...)", flush=True)
        time.sleep(8.0)

    try:
        client.wait_for_client()
        connected = True
        print("connected", flush=True)
        client.set_speed(6400)

        if args.wait:
            ram, ok = run_wait_manual(client, args.wait_timeout)
        elif args.load_slot:
            ram, ok = run_load_slot(client, args.load_slot)
        else:
            ram, ok = run_new_game(client)

        if not ok or ram is None:
            print("FAIL: never reached validated dining control", flush=True)
            return 1

        ok, errs = validate_fresh_dining_spawn(ram, require_jill=True)
        print(f"capture: {format_spawn_summary(ram)}", flush=True)
        if not ok:
            print("FAIL validation:", "; ".join(errs), flush=True)
            if int(ram.get("character_id", -1)) == 0:
                print("HINT: Chris selected — use --wait after loading a Jill save, or retry --new-game", flush=True)
            return 1

        args.output.parent.mkdir(parents=True, exist_ok=True)
        OUT_SHOT.parent.mkdir(parents=True, exist_ok=True)
        client.save_savestate(str(args.output))
        client.screenshot(str(OUT_SHOT))
        client.set_speed(100)
        print(f"OK: saved {args.output}", flush=True)
        print(f"     screenshot {OUT_SHOT}", flush=True)
        return 0
    finally:
        if connected:
            try:
                client.quit()
            except (OSError, ConnectionError):
                pass
        client.close()
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
