"""Normal-speed knife swing watcher: BizHawk + live per-frame input dump.

Watch the EmuHawk window at 100%. Terminal prints every emulated frame's
buttons and anim hooks for the production RAM-gated crouch knife macro.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_swing_watch.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_swing_watch.py --swings 5 --pause 4
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch knife swing inputs at normal speed")
    ap.add_argument("--port", type=int, default=5777)
    ap.add_argument("--speed", type=int, default=100, help="BizHawk speed %% (100=normal)")
    ap.add_argument("--pause", type=float, default=3.0, help="seconds between swings")
    ap.add_argument("--swings", type=int, default=0, help="0 = forever")
    ap.add_argument("--noops", type=int, default=4)
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env
    from re1_rl.knife_macro import (
        AIM_BUTTONS,
        SWING_BUTTONS,
        classify_knife_anim,
        format_knife_hooks,
        read_knife_hooks,
    )

    print(
        "[knife_watch] Production RAM-gated crouch knife phases:\n"
        f"  settle : all buttons OFF (neutral) until idle / standing ready\n"
        f"  aim    : {_buttons_label(AIM_BUTTONS)}  until crouch_aim 0x12/0x04/0\n"
        f"  swing  : {_buttons_label(SWING_BUTTONS)}  min ~10 emu frames\n"
        f"  recover: {_buttons_label(AIM_BUTTONS)}  until idle / recovery tail\n"
        "  abort  : release ALL if foreign/hurt anim appears\n",
        flush=True,
    )

    port = int(args.port)
    bridge = BizHawkClient(
        port=port,
        timeout=300.0,
        screenshot_path=str(ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    print(
        f"[knife_watch] launching EmuHawk port={port} speed={args.speed}% "
        "(watch this window)",
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

    def tracing_step(*a: Any, **kw: Any) -> Any:
        frame_buttons = kw.get("frame_buttons") or []
        sticky = kw.get("sticky") or {}
        result = orig_step(*a, **kw)
        # One call may advance many frames; log each scheduled pad state.
        if frame_buttons:
            for btn in frame_buttons:
                merged = {k: bool(v) for k, v in sticky.items() if v}
                merged.update({k: bool(v) for k, v in btn.items() if v})
                anim, aux, rec = read_knife_hooks(bridge)
                label = classify_knife_anim(anim, aux, rec)
                line = (
                    f"    f{len(trace) + 1:03d}: {_buttons_label(merged):<20} "
                    f"{label:<22} {format_knife_hooks(anim, aux, rec)}"
                )
                trace.append(line)
                print(line, flush=True)
        else:
            anim, aux, rec = read_knife_hooks(bridge)
            label = classify_knife_anim(anim, aux, rec)
            line = (
                f"    f{len(trace) + 1:03d}: {_buttons_label(dict(sticky)):<20} "
                f"{label:<22} {format_knife_hooks(anim, aux, rec)}"
            )
            trace.append(line)
            print(line, flush=True)
        return result

    def shutdown(*_: object) -> None:
        print("\n[knife_watch] stopping...", flush=True)
        bridge.step = orig_step  # type: ignore[method-assign]
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

    env = RE1Env(
        curriculum_path=ROOT / "curriculum" / "m0_dining_to_main_hall.json",
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=False,
    )
    env._ram_skip.use_engine_patches = False
    env.knife_use_ram_gates = True
    env.knife_echo_joypad = False

    bridge.wait_for_client()
    bridge.set_speed(int(args.speed))
    env.reset()

    noop = ACTION_NAMES.index("noop")
    knife = ACTION_NAMES.index("knife_swing")
    print(
        f"[knife_watch] ready — {args.noops} noops then knife_swing at "
        f"{args.speed}%, pause {args.pause}s. Ctrl+C to quit.\n",
        flush=True,
    )

    swing = 0
    while True:
        bridge.step = orig_step  # type: ignore[method-assign]
        for _ in range(int(args.noops)):
            env.step(noop)
        pre = read_knife_hooks(bridge)
        swing += 1
        print(
            f"======== swing #{swing}  pre={format_knife_hooks(*pre)} ========",
            flush=True,
        )
        print(
            "    frame  buttons              anim_label             hooks",
            flush=True,
        )
        trace.clear()
        bridge.step = tracing_step  # type: ignore[method-assign]
        _, _, _, _, info = env.step(knife)
        bridge.step = orig_step  # type: ignore[method-assign]
        report = info.get("knife_anim_report") or getattr(
            bridge, "last_knife_anim_report", None
        )
        if report:
            print(
                f"  >> outcome={report.get('outcome')} ok={report.get('ok')} "
                f"macro_frames={report.get('macro_frames')} "
                f"crouch_aim={report.get('crouch_aim')} "
                f"swing_anim={report.get('swing_anim')}",
                flush=True,
            )
            for iss in (report.get("issues") or [])[:6]:
                print(f"  >> issue: {iss}", flush=True)
        post = read_knife_hooks(bridge)
        print(f"  >> post={format_knife_hooks(*post)}\n", flush=True)
        if int(args.swings) > 0 and swing >= int(args.swings):
            print(f"[knife_watch] done after {swing} swings", flush=True)
            shutdown()
        time.sleep(float(args.pause))


if __name__ == "__main__":
    raise SystemExit(main())
