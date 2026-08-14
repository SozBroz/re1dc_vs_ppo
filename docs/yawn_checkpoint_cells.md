# Yawn rails checkpoint cells (`cpNN`)

Generated from [`data/yawn_checkpoint_route.json`](../data/yawn_checkpoint_route.json) (97 steps). Cell directory index is `seq - 1` (`cp00` = seq 1).

**Source of truth:** `data/yawn_checkpoint_route.json` (objectives and success conditions below are copied verbatim). Room names in parentheses come from [`data/rooms.json`](../data/rooms.json).

On success (yawn one-leg), the fleet captures/installs `states/yawn_rails/cells/cpNN/` for the completed index.

## Summary table

| Cell | Seq | Checkpoint ID | Room | Action | Objective |
|------|-----|---------------|------|--------|-----------|
| `cp00` | 1 | `emblem_105` | `105` | pickup | Pick up the wooden emblem |
| `cp01` | 2 | `kenneth_104` | `104` | navigate | Enter the Tea Room |
| `cp02` | 3 | `barry_return_105` | `105` | navigate | Enter Dining after Kenneth, still holding First Aid Spray |
| `cp03` | 4 | `main_hall_106` | `106` | navigate | Reach Main Hall after Kenneth |
| `cp04` | 5 | `upper_hall_203` | `203` | navigate | Climb to Main Hall 2F |
| `cp05` | 6 | `barry_hall_return_106` | `106` | navigate | Return from Main Hall 2F (203) back to Main Hall 1F (106) |
| `cp06` | 7 | `dining_return_105` | `105` | navigate | Return to Dining |
| `cp07` | 8 | `ammo_104` | `104` | navigate | Pass through the Tea Room toward the Bar |
| `cp08` | 9 | `bar_enter_10F` | `10F` | navigate | Enter the Bar |
| `cp09` | 10 | `music_notes_10F` | `10F` | pickup | Take the music notes (after wooden emblem alcove) |
| `cp10` | 11 | `piano_music_notes_10F` | `10F` | use_item | Play the piano with the music notes |
| `cp11` | 12 | `gold_emblem_10F` | `10F` | pickup | Take the gold emblem from the piano bookshelf (wooden emblem may still be held) |
| `cp12` | 13 | `place_emblem_10F` | `10F` | use_item | USE the wooden emblem into the bookshelf slot after taking gold |
| `cp13` | 14 | `tea_return_104` | `104` | navigate | Leave the Bar through the Tea Room |
| `cp14` | 15 | `place_gold_emblem_105` | `105` | use_item | Place the gold emblem on the dining fireplace (must leave inventory) |
| `cp15` | 16 | `shield_key_105` | `105` | pickup | Take the shield key after the fireplace opens |
| `cp16` | 17 | `ink_106` | `106` | navigate | Return through Main Hall |
| `cp17` | 18 | `gallery_107` | `107` | navigate | Cross the Art Room |
| `cp18` | 19 | `l_passage_enter_108` | `108` | navigate | Enter the L Passage (dangerous hallway) |
| `cp19` | 20 | `ammo_108` | `108` | pickup | Collect the L Passage handgun bullets (already in hallway) |
| `cp20` | 21 | `winding_109` | `109` | navigate | Reach the Winding Passage |
| `cp21` | 22 | `trap_entry_115` | `115` | navigate | Enter the shotgun trap room |
| `cp22` | 23 | `shotgun_room_enter_116` | `116` | navigate | Enter the shotgun armor room |
| `cp23` | 24 | `shotgun_116` | `116` | pickup | Take the shotgun |
| `cp24` | 25 | `barry_reenter_115` | `115` | navigate | Re-enter the trap room from the armor room with the shotgun |
| `cp25` | 26 | `barry_rescue_115` | `115` | navigate | Trigger the ceiling-lowering Barry rescue (already re-entered with shotgun) |
| `cp26` | 27 | `back_passage_10A` | `10A` | navigate | Reach the Back Passage |
| `cp27` | 28 | `crow_gallery_enter_117` | `117` | navigate | Enter the crow gallery |
| `cp28` | 29 | `gallery_portrait_1_117` | `117` | navigate | Interact with gallery portrait 1 (newborn) |
| `cp29` | 30 | `gallery_portrait_2_117` | `117` | navigate | Interact with gallery portrait 2 (infant) |
| `cp30` | 31 | `gallery_portrait_3_117` | `117` | navigate | Interact with gallery portrait 3 (boy) |
| `cp31` | 32 | `gallery_portrait_4_117` | `117` | navigate | Interact with gallery portrait 4 (young man) |
| `cp32` | 33 | `gallery_portrait_5_117` | `117` | navigate | Interact with gallery portrait 5 (middle-aged man) |
| `cp33` | 34 | `gallery_portrait_6_117` | `117` | navigate | Interact with gallery portrait 6 (old man) |
| `cp34` | 35 | `star_crest_117` | `117` | pickup | Complete the gallery puzzle and take the star crest |
| `cp35` | 36 | `back_passage_return_10A` | `10A` | navigate | Return to the Back Passage with the star crest |
| `cp36` | 37 | `courtyard_enter_11A` | `11A` | navigate | Enter the courtyard crest gate |
| `cp37` | 38 | `crest_gate_11A` | `11A` | use_item | Place a crest at the courtyard gate (any slot) |
| `cp38` | 39 | `back_passage_post_crest_10A` | `10A` | navigate | Return through the Back Passage after opening the courtyard gate |
| `cp39` | 40 | `east_stairs_101` | `10B` | navigate | Reach East Stairway 1F |
| `cp40` | 41 | `storeroom_enter_118` | `118` | navigate | Enter the mansion storeroom |
| `cp41` | 42 | `chemical_118` | `118` | pickup | Take the herbicide from the mansion storeroom |
| `cp42` | 43 | `east_stairs_101_post_storeroom` | `10B` | navigate | Return to East Stairway 1F from the storeroom |
| `cp43` | 44 | `east_stairs_201` | `207` | navigate | Climb East Stairway to 2F |
| `cp44` | 45 | `c_passage_204` | `204` | navigate | Reach the C Passage |
| `cp45` | 46 | `upper_hall_enter_203` | `203` | navigate | Enter Upper Hall for Barry's acid rounds |
| `cp46` | 47 | `acid_rounds_203` | `203` | pickup | Receive Barry's acid rounds |
| `cp47` | 48 | `terrace_entry_211` | `211` | navigate | Reach the Terrace Entry |
| `cp48` | 49 | `terrace_enter_212` | `212` | navigate | Enter the terrace balcony |
| `cp49` | 50 | `bazooka_212` | `212` | pickup | Take the bazooka on the terrace |
| `cp50` | 51 | `terrace_return_211` | `211` | navigate | Return through Terrace Entry from the balcony |
| `cp51` | 52 | `upper_hall_203_post_terrace` | `203` | navigate | Return through Upper Hall toward Dining 2F |
| `cp52` | 53 | `dining_2f_enter_202` | `202` | navigate | Enter Dining Room 2F |
| `cp53` | 54 | `statue_202` | `202` | navigate | Push the Dining 2F statue down |
| `cp54` | 55 | `west_stairs_207` | `201` | navigate | Reach West Stairway 2F |
| `cp55` | 56 | `west_stairs_10B` | `101` | navigate | Descend West Stairway |
| `cp56` | 57 | `save_100` | `100` | navigate | Reach the Mansion Save Room |
| `cp57` | 58 | `west_stairs_return_10B` | `101` | navigate | Return through West Stairway before the Central Corridor |
| `cp58` | 59 | `central_corridor_103` | `103` | navigate | Reach the Central Corridor |
| `cp59` | 60 | `tiger_room_enter_10C` | `10C` | navigate | Enter the tiger skull room |
| `cp60` | 61 | `armor_key_10C` | `10C` | use_item | Use the chemical and take the armor key |
| `cp61` | 62 | `central_corridor_post_armor_103` | `103` | navigate | Return through the Central Corridor before Plant 42 |
| `cp62` | 63 | `vacant_detour_enter_101` | `101` | navigate | Detour to 1F Left Stairs (101) for Vacant Room ammo |
| `cp63` | 64 | `vacant_enter_102` | `102` | navigate | Enter the Vacant Room (102) |
| `cp64` | 65 | `vacant_ammo_102` | `102` | pickup | Collect Vacant Room shotgun shells and handgun clips |
| `cp65` | 66 | `vacant_return_101` | `101` | navigate | Leave the Vacant Room back to 1F Left Stairs (101) |
| `cp66` | 67 | `vacant_return_103` | `103` | navigate | Return through F Passage (103) toward Keeper's Room |
| `cp67` | 68 | `plant_42_enter_10E` | `10E` | navigate | Enter the Room 42 / plant corridor |
| `cp68` | 69 | `ammo_10E` | `10E` | pickup | Collect the Keeper's Room handgun bullets and shotgun shells |
| `cp69` | 70 | `central_corridor_post_10E_103` | `103` | navigate | Return through the Central Corridor from Plant 42 |
| `cp70` | 71 | `tea_transit_104_post_10E` | `104` | navigate | Cross the Tea Room toward Dining for the blue jewel |
| `cp71` | 72 | `dining_enter_105_jewel` | `105` | navigate | Return to Dining Room for the blue jewel |
| `cp72` | 73 | `blue_jewel_105` | `105` | pickup | Collect the blue jewel after the statue drop |
| `cp73` | 74 | `tea_return_104_post_jewel` | `104` | navigate | Return through the Tea Room after the blue jewel |
| `cp74` | 75 | `central_corridor_post_jewel_103` | `103` | navigate | Return through the Central Corridor toward the Forest |
| `cp75` | 76 | `forest_enter_10D` | `10D` | navigate | Enter the Forest / keep room |
| `cp76` | 77 | `wind_crest_10D` | `10D` | use_item | Use the blue jewel and take the wind crest |
| `cp77` | 78 | `central_corridor_return_103` | `103` | navigate | Return to the Central Corridor after taking the wind crest |
| `cp78` | 79 | `tea_return_104_post_wind` | `104` | navigate | Cross the Tea Room after taking the wind crest |
| `cp79` | 80 | `dining_return_105_post_wind` | `105` | navigate | Cross Dining after taking the wind crest |
| `cp80` | 81 | `main_hall_return_106_post_wind` | `106` | navigate | Reach Main Hall after taking the wind crest |
| `cp81` | 82 | `upper_hall_return_203` | `203` | navigate | Return to Main Hall 2F |
| `cp82` | 83 | `c_passage_return_204` | `204` | navigate | Return to the C Passage |
| `cp83` | 84 | `richard_room_enter_20D` | `20D` | navigate | Enter the east wing hallway for Richard |
| `cp84` | 85 | `richard_cutscene_20D` | `20D` | navigate | Trigger Richard's Pillar Passage cutscene |
| `cp85` | 86 | `richard_forced_return_204` | `204` | navigate | Continue from Richard's cutscene into the C Passage |
| `cp86` | 87 | `east_stairs_201_post_richard` | `207` | navigate | Reach East Stairway 2F after Richard |
| `cp87` | 88 | `east_stairs_101_post_richard` | `10B` | navigate | Descend East Stairway after Richard |
| `cp88` | 89 | `yawn_box_enter_118` | `118` | navigate | Enter the storeroom for Yawn box prep |
| `cp89` | 90 | `yawn_box_prep_118` | `118` | navigate | Deposit wind crest, withdraw guns/ammo from the box, then leave to East Stairway |
| `cp90` | 91 | `east_stairs_201_to_yawn` | `207` | navigate | Climb East Stairway for Yawn |
| `cp91` | 92 | `c_passage_204_to_yawn` | `204` | navigate | Cross the C Passage from East Stairway toward Moon Hall |
| `cp92` | 93 | `moon_hall_enter_20D` | `20D` | navigate | Enter the east wing hallway for attic ammo |
| `cp93` | 94 | `ammo_20D` | `20D` | pickup | Collect the mandatory Pillar Passage handgun bullets |
| `cp94` | 95 | `attic_entry_20E` | `20E` | navigate | Reach the shield-key attic entrance |
| `cp95` | 96 | `yawn_arena_enter_210` | `210` | navigate | Enter the Yawn moon corridor |
| `cp96` | 97 | `yawn_moon_210` | `210` | fight | Complete the Yawn encounter, collect the shells, and take the moon crest |

## Details

### `cp00` — `emblem_105` (seq 1)

- **Room:** `105` (DINING ROOM)
- **Action:** `pickup`
- **Objective:** Pick up the wooden emblem
- **Required items:** _(none)_
- **Items gained:** `emblem`
- **How to achieve:** Be in / reach **105**. Pick up `emblem`.
- **Success condition:**
- **all of:**
  - Enter room `105`
  - Acquire item `emblem`

### `cp01` — `kenneth_104` (seq 2)

- **Room:** `104` (TEA ROOM)
- **Action:** `navigate`
- **Objective:** Enter the Tea Room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `104`

### `cp02` — `barry_return_105` (seq 3)

- **Room:** `105` (DINING ROOM)
- **Action:** `navigate`
- **Objective:** Enter Dining after Kenneth, still holding First Aid Spray
- **Required items:** `first_aid_spray_alt`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Hold: `first_aid_spray_alt`. `beretta` fireable ammo must be exactly **15**. Navigate until the success condition fires.
- **Success condition:**
- **all of:**
  - Enter room `105` from `104`
  - Observe cutscene with prefix `104:` (``:s`` scene key, not a door load)
  - Have item `first_aid_spray_alt` in inventory
  - Inventory `beretta` ammo exactly 15

### `cp03` — `main_hall_106` (seq 4)

- **Room:** `106` (MAIN HALL)
- **Action:** `navigate`
- **Objective:** Reach Main Hall after Kenneth
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **106**. Navigate until the success condition fires.
- **Success condition:**
- **all of:**
  - Enter room `106`
  - Observe cutscene with prefix `106:`

### `cp04` — `upper_hall_203` (seq 5)

- **Room:** `203` (HALL 2F)
- **Action:** `navigate`
- **Objective:** Climb to Main Hall 2F
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **203**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `203`

### `cp05` — `barry_hall_return_106` (seq 6)

- **Room:** `106` (MAIN HALL)
- **Action:** `navigate`
- **Objective:** Return from Main Hall 2F (203) back to Main Hall 1F (106)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **106**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `106` from `203`

### `cp06` — `dining_return_105` (seq 7)

- **Room:** `105` (DINING ROOM)
- **Action:** `navigate`
- **Objective:** Return to Dining
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `105`

### `cp07` — `ammo_104` (seq 8)

- **Room:** `104` (TEA ROOM)
- **Action:** `navigate`
- **Objective:** Pass through the Tea Room toward the Bar
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `104`

### `cp08` — `bar_enter_10F` (seq 9)

- **Room:** `10F` (BAR)
- **Action:** `navigate`
- **Objective:** Enter the Bar
- **Required items:** `emblem`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10F**. Hold: `emblem`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10F`

### `cp09` — `music_notes_10F` (seq 10)

- **Room:** `10F` (BAR)
- **Action:** `pickup`
- **Objective:** Take the music notes (after wooden emblem alcove)
- **Required items:** `emblem`
- **Items gained:** `music_notes`
- **How to achieve:** Be in / reach **10F**. Hold: `emblem`. Pick up `music_notes`.
- **Success condition:**
- **all of:**
  - Enter room `10F`
  - Acquire item `music_notes`

### `cp10` — `piano_music_notes_10F` (seq 11)

- **Room:** `10F` (BAR)
- **Action:** `use_item`
- **Objective:** Play the piano with the music notes
- **Required items:** `music_notes`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10F**. Hold: `music_notes`. Perform the story USE (inventory USE at the site).
- **Success condition:**
- **all of:**
  - Enter room `10F`
  - Story USE at `music_notes@10F_piano`

### `cp11` — `gold_emblem_10F` (seq 12)

- **Room:** `10F` (BAR)
- **Action:** `pickup`
- **Objective:** Take the gold emblem from the piano bookshelf (wooden emblem may still be held)
- **Required items:** _(none)_
- **Items gained:** `gold_emblem`
- **How to achieve:** Be in / reach **10F**. Pick up `gold_emblem`.
- **Success condition:**
- **all of:**
  - Enter room `10F`
  - Acquire item `gold_emblem`

### `cp12` — `place_emblem_10F` (seq 13)

- **Room:** `10F` (BAR)
- **Action:** `use_item`
- **Objective:** USE the wooden emblem into the bookshelf slot after taking gold
- **Required items:** `emblem`, `gold_emblem`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10F**. Hold: `emblem`, `gold_emblem`. Consume `emblem` via story USE. Perform the story USE (inventory USE at the site).
- **Success condition:**
- **all of:**
  - Enter room `10F`
  - **any of:**
    - Story USE at `emblem@10F_alcove`
    - Story USE at `emblem@10F_wall`
  - Lack item `emblem` in inventory

### `cp13` — `tea_return_104` (seq 14)

- **Room:** `104` (TEA ROOM)
- **Action:** `navigate`
- **Objective:** Leave the Bar through the Tea Room
- **Required items:** `gold_emblem`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Hold: `gold_emblem`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `104`

### `cp14` — `place_gold_emblem_105` (seq 15)

- **Room:** `105` (DINING ROOM)
- **Action:** `use_item`
- **Objective:** Place the gold emblem on the dining fireplace (must leave inventory)
- **Required items:** `gold_emblem`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Hold: `gold_emblem`. Consume `gold_emblem` via story USE. Perform the story USE (inventory USE at the site).
- **Success condition:**
- **all of:**
  - Enter room `105`
  - Story USE at `gold_emblem@105_fireplace`
  - Lack item `gold_emblem` in inventory

### `cp15` — `shield_key_105` (seq 16)

- **Room:** `105` (DINING ROOM)
- **Action:** `pickup`
- **Objective:** Take the shield key after the fireplace opens
- **Required items:** _(none)_
- **Items gained:** `shield_key`
- **How to achieve:** Be in / reach **105**. Pick up `shield_key`.
- **Success condition:**
- **all of:**
  - Enter room `105`
  - Acquire item `shield_key`

### `cp16` — `ink_106` (seq 17)

- **Room:** `106` (MAIN HALL)
- **Action:** `navigate`
- **Objective:** Return through Main Hall
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **106**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `106`

### `cp17` — `gallery_107` (seq 18)

- **Room:** `107` (GALLERY)
- **Action:** `navigate`
- **Objective:** Cross the Art Room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **107**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `107`

### `cp18` — `l_passage_enter_108` (seq 19)

- **Room:** `108` (L PASSAGE)
- **Action:** `navigate`
- **Objective:** Enter the L Passage (dangerous hallway)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **108**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `108`

### `cp19` — `ammo_108` (seq 20)

- **Room:** `108` (L PASSAGE)
- **Action:** `pickup`
- **Objective:** Collect the L Passage handgun bullets (already in hallway)
- **Required items:** _(none)_
- **Items gained:** `handgun_bullets`
- **How to achieve:** Be in / reach **108**. Pick up `handgun_bullets`.
- **Success condition:**
- Acquire item `handgun_bullets`

### `cp20` — `winding_109` (seq 21)

- **Room:** `109` (TRAP PASSAGE)
- **Action:** `navigate`
- **Objective:** Reach the Winding Passage
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **109**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `109`

### `cp21` — `trap_entry_115` (seq 22)

- **Room:** `115` (TRAP ROOM)
- **Action:** `navigate`
- **Objective:** Enter the shotgun trap room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **115**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `115`

### `cp22` — `shotgun_room_enter_116` (seq 23)

- **Room:** `116` (LIVING ROOM)
- **Action:** `navigate`
- **Objective:** Enter the shotgun armor room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **116**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `116`

### `cp23` — `shotgun_116` (seq 24)

- **Room:** `116` (LIVING ROOM)
- **Action:** `pickup`
- **Objective:** Take the shotgun
- **Required items:** _(none)_
- **Items gained:** `shotgun`
- **How to achieve:** Be in / reach **116**. Pick up `shotgun`.
- **Success condition:**
- Acquire item `shotgun`

### `cp24` — `barry_reenter_115` (seq 25)

- **Room:** `115` (TRAP ROOM)
- **Action:** `navigate`
- **Objective:** Re-enter the trap room from the armor room with the shotgun
- **Required items:** `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **115**. Hold: `shotgun`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `115` from `116`

### `cp25` — `barry_rescue_115` (seq 26)

- **Room:** `115` (TRAP ROOM)
- **Action:** `navigate`
- **Objective:** Trigger the ceiling-lowering Barry rescue (already re-entered with shotgun)
- **Required items:** `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **115**. Hold: `shotgun`. Navigate until the success condition fires.
- **Success condition:**
- **all of:**
  - Have item `shotgun` in inventory
  - Observe cutscene with prefix `115:`

### `cp26` — `back_passage_10A` (seq 27)

- **Room:** `10A` (BACK PASSAGE)
- **Action:** `navigate`
- **Objective:** Reach the Back Passage
- **Required items:** `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10A**. Hold: `shotgun`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10A`

### `cp27` — `crow_gallery_enter_117` (seq 28)

- **Room:** `117` (LARGE GALLERY)
- **Action:** `navigate`
- **Objective:** Enter the crow gallery
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **117**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `117`

### `cp28` — `gallery_portrait_1_117` (seq 29)

- **Room:** `117` (LARGE GALLERY)
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 1 (newborn)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **117**. Navigate until the success condition fires.
- **Success condition:**
- Gallery puzzle progress >= 0

### `cp29` — `gallery_portrait_2_117` (seq 30)

- **Room:** `117` (LARGE GALLERY)
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 2 (infant)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **117**. Navigate until the success condition fires.
- **Success condition:**
- Gallery puzzle progress >= 0

### `cp30` — `gallery_portrait_3_117` (seq 31)

- **Room:** `117` (LARGE GALLERY)
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 3 (boy)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **117**. Navigate until the success condition fires.
- **Success condition:**
- Gallery puzzle progress >= 0

### `cp31` — `gallery_portrait_4_117` (seq 32)

- **Room:** `117` (LARGE GALLERY)
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 4 (young man)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **117**. Navigate until the success condition fires.
- **Success condition:**
- Gallery puzzle progress >= 0

### `cp32` — `gallery_portrait_5_117` (seq 33)

- **Room:** `117` (LARGE GALLERY)
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 5 (middle-aged man)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **117**. Navigate until the success condition fires.
- **Success condition:**
- Gallery puzzle progress >= 0

### `cp33` — `gallery_portrait_6_117` (seq 34)

- **Room:** `117` (LARGE GALLERY)
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 6 (old man)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **117**. Navigate until the success condition fires.
- **Success condition:**
- Gallery puzzle progress >= 0

### `cp34` — `star_crest_117` (seq 35)

- **Room:** `117` (LARGE GALLERY)
- **Action:** `pickup`
- **Objective:** Complete the gallery puzzle and take the star crest
- **Required items:** _(none)_
- **Items gained:** `star_crest`
- **How to achieve:** Be in / reach **117**. Pick up `star_crest`.
- **Success condition:**
- Acquire item `star_crest`

### `cp35` — `back_passage_return_10A` (seq 36)

- **Room:** `10A` (BACK PASSAGE)
- **Action:** `navigate`
- **Objective:** Return to the Back Passage with the star crest
- **Required items:** `star_crest`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10A**. Hold: `star_crest`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10A`

### `cp36` — `courtyard_enter_11A` (seq 37)

- **Room:** `11A` (ROOFED PASSAGE)
- **Action:** `navigate`
- **Objective:** Enter the courtyard crest gate
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **11A**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `11A`

### `cp37` — `crest_gate_11A` (seq 38)

- **Room:** `11A` (ROOFED PASSAGE)
- **Action:** `use_item`
- **Objective:** Place a crest at the courtyard gate (any slot)
- **Required items:** `star_crest`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **11A**. Hold: `star_crest`. Consume `star_crest` via story USE. Perform the story USE (inventory USE at the site).
- **Success condition:**
- **all of:**
  - Kill **1** enemy in room `11A` this leg
  - **any of:**
    - **all of:**
      - Story USE at `star_crest@11A_crest_slot`
      - Lack item `star_crest` in inventory
    - **all of:**
      - Story USE at `sun_crest@11A_crest_slot`
      - Lack item `sun_crest` in inventory
    - **all of:**
      - Story USE at `moon_crest@11A_crest_slot`
      - Lack item `moon_crest` in inventory
    - **all of:**
      - Story USE at `wind_crest@11A_crest_slot`
      - Lack item `wind_crest` in inventory

### `cp38` — `back_passage_post_crest_10A` (seq 39)

- **Room:** `10A` (BACK PASSAGE)
- **Action:** `navigate`
- **Objective:** Return through the Back Passage after opening the courtyard gate
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10A**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10A`

### `cp39` — `east_stairs_101` (seq 40)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Action:** `navigate`
- **Objective:** Reach East Stairway 1F
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10B**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10B`

### `cp40` — `storeroom_enter_118` (seq 41)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Action:** `navigate`
- **Objective:** Enter the mansion storeroom
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **118**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `118`

### `cp41` — `chemical_118` (seq 42)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Action:** `pickup`
- **Objective:** Take the herbicide from the mansion storeroom
- **Required items:** _(none)_
- **Items gained:** `chemical`
- **How to achieve:** Be in / reach **118**. Pick up `chemical`.
- **Success condition:**
- Acquire item `chemical`

### `cp42` — `east_stairs_101_post_storeroom` (seq 43)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Action:** `navigate`
- **Objective:** Return to East Stairway 1F from the storeroom
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10B**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10B`

### `cp43` — `east_stairs_201` (seq 44)

- **Room:** `207` (2F RIGHT STAIRS)
- **Action:** `navigate`
- **Objective:** Climb East Stairway to 2F
- **Required items:** `chemical`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **207**. Hold: `chemical`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `207`

### `cp44` — `c_passage_204` (seq 45)

- **Room:** `204` (C PASSAGE)
- **Action:** `navigate`
- **Objective:** Reach the C Passage
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **204**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `204`

### `cp45` — `upper_hall_enter_203` (seq 46)

- **Room:** `203` (HALL 2F)
- **Action:** `navigate`
- **Objective:** Enter Upper Hall for Barry's acid rounds
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **203**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `203`

### `cp46` — `acid_rounds_203` (seq 47)

- **Room:** `203` (HALL 2F)
- **Action:** `pickup`
- **Objective:** Receive Barry's acid rounds
- **Required items:** _(none)_
- **Items gained:** `acid_rounds`
- **How to achieve:** Be in / reach **203**. Pick up `acid_rounds`.
- **Success condition:**
- Acquire item `acid_rounds`

### `cp47` — `terrace_entry_211` (seq 48)

- **Room:** `211` (TERRACE PASSAGE)
- **Action:** `navigate`
- **Objective:** Reach the Terrace Entry
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **211**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `211`

### `cp48` — `terrace_enter_212` (seq 49)

- **Room:** `212` (TERRACE)
- **Action:** `navigate`
- **Objective:** Enter the terrace balcony
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **212**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `212`

### `cp49` — `bazooka_212` (seq 50)

- **Room:** `212` (TERRACE)
- **Action:** `pickup`
- **Objective:** Take the bazooka on the terrace
- **Required items:** _(none)_
- **Items gained:** `bazooka_acid`
- **How to achieve:** Be in / reach **212**. Pick up `bazooka_acid`.
- **Success condition:**
- Acquire item `bazooka_acid`

### `cp50` — `terrace_return_211` (seq 51)

- **Room:** `211` (TERRACE PASSAGE)
- **Action:** `navigate`
- **Objective:** Return through Terrace Entry from the balcony
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **211**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `211`

### `cp51` — `upper_hall_203_post_terrace` (seq 52)

- **Room:** `203` (HALL 2F)
- **Action:** `navigate`
- **Objective:** Return through Upper Hall toward Dining 2F
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **203**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `203`

### `cp52` — `dining_2f_enter_202` (seq 53)

- **Room:** `202` (DINING ROOM 2F)
- **Action:** `navigate`
- **Objective:** Enter Dining Room 2F
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **202**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `202`

### `cp53` — `statue_202` (seq 54)

- **Room:** `202` (DINING ROOM 2F)
- **Action:** `navigate`
- **Objective:** Push the Dining 2F statue down
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **202**. Navigate until the success condition fires.
- **Success condition:**
- State flag `dining_statue_knocked` == `True`

### `cp54` — `west_stairs_207` (seq 55)

- **Room:** `201` (2F LEFT STAIRS)
- **Action:** `navigate`
- **Objective:** Reach West Stairway 2F
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **201**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `201`

### `cp55` — `west_stairs_10B` (seq 56)

- **Room:** `101` (1F LEFT STAIRS)
- **Action:** `navigate`
- **Objective:** Descend West Stairway
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **101**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `101`

### `cp56` — `save_100` (seq 57)

- **Room:** `100` (SAVE ROOM)
- **Action:** `navigate`
- **Objective:** Reach the Mansion Save Room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **100**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `100`

### `cp57` — `west_stairs_return_10B` (seq 58)

- **Room:** `101` (1F LEFT STAIRS)
- **Action:** `navigate`
- **Objective:** Return through West Stairway before the Central Corridor
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **101**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `101`

### `cp58` — `central_corridor_103` (seq 59)

- **Room:** `103` (F PASSAGE)
- **Action:** `navigate`
- **Objective:** Reach the Central Corridor
- **Required items:** `chemical`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **103**. Hold: `chemical`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `103`

### `cp59` — `tiger_room_enter_10C` (seq 60)

- **Room:** `10C` (GREEN HOUSE)
- **Action:** `navigate`
- **Objective:** Enter the tiger skull room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10C**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10C`

### `cp60` — `armor_key_10C` (seq 61)

- **Room:** `10C` (GREEN HOUSE)
- **Action:** `use_item`
- **Objective:** Use the chemical and take the armor key
- **Required items:** `chemical`
- **Items gained:** `armor_key`
- **How to achieve:** Be in / reach **10C**. Hold: `chemical`. Consume `chemical` via story USE. Gains: `armor_key`.
- **Success condition:**
- **all of:**
  - Story USE at `chemical@10C_greenhouse_pump`
  - Acquire item `armor_key`

### `cp61` — `central_corridor_post_armor_103` (seq 62)

- **Room:** `103` (F PASSAGE)
- **Action:** `navigate`
- **Objective:** Return through the Central Corridor before Plant 42
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **103**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `103`

### `cp62` — `vacant_detour_enter_101` (seq 63)

- **Room:** `101` (1F LEFT STAIRS)
- **Action:** `navigate`
- **Objective:** Detour to 1F Left Stairs (101) for Vacant Room ammo
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **101**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `101` from `103`

### `cp63` — `vacant_enter_102` (seq 64)

- **Room:** `102` (VACANT ROOM)
- **Action:** `navigate`
- **Objective:** Enter the Vacant Room (102)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **102**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `102` from `101`

### `cp64` — `vacant_ammo_102` (seq 65)

- **Room:** `102` (VACANT ROOM)
- **Action:** `pickup`
- **Objective:** Collect Vacant Room shotgun shells and handgun clips
- **Required items:** _(none)_
- **Items gained:** `shotgun_shells`, `handgun_bullets`
- **How to achieve:** Be in / reach **102**. Pick up `shotgun_shells`, `handgun_bullets`.
- **Success condition:**
- **all of:**
  - Acquire item `shotgun_shells`
  - Acquire item `handgun_bullets`

### `cp65` — `vacant_return_101` (seq 66)

- **Room:** `101` (1F LEFT STAIRS)
- **Action:** `navigate`
- **Objective:** Leave the Vacant Room back to 1F Left Stairs (101)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **101**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `101` from `102`

### `cp66` — `vacant_return_103` (seq 67)

- **Room:** `103` (F PASSAGE)
- **Action:** `navigate`
- **Objective:** Return through F Passage (103) toward Keeper's Room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **103**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `103` from `101`

### `cp67` — `plant_42_enter_10E` (seq 68)

- **Room:** `10E` (EMPLOYEE ROOM)
- **Action:** `navigate`
- **Objective:** Enter the Room 42 / plant corridor
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10E**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10E`

### `cp68` — `ammo_10E` (seq 69)

- **Room:** `10E` (EMPLOYEE ROOM)
- **Action:** `pickup`
- **Objective:** Collect the Keeper's Room handgun bullets and shotgun shells
- **Required items:** _(none)_
- **Items gained:** `handgun_bullets`, `shotgun_shells`
- **How to achieve:** Be in / reach **10E**. Pick up `handgun_bullets`, `shotgun_shells`.
- **Success condition:**
- **all of:**
  - Acquire item `handgun_bullets`
  - Acquire item `shotgun_shells`

### `cp69` — `central_corridor_post_10E_103` (seq 70)

- **Room:** `103` (F PASSAGE)
- **Action:** `navigate`
- **Objective:** Return through the Central Corridor from Plant 42
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **103**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `103`

### `cp70` — `tea_transit_104_post_10E` (seq 71)

- **Room:** `104` (TEA ROOM)
- **Action:** `navigate`
- **Objective:** Cross the Tea Room toward Dining for the blue jewel
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `104`

### `cp71` — `dining_enter_105_jewel` (seq 72)

- **Room:** `105` (DINING ROOM)
- **Action:** `navigate`
- **Objective:** Return to Dining Room for the blue jewel
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `105`

### `cp72` — `blue_jewel_105` (seq 73)

- **Room:** `105` (DINING ROOM)
- **Action:** `pickup`
- **Objective:** Collect the blue jewel after the statue drop
- **Required items:** _(none)_
- **Items gained:** `blue_jewel`
- **How to achieve:** Be in / reach **105**. Pick up `blue_jewel`.
- **Success condition:**
- Acquire item `blue_jewel`

### `cp73` — `tea_return_104_post_jewel` (seq 74)

- **Room:** `104` (TEA ROOM)
- **Action:** `navigate`
- **Objective:** Return through the Tea Room after the blue jewel
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `104`

### `cp74` — `central_corridor_post_jewel_103` (seq 75)

- **Room:** `103` (F PASSAGE)
- **Action:** `navigate`
- **Objective:** Return through the Central Corridor toward the Forest
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **103**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `103`

### `cp75` — `forest_enter_10D` (seq 76)

- **Room:** `10D` (TIGER STATUE ROOM)
- **Action:** `navigate`
- **Objective:** Enter the Forest / keep room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10D**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10D`

### `cp76` — `wind_crest_10D` (seq 77)

- **Room:** `10D` (TIGER STATUE ROOM)
- **Action:** `use_item`
- **Objective:** Use the blue jewel and take the wind crest
- **Required items:** `blue_jewel`
- **Items gained:** `wind_crest`
- **How to achieve:** Be in / reach **10D**. Hold: `blue_jewel`. Consume `blue_jewel` via story USE. Gains: `wind_crest`.
- **Success condition:**
- **all of:**
  - Story USE at `blue_jewel@10D_tiger_eye`
  - Acquire item `wind_crest`

### `cp77` — `central_corridor_return_103` (seq 78)

- **Room:** `103` (F PASSAGE)
- **Action:** `navigate`
- **Objective:** Return to the Central Corridor after taking the wind crest
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **103**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `103`

### `cp78` — `tea_return_104_post_wind` (seq 79)

- **Room:** `104` (TEA ROOM)
- **Action:** `navigate`
- **Objective:** Cross the Tea Room after taking the wind crest
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `104`

### `cp79` — `dining_return_105_post_wind` (seq 80)

- **Room:** `105` (DINING ROOM)
- **Action:** `navigate`
- **Objective:** Cross Dining after taking the wind crest
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `105`

### `cp80` — `main_hall_return_106_post_wind` (seq 81)

- **Room:** `106` (MAIN HALL)
- **Action:** `navigate`
- **Objective:** Reach Main Hall after taking the wind crest
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **106**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `106`

### `cp81` — `upper_hall_return_203` (seq 82)

- **Room:** `203` (HALL 2F)
- **Action:** `navigate`
- **Objective:** Return to Main Hall 2F
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **203**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `203`

### `cp82` — `c_passage_return_204` (seq 83)

- **Room:** `204` (C PASSAGE)
- **Action:** `navigate`
- **Objective:** Return to the C Passage
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **204**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `204`

### `cp83` — `richard_room_enter_20D` (seq 84)

- **Room:** `20D` (PILLAR PASSAGE)
- **Action:** `navigate`
- **Objective:** Enter the east wing hallway for Richard
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **20D**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `20D`

### `cp84` — `richard_cutscene_20D` (seq 85)

- **Room:** `20D` (PILLAR PASSAGE)
- **Action:** `navigate`
- **Objective:** Trigger Richard's Pillar Passage cutscene
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **20D**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Observe cutscene with prefix `20D:`

### `cp85` — `richard_forced_return_204` (seq 86)

- **Room:** `204` (C PASSAGE)
- **Action:** `navigate`
- **Objective:** Continue from Richard's cutscene into the C Passage
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **204**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `204`

### `cp86` — `east_stairs_201_post_richard` (seq 87)

- **Room:** `207` (2F RIGHT STAIRS)
- **Action:** `navigate`
- **Objective:** Reach East Stairway 2F after Richard
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **207**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `207`

### `cp87` — `east_stairs_101_post_richard` (seq 88)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Action:** `navigate`
- **Objective:** Descend East Stairway after Richard
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10B**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `10B`

### `cp88` — `yawn_box_enter_118` (seq 89)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Action:** `navigate`
- **Objective:** Enter the storeroom for Yawn box prep
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **118**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `118`

### `cp89` — `yawn_box_prep_118` (seq 90)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Action:** `navigate`
- **Objective:** Deposit wind crest, withdraw guns/ammo from the box, then leave to East Stairway
- **Required items:** `shield_key`, `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **118**. Hold: `shield_key`, `shotgun`. Navigate until the success condition fires.
- **Success condition:**
- (yawn_box_prep_exit) `{"type": "yawn_box_prep_exit"}`

### `cp90` — `east_stairs_201_to_yawn` (seq 91)

- **Room:** `207` (2F RIGHT STAIRS)
- **Action:** `navigate`
- **Objective:** Climb East Stairway for Yawn
- **Required items:** `shield_key`, `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **207**. Hold: `shield_key`, `shotgun`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `207`

### `cp91` — `c_passage_204_to_yawn` (seq 92)

- **Room:** `204` (C PASSAGE)
- **Action:** `navigate`
- **Objective:** Cross the C Passage from East Stairway toward Moon Hall
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **204**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `204`

### `cp92` — `moon_hall_enter_20D` (seq 93)

- **Room:** `20D` (PILLAR PASSAGE)
- **Action:** `navigate`
- **Objective:** Enter the east wing hallway for attic ammo
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **20D**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `20D`

### `cp93` — `ammo_20D` (seq 94)

- **Room:** `20D` (PILLAR PASSAGE)
- **Action:** `pickup`
- **Objective:** Collect the mandatory Pillar Passage handgun bullets
- **Required items:** _(none)_
- **Items gained:** `handgun_bullets`
- **How to achieve:** Be in / reach **20D**. Pick up `handgun_bullets`.
- **Success condition:**
- Acquire item `handgun_bullets`

### `cp94` — `attic_entry_20E` (seq 95)

- **Room:** `20E` (FRONT OF ATTIC)
- **Action:** `navigate`
- **Objective:** Reach the shield-key attic entrance
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **20E**. Hold: `shield_key`. Navigate until the success condition fires.
- **Success condition:**
- Enter room `20E` from `20D`

### `cp95` — `yawn_arena_enter_210` (seq 96)

- **Room:** `210` (ATTIC)
- **Action:** `navigate`
- **Objective:** Enter the Yawn moon corridor
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **210**. Navigate until the success condition fires.
- **Success condition:**
- Enter room `210`

### `cp96` — `yawn_moon_210` (seq 97)

- **Room:** `210` (ATTIC)
- **Action:** `fight`
- **Objective:** Complete the Yawn encounter, collect the shells, and take the moon crest
- **Required items:** `shield_key`, `shotgun`
- **Items gained:** `shotgun_shells`, `moon_crest`
- **How to achieve:** Be in / reach **210**. Hold: `shield_key`, `shotgun`. Fight until the combat success condition clears. Gains: `shotgun_shells`, `moon_crest`.
- **Success condition:**
- **all of:**
  - Acquire item `shotgun_shells`
  - Acquire item `moon_crest`
