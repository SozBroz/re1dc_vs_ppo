"""Live harness: does pushing the *west* (correct) statue trip inplace-kill?

Loads fleet pl79, seeks ``armor_vent_far``, then lets you push in EmuHawk while
printing the same detector the fleet uses (``armor_inplace_statue_push_detected``).

What to look for
----------------
- ``WEST_OK`` — west moved while pushing, east did not, no breach.
  (This is the intended shove; episode must NOT end.)
- ``FALSE_POS`` — breach fired while west moved and east did *not*.
  (Detector wrongly killing the correct push.)
- ``EAST_KILL`` — breach with east moved while seated.
  (Expected: shoving the already-seated east statue.)
- ``JITTER_KILL`` — breach with east delta above threshold during a west push
  even if you never touched east (coords noise / coupled motion).

Usage
-----
  python scripts/harness_armor_inplace_west_push.py

Keys (this console): Q=quit  R=reload pl79  M=mirror dump
Play movement in the EmuHawk window.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["RE1_PLANNER_LOYAL"] = "1"
os.environ["RE1_PLANNER_CHUNK"] = "data/planner_chunks/cp05_shield_key.json"
os.environ["RE1_PLANNER_RESET_PIN_FILE"] = str(
    ROOT / "_tmp" / "pin_pl79_inplace_harness.env"
)
os.environ["RE1_GO_EXPLORE_CAPTURE"] = "0"
os.environ["RE1_PB_CAPTURE"] = "0"

from re1_rl.armor_room_puzzle import (  # noqa: E402
    ARMOR_EAST_SCRIPT_TARGET,
    ARMOR_EAST_SCRIPT_TARGET_TOLERANCE,
    ARMOR_STATUE_MOVE_THRESHOLD,
    ARMOR_WEST_SCRIPT_TARGET,
    ARMOR_WEST_SCRIPT_TARGET_TOLERANCE,
    armor_inplace_statue_push_detected,
    armor_pushing,
    armor_stable_statues_seated,
    armor_statue_progress_reward,
    armor_vent_step_complete,
)
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import assert_rom_present, emuhawk_argv  # noqa: E402
from re1_rl.env import RE1Env  # noqa: E402
from re1_rl.pushable import PUSH_GAME_STATE  # noqa: E402
from re1_rl.sticky_input import empty_sticky  # noqa: E402

PORT = 5993
FRAME_SKIP = 8
CURRICULUM = ROOT / "curriculum" / "planner_loyal_one_leg.json"
PIN = ROOT / "_tmp" / "pin_pl79_inplace_harness.env"
PL79 = ROOT / "states" / "planner_loyal" / "cells" / "pl79" / "cell.State"
LOG = ROOT / "_tmp" / "armor_inplace_west_push.log"

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


def _delta(prev: dict, cur: dict, prefix: str) -> float:
    a = _xz(prev, prefix)
    b = _xz(cur, prefix)
    return math.hypot(b[0] - a[0], b[1] - a[1])


def seek_pl80_step(env: RE1Env) -> None:
    queue = env._planner_loyal_queue
    if queue is None:
        raise RuntimeError("planner queue missing")
    idx = next(
        i for i, s in enumerate(queue._steps) if s.get("beat_id") == "armor_vent_far"
    )
    queue.seek(idx)


def classify(
    *,
    breach: bool,
    push: bool,
    east_moved: bool,
    west_moved: bool,
    east_seated: bool,
) -> str:
    if not breach:
        if push and west_moved and not east_moved:
            return "WEST_OK"
        if push and west_moved and east_moved:
            return "BOTH_MOVE"
        if push:
            return "PUSH"
        return "idle"
    if east_moved and east_seated and not west_moved:
        return "EAST_KILL"
    if west_moved and not east_moved:
        return "FALSE_POS"
    if east_moved and west_moved:
        return "JITTER_KILL"
    if east_moved and east_seated:
        return "EAST_KILL"
    return "BREACH_??"


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
        "push": armor_pushing(state),
        "east": east,
        "west": west,
        "seated": seated,
        "pl80": armor_vent_step_complete(PL80_STEP, state),
        "state": state,
    }


def fmt(
    row: dict,
    *,
    dE: float,
    dW: float,
    breach: bool,
    tag: str,
    drip: float,
) -> str:
    e_mv = int(dE >= ARMOR_STATUE_MOVE_THRESHOLD)
    w_mv = int(dW >= ARMOR_STATUE_MOVE_THRESHOLD)
    alarm = "***" if breach else "   "
    return (
        f"{alarm} {tag:<11} breach={int(breach)} push={int(row['push'])} "
        f"dE={dE:6.1f} e_mv={e_mv} dW={dW:6.1f} w_mv={w_mv} "
        f"seated={row['seated']} west={row['west']} east={row['east']} "
        f"jill={row['jill']} drip={drip:+.3f} pl80={int(row['pl80'])} "
        f"beat={row['beat']} thr={ARMOR_STATUE_MOVE_THRESHOLD}"
    )


def dump_mirrors(row: dict) -> None:
    state = row["state"]
    _log(
        f"targets east={ARMOR_EAST_SCRIPT_TARGET}+/-"
        f"{ARMOR_EAST_SCRIPT_TARGET_TOLERANCE}  "
        f"west={ARMOR_WEST_SCRIPT_TARGET}+/-"
        f"{ARMOR_WEST_SCRIPT_TARGET_TOLERANCE}  "
        f"move_thr={ARMOR_STATUE_MOVE_THRESHOLD}"
    )
    for prefix in ("east", "west"):
        for suffix in ("", "_b", "_c"):
            _log(f"  {prefix}{suffix or '_a':<4} {_xz(state, prefix, suffix)}")


def load_pl79(env: RE1Env) -> dict:
    env.bridge.load_savestate(str(PL79))
    env.bridge.step(n=1, sticky=empty_sticky())
    seek_pl80_step(env)
    row = snapshot(env)
    _log(
        f"RELOAD beat={row['beat']} room={row['room']} seated={row['seated']} "
        f"east={row['east']} west={row['west']} push={int(row['push'])} "
        f"gs={row['gs']} pl80={int(row['pl80'])}"
    )
    if row["beat"] != "armor_vent_far":
        _log("WARNING: queue not on armor_vent_far — detector will stay silent")
    if not row["seated"][0]:
        _log("WARNING: east not seated at pl79 start — EAST_KILL path won't fire")
    if row["seated"][1]:
        _log("WARNING: west already seated — nothing left to push")
    return row


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
    if LOG.is_file():
        LOG.write_text("", encoding="utf-8")

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
    counts = {
        "WEST_OK": 0,
        "FALSE_POS": 0,
        "EAST_KILL": 0,
        "JITTER_KILL": 0,
        "BOTH_MOVE": 0,
        "BREACH_??": 0,
    }
    breach_latched = False
    try:
        bridge.wait_for_client()
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            frame_skip=FRAME_SKIP,
            project_root=ROOT,
        )
        env.reset()
        env.bridge.step(n=1, sticky=empty_sticky())
        row = load_pl79(env)
        prev_state = row["state"]
        _log(
            "Play in EmuHawk. Push WEST statue toward its vent. "
            "Watch for FALSE_POS / JITTER_KILL vs WEST_OK. "
            "Q=quit R=reload M=mirrors"
        )
        last_print = 0.0
        while True:
            key = _poll_key()
            if key == "q":
                _log(f"quit counts={counts} breach_latched={int(breach_latched)}")
                return 0
            if key == "r":
                row = load_pl79(env)
                prev_state = row["state"]
                breach_latched = False
                continue
            if key == "m":
                dump_mirrors(snapshot(env))

            for _ in range(FRAME_SKIP):
                env.bridge.frameadvance(1)
                env._step_count += 1

            row = snapshot(env)
            cur = row["state"]
            dE = _delta(prev_state, cur, "east")
            dW = _delta(prev_state, cur, "west")
            east_moved = dE >= ARMOR_STATUE_MOVE_THRESHOLD
            west_moved = dW >= ARMOR_STATUE_MOVE_THRESHOLD
            breach = bool(
                armor_inplace_statue_push_detected(
                    prev_state, cur, env._planner_loyal_queue
                )
            )
            drip = float(
                armor_statue_progress_reward(
                    prev_state, cur, env._planner_loyal_queue
                )
            )
            tag = classify(
                breach=breach,
                push=bool(row["push"] or armor_pushing(prev_state)),
                east_moved=east_moved,
                west_moved=west_moved,
                east_seated=bool(row["seated"][0]),
            )
            if tag in counts:
                counts[tag] += 1

            line = fmt(row, dE=dE, dW=dW, breach=breach, tag=tag, drip=drip)
            now = time.monotonic()
            interesting = (
                breach
                or abs(drip) > 1e-9
                or east_moved
                or west_moved
                or row["push"]
                or tag in ("FALSE_POS", "JITTER_KILL", "EAST_KILL", "WEST_OK")
            )
            if breach and not breach_latched:
                breach_latched = True
                _log(f"*** EPISODE WOULD END HERE (fleet -4) *** {line}")
            elif interesting or now - last_print >= 0.35:
                if breach or tag in ("FALSE_POS", "JITTER_KILL", "EAST_KILL"):
                    _log(line)
                else:
                    print(line, flush=True)
                last_print = now

            if row["pl80"] and not breach:
                _log(f"*** WOULD MINT PL80 (no inplace breach) *** {line}")

            prev_state = cur
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
