"""Key-item TODO list + per-room remaining-item tracking.

Two data sources:
  - data/route_jill_anypct.json  -> ordered key-item checklist (what the run
    must acquire, in route order, with the room it comes from)
  - data/room_items.json         -> everything obtainable per room (Evil
    Resource dump), used for "items left in this room" counts

Acquisition tracking uses the EVER-HELD set (items seen in inventory at any
point this episode), so banking/using an item doesn't un-check it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Route JSON / community walkthroughs use informal names; RAM decode uses
# memory_map.ITEM_IDS names. Normalize everything to the ITEM_IDS spelling.
# NOTE: the three MO discs collapse to one name -- ever-held tracking checks
# all MO-disc TODO entries after the first pickup (RAM has no per-disc id).
ITEM_ALIASES: dict[str, str] = {
    "wooden_emblem": "emblem",
    "handgun_clips": "clip",
    "handgun_clip": "clip",
    "piano_notes": "music_notes",
    "room_002_key": "dorm_key_002",
    "room_003_key": "dorm_key_003",
    "mo_disk": "mo_disc",
    "mo_disk_1": "mo_disc",
    "mo_disk_2": "mo_disc",
    "mo_disk_3": "mo_disc",
    # power room key is the item ITEM_IDS calls lab_key_1
    "power_room_key": "lab_key_1",
    # the two lab passcodes are both "pass_number" printouts from the MO
    # terminal; like the MO discs they collapse to one trackable name
    "passcode_a": "pass_number",
    "passcode_b": "pass_number",
}


def canonical_item(name: str) -> str:
    return ITEM_ALIASES.get(str(name), str(name))


def canonicalize(names: Iterable[str]) -> set[str]:
    return {canonical_item(n) for n in names}


@dataclass
class TodoEntry:
    seq: int
    room_id: str
    item: str
    action_type: str
    objective: str
    required_items: list[str]


def build_item_todo(route_path: str | Path) -> list[TodoEntry]:
    """Ordered list of every item the route acquires (the key-item TODO)."""
    with Path(route_path).open(encoding="utf-8") as f:
        route = json.load(f)
    todo: list[TodoEntry] = []
    for step in route:
        for item in step.get("items_gained", []):
            todo.append(TodoEntry(
                seq=int(step.get("seq", 0)),
                room_id=str(step.get("room_id", "")),
                item=canonical_item(item),
                action_type=str(step.get("action_type", "navigate")),
                objective=str(step.get("objective", "")),
                required_items=[canonical_item(r) for r in step.get("required_items", [])],
            ))
    return todo


class RoomItems:
    """data/room_items.json wrapper; degrades to empty if file is missing."""

    def __init__(self, path: str | Path) -> None:
        self.rooms: dict[str, list[dict[str, Any]]] = {}
        p = Path(path)
        if p.is_file():
            with p.open(encoding="utf-8") as f:
                raw = json.load(f)
            for room_id, entry in raw.items():
                if room_id.startswith("_"):
                    continue
                items = []
                for it in entry.get("items", []):
                    it = dict(it)
                    it["name"] = canonical_item(it.get("name", ""))
                    items.append(it)
                self.rooms[str(room_id)] = items

    @property
    def loaded(self) -> bool:
        return bool(self.rooms)

    def items_in_room(self, room_id: str) -> list[dict[str, Any]]:
        return self.rooms.get(str(room_id), [])

    @staticmethod
    def _obtainable(item: dict[str, Any], ever_held: set[str]) -> bool:
        """Can the agent take this item right now, given what it has held?

        Gate semantics (docs/item_gates.md):
          no gate      -> obtainable
          type "trap"  -> obtainable (takeable; consequences are separate)
          type "item"/"puzzle"/"event" -> obtainable only when every named
              requirement has been held. Requirements we cannot track yet
              (event flags, non-item strings) make the item NOT obtainable —
              conservative, so items_left_here never taunts the agent with
              pickups it cannot have.
        """
        gate = item.get("gate")
        if not gate:
            return True
        if gate.get("type") == "trap":
            return True
        requires = [canonical_item(r) for r in gate.get("requires", [])]
        return bool(requires) and all(r in ever_held for r in requires)

    def remaining_in_room(self, room_id: str, ever_held: set[str]) -> int:
        """Count of OBTAINABLE pickups in the room the agent has never held.
        Counts multi-quantity entries once per unit; gated items are excluded
        until their requirements have been held."""
        n = 0
        for it in self.items_in_room(room_id):
            if str(it.get("name", "")) in ever_held:
                continue
            if not self._obtainable(it, ever_held):
                continue
            n += int(it.get("count", 1))
        return n

    def key_items_remaining_in_room(self, room_id: str, ever_held: set[str]) -> int:
        return sum(
            int(it.get("count", 1))
            for it in self.items_in_room(room_id)
            if it.get("key_item")
            and str(it.get("name", "")) not in ever_held
            and self._obtainable(it, ever_held)
        )

    def gated_in_room(self, room_id: str, ever_held: set[str]) -> int:
        """Pickups in this room that exist but are NOT obtainable yet
        ("come back later" marker). Excludes items already held."""
        return sum(
            int(it.get("count", 1))
            for it in self.items_in_room(room_id)
            if str(it.get("name", "")) not in ever_held
            and not self._obtainable(it, ever_held)
        )


@dataclass
class ItemTracker:
    """Per-episode item state: current inventory + ever-held set + TODO cursor."""

    todo: list[TodoEntry]
    ever_held: set[str] = field(default_factory=set)
    inventory: list[tuple[str, int]] = field(default_factory=list)

    def update(self, inventory: list[tuple[str, int]]) -> set[str]:
        """Feed the current inventory read; returns newly acquired item names
        (canonicalized via ITEM_ALIASES)."""
        self.inventory = list(inventory)
        names = {canonical_item(name) for name, _ in inventory}
        new = names - self.ever_held
        self.ever_held |= new
        return new

    def acquired(self) -> list[TodoEntry]:
        return [t for t in self.todo if t.item in self.ever_held]

    def pending(self) -> list[TodoEntry]:
        return [t for t in self.todo if t.item not in self.ever_held]

    def next_needed(self) -> TodoEntry | None:
        p = self.pending()
        return p[0] if p else None

    def progress(self) -> tuple[int, int]:
        return len(self.acquired()), len(self.todo)

    def format_checklist(self, limit: int | None = None) -> str:
        """Human-readable checklist, [x]/[ ] per item in route order."""
        lines = []
        done, total = self.progress()
        lines.append(f"item TODO: {done}/{total} acquired")
        entries = self.todo if limit is None else self.todo[:limit]
        for t in entries:
            mark = "x" if t.item in self.ever_held else " "
            req = f"  (needs: {', '.join(t.required_items)})" if t.required_items else ""
            lines.append(f"  [{mark}] wp{t.seq:02d} {t.room_id}  {t.item}{req}")
        return "\n".join(lines)
