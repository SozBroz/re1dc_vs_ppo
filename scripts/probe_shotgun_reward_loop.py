"""Mash interact at the wall shotgun and audit the reward loop."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env
from re1_rl.item_todo import canonicalize
from re1_rl.progress import ProgressTracker
from re1_rl.reward import compute_reward

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"


def newest_quicksave() -> Path:
    saves = sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not saves:
        raise FileNotFoundError(f"no QuickSave state found in {STATE_DIR}")
    return saves[0]


def inventory_names(state: dict) -> set[str]:
    return canonicalize(name for name, _qty in state.get("inventory_slots", []))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-s", type=float, default=120.0)
    parser.add_argument("--port", type=int, default=7794)
    parser.add_argument("--speed", type=int, default=100)
    parser.add_argument("--state", type=Path, default=None)
    args = parser.parse_args()

    state_path = (args.state or newest_quicksave()).resolve()
    bridge = BizHawkClient(
        port=int(args.port), timeout=300.0, connect_timeout=120.0
    )
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={int(args.port)}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
    )

    env: RE1Env | None = None
    try:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=True,
        )
        env.reset()
        bridge.load_savestate(str(state_path))
        bridge.frameadvance(2)

        # Prime inventory tracking from the loaded state: the savestate's
        # starting inventory is baseline, not a pickup caused by this probe.
        initial = env._read_state(track_items=False)
        env._items.update(initial["inventory_slots"])
        initial["new_items"] = []
        env._progress = ProgressTracker()
        env._progress.first_visit(str(initial["room_id"]))
        env._prev_state = dict(initial)
        env._prev_hp = int(initial["hp"])
        env._episode_start_hp = int(initial["hp"])
        env._episode_min_hp = int(initial["hp"])
        previous = dict(initial)

        initial_has = "shotgun" in inventory_names(initial)
        print(
            f"[shotgun-loop] state={state_path.name} room={initial['room_id']} "
            f"initial_shotgun={initial_has} duration={args.duration_s:.1f}s",
            flush=True,
        )

        pickups = 0
        returns = 0
        steps = 0
        event_sum = 0.0
        last_event: str | None = None
        duplicate_events: list[tuple[int, str]] = []
        unexpected: list[tuple[int, dict[str, float]]] = []
        deadline = time.monotonic() + float(args.duration_s)

        while time.monotonic() < deadline:
            # Exact two-frame pair requested by the operator:
            # frame 1 Cross down, frame 2 Cross released.
            bridge.step(
                n=1,
                sticky={},
                pulse=None,
                pulse_hold={"cross": True},
                ring_stride=0,
                capture_final=False,
            )
            bridge.step(
                n=1,
                sticky={},
                pulse=None,
                pulse_hold=None,
                ring_stride=0,
                capture_final=False,
            )
            steps += 1
            current = env._read_state(track_items=True)
            current["step_emulated_frames"] = 2
            reward, raw_bd = compute_reward(
                previous,
                current,
                env._planner,
                progress=env._progress,
                graph=env.graph,
                success_room=env._stage.get("success_room"),
                return_breakdown=True,
            )
            previous = dict(current)
            bd = {key: float(value) for key, value in raw_bd.items()}
            active = {
                key: value
                for key, value in bd.items()
                if abs(value) > 1e-9 and key != "step"
            }
            other = {
                key: value
                for key, value in active.items()
                if key not in {"new_weapon", "shotgun_return"}
            }
            if other:
                unexpected.append((steps, other))

            event: str | None = None
            value = 0.0
            if bd.get("new_weapon", 0.0):
                event = "pickup"
                value = bd["new_weapon"]
                pickups += 1
            if bd.get("shotgun_return", 0.0):
                if event is not None:
                    duplicate_events.append((steps, "simultaneous"))
                event = "return"
                value = bd["shotgun_return"]
                returns += 1
            if event is not None:
                if event == last_event:
                    duplicate_events.append((steps, event))
                last_event = event
                event_sum += value
                print(
                    f"[shotgun-loop] step={steps} event={event} "
                    f"value={value:+.5f} reward={float(reward):+.5f} "
                    f"room={current.get('room_id')}",
                    flush=True,
                )
            if current.get("dead"):
                raise RuntimeError(f"Jill died at pair {steps}")

        final = env._read_state(track_items=False)
        final_has = "shotgun" in inventory_names(final)
        expected_delta = int(final_has) - int(initial_has)
        actual_delta = pickups - returns
        errors: list[str] = []
        if pickups == 0:
            errors.append("shotgun pickup never occurred")
        if duplicate_events:
            errors.append(f"non-alternating events: {duplicate_events[:5]}")
        if unexpected:
            errors.append(f"unexpected rewards: {unexpected[:5]}")
        if actual_delta != expected_delta:
            errors.append(
                f"inventory/event mismatch: delta={actual_delta} "
                f"expected={expected_delta}"
            )
        if abs(event_sum - float(actual_delta)) > 1e-9:
            errors.append(
                f"event reward mismatch: sum={event_sum:+.5f} "
                f"expected={float(actual_delta):+.5f}"
            )

        status = "PASS" if not errors else "FAIL"
        print(
            f"[shotgun-loop] {status} steps={steps} pickups={pickups} "
            f"returns={returns} final_shotgun={final_has} "
            f"event_sum={event_sum:+.5f} room={final.get('room_id')} "
            f"in_control={final.get('in_control')}",
            flush=True,
        )
        for error in errors:
            print(f"[shotgun-loop] ERROR {error}", flush=True)
        if returns == 0:
            print(
                "[shotgun-loop] NOTE stationary interact pairs never performed "
                "a wall replacement; Jill kept the shotgun",
                flush=True,
            )
        return 0 if not errors else 1
    finally:
        if env is not None:
            env.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
