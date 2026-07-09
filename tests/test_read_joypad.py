"""Joypad bridge response parsing (Nymashock stick axes)."""

from __future__ import annotations


def parse_buttons_field(btn_raw: object) -> dict[str, bool]:
    if isinstance(btn_raw, list):
        return {str(k): True for k in btn_raw if str(k) != "_"}
    return {
        str(k): bool(v) for k, v in dict(btn_raw).items() if str(k) != "_" and v
    }


def stick128(v: int | float, dead: int = 24) -> str | None:
    d = int(v) - 128
    if abs(d) < dead:
        return None
    return "neg" if d < 0 else "pos"


def test_empty_lua_table_encodes_as_json_list():
    assert parse_buttons_field([]) == {}


def test_object_buttons():
    assert parse_buttons_field({"left": True, "_": False}) == {"left": True}


def test_stick128_neutral():
    assert stick128(128) is None


def test_stick128_left_and_right():
    assert stick128(80) == "neg"
    assert stick128(200) == "pos"
