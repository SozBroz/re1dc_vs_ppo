"""PS1 (SLUS-00170) and PC GOG memory / save-file constants."""

from __future__ import annotations

# BizHawk MainRAM domain offset = PS1 bus address - 0x80000000
PS1_MAINRAM_BASE = 0x80000000

# --- PS1 Director's Cut (SLUS-00170) — MainRAM bus addresses ---
PLAYER_HP = 0x800C51AC  # u16; max 0x8C (140) Jill/Chris [CONFIRMED]
GAME_TIMER = 0x800C867C  # [CONFIRMED]
LAB_TIMER = 0x800C867A  # [CONFIRMED]
DOOR_FLAGS = 0x800C86B4  # bitfield, 4 bytes [CONFIRMED]
ITEM_BOX_BASE = 0x800C8724  # 2 bytes per slot: item_id, qty [CONFIRMED]
MAPS_FILES_FLAGS = 0x800C8714  # [CONFIRMED]
PLAYER_POS_BLOCK = 0x800C8784  # [LIKELY — verify via BizHawk]
ROOM_ID = None  # UNKNOWN — RAM search near 0x800C86xx

# PC GOG savedatN.dat inventory block (2 bytes per slot: id, qty)
PC_SAVE_INVENTORY_OFFSET = 0x320
# Observed on real GOG saves: 11 fixed slots; slot 7 may be empty (00 00) with
# later slots still populated; terminator pattern 09 FF often follows slot data.
PC_SAVE_INVENTORY_SLOTS = 11

PLAYER_HP_MAX = 0x8C  # 140

# PS1 item IDs (hex -> name). PC port may use overlapping but not identical IDs.
ITEM_IDS: dict[int, str] = {
    0x01: "knife",
    0x02: "beretta",
    0x03: "shotgun",
    0x0B: "first_aid_spray",
    0x1F: "emblem",
    0x20: "gold_emblem",
    0x21: "blue_jewel",
    0x22: "red_jewel",
    0x26: "chemical",
    0x27: "battery",
    0x28: "mo_disc",
    0x29: "wind_crest",
    0x2C: "moon_crest",
    0x2D: "star_crest",
    0x2E: "sun_crest",
    0x30: "lighter",
    0x31: "lockpick",
    0x33: "sword_key",
    0x34: "armor_key",
    0x35: "shield_key",
    0x36: "helmet_key",
    0x43: "red_herb",
    0x44: "green_herb",
    0x45: "blue_herb",
}

# Default RAM fields read by the bridge / env observation vector.
DEFAULT_RAM_FIELDS: list[tuple[str, int, str]] = [
    ("player_hp", PLAYER_HP, "u16"),
    ("game_timer", GAME_TIMER, "u32"),
    ("lab_timer", LAB_TIMER, "u16"),
    ("door_flags", DOOR_FLAGS, "u32"),
    ("maps_files_flags", MAPS_FILES_FLAGS, "u16"),
    ("player_pos_block", PLAYER_POS_BLOCK, "u32"),
]


def ps1_to_mainram_offset(bus_address: int) -> int:
    """Convert PS1 bus address to BizHawk MainRAM domain offset."""
    return bus_address - PS1_MAINRAM_BASE
