# RE1 RL — checkpoints, curriculum, and route guide

Human-readable map of **what the agent is learning**, **where checkpoints live**, and how that lines up with the [Evil Resource Resident Evil (1996)](https://www.evilresource.com/resident-evil) mansion walkthrough.

Use Evil Resource’s **Maps → Mansion** section while reading this. Room names below match our `data/rooms.json` debug codes (same IDs the autosplitter / RAM `room_id` byte uses).

---

## Two different “checkpoints”

| Kind | What it is | Where |
|------|------------|--------|
| **PPO checkpoint** | Saved neural network weights (policy + value). “How good is Jill at tank controls *right now*?” | `data/checkpoints/ppo_re1_*_steps.zip`, `data/ppo_re1_final.zip` |
| **Route waypoint** | A step on the Jill Any% speedrun script. “What should she accomplish next in the game?” | `data/route_jill_anypct.json` (`seq` 1–51) |
| **Curriculum stage** | Which route steps we train on *this episode* + spawn savestate | `curriculum/m0_*.json` |
| **BizHawk savestate** | Frozen game pose for `reset()` | `states/jill_control_fresh.State` (dining **105**, in control) |

Training logs (TensorBoard): `logs/tb/PPO_N/`.

**Important:** A PPO file at 1M steps does **not** mean Jill can finish the game. It only means the policy was trained that long on whatever curriculum was active (currently early mansion). Check **rooms visited** and **waypoint index** in training logs / `info` to see game progress.

---

## Room codes ↔ Evil Resource

Training logs, `info["room_id"]`, and route JSON all use the same **room code** string. It is composed at runtime in `re1_rl/env.py`:

```
room_code = f"{stage_id + 1}{room_byte:02X}"
```

| Piece | Source | Example for **105** (Dining Room) |
|-------|--------|-----------------------------------|
| Leading digit | `stage_id + 1` from PS1 RAM | `0 + 1` → **1** (Mansion 1F) |
| Trailing two hex digits | `room_id` byte from PS1 RAM | `0x05` → **05** |
| Full code | concat | **105** |

**Evil Resource navigation:** [evilresource.com/resident-evil](https://www.evilresource.com/resident-evil) → **Maps** → pick the area column below → find the room by **Evil Resource name**. Item pages and our `data/room_items.json` use the same labels (see `scripts/build_room_items.py` → `ER_TO_ROOM`).

**Lookup in shell:**

```powershell
python -c "import json; print(json.load(open('D:/re1_rl/data/rooms.json'))['107'])"
```

Canonical table (**116 rooms**, transcribed from `data/rooms.json`; Evil Resource names from `ER_TO_ROOM` / walkthrough cross-check):

| Code | RAM (`stage_id`, `room` byte) | Debug name (`rooms.json`) | Evil Resource area | Evil Resource name | Notes |
|------|-------------------------------|---------------------------|--------------------|--------------------|-------|
| **100** | stage_id=0, room=0x00 | SAVE ROOM | Mansion 1F | Mansion Save Room | — |
| **101** | stage_id=0, room=0x01 | 1F LEFT STAIRS | Mansion 1F | East Stairway 1F | — |
| **102** | stage_id=0, room=0x02 | VACANT ROOM | Mansion 1F | Vacant Room | — |
| **103** | stage_id=0, room=0x03 | F PASSAGE | Mansion 1F | Central Corridor | — |
| **104** | stage_id=0, room=0x04 | TEA ROOM | Mansion 1F | Tea Room | — |
| **105** | stage_id=0, room=0x05 | DINING ROOM | Mansion 1F | Dining Room | — |
| **106** | stage_id=0, room=0x06 | MAIN HALL | Mansion 1F | Main Hall 1F | — |
| **107** | stage_id=0, room=0x07 | GALLERY | Mansion 1F | Art Room | — |
| **108** | stage_id=0, room=0x08 | L PASSAGE | Mansion 1F | 'L' Passage | — |
| **109** | stage_id=0, room=0x09 | TRAP PASSAGE | Mansion 1F | Winding Passage | — |
| **10A** | stage_id=0, room=0x0A | BACK PASSAGE | Mansion 1F | Back Passage | — |
| **10B** | stage_id=0, room=0x0B | 1F RIGHT STAIRS | Mansion 1F | West Stairway 1F | — |
| **10C** | stage_id=0, room=0x0C | GREEN HOUSE | Mansion 1F | Greenhouse | — |
| **10D** | stage_id=0, room=0x0D | TIGER STATUE ROOM | Mansion 1F | Tiger Statue Room | — |
| **10E** | stage_id=0, room=0x0E | EMPLOYEE ROOM | Mansion 1F | Keeper's Room | — |
| **10F** | stage_id=0, room=0x0F | BAR | Mansion 1F | Bar | — |
| **110** | stage_id=0, room=0x10 | N/A | Mansion 1F | — | unused slot in debug table; unused debug slot |
| **111** | stage_id=0, room=0x11 | DRESSING ROOM | Mansion 1F | Dressing Room | — |
| **112** | stage_id=0, room=0x12 | WARDROBE | Mansion 1F | Wardrobe | — |
| **113** | stage_id=0, room=0x13 | BATHROOM | Mansion 1F | Bathroom | — |
| **114** | stage_id=0, room=0x14 | BOILER | Mansion 1F | Outside Boiler | — |
| **115** | stage_id=0, room=0x15 | TRAP ROOM | Mansion 1F | Trap Room | — |
| **116** | stage_id=0, room=0x16 | LIVING ROOM | Mansion 1F | Living Room | — |
| **117** | stage_id=0, room=0x17 | LARGE GALLERY | Mansion 1F | Large Gallery | — |
| **118** | stage_id=0, room=0x18 | STAIRS UNDER ROOM | Mansion 1F | Isolated Passage | — |
| **119** | stage_id=0, room=0x19 | N/A | Mansion 1F | — | unused slot in debug table; unused debug slot |
| **11A** | stage_id=0, room=0x1A | ROOFED PASSAGE | Mansion 1F | Roofed Passage | — |
| **11B** | stage_id=0, room=0x1B | STORE ROOM | Mansion 1F | Mansion Storeroom | — |
| **11C** | stage_id=0, room=0x1C | WARDROBE S | Mansion 1F | Wardrobe Closet | — |
| **200** | stage_id=1, room=0x00 | N/A | Mansion 2F | — | unused slot in debug table; unused debug slot |
| **201** | stage_id=1, room=0x01 | 2F LEFT STAIRS | Mansion 2F | East Stairway 2F | — |
| **202** | stage_id=1, room=0x02 | DINING ROOM 2F | Mansion 2F | Dining Room 2F | — |
| **203** | stage_id=1, room=0x03 | HALL 2F | Mansion 2F | Main Hall 2F | — |
| **204** | stage_id=1, room=0x04 | C PASSAGE | Mansion 2F | 'C' Passage | — |
| **205** | stage_id=1, room=0x05 | ARMOR ROOM | Mansion 2F | Armor Room | — |
| **206** | stage_id=1, room=0x06 | DOLL ROOM | Mansion 2F | Doll Room | — |
| **207** | stage_id=1, room=0x07 | 2F RIGHT STAIRS | Mansion 2F | West Stairway 2F | — |
| **208** | stage_id=1, room=0x08 | DEER ROOM | Mansion 2F | Deer Room | — |
| **209** | stage_id=1, room=0x09 | BEDROOM | Mansion 2F | Bedroom | — |
| **20A** | stage_id=1, room=0x0A | STUDY ROOM | Mansion 2F | Study | — |
| **20B** | stage_id=1, room=0x0B | FRONT LESSON ROOM | Mansion 2F | Lesson Room Entry | — |
| **20C** | stage_id=1, room=0x0C | LESSON ROOM | Mansion 2F | Lesson Room | — |
| **20D** | stage_id=1, room=0x0D | PILLAR PASSAGE | Mansion 2F | Pillar Passage | — |
| **20E** | stage_id=1, room=0x0E | FRONT OF ATTIC | Mansion 2F | Attic Entry | — |
| **20F** | stage_id=1, room=0x0F | DINING | Mansion 2F | Small Dining Room | — |
| **210** | stage_id=1, room=0x10 | ATTIC | Mansion 2F | Attic | — |
| **211** | stage_id=1, room=0x11 | TERRACE PASSAGE | Mansion 2F | Terrace Entry | — |
| **212** | stage_id=1, room=0x12 | TERRACE | Mansion 2F | Terrace | — |
| **213** | stage_id=1, room=0x13 | FRONT ELEVATOR | Mansion 2F | Elevator 2F | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **214** | stage_id=1, room=0x14 | ROUGH PASSAGE | Mansion 2F | Rough Passage | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **215** | stage_id=1, room=0x15 | STUFFED ROOM | Mansion 2F | Trophy Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **216** | stage_id=1, room=0x16 | LIBRARY A | Mansion 2F | Large Library | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **217** | stage_id=1, room=0x17 | LIBRARY B | Mansion 2F | Library B | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **218** | stage_id=1, room=0x18 | HIDDEN LIBRARY | Mansion 2F | Hidden Library | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **219** | stage_id=1, room=0x19 | SHED | Mansion 2F | Closet | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **21A** | stage_id=1, room=0x1A | UNDER PASSAGE 1 | Mansion 2F | Underground Passage 1 | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **21B** | stage_id=1, room=0x1B | UNDER PASSAGE 2 | Mansion 2F | Underground Passage 2 | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **21C** | stage_id=1, room=0x1C | UNDER KITCHEN | Mansion 2F | Kitchen | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **300** | stage_id=2, room=0x00 | FIRST HOUSE OUT | Courtyard / caves | Courtyard Garden | — |
| **301** | stage_id=2, room=0x01 | WATER GATE | Courtyard / caves | Water Gate | — |
| **302** | stage_id=2, room=0x02 | FALLS | Courtyard / caves | Falls | — |
| **303** | stage_id=2, room=0x03 | HELIPORT | Courtyard / caves | Heliport | — |
| **304** | stage_id=2, room=0x04 | SECOND HOUSE GATE | Courtyard / caves | Guardhouse Gate | — |
| **305** | stage_id=2, room=0x05 | FOUNTAIN | Courtyard / caves | Fountain | — |
| **306** | stage_id=2, room=0x06 | ITEM PASSAGE | Courtyard / caves | Item Chamber | — |
| **307** | stage_id=2, room=0x07 | LADDER PASSAGE | Courtyard / caves | Ladder Passage | — |
| **308** | stage_id=2, room=0x08 | BRANCH PASSAGE | Courtyard / caves | Branched Passage | — |
| **309** | stage_id=2, room=0x09 | DARKNESS PASSAGE | Courtyard / caves | Darkness Passage | — |
| **30A** | stage_id=2, room=0x0A | ENRICO ROOM | Courtyard / caves | Enrico Room | — |
| **30B** | stage_id=2, room=0x0B | ROCK PASSAGE | Courtyard / caves | Boulder Passage 2 | — |
| **30C** | stage_id=2, room=0x0C | BLACK TIGER ROOM | Courtyard / caves | Black Tiger Room | — |
| **30D** | stage_id=2, room=0x0D | STRAIGHT PASSAGE | Courtyard / caves | Boulder Passage 1 | — |
| **30E** | stage_id=2, room=0x0E | SAVE ROOM | Courtyard / caves | Underground Save Room | — |
| **30F** | stage_id=2, room=0x0F | CRANK PASSAGE | Courtyard / caves | Generator Room | — |
| **310** | stage_id=2, room=0x10 | COURT TO LABO | Courtyard / caves | Underground Entry | — |
| **311** | stage_id=2, room=0x11 | ROTATING HOLE | Courtyard / caves | Rotating Hole | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **400** | stage_id=3, room=0x00 | ENTER PASSAGE | Guardhouse | Guardhouse Entry | — |
| **401** | stage_id=3, room=0x01 | ROOM 001 | Guardhouse | Room 001 | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **402** | stage_id=3, room=0x02 | ROOM 001 BATHROOM | Guardhouse | Room 001 Bathroom | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **403** | stage_id=3, room=0x03 | SAVE ROOM | Guardhouse | Guardhouse Save Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **404** | stage_id=3, room=0x04 | BAR | Guardhouse | Rec Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **405** | stage_id=3, room=0x05 | CENTER PASSAGE | Guardhouse | Center Passage | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **406** | stage_id=3, room=0x06 | ROOM 002 | Guardhouse | Room 002 | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **407** | stage_id=3, room=0x07 | ROOM 002 BATHROOM | Guardhouse | Room 002 Bathroom | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **408** | stage_id=3, room=0x08 | HONEYCOMB PASSAGE | Guardhouse | Beehive Passage | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **409** | stage_id=3, room=0x09 | DRUG STOREHOUSE | Guardhouse | Drug Storeroom | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **40A** | stage_id=3, room=0x0A | ROOM 003 | Guardhouse | Room 003 | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **40B** | stage_id=3, room=0x0B | ROOM 003 BATHROOM | Guardhouse | Room 003 Bathroom | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **40C** | stage_id=3, room=0x0C | PLANT BOSS ROOM | Guardhouse | Plant 42 Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **40D** | stage_id=3, room=0x0D | UNDER PASSAGE | Guardhouse | Water Tank Entry | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **40E** | stage_id=3, room=0x0E | WATER TANK | Guardhouse | Water Tank | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **40F** | stage_id=3, room=0x0F | SECURITY ROOM | Guardhouse | Meeting Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **410** | stage_id=3, room=0x10 | ARMS STOREHOUSE | Guardhouse | Arms Storehouse | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **411** | stage_id=3, room=0x11 | CONTROL ROOM | Guardhouse | Control Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **500** | stage_id=4, room=0x00 | UNDER FOUNTAIN | Laboratory | Under Fountain | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **501** | stage_id=4, room=0x01 | HELIPORT PASSAGE | Laboratory | Heliport Passage | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **502** | stage_id=4, room=0x02 | LADDER ROOM | Laboratory | Ladder Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **503** | stage_id=4, room=0x03 | STAIRS | Laboratory | Stairway | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **504** | stage_id=4, room=0x04 | CONFERENCE ROOM | Laboratory | Visual Data Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **505** | stage_id=4, room=0x05 | O PASSAGE | Laboratory | 'O' Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **506** | stage_id=4, room=0x06 | SMALL LABORATORY | Laboratory | Small Laboratory | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **507** | stage_id=4, room=0x07 | MORTUARY | Laboratory | Mortuary | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **508** | stage_id=4, room=0x08 | DOUBLE LOCK | Laboratory | X-Ray Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **509** | stage_id=4, room=0x09 | PRIVATE ROOM A | Laboratory | Private Room A | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **50A** | stage_id=4, room=0x0A | PRIVATE ROOM B | Laboratory | Private Room B | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **50B** | stage_id=4, room=0x0B | FRONT OF CELL | Laboratory | Front of Cell | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **50C** | stage_id=4, room=0x0C | FRONT OF ELEVATOR | Laboratory | Front of Elevator | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **50D** | stage_id=4, room=0x0D | ESCAPE ELEVATOR | Laboratory | Escape Elevator | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **50E** | stage_id=4, room=0x0E | SAVE ROOM | Laboratory | Lab Save Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **50F** | stage_id=4, room=0x0F | MAZE A | Laboratory | Power Maze 1 | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **510** | stage_id=4, room=0x10 | MAZE B | Laboratory | Power Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **511** | stage_id=4, room=0x11 | MOVIE ROOM | Laboratory | Movie Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **512** | stage_id=4, room=0x12 | CELL | Laboratory | Cell | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **513** | stage_id=4, room=0x13 | TYRANT ROOM | Laboratory | Tyrant Room | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **514** | stage_id=4, room=0x14 | FRONT OF TYRANT | Laboratory | Front of Tyrant | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |
| **515** | stage_id=4, room=0x15 | B3 > B4 ELEVATOR | Laboratory | B3 > B4 Elevator | hex ID inferred from TapTalk table order (IDs column missing in mirrors); hex ID unverified |

**Stage digit quick reference:** **1** = Mansion 1F · **2** = Mansion 2F · **3** = Courtyard / caves · **4** = Guardhouse · **5** = Laboratory.

Rows marked *hex ID unverified* were inferred from community room-order tables (TapTalk mirrors); confirm in-game before hard-coding macros.

---

## Current training curriculum (`m0_dining_to_main_hall`)

Spawn: **`states/jill_control_fresh.State`** — Jill, dining room (**105**), in control, starter kit (knife / beretta / spray; no emblem yet). Updated 2026-07-03 from EmuHawk QuickSave.

Active route steps (through gallery attempt):

| Route `seq` | Room | Objective (plain English) | Reward when |
|-------------|------|-----------------------------|-------------|
| **1** | 105 Dining | Pick up **wooden emblem** | `has_item` wooden_emblem |
| **2** | 104 Tea Room | **Kenneth zombie cutscene** (hallway approach) | Enter room **104** |
| **3** | 105 Dining | **Talk to Barry** in dining room | Re-enter room **105** after Kenneth |
| **4** | 106 Main Hall | **Talk to Barry** in main hall | Enter room **106** |
| **5** | 106 Main Hall | **Report to Wesker** | Receive **lockpick** from Barry (same visit) |
| **6** | 203 Hall 2F (or **201** / **207**) | **Explore top floor** | Enter **203**, **201**, or **207** |
| **7** | 106 Main Hall | **Return to hall** after 2F (Barry dialogue) | Enter room **106** |
| **8** | 115 Trap Room | **Get shotgun** (living room trap; Barry rescue) | `has_item` **shotgun** |
| **9** | 107 Gallery | **Crow puzzle** → star crest | `has_item` **star_crest** |

**Episode success (training):** first entry into room **107** each episode → **`+200` pre-scale** (`success_room` ≈ **+20** after `REWARD_SCALE`). Episode **keeps running** until `max_steps` (24000) or death.

**Route after checkpoint 7:** compass should lead **106 → 116 living room → 115 trap room** (shotgun), then **back toward 107 gallery** — not straight into the gallery door.

**Not in this stage yet:** crow puzzle completion, chemicals & greenhouse (seq 10+), 1F map, handgun clips, emblem bank, etc.

During cutscenes / door transitions the env runs at **8×** speed and mashes **Triangle** on doors, **Cross** (1-frame taps) on dialogue and **“Pick it up?”** menus.

TensorBoard run that finished 1M steps: **`PPO_7`** → `data/ppo_re1_final.zip` (trained on the *previous* curriculum; restart after these changes).

---

## Full Jill Any% route (all 51 waypoints)

Follow along on Evil Resource by area. Items and puzzles are under **Key items**, **Guides → Puzzles**, and per-room map pages.

### Mansion 1F — start & east wing

| Seq | Room | Location | What happens |
|-----|------|----------|--------------|
| 1 | 105 | Dining Room | Pick up **wooden emblem** from table |
| 2 | 104 | Tea Room | **Kenneth zombie cutscene** (hallway approach) |
| 3 | 105 | Dining Room | **Talk to Barry** |
| 4 | 106 | Main Hall | **Talk to Barry** |
| 5 | 106 | Main Hall | **Report to Wesker** |
| 6 | 203 | Hall 2F (alt **201** east stairs, **207** west stairs) | **Explore top floor** |
| 7 | 106 | Main Hall | **Return to hall** after 2F explore |
| 6 | 115 | Trap Room | **Shotgun** (Jill Sandwich) |
| 7 | 107 | Gallery / Art Room | Crow puzzle → **star crest** |
| 8 | 11B | Store Room | Chemicals + **green herb** |
| 9 | 10C | Greenhouse | Pump chemicals, kill plant → **armor key** |

### Mansion 1F — west & puzzles

| Seq | Room | Location | What happens |
|-----|------|----------|--------------|
| 10 | 209 | Bedroom (2F) | Ammo pickup (route visits 2F early) |
| 11 | 10F | Bar / Piano Bar | Piano sonata, emblem swap in secret room |
| 12 | 105 | Dining Room | Place **gold emblem** → **shield key** behind clock |
| 13 | 202 | Dining Room 2F | Push statue → **blue jewel** |
| 14 | 10D | Tiger Statue Room | Blue jewel → **wind crest** |
| 15 | 205 | Armor Room | Statue puzzle → **sun crest** |
| 16 | 102 | Vacant Room | **Serum** (Rebecca’s room) |
| 17 | 210 | Attic | **Yawn 1** → **moon crest** |
| 18 | 11A | Roofed Passage | Four crests → courtyard door |

### Courtyard & Guardhouse

| Seq | Room | Location | What happens |
|-----|------|----------|--------------|
| 19 | 304 | Second House Gate | Into residence area |
| 20 | 400 | Enter Passage | Cover Plant 42 hole with statue |
| 21 | 401 | Room 001 | **Control room key** (bathroom tub) |
| 22 | 406 | Room 002 | Room 002 key + **red book** |
| 23 | 411 | Control Room | Drain shark hall |
| 24 | 410 | Arms Storehouse | Room 003 key + ammo |
| 25 | 409 | Drug Storehouse | **V-Jolt** prep (pool code 3-4-5) |
| 26 | 40E | Water Tank | Apply V-Jolt to roots |
| 27 | 40C | Plant Boss Room | **Plant 42** → **helmet key** |
| 28 | 400 | Enter Passage | Wesker scene, return to mansion |

### Mansion 2F & late mansion

| Seq | Room | Location | What happens |
|-----|------|----------|--------------|
| 29 | 203 | Hall 2F | Survive hunters |
| 30 | 20E | Front of Attic | After Yawn 2 route |
| 31 | 210 | Attic | **Yawn 2** |
| 32 | 217 | Library B | Hidden library shortcut |
| 33 | 216 | Library A | **MO disk** (if skipping basement) |
| 34 | 213 | Front Elevator | Security room — Barry passcode, battery |
| 35 | 20B | Front Lesson Room | **Doom book vol. 1** |
| 36 | 305 | Fountain | Doom book puzzle → **square crank** |

### Underground lab & ending

| Seq | Room | Location | What happens |
|-----|------|----------|--------------|
| 37 | 500 | Under Fountain | Elevator to lab |
| 38 | 30F | Crank Passage | Use square crank |
| 39 | 30C | Black Tiger Room | Boss |
| 40 | 30E | Save Room | Bank items |
| 41 | 30B | Rock Passage | Hex crank + 2nd MO disk |
| 42 | 506 | Small Laboratory | **Wolf medal** |
| 43 | 507 | Mortuary | **Eagle medal** |
| 44 | 305 | Fountain | Medals → lab elevator |
| 45 | 50E | Save Room | **Power room key** |
| 46 | 510 | Maze B | Power elevator |
| 47 | 508 | Double Lock | Passcodes with MO disks |
| 48 | 50C | Front of Elevator | Ride to B4 |
| 49 | 514 | Front of Tyrant | **Tyrant** fight |
| 50 | 501 | Heliport Passage | Escape route |
| 51 | 303 | Heliport | **Ending** |

---

## How PPO checkpoints are saved

From `scripts/train_parallel.py`:

- Every **~50k environment steps** (scaled by number of parallel envs):  
  `data/checkpoints/ppo_re1_<N>_steps.zip`
- End of run: **`data/ppo_re1_final.zip`**

Resume training:

```powershell
D:\re1_rl\venv\Scripts\python.exe scripts\train_parallel.py --n-envs 6 --total-steps 1000000 --resume D:\re1_rl\data\ppo_re1_final.zip
```

---

## Reading training progress (human)

During training, console lines like:

```text
[progress] first visit to room 106 at step 714
[rollout] ... best_wp=0 wp_hits=523 rooms=['105', '106']
```

| Field | Meaning |
|-------|---------|
| `first visit to room XXX` | Agent entered that room code for the first time this rollout |
| `best_wp` | Highest route waypoint **index** completed (0 = none, 1 = seq 2 done, etc.) |
| `wp_hits` | Count of +2 waypoint bonuses (can exceed `best_wp` across episodes) |
| `rooms` | Set of room codes seen this rollout |
| `ep_rew_mean` | Mean episode return (shaped reward; negative early is normal) |

**PPO_7 (1M steps):** reached rooms **105** and **106**, ~523 waypoint hits, ~1.2k steps per episode — learned dining ↔ main hall traffic, not full Barry + Kenneth sequence yet (curriculum/rewards were updated after that run).

---

## Pickups by room (Evil Resource cross-check)

Per-room item lists scraped from Evil Resource live in **`data/room_items.json`**. Regenerate with:

```powershell
D:\re1_rl\venv\Scripts\python.exe scripts\build_room_items.py
```

Human checklist for the route’s key items:

```powershell
D:\re1_rl\venv\Scripts\python.exe scripts\route_todo.py --rooms
```

---

## Related docs

- `docs/nn_architecture_and_encoding.md` — observation / reward fields
- `docs/item_gates.md` — puzzles and event-gated pickups
- `curriculum/README.md` — stage JSON schema
- [Evil Resource — Resident Evil](https://www.evilresource.com/resident-evil) — maps, items, puzzles
