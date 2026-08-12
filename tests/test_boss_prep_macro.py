"""Offline tests for verified boss-prep box banking."""

from __future__ import annotations

from re1_rl.boss_prep_macro import (
    ROOM_100_BOSS_BANK_DEPOSIT_IDS,
    room100_boss_bank_preflight,
)
from re1_rl.item_box import box_pollution_reason


def _empty_box(n: int = 16) -> list[tuple[int, int]]:
    return [(0, 0)] * n


def test_room100_preflight_requires_bazooka_and_acid() -> None:
    inv = [
        (0x07, 6),
        (0x11, 6),
        (0x0B, 30),
        (0x01, 0),
        (0x0E, 14),
        (0x0C, 5),
        (0, 0),
        (0, 0),
    ]
    box = _empty_box()
    ok, reason = room100_boss_bank_preflight(inv, box, room_id="100")
    assert ok and reason == ""


def test_room100_preflight_rejects_handgun_pollution() -> None:
    inv = [(0x07, 6), (0x11, 6)] + [(0, 0)] * 6
    box = [(0x0B, 25), (0x01, 0)] + [(0, 0)] * 14
    ok, reason = room100_boss_bank_preflight(inv, box, room_id="100")
    assert not ok
    assert reason == "handgun_in_box"


def test_room100_preflight_wrong_room() -> None:
    inv = [(0x07, 6), (0x11, 6)] + [(0, 0)] * 6
    box = _empty_box()
    ok, reason = room100_boss_bank_preflight(inv, box, room_id="118")
    assert not ok and reason == "wrong_room"


def test_room100_deposit_order() -> None:
    assert ROOM_100_BOSS_BANK_DEPOSIT_IDS == (0x07, 0x11)
