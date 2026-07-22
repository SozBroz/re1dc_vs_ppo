"""Probe: load a BizHawk .State, typewriter save slot 1, mash interact.

Verifies TypewriterSaveDetector + +0.3 typewriter_save reward on RE1Env.step().
Does not call env.reset() (that reloads curriculum init_savestate).

Usage:
  python scripts/probe_typewriter_save_slot1.py \\
    --state states/checkpoints/wp05_seq5_106.State
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import SELECT_SLOT_BASE
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.progress import ProgressTracker
from re1_rl.typewriter_save import count_ink_ribbons, near_main_hall_typewriter

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"

INTERACT = ACTION_NAMES.index("interact")
SELECT_SLOT_0 = SELECT_SLOT_BASE  # in-game save slot 1
FORWARD = ACTION_NAMES.index("forward")
TURN_RIGHT = ACTION_NAMES.index("turn_right")


def _ribbons(state: dict) -> int:
    return count_ink_ribbons(state)


def _summarize(state: dict) -> str:
    inv = state.get("inventory") or []
    return (
        f"room={state.get('room_id')} pos=({state.get('x')},{state.get('z')}) "
        f"ctrl={state.get('in_control')} ribbons={_ribbons(state)} inv={list(inv)} "
        f"near_tw={near_main_hall_typewriter(state)}"
    )


def _load_savestate_file(
    env: RE1Env,
    state_path: Path,
    *,
    meta_path: Path | None,
    cutscene_speed: int,
    skip_uncontrolled: bool,
) -> dict[str, Any]:
    """Direct bridge.load_savestate — never env.reset()."""
    resolved = state_path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"savestate missing: {resolved}")

    env._load_stage()
    from re1_rl.ram_skip import RamSkipper

    env._ram_skip = RamSkipper(
        env.bridge,
        training_speed=100,
        cutscene_speed=int(cutscene_speed),
        skip_chunk=600,
        use_engine_patches=True,
        invisible_during_skip=False,
    )
    env._ram_skip.install_engine_patches()

    print(f"[probe] load_savestate {resolved}", flush=True)
    env.bridge.load_savestate(str(resolved))
    env.bridge.frameadvance(8)
    if skip_uncontrolled:
        skipped, died = env._skip_uncontrolled()
        print(f"[probe] skip_uncontrolled frames={skipped} died={died}", flush=True)
    else:
        print("[probe] skip_uncontrolled disabled (preserve typewriter pose)", flush=True)

    env._progress = ProgressTracker()
    env._visited.reset()
    env._box_cache = None
    env._step_count = 0
    env._pb_captured_triggers = set()
    env._prev_hp = 0

    if meta_path and meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        idx = int(meta.get("waypoint_index", 0))
        assert env._planner is not None
        env._planner._index = idx
        env._progress.max_waypoint = idx
        env._progress.rewarded_waypoint_indices = set(range(idx))
        print(f"[probe] applied checkpoint meta wp={idx} room={meta.get('room_id')}", flush=True)

    state = env._read_state(track_items=True)
    env._seed_episode_progress(state)
    env._visited.update(state["room_id"], state["x"], state["z"])
    env._prev_state = dict(state)
    env._prev_hp = state["hp"] if state["hp"] > 0 else 0

    det = getattr(env, "_typewriter_save_detector", None)
    if det is not None:
        det.begin_episode(from_sidecar=False, state=state)

    return state


def _run_actions(env: RE1Env, actions: list[int], label: str) -> dict[str, Any]:
    print(f"=== {label} ===", flush=True)
    last: dict[str, Any] = {}
    for i, action in enumerate(actions):
        prev = env._read_state(track_items=False)
        rb_before = _ribbons(prev)
        _obs, reward, _t, _tr, info = env.step(action)
        state = env._read_state(track_items=False)
        bd = info.get("reward_breakdown") or {}
        tw = float(bd.get("typewriter_save", 0.0) or 0.0)
        rb_after = _ribbons(state)
        det = env._typewriter_save_detector
        print(
            f"  [{i:02d}] {ACTION_NAMES[action]:14s} r={reward:+.4f} tw={tw:.3f} "
            f"rib={rb_before}->{rb_after} room={state.get('room_id')} "
            f"ctrl={state.get('in_control')} det_pend={getattr(det, '_pending', False)}",
            flush=True,
        )
        last = {
            "reward": reward,
            "typewriter_save": tw,
            "ribbons": rb_after,
            "room": state.get("room_id"),
        }
        if tw > 0:
            print("SUCCESS: +0.3 typewriter_save paid", flush=True)
            break
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7796)
    ap.add_argument(
        "--state",
        type=Path,
        default=ROOT
        / "tools"
        / "BizHawk-2.11.1"
        / "PSX"
        / "State"
        / "Resident Evil - Director's Cut (USA).Nymashock.QuickSave1.State",
        help="BizHawk .State (default: newest QuickSave1)",
    )
    ap.add_argument("--mash-steps", type=int, default=72)
    ap.add_argument("--cutscene-speed", type=int, default=6400)
    ap.add_argument(
        "--skip-uncontrolled",
        action="store_true",
        help="Run cutscene skip after load (default off — keeps typewriter pose)",
    )
    ap.add_argument(
        "--expect-room",
        default="106",
        help="Abort if loaded room_id differs (proves savestate loaded)",
    )
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient

    meta = args.state.with_suffix(".json")
    if not meta.is_file():
        stem = args.state.stem.rsplit("_", 1)[0]
        alt = args.state.parent / f"{stem}.json"
        meta = alt if alt.is_file() else None

    bridge = BizHawkClient(port=args.port, timeout=180.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={args.port}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            frame_skip=8,
            project_root=ROOT,
        )
        state = _load_savestate_file(
            env,
            args.state,
            meta_path=meta,
            cutscene_speed=int(args.cutscene_speed),
            skip_uncontrolled=bool(args.skip_uncontrolled),
        )
        print("=== loaded ===", flush=True)
        print(_summarize(state), flush=True)

        got = str(state.get("room_id", "") or "")
        want = str(args.expect_room)
        if got != want:
            print(
                f"ERROR: savestate did not land in room {want} (got {got!r}). "
                f"Check --state path.",
                flush=True,
            )
            return 2

        if not near_main_hall_typewriter(state):
            print("WARN: not near typewriter coords after load", flush=True)
        if _ribbons(state) < 1:
            print("WARN: no ink_ribbon in inventory after load", flush=True)

        # Typewriter: interact-only mash (select_slot is inventory/equip submenu, not TW).
        save_macro = [INTERACT] * int(args.mash_steps)
        result = _run_actions(env, save_macro, "mash interact (X) at typewriter")

        out = ROOT / "data" / "_probe_typewriter_save_slot1.json"
        out.write_text(
            json.dumps({"loaded": _summarize(state), "result": result}, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {out}", flush=True)
        return 0 if float(result.get("typewriter_save", 0)) > 0 else 1
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        bridge.close()
        proc.terminate()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
