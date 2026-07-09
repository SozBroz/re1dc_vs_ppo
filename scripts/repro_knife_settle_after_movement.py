"""Repro agent-ram step 1140: movement spam then knife_swing from standing idle."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import KNIFE_SWING_ACTION
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.knife_macro import read_pre_knife_state

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools/BizHawk-2.11.1/EmuHawk.exe"
ROM = ROOT / "roms/Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua/re1_client.lua"
CURRICULUM = ROOT / "curriculum/m0_dining_to_main_hall.json"


def main() -> int:
    port = 7788
    bridge = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={port}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        env.reset()
        moves = [
            ACTION_NAMES.index("quickturn"),
            ACTION_NAMES.index("quickturn"),
            ACTION_NAMES.index("run_forward"),
            ACTION_NAMES.index("run_forward"),
        ] * 4
        for a in moves:
            env.step(a)
        pre = read_pre_knife_state(bridge)
        m = env.unwrapped.action_masks()
        print(f"pre_knife={pre}", flush=True)
        print(f"knife_legal={m[KNIFE_SWING_ACTION]}", flush=True)
        _, rew, _, _, info = env.step(KNIFE_SWING_ACTION)
        report = getattr(bridge, "last_knife_anim_report", {}) or {}
        print(
            f"outcome={report.get('outcome')} frames={info.get('state', {}).get('step_emulated_frames')} "
            f"rew={rew}",
            flush=True,
        )
        assert report.get("outcome") != "settle_timeout", report
        print("PASS", flush=True)
    finally:
        try:
            bridge.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
