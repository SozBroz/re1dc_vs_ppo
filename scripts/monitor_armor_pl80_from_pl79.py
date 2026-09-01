"""Live play-from-pl79 monitor for the production pl80 mint gate.

Loads the fleet pl79 cell, seeks the planner to ``armor_vent_far``, then lets
you play in EmuHawk while printing the exact OM-object fields and whether
``armor_vent_step_complete`` would mint pl80 right now.

  python scripts/monitor_armor_pl80_from_pl79.py

Controls (EmuHawk window for movement; this console for keys):
  Q = quit
  R = reload pl79
  M = print one verbose dump (all three mirrors)

No minting — this is observe-only.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["RE1_PLANNER_LOYAL"] = "1"
os.environ["RE1_PLANNER_CHUNK"] = "data/planner_chunks/cp05_shield_key.json"
os.environ["RE1_PLANNER_RESET_PIN_FILE"] = str(ROOT / "_tmp" / "pin_pl79_monitor.env")
os.environ["RE1_GO_EXPLORE_CAPTURE"] = "0"
os.environ["RE1_PB_CAPTURE"] = "0"

from re1_rl.armor_room_puzzle import (  # noqa: E402
    ARMOR_EAST_SCRIPT_TARGET,
    ARMOR_SCRIPT_TARGET_TOLERANCE,
    ARMOR_WEST_SCRIPT_TARGET,
    armor_stable_statues_seated,
    armor_vent_step_complete,
)
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import assert_rom_present, emuhawk_argv  # noqa: E402
from re1_rl.env import RE1Env  # noqa: E402
from re1_rl.pushable import PUSH_GAME_STATE  # noqa: E402
from re1_rl.sticky_input import empty_sticky  # noqa: E402

PORT = 5991
CURRICULUM = ROOT / "curriculum" / "planner_loyal_one_leg.json"
PIN = ROOT / "_tmp" / "pin_pl79_monitor.env"
PL79 = ROOT / "states" / "planner_loyal" / "cells" / "pl79" / "cell.State"
LOG = ROOT / "_tmp" / "armor_pl80_monitor.log"

PL79_STEP = {"beat_id": "armor_vent_door", "site_id": "armor_vent_door"}
PL80_STEP = {"beat_id": "armor_vent_far", "site_id": "armor_vent_far"}


def _poll_key() -> str | None:
    if sys.platform == "win32":
        import msvcrt

        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            return ch.lower() if ch else None
        return None
    import select

    if select.select([sys.stdin], [], [], 0.0)[0]:
        return sys.stdin.read(1).lower()
    return None


def _log(msg: str) -> None:
    print(msg, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def _xz(state: dict, prefix: str, suffix: str = "") -> tuple[int, int]:
    return (
        int(state.get(f"armor_{prefix}_statue_x{suffix}", 0) or 0),
        int(state.get(f"armor_{prefix}_statue_z{suffix}", 0) or 0),
    )


def _d(xz: tuple[int, int], target: tuple[int, int]) -> tuple[int, int]:
    return xz[0] - target[0], xz[1] - target[1]


def _mirrors_agree(state: dict, prefix: str, target: tuple[int, int]) -> bool:
    for suffix in ("", "_b", "_c"):
        x, z = _xz(state, prefix, suffix)
        if abs(x - target[0]) > ARMOR_SCRIPT_TARGET_TOLERANCE:
            return False
        if abs(z - target[1]) > ARMOR_SCRIPT_TARGET_TOLERANCE:
            return False
    return True


def seek_pl80_step(env: RE1Env) -> None:
    queue = env._planner_loyal_queue
    if queue is None:
        raise RuntimeError("planner queue missing")
    idx = next(
        i
        for i, s in enumerate(queue._steps)
        if s.get("beat_id") == "armor_vent_far"
    )
    queue.seek(idx)


def snapshot(env: RE1Env) -> dict:
    state = env._read_state(track_items=False)
    queue = env._planner_loyal_queue
    beat = None
    if queue is not None and isinstance(queue.current, dict):
        beat = queue.current.get("beat_id")
    east = _xz(state, "east")
    west = _xz(state, "west")
    seated = armor_stable_statues_seated(state)
    return {
        "beat": beat,
        "room": state.get("room_id"),
        "jill": (
            int(state.get("x") or 0),
            int(state.get("z") or 0),
            int(state.get("facing") or 0),
        ),
        "gs": hex(int(state.get("game_state") or 0)),
        "push": int(state.get("game_state") or 0) == PUSH_GAME_STATE,
        "east": east,
        "west": west,
        "dE": _d(east, ARMOR_EAST_SCRIPT_TARGET),
        "dW": _d(west, ARMOR_WEST_SCRIPT_TARGET),
        "mE": _mirrors_agree(state, "east", ARMOR_EAST_SCRIPT_TARGET),
        "mW": _mirrors_agree(state, "west", ARMOR_WEST_SCRIPT_TARGET),
        "seated": seated,
        "pl79": armor_vent_step_complete(PL79_STEP, state),
        "pl80": armor_vent_step_complete(PL80_STEP, state),
        "state": state,
    }


def fmt_line(row: dict) -> str:
    mint = "MINT_PL80" if row["pl80"] else ("pl79_ok" if row["pl79"] else "----")
    return (
        f"{mint:<9} beat={row['beat']} room={row['room']} "
        f"push={int(row['push'])} jill={row['jill']} "
        f"east={row['east']} dE={row['dE']} mE={int(row['mE'])} "
        f"west={row['west']} dW={row['dW']} mW={int(row['mW'])} "
        f"seated={row['seated']} pl79={int(row['pl79'])} pl80={int(row['pl80'])}"
    )


def dump_mirrors(row: dict) -> None:
    state = row["state"]
    _log(
        f"targets east={ARMOR_EAST_SCRIPT_TARGET} "
        f"west={ARMOR_WEST_SCRIPT_TARGET} tol=+/-{ARMOR_SCRIPT_TARGET_TOLERANCE}"
    )
    for prefix in ("east", "west"):
        for suffix in ("", "_b", "_c"):
            xz = _xz(state, prefix, suffix)
            _log(f"  {prefix}{suffix or '_a':<4} {xz}")


def load_pl79(env: RE1Env) -> None:
    env.bridge.load_savestate(str(PL79))
    env.bridge.step(n=1, sticky=empty_sticky())
    seek_pl80_step(env)
    row = snapshot(env)
    _log(f"RELOAD {fmt_line(row)}")
    if not row["pl79"]:
        _log("WARNING: pl79 cell does not pass the east-vent gate")
    if row["pl80"]:
        _log("WARNING: pl79 cell already passes pl80 (both seated)")


def main() -> int:
    assert_rom_present()
    if not PL79.is_file():
        print("missing", PL79)
        return 1
    PIN.parent.mkdir(parents=True, exist_ok=True)
    PIN.write_text(
        "RE1_PLANNER_RESET_PIN_INDEX=79\nRE1_PLANNER_RESET_PIN_RANGE=\n",
        encoding="utf-8",
    )

    argv = emuhawk_argv(port=PORT)
    bridge = BizHawkClient(port=PORT, timeout=180.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        argv,
        cwd=str(Path(argv[0]).parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = None
    last_pl80 = False
    try:
        bridge.wait_for_client()
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            frame_skip=8,
            project_root=ROOT,
        )
        env.reset()
        env.bridge.step(n=1, sticky=empty_sticky())
        load_pl79(env)
        _log(
            "Play in EmuHawk. Push the west statue onto its vent. "
            "Line flips to MINT_PL80 when the fleet would mint. Q=quit R=reload M=mirrors"
        )
        last_print = 0.0
        while True:
            key = _poll_key()
            if key == "q":
                _log("quit")
                return 0
            if key == "r":
                load_pl79(env)
            if key == "m":
                dump_mirrors(snapshot(env))

            # Keep Lua sticky clear so keyboard/pad in EmuHawk works.
            env.bridge.frameadvance(1)
            env._step_count += 1
            now = time.monotonic()
            if now - last_print < 0.25:
                continue
            last_print = now
            row = snapshot(env)
            line = fmt_line(row)
            # Always print on pl80 edge; otherwise throttle to 0.25s.
            if row["pl80"] and not last_pl80:
                _log(f"*** WOULD MINT PL80 NOW *** {line}")
            else:
                print(line, flush=True)
            last_pl80 = bool(row["pl80"])
    finally:
        if env is not None:
            try:
                env.close()
            except Exception:
                pass
        try:
            if getattr(bridge, "_client", None) is not None:
                bridge.quit()
        except (OSError, RuntimeError):
            pass
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
