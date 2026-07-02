"""PS1 (SLUS-00170) and PC GOG memory / save-file constants."""

from __future__ import annotations

# BizHawk MainRAM domain offset = PS1 bus address - 0x80000000
PS1_MAINRAM_BASE = 0x80000000

# --- PS1 — MainRAM bus addresses ---
# Our disc: Director's Cut, serial SLUS-00551 (verified from disc image).
# Addresses below were seeded from original-release (SLUS-00170) GameShark DBs;
# the SLUS-00551 cheat list (epsxe SLUS_005.51.txt) confirms PLAYER_HP is the
# SAME address in Standard/Original mode, so the save block likely matches.
# Advanced/Arranged mode uses a different block (~0x800B8BC6) — avoid Arranged.
GAME_SERIAL = "SLUS-00551"
PLAYER_HP = 0x800C51AC  # u16; max 0x8C (140) Jill/Chris [CONFIRMED, both serials]
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

# Item IDs (hex -> name). Full table from the PS1 GameShark item list (8bs.com
# mirror) cross-checked against PC savedat hex codes (cheatinfo.de) — the two
# platforms use the SAME IDs. Verified against real GOG saves (see save_parser).
# Key naming per PC save-hacking docs: 33 sword, 34 armor, 35 shield, 36 helmet.
# (One PS1 mirror lists 33-36 generically as "Mansion Key".)
ITEM_IDS: dict[int, str] = {
    0x01: "knife",
    0x02: "beretta",
    0x03: "shotgun",
    0x04: "colt_python_dumdum",
    0x05: "colt_python",
    0x06: "flamethrower",
    0x07: "bazooka_acid",
    0x08: "bazooka_explosive",
    0x09: "bazooka_flame",
    0x0A: "rocket_launcher",
    0x0B: "first_aid_spray",
    0x0C: "shotgun_shells",
    0x0D: "dumdum_rounds",
    0x0E: "magnum_rounds",
    0x0F: "flamethrower_fuel",
    0x10: "explosive_rounds",
    0x11: "acid_rounds",
    0x12: "flame_rounds",
    0x13: "empty_bottle",
    0x14: "water",
    0x15: "umb_no2",
    0x16: "umb_no4",
    0x17: "umb_no7",
    0x18: "umb_no13",
    0x19: "yellow_6",
    0x1A: "n_p003",
    0x1B: "v_jolt",
    0x1C: "broken_shotgun",
    0x1D: "square_crank",
    0x1E: "hex_crank",
    0x1F: "emblem",
    0x20: "gold_emblem",
    0x21: "blue_jewel",
    0x22: "red_jewel",
    0x23: "music_notes",
    0x24: "wolf_medal",
    0x25: "eagle_medal",
    0x26: "chemical",
    0x27: "battery",
    0x28: "mo_disc",
    0x29: "wind_crest",
    0x2A: "flare",
    0x2B: "slides",
    0x2C: "moon_crest",
    0x2D: "star_crest",
    0x2E: "sun_crest",
    0x2F: "ink_ribbon",
    0x30: "lighter",
    0x31: "lockpick",
    0x33: "sword_key",
    0x34: "armor_key",
    0x35: "shield_key",
    0x36: "helmet_key",
    0x37: "lab_key_1",
    0x38: "special_key",
    0x39: "dorm_key_002",
    0x3A: "dorm_key_003",
    0x3B: "control_room_key",
    0x3C: "lab_key_2",
    0x3D: "small_key",
    0x3E: "red_book",
    0x3F: "doom_book_2",
    0x40: "doom_book_1",
    0x41: "first_aid_spray_alt",
    0x42: "serum",
    0x43: "red_herb",
    0x44: "green_herb",
    0x45: "blue_herb",
    0x46: "mixed_herbs",
    0x6F: "ingram_inf",  # PC-only bonus weapon
    0x70: "minimi_inf",  # PC-only bonus weapon
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
