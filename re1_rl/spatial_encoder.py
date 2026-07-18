"""Egocentric spatial observation: items, enemies, exits + visited mask.

Same named-field discipline as obs_encoder: every slot in the `spatial`
vector has a NAME, so encoding, pretty-printing and the overlay never
disagree. See docs/privileged_obs_spec.md for provenance and semantics.

Purity note (docs/memory_hooks_and_observation_design.md sec. 0): everything
here is a SENSOR. Positions come from data/item_positions.json (empirical
pickups + RDT later), live enemy reads, and the harvested door table. The
policy still has to learn which buttons to press.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from re1_rl.item_affordances import door_requires_key_index
from re1_rl.item_todo import ItemTracker, RoomItems, canonical_item
from re1_rl.memory_map import ITEM_IDS
from re1_rl.rdt_interactables import kind_id, load_rdt_interactables
from re1_rl.room_graph import RoomGraph

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ROOMS = _ROOT / "data" / "rooms.json"

FACING_FULL_CIRCLE = 4096.0
DIST_NORM = 4096.0
MAX_ITEM_ID = 0x4B  # highest mixed-herb id (G+G+B)
MAX_ENEMY_TYPE = 32.0

ITEM_SLOTS = 8
ENEMY_SLOTS = 5
EXIT_SLOTS = 4
INTERACTABLE_SLOTS = 2

_ITEM_SLOT_FIELDS = [
    ("rel_x", "(item_x - player_x) / 4096, clip [-2,2]; 0 if position unknown"),
    ("rel_z", "(item_z - player_z) / 4096, clip [-2,2]; 0 if position unknown"),
    ("dist", "euclidean distance / 4096, clip [0,2]"),
    ("bearing_sin", "sin(angle to item - facing); + = item to the left"),
    ("bearing_cos", "cos(angle to item - facing); 1 = dead ahead"),
    ("item_id", "inventory item id / 0x46 (0 = non-inventory/unknown)"),
    ("key_item", "1 = key item"),
    ("gated", "1 = present but locked behind tracked requirements"),
]
_ENEMY_SLOT_FIELDS = [
    ("rel_x", "(enemy_x - player_x) / 4096, clip [-2,2]"),
    ("rel_z", "(enemy_z - player_z) / 4096, clip [-2,2]"),
    ("dist", "euclidean distance / 4096, clip [0,2]"),
    ("bearing_sin", "sin(angle to enemy - facing); + = to the left"),
    ("bearing_cos", "cos(angle to enemy - facing); 1 = dead ahead"),
    ("type_id", "enemy type id / 32"),
    ("hp", "enemy hp / 255"),
    ("alive", "1 = alive"),
]
_EXIT_SLOT_FIELDS = [
    ("bearing_sin", "sin(angle to exit door - facing)"),
    ("bearing_cos", "cos(angle to exit door - facing)"),
    ("dist", "euclidean distance / 4096, clip [0,2]"),
    ("to_room", "destination room_index / 128"),
    ("known", "1 = harvested door edge in graph"),
    ("requires_key", "KEY_ITEM_NAMES index / 128, or 127/128 if none"),
]
EXIT_SLOT_DIM = len(_EXIT_SLOT_FIELDS)
_INTERACTABLE_SLOT_FIELDS = [
    ("bearing_sin", "sin(angle to interactable - facing)"),
    ("bearing_cos", "cos(angle to interactable - facing)"),
    ("dist", "euclidean distance / 4096, clip [0,2]"),
    ("kind_id", "item_box/typewriter/trigger id / 3"),
]


def _build_fields() -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [
        ("items_obtainable_here", "obtainable never-held pickups in room / 8"),
    ]
    for i in range(ITEM_SLOTS):
        fields.extend((f"item{i}_{n}", d) for n, d in _ITEM_SLOT_FIELDS)
    fields.append(("enemy_count", "alive enemies in room / 10 (0 until RAM hook)"))
    for i in range(ENEMY_SLOTS):
        fields.extend((f"enemy{i}_{n}", d) for n, d in _ENEMY_SLOT_FIELDS)
    fields.append(("num_known_exits", "harvested exits from this room / 8"))
    for i in range(EXIT_SLOTS):
        fields.extend((f"exit{i}_{n}", d) for n, d in _EXIT_SLOT_FIELDS)
    fields.append(("interactables_here", "RDT box/typewriter/trigger in room / 8"))
    for i in range(INTERACTABLE_SLOTS):
        fields.extend((f"interactable{i}_{n}", d) for n, d in _INTERACTABLE_SLOT_FIELDS)
    return fields


SPATIAL_FIELDS: list[tuple[str, str]] = _build_fields()
SPATIAL_DIM = len(SPATIAL_FIELDS)  # 128 + 4 exits * 3 new fields = 140

_NAME_TO_ITEM_ID = {name: iid for iid, name in ITEM_IDS.items()}


@lru_cache(maxsize=1)
def load_room_index(rooms_path: str = str(_DEFAULT_ROOMS)) -> dict[str, int]:
    """Stable alphanumeric room code -> index (matches obs_encoder / rooms.json)."""
    p = Path(rooms_path)
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8") as f:
        rooms = json.load(f)
    return {rid: i for i, rid in enumerate(sorted(rooms.keys()))}


def _room_norm(room_id: str, room_index: dict[str, int]) -> float:
    return room_index.get(str(room_id), 127) / 128.0

# Visited mask: per-room 16x16 allocentric grid anchored on the first pose
# seen in the room this episode (PokeRL-style; progress_scaffolding sec. 1.6).
VISITED_GRID = 16
VISITED_CELL_UNITS = 256.0  # world units per cell -> grid spans +-2048
VISITED_SHAPE = (VISITED_GRID, VISITED_GRID, 1)


class ItemPositions:
    """data/item_positions.json wrapper; keyed "room:item" -> {x, z, ...}.

    Built by scripts/build_item_positions.py from pickups_empirical.json
    (ground truth) plus manual anchors. Degrades to empty if missing.
    """

    def __init__(self, path: str | Path) -> None:
        self.positions: dict[tuple[str, str], tuple[float, float]] = {}
        p = Path(path)
        if p.is_file():
            with p.open(encoding="utf-8") as f:
                raw = json.load(f)
            for key, entry in raw.items():
                if key.startswith("_"):
                    continue
                room, _, item = key.partition(":")
                self.positions[(room, canonical_item(item))] = (
                    float(entry["x"]), float(entry["z"]),
                )

    @property
    def loaded(self) -> bool:
        return bool(self.positions)

    def get(self, room_id: str, item_name: str) -> tuple[float, float] | None:
        return self.positions.get((str(room_id), canonical_item(item_name)))


class VisitedMask:
    """Per-episode, per-room visited-cell grid. Reset every episode."""

    def __init__(self) -> None:
        self._anchors: dict[str, tuple[float, float]] = {}
        self._masks: dict[str, np.ndarray] = {}

    def reset(self) -> None:
        self._anchors.clear()
        self._masks.clear()

    def _cell(self, room: str, x: float, z: float) -> tuple[int, int]:
        ax, az = self._anchors[room]
        half = VISITED_GRID // 2
        cx = int(np.clip((x - ax) // VISITED_CELL_UNITS + half, 0, VISITED_GRID - 1))
        cz = int(np.clip((z - az) // VISITED_CELL_UNITS + half, 0, VISITED_GRID - 1))
        return cz, cx  # row = z, col = x

    def update(self, room_id: str, x: float, z: float) -> bool:
        """Mark the cell under the player; True if the cell was new."""
        room = str(room_id)
        if room not in self._anchors:
            self._anchors[room] = (float(x), float(z))
            self._masks[room] = np.zeros((VISITED_GRID, VISITED_GRID), dtype=np.float32)
        r, c = self._cell(room, float(x), float(z))
        new = self._masks[room][r, c] == 0.0
        self._masks[room][r, c] = 1.0
        return bool(new)

    def plane(self, room_id: str) -> np.ndarray:
        mask = self._masks.get(str(room_id))
        if mask is None:
            return np.zeros(VISITED_SHAPE, dtype=np.float32)
        return mask[..., None]


def _egocentric(px: float, pz: float, theta: float,
                tx: float, tz: float) -> tuple[float, float, float, float, float]:
    """(rel_x, rel_z, dist, bearing_sin, bearing_cos), all normalized."""
    dx = (tx - px) / DIST_NORM
    dz = (tz - pz) / DIST_NORM
    dist = float(np.clip(math.hypot(dx, dz), 0.0, 2.0))
    bearing = math.atan2(dz, dx) - theta
    return (
        float(np.clip(dx, -2.0, 2.0)),
        float(np.clip(dz, -2.0, 2.0)),
        dist,
        math.sin(bearing),
        math.cos(bearing),
    )


class StaticEnemySpawns:
    """RDT / room_enemies.json spawn coords when live RAM table is unmapped."""

    def __init__(self, path: str | Path) -> None:
        self.spawns: dict[str, list[dict[str, Any]]] = {}
        p = Path(path)
        if not p.is_file():
            return
        with p.open(encoding="utf-8") as f:
            raw = json.load(f)
        for room_id, block in raw.items():
            if room_id.startswith("_") or not isinstance(block, dict):
                continue
            rows = []
            for e in block.get("enemies", []):
                if "x" not in e or "z" not in e:
                    continue
                rows.append({
                    "x": float(e["x"]),
                    "z": float(e["z"]),
                    "type_id": float(e.get("model_id", 1)),
                    "hp": 255.0,
                    "alive": True,
                })
            if rows:
                self.spawns[str(room_id)] = rows

    @property
    def loaded(self) -> bool:
        return bool(self.spawns)

    def for_room(self, room_id: str) -> list[dict[str, Any]]:
        return list(self.spawns.get(str(room_id), []))


class SpatialEncoder:
    """state dict (+ static tables) -> named `spatial` vector."""

    def __init__(
        self,
        item_positions: ItemPositions | None = None,
        graph: RoomGraph | None = None,
        static_enemies: StaticEnemySpawns | None = None,
        interactables: dict[str, list[dict[str, Any]]] | None = None,
        *,
        room_index: dict[str, int] | None = None,
        rooms_path: str | Path | None = None,
    ) -> None:
        self.item_positions = item_positions
        self.graph = graph
        self.static_enemies = static_enemies
        self.interactables = interactables if interactables is not None else load_rdt_interactables()
        if room_index is not None:
            self.room_index = room_index
        elif rooms_path is not None:
            self.room_index = load_room_index(str(rooms_path))
        else:
            self.room_index = load_room_index()
        self._door_requires_key = door_requires_key_index()

    def encode(
        self,
        state: dict[str, Any],
        room_items: RoomItems | None = None,
        item_tracker: ItemTracker | None = None,
    ) -> np.ndarray:
        v = np.zeros(SPATIAL_DIM, dtype=np.float32)
        px = float(state.get("x", 0))
        pz = float(state.get("z", 0))
        theta = 2.0 * math.pi * float(state.get("facing", 0)) / FACING_FULL_CIRCLE
        room = str(state.get("room_id", ""))

        i = self._encode_items(v, 0, room, px, pz, theta, room_items, item_tracker)
        i = self._encode_enemies(v, i, state, px, pz, theta)
        i = self._encode_exits(v, i, room, px, pz, theta)
        self._encode_interactables(v, i, room, px, pz, theta)
        return v

    # --- items ---

    def _visible_items(
        self,
        room: str,
        room_items: RoomItems | None,
        item_tracker: ItemTracker | None,
    ) -> list[tuple[dict[str, Any], bool]]:
        """[(item, gated)] never-held items the agent may reason about.

        Obtainable items always show. Gated items show ONLY when their gate
        names concrete requirements we track (item names) — puzzle/event
        gates with empty requires stay hidden until SCD flags are wired
        (conservative rule from docs/item_gates.md).
        """
        if room_items is None or not room_items.loaded:
            return []
        held = item_tracker.ever_held if item_tracker is not None else set()
        out: list[tuple[dict[str, Any], bool]] = []
        for it in room_items.items_in_room(room):
            if str(it.get("name", "")) in held:
                continue
            if RoomItems._obtainable(it, held):
                out.append((it, False))
            elif (it.get("gate") or {}).get("requires"):
                out.append((it, True))
        return out

    def _encode_items(
        self,
        v: np.ndarray,
        i: int,
        room: str,
        px: float,
        pz: float,
        theta: float,
        room_items: RoomItems | None,
        item_tracker: ItemTracker | None,
    ) -> int:
        visible = self._visible_items(room, room_items, item_tracker)
        v[i] = min(sum(1 for _, gated in visible if not gated), 8) / 8.0
        i += 1

        # resolve positions; items with known coords sort nearest-first,
        # unknown-position items go last (still expose id/key/gated bits)
        rows: list[tuple[float, dict[str, Any], bool, tuple[float, float] | None]] = []
        for it, gated in visible:
            pos = (self.item_positions.get(room, it.get("name", ""))
                   if self.item_positions is not None else None)
            if pos is not None:
                d = math.hypot(pos[0] - px, pos[1] - pz)
                rows.append((d, it, gated, pos))
            else:
                rows.append((float("inf"), it, gated, None))
        rows.sort(key=lambda r: r[0])

        for _, it, gated, pos in rows[:ITEM_SLOTS]:
            if pos is not None:
                v[i:i + 5] = _egocentric(px, pz, theta, pos[0], pos[1])
            item_id = _NAME_TO_ITEM_ID.get(canonical_item(it.get("name", "")), 0)
            v[i + 5] = item_id / MAX_ITEM_ID
            v[i + 6] = 1.0 if it.get("key_item") else 0.0
            v[i + 7] = 1.0 if gated else 0.0
            i += 8
        return 1 + ITEM_SLOTS * 8

    # --- enemies ---

    def _encode_enemies(
        self,
        v: np.ndarray,
        i: int,
        state: dict[str, Any],
        px: float,
        pz: float,
        theta: float,
    ) -> int:
        enemies: list[dict[str, Any]] = list(state.get("enemies", []) or [])
        if not enemies and self.static_enemies is not None:
            enemies = self.static_enemies.for_room(str(state.get("room_id", "")))
        alive = [e for e in enemies if e.get("alive", True)]
        v[i] = min(len(alive), 10) / 10.0
        i += 1

        alive.sort(key=lambda e: math.hypot(float(e.get("x", 0)) - px,
                                            float(e.get("z", 0)) - pz))
        for e in alive[:ENEMY_SLOTS]:
            v[i:i + 5] = _egocentric(px, pz, theta,
                                     float(e.get("x", 0)), float(e.get("z", 0)))
            v[i + 5] = float(e.get("type_id", 0)) / MAX_ENEMY_TYPE
            v[i + 6] = float(np.clip(float(e.get("hp", 0)) / 255.0, 0.0, 1.0))
            v[i + 7] = 1.0
            i += 8
        return 1 + ITEM_SLOTS * 8 + 1 + ENEMY_SLOTS * 8

    # --- exits ---

    def _encode_exits(
        self,
        v: np.ndarray,
        i: int,
        room: str,
        px: float,
        pz: float,
        theta: float,
    ) -> int:
        if self.graph is None:
            return i + 1 + EXIT_SLOTS * EXIT_SLOT_DIM
        doors = [d for (frm, _), d in self.graph.doors.items() if frm == room]
        v[i] = min(len(doors), 8) / 8.0
        i += 1
        doors.sort(key=lambda d: math.hypot(d.x - px, d.z - pz))
        for d in doors[:EXIT_SLOTS]:
            _, _, dist, bsin, bcos = _egocentric(px, pz, theta, d.x, d.z)
            v[i] = bsin
            v[i + 1] = bcos
            v[i + 2] = dist
            v[i + 3] = _room_norm(d.to_room, self.room_index)
            v[i + 4] = 1.0  # edge present in harvested graph
            key_idx = self._door_requires_key.get((d.from_room, d.to_room))
            v[i + 5] = (key_idx if key_idx is not None else 127) / 128.0
            i += EXIT_SLOT_DIM
        return i

    def _encode_interactables(
        self,
        v: np.ndarray,
        i: int,
        room: str,
        px: float,
        pz: float,
        theta: float,
    ) -> None:
        rows = list(self.interactables.get(str(room), []))
        v[i] = min(len(rows), 8) / 8.0
        i += 1
        rows.sort(key=lambda r: math.hypot(float(r["x"]) - px, float(r["z"]) - pz))
        for row in rows[:INTERACTABLE_SLOTS]:
            _, _, dist, bsin, bcos = _egocentric(
                px, pz, theta, float(row["x"]), float(row["z"]),
            )
            v[i] = bsin
            v[i + 1] = bcos
            v[i + 2] = dist
            v[i + 3] = kind_id(str(row.get("kind", "")))
            i += 4
