"""Load Resident Evil in-game saves from the PS1 memory card (title menu automation)."""

from __future__ import annotations

from typing import Any

from re1_rl.fresh_spawn import format_spawn_summary
from re1_rl.memory_map import (
    CHARACTER_ID,
    GAME_MODE,
    IN_CONTROL_MASK,
    MENU_ROOM_ID,
    PLAYER_HP,
    ROOM_ID,
    STAGE_ID,
)


def tap(client: Any, button: str, hold: int = 2, release: int = 8) -> None:
    client.send_buttons({button: True})
    client.frameadvance(hold)
    client.send_buttons({})
    client.frameadvance(release)


def mash_to_menu(client: Any, *, max_frames: int = 6000) -> bool:
    """Mash through BIOS / logos until the title menu room byte (27) appears."""
    for step in range(max_frames):
        if step % 4 == 0:
            client.send_buttons({"cross": True})
        elif step % 4 == 2:
            client.send_buttons({"start": True})
        else:
            client.send_buttons({})
        client.frameadvance(1)
        if step % 30 == 0:
            ram = client.read_ram([("room_id", ROOM_ID, "u8")])
            if int(ram["room_id"]) == MENU_ROOM_ID:
                client.send_buttons({})
                client.frameadvance(40)
                return True
    return False


def load_memory_card_slot(client: Any, slot: int) -> None:
    """Reboot core and best-effort LOAD GAME for memory-card slot ``slot`` (1..15)."""
    if slot < 1 or slot > 15:
        raise ValueError(f"save slot must be 1..15, got {slot}")

    print(f"[ingame_save] rebooting; LOAD GAME slot {slot}...", flush=True)
    client.reboot()
    client.frameadvance(90)

    if not mash_to_menu(client):
        print("[ingame_save] WARN: title menu not detected before LOAD GAME taps", flush=True)

    # Title default is often NEW GAME — move to LOAD GAME.
    tap(client, "down", hold=2, release=30)
    tap(client, "cross", hold=2, release=50)

    # Slot list: slot 1 is top.
    for _ in range(slot - 1):
        tap(client, "down", hold=2, release=25)
    tap(client, "cross", hold=2, release=50)
    # Confirm overwrite / load prompt if shown.
    tap(client, "cross", hold=2, release=80)


def wait_for_loaded_save(
    client: Any,
    *,
    timeout_frames: int = 24000,
    poll_every: int = 30,
) -> tuple[bool, dict]:
    """After LOAD GAME taps, mash intro/cutscenes until mansion gameplay loads.

    Do NOT use RamSkipper here — turbo cross-mash at the title menu starts NEW GAME.
    """
    last: dict = {}
    for step in range(0, timeout_frames, poll_every):
        if step % 4 == 0:
            client.send_buttons({"cross": True})
        elif step % 4 == 2:
            client.send_buttons({"start": True})
        else:
            client.send_buttons({})
        client.frameadvance(poll_every)

        ram = client.read_ram(
            [
                ("player_hp", PLAYER_HP, "u16"),
                ("room_id", ROOM_ID, "u8"),
                ("game_mode", GAME_MODE, "u8"),
                ("stage_id", STAGE_ID, "u8"),
                ("character_id", CHARACTER_ID, "u8"),
            ]
        )
        last = dict(ram)
        hp = int(ram["player_hp"])
        room = int(ram["room_id"])
        mode = int(ram["game_mode"])
        if hp > 0 and room != MENU_ROOM_ID and bool(mode & IN_CONTROL_MASK):
            print(f"[ingame_save] save loaded: {format_spawn_summary(ram)}", flush=True)
            client.send_buttons({})
            client.frameadvance(20)
            return True, last
        if step % 600 == 0 and step > 0:
            print(f"[ingame_save] still loading... {format_spawn_summary(ram)}", flush=True)

    print(
        f"[ingame_save] FAIL: gameplay not reached in {timeout_frames} frames; "
        f"last {format_spawn_summary(last) if last else 'n/a'}",
        flush=True,
    )
    return False, last
