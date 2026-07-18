"""Live shotgun attack matrix from the newest QuickSave.

Each case reloads the save, equips the real inventory shotgun, fires once, and
records every macro frame's pad, animation hooks, and shotgun ammo count.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import (
    ATTACK_ACTION,
    EQUIP_ACTION,
    SELECT_SLOT_BASE,
)
from re1_rl.attack_macro import execute_attack_macro, read_equipped_weapon
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.item_box import read_inventory
from re1_rl.knife_macro import read_knife_hooks
from re1_rl.sticky_input import STICKY_KEYS, StickyInputState

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
SHOTGUN_ID = 0x03
BERETTA_ID = 0x02


@dataclass(frozen=True)
class Case:
    name: str
    setup_action: str | None = None
    sticky_contamination: str | None = None
    weapon_id: int = SHOTGUN_ID
    equip_via_menu: bool = True


CASES = (
    Case("neutral"),
    Case("after_forward", setup_action="forward"),
    Case("after_back", setup_action="back"),
    Case("after_turn_left", setup_action="turn_left"),
    Case("after_turn_right", setup_action="turn_right"),
    Case("after_run_forward", setup_action="run_forward"),
    Case("after_quickturn", setup_action="quickturn"),
    Case("sticky_up", sticky_contamination="up"),
    Case("sticky_down", sticky_contamination="down"),
    Case("sticky_left", sticky_contamination="left"),
    Case("sticky_right", sticky_contamination="right"),
    Case("sticky_run", sticky_contamination="square"),
    Case("sticky_all", sticky_contamination="all"),
    Case(
        "beretta_neutral_baseline",
        weapon_id=BERETTA_ID,
        equip_via_menu=False,
    ),
)


def newest_quicksave() -> Path:
    saves = sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not saves:
        raise FileNotFoundError(f"no QuickSave states in {STATE_DIR}")
    return saves[0]


def ammo(bridge: BizHawkClient, weapon_id: int) -> int:
    return sum(
        int(qty)
        for item_id, qty in read_inventory(bridge)
        if int(item_id) == int(weapon_id)
    )


def buttons_for_call(kwargs: dict[str, Any]) -> set[str]:
    pressed = {
        key
        for key, value in (kwargs.get("sticky") or {}).items()
        if bool(value)
    }
    for frame in kwargs.get("frame_buttons") or []:
        pressed.update(key for key, value in frame.items() if bool(value))
    return pressed


def summarize_trace(
    trace: list[dict[str, Any]],
    *,
    ammo_before: int,
) -> dict[str, Any]:
    cross = [row["frame"] for row in trace if "cross" in row["buttons"]]
    r1 = [row["frame"] for row in trace if "r1" in row["buttons"]]
    fire_anim = [
        row["frame"]
        for row in trace
        if row["anim"] == 0x14 and row["aux"] == 0x03
    ]
    ammo_drop = next(
        (row["frame"] for row in trace if row["ammo"] < ammo_before),
        None,
    )
    movement_leaks = [
        row
        for row in trace
        if any(key in row["buttons"] for key in STICKY_KEYS)
    ]
    return {
        "frames_traced": len(trace),
        "first_r1_frame": min(r1) if r1 else None,
        "last_r1_frame": max(r1) if r1 else None,
        "first_cross_frame": min(cross) if cross else None,
        "last_cross_frame": max(cross) if cross else None,
        "cross_hold_frames": len(cross),
        "first_fire_anim_frame": min(fire_anim) if fire_anim else None,
        "last_fire_anim_frame": max(fire_anim) if fire_anim else None,
        "fire_anim_frames": len(fire_anim),
        "ammo_drop_frame": ammo_drop,
        "movement_leaks": movement_leaks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7794)
    parser.add_argument("--speed", type=int, default=35)
    parser.add_argument("--pause", type=float, default=0.75)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument(
        "--cases",
        type=str,
        default="",
        help="comma-separated case names; default all",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "shotgun_attack_matrix.jsonl",
    )
    args = parser.parse_args()

    state_path = (args.state or newest_quicksave()).resolve()
    cases = list(CASES)
    if args.cases.strip():
        wanted = {name.strip() for name in args.cases.split(",") if name.strip()}
        cases = [case for case in cases if case.name in wanted]
        missing = wanted - {case.name for case in cases}
        if missing:
            raise SystemExit(f"unknown cases: {sorted(missing)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

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
    original_step = bridge.step
    failures = 0
    rows: list[dict[str, Any]] = []

    try:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        env._ram_skip.use_engine_patches = False
        env.reset()
        action_index = {name: i for i, name in enumerate(ACTION_NAMES)}

        print(
            f"[shotgun-matrix] save={state_path.name} cases={len(cases)} "
            f"speed={args.speed}% log={args.out}",
            flush=True,
        )

        for case in cases:
            bridge.step = original_step  # type: ignore[method-assign]
            bridge.load_savestate(str(state_path))
            bridge.frameadvance(4)
            env._sticky_input = StickyInputState()
            env._prev_state = env._read_state(track_items=False)
            env._prev_hp = int(env._prev_state["hp"])
            env._episode_start_hp = int(env._prev_state["hp"])

            equip: dict[str, Any] = {
                "ok": True,
                "reason": "savestate_equipped",
            }
            if case.equip_via_menu:
                env.step(EQUIP_ACTION)
                _o, _r, _t, _tr, equip_info = env.step(
                    SELECT_SLOT_BASE + 4
                )
                equip = dict(equip_info.get("magic_report") or {})
            equipped = read_equipped_weapon(bridge)
            ammo_before = ammo(bridge, case.weapon_id)
            if not equip.get("ok") or equipped != case.weapon_id:
                raise RuntimeError(
                    f"{case.name}: equip failed report={equip} equipped=0x{equipped:02X}"
                )

            if case.setup_action is not None:
                env.step(action_index[case.setup_action])

            sticky = env._sticky_input.as_dict()
            if case.sticky_contamination == "all":
                sticky.update({key: True for key in STICKY_KEYS})
            elif case.sticky_contamination is not None:
                sticky[case.sticky_contamination] = True

            mask_legal = bool(env.action_masks()[ATTACK_ACTION])
            trace: list[dict[str, Any]] = []

            def tracing_step(*step_args: Any, **step_kwargs: Any) -> Any:
                result = original_step(*step_args, **step_kwargs)
                anim, aux, recovery = read_knife_hooks(bridge)
                trace.append(
                    {
                        "frame": len(trace) + 1,
                        "buttons": sorted(buttons_for_call(step_kwargs)),
                        "anim": int(anim),
                        "aux": int(aux),
                        "recovery": int(recovery),
                        "ammo": ammo(bridge, case.weapon_id),
                    }
                )
                return result

            bridge.step = tracing_step  # type: ignore[method-assign]
            died, macro_frames, report = execute_attack_macro(
                bridge,
                empty_sticky=sticky,
                prev_hp=int(env._prev_hp),
                episode_start_hp=int(env._episode_start_hp),
            )
            bridge.step = original_step  # type: ignore[method-assign]
            ammo_after = ammo(bridge, case.weapon_id)
            timing = summarize_trace(trace, ammo_before=ammo_before)

            errors: list[str] = []
            if died:
                errors.append("death")
            if report.get("outcome") != "ok":
                errors.append(f"outcome={report.get('outcome')}")
            if ammo_before - ammo_after != 1:
                errors.append(f"ammo={ammo_before}->{ammo_after}")
            if not report.get("saw_fire_anim"):
                errors.append("no_fire_anim")
            if timing["movement_leaks"]:
                errors.append("movement_input_leaked_into_macro")
            if timing["cross_hold_frames"] < 1:
                errors.append("cross_never_pressed")
            if timing["ammo_drop_frame"] is None:
                errors.append("ammo_never_dropped")

            row = {
                "case": case.name,
                "weapon": report.get("weapon"),
                "weapon_id": f"0x{case.weapon_id:02X}",
                "setup_action": case.setup_action,
                "sticky_contamination": case.sticky_contamination,
                "attack_mask_legal": mask_legal,
                "equipped": f"0x{equipped:02X}",
                "equip_report": equip,
                "ammo_before": ammo_before,
                "ammo_after": ammo_after,
                "macro_frames": int(macro_frames),
                "report": report,
                "timing": timing,
                "trace": trace,
                "errors": errors,
                "passed": not errors,
            }
            rows.append(row)
            with args.out.open("a", encoding="utf-8") as output:
                output.write(json.dumps(row) + "\n")

            mark = "PASS" if not errors else "FAIL"
            print(
                f"[shotgun-matrix] {mark} {case.name:<25} "
                f"weapon={report.get('weapon')} ammo={ammo_before}->{ammo_after} "
                f"frames={macro_frames} cross={timing['first_cross_frame']}-"
                f"{timing['last_cross_frame']} drop={timing['ammo_drop_frame']} "
                f"fire={timing['first_fire_anim_frame']}-"
                f"{timing['last_fire_anim_frame']} mask={mask_legal}",
                flush=True,
            )
            if errors:
                failures += 1
                print(f"[shotgun-matrix]   errors={errors}", flush=True)
            time.sleep(float(args.pause))

        if {"neutral", "beretta_neutral_baseline"} <= {
            row["case"] for row in rows
        }:
            shotgun_neutral = next(
                row for row in rows if row["case"] == "neutral"
            )
            beretta_neutral = next(
                row for row in rows
                if row["case"] == "beretta_neutral_baseline"
            )
            compare_keys = (
                "first_cross_frame",
                "last_cross_frame",
                "ammo_drop_frame",
                "first_fire_anim_frame",
                "last_fire_anim_frame",
                "fire_anim_frames",
            )
            differences = {
                key: (
                    shotgun_neutral["timing"][key],
                    beretta_neutral["timing"][key],
                )
                for key in compare_keys
                if shotgun_neutral["timing"][key]
                != beretta_neutral["timing"][key]
            }
            print(
                "[shotgun-matrix] timing_compare "
                f"shotgun_vs_beretta={differences or 'same'}",
                flush=True,
            )
        print(
            f"[shotgun-matrix] done passed={len(rows) - failures}/{len(rows)} "
            f"log={args.out}",
            flush=True,
        )
        return 0 if failures == 0 else 1
    finally:
        bridge.step = original_step  # type: ignore[method-assign]
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
