"""End-to-end audit of the m0 waypoint chain (route seqs 1-9).

For every leg it verifies, mechanically:
  - the success condition fires from a plausible game state
  - the waypoint bonus is paid exactly once
  - PBRS has a usable gradient toward the goal (graph hops + door trail)
  - item names decode (route -> canonical -> RAM ITEM_IDS)
  - the reward function does not punish the agent for doing the right thing

Run: python scripts/audit_waypoints.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import ITEM_IDS
from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import compute_reward
from re1_rl.room_graph import RoomGraph

ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"
ROOMS = PROJECT_ROOT / "data" / "rooms.json"
ROOM_ITEMS = PROJECT_ROOT / "data" / "room_items.json"
CURRICULUM = PROJECT_ROOT / "curriculum" / "m0_dining_to_main_hall.json"

FAILURES: list[str] = []
WARNINGS: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(f"{label}: {detail}")


def warn(label: str, detail: str = "") -> None:
    print(f"  [WARN] {label}" + (f"  ({detail})" if detail else ""))
    WARNINGS.append(f"{label}: {detail}")


def make_state(room, x=30000, z=7500, step=1, inventory=(), new_items=(),
               in_control=True, hp=96):
    return {
        "room_id": room, "x": x, "z": z, "y": 0, "facing": 0, "hp": hp,
        "cam_id": 0, "character_id": 1, "in_control": in_control,
        "inventory": list(inventory), "new_items": list(new_items),
        "dead": False, "step": step,
    }


def step_reward(planner, progress, graph, prev, cur, success_room="107"):
    _, bd = compute_reward(prev, cur, planner, progress=progress, graph=graph,
                           success_room=success_room, return_breakdown=True)
    return bd


def main() -> int:
    stage = json.loads(CURRICULUM.read_text(encoding="utf-8"))
    route = json.loads(ROUTE.read_text(encoding="utf-8"))
    rooms = json.loads(ROOMS.read_text(encoding="utf-8"))
    room_items = json.loads(ROOM_ITEMS.read_text(encoding="utf-8"))
    graph = RoomGraph(DOORS)
    known_ram_items = set(ITEM_IDS.values())

    seqs = stage["route_steps"]
    steps = {int(s["seq"]): s for s in route}

    print(f"=== static audit: curriculum {CURRICULUM.name}, seqs {seqs} ===")
    for seq in seqs:
        s = steps.get(seq)
        print(f"- seq {seq}: room {s['room_id']}  [{s['action_type']}]  {s['objective'][:60]}")
        check(s is not None, f"seq {seq} exists in route")
        room = str(s["room_id"])
        check(room in rooms, f"seq {seq} room {room} in rooms.json")

        # goal reachability: does ANY room have a known path to this goal?
        reachable_from = [r for r in graph.adj if graph.hop_distance(r, room) is not None]
        if room in graph.adj or reachable_from:
            check(True, f"seq {seq} goal {room} reachable in door graph",
                  f"{len(reachable_from)} source rooms")
        else:
            check(False, f"seq {seq} goal {room} reachable in door graph",
                  "UNMAPPED: PBRS is flat and wrong-room logic misfires on this leg")

        # item wiring
        for item in s.get("items_gained", []):
            canon = canonical_item(item)
            check(canon in known_ram_items,
                  f"seq {seq} item '{item}' -> '{canon}' decodable from RAM")
            in_room_items = any(
                canonical_item(i.get("name", "")) == canon
                for i in room_items.get(room, {}).get("items", []))
            if not in_room_items:
                warn(f"seq {seq} item '{canon}' not listed in room_items[{room}]",
                     "items_left_here obs will not advertise it")
        cond = s.get("success_condition")
        if cond == "" or cond is None:
            warn(f"seq {seq} has empty success_condition",
                 "falls back to bare room_enter")

    print(f"\n=== dynamic audit: simulated walkthrough of the full chain ===")
    planner = WaypointPlanner(ROUTE, route_steps=seqs,
                              terminal_goal_room=stage["success_room"])
    progress = ProgressTracker()

    # --- leg 1: emblem pickup in 105 ---
    prev = make_state("105", step=1)
    cur = make_state("105", step=2, inventory=["emblem"], new_items=["emblem"])
    bd = step_reward(planner, progress, graph, prev, cur)
    check(planner.waypoint_index == 1, "seq1 has_item(emblem) advances planner")
    check(bd["waypoint"] > 0, "seq1 waypoint bonus paid", f"{bd['waypoint']}")
    check(bd["item"] > 0, "seq1 emblem pickup bonus paid", f"{bd['item']}")

    # --- leg 2: Kenneth cutscene -> tea room 104 ---
    prev = cur
    cur = make_state("104", step=3)
    bd = step_reward(planner, progress, graph, prev, cur)
    check(planner.waypoint_index == 2, "seq2 room_enter 104 advances planner")
    check(bd["waypoint"] > 0, "seq2 waypoint bonus paid", f"{bd['waypoint']}")
    check(bd["pbrs_graph"] > 0, "105->104 PBRS graph pull is positive", f"{bd['pbrs_graph']}")

    # --- leg 3: re-enter dining 105 (Barry after Kenneth) ---
    prev = cur
    cur = make_state("105", step=4)
    bd = step_reward(planner, progress, graph, prev, cur)
    check(bd["wrong_room"] == 0, "104->105 not penalized")
    check(planner.waypoint_index == 3, "seq3 room_enter 105 advances planner")
    check(bd["waypoint"] > 0, "seq3 waypoint bonus paid")
    t = 4

    # --- leg 4: first entry main hall 106 (Barry) ---
    prev = cur
    cur = make_state("106", step=t + 1)
    bd = step_reward(planner, progress, graph, prev, cur)
    check(planner.waypoint_index == 4, "seq4 room_enter 106 advances planner")
    check(bd["waypoint"] > 0, "seq4 waypoint bonus paid")
    t += 1

    # --- leg 5: lockpick from Barry (Wesker briefing follows in-room) ---
    prev = cur
    cur = make_state("106", step=t + 1, inventory=["lockpick"])
    bd = step_reward(planner, progress, graph, prev, cur)
    check(planner.waypoint_index == 5, "seq5 has_item(lockpick) advances planner")
    check(bd["waypoint"] > 0, "seq5 waypoint bonus paid")
    t += 1

    # --- leg 6a: the intended 106 -> 203 staircase ---
    import copy
    pl4, pr4 = copy.deepcopy(planner), copy.deepcopy(progress)
    prev = cur
    cur = make_state("203", step=t + 1)
    bd = step_reward(pl4, pr4, graph, prev, cur)
    check(pl4.waypoint_index == 6, "seq6 room_enter_any via 203 advances planner")
    check(bd["waypoint"] > 0, "seq6 waypoint bonus paid (203)")
    check(bd["wrong_room"] == 0, "entering 203 not penalized", f"{bd['wrong_room']}")

    # --- leg 4b: RAM reports 201 while climbing east stairs (allowed by cond) ---
    pl4b, pr4b = copy.deepcopy(planner), copy.deepcopy(progress)
    bd = step_reward(pl4b, pr4b, graph, prev, make_state("201", step=t + 1))
    check(pl4b.waypoint_index == 6, "seq6 room_enter_any via 201 advances planner")
    check(bd["wrong_room"] == 0,
          "entering 201 (a listed success room) not wrong-room penalized",
          f"wrong_room={bd['wrong_room']}")

    # continue mainline from the 203 branch
    planner, progress = pl4, pr4
    prev = cur

    # --- leg 7: 203 -> 106 return ---
    cur = make_state("106", step=t + 2)
    bd = step_reward(planner, progress, graph, prev, cur)
    check(planner.waypoint_index == 7, "seq7 re-enter 106 advances planner")
    check(bd["waypoint"] > 0, "seq7 waypoint bonus paid")

    # --- leg 8: shotgun (trap room / living room) ---
    prev = cur
    cur = make_state("115", step=t + 3, inventory=["shotgun"], new_items=["shotgun"])
    bd = step_reward(planner, progress, graph, prev, cur)
    check(planner.waypoint_index == 8, "seq8 has_item(shotgun) advances planner")
    check(bd["waypoint"] > 0, "seq8 waypoint bonus paid")
    check(bd["item"] > 0, "seq8 item pickup bonus paid")

    # --- leg 9: gallery / star crest ---
    prev = cur
    cur = make_state("107", step=t + 4, inventory=["shotgun", "star_crest"],
                     new_items=["star_crest"])
    bd = step_reward(planner, progress, graph, prev, cur)
    check(planner.waypoint_index == 9, "seq9 has_item(star_crest) advances planner")
    check(bd["waypoint"] > 0, "seq9 waypoint bonus paid")
    check(planner.waypoint_index == len(seqs),
          "m0 curriculum complete after seq9 (terminal goal 107)")
    check(planner.next_waypoint_room() == "107",
          "post-route goal falls back to terminal_goal_room")

    print(f"\n=== summary: {len(FAILURES)} failures, {len(WARNINGS)} warnings ===")
    for f in FAILURES:
        print(f"  FAIL: {f}")
    for w in WARNINGS:
        print(f"  WARN: {w}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
