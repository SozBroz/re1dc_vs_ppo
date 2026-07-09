"""Collect a savestate at the moment each route checkpoint completes.

Plays the m0 stage with the best policy (prior_s1) mixed with the biased
exploration walk. Whenever the planner advances to a waypoint index never
captured before, saves states/checkpoints/wpNN_seqN_<room>.State plus a
manifest.json entry recording room/inventory/step context.

The captured state for index i is the START state for leg i+1, consumed by
scripts/verify_checkpoint_states.py.

Usage:
    python scripts/collect_checkpoint_states.py --minutes 45
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

CURRICULUM = PROJECT_ROOT / "curriculum" / "m0_dining_to_main_hall.json"
OUT_DIR = PROJECT_ROOT / "states" / "checkpoints"
MANIFEST = OUT_DIR / "manifest.json"
DEFAULT_POLICY = PROJECT_ROOT / "data" / "ppo_re1_final_prior_s1.zip"

# forward-biased walk (same shape that harvested the door graph)
ACTION_WEIGHTS = {0: 0.01, 1: 0.14, 2: 0.02, 3: 0.12, 4: 0.12,
                  5: 0.33, 6: 0.04, 7: 0.18, 8: 0.02, 9: 0.02}
ACTIONS = list(ACTION_WEIGHTS)
WEIGHTS = list(ACTION_WEIGHTS.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=45.0)
    ap.add_argument("--policy", default=str(DEFAULT_POLICY))
    ap.add_argument("--eps", type=float, default=0.3,
                    help="probability of a walk action instead of the policy")
    ap.add_argument("--port", type=int, default=5555)
    args = ap.parse_args()

    from scripts.train_parallel import make_env

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    if MANIFEST.is_file():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    model = None
    if args.policy and Path(args.policy).is_file():
        from stable_baselines3 import PPO
        model = PPO.load(args.policy, device="cpu")
        print(f"[collect] policy driver: {args.policy} (eps={args.eps})", flush=True)

    # make_env owns the full EmuHawk launch/handshake lifecycle
    env = make_env(args.port - 5555, str(CURRICULUM.relative_to(PROJECT_ROOT)))()
    bridge = env.unwrapped.bridge

    route_steps = json.loads(CURRICULUM.read_text(encoding="utf-8"))["route_steps"]
    deadline = time.time() + args.minutes * 60.0
    episodes = 0
    try:
        obs, _ = env.reset()
        last_index = 0
        episodes = 1
        while time.time() < deadline:
            if model is not None and random.random() > args.eps:
                action, _ = model.predict(obs, deterministic=False)
                action = int(action)
            else:
                action = random.choices(ACTIONS, weights=WEIGHTS)[0]
            obs, _, term, trunc, info = env.step(action)

            idx = int(info["waypoint_index"])
            if idx > last_index:
                last_index = idx
                key = f"wp{idx:02d}"
                if key not in manifest:
                    seq = route_steps[idx - 1]
                    state = info["state"]
                    fname = f"wp{idx:02d}_seq{seq}_{state['room_id']}.State"
                    bridge.save_savestate(str(OUT_DIR / fname))
                    manifest[key] = {
                        "file": f"states/checkpoints/{fname}",
                        "completed_seq": seq,
                        "waypoint_index": idx,
                        "room_id": state["room_id"],
                        "inventory": state["inventory"],
                        "episode_step": int(state["step"]),
                        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    MANIFEST.write_text(json.dumps(manifest, indent=2),
                                        encoding="utf-8")
                    print(f"[collect] CAPTURED {key} (seq {seq}) in "
                          f"{state['room_id']} at step {state['step']}", flush=True)

            if term or trunc:
                obs, _ = env.reset()
                last_index = 0
                episodes += 1
                captured = sorted(manifest)
                print(f"[collect] episode {episodes} start; captured so far: "
                      f"{captured}", flush=True)
                if len(manifest) >= len(route_steps):
                    print("[collect] all checkpoints captured", flush=True)
                    break
    finally:
        env.close()

    print(f"[collect] done: {len(manifest)}/{len(route_steps)} checkpoints, "
          f"{episodes} episodes; manifest at {MANIFEST}", flush=True)
    print("COLLECT_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
