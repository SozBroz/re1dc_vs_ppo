"""Automated door-edge harvester: random-walk N parallel envs and record
every room transition into per-worker JSON files, then merge into
data/doors_empirical.json (existing keys win).

Same pose semantics as scripts/log_door_transitions.py:
  exit pose  = last in-control (x, z, facing, cam) in the old room
  entry pose = first in-control (x, z) in the new room

Usage:
    python scripts/harvest_doors.py --n-envs 6 --minutes 75
    python scripts/harvest_doors.py --policy data/ppo_re1_final_prior_s1.zip
    python scripts/harvest_doors.py --merge-only   # just merge worker files

With --policy, each worker mixes the trained policy (which can already reach
deep rooms) with the biased random walk: policy action with prob 1-eps,
walk action with prob eps. Pure walk when --policy is omitted.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DOORS_PATH = PROJECT_ROOT / "data" / "doors_empirical.json"
CURRICULUM = "curriculum/exp_m0_cap12k.json"
BASE_PORT = 5555

# forward-biased random walk; interact triggers doors when facing them
ACTION_WEIGHTS = {
    1: 0.15,  # forward
    3: 0.13,  # turn_left
    4: 0.13,  # turn_right
    5: 0.39,  # run_forward
    7: 0.20,  # interact
}
ACTIONS = list(ACTION_WEIGHTS)
WEIGHTS = list(ACTION_WEIGHTS.values())


def worker_path(port: int) -> Path:
    return PROJECT_ROOT / "data" / f"doors_harvest_{port}.json"


def worker(rank: int, minutes: float, policy_path: str | None = None,
           eps: float = 0.3) -> None:
    from scripts.train_parallel import make_env

    port = BASE_PORT + rank
    random.seed(port * 7919 + int(time.time()))
    env = make_env(rank, CURRICULUM, BASE_PORT)()
    model = None
    if policy_path:
        from stable_baselines3 import PPO
        model = PPO.load(policy_path, device="cpu")
        print(f"[harvest:{port}] policy-seeded from {policy_path} (eps={eps})",
              flush=True)
    doors: dict = {}
    out = worker_path(port)

    def pose_from(info: dict) -> dict:
        st = info["state"]
        return {
            "room": st["room_id"],
            "x": st["x"],
            "z": st["z"],
            "facing": st["facing"],
            "cam_id": st["cam_id"],
            "in_control": st["in_control"],
        }

    deadline = time.time() + minutes * 60.0
    steps = 0
    try:
        obs, _ = env.reset()
        last_control_pose: dict | None = None
        prev_room: str | None = None
        pending_exit: dict | None = None
        while time.time() < deadline:
            if model is not None and random.random() > eps:
                action, _ = model.predict(obs, deterministic=False)
                action = int(action)
            else:
                action = random.choices(ACTIONS, weights=WEIGHTS)[0]
            obs, _, term, trunc, info = env.step(action)
            steps += 1
            pose = pose_from(info)

            if prev_room is not None and pose["room"] != prev_room:
                pending_exit = last_control_pose

            if pending_exit is not None and pose["in_control"] \
                    and pose["room"] != pending_exit["room"]:
                key = f"{pending_exit['room']}->{pose['room']}"
                if key not in doors:
                    doors[key] = {
                        "from_room": pending_exit["room"],
                        "to_room": pose["room"],
                        "door_x": pending_exit["x"],
                        "door_z": pending_exit["z"],
                        "door_facing": pending_exit["facing"],
                        "door_cam_id": pending_exit["cam_id"],
                        "entry_x": pose["x"],
                        "entry_z": pose["z"],
                        "notes": "logged by harvest_doors.py (random walk)",
                    }
                    out.write_text(json.dumps(doors, indent=2), encoding="utf-8")
                    print(f"[harvest:{port}] saved {key} "
                          f"door=({pending_exit['x']},{pending_exit['z']}) "
                          f"total={len(doors)}", flush=True)
                pending_exit = None

            if pose["in_control"]:
                last_control_pose = pose
            prev_room = pose["room"]

            if term or trunc:
                obs, _ = env.reset()
                last_control_pose = None
                prev_room = None
                pending_exit = None
    finally:
        print(f"[harvest:{port}] done: {steps} steps, {len(doors)} edges", flush=True)
        env.close()


def merge() -> None:
    doors = json.loads(DOORS_PATH.read_text(encoding="utf-8")) if DOORS_PATH.is_file() else {}
    before = len([k for k in doors if not k.startswith("_")])
    added = []
    for f in sorted(PROJECT_ROOT.glob("data/doors_harvest_*.json")):
        harvested = json.loads(f.read_text(encoding="utf-8"))
        for key, d in harvested.items():
            if key not in doors:  # hand-measured / earlier edges win
                doors[key] = d
                added.append(key)
    DOORS_PATH.write_text(json.dumps(doors, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    after = len([k for k in doors if not k.startswith("_")])
    print(f"[merge] {before} -> {after} edges (+{after - before}); new: {sorted(set(added))}")
    print("HARVEST_MERGE_DONE", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-envs", type=int, default=6)
    ap.add_argument("--minutes", type=float, default=75.0)
    ap.add_argument("--policy", default=None,
                    help="PPO .zip; mix policy actions with the random walk")
    ap.add_argument("--eps", type=float, default=0.3,
                    help="probability of a walk action when --policy is set")
    ap.add_argument("--merge-only", action="store_true")
    args = ap.parse_args()

    if not args.merge_only:
        procs = [
            mp.Process(target=worker, args=(i, args.minutes, args.policy, args.eps),
                       daemon=False)
            for i in range(args.n_envs)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
    merge()
    print("HARVEST_DONE", flush=True)
    return 0


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    raise SystemExit(main())
