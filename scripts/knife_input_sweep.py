"""Exhaustive knife input sweep: crouch + standing stances.

For each stance, systematically:
  - baseline production button schedule
  - ablate each required button
  - contaminate aim / swing / both with every other pad button
  - sticky leftover directions/run during the schedule

Success = saw swing_recovery (0x13) and/or standing_knife (0x14).
Crouch also requires crouch_aim (0x12/0x04) for a full pass.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_input_sweep.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_input_sweep.py --port 5798 --speed 200
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
OUT_JSON = ROOT / "data" / "knife_input_sweep.json"

# Every friendly button the Lua BUTTON_MAP accepts.
ALL_PAD: tuple[str, ...] = (
    "up",
    "down",
    "left",
    "right",
    "cross",
    "circle",
    "square",
    "triangle",
    "r1",
    "l1",
    "r2",
    "l2",
    "start",
    "select",
)

CROUCH_AIM = {"r1": True, "down": True}
CROUCH_SWING = {"r1": True, "down": True, "cross": True}
STAND_AIM = {"r1": True}
STAND_SWING = {"r1": True, "cross": True}

AIM_EMU = 10  # 5 game frames * 2
SWING_EMU = 10
RECOVERY_EMU = 22


def _merge(base: dict[str, bool], *extras: dict[str, bool]) -> dict[str, bool]:
    out = dict(base)
    for e in extras:
        for k, v in e.items():
            if v:
                out[k] = True
            elif k in out and not v:
                out.pop(k, None)
    return out


def _drop(base: dict[str, bool], key: str) -> dict[str, bool]:
    out = dict(base)
    out.pop(key, None)
    return out


def _schedule(
    aim: dict[str, bool],
    swing: dict[str, bool],
    recovery: dict[str, bool] | None = None,
    *,
    aim_n: int = AIM_EMU,
    swing_n: int = SWING_EMU,
    recovery_n: int = RECOVERY_EMU,
) -> list[dict[str, bool]]:
    rec = recovery if recovery is not None else aim
    return [dict(aim)] * aim_n + [dict(swing)] * swing_n + [dict(rec)] * recovery_n


def _trial_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    # --- Crouch ---
    cases.append(
        {
            "stance": "crouch",
            "kind": "baseline",
            "label": "crouch_baseline",
            "schedule": _schedule(CROUCH_AIM, CROUCH_SWING),
            "sticky": {},
        }
    )
    for btn in ("r1", "down"):
        cases.append(
            {
                "stance": "crouch",
                "kind": "ablate_aim",
                "label": f"crouch_aim_drop_{btn}",
                "schedule": _schedule(_drop(CROUCH_AIM, btn), CROUCH_SWING),
                "sticky": {},
            }
        )
    for btn in ("r1", "down", "cross"):
        cases.append(
            {
                "stance": "crouch",
                "kind": "ablate_swing",
                "label": f"crouch_swing_drop_{btn}",
                "schedule": _schedule(CROUCH_AIM, _drop(CROUCH_SWING, btn)),
                "sticky": {},
            }
        )
    for btn in ALL_PAD:
        if btn in CROUCH_AIM:
            continue
        cases.append(
            {
                "stance": "crouch",
                "kind": "contam_aim",
                "label": f"crouch_aim_plus_{btn}",
                "schedule": _schedule(
                    _merge(CROUCH_AIM, {btn: True}), CROUCH_SWING
                ),
                "sticky": {},
            }
        )
    for btn in ALL_PAD:
        if btn in CROUCH_SWING:
            continue
        cases.append(
            {
                "stance": "crouch",
                "kind": "contam_swing",
                "label": f"crouch_swing_plus_{btn}",
                "schedule": _schedule(
                    CROUCH_AIM, _merge(CROUCH_SWING, {btn: True})
                ),
                "sticky": {},
            }
        )
    for btn in ALL_PAD:
        if btn in CROUCH_SWING:
            continue
        cases.append(
            {
                "stance": "crouch",
                "kind": "contam_both",
                "label": f"crouch_both_plus_{btn}",
                "schedule": _schedule(
                    _merge(CROUCH_AIM, {btn: True}),
                    _merge(CROUCH_SWING, {btn: True}),
                ),
                "sticky": {},
            }
        )
    for sticky_btn in ("up", "left", "right", "square"):
        cases.append(
            {
                "stance": "crouch",
                "kind": "sticky",
                "label": f"crouch_sticky_{sticky_btn}",
                "schedule": _schedule(CROUCH_AIM, CROUCH_SWING),
                "sticky": {sticky_btn: True},
            }
        )

    # --- Standing ---
    cases.append(
        {
            "stance": "standing",
            "kind": "baseline",
            "label": "standing_baseline",
            "schedule": _schedule(STAND_AIM, STAND_SWING),
            "sticky": {},
        }
    )
    cases.append(
        {
            "stance": "standing",
            "kind": "ablate_aim",
            "label": "standing_aim_drop_r1",
            "schedule": _schedule(_drop(STAND_AIM, "r1"), STAND_SWING),
            "sticky": {},
        }
    )
    for btn in ("r1", "cross"):
        cases.append(
            {
                "stance": "standing",
                "kind": "ablate_swing",
                "label": f"standing_swing_drop_{btn}",
                "schedule": _schedule(STAND_AIM, _drop(STAND_SWING, btn)),
                "sticky": {},
            }
        )
    for btn in ALL_PAD:
        if btn in STAND_AIM:
            continue
        cases.append(
            {
                "stance": "standing",
                "kind": "contam_aim",
                "label": f"standing_aim_plus_{btn}",
                "schedule": _schedule(
                    _merge(STAND_AIM, {btn: True}), STAND_SWING
                ),
                "sticky": {},
            }
        )
    for btn in ALL_PAD:
        if btn in STAND_SWING:
            continue
        cases.append(
            {
                "stance": "standing",
                "kind": "contam_swing",
                "label": f"standing_swing_plus_{btn}",
                "schedule": _schedule(
                    STAND_AIM, _merge(STAND_SWING, {btn: True})
                ),
                "sticky": {},
            }
        )
    for btn in ALL_PAD:
        if btn in STAND_SWING:
            continue
        cases.append(
            {
                "stance": "standing",
                "kind": "contam_both",
                "label": f"standing_both_plus_{btn}",
                "schedule": _schedule(
                    _merge(STAND_AIM, {btn: True}),
                    _merge(STAND_SWING, {btn: True}),
                ),
                "sticky": {},
            }
        )
    for sticky_btn in ("up", "down", "left", "right", "square"):
        cases.append(
            {
                "stance": "standing",
                "kind": "sticky",
                "label": f"standing_sticky_{sticky_btn}",
                "schedule": _schedule(STAND_AIM, STAND_SWING),
                "sticky": {sticky_btn: True},
            }
        )

    return cases


def _score(summary: dict[str, Any], stance: str) -> dict[str, Any]:
    swung = bool(
        summary.get("saw_swing_recovery") or summary.get("saw_standing_knife")
    )
    aimed = bool(summary.get("saw_crouch_aim")) if stance == "crouch" else True
    ok = swung and aimed
    return {
        "ok": ok,
        "swung": swung,
        "aimed": aimed,
        "labels": summary.get("labels_seen", []),
        "hooks": summary.get("hook_pair_counts", {}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Exhaustive knife input sweep")
    ap.add_argument("--port", type=int, default=5798)
    ap.add_argument("--speed", type=int, default=200)
    ap.add_argument("--out", type=Path, default=OUT_JSON)
    ap.add_argument(
        "--kinds",
        type=str,
        default="",
        help="comma filter of kinds (baseline,ablate_aim,...); default all",
    )
    ap.add_argument(
        "--stances",
        type=str,
        default="crouch,standing",
        help="comma filter of stances",
    )
    ap.add_argument(
        "--consecutive",
        type=int,
        default=4,
        help="also run N consecutive production RAM-gated crouch swings",
    )
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env
    from re1_rl.knife_macro import (
        execute_knife_macro,
        summarize_knife_trace,
        trace_knife_button_schedule,
    )
    from re1_rl.sticky_input import StickyInputState

    wanted_kinds = {
        k.strip() for k in args.kinds.split(",") if k.strip()
    } or None
    wanted_stances = {s.strip() for s in args.stances.split(",") if s.strip()}

    cases = [
        c
        for c in _trial_cases()
        if c["stance"] in wanted_stances
        and (wanted_kinds is None or c["kind"] in wanted_kinds)
    ]

    port = int(args.port)
    bridge = BizHawkClient(
        port=port,
        timeout=300.0,
        screenshot_path=str(ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    print(
        f"[knife_sweep] launching EmuHawk port={port} cases={len(cases)}",
        flush=True,
    )
    proc = subprocess.Popen(
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

    env: RE1Env | None = None

    def shutdown(code: int) -> None:
        try:
            if env is not None:
                env.close()
        except Exception:
            pass
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        raise SystemExit(code)

    signal.signal(signal.SIGINT, lambda *_: shutdown(130))

    env = RE1Env(
        curriculum_path=ROOT / "curriculum" / "m0_dining_to_main_hall.json",
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=False,
    )
    env._ram_skip.use_engine_patches = False
    env.knife_use_ram_gates = True

    bridge.wait_for_client()
    bridge.set_speed(int(args.speed))
    env.reset()
    savestate = str(ROOT / env._stage["init_savestate"])
    empty = {k: False for k in ("up", "down", "left", "right", "square")}

    results: list[dict[str, Any]] = []
    fails: list[dict[str, Any]] = []

    for i, case in enumerate(cases):
        bridge.load_savestate(savestate)
        bridge.frameadvance(4)
        env._sticky_input = StickyInputState()
        sticky = dict(empty)
        sticky.update(case["sticky"])
        # Warm sticky into Lua latch by stepping neutral with sticky set.
        if any(sticky.values()):
            bridge.step(n=2, sticky=sticky, frame_buttons=[{}])
        frames = trace_knife_button_schedule(
            bridge,
            case["schedule"],
            empty_sticky=sticky,
            warmup_frames=2,
            tail_frames=8,
        )
        summary = summarize_knife_trace(frames)
        score = _score(summary, case["stance"])
        row = {
            "i": i,
            "label": case["label"],
            "stance": case["stance"],
            "kind": case["kind"],
            "sticky": case["sticky"],
            **score,
        }
        results.append(row)
        mark = "OK" if score["ok"] else "FAIL"
        print(
            f"[knife_sweep] {mark} {case['label']:<36} "
            f"aimed={int(score['aimed'])} swung={int(score['swung'])} "
            f"labels={score['labels']}",
            flush=True,
        )
        if not score["ok"]:
            fails.append(row)

    consecutive: list[dict[str, Any]] = []
    if int(args.consecutive) > 0 and "crouch" in wanted_stances:
        bridge.load_savestate(savestate)
        bridge.frameadvance(4)
        env._sticky_input = StickyInputState()
        for n in range(int(args.consecutive)):
            died, frames_n = execute_knife_macro(
                bridge,
                empty_sticky=empty,
                use_ram_gates=True,
                prev_hp=96,
                episode_start_hp=96,
            )
            report = getattr(bridge, "last_knife_anim_report", {}) or {}
            row = {
                "swing": n + 1,
                "died": bool(died),
                "frames": frames_n,
                "outcome": report.get("outcome"),
                "ok": bool(report.get("ok")),
                "crouch_aim": report.get("crouch_aim"),
                "swing_anim": report.get("swing_anim"),
                "issues": report.get("issues", []),
                "pre": (report.get("pre_state") or {}).get("hooks"),
            }
            consecutive.append(row)
            mark = "OK" if row["outcome"] == "ok" else "FAIL"
            print(
                f"[knife_sweep] consecutive {mark} #{n + 1} "
                f"outcome={row['outcome']} pre={row['pre']} "
                f"aim={row['crouch_aim']} swing={row['swing_anim']}",
                flush=True,
            )

    # Aggregate killers
    killers: dict[str, list[str]] = {}
    for row in fails:
        key = f"{row['stance']}/{row['kind']}"
        killers.setdefault(key, []).append(row["label"])

    payload = {
        "n_cases": len(results),
        "n_ok": sum(1 for r in results if r["ok"]),
        "n_fail": len(fails),
        "killers_by_kind": killers,
        "fails": fails,
        "results": results,
        "consecutive_crouch_ram_gated": consecutive,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"[knife_sweep] done ok={payload['n_ok']}/{payload['n_cases']} "
        f"fails={payload['n_fail']} -> {args.out}",
        flush=True,
    )
    if killers:
        print("[knife_sweep] FAIL clusters:", flush=True)
        for k, labels in sorted(killers.items()):
            print(f"  {k}: {len(labels)}  e.g. {labels[:6]}", flush=True)
    shutdown(0 if payload["n_fail"] == 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
