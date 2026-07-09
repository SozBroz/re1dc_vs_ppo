"""Live human-readable view of the agent's world.

Steps the env (random policy by default, or --policy <model.zip>) and shows:
  - cv2 window: game frame + HUD panel (compass, reward bars, planner state)
  - console: full named obs table every --print-every steps

Launch order (same as training):
  1. python scripts/watch_env.py
  2. EmuHawk with --lua=lua/re1_client.lua --socket_ip=127.0.0.1 --socket_port=5555

Keys in the cv2 window: q = quit, space = pause.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import cv2

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env
from re1_rl.obs_encoder import format_obs_table
from re1_rl.overlay import annotate_frame
from re1_rl.telemetry import EpisodeLogger


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curriculum", default="curriculum/m0_dining_to_main_hall.json")
    ap.add_argument("--policy", default=None, help="SB3 .zip; random actions if omitted")
    ap.add_argument("--steps", type=int, default=500)
    ap.add_argument("--print-every", type=int, default=20)
    ap.add_argument("--log", action="store_true", help="also write episode JSONL telemetry")
    args = ap.parse_args()

    bridge = BizHawkClient()
    bridge.start_server()
    print("[watch] waiting for EmuHawk...")
    bridge.wait_for_client()
    bridge.set_speed(400)

    env = RE1Env(PROJECT_ROOT / args.curriculum, bridge=bridge, project_root=PROJECT_ROOT)
    if args.log:
        env = EpisodeLogger(env, out_dir=PROJECT_ROOT / "data" / "episodes")

    model = None
    if args.policy:
        from sb3_contrib import MaskablePPO  # noqa: F401
        from stable_baselines3 import PPO
        model = PPO.load(args.policy)

    obs, info = env.reset()
    print(format_obs_table(obs))
    print()
    print(env.unwrapped._items.format_checklist(limit=12))

    paused = False
    for step in range(args.steps):
        while paused:
            if cv2.waitKey(100) & 0xFF == ord(" "):
                paused = False
        if model is not None:
            action, _ = model.predict(obs, deterministic=False)
            action = int(action)
        else:
            action = env.action_space.sample()

        obs, reward, terminated, truncated, info = env.step(action)

        base_env = env.unwrapped
        rgb = base_env.bridge.screenshot()
        hud = annotate_frame(rgb, obs, info, reward)
        cv2.imshow("re1_rl watch", hud)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord(" "):
            paused = True

        if step % args.print_every == 0:
            print(f"\n=== step {step}  action={info.get('action_name')}  "
                  f"reward={reward:+.4f}  room={info.get('room_id')} ===")
            print(format_obs_table(obs))

        if info.get("new_items"):
            print(f"[watch] step {step}: picked up {info['new_items']} "
                  f"(TODO {info['item_todo'][0]}/{info['item_todo'][1]})")

        if terminated or truncated:
            print(f"[watch] episode end (terminated={terminated}); resetting")
            obs, info = env.reset()

    env.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
