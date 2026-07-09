"""Diagnose why loiter/pickup legs fail from checkpoint savestates.

Boots the wp02 verify stage (start in 106, need 60 in-control steps) and the
wp00 stage (emblem pickup), walks a few hundred steps, and logs the raw
truth: room, in_control flag, the progress counter, waypoint index.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.train_parallel import make_env

CKPT_DIR = PROJECT_ROOT / "states" / "checkpoints"

ACTION_WEIGHTS = {0: 0.01, 1: 0.14, 2: 0.02, 3: 0.12, 4: 0.12,
                  5: 0.33, 6: 0.04, 7: 0.18, 8: 0.02, 9: 0.02}
ACTIONS = list(ACTION_WEIGHTS)
WEIGHTS = list(ACTION_WEIGHTS.values())


def probe(env, label: str, steps: int = 400) -> None:
    print(f"\n=== {label} ===", flush=True)
    obs, info = env.reset()
    st = info["state"]
    print(f"reset: room={st['room_id']} in_control={st['in_control']} "
          f"hp={st['hp']} inv={st['inventory']}", flush=True)
    random.seed(0)
    tracker = env.unwrapped._progress
    in_control_true = 0
    for t in range(1, steps + 1):
        action = random.choices(ACTIONS, weights=WEIGHTS)[0]
        obs, _, term, trunc, info = env.step(action)
        st = info["state"]
        if st["in_control"]:
            in_control_true += 1
        if t % 40 == 0 or int(info["waypoint_index"]) > 0:
            print(f"t={t:4d} room={st['room_id']} in_control={st['in_control']} "
                  f"ctl_steps_106={tracker.in_control_steps_in_room('106')} "
                  f"ctl_steps_105={tracker.in_control_steps_in_room('105')} "
                  f"wp_idx={info['waypoint_index']} "
                  f"in_control_frac={in_control_true/t:.2f} "
                  f"skipped={info['frames_skipped']}", flush=True)
        if int(info["waypoint_index"]) > 0:
            print(f"ADVANCED at t={t}", flush=True)
            break
        if term or trunc:
            print(f"episode ended at t={t} (term={term})", flush=True)
            break


def main() -> int:
    stage = json.loads((PROJECT_ROOT / "curriculum" /
                        "m0_dining_to_main_hall.json").read_text(encoding="utf-8"))

    # wp02 leg: start in 106, seq3 loiter (route_steps sliced from idx 2)
    wp02 = json.loads((CKPT_DIR / "wp02.json").read_text(encoding="utf-8"))
    s = dict(stage)
    s["init_savestate"] = wp02["file"]
    s["route_steps"] = stage["route_steps"][2:]
    s["max_steps"] = 2000
    (CKPT_DIR / "_diag_wp02.json").write_text(json.dumps(s), encoding="utf-8")

    # wp00 leg: fresh spawn, seq1 emblem pickup
    s0 = dict(stage)
    s0["max_steps"] = 2000
    (CKPT_DIR / "_diag_wp00.json").write_text(json.dumps(s0), encoding="utf-8")

    env = make_env(12, "states/checkpoints/_diag_wp02.json")()
    try:
        probe(env, "wp02: loiter-in-106 leg (seq 3)")
        env.unwrapped.curriculum_path = CKPT_DIR / "_diag_wp00.json"
        probe(env, "wp00: emblem pickup leg (seq 1)", steps=600)
    finally:
        env.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
