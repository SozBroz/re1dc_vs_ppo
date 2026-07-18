"""Binary-ish search: min continuous Up frames to start shelf push.

Reload newest QuickSave each trial. Hold Up for N emulated frames (no Cross).
Success = game_state changes to 0x80800044 and/or facing snaps / x moves.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import DEFAULT_RAM_FIELDS

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
PORT = 7788
PUSH_GS = 0x80800044


def newest() -> Path:
    return sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[0]


def snap(bridge: BizHawkClient) -> dict:
    ram = bridge.read_ram(list(DEFAULT_RAM_FIELDS))
    return {
        "x": int(ram["player_x"]),
        "z": int(ram["player_z"]),
        "facing": int(ram["player_facing"]),
        "gs": int(ram["game_state"]),
    }


def hold_up(bridge: BizHawkClient, frames: int) -> None:
    """Hold Up continuously for exactly ``frames`` emulated frames."""
    if frames <= 0:
        return
    # One sticky step of N frames — Lua holds buttons across the batch.
    bridge.step(
        n=frames,
        sticky={"up": True, "down": False, "left": False, "right": False, "square": False},
        pulse=None,
        pulse_hold=None,
    )


def observe_after(bridge: BizHawkClient, settle_frames: int = 8) -> dict:
    """Advance a few frames still holding Up so a late-trigger push can show."""
    bridge.step(
        n=settle_frames,
        sticky={"up": True, "down": False, "left": False, "right": False, "square": False},
    )
    return snap(bridge)


def trial(bridge: BizHawkClient, state: Path, hold_frames: int) -> dict:
    bridge.load_savestate(str(state.resolve()))
    bridge.frameadvance(2)
    before = snap(bridge)
    hold_up(bridge, hold_frames)
    after_hold = snap(bridge)
    # If not yet pushed, give a short continue-hold window (same continuous press)
    # so we don't miss a trigger that needs a couple more frames of contact.
    # For min-search we only count success if push started during the hold itself
    # OR within 0 extra — use after_hold only for strict; also report settle.
    settled = observe_after(bridge, settle_frames=4)
    pushed_at_hold = after_hold["gs"] == PUSH_GS or (
        after_hold["gs"] != before["gs"] and after_hold["facing"] != before["facing"]
    )
    pushed_at_settle = settled["gs"] == PUSH_GS or (
        settled["gs"] != before["gs"] and settled["facing"] != before["facing"]
    )
    moved = abs(settled["x"] - before["x"]) + abs(settled["z"] - before["z"])
    return {
        "hold": hold_frames,
        "before": before,
        "after_hold": after_hold,
        "settled": settled,
        "pushed_at_hold": pushed_at_hold,
        "pushed_at_settle": pushed_at_settle,
        "moved": moved,
    }


def main() -> int:
    state = newest()
    print(f"[min] state={state.name} mtime={time.ctime(state.stat().st_mtime)}", flush=True)

    bridge = BizHawkClient(port=PORT, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(400)
        bridge.load_savestate(str(state.resolve()))
        bridge.frameadvance(2)
        base = snap(bridge)
        print(f"[min] loaded gs=0x{base['gs']:08X} pos=({base['x']},{base['z']}) facing={base['facing']}", flush=True)

        # Coarse sweep then refine around first success
        candidates = [4, 8, 12, 16, 20, 24, 28, 32, 40]
        results = []
        first_ok = None
        for n in candidates:
            r = trial(bridge, state, n)
            results.append(r)
            # Min search uses push visible at end of hold (no settle credit).
            ok = r["pushed_at_hold"]
            print(
                f"hold={n:2d}: pushed_hold={r['pushed_at_hold']} "
                f"pushed_settle={r['pushed_at_settle']} moved={r['moved']} "
                f"gs_hold=0x{r['after_hold']['gs']:08X} "
                f"gs_settle=0x{r['settled']['gs']:08X} "
                f"facing {r['before']['facing']}->{r['after_hold']['facing']}",
                flush=True,
            )
            if ok and first_ok is None:
                first_ok = n

        if first_ok is None:
            print("[min] no push in coarse sweep (at end of hold)", flush=True)
            return 2

        # Refine: binary search between previous fail and first_ok
        lo = 0
        for n, r in zip(candidates, results):
            if n < first_ok:
                lo = n
        hi = first_ok
        print(f"[min] refining between {lo} (fail) and {hi} (ok)", flush=True)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            r = trial(bridge, state, mid)
            ok = r["pushed_at_hold"]
            print(
                f"hold={mid:2d}: pushed_hold={r['pushed_at_hold']} "
                f"pushed_settle={r['pushed_at_settle']} moved={r['moved']} "
                f"gs_hold=0x{r['after_hold']['gs']:08X} "
                f"gs_settle=0x{r['settled']['gs']:08X}",
                flush=True,
            )
            if ok:
                hi = mid
            else:
                lo = mid

        # Confirm hi a couple times; also test hi without settle padding
        print(f"[min] confirming hold={hi} (strict: no settle pad)...", flush=True)
        for rep in range(3):
            bridge.load_savestate(str(state.resolve()))
            bridge.frameadvance(2)
            before = snap(bridge)
            hold_up(bridge, hi)
            after = snap(bridge)
            ok = after["gs"] == PUSH_GS or (
                after["gs"] != before["gs"] and after["facing"] != before["facing"]
            )
            print(
                f"  confirm#{rep+1} hold={hi}: ok={ok} "
                f"gs=0x{after['gs']:08X} facing={before['facing']}->{after['facing']} "
                f"d=({after['x']-before['x']},{after['z']-before['z']})",
                flush=True,
            )

        # Also confirm hi-1 fails strictly
        if hi > 1:
            bridge.load_savestate(str(state.resolve()))
            bridge.frameadvance(2)
            before = snap(bridge)
            hold_up(bridge, hi - 1)
            after = snap(bridge)
            ok = after["gs"] == PUSH_GS or (
                after["gs"] != before["gs"] and after["facing"] != before["facing"]
            )
            print(
                f"  confirm hold={hi-1}: ok={ok} gs=0x{after['gs']:08X} "
                f"facing={before['facing']}->{after['facing']}",
                flush=True,
            )

        steps_at_4 = (hi + 3) // 4
        print(
            f"\n[min] MINIMUM continuous Up frames to START push: {hi}\n"
            f"[min] At training frame_skip=4 that is {steps_at_4} consecutive "
            f"forward step(s) ({steps_at_4}*4={steps_at_4*4} frames reserved).",
            flush=True,
        )
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
