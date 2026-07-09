"""Verify each checkpoint savestate: correct restore, correct agent inputs,
and live achievability of the NEXT checkpoint.

For every leg (wp00 = stage init, wp01.. = states/checkpoints/manifest.json):
  1. boot the savestate in a real env
  2. RESTORE  -- agent is in the room the manifest recorded
  3. INPUTS   -- goal vector points at the right next objective: goal room,
                 compass/door fields, hop distance, wrong_room_flag,
                 has_required_items (each asserted, printed with values)
  4. ACHIEVE  -- drive with the prior_s1 policy + exploration walk for up to
                 --probe-steps; report whether the next checkpoint fired,
                 in how many steps, and the reward evidence along the way
                 (waypoint bonus paid, PBRS net, wrong-room fines)

Writes states/checkpoints/verify_report.json and prints a verdict table.

Usage:
    python scripts/verify_checkpoint_states.py --probe-steps 3000
    python scripts/verify_checkpoint_states.py --legs wp00,wp03 --probe-steps 5000
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

from re1_rl.obs_encoder import GOAL_FIELDS

CURRICULUM = PROJECT_ROOT / "curriculum" / "m0_dining_to_main_hall.json"
CKPT_DIR = PROJECT_ROOT / "states" / "checkpoints"
MANIFEST = CKPT_DIR / "manifest.json"
REPORT = CKPT_DIR / "verify_report.json"
DEFAULT_POLICY = PROJECT_ROOT / "data" / "ppo_re1_final_prior_s1.zip"

GOAL_IDX = {name: i for i, (name, _) in enumerate(GOAL_FIELDS)}

ACTION_WEIGHTS = {0: 0.01, 1: 0.14, 2: 0.02, 3: 0.12, 4: 0.12,
                  5: 0.33, 6: 0.04, 7: 0.18, 8: 0.02, 9: 0.02}
ACTIONS = list(ACTION_WEIGHTS)
WEIGHTS = list(ACTION_WEIGHTS.values())


def check(results: list, ok: bool, label: str, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"    [{mark}] {label}" + (f"  ({detail})" if detail else ""), flush=True)
    results.append({"check": label, "ok": bool(ok), "detail": detail})
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-steps", type=int, default=3000)
    ap.add_argument("--legs", default=None, help="comma list, e.g. wp00,wp03")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY))
    ap.add_argument("--eps", type=float, default=0.3)
    ap.add_argument("--port", type=int, default=5555)
    args = ap.parse_args()

    from scripts.train_parallel import make_env
    from re1_rl.room_graph import RoomGraph

    stage = json.loads(CURRICULUM.read_text(encoding="utf-8"))
    route_steps: list[int] = stage["route_steps"]
    route = json.loads((PROJECT_ROOT / "data" / "route_jill_anypct.json")
                       .read_text(encoding="utf-8"))
    steps_by_seq = {int(s["seq"]): s for s in route}
    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")

    manifest: dict[str, dict] = {}
    if MANIFEST.is_file():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # fleet-training captures write one wpNN.json sidecar per checkpoint
    for sidecar in sorted(CKPT_DIR.glob("wp*.json")):
        manifest.setdefault(sidecar.stem,
                            json.loads(sidecar.read_text(encoding="utf-8")))

    # wp00 == stage init savestate (leg toward seq route_steps[0])
    legs: dict[str, dict] = {
        "wp00": {"file": stage["init_savestate"], "waypoint_index": 0,
                 "room_id": None},
    }
    legs.update(manifest)
    only = set(args.legs.split(",")) if args.legs else None

    model = None
    if args.policy and Path(args.policy).is_file():
        from stable_baselines3 import PPO
        model = PPO.load(args.policy, device="cpu")

    # make_env owns the full EmuHawk launch/handshake lifecycle
    env = make_env(args.port - 5555, str(CURRICULUM.relative_to(PROJECT_ROOT)))()

    report: dict[str, dict] = {}
    try:
        for key in sorted(legs):
            if only and key not in only:
                continue
            entry = legs[key]
            idx = int(entry["waypoint_index"])
            if idx >= len(route_steps):
                continue  # route finished; no next leg
            next_seq = route_steps[idx]
            next_step = steps_by_seq[next_seq]
            goal_room = str(next_step["room_id"])
            results: list = []
            print(f"\n=== {key}: leg toward seq {next_seq} "
                  f"({goal_room}: {next_step['objective'][:55]}) ===", flush=True)

            if not (PROJECT_ROOT / entry["file"]).is_file():
                print(f"    [MISS] savestate not collected yet: {entry['file']}",
                      flush=True)
                report[key] = {"status": "MISSING", "file": entry["file"]}
                continue

            # stage clone starting at this checkpoint with the remaining legs
            verify_stage = dict(stage)
            verify_stage["init_savestate"] = entry["file"]
            verify_stage["route_steps"] = route_steps[idx:]
            verify_stage["max_steps"] = args.probe_steps
            tmp = CKPT_DIR / f"_verify_stage_{key}.json"
            tmp.write_text(json.dumps(verify_stage, indent=2), encoding="utf-8")
            env.unwrapped.curriculum_path = tmp

            obs, info = env.reset()
            state = info["state"]

            # --- RESTORE ---
            if entry.get("room_id"):
                check(results, state["room_id"] == entry["room_id"],
                      "savestate restores recorded room",
                      f"expected {entry['room_id']}, got {state['room_id']}")
            check(results, state["hp"] > 0, "player alive", f"hp={state['hp']}")

            # --- INPUTS ---
            g = obs["goal"]
            check(results, info["waypoint"] == goal_room,
                  "planner goal is next checkpoint room",
                  f"planner says {info['waypoint']}, route says {goal_room}")
            enc = env.unwrapped._encoder
            check(results,
                  abs(g[GOAL_IDX["goal_room_index"]] - enc._room_idx_norm(goal_room)) < 1e-6,
                  "goal vector encodes that room",
                  f"goal_room_index={g[GOAL_IDX['goal_room_index']]:.4f}")
            in_target = state["room_id"] == goal_room
            check(results,
                  bool(g[GOAL_IDX["in_target_room"]]) == in_target,
                  "in_target_room flag correct",
                  f"flag={g[GOAL_IDX['in_target_room']]:.0f}")
            hops = graph.hop_distance(state["room_id"], goal_room)
            if hops is not None:
                check(results, g[GOAL_IDX["route_hop_distance"]] < 1.0,
                      "hop distance informative (goal mapped)",
                      f"hops={hops}, field={g[GOAL_IDX['route_hop_distance']]:.3f}")
                check(results, g[GOAL_IDX["wrong_room_flag"]] == 0.0,
                      "wrong_room_flag clear on route")
                if not in_target:
                    check(results, g[GOAL_IDX["doors_available"]] == 1.0,
                          "door compass available toward goal",
                          f"door_dist={g[GOAL_IDX['door_distance']]:.3f}")
            else:
                check(results, g[GOAL_IDX["route_hop_distance"]] == 1.0,
                      "hop distance saturates (goal UNMAPPED - known gap)",
                      "PBRS flat on this leg; no door compass")
            check(results, g[GOAL_IDX["has_required_items"]] == 1.0,
                  "has_required_items satisfied",
                  f"inventory={state['inventory']}")

            # --- ACHIEVE ---
            fired = False
            fired_step = None
            bonus_paid = 0.0
            wrong_fines = 0.0
            pbrs_net = 0.0
            t0 = time.time()
            for t in range(1, args.probe_steps + 1):
                if model is not None and random.random() > args.eps:
                    action, _ = model.predict(obs, deterministic=False)
                    action = int(action)
                else:
                    action = random.choices(ACTIONS, weights=WEIGHTS)[0]
                obs, _, term, trunc, info = env.step(action)
                bd = info["reward_breakdown"]
                bonus_paid += bd["waypoint"]
                wrong_fines += bd["wrong_room"]
                pbrs_net += bd["pbrs_graph"] + bd["pbrs_door"]
                if int(info["waypoint_index"]) >= 1:
                    fired = True
                    fired_step = t
                    break
                if term or trunc:
                    break
            mins = (time.time() - t0) / 60.0
            check(results, fired,
                  f"NEXT CHECKPOINT (seq {next_seq}) achieved by driver",
                  f"step {fired_step}, {mins:.1f} min" if fired
                  else f"not within {args.probe_steps} steps ({mins:.1f} min)")
            if fired:
                check(results, bonus_paid > 0, "waypoint bonus paid on completion",
                      f"sum={bonus_paid}")
            print(f"    evidence: waypoint_bonus={bonus_paid:+.1f} "
                  f"wrong_room_fines={wrong_fines:+.1f} pbrs_net={pbrs_net:+.2f}",
                  flush=True)

            report[key] = {
                "status": "OK" if all(r["ok"] for r in results) else "DEFECT",
                "next_seq": next_seq, "goal_room": goal_room,
                "achieved": fired, "achieved_step": fired_step,
                "waypoint_bonus_sum": bonus_paid,
                "wrong_room_fines_sum": wrong_fines,
                "pbrs_net": round(pbrs_net, 3),
                "checks": results,
            }
    finally:
        env.close()

    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n=== verdict table ===", flush=True)
    for key in sorted(report):
        r = report[key]
        if r.get("status") == "MISSING":
            print(f"  {key}: MISSING ({r['file']})")
            continue
        ach = f"achieved in {r['achieved_step']}" if r["achieved"] else "NOT achieved"
        print(f"  {key} -> seq {r['next_seq']} ({r['goal_room']}): "
              f"{r['status']}, {ach}")
    print(f"report: {REPORT}", flush=True)
    print("VERIFY_DONE", flush=True)
    bad = any(r.get("status") == "DEFECT" for r in report.values())
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
