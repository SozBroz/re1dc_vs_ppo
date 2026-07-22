"""Probe typewriter save detection across memory-card slots.

Reloads QuickSave1 before each slot, navigates Save -> slot N -> Yes with
raw cross/down taps (same timing as gold-path probe), then waits for
TypewriterSaveDetector + env +0.3 via noop steps.

Usage:
  python scripts/probe_typewriter_save_all_slots.py --slots 1 2 3 5 15
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

from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.ingame_save import tap
from re1_rl.reward import TYPEWRITER_SAVE_BONUS, compute_reward
from re1_rl.typewriter_save import count_ink_ribbons
from scripts.probe_typewriter_save_slot1 import _load_savestate_file
from tests.test_scaffolding import make_planner

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
DEFAULT_STATE = (
    ROOT
    / "tools/BizHawk-2.11.1/PSX/State/Resident Evil - Director's Cut (USA).Nymashock.QuickSave1.State"
)
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
INTERACT = ACTION_NAMES.index("interact")
TURN_RIGHT = ACTION_NAMES.index("turn_right")
TURN_LEFT = ACTION_NAMES.index("turn_left")
FORWARD = ACTION_NAMES.index("forward")


def _face_typewriter(env: RE1Env) -> None:
    """Small turn sweep from gold-path probe — needed for TW interact."""
    for action in [TURN_RIGHT] * 4 + [TURN_LEFT] * 8 + [FORWARD]:
        env.step(action)


def _poll_det(
    env: RE1Env,
    prev: dict[str, Any],
    label: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Read state, advance detector; return (cur, result) if save completed."""
    cur = env._read_state(track_items=True)
    det = env._typewriter_save_detector
    if det.update(prev, cur):
        _, bd = compute_reward(
            prev,
            cur,
            make_planner(),
            progress=env._progress,
            typewriter_save_complete=True,
            return_breakdown=True,
        )
        tw = float(bd.get("typewriter_save", 0.0) or 0.0)
        return cur, {
            "ok": tw >= TYPEWRITER_SAVE_BONUS - 1e-6,
            "typewriter_save": tw,
            "ribbons_after": count_ink_ribbons(cur),
            "completed_room": det.completed_room,
            "in_control": cur.get("in_control"),
            "phase": label,
        }
    return cur, None


def _tap_and_poll(
    env: RE1Env,
    bridge: Any,
    prev: dict[str, Any],
    button: str,
    label: str,
    *,
    hold: int = 2,
    release: int = 40,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    tap(bridge, button, hold=hold, release=release)
    return _poll_det(env, prev, label)


def _save_to_slot(
    env: RE1Env,
    bridge: Any,
    slot: int,
    ribbons_before: int,
) -> dict[str, Any]:
    prev = env._read_state(track_items=True)
    prev, hit = _tap_and_poll(env, bridge, prev, "cross", "open_tw")
    if hit:
        hit["slot"] = slot
        hit["ribbons_before"] = ribbons_before
        return hit
    prev, hit = _tap_and_poll(env, bridge, prev, "cross", "select_save")
    if hit:
        hit["slot"] = slot
        hit["ribbons_before"] = ribbons_before
        return hit
    for n in range(slot - 1):
        prev, hit = _tap_and_poll(
            env, bridge, prev, "down", f"slot_down_{n + 1}", release=25
        )
        if hit:
            hit["slot"] = slot
            hit["ribbons_before"] = ribbons_before
            return hit
    prev, hit = _tap_and_poll(env, bridge, prev, "cross", "pick_slot")
    if hit:
        hit["slot"] = slot
        hit["ribbons_before"] = ribbons_before
        return hit
    prev, hit = _tap_and_poll(env, bridge, prev, "cross", "confirm_yes")
    if hit:
        hit["slot"] = slot
        hit["ribbons_before"] = ribbons_before
        return hit

    rb = count_ink_ribbons(prev)
    if rb < ribbons_before:
        print(f"  ribbon dropped to {rb} after menu; mashing cinema", flush=True)

    for i in range(250):
        tap(bridge, "cross", hold=1, release=4)
        cur = env._read_state(track_items=True)
        if count_ink_ribbons(cur) < ribbons_before and i < 30:
            print(
                f"    cinema[{i}] rib={count_ink_ribbons(cur)} "
                f"ctrl={cur.get('in_control')} pend={env._typewriter_save_detector._pending}",
                flush=True,
            )
        if env._typewriter_save_detector.update(prev, cur):
            _, bd = compute_reward(
                prev,
                cur,
                make_planner(),
                progress=env._progress,
                typewriter_save_complete=True,
                return_breakdown=True,
            )
            tw = float(bd.get("typewriter_save", 0.0) or 0.0)
            return {
                "ok": tw >= TYPEWRITER_SAVE_BONUS - 1e-6,
                "steps": i + 1,
                "typewriter_save": tw,
                "ribbons_before": ribbons_before,
                "ribbons_after": count_ink_ribbons(cur),
                "completed_room": env._typewriter_save_detector.completed_room,
                "in_control": cur.get("in_control"),
                "phase": "cinema_mash",
                "slot": slot,
            }
        prev = cur

    det = env._typewriter_save_detector
    return {
        "ok": False,
        "typewriter_save": 0.0,
        "ribbons_before": ribbons_before,
        "ribbons_after": count_ink_ribbons(prev),
        "completed_room": det.completed_room,
        "pending": det._pending,
        "slot": slot,
    }


def _try_slot(env: RE1Env, state_path: Path, slot: int) -> dict[str, Any]:
    print(f"\n=== slot {slot} ===", flush=True)
    state = _load_savestate_file(
        env,
        state_path,
        meta_path=None,
        cutscene_speed=6400,
        skip_uncontrolled=False,
    )
    ribbons_before = count_ink_ribbons(state)
    if ribbons_before < 1:
        return {"slot": slot, "ok": False, "error": "no ink_ribbon after load"}
    if str(state.get("room_id", "")) != "106":
        return {
            "slot": slot,
            "ok": False,
            "error": f"wrong room {state.get('room_id')!r}",
        }

    bridge = env.bridge
    _face_typewriter(env)
    for _ in range(12):
        env.step(INTERACT)
    ribbons_before = count_ink_ribbons(env._read_state(track_items=True))
    result = _save_to_slot(env, bridge, slot, ribbons_before)
    result["slot"] = slot
    status = "PASS" if result.get("ok") else "FAIL"
    print(
        f"  {status} slot={slot} tw={result.get('typewriter_save')} "
        f"rib={result.get('ribbons_before')}->{result.get('ribbons_after')} "
        f"room={result.get('completed_room')}",
        flush=True,
    )
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7800)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument(
        "--slots",
        type=int,
        nargs="+",
        default=[1, 2, 3, 5, 15],
        help="memory-card slots to test (1..15)",
    )
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient

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
    results: list[dict[str, Any]] = []
    try:
        bridge.wait_for_client()
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            frame_skip=1,
            project_root=ROOT,
        )
        for slot in args.slots:
            results.append(_try_slot(env, args.state, int(slot)))

        out_path = ROOT / "data" / "_probe_typewriter_save_all_slots.json"
        summary = {
            "slots_tested": args.slots,
            "passed": sum(1 for r in results if r.get("ok")),
            "failed": sum(1 for r in results if not r.get("ok")),
            "results": results,
        }
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nwrote {out_path}", flush=True)
        print(
            f"SUMMARY: {summary['passed']}/{len(results)} slots passed",
            flush=True,
        )
        return 0 if summary["failed"] == 0 else 1
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        bridge.close()
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
