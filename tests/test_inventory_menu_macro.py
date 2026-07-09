"""Offline tests for inventory grid navigation and proprio anim slots."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.inventory_menu_macro import (
    CLOSE_ITEM_SETTLE_FRAMES,
    CLOSE_START_FRAMES,
    EQUIP_SUBMENU_CROSS_FRAMES,
    EQUIP_SUBMENU_SETTLE_FRAMES,
    OPEN_SETTLE_FRAMES,
    OPEN_START_FRAMES,
    execute_equip_macro,
    slot_nav_moves,
)
from re1_rl.obs_encoder import PROPRIO_DIM, ObsEncoder
from re1_rl.room_graph import RoomGraph
from re1_rl.weapon_equip import weapon_already_equipped
from tests.test_scaffolding import make_state

ROOMS = Path(__file__).resolve().parents[1] / "data" / "rooms.json"
DOORS = Path(__file__).resolve().parents[1] / "data" / "doors_empirical.json"


class _RecordingClient:
    def __init__(
        self,
        *,
        equipped_id: int = 0,
        equipped_slot_1b: int = 0,
        inv_ids: list[int] | None = None,
        equip_target_slot: int | None = None,
    ) -> None:
        self.equipped_id = int(equipped_id)
        self.equipped_slot_1b = int(equipped_slot_1b)
        self.inv_ids = list(inv_ids or [0] * 8)
        self.equip_target_slot = equip_target_slot
        self.in_item_menu = False
        self.steps: list[tuple[dict[str, bool], int]] = []

    def step(self, buttons: dict[str, bool], n: int = 1):
        self.steps.append((dict(buttons), int(n)))
        if buttons.get("start"):
            self.in_item_menu = not self.in_item_menu
        crosses = sum(1 for b, _ in self.steps if b.get("cross"))
        if (
            crosses >= 2
            and self.equipped_id == 0
            and self.equip_target_slot is not None
        ):
            slot = int(self.equip_target_slot)
            self.equipped_id = int(self.inv_ids[slot])
            self.equipped_slot_1b = slot + 1
        return {}, False

    def read_ram(self, fields):
        out = {}
        for name, _addr, _dt in fields:
            if name == "equipped_weapon_id":
                out[name] = self.equipped_id
            elif name == "equipped_slot_1based":
                out[name] = self.equipped_slot_1b
            elif name == "game_mode":
                out[name] = 0x40 if self.in_item_menu else 0x80
            elif name == "game_state":
                out[name] = 0x00000080 if self.in_item_menu else 0x90808000
            elif name.startswith("inv_slot_"):
                idx = int(name.split("_")[-1])
                item_id = self.inv_ids[idx] if idx < len(self.inv_ids) else 0
                out[name] = item_id
            elif name == "player_hp":
                out[name] = 96
            else:
                out[name] = 0
        return out


def test_weapon_already_equipped() -> None:
    assert weapon_already_equipped(0x01, 0x01)
    assert not weapon_already_equipped(0x01, 0x02)
    assert not weapon_already_equipped(0, 0x01)


def test_execute_equip_macro_skips_already_equipped_knife() -> None:
    client = _RecordingClient(equipped_id=0x01, equipped_slot_1b=1, inv_ids=[0x01, 0x02])
    died, frames, report = execute_equip_macro(
        client, 0, prev_hp=96, episode_start_hp=96,
    )
    assert not died
    assert frames == 0
    assert report["reason"] == "already_equipped"
    assert client.steps == []


def test_execute_equip_macro_closes_item_screen_with_start() -> None:
    client = _RecordingClient(
        equipped_id=0, inv_ids=[0x01, 0x02], equip_target_slot=1,
    )
    died, frames, report = execute_equip_macro(
        client, 1, prev_hp=96, episode_start_hp=96,
    )
    assert not died
    assert report["ok"] is True
    assert client.steps[0] == ({"start": True}, OPEN_START_FRAMES)
    assert client.steps[-2] == ({"start": True}, CLOSE_START_FRAMES)
    assert client.steps[-1] == ({}, CLOSE_ITEM_SETTLE_FRAMES)
    cross_steps = [s for s in client.steps if s[0].get("cross")]
    assert len(cross_steps) == 2
    assert cross_steps[0] == ({"cross": True}, EQUIP_SUBMENU_CROSS_FRAMES)
    assert cross_steps[1] == ({"cross": True}, EQUIP_SUBMENU_CROSS_FRAMES)
    expected_min = (
        OPEN_START_FRAMES
        + OPEN_SETTLE_FRAMES
        + EQUIP_SUBMENU_CROSS_FRAMES
        + EQUIP_SUBMENU_SETTLE_FRAMES
        + EQUIP_SUBMENU_CROSS_FRAMES
        + EQUIP_SUBMENU_SETTLE_FRAMES
        + CLOSE_START_FRAMES
        + CLOSE_ITEM_SETTLE_FRAMES
    )
    assert frames >= expected_min


def test_slot_nav_from_home_row() -> None:
    assert slot_nav_moves(0, 1) == ["right"]
    assert slot_nav_moves(0, 2) == ["down"]
    assert slot_nav_moves(0, 3) == ["down", "right"]
    assert slot_nav_moves(1, 0) == ["left"]
    assert slot_nav_moves(3, 0) == ["up", "left"]


def test_slot_nav_same_slot() -> None:
    assert slot_nav_moves(4, 4) == []


def test_proprio_anim_history_and_poison() -> None:
    g = RoomGraph(DOORS)
    enc = ObsEncoder(ROOMS, g)
    hist = [
        (0x12, 0x04, 13),
        (0x13, 0x04, 8),
        (0x00, 0x00, 3),
        (0x14, 0x00, 0),
    ]
    s = make_state(anim_history=hist, poisoned=True)
    v = enc.encode_proprio(s, prev_hp=96)
    assert v.shape == (PROPRIO_DIM,)
    assert v[15] == round(0x12 / 255.0, 4) or abs(v[15] - 0x12 / 255.0) < 1e-5
    assert v[24] == round(0x14 / 255.0, 4) or abs(v[24] - 0x14 / 255.0) < 1e-5
    assert v[27] == 1.0
    assert np.all(np.isfinite(v))
