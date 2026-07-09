"""Offline probe: PBRS/wrong-room magnitudes under the 2-edge vs 18-edge
door graph, for the route states that matter (seq2 goal=106, seq4 goal=203)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import compute_reward
from re1_rl.room_graph import RoomGraph

DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"
ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"

full = json.loads(DOORS.read_text(encoding="utf-8"))
blind = {k: v for k, v in full.items() if k in ("_comment", "105->106", "106->105")}

tmp = Path(tempfile.mkdtemp())
(tmp / "blind.json").write_text(json.dumps(blind), encoding="utf-8")

g_blind = RoomGraph(tmp / "blind.json")
g_full = RoomGraph(DOORS)


def state(room, x=3400, z=17000, step=1):
    return {"room_id": room, "hp": 96, "x": x, "z": z, "step": step}


def probe(graph, label):
    print(f"--- {label} ---")
    # seq2: goal 106, transition 106 -> 104 (wandering off)
    p = WaypointPlanner(ROUTE, route_steps=[2, 3, 4, 5, 6, 7])
    pr = ProgressTracker()
    _, bd = compute_reward(state("106"), state("104"), p, progress=pr,
                           graph=graph, return_breakdown=True)
    tot = sum(bd.values())
    print(f"  goal=106, step 106->104: pbrs_graph={bd['pbrs_graph']:+.2f} "
          f"wrong_room={bd['wrong_room']:+.2f} total_raw={tot:+.2f} scaled={tot*0.1:+.3f}")

    # seq4: goal 203, agent in 106; door gradient toward 203 exit
    p4 = WaypointPlanner(ROUTE, route_steps=[4, 5, 6, 7])
    print(f"  goal=203: hops(106->203)={graph.hop_distance('106', '203')}, "
          f"exit_toward={graph.exit_toward('106', '203')}")
    door = graph.exit_toward("106", "203")
    if door:
        far = state("106", x=door.x - 2000, z=door.z)
        near = state("106", x=door.x - 1500, z=door.z, step=2)
        _, bd4 = compute_reward(far, near, p4, progress=ProgressTracker(),
                                graph=graph, return_breakdown=True)
        print(f"  goal=203, 500-unit step toward door in 106: "
              f"pbrs_door={bd4['pbrs_door']:+.3f} raw, scaled={(sum(bd4.values()))*0.1:+.4f}")
    # crossing 106 -> 203 itself
    _, bdx = compute_reward(state("106"), state("203"), p4,
                            progress=ProgressTracker(), graph=graph,
                            return_breakdown=True)
    print(f"  goal=203, transition 106->203: pbrs_graph={bdx['pbrs_graph']:+.2f} "
          f"waypoint={bdx['waypoint']:+.2f} total_scaled={sum(bdx.values())*0.1:+.3f}")


probe(g_blind, "BLIND graph (2 edges, pre-harvest)")
probe(g_full, "FULL graph (18 edges, post-harvest)")
print("SHAPING_PROBE_DONE")
