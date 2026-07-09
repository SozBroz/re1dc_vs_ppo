"""Tests for re1_rl.rdt_parser."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from re1_rl.rdt_parser import (
    parse_rdt_filename,
    parse_room_rdt,
    walk_scd,
)

RDT_DIR = Path(__file__).resolve().parents[1] / "data" / "rdt_raw"


def test_parse_filename():
    assert parse_rdt_filename("ROOM1050.RDT") == (1, "105", 0)
    assert parse_rdt_filename("room10a1.rdt") == (1, "10A", 1)
    assert parse_rdt_filename("ROOM3000.RDT") == (3, "300", 0)


def test_walk_door_set_opcode():
    # minimal DOOR_SET: dest room 06 stage 1 -> byte 0x06 at offset 15
    body = bytearray(26)
    body[0] = 0x0C
    body[1] = 1
    struct.pack_into("<hhhh", body, 2, 31600, 6300, 2000, 4000)
    body[15] = 0x06
    struct.pack_into("<hhh", body, 16, 3400, 1536, 17000)
    ev = list(walk_scd(bytes(body), "test"))
    assert len(ev) == 1
    assert ev[0]["kind"] == "door_set"
    assert ev[0]["dest_room"] == "06"
    assert ev[0]["entry_x"] == 3400
    assert ev[0]["entry_z"] == 17000


def test_walk_item_set_opcode():
    body = bytearray(18)
    body[0] = 0x0D
    body[1] = 2
    struct.pack_into("<hhhh", body, 2, 31700, 6300, 2000, 4000)
    body[10] = 0x09
    ev = list(walk_scd(bytes(body), "test"))
    assert ev[0]["kind"] == "item_set"
    assert ev[0]["type_code"] == 0x09


@pytest.mark.skipif(not (RDT_DIR / "ROOM1050.RDT").is_file(), reason="run extract_rdt_from_disc.py")
def test_room105_dining_parses():
    room = parse_room_rdt(RDT_DIR / "ROOM1050.RDT")
    assert room is not None
    assert room.room_id == "105"
    assert any(d.dest_room == "106" for d in room.doors)
    assert any(it.slot_id == 2 for it in room.items)
    door106 = next(d for d in room.doors if d.dest_room == "106")
    assert door106.entry_x == 3400
    assert door106.entry_z == 17000


@pytest.mark.skipif(not (RDT_DIR / "ROOM1040.RDT").is_file(), reason="run extract_rdt_from_disc.py")
def test_room104_has_enemy_spawn():
    room = parse_room_rdt(RDT_DIR / "ROOM1040.RDT")
    assert room is not None
    assert len(room.enemies) >= 1
