"""Equip Jill's combat knife after loading a dining-room savestate.

RE1 DC: START -> pause -> ITEM -> select knife (top-left) -> EQUIP.
Uses legacy bridge.step (non-sticky) so bootstrap is deterministic.
"""

from __future__ import annotations

from re1_rl.bizhawk_bridge import BizHawkClient


def _tap(client: BizHawkClient, buttons: dict[str, bool], *, frames: int) -> None:
    client.step(buttons=buttons, n=int(frames))


def equip_knife_from_pause_menu(client: BizHawkClient) -> None:
    """Open pause inventory and equip the knife in slot 0."""
    _tap(client, {"start": True}, frames=12)
    _tap(client, {}, frames=30)
    # Pause default is often CONTINUE; ITEM is one step down on Jill pause menu.
    _tap(client, {"down": True}, frames=10)
    _tap(client, {}, frames=8)
    _tap(client, {"cross": True}, frames=20)
    _tap(client, {}, frames=20)
    # Inventory opens with cursor not always on knife; top-left is knife.
    _tap(client, {"up": True}, frames=10)
    _tap(client, {}, frames=8)
    _tap(client, {"left": True}, frames=10)
    _tap(client, {}, frames=8)
    _tap(client, {"cross": True}, frames=15)  # EQUIP
    _tap(client, {}, frames=8)
    _tap(client, {"cross": True}, frames=15)  # confirm
    _tap(client, {}, frames=8)
    _tap(client, {"triangle": True}, frames=15)  # close inventory
    _tap(client, {}, frames=8)
    _tap(client, {"start": True}, frames=12)  # close pause
    _tap(client, {}, frames=15)
