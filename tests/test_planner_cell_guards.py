"""Guards for planner-loyal cell links: degenerate mints must not install."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.planner_loyal_cells import live_graft_matches_claim


def _env(room: str | None, hp: int) -> SimpleNamespace:
    env = SimpleNamespace()
    env._read_state = lambda track_items=False: {"room_id": room, "hp": hp}
    return env


def test_matching_live_ram_passes() -> None:
    assert live_graft_matches_claim(_env("105", 96), {"room_id": "105"}) is True


def test_room_mismatch_rejects() -> None:
    # Degenerate 2026-09-12 mints: claimed 105/106, RAM read 100 + hp 0.
    assert live_graft_matches_claim(_env("100", 0), {"room_id": "105"}) is False


def test_zero_hp_rejects() -> None:
    assert live_graft_matches_claim(_env("105", 0), {"room_id": "105"}) is False


def test_chip_damage_still_passes() -> None:
    assert live_graft_matches_claim(_env("105", 84), {"room_id": "105"}) is True


def test_unreadable_ram_fails_open() -> None:
    env = SimpleNamespace()
    env._read_state = lambda track_items=False: (_ for _ in ()).throw(OSError("gone"))
    assert live_graft_matches_claim(env, {"room_id": "105"}) is True


def test_no_reader_fails_open() -> None:
    assert live_graft_matches_claim(SimpleNamespace(), {"room_id": "105"}) is True
