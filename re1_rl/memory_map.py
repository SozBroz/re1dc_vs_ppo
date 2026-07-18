"""PS1 (SLUS-00170) and PC GOG memory / save-file constants."""

from __future__ import annotations

import math

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

# Game-mode / in-control byte [CONFIRMED via movement probes 2026-07-02].
# High byte of the gameState dword at 0x800C3000 (linear map of the
# autosplitter's GOG gameState 0x7E41C0; their check `& 0x90000000` uses the
# same bits). Observed: 0x80 = player control; 0x42/0x46 = door transition /
# in-engine cutscene; 0x44 = opening narration. Test bit 0x80.
GAME_STATE = 0x800C3000  # dword; autosplitter in-control check uses & 0x90000000
GAME_MODE = 0x800C3003  # high byte of GAME_STATE (LE)
OPENING_NARRATION_GAME_MODE = 0x44  # FMV / text crawl before mansion control
# Fresh-ROM helicopter / STARS intro FMV (play_human hunt 2026-07-07).
OPENING_FMV_GAME_STATE = 0x80040000
OPENING_FMV_SCENE_FLAG = 0x04
# Capcom / "Press Any Button" attract after intro FMV (room byte still 0).
PRESS_ANY_BUTTON_GAME_STATE = 0x80000000
# In-engine opening action preview (Gallery / Main Hall path); same gs/mode
# bytes as PS logo — disambiguate with hp>0 and room 6/7.
OPENING_GAMEPLAY_TEASER_GAME_STATE = 0x40000000
OPENING_GAMEPLAY_TEASER_GAME_MODE = 0x40
OPENING_TEASER_ROOM_IDS = (6, 7)  # 106 Main Hall, 107 Gallery
PLAYSTATION_LOGO_GAME_STATE = 0x40000000
PLAYSTATION_LOGO_GAME_MODE = 0x40
IN_CONTROL_MASK = 0x80
IN_CONTROL_GAMESTATE_MASK = 0x90000000

# In-game pause / ITEM / CONFIG / controller OPTIONS EDIT (hunt 2026-07-06,
# scripts/hunt_controller_config_screen.py on jill_control_fresh.State).
# Sub-screens vary in the low byte (0x40808000 ITEM grid, 0x40808004 STATUS/ECG).
PAUSE_MENU_GAME_STATE = 0x40808000
PAUSE_MENU_GAME_STATE_MASK = 0xFFFFFF00
PAUSE_MENU_GAME_MODE = 0x40
# STATUS / ECG health-bar sub-screen (live hunt 2026-07-08, play_human :7788).
# Same 0x008080xx session tag as ITEM but high byte 0x60 and mode 0x60.
STATUS_ECG_GAME_STATE = 0x60808000
STATUS_ECG_GAME_MODE = 0x60
PAUSE_MENU_GAME_MODES = frozenset({PAUSE_MENU_GAME_MODE, STATUS_ECG_GAME_MODE})
# OPTIONS / CONFIG subtree while ``game_mode`` stays 0x80 (live hunt 2026-07-07,
# play_human port 7780 — START -> CONFIG/OPTIONS). Distinct from dining play
# ``gs=0x80800000`` by the ``0x8000`` tag in the session dword.
OPTIONS_MENU_GAME_STATE = 0x80808000
OPTIONS_MENU_GAME_MODE = 0x80
GAME_TIMER = 0x800C867C  # [CONFIRMED]
LAB_TIMER = 0x800C867A  # [CONFIRMED]
DOOR_FLAGS = 0x800C86B4  # bitfield, 4 bytes [CONFIRMED]
ITEM_BOX_BASE = 0x800C8724  # 2 bytes per slot: item_id, qty [CONFIRMED]
# NOTE (2026-07-12, deferred): live QS5 dump shows the box array runs 48 slots
# (96 bytes) contiguously into INVENTORY_BASE @ 0x800C8784 — sparse UI scroll can
# park items past index 15 (e.g. knife at box[45]). Code still uses BOX_SLOTS=16
# until we widen withdraw actions; do not treat “first 16 empty” as “box empty”.
MAPS_FILES_FLAGS = 0x800C8714  # [CONFIRMED]
# Large Gallery (117) cradle-to-grave sequence. Correct Yes presses overwrite
# this byte with a step-specific one-hot; an out-of-order Yes clears it to 0.
GALLERY_PROGRESS = 0x800C3008  # u8 [CONFIRMED live, QuickSave3, 2026-07-17]
GALLERY_CONFIRM = 0x800C3009  # u8; changes on any portrait Yes confirmation
# Player entity block [CONFIRMED via live walk trace 2026-07-02, verify_pos.py]:
# X/Z step ~64-162 units per frame while walking; facing full circle = 4096
# (0x1000), turning ~192/quarter-second. Y is elevation (0 on ground floor).
PLAYER_X = 0x800C5158  # s16 (part of s32); world units
PLAYER_Y = 0x800C515C  # s16; elevation
PLAYER_Z = 0x800C5160  # s16; world units
PLAYER_FACING = 0x800C5198  # u16; 0..4095 angle, left turn decreases

# Player animation / action lock [HUNT CONFIRMED 2026-07-06,
# scripts/hunt_player_knife_anim.py on jill_control_fresh.State].
# 0x12 + aux 0x04 = crouched knife aim; press cross only in this state.
# 0x13 = late recovery; 0x00/0x00 = idle (next action allowed).
# 0x01 = walk pressed into a collider; 0x10 + gs 0x80800044 = shelf/object push
# (bar bookcase probe 2026-07-10). See re1_rl/pushable.py.
PLAYER_ANIM_STATE = 0x800C51AA  # u8; 0=idle, 0x12/0x13=knife swing/recovery
PLAYER_ACTION_AUX = 0x800C51A9  # u8; 0=idle, 0x04=knife active
PLAYER_RECOVERY_TIMER = 0x800C51B0  # u8; counts down 13→0 during recovery

# Poison flag [CANDIDATE — save struct Pl_poison_down is 0xB bytes before Pl_life
# at 0x21A; PLAYER_HP maps to Pl_life. Verify with Yawn / blue-herb hunt.]
PLAYER_POISON = 0x800C51A1  # u8; nonzero = poisoned

# Stage/room/camera block [CONFIRMED via autonomous boot run 2026-07-02].
# Source: RE1-Autosplitter (deserteagle417) EnglishGOG addresses, mapped
# GOG -> PS1 with the linear offset -0x7211C0 (anchor: HP 0x7E636C -> 0xC51AC),
# then verified in ram_log.csv: room byte hit 6 (MAIN HALL 106) the same frame
# HP initialised, then 5 (DINING ROOM 105) during the scripted intro cutscene.
STAGE_ID = 0x800C8660  # byte; 0-indexed (stage 0 = Mansion 1, "1XX" rooms)
ROOM_ID = 0x800C8661  # byte; room number within stage (6 = Main Hall 106)
# Title / front-end menu stack (recon boot: stage=0 room=27 hp=0 before mansion).
# Not the in-game STORE ROOM code 11B — that is stage 0 room 0x1B (27) only on menus.
MENU_ROOM_ID = 27
# Front-end New Game / Load Game stack (recon boot frame ~1950, death continue hunt).
MAIN_MENU_GAME_STATE = 0x80000000
MAIN_MENU_GAME_MODE = 0x80
# Brief transition into the menu stack seen after opening teaser (fresh boot hunt).
MAIN_MENU_ENTER_GAME_STATE = 0x41000000
MAIN_MENU_ENTER_GAME_MODE = 0x41
# Authentic engine death UI after live damage (hunt 2026-07-07, Kenneth kill).
# Low nibble varies: ``0x81800004`` (Kenneth), ``0x81800000`` (dog/hunter fade).
DEATH_UI_GAME_STATE = 0x81800004
DEATH_UI_GAME_STATE_MASK = 0xFFFFF000
DEATH_UI_GAME_MODE = 0x81
# Post-death Continue / Game Over prompt (dog-death hunt QuickSave4, frame ~396).
DEATH_CONTINUE_GAME_STATE = 0x41800000
DEATH_CONTINUE_GAME_MODE = 0x41
# In-room death overlay while HP RAM still shows last live value (QuickSave3 hunt).
DEATH_ROOM_OVERLAY_GAME_STATE = 0x80800001
# Engine HP sentinel during scripted kills / white fade (not a real HP value).
SCRIPTED_DEATH_HP = 0xFFFF
CAM_ID = 0x800C8662  # byte; fixed-camera index, changes on every camera cut
CHARACTER_ID = 0x800C8669  # byte; 0 = Chris, 1 = Jill

# Equipped weapon [HUNT CONFIRMED 2026-07-07, hunt_equipped_weapon2.py +
# verify_equipped_byte.py + collect_weapon_frame_data.py slot-drain probe].
# 0x800C5126 holds the ITEM id (0x01 knife, 0x02 beretta, ...; 0 = none).
# 0x800C8689 is the 1-BASED equipped inventory slot index — NOT an id
# mirror (it merely matched the beretta's id by coincidence: id 0x02 in
# slot 1 -> 1-based index 2). Firing drains ammo from slot (index - 1).
# RAM-equip recipe: write the id, the 1-based slot index, and the 0-based
# slot byte below; engine accepts it (live fire consumes correct ammo).
EQUIPPED_WEAPON_ID = 0x800C5126  # u8; equipped item id
EQUIPPED_SLOT_INDEX_1BASED = 0x800C8689  # u8; 1-based inventory slot, 0 = none
# 0-based inventory slot of the equipped item [observed: knife slot0 ->
# 0x00, beretta slot1 -> 0x01 across menu equips; not independently isolated].
EQUIPPED_SLOT_INDEX = 0x800C50BE  # u8

# ITEM-screen action submenu (live hunt QuickSave0 2026-07-12):
# After cross opens EQUIP/USE/CHECK/COMBN list:
#   0x800B7FE9 = number of entries (u8)
#   0x800B7FF4 = highlighted index (u8, 0-based)
# Across weapon / spray / ammo, COMBN is the last entry.
ITEM_SUBMENU_N_ENTRIES = 0x800B7FE9  # u8
ITEM_SUBMENU_CURSOR = 0x800B7FF4  # u8

# Item ids that count as weapons for action masking / attack macros.
WEAPON_ITEM_IDS: frozenset[int] = frozenset(
    {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x6F, 0x70}
)

# Player inventory [CONFIRMED via jill_control.State dump 2026-07-02]:
# GOG inventory 0x838814 - EnglishGOG inventoryOffset 0x4EED0 = 0x7E9944,
# linear map -0x7211C0 -> 0x800C8784. 2 bytes per slot (item_id, qty);
# Jill uses 8 slots (Chris 6). Fresh DC Jill start reads:
# knife / beretta qty 15 / first_aid_spray_alt qty 1.
INVENTORY_BASE = 0x800C8784
INVENTORY_SLOTS = 8
# ITEM-screen grid icons are NOT refreshed by writing INVENTORY_BASE alone.
# They live in BizHawk GPURAM; see re1_rl.inventory_icons (hunt 2026-07-12).

# PC GOG savedatN.dat inventory block (2 bytes per slot: id, qty)
PC_SAVE_INVENTORY_OFFSET = 0x320
# Observed on real GOG saves: 11 fixed slots; slot 7 may be empty (00 00) with
# later slots still populated; terminator pattern 09 FF often follows slot data.
PC_SAVE_INVENTORY_SLOTS = 11

PLAYER_HP_MAX = 0x8C  # 140


def player_died(
    hp: int,
    *,
    prev_hp: int = 0,
    episode_start_hp: int = 0,
) -> bool:
    """True when the character is dead (not the pre-init hp==0 cutscene case)."""
    if int(hp) > 0:
        return False
    return int(prev_hp) > 0 or int(episode_start_hp) > 0


# --- Engine code patches (GameShark-style, re-written every frame) ---
# Source: gamehacking.org/game/89739 (SLUS-00551, hacker nolberto82). Both
# patch CODE halfwords, not game state, so they are safe at any moment and
# must be re-applied each frame (savestate loads revert MainRAM).
#
# "Faster Door Sequence": the door-animation loop exits via a conditional
# branch whose upper halfword lives at 0x8001A64E; writing 0x1000 turns the
# instruction into `beq $zero,$zero,...` (always taken), so the door
# open/close sequence ends immediately. This is the PS1 analogue of the PC
# "Automatic Door Skip" mod, which NOPs the door-skip trigger call at
# Biohazard.exe+0x8D715 (see data/Biohazard.CT).
DOOR_SKIP_PATCH_ADDR = 0x8001A64E
DOOR_SKIP_PATCH_VALUE = 0x1000

# "Turbo Mode During Cutscenes": NOP (0x2400 = addiu $zero,...) the
# frame-wait upper halfword at 0x8007BAF6 while NOT in player control, so
# in-engine cutscenes tick at double logic rate; restore the original
# halfword 0x0044 once control returns. nolberto82's published condition is
# u16@0x800C3002 in {0x8080, 0x8000}; both share bit 7 of the high byte,
# i.e. our confirmed GAME_MODE & IN_CONTROL_MASK check.
CUTSCENE_TURBO_ADDR = 0x8007BAF6
CUTSCENE_TURBO_VALUE = 0x2400
CUTSCENE_TURBO_RESTORE = 0x0044

# Message/dialogue window flag [CONFIRMED via RAM diff scan 2026-07-03,
# scripts/_diag_dialog_flag8.py]. Talk/examine text boxes (e.g. Barry's
# blood-check dialogue) do NOT clear GAME_MODE bit 0x80 — the only reliable
# tell is bit 0x80 here: 0x00 idle/walking/aiming, 0x80 while any message
# window is open, 0x00 again once dismissed. Verified on two separate modals.
MESSAGE_FLAG = 0x800C8665
MESSAGE_FLAG_MASK = 0x80

# --- Enemy table [CONFIRMED 2026-07-12 live fire hunt] ---
# Gameshark "First Zombie Has Infinite Health" / "Zombie Health (House)" use
# 0x800C532C. Live beretta fire on QuickSave1 (room 202): u16 there drops by
# ~12 per hit; second living enemy at +0x18C tracks the same. MediaKite ASL
# heap map 0x801141FC was WRONG (never changed on hits) — combat rewards were
# silent because decode_enemy_table read garbage there.
ENEMY_TABLE_BASE: int | None = 0x800C532C
ENEMY_SLOT_STRIDE = 0x18C  # confirmed: GS first zombie + second slot
ENEMY_TABLE_SLOTS = 6
# Per-slot HP at struct base (ASL / GS). Cap rejects empty-slot garbage.
ENEMY_HP_MAX_PLAUSIBLE = 2000
# Off-map pool slots park near ~(30000, 30000); in-room entities use map scale.
ENEMY_POOL_COORD_ABS_MAX = 20000
# Knife/gun mask: enemy within this world distance of the player.
# Gun keeps the wide envelope; knife is tighter but still generous vs
# typical hit distances (~700–1500) so mid-room melee stays legal.
ENEMY_COMBAT_NEAR_DIST = 8000
ENEMY_KNIFE_COMBAT_NEAR_DIST = 5000
ENEMY_FIELD_OFFSETS: dict[str, tuple[int, str]] = {
    "hp": (0, "u16"),
    "x": (0xDE, "s16"),
    "z": (0xE0, "s16"),
    "active_byte": (0xEC, "u8"),
}


def enemy_coords_in_room_band(x: int, z: int) -> bool:
    """Reject off-map pool coordinates (stale HP ghosts).

    Also reject the null/origin park ``(0, 0)``: empty slots often keep a
    plausible HP there, and with ``ENEMY_COMBAT_NEAR_DIST`` that ghost sits
    "in range" of most early-mansion poses — unlocking endless miss macros
    that crush fleet step/s.
    """
    ax, az = abs(int(x)), abs(int(z))
    if ax == 0 and az == 0:
        return False
    return ax < ENEMY_POOL_COORD_ABS_MAX and az < ENEMY_POOL_COORD_ABS_MAX


def enemy_table_fields() -> list[tuple[str, int, str]]:
    """RAM field tuples for all enemy slots; empty until the base is known."""
    if ENEMY_TABLE_BASE is None or not ENEMY_FIELD_OFFSETS:
        return []
    fields: list[tuple[str, int, str]] = []
    for slot in range(ENEMY_TABLE_SLOTS):
        base = ENEMY_TABLE_BASE + slot * ENEMY_SLOT_STRIDE
        for fname, (off, dtype) in ENEMY_FIELD_OFFSETS.items():
            fields.append((f"enemy{slot}_{fname}", base + off, dtype))
    return fields


def decode_enemy_table(ram: dict[str, int | float]) -> list[dict[str, int]]:
    """[{x, z, hp, alive, in_room, combat_near, ...}] from enemy table RAM."""
    out: list[dict[str, int]] = []
    if ENEMY_TABLE_BASE is None or not ENEMY_FIELD_OFFSETS:
        return out
    px = int(ram.get("player_x", 0))
    pz = int(ram.get("player_z", 0))
    for slot in range(ENEMY_TABLE_SLOTS):
        vals = {f: int(ram.get(f"enemy{slot}_{f}", 0)) for f in ENEMY_FIELD_OFFSETS}
        hp = vals.get("hp", 0)
        if hp <= 0 or hp > ENEMY_HP_MAX_PLAUSIBLE:
            continue
        x = int(vals.get("x", 0))
        z = int(vals.get("z", 0))
        in_room = enemy_coords_in_room_band(x, z)
        dist = math.hypot(px - x, pz - z)
        combat_near = in_room and dist < ENEMY_COMBAT_NEAR_DIST
        knife_near = in_room and dist < ENEMY_KNIFE_COMBAT_NEAR_DIST
        vals["slot"] = slot
        vals["alive"] = 1 if in_room else 0
        vals["in_room"] = 1 if in_room else 0
        vals["combat_near"] = 1 if combat_near else 0
        vals["knife_near"] = 1 if knife_near else 0
        vals["dist"] = int(dist)
        out.append(vals)
    return out


# Interaction prompt ("Press X" affordance) [HUNT PENDING — see
# scripts/hunt_interaction_prompt.py]. MESSAGE_FLAG below fires only once a
# message window is OPEN; the pre-press prompt bit is still unmapped.
INTERACTION_PROMPT: int | None = None
INTERACTION_PROMPT_MASK = 0x80

# Scripted-scene flag [CONFIRMED via ground-truth probe 2026-07-03,
# scripts/_diag_dialog_flag11.py]. During scripted spans of in-engine scenes
# (e.g. Barry main-hall conversation between camera cuts) GAME_MODE bit 0x80
# stays SET and no message window is open, yet the player is frozen. Byte
# u8@0x800C3002 goes 0x80 -> 0x90 there: bit 0x10 marks the scripted scene.
# Consistent with nolberto82's turbo condition (in-control iff u16@0x800C3002
# in {0x8080, 0x8000} — 0x8090 fails it).
SCENE_FLAG = 0x800C3002
SCENE_FLAG_MASK = 0x10
# Idle in-room camera baseline (u8@SCENE_FLAG). Kenneth tea-room scare uses 0x84
# (bit 0x04); hunter/dog kills use 0x90 (bit 0x10). See play_human 7788 hunt.
SCENE_FLAG_IDLE = 0x80

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
    # Live QuickSave0 inventory: id 0x0B qty 30 renders as "Handgun Bullets"
    # (was mislabeled first_aid_spray). First Aid Spray is 0x41.
    0x0B: "handgun_bullets",
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
    0x46: "mixed_herbs_gr",
    0x47: "mixed_herbs_gg",
    0x48: "mixed_herbs_gb",
    0x49: "mixed_herbs_grb",
    0x4A: "mixed_herbs_ggg",
    0x4B: "mixed_herbs_ggb",
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
    ("gallery_progress", GALLERY_PROGRESS, "u8"),
    ("gallery_confirm", GALLERY_CONFIRM, "u8"),
    ("player_x", PLAYER_X, "s16"),
    ("player_y", PLAYER_Y, "s16"),
    ("player_z", PLAYER_Z, "s16"),
    ("player_facing", PLAYER_FACING, "u16"),
    ("stage_id", STAGE_ID, "u8"),
    ("room_id", ROOM_ID, "u8"),
    ("cam_id", CAM_ID, "u8"),
    ("character_id", CHARACTER_ID, "u8"),
    ("game_mode", GAME_MODE, "u8"),
    ("game_state", GAME_STATE, "u32"),
    ("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8"),
    # inventory slots: 2 bytes each (item_id, qty), read as u16 little-endian
    # -> low byte = item_id, high byte = qty
    ("inv_slot_0", INVENTORY_BASE + 0x0, "u16"),
    ("inv_slot_1", INVENTORY_BASE + 0x2, "u16"),
    ("inv_slot_2", INVENTORY_BASE + 0x4, "u16"),
    ("inv_slot_3", INVENTORY_BASE + 0x6, "u16"),
    ("inv_slot_4", INVENTORY_BASE + 0x8, "u16"),
    ("inv_slot_5", INVENTORY_BASE + 0xA, "u16"),
    ("inv_slot_6", INVENTORY_BASE + 0xC, "u16"),
    ("inv_slot_7", INVENTORY_BASE + 0xE, "u16"),
]


def decode_inventory(ram: dict[str, int | float]) -> list[tuple[str, int]]:
    """(item_name, qty) for occupied slots from a DEFAULT_RAM_FIELDS read."""
    out: list[tuple[str, int]] = []
    for i in range(INVENTORY_SLOTS):
        raw = int(ram.get(f"inv_slot_{i}", 0))
        item_id, qty = raw & 0xFF, raw >> 8
        if item_id:
            out.append((ITEM_IDS.get(item_id, f"unknown_0x{item_id:02X}"), qty))
    return out


def ps1_to_mainram_offset(bus_address: int) -> int:
    """Convert PS1 bus address to BizHawk MainRAM domain offset."""
    return bus_address - PS1_MAINRAM_BASE
