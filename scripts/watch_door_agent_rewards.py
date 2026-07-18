"""Run fleet policy at locked door; log reward breakdown (esp. interact spam).

Port 7788 — does not touch training fleet ports.

  python scripts/watch_door_agent_rewards.py --steps 400 --learner-host 192.168.0.111
"""

from __future__ import annotations

import argparse
import json
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
DOOR_STATE = ROOT / "states" / "jill_locked_door_107_108.State"
OUT = ROOT / "data" / "logs" / "door_agent_reward_monitor.jsonl"
INTERACT_ACTION = 7


def _resync_env(env) -> tuple[dict, dict]:
    env._sticky_input.reset()
    env._frame_stack = []
    rgb = env.bridge.screenshot()
    frame_obs = env._push_frame(rgb)
    state = env._read_state()
    env._seed_episode_progress(state)
    env._episode_history.reset(str(state.get("room_id", "")), step=0)
    env._visited.reset()
    env._visited.update(state["room_id"], state["x"], state["z"])
    env._prev_state = state
    env._prev_hp = int(state["hp"])
    env._episode_start_hp = int(state["hp"])
    env._episode_min_hp = int(state["hp"])
    env._step_count = 0
    obs = env._build_obs(frame_obs, state)
    env._start_bg_skip()
    return state, obs


def _load_fleet_policy(*, learner_host: str, learner_port: int, checkpoint: Path | None):
    from re1_rl.async_fleet import load_async_learner

    candidates = [
        checkpoint,
        ROOT / "data" / "_fleet_latest_policy.zip",
        ROOT / "data" / "ppo_re1_final_reward_tune_1040k.zip",
    ]
    for path in candidates:
        if path is not None and Path(path).is_file():
            model = load_async_learner(device="cpu", resume=Path(path), tb_log=None)
            print(f"[door-watch] policy from disk {Path(path).name}", flush=True)
            return model, int(getattr(model, "num_timesteps", 0))

    raise FileNotFoundError(
        "no checkpoint — pass --checkpoint or scp latest from learner host"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=7788)
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--speed", type=int, default=3200)
    ap.add_argument("--learner-host", default="192.168.0.111")
    ap.add_argument("--learner-port", type=int, default=8765)
    ap.add_argument("--checkpoint", type=Path, default=None)
    ap.add_argument("--savestate", type=Path, default=DOOR_STATE)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env

    if args.out.exists():
        args.out.unlink()

    bridge = BizHawkClient(port=args.port, timeout=300.0, connect_timeout=120.0)
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

    totals: dict[str, float] = {}
    interact_rows: list[dict] = []
    positive_events = 0
    interact_at_door = 0

    try:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=True,
        )
        env.reset()
        policy, policy_tag = _load_fleet_policy(
            learner_host=str(args.learner_host),
            learner_port=int(args.learner_port),
            checkpoint=args.checkpoint,
        )

        savestate = args.savestate.resolve()
        if not savestate.is_file():
            print(f"[door-watch] missing {savestate}", flush=True)
            return 1
        bridge.load_savestate(str(savestate))
        bridge.frameadvance(8)
        state0, obs = _resync_env(env)
        print(
            f"[door-watch] start room={state0.get('room_id')} "
            f"pos=({state0.get('x')},{state0.get('z')}) policy={policy_tag}",
            flush=True,
        )

        rng = np.random.default_rng(0)
        ep_rew = 0.0

        for step_i in range(int(args.steps)):
            masks = env.unwrapped.action_masks()
            legal = np.flatnonzero(masks)
            if len(legal) == 0:
                time.sleep(0.05)
                continue

            try:
                action, _ = policy.predict(obs, action_masks=masks, deterministic=False)
            except TypeError:
                action, _ = policy.predict(obs, deterministic=False)
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

            pos_hits = {k: v for k, v in hits.items() if v > 0}
            if pos_hits:
                positive_events += 1

            room = str(state.get("room_id", ""))
            is_interact = action == INTERACT_ACTION
            if is_interact and room == "107":
                interact_at_door += 1

            row = {
                "step": step_i,
                "action": ACTION_NAMES[action],
                "reward": float(rew),
                "ep_reward": float(ep_rew),
                "room": room,
                "scene_flag": int(state.get("scene_flag", 0) or 0),
                "msg_flag": int(state.get("msg_flag", 0) or 0),
                "frames": int(state.get("step_emulated_frames", 0) or 0),
                "cutscene_key": state.get("cutscene_key"),
                "breakdown": hits,
                "positive": pos_hits,
            }
            with args.out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

            if is_interact or pos_hits or step_i % 50 == 0:
                flag = ""
                if hits:
                    flag = " " + ",".join(f"{k}:{v:+.4f}" for k, v in sorted(hits.items()))
                print(
                    f"[door-watch] #{step_i:3d} {ACTION_NAMES[action]:<12} "
                    f"rew={rew:+.4f} ep={ep_rew:+.4f} room={room}{flag}",
                    flush=True,
                )

            if term or trunc:
                print(f"[door-watch] episode end term={term} trunc={trunc}", flush=True)
                break

        pos_totals = {k: v for k, v in totals.items() if v > 0}
        print(
            f"[door-watch] done steps={step_i + 1} ep_rew={ep_rew:+.4f} "
            f"interact@107={interact_at_door} positive_steps={positive_events}",
            flush=True,
        )
        print(f"[door-watch] term totals:", flush=True)
        for k in sorted(totals):
            print(f"  {k}: {totals[k]:+.6f}", flush=True)
        if pos_totals:
            print(f"[door-watch] WARN positive terms: {pos_totals}", flush=True)
            return 2
        if totals.get("new_cutscene", 0.0) != 0.0:
            print("[door-watch] FAIL new_cutscene paid", flush=True)
            return 2
        print("[door-watch] PASS: no positive reward terms", flush=True)
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        bridge.close()
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
