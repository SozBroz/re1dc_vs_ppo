"""Offline tests for inventory grid navigation and proprio anim slots."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.inventory_menu_macro import (
    CLOSE_ITEM_SETTLE_FRAMES,
    CLOSE_START_FRAMES,
    CLOSE_TRIANGLE_FRAMES,
    CLOSE_TRIANGLE_SETTLE_FRAMES,
    EQUIP_SUBMENU_CROSS_FRAMES,
    EQUIP_SUBMENU_SETTLE_FRAMES,
    OPEN_SETTLE_FRAMES,
    OPEN_START_FRAMES,
    close_document_examine_ui,
    dismiss_orphan_item_menu,
    execute_equip_macro,
    slot_nav_moves,
)
from re1_rl.action_mask import COMBINE_ACTION, EQUIP_ACTION, USE_ACTION
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
        start_closes_menu: bool = True,
        triangle_closes_menu: bool = False,
        document_examine: bool = False,
    ) -> None:
        self.equipped_id = int(equipped_id)
        self.equipped_slot_1b = int(equipped_slot_1b)
        self.inv_ids = list(inv_ids or [0] * 8)
        self.equip_target_slot = equip_target_slot
        self.in_item_menu = False
        self.start_closes_menu = bool(start_closes_menu)
        self.triangle_closes_menu = bool(triangle_closes_menu)
        self.document_examine = bool(document_examine)
        self.steps: list[tuple[dict[str, bool], int]] = []

    def step(self, buttons: dict[str, bool], n: int = 1):
        self.steps.append((dict(buttons), int(n)))
        if buttons.get("start") and self.start_closes_menu:
            self.in_item_menu = not self.in_item_menu
        if buttons.get("triangle") and self.triangle_closes_menu:
            self.in_item_menu = False
            self.document_examine = False
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
                # Document examine: exact 0x40808100; ITEM grid: 0x40808000.
                if self.in_item_menu and self.document_examine:
                    out[name] = 0x40808100
                elif self.in_item_menu:
                    out[name] = 0x40808000
                else:
                    out[name] = 0x90808000
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


def test_dismiss_orphan_item_menu_closes_with_start() -> None:
    client = _RecordingClient()
    client.in_item_menu = True
    still, frames, report = dismiss_orphan_item_menu(
        client, prev_hp=96, episode_start_hp=96
    )
    assert not still
    assert report["cleared"] is True
    assert report.get("skipped") is not True
    assert frames >= CLOSE_START_FRAMES + CLOSE_ITEM_SETTLE_FRAMES
    assert client.steps[0] == ({"start": True}, CLOSE_START_FRAMES)
    assert not client.in_item_menu


def test_dismiss_orphan_item_menu_skips_when_already_clear() -> None:
    client = _RecordingClient()
    client.in_item_menu = False
    still, frames, report = dismiss_orphan_item_menu(
        client, prev_hp=96, episode_start_hp=96
    )
    assert not still
    assert frames == 0
    assert report.get("skipped") is True
    assert client.steps == []


def test_close_document_examine_ui_triangle() -> None:
    client = _RecordingClient(start_closes_menu=False, triangle_closes_menu=True)
    client.in_item_menu = True
    died, frames = close_document_examine_ui(
        client, prev_hp=96, episode_start_hp=96
    )
    assert not died
    assert frames >= CLOSE_TRIANGLE_FRAMES + CLOSE_TRIANGLE_SETTLE_FRAMES
    assert client.steps[0] == ({"triangle": True}, CLOSE_TRIANGLE_FRAMES)
    assert not client.in_item_menu


def test_dismiss_orphan_document_examine_triangle_direct() -> None:
    """QS1 botany book gs=0x40808100: Triangle immediately (no Start waste)."""
    client = _RecordingClient(
        start_closes_menu=False,
        triangle_closes_menu=True,
        document_examine=True,
    )
    client.in_item_menu = True
    still, frames, report = dismiss_orphan_item_menu(
        client, prev_hp=96, episode_start_hp=96
    )
    assert not still
    assert report["cleared"] is True
    assert report["path"] == "triangle_document"
    assert client.steps[0] == ({"triangle": True}, CLOSE_TRIANGLE_FRAMES)
    assert not any(b.get("start") for b, _ in client.steps)
    assert frames >= CLOSE_TRIANGLE_FRAMES + CLOSE_TRIANGLE_SETTLE_FRAMES
    assert not client.in_item_menu


def test_dismiss_orphan_falls_back_to_triangle_for_document() -> None:
    """Unknown pause-tree leftover: Start fails, Triangle clears it."""
    client = _RecordingClient(start_closes_menu=False, triangle_closes_menu=True)
    client.in_item_menu = True
    still, frames, report = dismiss_orphan_item_menu(
        client, prev_hp=96, episode_start_hp=96
    )
    assert not still
    assert report["cleared"] is True
    assert report["path"] == "triangle_document"
    assert any(b.get("triangle") for b, _ in client.steps)
    assert frames > CLOSE_START_FRAMES + CLOSE_ITEM_SETTLE_FRAMES
    assert not client.in_item_menu


def test_inventory_macro_owns_item_menu_gating() -> None:
    """Orphan dismiss must not run while equip/use/combine owns the screen."""

    class _Probe:
        _macro_active = False
        _use_phase = 0
        _equip_phase = 0
        _combine_phase = 0

        def _inventory_macro_owns_item_menu(self, action: int) -> bool:
            from re1_rl.env import RE1Env

            return RE1Env._inventory_macro_owns_item_menu(self, int(action))  # type: ignore[arg-type]

    p = _Probe()
    assert not p._inventory_macro_owns_item_menu(0)
    assert p._inventory_macro_owns_item_menu(USE_ACTION)
    assert p._inventory_macro_owns_item_menu(EQUIP_ACTION)
    assert p._inventory_macro_owns_item_menu(COMBINE_ACTION)
    p._equip_phase = 1
    assert p._inventory_macro_owns_item_menu(0)
    p._equip_phase = 0
    p._macro_active = True
    assert p._inventory_macro_owns_item_menu(0)


def test_execute_equip_releases_if_start_does_not_open_menu() -> None:
    """Hitstun ate Start: one attempt, confirm fail, no further menu inputs."""

    class _DeafStartClient(_RecordingClient):
        def step(self, buttons: dict[str, bool], n: int = 1):
            self.steps.append((dict(buttons), int(n)))
            # Start never opens ITEM (inputs eaten).
            return {}, False

    client = _DeafStartClient(
        equipped_id=0, inv_ids=[0x01, 0x02], equip_target_slot=1,
    )
    died, frames, report = execute_equip_macro(
        client, 1, prev_hp=96, episode_start_hp=96,
    )
    assert not died
    assert report["ok"] is False
    assert report["reason"] == "item_menu_open_failed"
    assert frames == OPEN_START_FRAMES + OPEN_SETTLE_FRAMES
    assert client.steps == [
        ({"start": True}, OPEN_START_FRAMES),
        ({}, OPEN_SETTLE_FRAMES),
    ]
    assert not any(b.get("cross") for b, _ in client.steps)


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
