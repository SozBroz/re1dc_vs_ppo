"""Scramble OPTIONS cursor, hunt RAM, and test dismiss strategies.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_options_scramble_dismiss.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_options_scramble_dismiss.py --scramble 12
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.game_session import options_menu_from_ram
from re1_rl.memory_map import (
    GAME_MODE,
    GAME_STATE,
    PLAYER_HP,
    PLAYER_X,
    PLAYER_Z,
)
from re1_rl.options_menu_macro import dismiss_options_menu, read_options_ram
from re1_rl.ram_skip import pause_menu_tree_from_ram

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"

SCAN_WINDOWS = (
    (0x800C3000, 96),
    (0x800C8660, 64),
    (0x800B7F80, 64),
)

POLL = [
    ("game_state", GAME_STATE, "u32"),
    ("game_mode", GAME_MODE, "u8"),
    ("player_hp", PLAYER_HP, "u16"),
]


def newest_quicksave() -> Path:
    states = sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not states:
        raise FileNotFoundError(f"no QuickSave under {STATE_DIR}")
    return states[0]


def _scan(bridge: BizHawkClient) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for base, count in SCAN_WINDOWS:
        fields = [(f"b{base + i}", base + i, "u8") for i in range(count)]
        raw = bridge.read_ram(fields)
        out[f"0x{base:08X}"] = [int(raw[f"b{base + i}"]) for i in range(count)]
    return out


def _diff(a: dict[str, list[int]], b: dict[str, list[int]]) -> list[tuple[str, int, int, int]]:
    hits: list[tuple[str, int, int, int]] = []
    for key in a:
        for i, (x, y) in enumerate(zip(a[key], b[key])):
            if x != y:
                hits.append((key, i, x, y))
    return hits


def _tap(bridge: BizHawkClient, buttons: dict[str, bool], *, frames: int = 8) -> None:
    bridge.step(buttons=buttons, n=frames)
    bridge.step(buttons={}, n=12)


def _fmt_ram(ram: dict[str, int]) -> str:
    return (
        f"gs=0x{ram['game_state']:08X} mode=0x{ram['game_mode']:02X} "
        f"room={ram['room_id']} hp={ram['player_hp']}"
    )


def _cleared(bridge: BizHawkClient) -> bool:
    ram = read_options_ram(bridge)
    return not (
        options_menu_from_ram(ram)
        or pause_menu_tree_from_ram(ram)
    )


def _reload_options(bridge: BizHawkClient, state: Path) -> dict[str, int]:
    bridge.load_savestate(str(state))
    bridge.frameadvance(5)
    ram = read_options_ram(bridge)
    if not options_menu_from_ram(ram):
        raise RuntimeError(f"savestate not on OPTIONS: {_fmt_ram(ram)}")
    return ram


def hunt_cursor_byte(bridge: BizHawkClient, state: Path) -> None:
    print("=== cursor RAM hunt (direction taps) ===", flush=True)
    _reload_options(bridge, state)
    base = _scan(bridge)
    for direction in ("down", "up", "right", "left"):
        _reload_options(bridge, state)
        _tap(bridge, {direction: True})
        after = _scan(bridge)
        hits = _diff(base, after)
        print(f"[hunt] {direction}: {len(hits)} byte diffs", flush=True)
        for key, off, old, new in hits[:20]:
            addr = int(key, 16) + off
            print(f"  0x{addr:08X}: {old} -> {new}", flush=True)


def scramble(bridge: BizHawkClient, *, taps: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    dirs = ("up", "down", "left", "right")
    seq: list[str] = []
    for _ in range(taps):
        d = rng.choice(dirs)
        n = rng.randint(1, 4)
        for _ in range(n):
            _tap(bridge, {d: True})
        seq.append(f"{d}x{n}")
    return seq


def try_sequence(
    bridge: BizHawkClient,
    state: Path,
    *,
    label: str,
    buttons: list[str],
    prev_hp: int,
) -> bool:
    _reload_options(bridge, state)
    for btn in buttons:
        if btn == "wait":
            bridge.step(buttons={}, n=20)
            continue
        _tap(bridge, {btn: True})
    if _cleared(bridge):
        pos0 = bridge.read_ram([("x", PLAYER_X, "s16"), ("z", PLAYER_Z, "s16")])
        bridge.step(buttons={"up": True}, n=40)
        pos1 = bridge.read_ram([("x", PLAYER_X, "s16"), ("z", PLAYER_Z, "s16")])
        moved = int(pos0["x"]) != int(pos1["x"]) or int(pos0["z"]) != int(pos1["z"])
        print(f"[seq] {label}: CLEARED moved={moved}", flush=True)
        return True
    ram = read_options_ram(bridge)
    print(f"[seq] {label}: FAIL still options {_fmt_ram(ram)}", flush=True)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7793)
    ap.add_argument("--state", type=Path, default=None)
    ap.add_argument("--scramble", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--hunt-only", action="store_true")
    args = ap.parse_args()
    state = args.state or newest_quicksave()
    print(f"state={state} mtime={time.ctime(state.stat().st_mtime)}", flush=True)

    bridge = BizHawkClient(
        port=args.port,
        timeout=120.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / f"_options_scramble_{args.port}.png"),
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
            "--chromeless",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(400)
        ram0 = _reload_options(bridge, state)
        hp = int(ram0["player_hp"])
        print(f"baseline: {_fmt_ram(ram0)}", flush=True)

        hunt_cursor_byte(bridge, state)
        if args.hunt_only:
            return 0

        # Candidate exit strategies from RE1 CONFIG subtree layout hunts.
        candidates: list[tuple[str, list[str]]] = [
            ("rrx+start", ["right", "right", "cross", "start"]),
            ("downx6+cross+start", ["down"] * 6 + ["cross", "start"]),
            ("downx8+cross+start", ["down"] * 8 + ["cross", "start"]),
            ("upx6+cross+start", ["up"] * 6 + ["cross", "start"]),
            ("leftx4+downx4+cross+start", ["left"] * 4 + ["down"] * 4 + ["cross", "start"]),
            ("circle", ["circle"]),
            ("circle+circle", ["circle", "circle"]),
            ("triangle", ["triangle"]),
            ("cross", ["cross"]),
            ("start", ["start"]),
            ("down+cross+start", ["down", "cross", "start"]),
            ("right+cross+start", ["right", "cross", "start"]),
        ]

        print("=== baseline dismiss (no scramble) ===", flush=True)
        _reload_options(bridge, state)
        still, frames, report = dismiss_options_menu(
            bridge, prev_hp=hp, episode_start_hp=hp, max_attempts=1
        )
        print(f"macro once: still={still} frames={frames} report={report}", flush=True)

        print(f"=== scramble {args.scramble} taps seed={args.seed} ===", flush=True)
        passes = 0
        trials = 0
        for trial in range(args.scramble):
            _reload_options(bridge, state)
            seq = scramble(bridge, taps=random.randint(3, 8), seed=args.seed + trial)
            ram = read_options_ram(bridge)
            print(f"[trial {trial}] scrambled {seq} -> {_fmt_ram(ram)}", flush=True)
            ok = False
            for label, buttons in candidates:
                if try_sequence(bridge, state, label=label, buttons=buttons, prev_hp=hp):
                    ok = True
                    break
            if not ok:
                _reload_options(bridge, state)
                scramble(bridge, taps=5, seed=args.seed + trial + 1000)
                still2, _, rep2 = dismiss_options_menu(
                    bridge, prev_hp=hp, episode_start_hp=hp, max_attempts=3
                )
                ok = not still2
                print(f"[trial {trial}] macro after rescramble: ok={ok} {rep2}", flush=True)
            trials += 1
            passes += int(ok)
        print(f"SUMMARY: {passes}/{trials} recovered after scramble", flush=True)
        return 0 if passes == trials else 1
    finally:
        try:
            bridge._request({"cmd": "quit"})
        except Exception:
            pass
        bridge.close()
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
