"""Tests for read-only post-Richard lab countdown detection."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.memory_map import (
    IN_CONTROL_MASK,
    PAUSE_MENU_GAME_MODE,
    RICHARD_LAB_COUNTDOWN_STATUS_GAME_STATE,
)
from re1_rl.inventory_menu_macro import dismiss_orphan_item_menu
from re1_rl.ram_skip import item_inventory_screen_from_ram
from re1_rl.richard_lab import (
    read_richard_lab_ram,
    richard_lab_countdown_screen_from_ram,
    richard_lab_timer_active,
)


class _ReadOnlyClient:
    def read_ram(self, fields):
        values = {
            "game_mode": PAUSE_MENU_GAME_MODE,
            "game_state": RICHARD_LAB_COUNTDOWN_STATUS_GAME_STATE,
            "lab_timer": 31829,
        }
        return {name: values[name] for name, _address, _dtype in fields}


class _RichardPauseClient:
    def __init__(self) -> None:
        self.in_menu = True
        self.lab_timer = 31829
        self.steps = []

    def read_ram(self, fields):
        values = {
            "game_mode": PAUSE_MENU_GAME_MODE if self.in_menu else IN_CONTROL_MASK,
            "game_state": (
                RICHARD_LAB_COUNTDOWN_STATUS_GAME_STATE
                if self.in_menu
                else 0x80800004
            ),
            "lab_timer": self.lab_timer,
            "player_hp": 96,
        }
        return {name: values.get(name, 0) for name, _address, _dtype in fields}

    def step(self, buttons, n=1):
        self.steps.append((dict(buttons), int(n)))
        if buttons.get("triangle"):
            self.in_menu = False
        return {}, False


def test_richard_lab_countdown_screen_detects_qs8_signature() -> None:
    ram = {
        "game_mode": PAUSE_MENU_GAME_MODE,
        "game_state": RICHARD_LAB_COUNTDOWN_STATUS_GAME_STATE,
        "lab_timer": 31829,
    }
    assert richard_lab_countdown_screen_from_ram(ram)
    assert richard_lab_timer_active(ram)
    assert item_inventory_screen_from_ram(ram)


def test_richard_lab_countdown_screen_rejects_normal_status() -> None:
    ram = {
        "game_mode": PAUSE_MENU_GAME_MODE,
        "game_state": 0x40808004,
        "lab_timer": 100,
    }
    assert not richard_lab_countdown_screen_from_ram(ram)


def test_richard_lab_reader_requires_no_write_api() -> None:
    ram = read_richard_lab_ram(_ReadOnlyClient())
    assert ram["lab_timer"] == 31829
    assert richard_lab_countdown_screen_from_ram(ram)


def test_generic_pause_dismissal_preserves_richard_timer() -> None:
    client = _RichardPauseClient()
    still, _frames, report = dismiss_orphan_item_menu(
        client, prev_hp=96, episode_start_hp=96
    )
    assert not still
    assert report["cleared"] is True
    assert client.steps[0][0] == {"start": True}
    assert any(buttons == {"triangle": True} for buttons, _frames in client.steps)
    assert client.lab_timer == 31829


def test_in_control_play_not_richard_trap() -> None:
    ram = {
        "game_mode": IN_CONTROL_MASK,
        "game_state": 0x80800004,
        "lab_timer": 0,
    }
    assert not richard_lab_countdown_screen_from_ram(ram)
    assert not richard_lab_timer_active(ram)
