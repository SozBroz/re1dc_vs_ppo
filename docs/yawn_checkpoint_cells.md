# Yawn rails checkpoint cells (`cpNN`)

Generated from [`data/yawn_checkpoint_route.json`](../data/yawn_checkpoint_route.json) (82 steps). Cell directory index is `seq - 1` (`cp00` = seq 1).

On success (yawn one-leg), the fleet captures/installs `states/yawn_rails/cells/cpNN/` for the completed index.

## Summary table

| cell | seq | checkpoint_id | room | action | objective |
|------|-----|---------------|------|--------|-----------|
| `cp00` | 1 | `emblem_105` | `105` | pickup | Pick up the wooden emblem |
| `cp01` | 2 | `kenneth_104` | `104` | navigate | Reach the Tea Room and trigger Kenneth |
| `cp02` | 3 | `barry_return_105` | `105` | navigate | Return to Barry in Dining |
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
| `cp34` | 35 | `star_crest_117` | `117` | pickup | Take the star crest after completing the gallery puzzle |
| `cp35` | 36 | `back_passage_return_10A` | `10A` | navigate | Return to the Back Passage with the star crest |
| `cp36` | 37 | `courtyard_enter_11A` | `11A` | navigate | Enter the courtyard crest gate |
| `cp37` | 38 | `crest_gate_11A` | `11A` | use_item | Place the star crest at the courtyard gate |
| `cp38` | 39 | `east_stairs_101` | `101` | navigate | Reach East Stairway 1F |
| `cp39` | 40 | `storeroom_enter_11B` | `11B` | navigate | Enter the mansion storeroom |
| `cp40` | 41 | `chemical_11B` | `11B` | pickup | Take the herbicide from the mansion storeroom |
| `cp41` | 42 | `east_stairs_201` | `201` | navigate | Climb East Stairway to 2F |
| `cp42` | 43 | `c_passage_204` | `204` | navigate | Reach the C Passage |
| `cp43` | 44 | `upper_hall_enter_203` | `203` | navigate | Enter Upper Hall for Barry's acid rounds |
| `cp44` | 45 | `acid_rounds_203` | `203` | pickup | Receive Barry's acid rounds |
| `cp45` | 46 | `terrace_entry_211` | `211` | navigate | Reach the Terrace Entry |
| `cp46` | 47 | `terrace_enter_212` | `212` | navigate | Enter the terrace balcony |
| `cp47` | 48 | `bazooka_212` | `212` | pickup | Take the bazooka on the terrace |
| `cp48` | 49 | `dining_2f_enter_202` | `202` | navigate | Enter Dining Room 2F |
| `cp49` | 50 | `statue_202` | `202` | navigate | Push the Dining 2F statue down |
| `cp50` | 51 | `west_stairs_207` | `207` | navigate | Reach West Stairway 2F |
| `cp51` | 52 | `west_stairs_10B` | `10B` | navigate | Descend West Stairway |
| `cp52` | 53 | `save_100` | `100` | navigate | Reach the Mansion Save Room |
| `cp53` | 54 | `central_corridor_103` | `103` | navigate | Reach the Central Corridor |
| `cp54` | 55 | `tiger_room_enter_10C` | `10C` | navigate | Enter the tiger skull room |
| `cp55` | 56 | `armor_key_10C` | `10C` | use_item | Use the chemical and take the armor key |
| `cp56` | 57 | `plant_42_enter_10E` | `10E` | navigate | Enter the Room 42 / plant corridor |
| `cp57` | 58 | `ammo_10E` | `10E` | pickup | Collect the Keeper's Room handgun bullets and shotgun shells |
| `cp58` | 59 | `dining_enter_105_jewel` | `105` | navigate | Return to Dining Room for the blue jewel |
| `cp59` | 60 | `blue_jewel_105` | `105` | pickup | Collect the blue jewel after the statue drop |
| `cp60` | 61 | `forest_enter_10D` | `10D` | navigate | Enter the Forest / keep room |
| `cp61` | 62 | `wind_crest_10D` | `10D` | use_item | Use the blue jewel and take the wind crest |
| `cp62` | 63 | `central_corridor_return_103` | `103` | navigate | Return to the Central Corridor after taking the wind crest |
| `cp63` | 64 | `tea_return_104_post_wind` | `104` | navigate | Cross the Tea Room after taking the wind crest |
| `cp64` | 65 | `dining_return_105_post_wind` | `105` | navigate | Cross Dining after taking the wind crest |
| `cp65` | 66 | `main_hall_return_106_post_wind` | `106` | navigate | Reach Main Hall after taking the wind crest |
| `cp66` | 67 | `upper_hall_return_203` | `203` | navigate | Return to Main Hall 2F |
| `cp67` | 68 | `c_passage_return_204` | `204` | navigate | Return to the C Passage |
| `cp68` | 69 | `richard_room_enter_20D` | `20D` | navigate | Enter the east wing hallway for Richard |
| `cp69` | 70 | `richard_cutscene_20D` | `20D` | navigate | Trigger Richard's Pillar Passage cutscene |
| `cp70` | 71 | `richard_forced_return_204` | `204` | navigate | Continue from Richard's cutscene into the C Passage |
| `cp71` | 72 | `east_stairs_201_post_richard` | `201` | navigate | Reach East Stairway 2F after Richard |
| `cp72` | 73 | `east_stairs_101_post_richard` | `101` | navigate | Descend East Stairway after Richard |
| `cp73` | 74 | `yawn_box_enter_11B` | `11B` | navigate | Enter the storeroom for Yawn box prep |
| `cp74` | 75 | `yawn_box_prep_11B` | `11B` | navigate | Prepare inventory at the item box and wait for the lab timer to expire naturally |
| `cp75` | 76 | `east_stairs_101_to_yawn` | `101` | navigate | Return to East Stairway 1F for Yawn |
| `cp76` | 77 | `east_stairs_201_to_yawn` | `201` | navigate | Climb East Stairway for Yawn |
| `cp77` | 78 | `moon_hall_enter_20D` | `20D` | navigate | Enter the east wing hallway for attic ammo |
| `cp78` | 79 | `ammo_20D` | `20D` | pickup | Collect the mandatory Pillar Passage handgun bullets |
| `cp79` | 80 | `attic_entry_20E` | `20E` | navigate | Reach the shield-key attic entrance |
| `cp80` | 81 | `yawn_arena_enter_210` | `210` | navigate | Enter the Yawn moon corridor |
| `cp81` | 82 | `yawn_moon_210` | `210` | fight | Complete the Yawn encounter, collect the shells, and take the moon crest |

## Details

### `cp00` — `emblem_105` (seq 1)

- **Room:** `105`
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

- **Room:** `104`
- **Action:** `navigate`
- **Objective:** Reach the Tea Room and trigger Kenneth
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Navigate until the success enter/settle conditions fire. Must observe a `104:` cutscene, then settle after it.
- **Success condition:**
- **all of:**
  - Enter room `104`
  - Observe cutscene with prefix `104:`
  - Stay in-control in `104` for **45** steps **after** cutscene `104:`

### `cp02` — `barry_return_105` (seq 3)

- **Room:** `105`
- **Action:** `navigate`
- **Objective:** Return to Barry in Dining
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Navigate until the success enter/settle conditions fire. Must see post-Kenneth Barry return beat (`105:2:s1`), then settle.
- **Success condition:**
- **all of:**
  - Enter room `105` from `104`
  - Observe cutscene with prefix `105:2:s1`
  - Stay in-control in `105` for **60** steps **after** cutscene `105:2:s1`

### `cp03` — `main_hall_106` (seq 4)

- **Room:** `106`
- **Action:** `navigate`
- **Objective:** Reach Main Hall after Kenneth
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **106**. Navigate until the success enter/settle conditions fire. Must observe a `106:` cutscene, then settle after it.
- **Success condition:**
- **all of:**
  - Enter room `106`
  - Observe cutscene with prefix `106:`
  - Stay in-control in `106` for **45** steps **after** cutscene `106:`

### `cp04` — `upper_hall_203` (seq 5)

- **Room:** `203`
- **Action:** `navigate`
- **Objective:** Climb to Main Hall 2F
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **203**. Navigate until the success enter/settle conditions fire. First climb to 2F has **no cutscene**; settle in-room only.
- **Success condition:**
- **all of:**
  - Enter room `203`
  - Stay in-control in `203` for **30** steps

### `cp05` — `barry_hall_return_106` (seq 6)

- **Room:** `106`
- **Action:** `navigate`
- **Objective:** Return from Main Hall 2F (203) back to Main Hall 1F (106)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **106**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- **all of:**
  - Enter room `106` from `203`
  - Stay in-control in `106` for **30** steps

### `cp06` — `dining_return_105` (seq 7)

- **Room:** `105`
- **Action:** `navigate`
- **Objective:** Return to Dining
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `105`

### `cp07` — `ammo_104` (seq 8)

- **Room:** `104`
- **Action:** `navigate`
- **Objective:** Pass through the Tea Room toward the Bar
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `104`

### `cp08` — `bar_enter_10F` (seq 9)

- **Room:** `10F`
- **Action:** `navigate`
- **Objective:** Enter the Bar
- **Required items:** `emblem`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10F**. Hold: `emblem`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `10F`

### `cp09` — `music_notes_10F` (seq 10)

- **Room:** `10F`
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

- **Room:** `10F`
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

- **Room:** `10F`
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

- **Room:** `10F`
- **Action:** `use_item`
- **Objective:** USE the wooden emblem into the bookshelf slot after taking gold
- **Required items:** `emblem`, `gold_emblem`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10F**. Hold: `emblem`, `gold_emblem`. Perform the story USE (inventory USE at the site). Put the wooden emblem back after taking gold; inventory must lose emblem.
- **Success condition:**
- **all of:**
  - Enter room `10F`
  - **any of:**
    - Story USE at `emblem@10F_alcove`
    - Story USE at `emblem@10F_wall`
  - No longer holding `emblem`

### `cp13` — `tea_return_104` (seq 14)

- **Room:** `104`
- **Action:** `navigate`
- **Objective:** Leave the Bar through the Tea Room
- **Required items:** `gold_emblem`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Hold: `gold_emblem`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `104`

### `cp14` — `place_gold_emblem_105` (seq 15)

- **Room:** `105`
- **Action:** `use_item`
- **Objective:** Place the gold emblem on the dining fireplace (must leave inventory)
- **Required items:** `gold_emblem`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Hold: `gold_emblem`. Perform the story USE (inventory USE at the site). USE gold emblem on dining fireplace; must leave inventory.
- **Success condition:**
- **all of:**
  - Enter room `105`
  - Story USE at `gold_emblem@105_fireplace`
  - No longer holding `gold_emblem`

### `cp15` — `shield_key_105` (seq 16)

- **Room:** `105`
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

- **Room:** `106`
- **Action:** `navigate`
- **Objective:** Return through Main Hall
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **106**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `106`

### `cp17` — `gallery_107` (seq 18)

- **Room:** `107`
- **Action:** `navigate`
- **Objective:** Cross the Art Room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **107**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `107`

### `cp18` — `l_passage_enter_108` (seq 19)

- **Room:** `108`
- **Action:** `navigate`
- **Objective:** Enter the L Passage (dangerous hallway)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **108**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `108`

### `cp19` — `ammo_108` (seq 20)

- **Room:** `108`
- **Action:** `pickup`
- **Objective:** Collect the L Passage handgun bullets (already in hallway)
- **Required items:** _(none)_
- **Items gained:** `handgun_bullets`
- **How to achieve:** Be in / reach **108**. Pick up `handgun_bullets`.
- **Success condition:**
- Acquire item `handgun_bullets`

### `cp20` — `winding_109` (seq 21)

- **Room:** `109`
- **Action:** `navigate`
- **Objective:** Reach the Winding Passage
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **109**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `109`

### `cp21` — `trap_entry_115` (seq 22)

- **Room:** `115`
- **Action:** `navigate`
- **Objective:** Enter the shotgun trap room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **115**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `115`

### `cp22` — `shotgun_room_enter_116` (seq 23)

- **Room:** `116`
- **Action:** `navigate`
- **Objective:** Enter the shotgun armor room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **116**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `116`

### `cp23` — `shotgun_116` (seq 24)

- **Room:** `116`
- **Action:** `pickup`
- **Objective:** Take the shotgun
- **Required items:** _(none)_
- **Items gained:** `shotgun`
- **How to achieve:** Be in / reach **116**. Pick up `shotgun`.
- **Success condition:**
- Acquire item `shotgun`

### `cp24` — `barry_reenter_115` (seq 25)

- **Room:** `115`
- **Action:** `navigate`
- **Objective:** Re-enter the trap room from the armor room with the shotgun
- **Required items:** `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **115**. Hold: `shotgun`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `115` from `116`

### `cp25` — `barry_rescue_115` (seq 26)

- **Room:** `115`
- **Action:** `navigate`
- **Objective:** Trigger the ceiling-lowering Barry rescue (already re-entered with shotgun)
- **Required items:** `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **115**. Hold: `shotgun`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- **all of:**
  - Holding item `shotgun`
  - Observe cutscene with prefix `115:`

### `cp26` — `back_passage_10A` (seq 27)

- **Room:** `10A`
- **Action:** `navigate`
- **Objective:** Reach the Back Passage
- **Required items:** `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10A**. Hold: `shotgun`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `10A`

### `cp27` — `crow_gallery_enter_117` (seq 28)

- **Room:** `117`
- **Action:** `navigate`
- **Objective:** Enter the crow gallery
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **117**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `117`

### `cp28` — `gallery_portrait_1_117` (seq 29)

- **Room:** `117`
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 1 (newborn)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in **117**. Interact with the correct portrait (newborn) in sequence.
- **Success condition:**
- Gallery progress ≥ 1 correct portrait(s)

### `cp29` — `gallery_portrait_2_117` (seq 30)

- **Room:** `117`
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 2 (infant)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in **117**. Interact with the correct portrait (infant) in sequence.
- **Success condition:**
- Gallery progress ≥ 2 correct portrait(s)

### `cp30` — `gallery_portrait_3_117` (seq 31)

- **Room:** `117`
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 3 (boy)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in **117**. Interact with the correct portrait (boy) in sequence.
- **Success condition:**
- Gallery progress ≥ 3 correct portrait(s)

### `cp31` — `gallery_portrait_4_117` (seq 32)

- **Room:** `117`
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 4 (young man)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in **117**. Interact with the correct portrait (young man) in sequence.
- **Success condition:**
- Gallery progress ≥ 4 correct portrait(s)

### `cp32` — `gallery_portrait_5_117` (seq 33)

- **Room:** `117`
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 5 (middle-aged man)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in **117**. Interact with the correct portrait (middle-aged man) in sequence.
- **Success condition:**
- Gallery progress ≥ 5 correct portrait(s)

### `cp33` — `gallery_portrait_6_117` (seq 34)

- **Room:** `117`
- **Action:** `navigate`
- **Objective:** Interact with gallery portrait 6 (old man)
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in **117**. Interact with the correct portrait (old man) in sequence.
- **Success condition:**
- Gallery progress ≥ 6 correct portrait(s)

### `cp34` — `star_crest_117` (seq 35)

- **Room:** `117`
- **Action:** `pickup`
- **Objective:** Take the star crest after completing the gallery puzzle
- **Required items:** _(none)_
- **Items gained:** `star_crest`
- **How to achieve:** Be in **117**. After all six portraits, interact with the final switch and pick up `star_crest`.
- **Success condition:**
- Acquire item `star_crest`

### `cp35` — `back_passage_return_10A` (seq 30)

- **Room:** `10A`
- **Action:** `navigate`
- **Objective:** Return to the Back Passage with the star crest
- **Required items:** `star_crest`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10A**. Hold: `star_crest`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `10A`

### `cp36` — `courtyard_enter_11A` (seq 31)

- **Room:** `11A`
- **Action:** `navigate`
- **Objective:** Enter the courtyard crest gate
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **11A**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `11A`

### `cp37` — `crest_gate_11A` (seq 32)

- **Room:** `11A`
- **Action:** `use_item`
- **Objective:** Place the star crest at the courtyard gate
- **Required items:** `star_crest`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **11A**. Hold: `star_crest`. Perform the story USE (inventory USE at the site).
- **Success condition:**
- No longer holding `star_crest`

### `cp38` — `east_stairs_101` (seq 33)

- **Room:** `101`
- **Action:** `navigate`
- **Objective:** Reach East Stairway 1F
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **101**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `101`

### `cp39` — `storeroom_enter_11B` (seq 34)

- **Room:** `11B`
- **Action:** `navigate`
- **Objective:** Enter the mansion storeroom
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **11B**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `11B`

### `cp40` — `chemical_11B` (seq 35)

- **Room:** `11B`
- **Action:** `pickup`
- **Objective:** Take the herbicide from the mansion storeroom
- **Required items:** _(none)_
- **Items gained:** `chemical`
- **How to achieve:** Be in / reach **11B**. Pick up `chemical`.
- **Success condition:**
- Acquire item `chemical`

### `cp41` — `east_stairs_201` (seq 36)

- **Room:** `201`
- **Action:** `navigate`
- **Objective:** Climb East Stairway to 2F
- **Required items:** `chemical`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **201**. Hold: `chemical`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `201`

### `cp42` — `c_passage_204` (seq 37)

- **Room:** `204`
- **Action:** `navigate`
- **Objective:** Reach the C Passage
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **204**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `204`

### `cp43` — `upper_hall_enter_203` (seq 38)

- **Room:** `203`
- **Action:** `navigate`
- **Objective:** Enter Upper Hall for Barry's acid rounds
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **203**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `203`

### `cp44` — `acid_rounds_203` (seq 39)

- **Room:** `203`
- **Action:** `pickup`
- **Objective:** Receive Barry's acid rounds
- **Required items:** _(none)_
- **Items gained:** `acid_rounds`
- **How to achieve:** Be in / reach **203**. Pick up `acid_rounds`.
- **Success condition:**
- Acquire item `acid_rounds`

### `cp45` — `terrace_entry_211` (seq 40)

- **Room:** `211`
- **Action:** `navigate`
- **Objective:** Reach the Terrace Entry
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **211**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `211`

### `cp46` — `terrace_enter_212` (seq 41)

- **Room:** `212`
- **Action:** `navigate`
- **Objective:** Enter the terrace balcony
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **212**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `212`

### `cp47` — `bazooka_212` (seq 42)

- **Room:** `212`
- **Action:** `pickup`
- **Objective:** Take the bazooka on the terrace
- **Required items:** _(none)_
- **Items gained:** `bazooka_acid`
- **How to achieve:** Be in / reach **212**. Pick up `bazooka_acid`.
- **Success condition:**
- Acquire item `bazooka_acid`

### `cp48` — `dining_2f_enter_202` (seq 43)

- **Room:** `202`
- **Action:** `navigate`
- **Objective:** Enter Dining Room 2F
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **202**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `202`

### `cp49` — `statue_202` (seq 44)

- **Room:** `202`
- **Action:** `navigate`
- **Objective:** Push the Dining 2F statue down
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **202**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- State flag `dining_statue_knocked` == `True`

### `cp50` — `west_stairs_207` (seq 45)

- **Room:** `207`
- **Action:** `navigate`
- **Objective:** Reach West Stairway 2F
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **207**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `207`

### `cp51` — `west_stairs_10B` (seq 46)

- **Room:** `10B`
- **Action:** `navigate`
- **Objective:** Descend West Stairway
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10B**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `10B`

### `cp52` — `save_100` (seq 47)

- **Room:** `100`
- **Action:** `navigate`
- **Objective:** Reach the Mansion Save Room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **100**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `100`

### `cp53` — `central_corridor_103` (seq 48)

- **Room:** `103`
- **Action:** `navigate`
- **Objective:** Reach the Central Corridor
- **Required items:** `chemical`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **103**. Hold: `chemical`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `103`

### `cp54` — `tiger_room_enter_10C` (seq 49)

- **Room:** `10C`
- **Action:** `navigate`
- **Objective:** Enter the tiger skull room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10C**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `10C`

### `cp55` — `armor_key_10C` (seq 50)

- **Room:** `10C`
- **Action:** `use_item`
- **Objective:** Use the chemical and take the armor key
- **Required items:** `chemical`
- **Items gained:** `armor_key`
- **How to achieve:** Be in / reach **10C**. Hold: `chemical`. Perform the story USE (inventory USE at the site). Gains: `armor_key`.
- **Success condition:**
- **all of:**
  - Story USE at `chemical@10C_greenhouse_pump`
  - Acquire item `armor_key`

### `cp56` — `plant_42_enter_10E` (seq 51)

- **Room:** `10E`
- **Action:** `navigate`
- **Objective:** Enter the Room 42 / plant corridor
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10E**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `10E`

### `cp57` — `ammo_10E` (seq 52)

- **Room:** `10E`
- **Action:** `pickup`
- **Objective:** Collect the Keeper's Room handgun bullets and shotgun shells
- **Required items:** _(none)_
- **Items gained:** `handgun_bullets`, `shotgun_shells`
- **How to achieve:** Be in / reach **10E**. Pick up `handgun_bullets`.
- **Success condition:**
- **all of:**
  - Acquire item `handgun_bullets`
  - Acquire item `shotgun_shells`

### `cp58` — `dining_enter_105_jewel` (seq 53)

- **Room:** `105`
- **Action:** `navigate`
- **Objective:** Return to Dining Room for the blue jewel
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `105`

### `cp59` — `blue_jewel_105` (seq 54)

- **Room:** `105`
- **Action:** `pickup`
- **Objective:** Collect the blue jewel after the statue drop
- **Required items:** _(none)_
- **Items gained:** `blue_jewel`
- **How to achieve:** Be in / reach **105**. Pick up `blue_jewel`.
- **Success condition:**
- Acquire item `blue_jewel`

### `cp60` — `forest_enter_10D` (seq 55)

- **Room:** `10D`
- **Action:** `navigate`
- **Objective:** Enter the Forest / keep room
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **10D**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `10D`

### `cp61` — `wind_crest_10D` (seq 56)

- **Room:** `10D`
- **Action:** `use_item`
- **Objective:** Use the blue jewel and take the wind crest
- **Required items:** `blue_jewel`
- **Items gained:** `wind_crest`
- **How to achieve:** Be in / reach **10D**. Hold: `blue_jewel`. Perform the story USE (inventory USE at the site). Gains: `wind_crest`.
- **Success condition:**
- **all of:**
  - Story USE at `blue_jewel@10D_tiger_eye`
  - Acquire item `wind_crest`

### `cp62` — `central_corridor_return_103` (seq 57)

- **Room:** `103`
- **Action:** `navigate`
- **Objective:** Return to the Central Corridor after taking the wind crest
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **103**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `103`

### `cp63` — `tea_return_104_post_wind` (seq 58)

- **Room:** `104`
- **Action:** `navigate`
- **Objective:** Cross the Tea Room after taking the wind crest
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **104**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `104`

### `cp64` — `dining_return_105_post_wind` (seq 59)

- **Room:** `105`
- **Action:** `navigate`
- **Objective:** Cross Dining after taking the wind crest
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **105**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `105`

### `cp65` — `main_hall_return_106_post_wind` (seq 60)

- **Room:** `106`
- **Action:** `navigate`
- **Objective:** Reach Main Hall after taking the wind crest
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **106**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `106`

### `cp66` — `upper_hall_return_203` (seq 61)

- **Room:** `203`
- **Action:** `navigate`
- **Objective:** Return to Main Hall 2F
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **203**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `203`

### `cp67` — `c_passage_return_204` (seq 62)

- **Room:** `204`
- **Action:** `navigate`
- **Objective:** Return to the C Passage
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **204**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `204`

### `cp68` — `richard_room_enter_20D` (seq 63)

- **Room:** `20D`
- **Action:** `navigate`
- **Objective:** Enter the east wing hallway for Richard
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **20D**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- `room_enter_any`: `{"room_ids": ["20D", "204"], "type": "room_enter_any"}`

### `cp69` — `richard_cutscene_20D` (seq 64)

- **Room:** `20D`
- **Action:** `navigate`
- **Objective:** Trigger Richard's Pillar Passage cutscene
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **20D**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Observe cutscene with prefix `20D:`

### `cp70` — `richard_forced_return_204` (seq 65)

- **Room:** `204`
- **Action:** `navigate`
- **Objective:** Continue from Richard's cutscene into the C Passage
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **204**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `204`

### `cp71` — `east_stairs_201_post_richard` (seq 66)

- **Room:** `201`
- **Action:** `navigate`
- **Objective:** Reach East Stairway 2F after Richard
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **201**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `201`

### `cp72` — `east_stairs_101_post_richard` (seq 67)

- **Room:** `101`
- **Action:** `navigate`
- **Objective:** Descend East Stairway after Richard
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **101**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `101`

### `cp73` — `yawn_box_enter_11B` (seq 68)

- **Room:** `11B`
- **Action:** `navigate`
- **Objective:** Enter the storeroom for Yawn box prep
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **11B**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `11B`

### `cp74` — `yawn_box_prep_11B` (seq 69)

- **Room:** `11B`
- **Action:** `navigate`
- **Objective:** Prepare inventory at the item box and wait for the lab timer to expire naturally
- **Required items:** `shield_key`, `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **11B**. Hold: `shield_key`, `shotgun`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- State flag `lab_timer` == `0`

### `cp75` — `east_stairs_101_to_yawn` (seq 70)

- **Room:** `101`
- **Action:** `navigate`
- **Objective:** Return to East Stairway 1F for Yawn
- **Required items:** `shield_key`, `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **101**. Hold: `shield_key`, `shotgun`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `101`

### `cp76` — `east_stairs_201_to_yawn` (seq 71)

- **Room:** `201`
- **Action:** `navigate`
- **Objective:** Climb East Stairway for Yawn
- **Required items:** `shield_key`, `shotgun`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **201**. Hold: `shield_key`, `shotgun`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `201`

### `cp77` — `moon_hall_enter_20D` (seq 72)

- **Room:** `20D`
- **Action:** `navigate`
- **Objective:** Enter the east wing hallway for attic ammo
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **20D**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `20D`

### `cp78` — `ammo_20D` (seq 73)

- **Room:** `20D`
- **Action:** `pickup`
- **Objective:** Collect the mandatory Pillar Passage handgun bullets
- **Required items:** _(none)_
- **Items gained:** `handgun_bullets`
- **How to achieve:** Be in / reach **20D**. Pick up `handgun_bullets`.
- **Success condition:**
- Acquire item `handgun_bullets`

### `cp79` — `attic_entry_20E` (seq 74)

- **Room:** `20E`
- **Action:** `navigate`
- **Objective:** Reach the shield-key attic entrance
- **Required items:** `shield_key`
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **20E**. Hold: `shield_key`. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `20E` from `20D`

### `cp80` — `yawn_arena_enter_210` (seq 75)

- **Room:** `210`
- **Action:** `navigate`
- **Objective:** Enter the Yawn moon corridor
- **Required items:** _(none)_
- **Items gained:** _(none)_
- **How to achieve:** Be in / reach **210**. Navigate until the success enter/settle conditions fire.
- **Success condition:**
- Enter room `210`

### `cp81` — `yawn_moon_210` (seq 76)

- **Room:** `210`
- **Action:** `fight`
- **Objective:** Complete the Yawn encounter, collect the shells, and take the moon crest
- **Required items:** `shield_key`, `shotgun`
- **Items gained:** `shotgun_shells`, `moon_crest`
- **How to achieve:** Be in / reach **210**. Hold: `shield_key`, `shotgun`. Fight until the combat success condition clears. Gains: `shotgun_shells`, `moon_crest`.
- **Success condition:**
- **all of:**
  - Acquire item `shotgun_shells`
  - Acquire item `moon_crest`

