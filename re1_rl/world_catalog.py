"""Static mansion almanac tensors built once from JSON data files.

These buffers are **not** shipped in worker rollouts. The learner/policy rebuilds
them via ``register_buffer`` using ``WorldCatalog.as_torch_buffers()``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from re1_rl.item_todo import RoomItems, canonical_item
from re1_rl.key_items import KEY_ITEM_NAMES
from re1_rl.memory_map import ITEM_IDS
from re1_rl.room_graph import RoomGraph

NUM_ROOMS = 128
PAD_ROOM = 127
MAX_NEIGHBORS = 6
MAX_ITEM_ID = 0x4B

_NAME_TO_ITEM_ID = {name: iid for iid, name in ITEM_IDS.items()}
_KEY_NAME_TO_INDEX = {name: i for i, name in enumerate(KEY_ITEM_NAMES)}

GATE_NONE = 0
GATE_ITEM = 1
GATE_PUZZLE = 2
GATE_EVENT = 3
GATE_TRAP = 4
_GATE_TYPE = {
    "item": GATE_ITEM,
    "puzzle": GATE_PUZZLE,
    "event": GATE_EVENT,
    "trap": GATE_TRAP,
}

CAT_KEY = 0
CAT_RECOVERY = 1
CAT_AMMO = 2
CAT_WEAPON = 3
CAT_FILE = 4
CAT_MISC = 5
_CAT_NAME = {
    "key": CAT_KEY,
    "recovery": CAT_RECOVERY,
    "ammo": CAT_AMMO,
    "weapon": CAT_WEAPON,
    "file": CAT_FILE,
    "misc": CAT_MISC,
}


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _room_table(rooms_path: Path) -> dict[str, int]:
    rooms = _load_json(rooms_path)
    return {rid: i for i, rid in enumerate(sorted(rooms.keys()))}


def _room_idx(room_index: dict[str, int], room_id: str | None) -> int:
    if not room_id:
        return PAD_ROOM
    return int(room_index.get(str(room_id), PAD_ROOM))


def _item_id_for_name(name: str) -> int:
    return int(_NAME_TO_ITEM_ID.get(canonical_item(name), 0))


@dataclass
class WorldCatalog:
    """Frozen Evil Resource almanac for the 116-room mansion table (+ pad)."""

    room_index: dict[str, int] = field(default_factory=dict)
    room_items_path: Path | None = None
    num_pickups: int = 0
    num_keys: int = 0
    num_files: int = 0
    num_combine: int = 0
    file_code_width: int = 0

    map_neighbors: np.ndarray = field(
        default_factory=lambda: np.full((NUM_ROOMS, MAX_NEIGHBORS), PAD_ROOM, dtype=np.float32)
    )
    map_degree: np.ndarray = field(default_factory=lambda: np.zeros(NUM_ROOMS, dtype=np.float32))
    room_area: np.ndarray = field(default_factory=lambda: np.zeros(NUM_ROOMS, dtype=np.float32))
    room_stage: np.ndarray = field(default_factory=lambda: np.zeros(NUM_ROOMS, dtype=np.float32))

    pickup_room_idx: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    pickup_item_id: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    pickup_category: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    pickup_key_flag: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    pickup_gate_type: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    pickup_requires_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float32))

    key_pickup_room: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    key_use_room: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    key_unlock_room: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    key_door_from: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    key_item_id: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))

    link_requires_key: np.ndarray = field(
        default_factory=lambda: np.full((NUM_ROOMS, MAX_NEIGHBORS), PAD_ROOM, dtype=np.float32)
    )

    file_room_idx: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    file_id: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    file_code_const: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float32))

    combine_src_a: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    combine_src_b: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    combine_dst: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))

    @classmethod
    def from_files(cls, project_root: str | Path) -> WorldCatalog:
        root = Path(project_root)
        data = root / "data"
        rooms_path = data / "rooms.json"
        room_index = _room_table(rooms_path)
        rooms_meta = _load_json(rooms_path)

        graph = RoomGraph(data / "doors_empirical.json", data / "doors_rdt.json")
        room_areas = _load_json(data / "room_areas.json")
        item_categories = _load_json(data / "item_categories.json")
        affordances = _load_json(data / "item_affordances.json")
        er_files = _load_json(data / "er_files.json")
        combine_recipes = _load_json(data / "combine_recipes.json")
        room_items_path = data / "room_items.json"
        room_items = RoomItems(room_items_path)

        cat = cls(room_index=room_index, room_items_path=room_items_path)
        cat._build_topology(graph, room_index)
        cat._build_room_tags(rooms_meta, room_areas, room_index)
        cat._build_pickups(room_items, item_categories, room_index)
        cat._build_key_buffers(affordances, room_index)
        cat._build_link_requires_key(affordances, room_index)
        cat._build_files(er_files, room_index)
        cat._build_combine(combine_recipes)
        return cat

    def _build_topology(self, graph: RoomGraph, room_index: dict[str, int]) -> None:
        inv = {idx: rid for rid, idx in room_index.items()}
        for idx in range(NUM_ROOMS):
            if idx not in inv:
                continue
            room = inv[idx]
            nbrs = sorted(n for n in graph.adj.get(room, ()) if n in room_index)[:MAX_NEIGHBORS]
            self.map_neighbors[idx, : len(nbrs)] = [room_index[n] for n in nbrs]
            self.map_degree[idx] = float(len(nbrs))

    def _build_room_tags(
        self,
        rooms_meta: dict[str, Any],
        room_areas: dict[str, Any],
        room_index: dict[str, int],
    ) -> None:
        for room_id, idx in room_index.items():
            meta = rooms_meta.get(room_id, {})
            area = room_areas.get(room_id, {})
            self.room_area[idx] = float(area.get("area_id", 0))
            stage = int(meta.get("stage", 1))
            self.room_stage[idx] = float(stage) / 7.0

    def _build_pickups(
        self,
        room_items: RoomItems,
        item_categories: dict[str, str],
        room_index: dict[str, int],
    ) -> None:
        rows: list[dict[str, Any]] = []
        for room_id in sorted(room_items.rooms):
            for item in room_items.items_in_room(room_id):
                rows.append({"room_id": room_id, **item})

        n = len(rows)
        k = len(KEY_ITEM_NAMES)
        self.num_pickups = n
        self.num_keys = k

        self.pickup_room_idx = np.full(n, PAD_ROOM, dtype=np.float32)
        self.pickup_item_id = np.zeros(n, dtype=np.float32)
        self.pickup_category = np.zeros(n, dtype=np.float32)
        self.pickup_key_flag = np.zeros(n, dtype=np.float32)
        self.pickup_gate_type = np.zeros(n, dtype=np.float32)
        self.pickup_requires_mask = np.zeros((n, k), dtype=np.float32)

        for i, row in enumerate(rows):
            name = str(row.get("name", ""))
            self.pickup_room_idx[i] = float(_room_idx(room_index, row["room_id"]))
            iid = int(row.get("item_id") or _item_id_for_name(name))
            self.pickup_item_id[i] = float(iid)
            cat_name = item_categories.get(name, "misc")
            self.pickup_category[i] = float(_CAT_NAME.get(cat_name, CAT_MISC))
            self.pickup_key_flag[i] = 1.0 if row.get("key_item") else 0.0
            gate = row.get("gate") or {}
            gtype = str(gate.get("type", "")).lower()
            self.pickup_gate_type[i] = float(_GATE_TYPE.get(gtype, GATE_NONE))
            for req in gate.get("requires", []):
                cname = canonical_item(str(req))
                if cname in _KEY_NAME_TO_INDEX:
                    self.pickup_requires_mask[i, _KEY_NAME_TO_INDEX[cname]] = 1.0

    def _build_key_buffers(self, affordances: dict[str, Any], room_index: dict[str, int]) -> None:
        k = len(KEY_ITEM_NAMES)
        self.key_pickup_room = np.full(k, PAD_ROOM, dtype=np.float32)
        self.key_use_room = np.full(k, PAD_ROOM, dtype=np.float32)
        self.key_unlock_room = np.full(k, PAD_ROOM, dtype=np.float32)
        self.key_door_from = np.full(k, PAD_ROOM, dtype=np.float32)
        self.key_item_id = np.zeros(k, dtype=np.float32)

        for i, name in enumerate(KEY_ITEM_NAMES):
            self.key_item_id[i] = float(_item_id_for_name(name))
            entry = affordances.get(name, {})
            pickups = entry.get("pickup_rooms") or []
            if pickups:
                self.key_pickup_room[i] = float(_room_idx(room_index, pickups[0]))
            use_rooms = entry.get("use_rooms") or []
            if use_rooms:
                self.key_use_room[i] = float(_room_idx(room_index, use_rooms[0]))
            edges = entry.get("door_edges") or []
            if edges:
                edge = edges[0]
                self.key_door_from[i] = float(_room_idx(room_index, edge.get("from_room")))
                self.key_unlock_room[i] = float(_room_idx(room_index, edge.get("to_room")))
            elif use_rooms:
                self.key_unlock_room[i] = float(_room_idx(room_index, use_rooms[0]))

    def _build_link_requires_key(self, affordances: dict[str, Any], room_index: dict[str, int]) -> None:
        edge_to_key: dict[tuple[str, str], int] = {}
        for key_name, entry in affordances.items():
            if key_name not in _KEY_NAME_TO_INDEX:
                continue
            kid = _KEY_NAME_TO_INDEX[key_name]
            for edge in entry.get("door_edges") or []:
                fr = str(edge.get("from_room", ""))
                to = str(edge.get("to_room", ""))
                if fr and to:
                    edge_to_key[(fr, to)] = kid

        inv = {v: k for k, v in room_index.items()}
        for idx in range(NUM_ROOMS):
            for slot in range(MAX_NEIGHBORS):
                nidx = int(self.map_neighbors[idx, slot])
                if nidx == PAD_ROOM:
                    continue
                fr = inv.get(idx)
                to = inv.get(nidx)
                if fr is None or to is None:
                    continue
                kid = edge_to_key.get((fr, to))
                if kid is not None:
                    self.link_requires_key[idx, slot] = float(kid)

    def _build_files(self, er_files: list[dict[str, Any]], room_index: dict[str, int]) -> None:
        f = len(er_files)
        max_codes = max((len(row.get("codes") or []) for row in er_files), default=0)
        self.num_files = f
        self.file_code_width = max_codes
        self.file_room_idx = np.full(f, PAD_ROOM, dtype=np.float32)
        self.file_id = np.zeros(f, dtype=np.float32)
        self.file_code_const = np.zeros((f, max_codes), dtype=np.float32)
        for i, row in enumerate(er_files):
            name = canonical_item(str(row.get("name", "")))
            self.file_room_idx[i] = float(_room_idx(room_index, str(row.get("room", ""))))
            self.file_id[i] = float(_item_id_for_name(name))
            codes = [float(c) for c in (row.get("codes") or [])]
            if codes:
                self.file_code_const[i, : len(codes)] = codes

    def _build_combine(self, combine_recipes: list[dict[str, Any]]) -> None:
        r = len(combine_recipes)
        self.num_combine = r
        self.combine_src_a = np.zeros(r, dtype=np.float32)
        self.combine_src_b = np.zeros(r, dtype=np.float32)
        self.combine_dst = np.zeros(r, dtype=np.float32)
        for i, row in enumerate(combine_recipes):
            self.combine_src_a[i] = float(int(row.get("a", 0)))
            self.combine_src_b[i] = float(int(row.get("b", 0)))
            dst = int(row.get("dst", 0))
            self.combine_dst[i] = float(min(dst, MAX_ITEM_ID))

    def _iter_pickup_rows(self) -> list[dict[str, Any]]:
        if self.room_items_path is None:
            return []
        ri = RoomItems(self.room_items_path)
        rows: list[dict[str, Any]] = []
        for room_id in sorted(ri.rooms):
            for item in ri.items_in_room(room_id):
                rows.append(item)
        return rows

    def pickup_active_mask(
        self,
        ever_held: set[str] | frozenset[str],
        obtainable_fn: Callable[[dict[str, Any], set[str]], bool] | None = None,
    ) -> np.ndarray:
        """1.0 for catalog pickups still obtainable and not yet ever-held."""
        held = {canonical_item(n) for n in ever_held}
        if obtainable_fn is None:
            obtainable_fn = RoomItems._obtainable

        mask = np.zeros(self.num_pickups, dtype=np.float32)
        for i, item in enumerate(self._iter_pickup_rows()):
            name = str(item.get("name", ""))
            if name in held:
                continue
            if obtainable_fn(item, held):
                mask[i] = 1.0
        return mask

    def as_torch_buffers(self) -> dict[str, Any]:
        """Return tensors suitable for ``nn.Module.register_buffer``."""
        try:
            import torch
        except ImportError as exc:  # pragma: no cover
            raise ImportError("torch is required for as_torch_buffers()") from exc

        out: dict[str, Any] = {}
        for name in (
            "map_neighbors",
            "map_degree",
            "room_area",
            "room_stage",
            "pickup_room_idx",
            "pickup_item_id",
            "pickup_category",
            "pickup_key_flag",
            "pickup_gate_type",
            "pickup_requires_mask",
            "key_pickup_room",
            "key_use_room",
            "key_unlock_room",
            "key_door_from",
            "key_item_id",
            "link_requires_key",
            "file_room_idx",
            "file_id",
            "file_code_const",
            "combine_src_a",
            "combine_src_b",
            "combine_dst",
        ):
            out[name] = torch.from_numpy(getattr(self, name))
        return out
