"""Half-speed beretta attack watcher: live per-frame anim hooks.

Equips beretta, optionally faces south (down on pad), then runs the
production standing ``attack`` macro while printing every emulated frame's
buttons + anim/aux/recovery so you can watch the EmuHawk window.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\beretta_attack_watch.py --speed 50
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\beretta_attack_watch.py --speed 50 --face-down --shots 5
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\beretta_attack_watch.py --speed 50 --hold-down
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"


def _buttons_label(btn: dict[str, bool] | None) -> str:
    if not btn:
        return "(neutral)"
    held = sorted(k for k, v in btn.items() if v)
    return "+".join(held) if held else "(neutral)"


def _gun_label(anim: int, aux: int, recovery: int) -> str:
    from re1_rl.attack_macro import (
        AIM_ANIM_RAISING,
        AIM_ANIM_STABLE,
        FIRE_ANIM,
        GUN_AUX_TRACK,
        is_gun_aim_stable,
    )
    from re1_rl.knife_macro import classify_knife_anim

    if is_gun_aim_stable(anim, aux, recovery):
        return "gun_aim_stable"
    if anim == AIM_ANIM_RAISING and aux == GUN_AUX_TRACK:
        return "gun_aim_raising"
    if anim == FIRE_ANIM and aux == GUN_AUX_TRACK:
        return "gun_fire"
    if anim == 0x15 and aux == GUN_AUX_TRACK:
        return "gun_post_fire"
    if anim == 0x17 and aux == GUN_AUX_TRACK:
        return "gun_recover"
    return classify_knife_anim(anim, aux, recovery)


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch beretta attack macro + anim frames")
    ap.add_argument("--port", type=int, default=7789)
    ap.add_argument("--speed", type=int, default=50, help="BizHawk speed %% (50=half)")
    ap.add_argument("--pause", type=float, default=2.5, help="seconds between shots")
    ap.add_argument("--shots", type=int, default=0, help="0 = forever")
    ap.add_argument("--noops", type=int, default=2)
    ap.add_argument(
        "--face-down",
        action="store_true",
        help="hold down briefly before each shot so Jill faces south",
    )
    ap.add_argument(
        "--face-frames",
        type=int,
        default=18,
        help="emulated frames of down held when --face-down",
    )
    ap.add_argument(
        "--hold-down",
        action="store_true",
        help="force Down into aim/fire buttons (wrong crouch pad — repro fail path)",
    )
    ap.add_argument("--run-first", type=int, default=0, help="run_forward steps before loop")
    args = ap.parse_args()

    from re1_rl.action_mask import ATTACK_ACTION
    from re1_rl.attack_macro import AIM_BUTTONS, FIRE_BUTTONS
    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env
    from re1_rl.knife_macro import format_knife_hooks, read_knife_hooks
    from re1_rl.weapon_equip import magic_equip

    port = int(args.port)
    bridge = BizHawkClient(
        port=port,
        timeout=300.0,
        screenshot_path=str(ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    print(
        f"[beretta_watch] launching EmuHawk port={port} speed={args.speed}% "
        f"(watch this window) face_down={args.face_down} hold_down={args.hold_down}",
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
    )

    env: RE1Env | None = None
    trace: list[str] = []
    orig_step = bridge.step
    orig_aim = dict(AIM_BUTTONS)
    orig_fire = dict(FIRE_BUTTONS)

    def tracing_step(*a: Any, **kw: Any) -> Any:
        frame_buttons = kw.get("frame_buttons") or []
        sticky = kw.get("sticky") or {}
        result = orig_step(*a, **kw)
        if frame_buttons:
            for btn in frame_buttons:
                merged = {k: bool(v) for k, v in sticky.items() if v}
                merged.update({k: bool(v) for k, v in btn.items() if v})
                anim, aux, rec = read_knife_hooks(bridge)
                label = _gun_label(anim, aux, rec)
                line = (
                    f"    f{len(trace) + 1:03d}: {_buttons_label(merged):<22} "
                    f"{label:<22} {format_knife_hooks(anim, aux, rec)}"
                )
                trace.append(line)
                print(line, flush=True)
        else:
            anim, aux, rec = read_knife_hooks(bridge)
            label = _gun_label(anim, aux, rec)
            sticky_d = {k: bool(v) for k, v in sticky.items() if v}
            # _step_one_frame passes buttons via sticky / single-frame path
            buttons = kw.get("buttons")
            if isinstance(buttons, dict) and buttons:
                sticky_d = {k: bool(v) for k, v in buttons.items() if v}
            line = (
                f"    f{len(trace) + 1:03d}: {_buttons_label(sticky_d):<22} "
                f"{label:<22} {format_knife_hooks(anim, aux, rec)}"
            )
            trace.append(line)
            print(line, flush=True)
        return result

    def shutdown(*_: object) -> None:
        print("\n[beretta_watch] stopping...", flush=True)
        bridge.step = orig_step  # type: ignore[method-assign]
        AIM_BUTTONS.clear()
        AIM_BUTTONS.update(orig_aim)
        FIRE_BUTTONS.clear()
        FIRE_BUTTONS.update(orig_fire)
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
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    if args.hold_down:
        AIM_BUTTONS.clear()
        AIM_BUTTONS.update({"r1": True, "down": True})
        FIRE_BUTTONS.clear()
        FIRE_BUTTONS.update({"r1": True, "down": True, "cross": True})
        print(
            "[beretta_watch] HOLD-DOWN pad injected into ranged AIM/FIRE "
            f"(aim={_buttons_label(AIM_BUTTONS)} fire={_buttons_label(FIRE_BUTTONS)})",
            flush=True,
        )

    env = RE1Env(
        curriculum_path=ROOT / "curriculum" / "m0_dining_to_main_hall.json",
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=False,
    )
    env._ram_skip.use_engine_patches = False

    bridge.wait_for_client()
    bridge.set_speed(int(args.speed))
    env.reset()

    eq = magic_equip(bridge, 0x02)
    bridge.frameadvance(4)
    print(f"[beretta_watch] equipped beretta via magic_equip -> {eq}", flush=True)

    noop = ACTION_NAMES.index("noop")
    back = ACTION_NAMES.index("back")
    run_fwd = ACTION_NAMES.index("run_forward")

    for _ in range(int(args.run_first)):
        env.step(run_fwd)

    print(
        f"[beretta_watch] ready — attack at {args.speed}%, pause {args.pause}s. "
        "Ctrl+C to quit.\n",
        flush=True,
    )

    shot = 0
    while True:
        bridge.step = orig_step  # type: ignore[method-assign]
        for _ in range(int(args.noops)):
            env.step(noop)
        if args.face_down:
            print(
                f"  -- facing down ({args.face_frames} frames of 'back') --",
                flush=True,
            )
            for _ in range(int(args.face_frames)):
                env.step(back)

        pre = read_knife_hooks(bridge)
        shot += 1
        print(
            f"======== shot #{shot}  pre={format_knife_hooks(*pre)} "
            f"label={_gun_label(*pre)} ========",
            flush=True,
        )
        print(
            "    frame  buttons                anim_label             hooks",
            flush=True,
        )
        trace.clear()
        bridge.step = tracing_step  # type: ignore[method-assign]
        _, _, _, _, info = env.step(ATTACK_ACTION)
        bridge.step = orig_step  # type: ignore[method-assign]
        report = info.get("attack_report") or {}
        print(
            f"  >> outcome={report.get('outcome')} weapon={report.get('weapon')} "
            f"ammo_spent={report.get('ammo_spent')} frames={report.get('frames')} "
            f"saw_fire={report.get('saw_fire_anim')} path={report.get('macro_path')}",
            flush=True,
        )
        for line in (report.get("trail") or [])[-8:]:
            print(f"  >> trail {line}", flush=True)
        post = read_knife_hooks(bridge)
        print(
            f"  >> post={format_knife_hooks(*post)} label={_gun_label(*post)}\n",
            flush=True,
        )
        if int(args.shots) > 0 and shot >= int(args.shots):
            print(f"[beretta_watch] done after {shot} shots", flush=True)
            print("[beretta_watch] leaving EmuHawk open — Ctrl+C to quit", flush=True)
            while True:
                time.sleep(3600)
        time.sleep(float(args.pause))


if __name__ == "__main__":
    raise SystemExit(main())
