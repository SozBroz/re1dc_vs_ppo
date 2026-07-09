# RE1 PC Cheat Engine Address Map

Extracted from `ResidentEvil.CT` (GOG / `ResidentEvil.exe`) and `Biohazard.CT` (MediaKite / `Biohazard.exe`).

## Navigation-relevant finds

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Player X Coord | Biohazard.exe+0x8351E8 | 4 Bytes | — | Biohazard.CT |
| Player Y Coord | Biohazard.exe+0x8351EC | 4 Bytes | — | Biohazard.CT |
| Player Z Coord | Biohazard.exe+0x8351F0 | 4 Bytes | — | Biohazard.CT |
| Current Room | Biohazard.exe+0x8386F1 | Byte | — | Biohazard.CT |
| Disassembler comment: Do not touch: This is where rooms are loaded? | Biohazard.exe+0x8D80D | disassembler_comment | — | Biohazard.CT |
| Entity Flag | Biohazard.exe+0x96ECB4 | 4 Bytes | — | Biohazard.CT |
| Story Line | Biohazard.exe+0x833092 | Byte | — | Biohazard.CT |

### MediaKite navigation anchors (Biohazard.CT)

| Description | module+offset | type | table |
|---|---|---|---|
| Disassembler comment: Door closing trigger, nop this to remove its sound when loading the room | Biohazard.exe+0x8D85F | disassembler_comment | Biohazard.CT |
| Map Flag | Biohazard.exe+0x838794 | Array of byte | Biohazard.CT |
| Player X Coord | Biohazard.exe+0x8351E8 | 4 Bytes | Biohazard.CT |
| Player Y Coord | Biohazard.exe+0x8351EC | 4 Bytes | Biohazard.CT |
| Player Z Coord | Biohazard.exe+0x8351F0 | 4 Bytes | Biohazard.CT |
| Current Room | Biohazard.exe+0x8386F1 | Byte | Biohazard.CT |
| Disassembler comment: Do not touch: This is where rooms are loaded? | Biohazard.exe+0x8D80D | disassembler_comment | Biohazard.CT |
| Entity Flag | Biohazard.exe+0x96ECB4 | 4 Bytes | Biohazard.CT |

### GOG probe candidates (no direct nav entries)

Nearest known GOG work-RAM cluster (`ResidentEvil.exe+0x7E6300`–`0x7E9900`):

| Address | Known use | Why probe |
|---|---|---|
| ResidentEvil.exe+0x7E62E4 | Character/scenario flags (script ref) | Adjacent to HP; may encode player state |
| ResidentEvil.exe+0x7E41C0 | Global event flag dword (timer script) | Bitfield touched on room transitions / saves |
| ResidentEvil.exe+0x7E41C7 | Byte flag tested with timer | Door/cutscene gating near event block |
| ResidentEvil.exe+0x7E9826 | Byte compared during save (stage-like) | Values 0x33–0x3D range — possible area/stage ID |
| ResidentEvil.exe+0x7E9849 | Ammo slot index byte | Inventory-adjacent; scan ±0x100 for room byte |
| ResidentEvil.exe+0x7EBCD4 | Player entity pointer (HP damage script) | Deref +0x98 → position struct candidate |
| ResidentEvil.exe+0x7E0DF8 | Pointer used in combat routine | May reference active room/entity table |

Disassembler hint in Biohazard.CT: `Biohazard.exe+0x8D80D` — comment *"Do not touch: This is where rooms are loaded?"* (code address, not a data variable).

## PS1 ↔ PC correlation

Known PS1 bus addresses (GameShark era):

- PS1 `hp` = `0x800C51AC`
- PS1 `timer` = `0x800C867C`
- PS1 `item_box` = `0x800C8724`
- PS1 `door_flags` = `0x800C86B4`
- PS1 `maps_flags` = `0x800C8714`

**Anchor:** PS1 timer `0x800C867C` ↔ GOG PC `ResidentEvil.exe+0x7E983C`.

Implied linear map: `PC_offset = 0x7E983C + (PS1_addr - 0x800C867C)`

| PS1 symbol | PS1 addr | Δ from PS1 timer | Predicted GOG offset | Verified in CE table? |
|---|---:|---:|---:|---|
| hp | 0x800C51AC | -13520 (0x34D0) | 0x7E636C | Yes — Current HP |
| timer | 0x800C867C | +0 (0x0) | 0x7E983C | Yes — Low Game Timer |
| item_box | 0x800C8724 | +168 (0xA8) | 0x7E98E4 | Yes — Chest inventory base |
| door_flags | 0x800C86B4 | +56 (0x38) | 0x7E9874 | Predicted only (not in CE table) |
| maps_flags | 0x800C8714 | +152 (0x98) | 0x7E98D4 | Predicted only (not in CE table) |

**Room ID (PS1) — not in supplied GameShark list.** Community/autosplitter probes often use stage+room bytes in the `0x800C98xx` region. Using the same linear map:

| Guess label | PS1 addr | Predicted GOG offset |
|---|---:|---:|
| Current room byte (common probe) | 0x800C9884 | 0x7EAA44 |
| Stage / area byte (common probe) | 0x800C9880 | 0x7EAA40 |

**Verification:** GOG `ResidentEvil.CT` does **not** list room ID or XYZ. MediaKite maps room to `Biohazard.exe+0x8386F1` and position to `+0x8351E8/+EC/+F0` — different EXE layout; do not apply GOG PS1 linear map to MediaKite offsets.

**HP check:** PS1 `0x800C51AC` → predicted `0x7E636C` matches GOG `Current HP` entry exactly (confirms map).

## ammo

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Toggle Typewriters don't require Ink Ribbons (script ref) | Biohazard.exe+0x56636 | script_reference | — | Biohazard.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5812B | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58130 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58133 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58139 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5813C | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58143 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5814A | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58150 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58152 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58158 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5815D | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58161 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58165 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58167 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5816C | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5816E | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58170 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58172 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x58174 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5817A | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5817D | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x581D3 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x582AB | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5A370 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x5A4B0 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x6325A | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x6325B | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x6325C | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x6325E | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63264 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63266 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x6326D | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63271 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63273 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63275 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63277 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63279 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x6327B | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63280 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63282 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63286 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63288 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x6328F | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63291 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x63298 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x6329A | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x632A6 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x632AE | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x7E0DEC | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x7E41C0 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x7E63A2 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x7E9826 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x7E9849 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0x922768 | script_reference | — | ResidentEvil.CT |
| Set Ink Ribbons To 50 When Saving (script ref) | ResidentEvil.exe+0x922768 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0xC0DD8 | script_reference | — | ResidentEvil.CT |
| Infinite Ammo (script ref) | ResidentEvil.exe+0xC0DDC | script_reference | — | ResidentEvil.CT |

## character

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Character Outfit | Biohazard.exe+0x3A7544 | Byte | — | Biohazard.CT |
| Player Character | Biohazard.exe+0x8386F9 | Byte | — | Biohazard.CT |

## door_flags

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Disassembler comment: Door Skip Trigger, nop this to make it work | Biohazard.exe+0x8D715 | disassembler_comment | — | Biohazard.CT |
| Toggle Door Skip (script ref) | Biohazard.exe+0x8D715 | script_reference | — | Biohazard.CT |
| Disassembler comment: Door closing trigger, nop this to remove its sound when loading the room | Biohazard.exe+0x8D85F | disassembler_comment | — | Biohazard.CT |
| Toggle Door Skip (script ref) | Biohazard.exe+0x8D85F | script_reference | — | Biohazard.CT |

## event_flags

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Game Status Flag | Biohazard.exe+0x833090 | Array of byte | — | Biohazard.CT |
| System Flag | Biohazard.exe+0x833094 | Array of byte | — | Biohazard.CT |
| Game System Trigger | Biohazard.exe+0x8384E8 | Array of byte | — | Biohazard.CT |
| Camera Cut | Biohazard.exe+0x8386F2 | Byte | — | Biohazard.CT |
| Map Flag | Biohazard.exe+0x838794 | Array of byte | — | Biohazard.CT |
| Character Is Controllable | Biohazard.exe+0x83AB90 | Byte | — | Biohazard.CT |

## hp

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Health Status | Biohazard.exe+0x82E94C | Array of byte | — | Biohazard.CT |
| Health Status (Pulse) | Biohazard.exe+0x82E94F | Array of byte | — | Biohazard.CT |
| Player Current Health | Biohazard.exe+0x83523C | Byte | — | Biohazard.CT |
| Player Is Poisoned | Biohazard.exe+0x835290 | Byte | — | Biohazard.CT |
| Player Max Health | Biohazard.exe+0x835329 | Byte | — | Biohazard.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35900 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35905 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35906 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35909 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x3590E | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35915 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35916 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35918 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x3591A | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x3591C | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35924 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x3592B | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x3592D | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x3592F | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35934 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35937 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x3593C | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35942 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x35944 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x3594A | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x3594F | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C1FA | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C200 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C202 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C204 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C20A | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C20C | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C213 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C215 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C21B | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C21D | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C224 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C22B | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C22D | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C232 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C235 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C238 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C23A | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C23F | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C242 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C244 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x3C249 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x7E0DF8 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0x7E62E4 | script_reference | — | ResidentEvil.CT |
| Current HP | ResidentEvil.exe+0x7E636C | Byte | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x7E636C | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x7EBCD4 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x7FCA0 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0x91FDD0 | script_reference | — | ResidentEvil.CT |
| Won't Lose HP When Taking Damage (script ref) | ResidentEvil.exe+0xBB3C8 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0xBB69E | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0xBB6A2 | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0xBBFFE | script_reference | — | ResidentEvil.CT |
| High Damage (script ref) | ResidentEvil.exe+0xBC002 | script_reference | — | ResidentEvil.CT |

## inventory

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Click to update inventory (script ref) | Biohazard.exe+0x14140 | script_reference | — | Biohazard.CT |
| Currently Equipped Item | Biohazard.exe+0x8351B6 | Byte | — | Biohazard.CT |
| Total Items | Biohazard.exe+0x8386F7 | Byte | — | Biohazard.CT |
| Equipped Weapon Inventory Slot Position (Don't edit) | Biohazard.exe+0x838719 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387B5 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387B7 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387B9 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387BB | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387BD | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387BF | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387C1 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387C3 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387C5 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387C7 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387C9 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387CB | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387CD | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387CF | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x8387D1 | Byte | — | Biohazard.CT |
| Inventory | Biohazard.exe+0x838813 | Unknown | — | Biohazard.CT |
| Inventory Item 1 | Biohazard.exe+0x838814 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x838815 | Byte | — | Biohazard.CT |
| Inventory Item 2 | Biohazard.exe+0x838816 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x838817 | Byte | — | Biohazard.CT |
| Inventory Item 3 | Biohazard.exe+0x838818 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x838819 | Byte | — | Biohazard.CT |
| Inventory Item 4 | Biohazard.exe+0x83881A | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x83881B | Byte | — | Biohazard.CT |
| Inventory Item 5 | Biohazard.exe+0x83881C | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x83881D | Byte | — | Biohazard.CT |
| Inventory Item 6 | Biohazard.exe+0x83881E | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x83881F | Byte | — | Biohazard.CT |
| Inventory Item 7 (Jill Only - Don't edit if playing as Chris) | Biohazard.exe+0x838820 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x838821 | Byte | — | Biohazard.CT |
| Inventory Item 8 (Jill Only - Don't edit if playing as Chris) | Biohazard.exe+0x838822 | Byte | — | Biohazard.CT |
| Quantity | Biohazard.exe+0x838823 | Byte | — | Biohazard.CT |
| Slot 1 ID | ResidentEvil.exe+0x7E98E4 | Byte | — | ResidentEvil.CT |
| Slot 1 Quantity | ResidentEvil.exe+0x7E98E5 | Byte | — | ResidentEvil.CT |
| Slot 2 ID | ResidentEvil.exe+0x7E98E6 | Byte | — | ResidentEvil.CT |
| Slot 2 Quantity | ResidentEvil.exe+0x7E98E7 | Byte | — | ResidentEvil.CT |
| Slot 3 ID | ResidentEvil.exe+0x7E98E8 | Byte | — | ResidentEvil.CT |
| Slot 3 Quantity | ResidentEvil.exe+0x7E98E9 | Byte | — | ResidentEvil.CT |
| Slot 4 ID | ResidentEvil.exe+0x7E98EA | Byte | — | ResidentEvil.CT |
| Slot 4 Quantity | ResidentEvil.exe+0x7E98EB | Byte | — | ResidentEvil.CT |
| Slot 5 ID | ResidentEvil.exe+0x7E98EC | Byte | — | ResidentEvil.CT |
| Slot 5 Quantity | ResidentEvil.exe+0x7E98ED | Byte | — | ResidentEvil.CT |
| Slot 6 ID | ResidentEvil.exe+0x7E98EE | Byte | — | ResidentEvil.CT |
| Slot 6 Quantity | ResidentEvil.exe+0x7E98EF | Byte | — | ResidentEvil.CT |
| Slot 7 ID | ResidentEvil.exe+0x7E98F0 | Byte | — | ResidentEvil.CT |
| Slot 7 Quantity | ResidentEvil.exe+0x7E98F1 | Byte | — | ResidentEvil.CT |
| Slot 8 ID | ResidentEvil.exe+0x7E98F2 | Byte | — | ResidentEvil.CT |
| Slot 8 Quantity | ResidentEvil.exe+0x7E98F3 | Byte | — | ResidentEvil.CT |
| Slot 9 ID | ResidentEvil.exe+0x7E98F4 | Byte | — | ResidentEvil.CT |
| Slot 9 Quantity | ResidentEvil.exe+0x7E98F5 | Byte | — | ResidentEvil.CT |
| Slot 10 ID | ResidentEvil.exe+0x7E98F6 | Byte | — | ResidentEvil.CT |
| Slot 10 Quantity | ResidentEvil.exe+0x7E98F7 | Byte | — | ResidentEvil.CT |
| Slot 11 ID | ResidentEvil.exe+0x7E98F8 | Byte | — | ResidentEvil.CT |
| Slot 11 Quantity | ResidentEvil.exe+0x7E98F9 | Byte | — | ResidentEvil.CT |
| Slot 12 ID | ResidentEvil.exe+0x7E98FA | Byte | — | ResidentEvil.CT |
| Slot 12 Quantity | ResidentEvil.exe+0x7E98FB | Byte | — | ResidentEvil.CT |
| Slot 13 ID | ResidentEvil.exe+0x7E98FC | Byte | — | ResidentEvil.CT |
| Slot 13 Quantity | ResidentEvil.exe+0x7E98FD | Byte | — | ResidentEvil.CT |
| Slot 14 ID | ResidentEvil.exe+0x7E98FE | Byte | — | ResidentEvil.CT |
| Slot 14 Quantity | ResidentEvil.exe+0x7E98FF | Byte | — | ResidentEvil.CT |
| Slot 15 ID | ResidentEvil.exe+0x7E9900 | Byte | — | ResidentEvil.CT |
| Slot 15 Quantity | ResidentEvil.exe+0x7E9901 | Byte | — | ResidentEvil.CT |
| Slot 16 ID | ResidentEvil.exe+0x7E9902 | Byte | — | ResidentEvil.CT |
| Slot 16 Quantity | ResidentEvil.exe+0x7E9903 | Byte | — | ResidentEvil.CT |
| Slot 17 ID | ResidentEvil.exe+0x7E9904 | Byte | — | ResidentEvil.CT |
| Slot 17 Quantity | ResidentEvil.exe+0x7E9905 | Byte | — | ResidentEvil.CT |
| Slot 18 ID | ResidentEvil.exe+0x7E9906 | Byte | — | ResidentEvil.CT |
| Slot 18 Quantity | ResidentEvil.exe+0x7E9907 | Byte | — | ResidentEvil.CT |
| Slot 19 ID | ResidentEvil.exe+0x7E9908 | Byte | — | ResidentEvil.CT |
| Slot 19 Quantity | ResidentEvil.exe+0x7E9909 | Byte | — | ResidentEvil.CT |
| Slot 20 ID | ResidentEvil.exe+0x7E990A | Byte | — | ResidentEvil.CT |
| Slot 20 Quantity | ResidentEvil.exe+0x7E990B | Byte | — | ResidentEvil.CT |
| Slot 21 ID | ResidentEvil.exe+0x7E990C | Byte | — | ResidentEvil.CT |
| Slot 21 Quantity | ResidentEvil.exe+0x7E990D | Byte | — | ResidentEvil.CT |
| Slot 22 ID | ResidentEvil.exe+0x7E990E | Byte | — | ResidentEvil.CT |
| Slot 22 Quantity | ResidentEvil.exe+0x7E990F | Byte | — | ResidentEvil.CT |
| Slot 23 ID | ResidentEvil.exe+0x7E9910 | Byte | — | ResidentEvil.CT |
| Slot 23 Quantity | ResidentEvil.exe+0x7E9911 | Byte | — | ResidentEvil.CT |
| Slot 24 ID | ResidentEvil.exe+0x7E9912 | Byte | — | ResidentEvil.CT |
| Slot 24 Quantity | ResidentEvil.exe+0x7E9913 | Byte | — | ResidentEvil.CT |
| Slot 25 ID | ResidentEvil.exe+0x7E9914 | Byte | — | ResidentEvil.CT |
| Slot 25 Quantity | ResidentEvil.exe+0x7E9915 | Byte | — | ResidentEvil.CT |
| Slot 26 ID | ResidentEvil.exe+0x7E9916 | Byte | — | ResidentEvil.CT |
| Slot 26 Quantity | ResidentEvil.exe+0x7E9917 | Byte | — | ResidentEvil.CT |
| Slot 27 ID | ResidentEvil.exe+0x7E9918 | Byte | — | ResidentEvil.CT |
| Slot 27 Quantity | ResidentEvil.exe+0x7E9919 | Byte | — | ResidentEvil.CT |
| Slot 28 ID | ResidentEvil.exe+0x7E991A | Byte | — | ResidentEvil.CT |
| Slot 28 Quantity | ResidentEvil.exe+0x7E991B | Byte | — | ResidentEvil.CT |
| Slot 29 ID | ResidentEvil.exe+0x7E991C | Byte | — | ResidentEvil.CT |
| Slot 29 Quantity | ResidentEvil.exe+0x7E991D | Byte | — | ResidentEvil.CT |
| Slot 30 ID | ResidentEvil.exe+0x7E991E | Byte | — | ResidentEvil.CT |
| Slot 30 Quantity | ResidentEvil.exe+0x7E991F | Byte | — | ResidentEvil.CT |
| Slot 31 ID | ResidentEvil.exe+0x7E9920 | Byte | — | ResidentEvil.CT |
| Slot 31 Quantity | ResidentEvil.exe+0x7E9921 | Byte | — | ResidentEvil.CT |
| Slot 32 ID | ResidentEvil.exe+0x7E9922 | Byte | — | ResidentEvil.CT |
| Slot 32 Quantity | ResidentEvil.exe+0x7E9923 | Byte | — | ResidentEvil.CT |
| Slot 33 ID | ResidentEvil.exe+0x7E9924 | Byte | — | ResidentEvil.CT |
| Slot 33 Quantity | ResidentEvil.exe+0x7E9925 | Byte | — | ResidentEvil.CT |
| Slot 34 ID | ResidentEvil.exe+0x7E9926 | Byte | — | ResidentEvil.CT |
| Slot 34 Quantity | ResidentEvil.exe+0x7E9927 | Byte | — | ResidentEvil.CT |
| Slot 35 ID | ResidentEvil.exe+0x7E9928 | Byte | — | ResidentEvil.CT |
| Slot 35 Quantity | ResidentEvil.exe+0x7E9929 | Byte | — | ResidentEvil.CT |
| Slot 36 ID | ResidentEvil.exe+0x7E992A | Byte | — | ResidentEvil.CT |
| Slot 36 Quantity | ResidentEvil.exe+0x7E992B | Byte | — | ResidentEvil.CT |
| Slot 37 ID | ResidentEvil.exe+0x7E992C | Byte | — | ResidentEvil.CT |
| Slot 37 Quantity | ResidentEvil.exe+0x7E992D | Byte | — | ResidentEvil.CT |
| Slot 38 ID | ResidentEvil.exe+0x7E992E | Byte | — | ResidentEvil.CT |
| Slot 38 Quantity | ResidentEvil.exe+0x7E992F | Byte | — | ResidentEvil.CT |
| Slot 39 ID | ResidentEvil.exe+0x7E9930 | Byte | — | ResidentEvil.CT |
| Slot 39 Quantity | ResidentEvil.exe+0x7E9931 | Byte | — | ResidentEvil.CT |
| Slot 40 ID | ResidentEvil.exe+0x7E9932 | Byte | — | ResidentEvil.CT |
| Slot 40 Quantity | ResidentEvil.exe+0x7E9933 | Byte | — | ResidentEvil.CT |
| Slot 41 ID | ResidentEvil.exe+0x7E9934 | Byte | — | ResidentEvil.CT |
| Slot 41 Quantity | ResidentEvil.exe+0x7E9935 | Byte | — | ResidentEvil.CT |
| Slot 42 ID | ResidentEvil.exe+0x7E9936 | Byte | — | ResidentEvil.CT |
| Slot 42 Quantity | ResidentEvil.exe+0x7E9937 | Byte | — | ResidentEvil.CT |
| Slot 43 ID | ResidentEvil.exe+0x7E9938 | Byte | — | ResidentEvil.CT |
| Slot 43 Quantity | ResidentEvil.exe+0x7E9939 | Byte | — | ResidentEvil.CT |
| Slot 44 ID | ResidentEvil.exe+0x7E993A | Byte | — | ResidentEvil.CT |
| Slot 44 Quantity | ResidentEvil.exe+0x7E993B | Byte | — | ResidentEvil.CT |
| Slot 45 ID | ResidentEvil.exe+0x7E993C | Byte | — | ResidentEvil.CT |
| Slot 45 Quantity | ResidentEvil.exe+0x7E993D | Byte | — | ResidentEvil.CT |
| Slot 46 ID | ResidentEvil.exe+0x7E993E | Byte | — | ResidentEvil.CT |
| Slot 46 Quantity | ResidentEvil.exe+0x7E993F | Byte | — | ResidentEvil.CT |
| Slot 47 ID | ResidentEvil.exe+0x7E9940 | Byte | — | ResidentEvil.CT |
| Slot 47 Quantity | ResidentEvil.exe+0x7E9941 | Byte | — | ResidentEvil.CT |
| Slot 48 ID | ResidentEvil.exe+0x7E9942 | Byte | — | ResidentEvil.CT |
| Slot 48 Quantity | ResidentEvil.exe+0x7E9943 | Byte | — | ResidentEvil.CT |
| Slot 1 Item ID | ResidentEvil.exe+0x7E9944 | Byte | — | ResidentEvil.CT |
| Slot 1 Item Quantity | ResidentEvil.exe+0x7E9945 | Byte | — | ResidentEvil.CT |
| Slot 2 Item ID | ResidentEvil.exe+0x7E9946 | Byte | — | ResidentEvil.CT |
| Slot 2 Item Quantity | ResidentEvil.exe+0x7E9947 | Byte | — | ResidentEvil.CT |
| Slot 3 Item ID | ResidentEvil.exe+0x7E9948 | Byte | — | ResidentEvil.CT |
| Slot 3 Item Quantity | ResidentEvil.exe+0x7E9949 | Byte | — | ResidentEvil.CT |
| Slot 4 Item ID | ResidentEvil.exe+0x7E994A | Byte | — | ResidentEvil.CT |
| Slot 4 Item Quantity | ResidentEvil.exe+0x7E994B | Byte | — | ResidentEvil.CT |
| Slot 5 Item ID | ResidentEvil.exe+0x7E994C | Byte | — | ResidentEvil.CT |
| Slot 5 Item Quantity | ResidentEvil.exe+0x7E994D | Byte | — | ResidentEvil.CT |
| Slot 6 Item ID | ResidentEvil.exe+0x7E994E | Byte | — | ResidentEvil.CT |
| Slot 6 Item Quantity | ResidentEvil.exe+0x7E994F | Byte | — | ResidentEvil.CT |
| Slot 7 Item ID (Jill Only - Slot 1 Rebecca) | ResidentEvil.exe+0x7E9950 | Byte | — | ResidentEvil.CT |
| Slot 7 Item Quantity (Jill Only - Slot 1 Rebecca) | ResidentEvil.exe+0x7E9951 | Byte | — | ResidentEvil.CT |
| Slot 8 Item ID (Jill - Slot 2 Rebecca) | ResidentEvil.exe+0x7E9952 | Byte | — | ResidentEvil.CT |
| Slot 8 Item Quantity (Jill - Slot 2 Rebecca) | ResidentEvil.exe+0x7E9953 | Byte | — | ResidentEvil.CT |
| Slot 3 Item ID (Rebecca Only) | ResidentEvil.exe+0x7E9954 | Byte | — | ResidentEvil.CT |
| Slot 3 Item Quantity (Rebecca Only) | ResidentEvil.exe+0x7E9955 | Byte | — | ResidentEvil.CT |
| Slot 4 Item ID (Rebecca Only) | ResidentEvil.exe+0x7E9956 | Byte | — | ResidentEvil.CT |
| Slot 4 Item Quantity (Rebecca Only) | ResidentEvil.exe+0x7E9957 | Byte | — | ResidentEvil.CT |

## item_box

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Open Item Box Anywhere (script ref) | Biohazard.exe+0x833090 | script_reference | — | Biohazard.CT |
| Open Item Box Anywhere (script ref) | Biohazard.exe+0x833093 | script_reference | — | Biohazard.CT |
| Open Item Box Anywhere (script ref) | Biohazard.exe+0x8384E8 | script_reference | — | Biohazard.CT |
| Item Box | Biohazard.exe+0x8387B3 | Unknown | — | Biohazard.CT |
| Item 1 | Biohazard.exe+0x8387B4 | Byte | — | Biohazard.CT |
| Item 2 | Biohazard.exe+0x8387B6 | Byte | — | Biohazard.CT |
| Item 3 | Biohazard.exe+0x8387B8 | Byte | — | Biohazard.CT |
| Item 4 | Biohazard.exe+0x8387BA | Byte | — | Biohazard.CT |
| Item 5 | Biohazard.exe+0x8387BC | Byte | — | Biohazard.CT |
| Item 6 | Biohazard.exe+0x8387BE | Byte | — | Biohazard.CT |
| Item 7 | Biohazard.exe+0x8387C0 | Byte | — | Biohazard.CT |
| Item 8 | Biohazard.exe+0x8387C2 | Byte | — | Biohazard.CT |
| Item 9 | Biohazard.exe+0x8387C4 | Byte | — | Biohazard.CT |
| Item 10 | Biohazard.exe+0x8387C6 | Byte | — | Biohazard.CT |
| Item 11 | Biohazard.exe+0x8387C8 | Byte | — | Biohazard.CT |
| Item 12 | Biohazard.exe+0x8387CA | Byte | — | Biohazard.CT |
| Item 13 | Biohazard.exe+0x8387CC | Byte | — | Biohazard.CT |
| Item 14 | Biohazard.exe+0x8387CE | Byte | — | Biohazard.CT |
| Item 15 | Biohazard.exe+0x8387D0 | Byte | — | Biohazard.CT |
| All Items In Chest (script ref) | ResidentEvil.exe+0x7E98E4 | script_reference | — | ResidentEvil.CT |

## misc

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Enable (script ref) | Biohazard.exe+0x6A8E10 | script_reference | — | Biohazard.CT |
| Go to Main Menu (script ref) | Biohazard.exe+0x833097 | script_reference | — | Biohazard.CT |
| Game Speed (FrameRate) | Biohazard.exe+0x8330AC | Byte | — | Biohazard.CT |
| Toggle Speed Hack (script ref) | Biohazard.exe+0x8351B4 | script_reference | — | Biohazard.CT |
| Enable (script ref) | Biohazard.exe+0x83523C | script_reference | — | Biohazard.CT |
| Enable (script ref) | Biohazard.exe+0x835290 | script_reference | — | Biohazard.CT |
| Enable (script ref) | Biohazard.exe+0x835329 | script_reference | — | Biohazard.CT |
| Open Save Menu Anywhere (script ref) | Biohazard.exe+0x8384E6 | script_reference | — | Biohazard.CT |
| Show Last Text (Debug on screen text) | Biohazard.exe+0x8386F5 | Byte | — | Biohazard.CT |
| Go to Main Menu (script ref) | Biohazard.exe+0x838710 | script_reference | — | Biohazard.CT |
| Pad 1 | Biohazard.exe+0x838710 | 2 Bytes | — | Biohazard.CT |
| Go to Main Menu (script ref) | Biohazard.exe+0x83F8D4 | script_reference | — | Biohazard.CT |
| Pad 2 | Biohazard.exe+0x83F8D4 | 2 Bytes | — | Biohazard.CT |
| Current Message (It may crash the game) | Biohazard.exe+0x83F8EC | Array of byte | — | Biohazard.CT |
| Text char counter | Biohazard.exe+0x83F8F0 | Byte | — | Biohazard.CT |
| Toggle Speed Up Text (script ref) | Biohazard.exe+0x91B95 | script_reference | — | Biohazard.CT |
| Enable (script ref) | Biohazard.exe+0xAD68 | script_reference | — | Biohazard.CT |
| Save Anywhere (Alt+V) | ResidentEvil.exe+0x7E9616 | Byte | — | ResidentEvil.CT |

## player_position

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Player X Coord | Biohazard.exe+0x8351E8 | 4 Bytes | — | Biohazard.CT |
| Player Y Coord | Biohazard.exe+0x8351EC | 4 Bytes | — | Biohazard.CT |
| Player Z Coord | Biohazard.exe+0x8351F0 | 4 Bytes | — | Biohazard.CT |

## room_id

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Current Room | Biohazard.exe+0x8386F1 | Byte | — | Biohazard.CT |
| Disassembler comment: Do not touch: This is where rooms are loaded? | Biohazard.exe+0x8D80D | disassembler_comment | — | Biohazard.CT |

## save_count

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Toggle Saves Always 0 (script ref) | nvd3dum.dll+0x5C0000 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730D4 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730D8 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730DC | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730E0 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730E2 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730E8 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730ED | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730EF | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730F1 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730F4 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730F7 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x730FC | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x73100 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x73104 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x73109 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x7310F | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x73115 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x73117 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x7311A | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x7311C | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | DDRAW.dll+0x73126 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | Biohazard.exe+0x830020 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | Biohazard.exe+0x830024 | script_reference | — | Biohazard.CT |
| Number of Saves | Biohazard.exe+0x838718 | Byte | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | Biohazard.exe+0x838718 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | Biohazard.exe+0xAA360 | script_reference | — | Biohazard.CT |
| Toggle Saves Always 0 (script ref) | Biohazard.exe+0xAA362 | script_reference | — | Biohazard.CT |

## stage

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Story Line | Biohazard.exe+0x833092 | Byte | — | Biohazard.CT |
| Entity Flag | Biohazard.exe+0x96ECB4 | 4 Bytes | — | Biohazard.CT |

## timer

| Description | module+offset | type | pointer path | table |
|---|---|---|---|---|
| Game Timer | Biohazard.exe+0x6A8E10 | 4 Bytes | — | Biohazard.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x1C490 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x7E41C0 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x7E41C7 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x7E62E4 | script_reference | — | ResidentEvil.CT |
| Open Chest Anywhere (Alt+C) | ResidentEvil.exe+0x7E9617 | Byte | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x7E983C | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x7EBCDC | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x7F07FC | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C65 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C6A | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C71 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C73 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C7C | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C7E | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C87 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C8D | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C8F | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C95 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C97 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80C9E | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CA0 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CA7 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CAE | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CB4 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CBB | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CBD | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CC2 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CC8 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CCD | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80CF3 | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0x80D7C | script_reference | — | ResidentEvil.CT |
| Low Game Timer (script ref) | ResidentEvil.exe+0xD2294 | script_reference | — | ResidentEvil.CT |
