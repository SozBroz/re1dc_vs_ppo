"""Scancode-based tank controls for GOG RE1 via pydirectinput."""

from __future__ import annotations

import time

import pydirectinput

# DirectInput scan codes (set 1) — tank control layout for RE1 PC.
SCAN_UP = 0xC8
SCAN_DOWN = 0xD0
SCAN_LEFT = 0xCB
SCAN_RIGHT = 0xCD
SCAN_RUN = 0x2A  # Left Shift
SCAN_ACTION = 0x1C  # Enter
SCAN_AIM = 0x2F  # V — TODO: verify in-game key bindings
SCAN_FIRE = 0x39  # Space


def _tap(scancode: int, hold_s: float = 0.05) -> None:
    pydirectinput.keyDown(scancode, char=False)
    time.sleep(hold_s)
    pydirectinput.keyUp(scancode, char=False)


def forward(hold_s: float = 0.1) -> None:
    _tap(SCAN_UP, hold_s)


def back(hold_s: float = 0.1) -> None:
    _tap(SCAN_DOWN, hold_s)


def turn_left(hold_s: float = 0.05) -> None:
    _tap(SCAN_LEFT, hold_s)


def turn_right(hold_s: float = 0.05) -> None:
    _tap(SCAN_RIGHT, hold_s)


def run_forward(hold_s: float = 0.1) -> None:
    pydirectinput.keyDown(SCAN_RUN, char=False)
    _tap(SCAN_UP, hold_s)
    pydirectinput.keyUp(SCAN_RUN, char=False)


def interact(hold_s: float = 0.05) -> None:
    _tap(SCAN_ACTION, hold_s)


def aim(hold_s: float = 0.05) -> None:
    _tap(SCAN_AIM, hold_s)


def fire(hold_s: float = 0.05) -> None:
    _tap(SCAN_FIRE, hold_s)
