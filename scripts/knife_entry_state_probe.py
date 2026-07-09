"""Probe which pre-button RAM states accept crouch-knife aim inputs.

Induces common fleet pre-states (idle, after run, after turn, weapon-ready),
then tries aim entry with simultaneous R1+down vs R1-first preamble.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_entry_state_probe.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_entry_state_probe.py --port 5796
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
OUT_JSON = ROOT / "data" / "knife_entry_probe.json"

SCENARIOS: list[dict] = [
    {"name": "neutral_idle", "setup": [("noop", 24)]},
    {"name": "after_run", "setup": [("run_forward", 8), ("noop", 16)]},
    {"name": "after_walk", "setup": [("forward", 8), ("noop", 16)]},
    {"name": "after_turn_left", "setup": [("turn_left", 6), ("noop", 16)]},
    {"name": "after_turn_right", "setup": [("turn_right", 6), ("noop", 16)]},
    {"name": "after_quickturn", "setup": [("quickturn", 1), ("noop", 20)]},
    {"name": "r1_weapon_ready", "setup": [("_r1_pulse", 8), ("noop", 4)]},
    {"name": "standing_idle_hold_aim", "setup": [("_r1_pulse", 20)]},
    {"name": "standing_idle_aim_release", "setup": [("_r1_pulse", 12), ("noop", 2)]},
]

STRATEGIES = ("simultaneous", "r1_first")


def _run_setup(env, action_index: dict[str, int], setup: list[tuple[str, int]]) -> None:
    for action_name, count in setup:
        if action_name == "_r1_pulse":
            for _ in range(int(count)):
                env.bridge.step(n=1, sticky={}, pulse={"r1": True})
            continue
        action = action_index[action_name]
        for _ in range(int(count)):
            env.step(action)


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-state crouch aim entry probe")
    ap.add_argument("--port", type=int, default=5796)
    ap.add_argument("--speed", type=int, default=200)
    ap.add_argument("--r1-preamble", type=int, default=8)
    ap.add_argument("--max-aim-frames", type=int, default=60)
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env
    from re1_rl.knife_macro import probe_crouch_aim_entry, read_pre_knife_state
    from re1_rl.sticky_input import StickyInputState

    port = int(args.port)
    bridge = BizHawkClient(
        port=port,
        timeout=300.0,
        screenshot_path=str(ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    print(f"[entry_probe] launching EmuHawk port={port}", flush=True)
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
    savestate = ROOT / "curriculum" / "m0_dining_to_main_hall.json"

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
        curriculum_path=savestate,
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=False,
    )
    env._ram_skip.use_engine_patches = False
    bridge.wait_for_client()
    bridge.set_speed(int(args.speed))
    env.reset()

    action_index = {name: ACTION_NAMES.index(name) for name in ACTION_NAMES}
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    state_path = str(ROOT / env._stage["init_savestate"])

    results: list[dict] = []
    print("[entry_probe] scenario x strategy matrix:", flush=True)

    for scenario in SCENARIOS:
        for strategy in STRATEGIES:
            bridge.load_savestate(state_path)
            bridge.frameadvance(4)
            env._sticky_input = StickyInputState()
            _run_setup(env, action_index, scenario["setup"])
            pre = read_pre_knife_state(bridge)
            rep = probe_crouch_aim_entry(
                bridge,
                strategy=strategy,
                r1_preamble_frames=int(args.r1_preamble),
                max_frames=int(args.max_aim_frames),
                empty_sticky=empty,
                prev_hp=96,
                episode_start_hp=96,
            )
            row = {
                "scenario": scenario["name"],
                "strategy": strategy,
                "pre_state": pre,
                "probe": rep,
            }
            results.append(row)
            ok = "OK" if rep["reached_crouch_aim"] else "FAIL"
            print(
                f"  {scenario['name']:22} {strategy:14} {ok:4} "
                f"pre={pre['label']:22} {pre['hooks']} "
                f"ready={int(pre['knife_action_ready'])} "
                f"final={rep['final_label']}",
                flush=True,
            )

    payload = {
        "port": port,
        "r1_preamble": int(args.r1_preamble),
        "max_aim_frames": int(args.max_aim_frames),
        "results": results,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[entry_probe] wrote {OUT_JSON}", flush=True)

    n_ok = sum(1 for r in results if r["probe"]["reached_crouch_aim"])
    print(f"[entry_probe] reached crouch_aim: {n_ok}/{len(results)}", flush=True)
    shutdown(0 if n_ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
