"""Single-agent knife swing repeater for visual QA.

One EmuHawk window, deterministic noop→knife loop. No fleet, no PPO.

Verifies input delivery: every swing reads back joypad.get() per frame from
Lua and diffs it against the scheduled macro (does not depend on enemy
damage — do NOT use knife_swing_missed for input QA).

Usage:
    python scripts/knife_solo_demo.py
    python scripts/knife_solo_demo.py --speed 100 --no-turbo-patches
    python scripts/knife_solo_demo.py --aim 5 --swing 5 --recovery 11 --scale 2
    python scripts/knife_solo_demo.py --swings 5   # bounded run, exits with status
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"
DEFAULT_PORT = 5777
DEFAULT_CURRICULUM = "curriculum/m0_dining_to_main_hall.json"


def expected_echo(frames: list[dict[str, bool]]) -> list[str]:
    return ["+".join(sorted(k for k, v in f.items() if v)) for f in frames]


def main() -> int:
    ap = argparse.ArgumentParser(description="Single EmuHawk knife swing demo")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--speed", type=int, default=100, help="BizHawk speed %% (100=normal)")
    ap.add_argument(
        "--turbo-patches",
        action="store_true",
        help="enable in-engine RAM turbo patches (off by default for clean QA)",
    )
    ap.add_argument(
        "--fixed-schedule",
        action="store_true",
        help="blind 42-frame macro + joypad echo QA (default: RAM-gated crouch aim)",
    )
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between knife swings")
    ap.add_argument("--noops", type=int, default=4, help="noop env steps before each swing")
    ap.add_argument("--swings", type=int, default=0, help="exit after N swings (0 = forever)")
    ap.add_argument("--aim", type=int, default=None, help="aim phase, game frames")
    ap.add_argument("--swing", type=int, default=None, help="swing phase, game frames")
    ap.add_argument("--recovery", type=int, default=None, help="recovery phase, game frames")
    ap.add_argument(
        "--scale", type=int, default=None,
        help="emulated frames per game frame (default 2: 30fps game logic)",
    )
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env
    from re1_rl.knife_macro import build_knife_frame_buttons

    phase_overrides = {}
    if args.aim is not None:
        phase_overrides["aim"] = int(args.aim)
    if args.swing is not None:
        phase_overrides["swing"] = int(args.swing)
    if args.recovery is not None:
        phase_overrides["recovery"] = int(args.recovery)
    if args.scale is not None:
        phase_overrides["scale"] = int(args.scale)
    schedule = build_knife_frame_buttons(**phase_overrides)
    want = expected_echo(schedule)

    port = int(args.port)
    shot = str(PROJECT_ROOT / "data" / f"_frame_{port}.png")
    bridge = BizHawkClient(port=port, timeout=300.0, screenshot_path=shot)
    bridge.start_server()

    print(
        f"[knife_solo] spawning EmuHawk port {port}, speed={args.speed}%, "
        f"turbo_patches={args.turbo_patches}, macro={len(schedule)} emu frames",
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
    bridge.wait_for_client()
    bridge.set_speed(int(args.speed))

    env = RE1Env(
        curriculum_path=PROJECT_ROOT / DEFAULT_CURRICULUM,
        bridge=bridge,
        project_root=PROJECT_ROOT,
        async_cutscene_skip=False,
    )
    env._ram_skip.use_engine_patches = bool(args.turbo_patches)
    env._ram_skip.training_speed = int(args.speed)
    env._ram_skip.cutscene_speed = int(args.speed)
    env.knife_use_ram_gates = not args.fixed_schedule
    env.knife_echo_joypad = bool(args.fixed_schedule)
    from re1_rl import knife_macro as km

    if any(k in phase_overrides for k in ("aim", "swing", "recovery")):
        env.knife_phases = (
            phase_overrides.get("aim", km.KNIFE_AIM_GAME_FRAMES),
            phase_overrides.get("swing", km.KNIFE_SWING_GAME_FRAMES),
            phase_overrides.get("recovery", km.KNIFE_RECOVERY_GAME_FRAMES),
        )
    if "scale" in phase_overrides:
        env.knife_scale = phase_overrides["scale"]

    noop = ACTION_NAMES.index("noop")
    knife = ACTION_NAMES.index("knife_swing")

    def shutdown(*_: object) -> None:
        print("\n[knife_solo] stopping...", flush=True)
        try:
            env.close()
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

    orig_close = env.close

    def close_with_emu() -> None:
        try:
            orig_close()
        finally:
            try:
                proc.terminate()
            except OSError:
                pass

    env.close = close_with_emu  # type: ignore[method-assign]

    env.reset()
    print(
        f"[knife_solo] ready — {args.noops} noops, knife_swing "
        f"({len(schedule)}-emu-frame macro), pause {args.pause}s. Ctrl+C to quit.",
        flush=True,
    )

    swing = 0
    bad_swings = 0
    while True:
        for _ in range(int(args.noops)):
            env.step(noop)
        _, _, _, _, info = env.step(knife)
        swing += 1
        st = info.get("state", {})
        echo = bridge.last_step_echo
        if echo is None:
            verdict = "echo=MISSING"
        elif len(echo) != len(want):
            verdict = f"echo=SHORT ({len(echo)}/{len(want)} frames)"
            bad_swings += 1
        else:
            diffs = [
                f"f{i + 1}: want[{w}] got[{g}]"
                for i, (w, g) in enumerate(zip(want, echo))
                if w != g
            ]
            if diffs:
                verdict = f"echo=MISMATCH ({len(diffs)} frames)"
                bad_swings += 1
            else:
                verdict = "echo=OK all frames delivered"
        print(
            f"[knife_solo] swing #{swing} {verdict} hp={st.get('hp')}",
            flush=True,
        )
        if echo is not None and verdict.startswith("echo=MISMATCH"):
            for d in diffs[:8]:
                print(f"[knife_solo]   {d}", flush=True)
        if int(args.swings) > 0 and swing >= int(args.swings):
            print(
                f"[knife_solo] done: {swing} swings, {bad_swings} with input loss",
                flush=True,
            )
            shutdown()
        time.sleep(float(args.pause))


if __name__ == "__main__":
    raise SystemExit(main())
