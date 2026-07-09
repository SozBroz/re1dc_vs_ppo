"""RE1 PS1 RDT header + SCD bytecode parser.

Extracts doors, items, enemies, and interactables from room*.rdt files.
Format reference: http://justsolve.archiveteam.org/wiki/RDT_(Resident_Evil_1997)
and biorand/biohazard-utils (Rdt1.cs).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# Fixed opcode sizes (Just Solve wiki). Variable opcodes use skip helpers.
OPCODE_SIZES: dict[int, int] = {
    0x00: 2, 0x01: 2, 0x02: 2, 0x03: 2,
    0x04: 4, 0x05: 4, 0x06: 4, 0x07: 6, 0x08: 4,
    0x09: 2, 0x0A: 2, 0x0B: 4,
    0x0C: 26, 0x0D: 18, 0x0E: 2, 0x0F: 8,
    0x10: 2, 0x11: 2, 0x12: 10, 0x13: 4, 0x14: 4,
    0x15: 2, 0x16: 2, 0x17: 10, 0x18: 26, 0x19: 4,
    0x1A: 2, 0x1B: 22, 0x1C: 6, 0x1D: 2, 0x1E: 4,
    0x1F: 28, 0x20: 14, 0x21: 14, 0x22: 4, 0x23: 2,
    0x24: 4, 0x25: 4, 0x27: 2, 0x29: 2, 0x2A: 12,
    0x2B: 4, 0x2C: 2, 0x2D: 4, 0x2F: 4, 0x30: 12,
    0x31: 4, 0x32: 4, 0x33: 2, 0x34: 8, 0x35: 4,
    0x36: 4, 0x37: 4, 0x38: 4, 0x39: 2, 0x3A: 4,
    0x3B: 6, 0x3C: 6, 0x3D: 12, 0x3E: 2, 0x3F: 6,
    0x40: 16, 0x41: 4, 0x42: 4, 0x43: 4, 0x44: 2,
    0x45: 2, 0x46: 2, 0x47: 14, 0x48: 2, 0x49: 2,
    0x4A: 2, 0x4B: 2, 0x4C: 4, 0x4D: 2, 0x4E: 4,
    0x4F: 2, 0x50: 2,
}

ITEM_TYPE_NAMES: dict[int, str] = {
    0x00: "object",
    0x02: "message",
    0x07: "trigger",
    0x08: "item_box",
    0x09: "pickable",
    0x10: "typewriter",
}

PICKABLE_TYPES = {0x00, 0x09}
INTERACTABLE_TYPES = {0x02, 0x07, 0x08, 0x10}

RDT_NAME_RE = re.compile(
    r"^ROOM([0-9A-Fa-f]{3})([01])\.RDT$", re.IGNORECASE
)


@dataclass
class RdtHeader:
    n_sprite: int
    n_cut: int
    n_omodel: int
    n_item: int
    n_door: int
    n_room_at: int


@dataclass
class DoorSet:
    door_id: int
    zone_x: int
    zone_z: int
    zone_w: int
    zone_h: int
    dest_stage: int
    dest_room: str
    entry_x: int
    entry_z: int
    entry_dir: int
    script: str
    gated: bool = False


@dataclass
class ItemSet:
    slot_id: int
    x: int
    z: int
    w: int
    h: int
    type_code: int
    type_name: str
    script: str
    gated: bool = False


@dataclass
class EnemySet:
    model: int
    x: int
    z: int
    entity_id: int
    killed_index: int
    script: str
    gated: bool = False


@dataclass
class InteractableSet:
    """Typewriter, message, trigger — non-inventory interactables."""
    slot_id: int
    x: int
    z: int
    kind: str
    script: str
    gated: bool = False


@dataclass
class RoomRdt:
    filename: str
    stage: int
    room_id: str
    variant: int
    header: RdtHeader
    doors: list[DoorSet] = field(default_factory=list)
    items: list[ItemSet] = field(default_factory=list)
    enemies: list[EnemySet] = field(default_factory=list)
    interactables: list[InteractableSet] = field(default_factory=list)
    flag_tests: list[dict[str, Any]] = field(default_factory=list)


def parse_rdt_filename(name: str) -> tuple[int, str, int] | None:
    m = RDT_NAME_RE.match(name.upper())
    if not m:
        return None
    room_hex, variant = m.group(1), int(m.group(2))
    stage = int(room_hex[0], 16)
    room_id = room_hex.upper()
    return stage, room_id, variant


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _i16(data: bytes, off: int) -> int:
    return struct.unpack_from("<h", data, off)[0]


def read_header(data: bytes) -> RdtHeader:
    return RdtHeader(
        n_sprite=data[0],
        n_cut=data[1],
        n_omodel=data[2],
        n_item=data[3],
        n_door=data[4],
        n_room_at=data[5],
    )


def read_offsets(data: bytes) -> list[int]:
    return list(struct.unpack_from("<19I", data, 0x48))


def iter_scd_procedures(data: bytes, base: int) -> Iterator[tuple[str, bytes]]:
    """Yield (script_name, procedure_bytecode) from an SCD container."""
    if base <= 0 or base >= len(data):
        return
    addr = base
    idx = 0
    while addr + 2 <= len(data):
        length = _u16(data, addr)
        if length == 0:
            break
        if length <= 2:
            break
        if addr + length > len(data):
            break
        body = data[addr + 2 : addr + length]
        yield (f"proc{idx}", body)
        addr += length
        idx += 1


def _opcode_size(op: int, data: bytes, pos: int) -> int:
    if op in OPCODE_SIZES:
        sz = OPCODE_SIZES[op]
        if op == 0x46:
            # variable: 2 + 12*n + 2*m — skip conservatively
            return min(44, len(data) - pos)
        if op == 0x28:
            return 4 if pos + 4 <= len(data) else 2
        if op == 0x33:
            return 4 if pos + 4 <= len(data) and data[pos + 2] != 0 else 2
        return sz
    return 2


def walk_scd(body: bytes, script: str, gated: bool = False) -> Iterator[dict[str, Any]]:
    """Walk one SCD procedure; yield decoded opcodes.

    Uses linear opcode-size stepping. If/Else blocks are not interpreted —
    gated=False for all records in v1 (conservative; avoids block-skip bugs).
    """
    pos = 0
    n = len(body)
    steps = 0
    while pos < n and steps < n * 2:
        steps += 1
        op = body[pos]
        sz = _opcode_size(op, body, pos)
        if pos + sz > n:
            break
        chunk = body[pos : pos + sz]

        if op == 0x04 and len(chunk) >= 4:
            yield {
                "kind": "bit_test",
                "script": script,
                "object": chunk[1],
                "param": chunk[2],
                "op": chunk[3],
                "gated": gated,
            }
        elif op == 0x0C and len(chunk) >= 26:
            packed = chunk[15]
            dest_stage = (packed >> 5) & 0x07
            dest_room = f"{packed & 0x1F:02X}"
            yield {
                "kind": "door_set",
                "script": script,
                "gated": gated,
                "door_id": chunk[1],
                "zone_x": _i16(chunk, 2),
                "zone_z": _i16(chunk, 4),
                "zone_w": _i16(chunk, 6),
                "zone_h": _i16(chunk, 8),
                "dest_stage": dest_stage,
                "dest_room": dest_room,
                "entry_x": _i16(chunk, 16),
                "entry_z": _i16(chunk, 20),
                "entry_dir": _i16(chunk, 22),
            }
        elif op == 0x0D and len(chunk) >= 18:
            yield {
                "kind": "item_set",
                "script": script,
                "gated": gated,
                "slot_id": chunk[1],
                "x": _i16(chunk, 2),
                "z": _i16(chunk, 4),
                "w": _i16(chunk, 6),
                "h": _i16(chunk, 8),
                "type_code": chunk[10],
            }
        elif op == 0x1B and len(chunk) >= 22:
            yield {
                "kind": "em_set",
                "script": script,
                "gated": gated,
                "model": chunk[1],
                "killed_index": chunk[3],
                "x": _u16(chunk, 12),
                "z": _u16(chunk, 16),
                "entity_id": chunk[20],
            }
        pos += max(sz, 1)


def _dest_stage(current: int, raw: int) -> int:
    return current if raw == 0 else raw


def _full_room_id(stage: int, room_nibble: str) -> str:
    """Combine stage digit + room low-byte into rooms.json code (e.g. 1 + 06 -> 106)."""
    return f"{stage}{room_nibble.upper()}"


def parse_room_rdt(path: str | Path) -> RoomRdt | None:
    path = Path(path)
    parsed = parse_rdt_filename(path.name)
    if parsed is None:
        return None
    stage, room_id, variant = parsed
    data = path.read_bytes()
    header = read_header(data)
    offsets = read_offsets(data)
    room = RoomRdt(
        filename=path.name,
        stage=stage,
        room_id=room_id,
        variant=variant,
        header=header,
    )

    script_bases: list[tuple[str, int]] = []
    if offsets[6]:
        script_bases.append(("init", offsets[6]))
    if offsets[7]:
        script_bases.append(("main", offsets[7]))
    # offsets[8] is event pointer table, not a procedure container — skip

    seen_doors: set[tuple[int, int, int]] = set()
    seen_items: set[tuple[int, int, int]] = set()
    seen_enemies: set[tuple[int, int]] = set()

    for script_kind, base in script_bases:
        for proc_name, body in iter_scd_procedures(data, base):
            script = f"{script_kind}/{proc_name}"
            for ev in walk_scd(body, script):
                kind = ev["kind"]
                if kind == "bit_test":
                    room.flag_tests.append(ev)
                elif kind == "door_set":
                    key = (ev["door_id"], ev["zone_x"], ev["zone_z"])
                    if key in seen_doors:
                        continue
                    seen_doors.add(key)
                    ds = _dest_stage(stage, ev["dest_stage"])
                    dest_rid = _full_room_id(ds, ev["dest_room"])
                    room.doors.append(DoorSet(
                        door_id=ev["door_id"],
                        zone_x=ev["zone_x"] + ev["zone_w"] // 2,
                        zone_z=ev["zone_z"] + ev["zone_h"] // 2,
                        zone_w=ev["zone_w"],
                        zone_h=ev["zone_h"],
                        dest_stage=ds,
                        dest_room=dest_rid,
                        entry_x=ev["entry_x"],
                        entry_z=ev["entry_z"],
                        entry_dir=ev["entry_dir"],
                        script=script,
                        gated=ev["gated"],
                    ))
                elif kind == "item_set":
                    tc = ev["type_code"]
                    key = (ev["slot_id"], ev["x"], ev["z"])
                    if key in seen_items:
                        continue
                    seen_items.add(key)
                    cx = ev["x"] + ev["w"] // 2
                    cz = ev["z"] + ev["h"] // 2
                    if tc in PICKABLE_TYPES:
                        room.items.append(ItemSet(
                            slot_id=ev["slot_id"],
                            x=cx, z=cz,
                            w=ev["w"], h=ev["h"],
                            type_code=tc,
                            type_name=ITEM_TYPE_NAMES.get(tc, "pickable"),
                            script=script,
                            gated=ev["gated"],
                        ))
                    elif tc in INTERACTABLE_TYPES:
                        room.interactables.append(InteractableSet(
                            slot_id=ev["slot_id"],
                            x=cx, z=cz,
                            kind=ITEM_TYPE_NAMES.get(tc, f"type_{tc:02x}"),
                            script=script,
                            gated=ev["gated"],
                        ))
                elif kind == "em_set":
                    if ev["killed_index"] == 0xFF:
                        continue
                    key = (ev["entity_id"], ev["model"])
                    if key in seen_enemies:
                        continue
                    seen_enemies.add(key)
                    room.enemies.append(EnemySet(
                        model=ev["model"],
                        x=ev["x"],
                        z=ev["z"],
                        entity_id=ev["entity_id"],
                        killed_index=ev["killed_index"],
                        script=script,
                        gated=ev["gated"],
                    ))
    return room


def room_rdt_to_dict(room: RoomRdt) -> dict[str, Any]:
    return {
        "filename": room.filename,
        "stage": room.stage,
        "room_id": room.room_id,
        "variant": room.variant,
        "doors": [
            {
                "door_id": d.door_id,
                "zone_x": d.zone_x, "zone_z": d.zone_z,
                "zone_w": d.zone_w, "zone_h": d.zone_h,
                "dest_stage": d.dest_stage, "dest_room": d.dest_room,
                "entry_x": d.entry_x, "entry_z": d.entry_z,
                "entry_dir": d.entry_dir,
                "script": d.script, "gated": d.gated,
            }
            for d in room.doors
        ],
        "items": [
            {
                "slot_id": it.slot_id,
                "x": it.x, "z": it.z,
                "w": it.w, "h": it.h,
                "type": it.type_name,
                "script": it.script, "gated": it.gated,
            }
            for it in room.items
        ],
        "enemies": [
            {
                "model": e.model,
                "x": e.x, "z": e.z,
                "entity_id": e.entity_id,
                "killed_index": e.killed_index,
                "script": e.script, "gated": e.gated,
            }
            for e in room.enemies
        ],
        "interactables": [
            {
                "slot_id": i.slot_id,
                "x": i.x, "z": i.z,
                "kind": i.kind,
                "script": i.script, "gated": i.gated,
            }
            for i in room.interactables
        ],
        "flag_tests": room.flag_tests,
    }
