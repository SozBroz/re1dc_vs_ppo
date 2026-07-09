# RDT Pipeline — Feasibility Deep Dive

**Date:** 2026-07-04 · **Target:** RE1 Director's Cut PS1 (SLUS-00551)  
**Goal:** Extract room items, doors, enemies, and SCD flags from disc `.rdt` files to
bootstrap `item_positions.json`, `doors_empirical.json`, and `room_enemies.json`
without relying solely on human playthrough logging.

Related: `data/SOURCES.md`, `docs/privileged_obs_spec.md` §2.1 (Phase 1b),
`scripts/build_room_items.py`, `scripts/build_item_positions.py`.

---

## Executive summary

**Verdict: implementable.** RE1 room data lives in `.rdt` files on the PS1 disc.
The format is documented (Just Solve wiki, TapTalk modding thread) and multiple
open-source parsers exist. A practical pipeline for `re1_rl`:

1. Extract disc image → locate `room*.rdt` under stage folders.
2. Parse RDT header → find SCD script offsets (init + sub scripts).
3. Walk SCD bytecode → emit `ITEM_SET`, `DOOR_SET`, `EM_SET` records.
4. Cross-validate coordinates against `pickups_empirical.json` before trusting.
5. Merge into existing JSON schemas consumed by `spatial_encoder.py`.

**Main risks:** RDT x/y/z may not match live RAM world coords 1:1; many
`ITEM_SET` rows are behind `If` blocks gated by SCD work flags; item `id` is a
slot index, not `memory_map.ITEM_IDS` directly.

---

## What is an RDT file?

**RDT** = Room Data Table. One file per room load slot.

| Pattern | Meaning |
|---------|---------|
| `roomSXX0.rdt` | Stage `S` (1–5), room `XX` hex, variant `0` |
| `room1050.rdt` | Stage 1, room `0x105` (dining room) |
| `room1060.rdt` | Stage 1, room `0x106` (main hall) |

Stage mapping matches `data/rooms.json` (`"stage": 1` → mansion 1F, etc.).
TapTalk room list (cited in `data/SOURCES.md`) is the authoritative name table.

Each RDT bundles:

| Section | Offset index | Use for RL |
|---------|--------------|------------|
| RID | cameras | Low priority (rendering) |
| RVD | 0 | Camera zones — door trigger areas |
| SCA | 1 | Collision boundaries (navigation mesh hint) |
| SCD | 6, 7, 8 | **Init + execution scripts** — items, doors, flags |
| MSG | 11 | Room messages / examine text |
| EMR/EDD | 9–10 | Enemy skeleton animations |

**Important:** Header offset table order ≠ file layout order. Data blocks are
stored in a fixed physical order (RID → RVD → SCA → SCD → …). See TapTalk RDT
format thread for the canonical block sequence.

Sources:
- [Just Solve — RDT (Resident Evil 1997)](http://justsolve.archiveteam.org/wiki/RDT_(Resident_Evil_1997))
- [TapTalk — RDT file format](https://www.tapatalk.com/groups/residentevil123/rdt-file-format-t2683.html)

---

## SCD bytecode — what we need

SCD = Scenario script embedded in RDT. Three script slots per room:

| Slot | RDT offset | Runs when |
|------|------------|-----------|
| Init | 6 | Once on first room entry |
| Sub0 | 7 | Main loop thread 0 |
| Sub1 | 8 | Main loop thread 1 |

Init script starts with `uint16` byte length, then bytecode. Control flow uses
`If (0x01)` / `Else (0x02)` / `Endif (0x03)` — **no If/Else/Endif nesting** per
wiki; gated spawns are common.

### Opcodes relevant to privileged obs

| Opcode | Hex | Size | Struct | RL use |
|--------|-----|------|--------|--------|
| `DOOR_SET` | `0x0C` | 26 B | zone rect + `next_stage_and_room` + spawn xyz/dir | Door graph edges |
| `ITEM_SET` | `0x0D` | 18 B | id, x,y,w,h, type byte | Ground item positions |
| `EM_SET` | `0x1B` | 22 B | model, xyz, entity id, killed index | Enemy spawn table |
| `BIT_TEST` | `0x04` | 4 B | object, bit, op | SCD work-flag conditions |
| `BIT_OP` | `0x05` | 4 B | set/clear work flags | Flag semantics |
| `OBJ06_TEST` | `0x06` | 4 B | compare stage/room/camera | Conditional spawns |
| `PLAYER_POS_SET` | `0x20` | 14 B | xyz, angle | Player warp (checkpoints) |
| `OM_SET` | `0x1F` | 28 B | movable object index | Crates, statues |

#### `DOOR_SET` (0x0C) — 26 bytes

```
u8  opcode = 0x0C
u8  door_id
i16 x, y, w, h          # trigger zone
u8  unknown[5]
u8  next_stage_and_room # bits 7-5: stage, 4-0: room
i16 next_x, next_y, next_z
i16 next_dir
u16 unknown1
```

Maps directly to `doors_empirical.json` semantics (source room from filename,
dest from `next_stage_and_room`, entry pose from `next_x/next_z`).

#### `ITEM_SET` (0x0D) — 18 bytes

```
u8  opcode = 0x0D
u8  id                  # item slot id in room (not global item enum)
i16 x, y, w, h          # interaction zone
u8  type                # 0x09 = pickable, 0x08 = box, 0x10 = typewriter, ...
u8  unknown[7]
```

`type` byte disambiguates pickables vs obstacles. Global item identity requires
cross-ref: Evil Resource tables, `room_items.json`, or empirical pickup logs.
BioRand maps slot → item when randomizing.

#### `EM_SET` (0x1B) — 22 bytes

```
u8  opcode = 0x1B
u8  model               # enemy model id
u8  unknown0
u8  killed              # 0xFF = not enemy; else index in killed array
...
u16 x, y, z
u8  id                  # entity id (for EM_POS_SET)
```

Feeds `room_enemies.json` with **positions** (current Evil Resource scrape has
types only). Validates against `docs/enemy_ram_hunt.md` once RAM is mapped.

---

## Extraction workflow (disc → `.rdt` files)

### Current repo state

- `scripts/extract_rom.py` — unpacks `roms/Resident Evil - Director's Cut.7z` only.
- **No `.rdt` files in repo yet.** Need one-time disc extraction.

### Step 1 — Get a BIN/CUE or ISO

From the 7z archive (or your own dump): locate `*.bin` + `*.cue` for SLUS-00551.

### Step 2 — Extract filesystem with dumpsxiso

[dumpsxiso](https://github.com/Lameguy64/mkpsxiso) (mkpsxiso companion) extracts
ISO9660 files from PS1 images:

```powershell
dumpsxiso -x "path\to\SLUS-00551.bin" -d D:\re1_rl\roms\disc_extract
```

On RE1 PS1, room files typically live under paths like `rdt/` or stage
subfolders (`stage1/`, etc.) — **verify after extraction** with:

```powershell
Get-ChildItem -Recurse D:\re1_rl\roms\disc_extract -Filter "room*.rdt"
```

Expected: ~100+ files (`room1000.rdt` … `room5150.rdt`), not all slots used.

### Alternative tools

| Tool | Role | RE1 support |
|------|------|-------------|
| [dumpsxiso](https://github.com/Lameguy64/mkpsxiso) | ISO → files | Yes (generic PS1) |
| [psxrip](https://github.com/RupertAvery/psxrip) | Rip + organize | Yes |
| [reevengi-tools](https://github.com/pmandin/reevengi-tools) `iso_search` | Search ISO for TIM/EMD | RE3-focused; not RDT-primary |

---

## Existing parsers and libraries

### Tier 1 — Use or port

| Project | Lang | RE1 DC | SCD parse | Notes |
|---------|------|--------|-----------|-------|
| [lib_bio](https://github.com/mortician/lib_bio) | Python | **Yes** | Partial | Blender 2.79 RDT import; Python modules for formats |
| [biohazard-utils](https://github.com/biorand/biohazard-utils) | C# / .NET | **Yes** | Yes | BioRand's library; `Rdt1`, `ScdReader`, opcode DB |
| [biorand/classic](https://github.com/biorand/classic) | C# | PC RE1 | Yes | Randomizer modifies RDT doors/items — reference logic |
| [CRE-SCD-BHS](https://github.com/3lric/CRE-SCD-BHS) | Python | **RE1** | Opcode editor | RE1/1.5/2/3 opcode tables; good for validation |
| [Just Solve wiki](http://justsolve.archiveteam.org/wiki/RDT_(Resident_Evil_1997)) | Docs | PS1 | Struct defs | Primary struct reference |

### Tier 2 — RE2-heavy but conceptually transferable

| Project | Notes |
|---------|-------|
| [Bio2ScriptViewer](https://github.com/OpenBiohazard2/Bio2ScriptViewer) | RDT → init/sub SCD pseudocode; RE2 opcodes mostly align |
| [Bio2ScriptIde](https://github.com/OpenBiohazard2/Bio2ScriptIde) | Same family |
| [Tool_Hazard / IntelOrca.Biohazard](https://github.com/JacobMrox/Tool_Hazard) | RDT unpack/repack + SCD compiler |

### Tier 3 — Not primary for RDT

| Project | Notes |
|---------|-------|
| [reevengi-tools](https://github.com/pmandin/reevengi-tools) | BSS masks, PAK, RE2 BIN — not RDT parser |
| [bioclone-remake](https://github.com/MeganGrass/bioclone-remake) | Documents sections; SCD partial |

---

## Recommended implementation plan

### Phase A — Extraction script (1–2 hours)

Add `scripts/extract_rdt_from_disc.py`:

```text
Input:  roms/*.bin or pre-extracted disc_extract/
Output: data/rdt_raw/room*.rdt  (or symlink to extract tree)
        data/rdt_manifest.json  (filename → room_id, stage, path)
```

- Wrap `dumpsxiso` if BIN present; skip if `disc_extract/` already exists.
- Build manifest: `room1050.rdt` → `{stage: 1, room: "105", variant: 0}`.
- Cross-check manifest keys against `data/rooms.json`.

### Phase B — Pure-Python SCD walker (1 day)

Add `re1_rl/rdt_parser.py` + `scripts/parse_rdt_scd.py`:

```text
parse_rdt(path) → header, scd_offsets[init, sub0, sub1]
walk_scd(bytes) → list[ScdInsn]
extract_room_records(rdts) → room_records.json
```

Minimum insn decoder: skip/control (`0x00–0x03`), `BIT_*`, `DOOR_SET`, `ITEM_SET`,
`EM_SET`. Unknown opcodes: use fixed-size table from Just Solve or CRE-SCD-BHS.

Output schema `data/rdt_extracted.json`:

```json
{
  "105": {
    "items": [{"slot_id": 0, "x": 30700, "z": 7200, "type": 9, "scd_offset": 1234}],
    "doors": [{"id": 0, "zone": [...], "dest_stage": 1, "dest_room": "106", ...}],
    "enemies": [{"model": 1, "x": 12000, "z": 8000, "entity_id": 0}],
    "flag_tests": [{"opcode": "BIT_TEST", "object": 2, "bit": 5}]
  }
}
```

### Phase C — Merge into RL data (half day)

| Target | Merge rule |
|--------|------------|
| `item_positions.json` | RDT coords at `confidence: "rdt"`; empirical always wins |
| `doors_empirical.json` | RDT edges as `source: "rdt"`; empirical overrides pose |
| `room_enemies.json` | Add `x,z` from `EM_SET`; keep Evil Resource type names |

Add `scripts/validate_rdt_coords.py`:

- For each empirical pickup, find nearest RDT `ITEM_SET` in same room.
- Report median Δx/Δz — calibrate transform if systematic offset exists.

### Phase D — Optional: vendor biohazard-utils

If pure Python stalls on edge cases:

```powershell
dotnet run --project path\to\biohazard-utils -- parse-rdt room1050.rdt --json
```

Subprocess + JSON is acceptable for offline batch; keep runtime env Python-only.

---

## Coordinate system risks

| Risk | Mitigation |
|------|------------|
| RDT x/z ≠ RAM x/z | Calibrate with ≥5 empirical pickups; affine fit if needed |
| `ITEM_SET` y vs xz | RL obs uses x/z only; y is floor height |
| Zone w/h vs point | Use zone center `(x+w/2, z+h/2)` or nearest-point to player |
| Gated items in `If` blocks | Parse `BIT_TEST` + emit `gated: true` with flag ref |
| DC vs Original layout | We target SLUS-00551 DC; verify one room against PC if parsing fails |
| Item slot id ≠ item name | Join via `room_items.json` + empirical; build `slot_to_item.json` heuristically |

---

## SCD work flags connection

`capture_session.py` hunts live RAM for SCD work-flag bytes (`fb`/`fa`).
RDT `BIT_TEST` / `BIT_OP` opcodes reference **object index + bit number** in the
work-flag array — not yet the same addressing scheme as our RAM hunt.

Once both sides exist:

1. Parse all `BIT_TEST`/`BIT_OP` from init scripts → `rdt_flag_refs.json`.
2. Correlate with `scd_work_flags.json` entries from human capture.
3. Unlock `spatial_encoder` gated-item visibility for puzzle doors.

---

## What RDT gives us vs what it does not

| Data | RDT | Still need live RAM / human |
|------|-----|----------------------------|
| Item ground positions | Yes (zones) | Empirical validation |
| Door connectivity | Yes | Entry pose fine-tuning |
| Enemy spawn positions | Yes | Live HP, death state |
| Enemy types | Model id | Name mapping table |
| SCD flag semantics | Bit refs | RAM address + meaning |
| Interaction prompt | No | `pa`/`pt` hunt |
| Visited mask | No | Episode-local only |
| Dynamic despawns | Partial | Kill flags in work bits |

---

## Acceptance criteria (RDT pipeline)

- [ ] ≥90% of `rooms.json` mansion-1F slots have a matching `.rdt` on disc
- [ ] `parse_rdt_scd.py` emits records for rooms 100–11C without crash
- [ ] Dining emblem (`105`) RDT position within **500** RAM units of empirical
  (after calibration) once `pickups_empirical.json` exists
- [ ] `DOOR_SET` 105→106 edge matches `doors_empirical.json` dest room
- [ ] `EM_SET` in `104` lists ≥1 enemy with coordinates
- [ ] Unit tests: synthetic SCD byte fixtures for each opcode decoder
- [ ] Documented in this file; `data/SOURCES.md` updated with RDT provenance

---

## Suggested file layout (after implementation)

```text
D:\re1_rl\
  roms\
    disc_extract\          # dumpsxiso output (gitignored)
    rdt\                   # optional: copied room*.rdt only
  data\
    rdt_manifest.json
    rdt_extracted.json
    slot_to_item.json      # heuristic slot → canonical item name
  re1_rl\
    rdt_parser.py          # header + SCD walker
  scripts\
    extract_rdt_from_disc.py
    parse_rdt_scd.py
    validate_rdt_coords.py
    merge_rdt_into_positions.py
  tests\
    test_rdt_parser.py
```

---

## References

| URL | Content |
|-----|---------|
| http://justsolve.archiveteam.org/wiki/RDT_(Resident_Evil_1997) | Header, sections, SCD structs, opcode table |
| https://www.tapatalk.com/groups/residentevil123/rdt-file-format-t2683.html | Physical block order, offset decoding |
| https://www.tapatalk.com/groups/residentevil123/re1-rooms-list-bss-pak-rdt-t3197.html | Room ID ↔ debug name |
| https://github.com/mortician/lib_bio | Python RDT I/O, RE1 DC |
| https://github.com/biorand/biohazard-utils | C# Rdt1 + ScdReader |
| https://github.com/biorand/classic | Door/item randomization logic |
| https://github.com/3lric/CRE-SCD-BHS | RE1 opcode tables |
| https://github.com/Lameguy64/mkpsxiso | dumpsxiso extraction |
| `data/SOURCES.md` | Room ID provenance for this repo |
| `docs/capture_session_runbook.md` | Human capture to validate RDT output |

---

## Run order

```powershell
cd D:\re1_rl
D:\re1_rl\venv\Scripts\python.exe scripts\extract_rdt_from_disc.py
D:\re1_rl\venv\Scripts\python.exe scripts\parse_rdt_scd.py
D:\re1_rl\venv\Scripts\python.exe scripts\merge_rdt_into_data.py
D:\re1_rl\venv\Scripts\python.exe scripts\build_item_positions.py
```

**Status:** Implemented 2026-07-04. 335 RDT files extracted from SLUS-00551 BIN;
198 rooms parsed; 37 named item positions, 436 door edges, 15 enemy spawn coords
wired into `SpatialEncoder` / `RoomGraph` / `item_positions.json`.
