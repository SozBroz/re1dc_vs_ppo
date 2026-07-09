"""Unknown-room PBRS plateau must scale with the known-graph diameter so a
richer door graph never makes unmapped rooms cheaper than mapped ones."""
import json

from re1_rl.planner import WaypointPlanner
from re1_rl.reward import UNKNOWN_HOPS, potential
from re1_rl.room_graph import RoomGraph


def chain_graph(tmp_path, n_rooms: int) -> RoomGraph:
    rooms = [f"R{i}" for i in range(n_rooms)]
    doors = {}
    for a, b in zip(rooms, rooms[1:]):
        doors[f"{a}->{b}"] = {
            "from_room": a, "to_room": b, "door_x": 0, "door_z": 0,
        }
    p = tmp_path / "doors.json"
    p.write_text(json.dumps(doors), encoding="utf-8")
    return RoomGraph(p)


def make_planner(tmp_path, goal: str) -> WaypointPlanner:
    return WaypointPlanner(tmp_path / "no_route.json", waypoints=[goal])


def test_diameter_of_chain(tmp_path):
    assert chain_graph(tmp_path, 8).diameter == 7
    assert chain_graph(tmp_path, 2).diameter == 1


def test_unknown_plateau_small_graph_keeps_floor(tmp_path, monkeypatch):
    monkeypatch.setattr("re1_rl.reward.ENABLE_CHECKPOINT_PATH", True)
    g = chain_graph(tmp_path, 3)  # diameter 2 << UNKNOWN_HOPS
    planner = make_planner(tmp_path, "R2")
    phi_g, _ = potential({"room_id": "OFFMAP"}, planner, g)
    from re1_rl.reward import PBRS_GRAPH_WEIGHT
    assert phi_g == -UNKNOWN_HOPS * PBRS_GRAPH_WEIGHT


def test_unknown_plateau_tracks_large_graph(tmp_path, monkeypatch):
    monkeypatch.setattr("re1_rl.reward.ENABLE_CHECKPOINT_PATH", True)
    n = int(UNKNOWN_HOPS) + 4  # diameter n-1 > UNKNOWN_HOPS
    g = chain_graph(tmp_path, n)
    planner = make_planner(tmp_path, f"R{n - 1}")
    phi_unknown, _ = potential({"room_id": "OFFMAP"}, planner, g)
    # strictly worse than the farthest mapped room
    phi_far, _ = potential({"room_id": "R0"}, planner, g)
    assert phi_unknown < phi_far
    from re1_rl.reward import PBRS_GRAPH_WEIGHT
    assert phi_unknown == -(g.diameter + 2.0) * PBRS_GRAPH_WEIGHT
