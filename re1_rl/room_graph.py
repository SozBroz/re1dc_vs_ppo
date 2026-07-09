"""Room adjacency graph + door coordinate table.

Built from data/doors_empirical.json (grows as the route is logged). BFS hop
distance feeds the goal obs vector and the graph PBRS potential.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Door:
    from_room: str
    to_room: str
    x: int
    z: int
    entry_x: int | None = None
    entry_z: int | None = None
    entry_facing: int | None = None


class RoomGraph:
    def __init__(
        self,
        doors_path: str | Path,
        doors_rdt_path: str | Path | None = None,
    ) -> None:
        self.doors: dict[tuple[str, str], Door] = {}
        self.adj: dict[str, set[str]] = {}
        self._diameter: int | None = None
        self._load_doors_file(Path(doors_path))
        if doors_rdt_path is not None:
            self._load_doors_file(Path(doors_rdt_path), fallback=True)

    def _load_doors_file(self, path: Path, *, fallback: bool = False) -> None:
        if not path.is_file():
            return
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
        for key, d in raw.items():
            if key.startswith("_"):
                continue
            door = Door(
                from_room=str(d["from_room"]),
                to_room=str(d["to_room"]),
                x=int(d["door_x"]),
                z=int(d["door_z"]),
                entry_x=int(d["entry_x"]) if "entry_x" in d else None,
                entry_z=int(d["entry_z"]) if "entry_z" in d else None,
                entry_facing=int(d["entry_facing"]) if "entry_facing" in d else None,
            )
            edge = (door.from_room, door.to_room)
            if fallback and edge in self.doors:
                continue
            self.doors[edge] = door
            self.adj.setdefault(door.from_room, set()).add(door.to_room)

    def get_exit(self, from_room: str, to_room: str | None) -> Door | None:
        """Door in from_room leading to to_room (exact edge only, v1)."""
        if to_room is None:
            return None
        return self.doors.get((str(from_room), str(to_room)))

    def exit_toward(self, from_room: str, goal_room: str | None) -> Door | None:
        """Door in from_room on the shortest path toward goal_room."""
        if goal_room is None:
            return None
        hop = self.next_hop(from_room, goal_room)
        return self.get_exit(from_room, hop) if hop else None

    def next_hop(self, from_room: str, goal_room: str) -> str | None:
        """First room on the BFS shortest path from_room -> goal_room."""
        from_room, goal_room = str(from_room), str(goal_room)
        if from_room == goal_room:
            return None
        parents: dict[str, str] = {from_room: from_room}
        q = deque([from_room])
        while q:
            cur = q.popleft()
            for nxt in self.adj.get(cur, ()):
                if nxt in parents:
                    continue
                parents[nxt] = cur
                if nxt == goal_room:
                    # walk back to the first hop
                    node = nxt
                    while parents[node] != from_room:
                        node = parents[node]
                    return node
                q.append(nxt)
        return None

    def knows_room(self, room_id: str) -> bool:
        """True when the room appears anywhere in the harvested door graph."""
        room = str(room_id)
        if room in self.adj:
            return True
        return any(room in nbrs for nbrs in self.adj.values())

    @property
    def diameter(self) -> int:
        """Max finite BFS hop distance over known rooms (cached at load)."""
        if self._diameter is None:
            best = 0
            for src in self.adj:
                dist = {src: 0}
                q = deque([src])
                while q:
                    cur = q.popleft()
                    for nxt in self.adj.get(cur, ()):
                        if nxt in dist:
                            continue
                        dist[nxt] = dist[cur] + 1
                        q.append(nxt)
                best = max(best, max(dist.values(), default=0))
            self._diameter = best
        return self._diameter

    def hop_distance(self, from_room: str, goal_room: str | None) -> int | None:
        """BFS hops from_room -> goal_room; None if unreachable/unknown."""
        if goal_room is None:
            return None
        from_room, goal_room = str(from_room), str(goal_room)
        if from_room == goal_room:
            return 0
        seen = {from_room}
        q = deque([(from_room, 0)])
        while q:
            cur, d = q.popleft()
            for nxt in self.adj.get(cur, ()):
                if nxt in seen:
                    continue
                if nxt == goal_room:
                    return d + 1
                seen.add(nxt)
                q.append((nxt, d + 1))
        return None
