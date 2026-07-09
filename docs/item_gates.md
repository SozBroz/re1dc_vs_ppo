# Item gates — Jill Director's Cut Standard

Pickups the agent can **see the room for** but cannot take until a puzzle, item use, story event, or trap precondition is satisfied. Used by `RoomItems._obtainable()` in `re1_rl/item_todo.py` to filter `items_left_here` / `key_items_left_here`.

**Scope:** RE1 PS1 Director's Cut, **Jill**, **Standard** (original) layout — not Arranged/Advanced.

## Gate semantics

| `gate.type` | Counted in `items_left_here` when |
|-------------|-------------------------------------|
| *(no gate)* | Always, until ever-held |
| `trap` | Always (takeable; trap consequence is separate) |
| `item` | Every name in `requires` is in **ever-held** |
| `puzzle` | Every name in `requires` is in ever-held; if `requires` is empty, **not counted** until event-flag tracking exists (conservative) |
| `event` | Same as puzzle with empty requires — hidden until flags are wired |

Requirements use canonical `memory_map.ITEM_IDS` spellings; route aliases (`wooden_emblem` → `emblem`, etc.) normalize in code.

## Gated items table

| Room | Room name | Item | Type | Requirement | Explanation |
|------|-----------|------|------|-------------|-------------|
| 103 | F PASSAGE | green_herb | puzzle | — | Push both statues onto floor grates before pressing red button |
| 105 | DINING ROOM | shield_key | item | gold_emblem | Clock slides after gold emblem on fireplace |
| 106 | MAIN HALL | lockpick | event | — | Barry gives after first zombie in tea room |
| 106 | MAIN HALL | acid_rounds | event | — | Barry gives on 2F return if inventory space |
| 107 | GALLERY | map_1f | puzzle | — | Ladder behind statue, climb to bowl |
| 107 | GALLERY | ink_ribbon | puzzle | — | Push movable drawers aside |
| 10C | GREEN HOUSE | armor_key | item | chemical | Pump chemical into plant; examine crest |
| 10D | TIGER STATUE ROOM | wind_crest | item | blue_jewel | Jewel in tiger eye; statue slides |
| 10F | BAR | music_notes | puzzle | — | Push bookcase behind piano |
| 10F | BAR | gold_emblem | item | music_notes, emblem | Piano + emblem swap in secret alcove |
| 102 | VACANT ROOM | shotgun_shells | item | lockpick | Locked desk |
| 111 | DRESSING ROOM | shotgun_shells | item | lockpick | Locked desk |
| 115 | TRAP ROOM | shotgun | trap | — | Ceiling trap; Jill saved by Barry once |
| 116 | LIVING ROOM | shotgun | trap | — | Same rack; triggers trap room event |
| 117 | LARGE GALLERY | star_crest | puzzle | — | Crow paintings life-cycle button order |
| 11B | STORE ROOM | square_crank | puzzle | — | Stepladder to top shelf |
| 202 | DINING ROOM 2F | blue_jewel | puzzle | — | Push statue off balcony |
| 203 | HALL 2F | acid_rounds | event | — | Barry after Forest balcony scene |
| 205 | ARMOR ROOM | sun_crest | puzzle | — | Statues on grates + center button |
| 20A | STUDY ROOM | explosive_rounds | puzzle | — | Colored-bottle / bookcase cabinet puzzle |
| 20B | FRONT LESSON ROOM | map_2f | item | lighter | Light fireplace logs |
| 20D | PILLAR PASSAGE | clip | event | serum | Search Richard twice after serum (held ≠ given; flag TBD) |
| 20F | DINING | acid_rounds | puzzle | lighter | Candles + secret bookcase room |
| 210 | ATTIC | moon_crest | event | — | After Yawn 1 fight or retreat |
| 210 | ATTIC | shotgun_shells | event | — | Barrel during Yawn 1 visit |
| 212 | TERRACE | bazooka_acid | event | — | Forest Speyer corpse cutscene |
| 215 | STUFFED ROOM | red_jewel | puzzle | — | Lights off, stepladder under deer head |
| 217 | LIBRARY B | mo_disc | puzzle | — | Wall button + statue on lit tile |
| 305 | FOUNTAIN | doom_book_2 | puzzle | hex_crank | **Room disputed** — book is in Item Chamber in walkthroughs |
| 306 | ITEM PASSAGE | mo_disc | puzzle | hex_crank | Statue push + hex crank tile puzzle |
| 30A | ENRICO ROOM | hex_crank | event | — | Sparkle after Enrico cutscene |
| 30A | ENRICO ROOM | clip | event | — | Search body twice after scene |
| 30B | ROCK PASSAGE | mo_disc | item | hex_crank | Alcove after first boulder + crank |
| 30B | ROCK PASSAGE | map_underground | item | hex_crank | Same alcove as second MO disc |
| 30D | STRAIGHT PASSAGE | flame_rounds | event | — | Hunter after second boulder trap |
| 401 | ROOM 001 | shotgun_shells | item | lockpick | Locked desk |
| 402 | ROOM 001 BATHROOM | control_room_key | puzzle | — | Drain bathtub |
| 404 | BAR | pass_number | puzzle | — | Jill: pool code 345 in rec room (Chris: Barry gives) |
| 40A | ROOM 003 | ink_ribbon | item | lockpick | Locked desk |
| 40C | PLANT BOSS ROOM | helmet_key | event | — | Fireplace after Plant 42 |
| 506 | SMALL LABORATORY | wolf_medal | item | doom_book_2 | Medal inside book; opened at fountain (**desk placement disputed**) |
| 507 | MORTUARY | eagle_medal | item | doom_book_1 | Medal inside book; opened at fountain (**coffin note disputed**) |
| 508 | DOUBLE LOCK | passcode_a | item | mo_disc | MO terminal decode, not floor loot |
| 508 | DOUBLE LOCK | passcode_b | item | mo_disc | MO terminal decode, not floor loot |
| 302 | FALLS | flare | event | — | Barry good-ending item |
| 303 | HELIPORT | rocket_launcher | event | — | Brad bad-ending drop |
| 50B | FRONT OF CELL | flare | event | — | Barry good ending |
| 514 | FRONT OF TYRANT | rocket_launcher | event | — | Barry before Tyrant (good ending) |

**Totals:** 48 gated entries — **16 puzzle**, **15 item**, **15 event**, **2 trap**.

Chris-only items (e.g. `sword_key`) are excluded from Jill tables.

## Effect on `items_left_here`

Current encoder (`obs_encoder.py`):

```python
v[17] = min(room_items.remaining_in_room(room, held), 8) / 8.0
v[18] = min(room_items.key_items_remaining_in_room(room, held), 4) / 4.0
```

`remaining_in_room` already skips gated pickups until requirements are met. **Recommendations:**

1. **Keep ever-held gating** — banking must not inflate “left here”.
2. **Item-type gates** work today (`gold_emblem` hidden until `emblem` + `music_notes` held).
3. **Puzzle/event gates with empty `requires`** stay excluded (conservative) — avoids taunting the agent with `star_crest` before paintings are solved. Wire story flags into `ever_held` or a parallel `flags_held` set later.
4. **`trap` type** still counts — agent may attempt pickup; trap handling belongs in reward/env, not the obs counter.
5. **`20D` clip** — `requires: ["serum"]` is weak (must *give* serum to Richard); tighten when RAM flags exist.

## Wrong-room / duplicate findings

| Item | `room_items.json` | Canonical (Jill Standard) | Notes |
|------|-------------------|---------------------------|-------|
| serum | 100 + **102** | 100 SAVE ROOM only | 102 duplicate; route wp17 conflict |
| star_crest | **117** LARGE GALLERY | 117 (not 107 GALLERY) | Route wp08 wrong; data correct |
| doom_book_1 | *(unmapped)* | Courtyard Study after mansion revisit | `_unmatched`; needs room code |
| doom_book_2 | **305** FOUNTAIN | **306** ITEM PASSAGE cabinet | Walkthrough + Evil Resource: book in item chamber |
| wolf_medal | **506** desk | Inside doom_book_2, opened at **305** | Medal not a separate desk pickup in Standard |
| eagle_medal | **507** coffin | Inside doom_book_1, opened at **305** | Same |
| mo_disc (1st) | **213** + **217** | **217** LIBRARY B (213 = alt security path) | Any% may skip basement |
| battery | **219** SHED | Shed + **500** under fountain | 219 OK; 213 route conflict for alt |
| control_room_key | **402** | 402 bathroom | Route wp22 said 401 |
| dorm_key_002 | **408** | Honeycomb / beehive table | Route wp23 said 406 |
| red_book | **401** | Room 001 bed | Route wp23 said 406 |
| square_crank | **11B** | Store room top shelf | Route wp37 said 305 |
| shotgun | **115** + **116** | Trap room / living room rack | Same item; both marked `trap` |

## Route-critical gates (Jill any%)

1. **`gold_emblem` (10F)** — Piano + emblem chain; blocks `shield_key` and attic.
2. **`armor_key` (10C)** — Requires `chemical`; opens armor room, vacant room, bar route.
3. **`shield_key` (105)** — Requires `gold_emblem`; helmet doors and moon crest path.
4. **`moon_crest` (210)** — Event after Yawn 1; one of four courtyard crests.
5. **`helmet_key` (40C)** — Post–Plant 42 mansion revisit (hunters, Richard, lab approach).

Honorable mention: **`hex_crank` → MO discs → lab passcodes** for ending; **`square_crank`** for underground generator.

## Sources

- evilresource.com Jill mansion/guardhouse/lab item pages (Standard layout)
- GameFAQs: SweetPimp324 Item Location FAQ; KrystalCelest Jill DC walkthrough
- `data/route_jill_anypct.json` cross-check
- `data/room_items.json` `_unmatched` conflict log
