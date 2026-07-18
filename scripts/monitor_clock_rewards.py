"""Run trained policy from a savestate and print reward breakdowns.

Usage:
    python scripts/monitor_clock_rewards.py \\
        --savestate "tools/BizHawk-2.11.1/PSX/State/...QuickSave9.State" \\
        --checkpoint data/checkpoints/ppo_re1_25080000_steps.zip \\
        --steps 250
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"


def _print_pos(state: dict) -> str:
    return (
        f"room={state.get('room_id')} cam={state.get('cam_id')} "
        f"pos=({state.get('x')},{state.get('z')}) "
        f"hp={state.get('hp')}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=7788)
    ap.add_argument("--savestate", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--steps", type=int, default=250)
    ap.add_argument("--speed", type=int, default=3200)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    from re1_rl.async_fleet import load_async_learner
    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env
    from re1_rl.progress import ProgressTracker

    port = int(args.port)
    savestate = args.savestate.resolve()
    checkpoint = args.checkpoint.resolve()
    if not savestate.is_file():
        print(f"[clock-rew] missing savestate: {savestate}", flush=True)
        return 1
    if not checkpoint.is_file():
        print(f"[clock-rew] missing checkpoint: {checkpoint}", flush=True)
        return 1

    bridge = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={port}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    totals: dict[str, float] = {}
    nonzero_events = 0
    try:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=True,
        )
        env._load_stage()
        print(f"[clock-rew] loading savestate {savestate.name}...", flush=True)
        bridge.load_savestate(str(savestate))
        bridge.frameadvance(1)
        env._sticky_input.reset()
        env._prev_action = None
        env._use_phase = 0
        env._equip_phase = 0
        env._combine_phase = 0
        env._step_count = 0
        env._frame_stack = []
        env._progress = ProgressTracker()
        env._visited.reset()
        env._box_cache = None
        env._init_anim_history()
        rgb = bridge.screenshot()
        frame_obs = env._push_frame(rgb)
        env._prev_hp = 0
        state = env._read_state()
        env._seed_episode_progress(state)
        env._episode_history.reset(str(state.get("room_id", "")), step=0)
        env._visited.update(state["room_id"], state["x"], state["z"])
        env._prev_state = state
        env._prev_hp = state["hp"]
        env._start_bg_skip()
        obs = env._build_obs(frame_obs, state)
        print(f"[clock-rew] start {_print_pos(state)}", flush=True)

        model = load_async_learner(device="cpu", resume=checkpoint, tb_log=None)
        print(
            f"[clock-rew] policy from {checkpoint.name} "
            f"steps={model.num_timesteps:,}",
            flush=True,
        )

        rng = np.random.default_rng(int(args.seed))
        ep_rew = 0.0
        for step_i in range(int(args.steps)):
            masks = env.unwrapped.action_masks()
            legal = np.flatnonzero(masks)
            if len(legal) == 0:
                print(f"[clock-rew] #{step_i:3d} NO LEGAL ACTIONS", flush=True)
                time.sleep(0.2)
                continue

            action, _ = model.predict(obs, deterministic=False)
            action = int(action)
            if not masks[action]:
                action = int(rng.choice(legal))

            obs, rew, term, trunc, info = env.step(action)
            ep_rew += float(rew)
            state = info.get("state") or {}
            bd = info.get("reward_breakdown") or {}
            hits = {k: float(v) for k, v in bd.items() if abs(float(v)) > 1e-9}
            for k, v in hits.items():
                totals[k] = totals.get(k, 0.0) + v

            flags = []
            if hits:
                nonzero_events += 1
                flags.append("REW=" + ",".join(f"{k}:{v:+.4f}" for k, v in sorted(hits.items())))
            ck = state.get("cutscene_key")
            if ck:
                flags.append(f"cutscene_key={ck}")
            if info.get("new_items"):
                flags.append(f"new_items={info.get('new_items')}")

            print(
                f"[clock-rew] #{step_i:3d} {ACTION_NAMES[action]:<14} "
                f"rew={rew:+.4f} ep={ep_rew:+.4f} {_print_pos(state)} "
                f"{' '.join(flags)}",
                flush=True,
            )
            if term or trunc:
                print(f"[clock-rew] episode end term={term} trunc={trunc}", flush=True)
                break

        print(
            f"[clock-rew] done steps={step_i + 1} ep_rew={ep_rew:+.4f} "
            f"nonzero_steps={nonzero_events}",
            flush=True,
        )
        if totals:
            print("[clock-rew] reward term totals:", flush=True)
            for k in sorted(totals):
                print(f"  {k}: {totals[k]:+.6f}", flush=True)
        else:
            print("[clock-rew] no nonzero reward terms logged", flush=True)
    finally:
        try:
            bridge.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
