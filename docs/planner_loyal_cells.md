# Planner-loyal cells (`plNN`)

Generated from [`data/planner_chunks/cp05_shield_key.json`](../data/planner_chunks/cp05_shield_key.json) (123 authored steps after the lockpick tip). Room names in parentheses come from [`data/rooms.json`](../data/rooms.json).

**Source of truth:** the live chunk JSON. Seed cells `pl00`–`pl05` are the opening crystals (same beats as yawn `cp00`–`cp05`); they are **not** minted from this chunk.

On step success the fleet installs `states/planner_loyal/cells/plNN/` for the completed index.

- Slot formula: capturing steps only — `capture:false` (Richard) does not consume a `plNN`. After `pl85` (`204->20D`), next mint is `pl86` (`204->207`).
- Training starts: every minted `pl05+` (pin file `data/planner_loyal_reset_pin.env`; blank = uniform).
- After reset from a cell, the live step is `planner_step_index + 1` (or first chunk step from `pl05`).
- `wrong_traverse:A->B got C` means the **wanted** hop was `A->B`; they entered `C` instead (−4 divert). Completing `A->B` mints the cell and does **not** log `wrong_traverse`.
- Tea-room lock: `104->103` stays locked until `103->104` is done once (this chunk never opens it). `103->10C` / `103->10D` are open. Do not walk `116->106` after the shotgun. Vacant `102` clip+shells are taken on the armor-key return; skip re-loot.
- Chunk end-anchor: `place_wind_crest` (`pl127`). Mid-chunk success keeps the episode open.

## Summary table

| Cell | Step n | Checkpoint ID | Room | Op | Objective |
|------|--------|---------------|------|----|-----------|
| `pl00` | seed | `emblem_105` | `105` (DINING ROOM) | acquire | Pick up the wooden emblem |
| `pl01` | seed | `kenneth_104` | `104` (TEA ROOM) | traverse | Enter the Tea Room (Kenneth) |
| `pl02` | seed | `barry_return_105` | `105` (DINING ROOM) | traverse | Return to Dining after Kenneth |
| `pl03` | seed | `main_hall_106` | `106` (MAIN HALL) | traverse | Reach Main Hall after Kenneth |
| `pl04` | seed | `upper_hall_203` | `203` (HALL 2F) | traverse | Climb to Main Hall 2F |
| `pl05` | seed | `barry_hall_return_106` | `106` (MAIN HALL) | traverse | Return from 203 to Main Hall (lockpick tip) (training tip) |
| `pl06` | 1 | `106->105` | `105` (DINING ROOM) | traverse | Walk `106->105` into `105` (DINING ROOM) |
| `pl07` | 2 | `105->104` | `104` (TEA ROOM) | traverse | Walk `105->104` into `104` (TEA ROOM) |
| `pl08` | 3 | `104:handgun_bullets:1` | `104` (TEA ROOM) | acquire | Take `104:handgun_bullets:1` |
| `pl09` | 4 | `104:handgun_bullets:2` | `104` (TEA ROOM) | acquire | Take `104:handgun_bullets:2` |
| `pl10` | 5 | `104->10F` | `10F` (BAR) | traverse | Walk `104->10F` into `10F` (BAR) |
| `pl11` | 6 | `music_notes` | `10F` (BAR) | acquire | Take `10F:music_notes:1` |
| `pl12` | 7 | `piano_play` | `10F` (BAR) | objective | `piano_play` at `music_notes@10F_piano` |
| `pl13` | 8 | `gold_emblem` | `10F` (BAR) | acquire | Take `10F:gold_emblem:2` |
| `pl14` | 9 | `emblem_swap_alcove` | `10F` (BAR) | objective | `emblem_swap_alcove` at `emblem@10F_alcove` |
| `pl15` | 10 | `10F->104` | `104` (TEA ROOM) | traverse | Walk `10F->104` into `104` (TEA ROOM) |
| `pl16` | 11 | `104->105` | `105` (DINING ROOM) | traverse | Walk `104->105` into `105` (DINING ROOM) |
| `pl17` | 12 | `gold_emblem_fireplace` | `105` (DINING ROOM) | objective | `gold_emblem_fireplace` at `gold_emblem@105_fireplace` |
| `pl18` | 13 | `shield_key` | `105` (DINING ROOM) | acquire | Take `105:shield_key:2` |
| `pl19` | 14 | `105->106` | `106` (MAIN HALL) | traverse | Walk `105->106` into `106` (MAIN HALL) |
| `pl20` | 15 | `106->107` | `107` (GALLERY) | traverse | Walk `106->107` into `107` (GALLERY) |
| `pl21` | 16 | `107->108` | `108` (L PASSAGE) | traverse | Walk `107->108` into `108` (L PASSAGE) |
| `pl22` | 17 | `108:handgun_bullets:1` | `108` (L PASSAGE) | acquire | Take `108:handgun_bullets:1` |
| `pl23` | 18 | `108->109` | `109` (TRAP PASSAGE) | traverse | Walk `108->109` into `109` (TRAP PASSAGE) |
| `pl24` | 19 | `109:green_herb:1` | `109` (TRAP PASSAGE) | acquire | Take `109:green_herb:1` |
| `pl25` | 20 | `109->10A` | `10A` (BACK PASSAGE) | traverse | Walk `109->10A` into `10A` (BACK PASSAGE) |
| `pl26` | 21 | `10A->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `10A->10B` into `10B` (1F RIGHT STAIRS) |
| `pl27` | 22 | `10B:green_herb:1` | `10B` (1F RIGHT STAIRS) | acquire | Take `10B:green_herb:1` |
| `pl28` | 23 | `10B->118` | `118` (STAIRS UNDER ROOM) | traverse | Walk `10B->118` into `118` (STAIRS UNDER ROOM) |
| `pl29` | 24 | `chemical` | `118` (STAIRS UNDER ROOM) | acquire | Take `118:chemical:1` |
| `pl30` | 25 | `use_box` | `118` (STAIRS UNDER ROOM) | use_box | Rearrange the 118 box to the leave_118 loadout, then close the box |
| `pl31` | 26 | `118->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `118->10B` into `10B` (1F RIGHT STAIRS) |
| `pl32` | 27 | `10B->10A` | `10A` (BACK PASSAGE) | traverse | Walk `10B->10A` into `10A` (BACK PASSAGE) |
| `pl33` | 28 | `10A->109` | `109` (TRAP PASSAGE) | traverse | Walk `10A->109` into `109` (TRAP PASSAGE) |
| `pl34` | 29 | `109->115` | `115` (TRAP ROOM) | traverse | Walk `109->115` into `115` (TRAP ROOM) |
| `pl35` | 30 | `115->116` | `116` (LIVING ROOM) | traverse | Walk `115->116` into `116` (LIVING ROOM) |
| `pl36` | 31 | `116:shotgun:1` | `116` (LIVING ROOM) | acquire | Take `116:shotgun:1` |
| `pl37` | 32 | `116->115` | `115` (TRAP ROOM) | traverse | Walk `116->115` into `115` (TRAP ROOM) |
| `pl38` | 33 | `115->109` | `109` (TRAP PASSAGE) | traverse | Walk `115->109` into `109` (TRAP PASSAGE) |
| `pl39` | 34 | `109->10A` | `10A` (BACK PASSAGE) | traverse | Walk `109->10A` into `10A` (BACK PASSAGE) |
| `pl40` | 35 | `gallery_enter` | `117` (LARGE GALLERY) | traverse | Walk `10A->117` into `117` (LARGE GALLERY) |
| `pl41` | 36 | `gallery_portrait_1` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_1` at `gallery_portrait_1` — newborn |
| `pl42` | 37 | `gallery_portrait_2` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_2` at `gallery_portrait_2` — infant |
| `pl43` | 38 | `gallery_portrait_3` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_3` at `gallery_portrait_3` — boy |
| `pl44` | 39 | `gallery_portrait_4` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_4` at `gallery_portrait_4` — young man |
| `pl45` | 40 | `gallery_portrait_5` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_5` at `gallery_portrait_5` — middle-aged man |
| `pl46` | 41 | `gallery_portrait_6` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_6` at `gallery_portrait_6` — old man |
| `pl47` | 42 | `star_crest` | `117` (LARGE GALLERY) | acquire | Take `117:star_crest:1` |
| `pl48` | 43 | `117->10A` | `10A` (BACK PASSAGE) | traverse | Walk `117->10A` into `10A` (BACK PASSAGE) |
| `pl49` | 44 | `10A->11A` | `11A` (ROOFED PASSAGE) | traverse | Walk `10A->11A` into `11A` (ROOFED PASSAGE) |
| `pl50` | 45 | `place_star_crest` | `11A` (ROOFED PASSAGE) | objective | `place_star_crest` at `star_crest@11A_crest_slot` |
| `pl51` | 46 | `11A->10A` | `10A` (BACK PASSAGE) | traverse | Walk `11A->10A` into `10A` (BACK PASSAGE) |
| `pl52` | 47 | `10A->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `10A->10B` into `10B` (1F RIGHT STAIRS) |
| `pl53` | 48 | `10B->207` | `207` (2F RIGHT STAIRS) | traverse | Walk `10B->207` into `207` (2F RIGHT STAIRS) |
| `pl54` | 49 | `207->204` | `204` (C PASSAGE) | traverse | Walk `207->204` into `204` (C PASSAGE) |
| `pl55` | 50 | `204->203` | `203` (HALL 2F) | traverse | Walk `204->203` into `203` (HALL 2F) |
| `pl56` | 51 | `203->202` | `202` (DINING ROOM 2F) | traverse | Walk `203->202` into `202` (DINING ROOM 2F) |
| `pl57` | 52 | `202->201` | `201` (2F LEFT STAIRS) | traverse | Walk `202->201` into `201` (2F LEFT STAIRS) |
| `pl58` | 53 | `201->101` | `101` (1F LEFT STAIRS) | traverse | Walk `201->101` into `101` (1F LEFT STAIRS) |
| `pl59` | 54 | `101->103` | `103` (F PASSAGE) | traverse | Walk `101->103` into `103` (F PASSAGE) |
| `pl60` | 55 | `103->10C` | `10C` (GREEN HOUSE) | traverse | Walk `103->10C` into `10C` (GREEN HOUSE) |
| `pl61` | 56 | `greenhouse_pump` | `10C` (GREEN HOUSE) | objective | `greenhouse_pump` at `chemical@10C_greenhouse_pump` |
| `pl62` | 57 | `armor_key` | `10C` (GREEN HOUSE) | acquire | Take `10C:armor_key:1` |
| `pl63` | 58 | `10C:red_herb:3a` | `10C` (GREEN HOUSE) | acquire | Take `10C:red_herb:3a` (bench red 1/2) |
| `pl64` | 59 | `10C:green_herb:2a` | `10C` (GREEN HOUSE) | acquire | Take `10C:green_herb:2a` (bench green 1/2) |
| `pl65` | 60 | `10C:red_herb:3b` | `10C` (GREEN HOUSE) | acquire | Take `10C:red_herb:3b` (bench red 2/2) |
| `pl66` | 61 | `10C:green_herb:2b` | `10C` (GREEN HOUSE) | acquire | Take `10C:green_herb:2b` (bench green 2/2; 3rd green/red = divert) |
| `pl67` | 62 | `10C->103` | `103` (F PASSAGE) | traverse | Walk `10C->103` into `103` (F PASSAGE) |
| `pl68` | 63 | `103->101` | `101` (1F LEFT STAIRS) | traverse | Walk `103->101` into `101` (1F LEFT STAIRS) |
| `pl69` | 64 | `101->102` | `102` (VACANT ROOM) | traverse | Walk `101->102` into `102` (VACANT ROOM) |
| `pl70` | 65 | `102:handgun_bullets:1` | `102` (VACANT ROOM) | acquire | Take `102:handgun_bullets:1` |
| `pl71` | 66 | `102:shotgun_shells:2` | `102` (VACANT ROOM) | acquire | Take `102:shotgun_shells:2` |
| `pl72` | 67 | `102->101` | `101` (1F LEFT STAIRS) | traverse | Walk `102->101` into `101` (1F LEFT STAIRS) |
| `pl73` | 68 | `101->100` | `100` (SAVE ROOM) | traverse | Walk `101->100` into `100` (SAVE ROOM) |
| `pl74` | 69 | `use_box` | `100` (SAVE ROOM) | use_box | Rearrange the 100 box to the leave_100 loadout, then close the box |
| `pl75` | 70 | `100->101` | `101` (1F LEFT STAIRS) | traverse | Walk `100->101` into `101` (1F LEFT STAIRS) |
| `pl76` | 71 | `101->201` | `201` (2F LEFT STAIRS) | traverse | Walk `101->201` into `201` (2F LEFT STAIRS) |
| `pl77` | 72 | `201->202` | `202` (DINING ROOM 2F) | traverse | Walk `201->202` into `202` (DINING ROOM 2F) |
| `pl78` | 73 | `202->203` | `203` (HALL 2F) | traverse | Walk `202->203` into `203` (HALL 2F) |
| `pl79` | 74 | `203->204` | `204` (C PASSAGE) | traverse | Walk `203->204` into `204` (C PASSAGE) |
| `pl80` | 75 | `armor_room_enter` | `205` (ARMOR ROOM) | traverse | Walk `204->205` into `205` (ARMOR ROOM) |
| `pl81` | 76 | `armor_vent_door` | `205` (ARMOR ROOM) | do_puzzle | `armor_vent_door` at `armor_vent_door` — east statue exactly on its vent |
| `pl82` | 77 | `armor_vent_far` | `205` (ARMOR ROOM) | do_puzzle | `armor_vent_far` at `armor_vent_far` — both east and west statues exactly on their vents |
| `pl83` | 78 | `sun_crest` | `205` (ARMOR ROOM) | acquire | Take `205:sun_crest:1` (activate center button, then take cabinet sun crest) |
| `pl84` | 79 | `205->204` | `204` (C PASSAGE) | traverse | Walk `205->204` into `204` (C PASSAGE) |
| `pl85` | 80 | `richard_approach` | `20D` (PILLAR PASSAGE) | traverse | Walk `204->20D` into `20D` (PILLAR PASSAGE) |
| _(none)_ | 81 | `richard_bleedout` | `20D` (PILLAR PASSAGE) | trigger_cutscene | `richard_bleedout` at `20D:richard` — start ~6 min Richard timer; cinema dumps to 204 — no cell mint |
| `pl86` | 82 | `204->207` | `207` (2F RIGHT STAIRS) | traverse | Walk `204->207` into `207` (2F RIGHT STAIRS) |
| `pl87` | 83 | `207->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `207->10B` into `10B` (1F RIGHT STAIRS) |
| `pl88` | 84 | `10B->10A` | `10A` (BACK PASSAGE) | traverse | Walk `10B->10A` into `10A` (BACK PASSAGE) |
| `pl89` | 85 | `10A->11A` | `11A` (ROOFED PASSAGE) | traverse | Walk `10A->11A` into `11A` (ROOFED PASSAGE) |
| `pl90` | 86 | `place_sun_crest` | `11A` (ROOFED PASSAGE) | objective | `place_sun_crest` at `sun_crest@11A_crest_slot` — place held sun_crest; burns Richard timer |
| `pl91` | 87 | `11A->10A` | `10A` (BACK PASSAGE) | traverse | Walk `11A->10A` into `10A` (BACK PASSAGE) |
| `pl92` | 88 | `10A->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `10A->10B` into `10B` (1F RIGHT STAIRS) |
| `pl93` | 89 | `10B->207` | `207` (2F RIGHT STAIRS) | traverse | Walk `10B->207` into `207` (2F RIGHT STAIRS) |
| `pl94` | 90 | `207->204` | `204` (C PASSAGE) | traverse | Walk `207->204` into `204` (C PASSAGE) |
| `pl95` | 91 | `204->203` | `203` (HALL 2F) | traverse | Walk `204->203` into `203` (HALL 2F) |
| `pl96` | 92 | `dining_2f_enter` | `202` (DINING ROOM 2F) | traverse | Walk `203->202` into `202` (DINING ROOM 2F) |
| `pl97` | 93 | `push_statue_2f` | `202` (DINING ROOM 2F) | do_puzzle | `push_statue_2f` at `dining_statue_knocked` — push balcony statue down; blue jewel drops to dining hall 105 |
| `pl98` | 94 | `202->203` | `203` (HALL 2F) | traverse | Walk `202->203` into `203` (HALL 2F) |
| `pl99` | 95 | `203->106` | `106` (MAIN HALL) | traverse | Walk `203->106` into `106` (MAIN HALL) |
| `pl100` | 96 | `106->105` | `105` (DINING ROOM) | traverse | Walk `106->105` into `105` (DINING ROOM) |
| `pl101` | 97 | `blue_jewel` | `105` (DINING ROOM) | acquire | Take `105:blue_jewel:1` (statue drop puts jewel in dining hall 105 (not 202)) |
| `pl102` | 98 | `105->106` | `106` (MAIN HALL) | traverse | Walk `105->106` into `106` (MAIN HALL) |
| `pl103` | 99 | `106->203` | `203` (HALL 2F) | traverse | Walk `106->203` into `203` (HALL 2F) |
| `pl104` | 100 | `203->202` | `202` (DINING ROOM 2F) | traverse | Walk `203->202` into `202` (DINING ROOM 2F) |
| `pl105` | 101 | `202->201` | `201` (2F LEFT STAIRS) | traverse | Walk `202->201` into `201` (2F LEFT STAIRS) |
| `pl106` | 102 | `201->101` | `101` (1F LEFT STAIRS) | traverse | Walk `201->101` into `101` (1F LEFT STAIRS) |
| `pl107` | 103 | `101->103` | `103` (F PASSAGE) | traverse | Walk `101->103` into `103` (F PASSAGE) |
| `pl108` | 104 | `tiger_room_enter` | `10D` (TIGER STATUE ROOM) | traverse | Walk `103->10D` into `10D` (TIGER STATUE ROOM) |
| `pl109` | 105 | `tiger_jewel` | `10D` (TIGER STATUE ROOM) | objective | `tiger_jewel` at `blue_jewel@10D_tiger_eye` — insert blue jewel in tiger eye |
| `pl110` | 106 | `wind_crest` | `10D` (TIGER STATUE ROOM) | acquire | Take `10D:wind_crest:1` (acquire wind crest; continue to place_wind resource tail) |
| `pl111` | 107 | `10D->103` | `103` (F PASSAGE) | traverse | Walk `10D->103` into `103` (F PASSAGE) |
| `pl112` | 108 | `employee_room_enter` | `10E` (EMPLOYEE ROOM) | traverse | Walk `103->10E` into `10E` (EMPLOYEE ROOM) |
| `pl113` | 109 | `10E:handgun_bullets:1` | `10E` (EMPLOYEE ROOM) | acquire | Take `10E:handgun_bullets:1` |
| `pl114` | 110 | `10E:shotgun_shells:2` | `10E` (EMPLOYEE ROOM) | acquire | Take `10E:shotgun_shells:2` |
| `pl115` | 111 | `10E->103` | `103` (F PASSAGE) | traverse | Walk `10E->103` into `103` (F PASSAGE) |
| `pl116` | 112 | `tea_unlock_103_104` | `104` (TEA ROOM) | traverse | Walk `103->104` into `104` (TEA ROOM) |
| `pl117` | 113 | `104->105` | `105` (DINING ROOM) | traverse | Walk `104->105` into `105` (DINING ROOM) |
| `pl118` | 114 | `105->106` | `106` (MAIN HALL) | traverse | Walk `105->106` into `106` (MAIN HALL) |
| `pl119` | 115 | `dressing_room_enter` | `111` (DRESSING ROOM) | traverse | Walk `106->111` into `111` (DRESSING ROOM) |
| `pl120` | 116 | `111:shotgun_shells:2` | `111` (DRESSING ROOM) | acquire | Take `111:shotgun_shells:2` |
| `pl121` | 117 | `111->106` | `106` (MAIN HALL) | traverse | Walk `111->106` into `106` (MAIN HALL) |
| `pl122` | 118 | `106->107` | `107` (GALLERY) | traverse | Walk `106->107` into `107` (GALLERY) |
| `pl123` | 119 | `107->108` | `108` (L PASSAGE) | traverse | Walk `107->108` into `108` (L PASSAGE) |
| `pl124` | 120 | `108->109` | `109` (TRAP PASSAGE) | traverse | Walk `108->109` into `109` (TRAP PASSAGE) |
| `pl125` | 121 | `109->10A` | `10A` (BACK PASSAGE) | traverse | Walk `109->10A` into `10A` (BACK PASSAGE) |
| `pl126` | 122 | `10A->11A` | `11A` (ROOFED PASSAGE) | traverse | Walk `10A->11A` into `11A` (ROOFED PASSAGE) |
| `pl127` | 123 | `place_wind_crest` | `11A` (ROOFED PASSAGE) | objective | `place_wind_crest` at `wind_crest@11A_crest_slot` — chunk end-anchor |

## Details

### Seed cells (not from this chunk)

### `pl00` — `emblem_105` (seed)

- **Room:** `105` (DINING ROOM)
- **Op:** `acquire`
- **Objective:** Pick up the wooden emblem
- **Items gained:** `emblem`
- **Success:** acquire `emblem` in `105`

### `pl01` — `kenneth_104` (seed)

- **Room:** `104` (TEA ROOM)
- **Op:** `traverse`
- **Objective:** Enter the Tea Room (Kenneth)
- **Items gained:** _(none)_
- **Success:** enter `104` via `105->104`

### `pl02` — `barry_return_105` (seed)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Objective:** Return to Dining after Kenneth
- **Items gained:** _(none)_
- **Success:** enter `105` via `104->105`

### `pl03` — `main_hall_106` (seed)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Objective:** Reach Main Hall after Kenneth
- **Items gained:** _(none)_
- **Success:** enter `106` via `105->106`

### `pl04` — `upper_hall_203` (seed)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Objective:** Climb to Main Hall 2F
- **Items gained:** _(none)_
- **Success:** enter `203` via `106->203`

### `pl05` — `barry_hall_return_106` (seed)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Objective:** Return from 203 to Main Hall (lockpick tip)
- **Items gained:** _(none)_
- **Success:** enter `106` via `203->106`

### Chunk cells (`pl06`–`pl127`)

### `pl06` — `106->105` (step 1)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Edge:** `106->105`
- **Objective:** Walk `106->105` into `105` (DINING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->105` into `105` (DINING ROOM).
- **Success condition:** Enter room `105` via `106->105` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->105 got <room>` (−4).

### `pl07` — `105->104` (step 2)

- **Room:** `104` (TEA ROOM)
- **Op:** `traverse`
- **Edge:** `105->104`
- **Objective:** Walk `105->104` into `104` (TEA ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `105->104` into `104` (TEA ROOM).
- **Success condition:** Enter room `104` via `105->104` (already-there counts after cinema dump). Any other door is `wrong_traverse:105->104 got <room>` (−4).

### `pl08` — `104:handgun_bullets:1` (step 3)

- **Room:** `104` (TEA ROOM)
- **Op:** `acquire`
- **Pickup:** `104:handgun_bullets:1`
- **Objective:** Take `104:handgun_bullets:1`
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `104:handgun_bullets:1`.
- **Success condition:** Inventory gains `104:handgun_bullets:1` while this step is current

### `pl09` — `104:handgun_bullets:2` (step 4)

- **Room:** `104` (TEA ROOM)
- **Op:** `acquire`
- **Pickup:** `104:handgun_bullets:2`
- **Objective:** Take `104:handgun_bullets:2`
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `104:handgun_bullets:2`.
- **Success condition:** Inventory gains `104:handgun_bullets:2` while this step is current

### `pl10` — `104->10F` (step 5)

- **Room:** `10F` (BAR)
- **Op:** `traverse`
- **Edge:** `104->10F`
- **Objective:** Walk `104->10F` into `10F` (BAR)
- **Items gained:** _(none)_
- **How to achieve:** Walk `104->10F` into `10F` (BAR).
- **Success condition:** Enter room `10F` via `104->10F` (already-there counts after cinema dump). Any other door is `wrong_traverse:104->10F got <room>` (−4).

### `pl11` — `music_notes` (step 6)

- **Room:** `10F` (BAR)
- **Op:** `acquire`
- **Pickup:** `10F:music_notes:1`
- **Beat:** `music_notes`
- **Objective:** Take `10F:music_notes:1`
- **Items gained:** `music_notes`
- **How to achieve:** Take `10F:music_notes:1`.
- **Success condition:** Inventory gains `10F:music_notes:1` while this step is current

### `pl12` — `piano_play` (step 7)

- **Room:** `10F` (BAR)
- **Op:** `objective`
- **Site:** `music_notes@10F_piano`
- **Beat:** `piano_play`
- **Objective:** `piano_play` at `music_notes@10F_piano`
- **Items gained:** _(none)_
- **How to achieve:** `piano_play` at `music_notes@10F_piano`.
- **Success condition:** `story_use_success` == `music_notes@10F_piano` in room `10F`

### `pl13` — `gold_emblem` (step 8)

- **Room:** `10F` (BAR)
- **Op:** `acquire`
- **Pickup:** `10F:gold_emblem:2`
- **Beat:** `gold_emblem`
- **Objective:** Take `10F:gold_emblem:2`
- **Items gained:** `gold_emblem`
- **How to achieve:** Take `10F:gold_emblem:2`.
- **Success condition:** Inventory gains `10F:gold_emblem:2` while this step is current

### `pl14` — `emblem_swap_alcove` (step 9)

- **Room:** `10F` (BAR)
- **Op:** `objective`
- **Site:** `emblem@10F_alcove`
- **Beat:** `emblem_swap_alcove`
- **Objective:** `emblem_swap_alcove` at `emblem@10F_alcove`
- **Items gained:** _(none)_
- **How to achieve:** `emblem_swap_alcove` at `emblem@10F_alcove`.
- **Success condition:** `story_use_success` == `emblem@10F_alcove` in room `10F`

### `pl15` — `10F->104` (step 10)

- **Room:** `104` (TEA ROOM)
- **Op:** `traverse`
- **Edge:** `10F->104`
- **Objective:** Walk `10F->104` into `104` (TEA ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10F->104` into `104` (TEA ROOM).
- **Success condition:** Enter room `104` via `10F->104` (already-there counts after cinema dump). Any other door is `wrong_traverse:10F->104 got <room>` (−4).

### `pl16` — `104->105` (step 11)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Edge:** `104->105`
- **Objective:** Walk `104->105` into `105` (DINING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `104->105` into `105` (DINING ROOM).
- **Success condition:** Enter room `105` via `104->105` (already-there counts after cinema dump). Any other door is `wrong_traverse:104->105 got <room>` (−4).

### `pl17` — `gold_emblem_fireplace` (step 12)

- **Room:** `105` (DINING ROOM)
- **Op:** `objective`
- **Site:** `gold_emblem@105_fireplace`
- **Beat:** `gold_emblem_fireplace`
- **Objective:** `gold_emblem_fireplace` at `gold_emblem@105_fireplace`
- **Items gained:** _(none)_
- **How to achieve:** `gold_emblem_fireplace` at `gold_emblem@105_fireplace`.
- **Success condition:** `story_use_success` == `gold_emblem@105_fireplace` in room `105`

### `pl18` — `shield_key` (step 13)

- **Room:** `105` (DINING ROOM)
- **Op:** `acquire`
- **Pickup:** `105:shield_key:2`
- **Beat:** `shield_key`
- **Objective:** Take `105:shield_key:2`
- **Items gained:** `shield_key`
- **How to achieve:** Take `105:shield_key:2`.
- **Success condition:** Inventory gains `105:shield_key:2` while this step is current

### `pl19` — `105->106` (step 14)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `105->106`
- **Objective:** Walk `105->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `105->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `105->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:105->106 got <room>` (−4).

### `pl20` — `106->107` (step 15)

- **Room:** `107` (GALLERY)
- **Op:** `traverse`
- **Edge:** `106->107`
- **Objective:** Walk `106->107` into `107` (GALLERY)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->107` into `107` (GALLERY).
- **Success condition:** Enter room `107` via `106->107` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->107 got <room>` (−4).

### `pl21` — `107->108` (step 16)

- **Room:** `108` (L PASSAGE)
- **Op:** `traverse`
- **Edge:** `107->108`
- **Objective:** Walk `107->108` into `108` (L PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `107->108` into `108` (L PASSAGE).
- **Success condition:** Enter room `108` via `107->108` (already-there counts after cinema dump). Any other door is `wrong_traverse:107->108 got <room>` (−4).

### `pl22` — `108:handgun_bullets:1` (step 17)

- **Room:** `108` (L PASSAGE)
- **Op:** `acquire`
- **Pickup:** `108:handgun_bullets:1`
- **Objective:** Take `108:handgun_bullets:1`
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `108:handgun_bullets:1`.
- **Success condition:** Inventory gains `108:handgun_bullets:1` while this step is current

### `pl23` — `108->109` (step 18)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `traverse`
- **Edge:** `108->109`
- **Objective:** Walk `108->109` into `109` (TRAP PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `108->109` into `109` (TRAP PASSAGE).
- **Success condition:** Enter room `109` via `108->109` (already-there counts after cinema dump). Any other door is `wrong_traverse:108->109 got <room>` (−4).

### `pl24` — `109:green_herb:1` (step 19)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `acquire`
- **Pickup:** `109:green_herb:1`
- **Objective:** Take `109:green_herb:1`
- **Items gained:** `green_herb`
- **How to achieve:** Take `109:green_herb:1`.
- **Success condition:** Inventory gains `109:green_herb:1` while this step is current

### `pl25` — `109->10A` (step 20)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `109->10A`
- **Objective:** Walk `109->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `109->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `109->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:109->10A got <room>` (−4).

### `pl26` — `10A->10B` (step 21)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10A->10B`
- **Objective:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `10A->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->10B got <room>` (−4).

### `pl27` — `10B:green_herb:1` (step 22)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `acquire`
- **Pickup:** `10B:green_herb:1`
- **Objective:** Take `10B:green_herb:1`
- **Items gained:** `green_herb`
- **How to achieve:** Take `10B:green_herb:1`.
- **Success condition:** Inventory gains `10B:green_herb:1` while this step is current

### `pl28` — `10B->118` (step 23)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Op:** `traverse`
- **Edge:** `10B->118`
- **Objective:** Walk `10B->118` into `118` (STAIRS UNDER ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->118` into `118` (STAIRS UNDER ROOM).
- **Success condition:** Enter room `118` via `10B->118` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->118 got <room>` (−4).

### `pl29` — `chemical` (step 24)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Op:** `acquire`
- **Pickup:** `118:chemical:1`
- **Beat:** `chemical`
- **Objective:** Take `118:chemical:1`
- **Items gained:** `chemical`
- **How to achieve:** Take `118:chemical:1`.
- **Success condition:** Inventory gains `118:chemical:1` while this step is current

### `pl30` — `use_box` (step 25)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Op:** `use_box`
- **Objective:** Rearrange the 118 box to the leave_118 loadout, then close the box
- **Items gained:** _(none)_
- **How to achieve:** Rearrange the 118 box to the leave_118 loadout, then close the box.
- **Success condition:** Box closes and inventory matches `leave_118.held_on_exit`

### `pl31` — `118->10B` (step 26)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `118->10B`
- **Objective:** Walk `118->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `118->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `118->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:118->10B got <room>` (−4).

### `pl32` — `10B->10A` (step 27)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `10B->10A`
- **Objective:** Walk `10B->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `10B->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->10A got <room>` (−4).

### `pl33` — `10A->109` (step 28)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `traverse`
- **Edge:** `10A->109`
- **Objective:** Walk `10A->109` into `109` (TRAP PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->109` into `109` (TRAP PASSAGE).
- **Success condition:** Enter room `109` via `10A->109` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->109 got <room>` (−4).

### `pl34` — `109->115` (step 29)

- **Room:** `115` (TRAP ROOM)
- **Op:** `traverse`
- **Edge:** `109->115`
- **Objective:** Walk `109->115` into `115` (TRAP ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `109->115` into `115` (TRAP ROOM).
- **Success condition:** Enter room `115` via `109->115` (already-there counts after cinema dump). Any other door is `wrong_traverse:109->115 got <room>` (−4).

### `pl35` — `115->116` (step 30)

- **Room:** `116` (LIVING ROOM)
- **Op:** `traverse`
- **Edge:** `115->116`
- **Objective:** Walk `115->116` into `116` (LIVING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `115->116` into `116` (LIVING ROOM).
- **Success condition:** Enter room `116` via `115->116` (already-there counts after cinema dump). Any other door is `wrong_traverse:115->116 got <room>` (−4).

### `pl36` — `116:shotgun:1` (step 31)

- **Room:** `116` (LIVING ROOM)
- **Op:** `acquire`
- **Pickup:** `116:shotgun:1`
- **Objective:** Take `116:shotgun:1`
- **Items gained:** `shotgun`
- **How to achieve:** Take `116:shotgun:1`.
- **Success condition:** Inventory gains `116:shotgun:1` while this step is current

### `pl37` — `116->115` (step 32)

- **Room:** `115` (TRAP ROOM)
- **Op:** `traverse`
- **Edge:** `116->115`
- **Objective:** Walk `116->115` into `115` (TRAP ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `116->115` into `115` (TRAP ROOM).
- **Success condition:** Enter room `115` via `116->115` (already-there counts after cinema dump). Any other door is `wrong_traverse:116->115 got <room>` (−4).

### `pl38` — `115->109` (step 33)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `traverse`
- **Edge:** `115->109`
- **Objective:** Walk `115->109` into `109` (TRAP PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `115->109` into `109` (TRAP PASSAGE).
- **Success condition:** Enter room `109` via `115->109` (already-there counts after cinema dump). Any other door is `wrong_traverse:115->109 got <room>` (−4).

### `pl39` — `109->10A` (step 34)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `109->10A`
- **Objective:** Walk `109->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `109->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `109->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:109->10A got <room>` (−4).

### `pl40` — `gallery_enter` (step 35)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `traverse`
- **Edge:** `10A->117`
- **Beat:** `gallery_enter`
- **Objective:** Walk `10A->117` into `117` (LARGE GALLERY)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->117` into `117` (LARGE GALLERY).
- **Success condition:** Enter room `117` via `10A->117` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->117 got <room>` (−4).

### `pl41` — `gallery_portrait_1` (step 36)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_1`
- **Beat:** `gallery_portrait_1`
- **Note:** newborn
- **Objective:** `gallery_portrait_1` at `gallery_portrait_1` — newborn
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_1` at `gallery_portrait_1` — newborn.
- **Success condition:** Room `117` and gallery completed-steps >= 1

### `pl42` — `gallery_portrait_2` (step 37)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_2`
- **Beat:** `gallery_portrait_2`
- **Note:** infant
- **Objective:** `gallery_portrait_2` at `gallery_portrait_2` — infant
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_2` at `gallery_portrait_2` — infant.
- **Success condition:** Room `117` and gallery completed-steps >= 2

### `pl43` — `gallery_portrait_3` (step 38)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_3`
- **Beat:** `gallery_portrait_3`
- **Note:** boy
- **Objective:** `gallery_portrait_3` at `gallery_portrait_3` — boy
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_3` at `gallery_portrait_3` — boy.
- **Success condition:** Room `117` and gallery completed-steps >= 3

### `pl44` — `gallery_portrait_4` (step 39)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_4`
- **Beat:** `gallery_portrait_4`
- **Note:** young man
- **Objective:** `gallery_portrait_4` at `gallery_portrait_4` — young man
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_4` at `gallery_portrait_4` — young man.
- **Success condition:** Room `117` and gallery completed-steps >= 4

### `pl45` — `gallery_portrait_5` (step 40)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_5`
- **Beat:** `gallery_portrait_5`
- **Note:** middle-aged man
- **Objective:** `gallery_portrait_5` at `gallery_portrait_5` — middle-aged man
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_5` at `gallery_portrait_5` — middle-aged man.
- **Success condition:** Room `117` and gallery completed-steps >= 5

### `pl46` — `gallery_portrait_6` (step 41)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_6`
- **Beat:** `gallery_portrait_6`
- **Note:** old man
- **Objective:** `gallery_portrait_6` at `gallery_portrait_6` — old man
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_6` at `gallery_portrait_6` — old man.
- **Success condition:** Room `117` and gallery completed-steps >= 6

### `pl47` — `star_crest` (step 42)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `acquire`
- **Pickup:** `117:star_crest:1`
- **Beat:** `star_crest`
- **Objective:** Take `117:star_crest:1`
- **Items gained:** `star_crest`
- **How to achieve:** Take `117:star_crest:1`.
- **Success condition:** Inventory gains `117:star_crest:1` while this step is current

### `pl48` — `117->10A` (step 43)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `117->10A`
- **Objective:** Walk `117->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `117->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `117->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:117->10A got <room>` (−4).

### `pl49` — `10A->11A` (step 44)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `traverse`
- **Edge:** `10A->11A`
- **Objective:** Walk `10A->11A` into `11A` (ROOFED PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->11A` into `11A` (ROOFED PASSAGE).
- **Success condition:** Enter room `11A` via `10A->11A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->11A got <room>` (−4).

### `pl50` — `place_star_crest` (step 45)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `objective`
- **Site:** `star_crest@11A_crest_slot`
- **Beat:** `place_star_crest`
- **Objective:** `place_star_crest` at `star_crest@11A_crest_slot`
- **Items gained:** _(none)_
- **How to achieve:** `place_star_crest` at `star_crest@11A_crest_slot`.
- **Success condition:** `story_use_success` == `star_crest@11A_crest_slot` in room `11A`

### `pl51` — `11A->10A` (step 46)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `11A->10A`
- **Objective:** Walk `11A->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `11A->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `11A->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:11A->10A got <room>` (−4).

### `pl52` — `10A->10B` (step 47)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10A->10B`
- **Objective:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `10A->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->10B got <room>` (−4).

### `pl53` — `10B->207` (step 48)

- **Room:** `207` (2F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10B->207`
- **Objective:** Walk `10B->207` into `207` (2F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->207` into `207` (2F RIGHT STAIRS).
- **Success condition:** Enter room `207` via `10B->207` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->207 got <room>` (−4).

### `pl54` — `207->204` (step 49)

- **Room:** `204` (C PASSAGE)
- **Op:** `traverse`
- **Edge:** `207->204`
- **Objective:** Walk `207->204` into `204` (C PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `207->204` into `204` (C PASSAGE).
- **Success condition:** Enter room `204` via `207->204` (already-there counts after cinema dump). Any other door is `wrong_traverse:207->204 got <room>` (−4).

### `pl55` — `204->203` (step 50)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `204->203`
- **Objective:** Walk `204->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `204->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->203 got <room>` (−4).

### `pl56` — `203->202` (step 51)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `traverse`
- **Edge:** `203->202`
- **Objective:** Walk `203->202` into `202` (DINING ROOM 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->202` into `202` (DINING ROOM 2F).
- **Success condition:** Enter room `202` via `203->202` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->202 got <room>` (−4).

### `pl57` — `202->201` (step 52)

- **Room:** `201` (2F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `202->201`
- **Objective:** Walk `202->201` into `201` (2F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `202->201` into `201` (2F LEFT STAIRS).
- **Success condition:** Enter room `201` via `202->201` (already-there counts after cinema dump). Any other door is `wrong_traverse:202->201 got <room>` (−4).

### `pl58` — `201->101` (step 53)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `201->101`
- **Objective:** Walk `201->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `201->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `201->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:201->101 got <room>` (−4).

### `pl59` — `101->103` (step 54)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `101->103`
- **Objective:** Walk `101->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `101->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->103 got <room>` (−4).

### `pl60` — `103->10C` (step 55)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `traverse`
- **Edge:** `103->10C`
- **Objective:** Walk `103->10C` into `10C` (GREEN HOUSE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->10C` into `10C` (GREEN HOUSE).
- **Success condition:** Enter room `10C` via `103->10C` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->10C got <room>` (−4).

### `pl61` — `greenhouse_pump` (step 56)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `objective`
- **Site:** `chemical@10C_greenhouse_pump`
- **Beat:** `greenhouse_pump`
- **Objective:** `greenhouse_pump` at `chemical@10C_greenhouse_pump`
- **Items gained:** _(none)_
- **How to achieve:** `greenhouse_pump` at `chemical@10C_greenhouse_pump`.
- **Success condition:** `story_use_success` == `chemical@10C_greenhouse_pump` in room `10C`

### `pl62` — `armor_key` (step 57)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:armor_key:1`
- **Beat:** `armor_key`
- **Objective:** Take `10C:armor_key:1`
- **Items gained:** `armor_key`
- **How to achieve:** Take `10C:armor_key:1`.
- **Success condition:** Inventory gains `10C:armor_key:1` while this step is current

### `pl63` — `10C:red_herb:3a` (step 58)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:red_herb:3a`
- **Note:** bench red 1/2
- **Objective:** Take `10C:red_herb:3a` (bench red 1/2)
- **Items gained:** `red_herb`
- **How to achieve:** Take `10C:red_herb:3a` (bench red 1/2).
- **Success condition:** Inventory gains `10C:red_herb:3a` while this step is current

### `pl64` — `10C:green_herb:2a` (step 59)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:green_herb:2a`
- **Note:** bench green 1/2
- **Objective:** Take `10C:green_herb:2a` (bench green 1/2)
- **Items gained:** `green_herb`
- **How to achieve:** Take `10C:green_herb:2a` (bench green 1/2).
- **Success condition:** Inventory gains `10C:green_herb:2a` while this step is current

### `pl65` — `10C:red_herb:3b` (step 60)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:red_herb:3b`
- **Note:** bench red 2/2
- **Objective:** Take `10C:red_herb:3b` (bench red 2/2)
- **Items gained:** `red_herb`
- **How to achieve:** Take `10C:red_herb:3b` (bench red 2/2).
- **Success condition:** Inventory gains `10C:red_herb:3b` while this step is current

### `pl66` — `10C:green_herb:2b` (step 61)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:green_herb:2b`
- **Note:** bench green 2/2; 3rd green/red = divert
- **Objective:** Take `10C:green_herb:2b` (bench green 2/2; 3rd green/red = divert)
- **Items gained:** `green_herb`
- **How to achieve:** Take `10C:green_herb:2b` (bench green 2/2; 3rd green/red = divert).
- **Success condition:** Inventory gains `10C:green_herb:2b` while this step is current

### `pl67` — `10C->103` (step 62)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `10C->103`
- **Objective:** Walk `10C->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10C->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `10C->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:10C->103 got <room>` (−4).

### `pl68` — `103->101` (step 63)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `103->101`
- **Objective:** Walk `103->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `103->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->101 got <room>` (−4).

### `pl69` — `101->102` (step 64)

- **Room:** `102` (VACANT ROOM)
- **Op:** `traverse`
- **Edge:** `101->102`
- **Objective:** Walk `101->102` into `102` (VACANT ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->102` into `102` (VACANT ROOM).
- **Success condition:** Enter room `102` via `101->102` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->102 got <room>` (−4).

### `pl70` — `102:handgun_bullets:1` (step 65)

- **Room:** `102` (VACANT ROOM)
- **Op:** `acquire`
- **Pickup:** `102:handgun_bullets:1`
- **Objective:** Take `102:handgun_bullets:1`
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `102:handgun_bullets:1`.
- **Success condition:** Inventory gains `102:handgun_bullets:1` while this step is current

### `pl71` — `102:shotgun_shells:2` (step 66)

- **Room:** `102` (VACANT ROOM)
- **Op:** `acquire`
- **Pickup:** `102:shotgun_shells:2`
- **Objective:** Take `102:shotgun_shells:2`
- **Items gained:** `shotgun_shells`
- **How to achieve:** Take `102:shotgun_shells:2`.
- **Success condition:** Inventory gains `102:shotgun_shells:2` while this step is current

### `pl72` — `102->101` (step 67)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `102->101`
- **Objective:** Walk `102->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `102->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `102->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:102->101 got <room>` (−4).

### `pl73` — `101->100` (step 68)

- **Room:** `100` (SAVE ROOM)
- **Op:** `traverse`
- **Edge:** `101->100`
- **Objective:** Walk `101->100` into `100` (SAVE ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->100` into `100` (SAVE ROOM).
- **Success condition:** Enter room `100` via `101->100` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->100 got <room>` (−4).

### `pl74` — `use_box` (step 69)

- **Room:** `100` (SAVE ROOM)
- **Op:** `use_box`
- **Objective:** Rearrange the 100 box to the leave_100 loadout, then close the box
- **Items gained:** _(none)_
- **How to achieve:** Rearrange the 100 box to the leave_100 loadout, then close the box.
- **Success condition:** Box closes and inventory matches `leave_100.held_on_exit`

### `pl75` — `100->101` (step 70)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `100->101`
- **Objective:** Walk `100->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `100->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `100->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:100->101 got <room>` (−4).

### `pl76` — `101->201` (step 71)

- **Room:** `201` (2F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `101->201`
- **Objective:** Walk `101->201` into `201` (2F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->201` into `201` (2F LEFT STAIRS).
- **Success condition:** Enter room `201` via `101->201` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->201 got <room>` (−4).

### `pl77` — `201->202` (step 72)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `traverse`
- **Edge:** `201->202`
- **Objective:** Walk `201->202` into `202` (DINING ROOM 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `201->202` into `202` (DINING ROOM 2F).
- **Success condition:** Enter room `202` via `201->202` (already-there counts after cinema dump). Any other door is `wrong_traverse:201->202 got <room>` (−4).

### `pl78` — `202->203` (step 73)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `202->203`
- **Objective:** Walk `202->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `202->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `202->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:202->203 got <room>` (−4).

### `pl79` — `203->204` (step 74)

- **Room:** `204` (C PASSAGE)
- **Op:** `traverse`
- **Edge:** `203->204`
- **Objective:** Walk `203->204` into `204` (C PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->204` into `204` (C PASSAGE).
- **Success condition:** Enter room `204` via `203->204` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->204 got <room>` (−4).

### `pl80` — `armor_room_enter` (step 75)

- **Room:** `205` (ARMOR ROOM)
- **Op:** `traverse`
- **Edge:** `204->205`
- **Beat:** `armor_room_enter`
- **Objective:** Walk `204->205` into `205` (ARMOR ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->205` into `205` (ARMOR ROOM).
- **Success condition:** Enter room `205` via `204->205` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->205 got <room>` (−4).

### `pl81` — `armor_vent_door` (step 76)

- **Room:** `205` (ARMOR ROOM)
- **Op:** `do_puzzle`
- **Site:** `armor_vent_door`
- **Beat:** `armor_vent_door`
- **Note:** east statue exactly on its vent
- **Objective:** `armor_vent_door` at `armor_vent_door` — east statue exactly on its vent
- **Items gained:** _(none)_
- **How to achieve:** `armor_vent_door` at `armor_vent_door` — east statue exactly on its vent.
- **Success condition:** Room `205` and east OM-object target `(14035, 7340)` within ±8 in all three mirrors

### `pl82` — `armor_vent_far` (step 77)

- **Room:** `205` (ARMOR ROOM)
- **Op:** `do_puzzle`
- **Site:** `armor_vent_far`
- **Beat:** `armor_vent_far`
- **Note:** both east and west statues exactly on their vents
- **Objective:** `armor_vent_far` at `armor_vent_far` — both east and west statues exactly on their vents
- **Items gained:** _(none)_
- **How to achieve:** `armor_vent_far` at `armor_vent_far` — both east and west statues exactly on their vents.
- **Success condition:** Room `205`, east OM-object target `(14035, 7340)` within ±8 in all three mirrors, and west OM-object target `(4895, 7186)` within ±50 in all three agreeing mirrors; both are mandatory (one shove-grid cell still covers the west vent AOT)

### `pl83` — `sun_crest` (step 78)

- **Room:** `205` (ARMOR ROOM)
- **Op:** `acquire`
- **Pickup:** `205:sun_crest:1`
- **Beat:** `sun_crest`
- **Note:** activate center button, then take cabinet sun crest
- **Objective:** Take `205:sun_crest:1` (activate center button, then take cabinet sun crest)
- **Items gained:** `sun_crest`
- **How to achieve:** Take `205:sun_crest:1` (activate center button, then take cabinet sun crest).
- **Success condition:** Inventory gains `205:sun_crest:1` while this step is current

### `pl84` — `205->204` (step 79)

- **Room:** `204` (C PASSAGE)
- **Op:** `traverse`
- **Edge:** `205->204`
- **Note:** leave armor room toward Richard / crest / dining
- **Objective:** Walk `205->204` into `204` (C PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `205->204` into `204` (C PASSAGE).
- **Success condition:** Enter room `204` via `205->204` (already-there counts after cinema dump). Any other door is `wrong_traverse:205->204 got <room>` (−4).

### `pl85` — `richard_approach` (step 80)

- **Room:** `20D` (PILLAR PASSAGE)
- **Op:** `traverse`
- **Edge:** `204->20D`
- **Beat:** `richard_approach`
- **Note:** armor_key gate into Pillar Passage
- **Objective:** Walk `204->20D` into `20D` (PILLAR PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->20D` into `20D` (PILLAR PASSAGE).
- **Success condition:** Enter room `20D` via `204->20D` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->20D got <room>` (−4).

### `(no cell)` — `richard_bleedout` (step 81, capture:false)

- **Room:** `20D` (PILLAR PASSAGE)
- **Op:** `trigger_cutscene`
- **Site:** `20D:richard`
- **Beat:** `richard_bleedout`
- **Capture:** `false` (queue advance only; no `plNN` cell)
- **Note:** start ~6 min Richard timer; cinema dumps to 204 — no cell mint
- **Objective:** `richard_bleedout` at `20D:richard` — start ~6 min Richard timer; cinema dumps to 204 — no cell mint
- **Items gained:** _(none)_
- **How to achieve:** `richard_bleedout` at `20D:richard` — start ~6 min Richard timer; cinema dumps to 204 — no cell mint.
- **Success condition:** Mint `20D:richard` via long scripted skip in Pillar Passage (or confirmed 20D→204 dump). Starts Richard's ~6 min death timer. ``capture:false`` — no pl cell; cinema already dumps to C passage.

### `pl86` — `204->207` (step 82)

- **Room:** `207` (2F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `204->207`
- **Note:** leave C passage after Richard dump
- **Objective:** Walk `204->207` into `207` (2F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->207` into `207` (2F RIGHT STAIRS).
- **Success condition:** Enter room `207` via `204->207` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->207 got <room>` (−4).

### `pl87` — `207->10B` (step 83)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `207->10B`
- **Objective:** Walk `207->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `207->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `207->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:207->10B got <room>` (−4).

### `pl88` — `10B->10A` (step 84)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `10B->10A`
- **Objective:** Walk `10B->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `10B->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->10A got <room>` (−4).

### `pl89` — `10A->11A` (step 85)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `traverse`
- **Edge:** `10A->11A`
- **Objective:** Walk `10A->11A` into `11A` (ROOFED PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->11A` into `11A` (ROOFED PASSAGE).
- **Success condition:** Enter room `11A` via `10A->11A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->11A got <room>` (−4).

### `pl90` — `place_sun_crest` (step 86)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `objective`
- **Site:** `sun_crest@11A_crest_slot`
- **Beat:** `place_sun_crest`
- **Note:** place held sun_crest; burns Richard timer
- **Objective:** `place_sun_crest` at `sun_crest@11A_crest_slot` — place held sun_crest; burns Richard timer
- **Items gained:** _(none)_
- **How to achieve:** `place_sun_crest` at `sun_crest@11A_crest_slot` — place held sun_crest; burns Richard timer.
- **Success condition:** `story_use_success` == `sun_crest@11A_crest_slot` in room `11A`

### `pl91` — `11A->10A` (step 87)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `11A->10A`
- **Objective:** Walk `11A->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `11A->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `11A->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:11A->10A got <room>` (−4).

### `pl92` — `10A->10B` (step 88)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10A->10B`
- **Objective:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `10A->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->10B got <room>` (−4).

### `pl93` — `10B->207` (step 89)

- **Room:** `207` (2F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10B->207`
- **Objective:** Walk `10B->207` into `207` (2F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->207` into `207` (2F RIGHT STAIRS).
- **Success condition:** Enter room `207` via `10B->207` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->207 got <room>` (−4).

### `pl94` — `207->204` (step 90)

- **Room:** `204` (C PASSAGE)
- **Op:** `traverse`
- **Edge:** `207->204`
- **Objective:** Walk `207->204` into `204` (C PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `207->204` into `204` (C PASSAGE).
- **Success condition:** Enter room `204` via `207->204` (already-there counts after cinema dump). Any other door is `wrong_traverse:207->204 got <room>` (−4).

### `pl95` — `204->203` (step 91)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `204->203`
- **Objective:** Walk `204->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `204->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->203 got <room>` (−4).

### `pl96` — `dining_2f_enter` (step 92)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `traverse`
- **Edge:** `203->202`
- **Beat:** `dining_2f_enter`
- **Note:** Dining Room 2F
- **Objective:** Walk `203->202` into `202` (DINING ROOM 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->202` into `202` (DINING ROOM 2F).
- **Success condition:** Enter room `202` via `203->202` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->202 got <room>` (−4).

### `pl97` — `push_statue_2f` (step 93)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `do_puzzle`
- **Site:** `dining_statue_knocked`
- **Beat:** `push_statue_2f`
- **Note:** push balcony statue down; blue jewel drops to dining hall 105
- **Objective:** `push_statue_2f` at `dining_statue_knocked` — push balcony statue down; blue jewel drops to dining hall 105
- **Items gained:** _(none)_
- **How to achieve:** `push_statue_2f` at `dining_statue_knocked` — push balcony statue down; blue jewel drops to dining hall 105.
- **Success condition:** Room `202` and dining balcony statue knocked (`dining_statue_flag` bit 0x10 / `dining_statue_knocked`)

### `pl98` — `202->203` (step 94)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `202->203`
- **Note:** leave Dining 2F toward main hall / jewel
- **Objective:** Walk `202->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `202->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `202->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:202->203 got <room>` (−4).

### `pl99` — `203->106` (step 95)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `203->106`
- **Objective:** Walk `203->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `203->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->106 got <room>` (−4).

### `pl100` — `106->105` (step 96)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Edge:** `106->105`
- **Objective:** Walk `106->105` into `105` (DINING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->105` into `105` (DINING ROOM).
- **Success condition:** Enter room `105` via `106->105` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->105 got <room>` (−4).

### `pl101` — `blue_jewel` (step 97)

- **Room:** `105` (DINING ROOM)
- **Op:** `acquire`
- **Pickup:** `105:blue_jewel:1`
- **Beat:** `blue_jewel`
- **Note:** statue drop puts jewel in dining hall 105 (not 202)
- **Objective:** Take `105:blue_jewel:1` (statue drop puts jewel in dining hall 105 (not 202))
- **Items gained:** `blue_jewel`
- **How to achieve:** Take `105:blue_jewel:1` (statue drop puts jewel in dining hall 105 (not 202)).
- **Success condition:** Inventory gains `105:blue_jewel:1` while this step is current

### `pl102` — `105->106` (step 98)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `105->106`
- **Note:** back out; 104->103 still locked — do not tea-cut
- **Objective:** Walk `105->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `105->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `105->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:105->106 got <room>` (−4).

### `pl103` — `106->203` (step 99)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `106->203`
- **Objective:** Walk `106->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `106->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->203 got <room>` (−4).

### `pl104` — `203->202` (step 100)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `traverse`
- **Edge:** `203->202`
- **Objective:** Walk `203->202` into `202` (DINING ROOM 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->202` into `202` (DINING ROOM 2F).
- **Success condition:** Enter room `202` via `203->202` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->202 got <room>` (−4).

### `pl105` — `202->201` (step 101)

- **Room:** `201` (2F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `202->201`
- **Objective:** Walk `202->201` into `201` (2F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `202->201` into `201` (2F LEFT STAIRS).
- **Success condition:** Enter room `201` via `202->201` (already-there counts after cinema dump). Any other door is `wrong_traverse:202->201 got <room>` (−4).

### `pl106` — `201->101` (step 102)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `201->101`
- **Objective:** Walk `201->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `201->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `201->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:201->101 got <room>` (−4).

### `pl107` — `101->103` (step 103)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `101->103`
- **Note:** skip 102 vacant — clip+shells already taken
- **Objective:** Walk `101->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `101->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->103 got <room>` (−4).

### `pl108` — `tiger_room_enter` (step 104)

- **Room:** `10D` (TIGER STATUE ROOM)
- **Op:** `traverse`
- **Edge:** `103->10D`
- **Beat:** `tiger_room_enter`
- **Note:** Tiger Statue Room
- **Objective:** Walk `103->10D` into `10D` (TIGER STATUE ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->10D` into `10D` (TIGER STATUE ROOM).
- **Success condition:** Enter room `10D` via `103->10D` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->10D got <room>` (−4).

### `pl109` — `tiger_jewel` (step 105)

- **Room:** `10D` (TIGER STATUE ROOM)
- **Op:** `objective`
- **Site:** `blue_jewel@10D_tiger_eye`
- **Beat:** `tiger_jewel`
- **Note:** insert blue jewel in tiger eye
- **Objective:** `tiger_jewel` at `blue_jewel@10D_tiger_eye` — insert blue jewel in tiger eye
- **Items gained:** _(none)_
- **How to achieve:** `tiger_jewel` at `blue_jewel@10D_tiger_eye` — insert blue jewel in tiger eye.
- **Success condition:** `story_use_success` == `blue_jewel@10D_tiger_eye` in room `10D`

### `pl110` — `wind_crest` (step 106)

- **Room:** `10D` (TIGER STATUE ROOM)
- **Op:** `acquire`
- **Pickup:** `10D:wind_crest:1`
- **Beat:** `wind_crest`
- **Note:** acquire wind crest; continue to place_wind resource tail
- **Objective:** Take `10D:wind_crest:1` (acquire wind crest; continue to place_wind resource tail)
- **Items gained:** `wind_crest`
- **How to achieve:** Take `10D:wind_crest:1` (acquire wind crest; continue to place_wind resource tail).
- **Success condition:** Inventory gains `10D:wind_crest:1` while this step is current

### `pl111` — `10D->103` (step 107)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `10D->103`
- **Note:** exit tiger room after wind_crest
- **Objective:** Walk `10D->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10D->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `10D->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:10D->103 got <room>` (−4).

### `pl112` — `employee_room_enter` (step 108)

- **Room:** `10E` (EMPLOYEE ROOM)
- **Op:** `traverse`
- **Edge:** `103->10E`
- **Beat:** `employee_room_enter`
- **Note:** Employee Room / keeper ammo
- **Objective:** Walk `103->10E` into `10E` (EMPLOYEE ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->10E` into `10E` (EMPLOYEE ROOM).
- **Success condition:** Enter room `10E` via `103->10E` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->10E got <room>` (−4).

### `pl113` — `10E:handgun_bullets:1` (step 109)

- **Room:** `10E` (EMPLOYEE ROOM)
- **Op:** `acquire`
- **Pickup:** `10E:handgun_bullets:1`
- **Objective:** Take `10E:handgun_bullets:1`
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `10E:handgun_bullets:1`.
- **Success condition:** Inventory gains `10E:handgun_bullets:1` while this step is current

### `pl114` — `10E:shotgun_shells:2` (step 110)

- **Room:** `10E` (EMPLOYEE ROOM)
- **Op:** `acquire`
- **Pickup:** `10E:shotgun_shells:2`
- **Objective:** Take `10E:shotgun_shells:2`
- **Items gained:** `shotgun_shells`
- **How to achieve:** Take `10E:shotgun_shells:2`.
- **Success condition:** Inventory gains `10E:shotgun_shells:2` while this step is current

### `pl115` — `10E->103` (step 111)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `10E->103`
- **Objective:** Walk `10E->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10E->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `10E->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:10E->103 got <room>` (−4).

### `pl116` — `tea_unlock_103_104` (step 112)

- **Room:** `104` (TEA ROOM)
- **Op:** `traverse`
- **Edge:** `103->104`
- **Beat:** `tea_unlock_103_104`
- **Note:** first 103->104 after wind; opens tea both ways
- **Objective:** Walk `103->104` into `104` (TEA ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->104` into `104` (TEA ROOM).
- **Success condition:** Enter room `104` via `103->104` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->104 got <room>` (−4).

### `pl117` — `104->105` (step 113)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Edge:** `104->105`
- **Objective:** Walk `104->105` into `105` (DINING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `104->105` into `105` (DINING ROOM).
- **Success condition:** Enter room `105` via `104->105` (already-there counts after cinema dump). Any other door is `wrong_traverse:104->105 got <room>` (−4).

### `pl118` — `105->106` (step 114)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `105->106`
- **Objective:** Walk `105->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `105->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `105->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:105->106 got <room>` (−4).

### `pl119` — `dressing_room_enter` (step 115)

- **Room:** `111` (DRESSING ROOM)
- **Op:** `traverse`
- **Edge:** `106->111`
- **Beat:** `dressing_room_enter`
- **Note:** armor_key door; dressing ammo
- **Objective:** Walk `106->111` into `111` (DRESSING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->111` into `111` (DRESSING ROOM).
- **Success condition:** Enter room `111` via `106->111` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->111 got <room>` (−4).

### `pl120` — `111:shotgun_shells:2` (step 116)

- **Room:** `111` (DRESSING ROOM)
- **Op:** `acquire`
- **Pickup:** `111:shotgun_shells:2`
- **Objective:** Take `111:shotgun_shells:2`
- **Items gained:** `shotgun_shells`
- **How to achieve:** Take `111:shotgun_shells:2`.
- **Success condition:** Inventory gains `111:shotgun_shells:2` while this step is current

### `pl121` — `111->106` (step 117)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `111->106`
- **Objective:** Walk `111->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `111->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `111->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:111->106 got <room>` (−4).

### `pl122` — `106->107` (step 118)

- **Room:** `107` (GALLERY)
- **Op:** `traverse`
- **Edge:** `106->107`
- **Note:** art room / gallery circuit toward 11A
- **Objective:** Walk `106->107` into `107` (GALLERY)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->107` into `107` (GALLERY).
- **Success condition:** Enter room `107` via `106->107` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->107 got <room>` (−4).

### `pl123` — `107->108` (step 119)

- **Room:** `108` (L PASSAGE)
- **Op:** `traverse`
- **Edge:** `107->108`
- **Objective:** Walk `107->108` into `108` (L PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `107->108` into `108` (L PASSAGE).
- **Success condition:** Enter room `108` via `107->108` (already-there counts after cinema dump). Any other door is `wrong_traverse:107->108 got <room>` (−4).

### `pl124` — `108->109` (step 120)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `traverse`
- **Edge:** `108->109`
- **Objective:** Walk `108->109` into `109` (TRAP PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `108->109` into `109` (TRAP PASSAGE).
- **Success condition:** Enter room `109` via `108->109` (already-there counts after cinema dump). Any other door is `wrong_traverse:108->109 got <room>` (−4).

### `pl125` — `109->10A` (step 121)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `109->10A`
- **Objective:** Walk `109->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `109->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `109->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:109->10A got <room>` (−4).

### `pl126` — `10A->11A` (step 122)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `traverse`
- **Edge:** `10A->11A`
- **Objective:** Walk `10A->11A` into `11A` (ROOFED PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->11A` into `11A` (ROOFED PASSAGE).
- **Success condition:** Enter room `11A` via `10A->11A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->11A got <room>` (−4).

### `pl127` — `place_wind_crest` (step 123)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `objective`
- **Site:** `wind_crest@11A_crest_slot`
- **Beat:** `place_wind_crest`
- **Note:** chunk end-anchor
- **Objective:** `place_wind_crest` at `wind_crest@11A_crest_slot` — chunk end-anchor
- **Items gained:** _(none)_
- **How to achieve:** `place_wind_crest` at `wind_crest@11A_crest_slot` — chunk end-anchor.
- **Success condition:** `story_use_success` == `wind_crest@11A_crest_slot` in room `11A`

