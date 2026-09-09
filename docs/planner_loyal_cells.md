# Planner-loyal cells (`plNN`)

Generated from [`data/planner_chunks/cp05_shield_key.json`](../data/planner_chunks/cp05_shield_key.json) (129 authored steps after the lockpick tip). Room names in parentheses come from [`data/rooms.json`](../data/rooms.json).

**C-RE1 numbering (Sep 2026):** `pl00` is the dining fresh start (no emblem). Opening remint is `pl01` emblem … `pl06` lockpick (`opening_to_lockpick.json`). Live shield-key step 0 (`106->105`) mints `pl07`. Watch `states/planner_loyal/cells/plNN/cell.pst` — BizHawk `cell.State` is backed up under `backups/planner_loyal_bizhawk_20260904/`.

On step success the fleet installs `states/planner_loyal/cells/plNN/` for the completed index.

- Slot formula: capturing steps only — `capture:false` (Richard) does not consume a `plNN`. After `pl86` (`204->20D`), next mint is `pl87` (`204->207`).
- Training starts: every minted `pl06+` plus `pl00` when it has `cell.pst` (pin file `data/planner_loyal_reset_pin.env`; blank = uniform over loadable C-RE1 cells, **not** pinned to `pl00`).
- After reset from a cell, the live step is `planner_step_index + 1` (or first chunk step from `pl06`).
- `wrong_traverse:A->B got C` means the **wanted** hop was `A->B`; they entered `C` instead (−4 divert). Completing `A->B` mints the cell and does **not** log `wrong_traverse`.
- Tea-room lock: `104->103` stays locked until `103->104` is done once (this chunk never opens it). `103->10C` / `103->10D` are open. Do not walk `116->106` after the shotgun. Vacant `102` clip+shells are taken on the armor-key return; skip re-loot.
- Chunk end-anchor: `place_wind_crest` (`pl134`). Mid-chunk success keeps the episode open.

## What's minted / what it's stuck on

Live C-RE1 `cell.pst` on this machine: `pl00` (fresh_start_105), `pl01` (opening_to_lockpick_step01), `pl02` (opening_to_lockpick_step02), `pl03` (opening_to_lockpick_step03), `pl04` (opening_to_lockpick_step04), `pl05` (opening_to_lockpick_step05), `pl06` (opening_to_lockpick_step06), `pl07` (cp05_shield_key_step01), `pl08` (cp05_shield_key_step02), `pl09` (cp05_shield_key_step03), `pl10` (cp05_shield_key_step04), `pl100` (cp05_shield_key_step95), `pl101` (cp05_shield_key_step96), `pl102` (cp05_shield_key_step97), `pl103` (cp05_shield_key_step98), `pl104` (cp05_shield_key_step99), `pl105` (cp05_shield_key_step100), `pl106` (cp05_shield_key_step101), `pl107` (cp05_shield_key_step102), `pl108` (cp05_shield_key_step103), `pl109` (cp05_shield_key_step104), `pl11` (cp05_shield_key_step05), `pl110` (cp05_shield_key_step105), `pl111` (cp05_shield_key_step106), `pl112` (cp05_shield_key_step107), `pl113` (cp05_shield_key_step108), `pl114` (cp05_shield_key_step109), `pl115` (cp05_shield_key_step110), `pl116` (cp05_shield_key_step111), `pl117` (cp05_shield_key_step112), `pl118` (cp05_shield_key_step113), `pl119` (cp05_shield_key_step114), `pl12` (cp05_shield_key_step06), `pl120` (cp05_shield_key_step115), `pl121` (cp05_shield_key_step116), `pl122` (cp05_shield_key_step117), `pl123` (cp05_shield_key_step118), `pl124` (cp05_shield_key_step119), `pl125` (cp05_shield_key_step120), `pl126` (cp05_shield_key_step121), `pl127` (cp05_shield_key_step122), `pl128` (cp05_shield_key_step123), `pl129` (cp05_shield_key_step124), `pl13` (cp05_shield_key_step07), `pl130` (cp05_shield_key_step125), `pl131` (cp05_shield_key_step126), `pl132` (cp05_shield_key_step127), `pl133` (cp05_shield_key_step128), `pl134` (cp05_shield_key_step129), `pl14` (cp05_shield_key_step08), `pl15` (cp05_shield_key_step09), `pl16` (cp05_shield_key_step10), `pl17` (cp05_shield_key_step11), `pl18` (cp05_shield_key_step12), `pl19` (cp05_shield_key_step13), `pl20` (cp05_shield_key_step14), `pl21` (cp05_shield_key_step15), `pl22` (cp05_shield_key_step16), `pl23` (cp05_shield_key_step17), `pl24` (cp05_shield_key_step18), `pl25` (cp05_shield_key_step19), `pl26` (cp05_shield_key_step20), `pl27` (cp05_shield_key_step21), `pl28` (cp05_shield_key_step22), `pl29` (cp05_shield_key_step23), `pl30` (cp05_shield_key_step24), `pl31` (cp05_shield_key_step25), `pl32` (cp05_shield_key_step26), `pl33` (cp05_shield_key_step27), `pl34` (cp05_shield_key_step28), `pl35` (cp05_shield_key_step29), `pl36` (cp05_shield_key_step30), `pl37` (cp05_shield_key_step31), `pl38` (cp05_shield_key_step32), `pl39` (cp05_shield_key_step33), `pl40` (cp05_shield_key_step34), `pl41` (cp05_shield_key_step35), `pl42` (cp05_shield_key_step36), `pl43` (cp05_shield_key_step37), `pl44` (cp05_shield_key_step38), `pl45` (cp05_shield_key_step39), `pl46` (cp05_shield_key_step40), `pl47` (cp05_shield_key_step41), `pl48` (cp05_shield_key_step42), `pl49` (cp05_shield_key_step43), `pl50` (cp05_shield_key_step44), `pl51` (cp05_shield_key_step45), `pl52` (cp05_shield_key_step46), `pl53` (cp05_shield_key_step47), `pl54` (cp05_shield_key_step48), `pl55` (cp05_shield_key_step49), `pl56` (cp05_shield_key_step50), `pl57` (cp05_shield_key_step51), `pl58` (cp05_shield_key_step52), `pl59` (cp05_shield_key_step53), `pl60` (cp05_shield_key_step54), `pl61` (cp05_shield_key_step55), `pl62` (cp05_shield_key_step56), `pl63` (cp05_shield_key_step57), `pl64` (cp05_shield_key_step58), `pl65` (cp05_shield_key_step59), `pl66` (cp05_shield_key_step60), `pl67` (cp05_shield_key_step61), `pl68` (cp05_shield_key_step62), `pl69` (cp05_shield_key_step63), `pl70` (cp05_shield_key_step64), `pl71` (cp05_shield_key_step65), `pl72` (cp05_shield_key_step66), `pl73` (cp05_shield_key_step67), `pl74` (cp05_shield_key_step68), `pl75` (cp05_shield_key_step69), `pl76` (cp05_shield_key_step70), `pl77` (cp05_shield_key_step71), `pl78` (cp05_shield_key_step72), `pl79` (cp05_shield_key_step73), `pl80` (cp05_shield_key_step74), `pl81` (cp05_shield_key_step75), `pl82` (cp05_shield_key_step76), `pl83` (cp05_shield_key_step77), `pl84` (cp05_shield_key_step78), `pl85` (cp05_shield_key_step79), `pl86` (cp05_shield_key_step80), `pl87` (cp05_shield_key_step81), `pl88` (cp05_shield_key_step83), `pl89` (cp05_shield_key_step84), `pl90` (cp05_shield_key_step85), `pl91` (cp05_shield_key_step86), `pl92` (cp05_shield_key_step87), `pl93` (cp05_shield_key_step88), `pl94` (cp05_shield_key_step89), `pl95` (cp05_shield_key_step90), `pl96` (cp05_shield_key_step91), `pl97` (cp05_shield_key_step92), `pl98` (cp05_shield_key_step93), `pl99` (cp05_shield_key_step94).
Highest minted is `pl99`. The fleet is **stuck trying to mint `pl100`** (see the summary row for that slot — that is the current objective).

Logs (pking): `reset tip=` is the start cell; `queue_seek ... want=` is the step they must finish; `fail='planner_divert' target=` / `divert=` is why they died before minting. `minted pl` / `reject quality pl` is a completed cell.

```powershell
Get-ChildItem D:\re1_rl\states\planner_loyal\cells -Directory | % Name
findstr /C:"[planner_loyal] minted pl" /C:"reset tip=" /C:"queue_seek" /C:"planner_divert" D:\re1_rl\data\logs\worker_pking-recomp.log
```

## Summary table

| Cell | Step n | Checkpoint ID | Room | Op | Objective |
|------|--------|---------------|------|----|-----------|
| `pl00` | seed | `fresh_start_105` | `105` (DINING ROOM) | tip | Dining fresh start, no emblem (installed, never minted) |
| `pl01` | seed | `emblem_105` | `105` (DINING ROOM) | acquire | Pick up the wooden emblem |
| `pl02` | seed | `kenneth_104` | `104` (TEA ROOM) | traverse | Enter the Tea Room (Kenneth) |
| `pl03` | seed | `barry_return_105` | `105` (DINING ROOM) | traverse | Return to Dining after Kenneth |
| `pl04` | seed | `main_hall_106` | `106` (MAIN HALL) | traverse | Reach Main Hall after Kenneth |
| `pl05` | seed | `upper_hall_203` | `203` (HALL 2F) | traverse | Climb to Main Hall 2F |
| `pl06` | seed | `barry_hall_return_106` | `106` (MAIN HALL) | traverse | Return from 203 to Main Hall (lockpick tip) (training tip) |
| `pl07` | 1 | `106->105` | `105` (DINING ROOM) | traverse | Walk `106->105` into `105` (DINING ROOM) |
| `pl08` | 2 | `105->104` | `104` (TEA ROOM) | traverse | Walk `105->104` into `104` (TEA ROOM) |
| `pl09` | 3 | `104:handgun_bullets:1` | `104` (TEA ROOM) | acquire | Take `104:handgun_bullets:1` |
| `pl10` | 4 | `104:handgun_bullets:2` | `104` (TEA ROOM) | acquire | Take `104:handgun_bullets:2` |
| `pl11` | 5 | `104->10F` | `10F` (BAR) | traverse | Walk `104->10F` into `10F` (BAR) |
| `pl12` | 6 | `music_notes` | `10F` (BAR) | acquire | Take `10F:music_notes:1` |
| `pl13` | 7 | `piano_play` | `10F` (BAR) | objective | `piano_play` at `music_notes@10F_piano` |
| `pl14` | 8 | `gold_emblem` | `10F` (BAR) | acquire | Take `10F:gold_emblem:2` |
| `pl15` | 9 | `emblem_swap_alcove` | `10F` (BAR) | objective | `emblem_swap_alcove` at `emblem@10F_alcove` |
| `pl16` | 10 | `10F->104` | `104` (TEA ROOM) | traverse | Walk `10F->104` into `104` (TEA ROOM) |
| `pl17` | 11 | `104->105` | `105` (DINING ROOM) | traverse | Walk `104->105` into `105` (DINING ROOM) |
| `pl18` | 12 | `gold_emblem_fireplace` | `105` (DINING ROOM) | objective | `gold_emblem_fireplace` at `gold_emblem@105_fireplace` |
| `pl19` | 13 | `shield_key` | `105` (DINING ROOM) | acquire | Take `105:shield_key:2` |
| `pl20` | 14 | `105->106` | `106` (MAIN HALL) | traverse | Walk `105->106` into `106` (MAIN HALL) |
| `pl21` | 15 | `106->107` | `107` (GALLERY) | traverse | Walk `106->107` into `107` (GALLERY) |
| `pl22` | 16 | `107->108` | `108` (L PASSAGE) | traverse | Walk `107->108` into `108` (L PASSAGE) |
| `pl23` | 17 | `108:handgun_bullets:1` | `108` (L PASSAGE) | acquire | Take `108:handgun_bullets:1` |
| `pl24` | 18 | `108->109` | `109` (TRAP PASSAGE) | traverse | Walk `108->109` into `109` (TRAP PASSAGE) |
| `pl25` | 19 | `109:green_herb:1` | `109` (TRAP PASSAGE) | acquire | Take `109:green_herb:1` |
| `pl26` | 20 | `109->10A` | `10A` (BACK PASSAGE) | traverse | Walk `109->10A` into `10A` (BACK PASSAGE) |
| `pl27` | 21 | `10A->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `10A->10B` into `10B` (1F RIGHT STAIRS) |
| `pl28` | 22 | `10B:green_herb:1` | `10B` (1F RIGHT STAIRS) | acquire | Take `10B:green_herb:1` |
| `pl29` | 23 | `10B->118` | `118` (STAIRS UNDER ROOM) | traverse | Walk `10B->118` into `118` (STAIRS UNDER ROOM) |
| `pl30` | 24 | `chemical` | `118` (STAIRS UNDER ROOM) | acquire | Take `118:chemical:1` |
| `pl31` | 25 | `use_box` | `118` (STAIRS UNDER ROOM) | use_box | Rearrange the 118 box to the leave_118 loadout, then close the box |
| `pl32` | 26 | `118->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `118->10B` into `10B` (1F RIGHT STAIRS) |
| `pl33` | 27 | `10B->10A` | `10A` (BACK PASSAGE) | traverse | Walk `10B->10A` into `10A` (BACK PASSAGE) |
| `pl34` | 28 | `10A->109` | `109` (TRAP PASSAGE) | traverse | Walk `10A->109` into `109` (TRAP PASSAGE) |
| `pl35` | 29 | `109->115` | `115` (TRAP ROOM) | traverse | Walk `109->115` into `115` (TRAP ROOM) |
| `pl36` | 30 | `115->116` | `116` (LIVING ROOM) | traverse | Walk `115->116` into `116` (LIVING ROOM) |
| `pl37` | 31 | `116:shotgun:1` | `116` (LIVING ROOM) | acquire | Take `116:shotgun:1` |
| `pl38` | 32 | `116->115` | `115` (TRAP ROOM) | traverse | Walk `116->115` into `115` (TRAP ROOM) |
| `pl39` | 33 | `115->109` | `109` (TRAP PASSAGE) | traverse | Walk `115->109` into `109` (TRAP PASSAGE) |
| `pl40` | 34 | `109->10A` | `10A` (BACK PASSAGE) | traverse | Walk `109->10A` into `10A` (BACK PASSAGE) |
| `pl41` | 35 | `gallery_enter` | `117` (LARGE GALLERY) | traverse | Walk `10A->117` into `117` (LARGE GALLERY) |
| `pl42` | 36 | `gallery_portrait_1` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_1` at `gallery_portrait_1` — newborn |
| `pl43` | 37 | `gallery_portrait_2` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_2` at `gallery_portrait_2` — infant |
| `pl44` | 38 | `gallery_portrait_3` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_3` at `gallery_portrait_3` — boy |
| `pl45` | 39 | `gallery_portrait_4` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_4` at `gallery_portrait_4` — young man |
| `pl46` | 40 | `gallery_portrait_5` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_5` at `gallery_portrait_5` — middle-aged man |
| `pl47` | 41 | `gallery_portrait_6` | `117` (LARGE GALLERY) | do_puzzle | `gallery_portrait_6` at `gallery_portrait_6` — old man |
| `pl48` | 42 | `gallery_end_of_life` | `117` (LARGE GALLERY) | do_puzzle | `gallery_end_of_life` at `gallery_end_of_life` — death painting / slot 8; spawns star crest |
| `pl49` | 43 | `star_crest` | `117` (LARGE GALLERY) | acquire | Take `117:star_crest:1` |
| `pl50` | 44 | `117->10A` | `10A` (BACK PASSAGE) | traverse | Walk `117->10A` into `10A` (BACK PASSAGE) |
| `pl51` | 45 | `10A->11A` | `11A` (ROOFED PASSAGE) | traverse | Walk `10A->11A` into `11A` (ROOFED PASSAGE) |
| `pl52` | 46 | `place_star_crest` | `11A` (ROOFED PASSAGE) | objective | `place_star_crest` at `star_crest@11A_crest_slot` |
| `pl53` | 47 | `11A->10A` | `10A` (BACK PASSAGE) | traverse | Walk `11A->10A` into `10A` (BACK PASSAGE) |
| `pl54` | 48 | `10A->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `10A->10B` into `10B` (1F RIGHT STAIRS) |
| `pl55` | 49 | `10B->207` | `207` (2F RIGHT STAIRS) | traverse | Walk `10B->207` into `207` (2F RIGHT STAIRS) |
| `pl56` | 50 | `207->204` | `204` (C PASSAGE) | traverse | Walk `207->204` into `204` (C PASSAGE) |
| `pl57` | 51 | `204->203` | `203` (HALL 2F) | traverse | Walk `204->203` into `203` (HALL 2F) |
| `pl58` | 52 | `203->202` | `202` (DINING ROOM 2F) | traverse | Walk `203->202` into `202` (DINING ROOM 2F) |
| `pl59` | 53 | `202->201` | `201` (2F LEFT STAIRS) | traverse | Walk `202->201` into `201` (2F LEFT STAIRS) |
| `pl60` | 54 | `201->101` | `101` (1F LEFT STAIRS) | traverse | Walk `201->101` into `101` (1F LEFT STAIRS) |
| `pl61` | 55 | `101->103` | `103` (F PASSAGE) | traverse | Walk `101->103` into `103` (F PASSAGE) |
| `pl62` | 56 | `103->10C` | `10C` (GREEN HOUSE) | traverse | Walk `103->10C` into `10C` (GREEN HOUSE) |
| `pl63` | 57 | `greenhouse_pump` | `10C` (GREEN HOUSE) | objective | `greenhouse_pump` at `chemical@10C_greenhouse_pump` |
| `pl64` | 58 | `armor_key` | `10C` (GREEN HOUSE) | acquire | Take `10C:armor_key:1` |
| `pl65` | 59 | `10C:red_herb:3a` | `10C` (GREEN HOUSE) | acquire | Take `10C:red_herb:3a` (bench red 1/2) |
| `pl66` | 60 | `10C:green_herb:2a` | `10C` (GREEN HOUSE) | acquire | Take `10C:green_herb:2a` (bench green 1/2) |
| `pl67` | 61 | `10C:red_herb:3b` | `10C` (GREEN HOUSE) | acquire | Take `10C:red_herb:3b` (bench red 2/2) |
| `pl68` | 62 | `10C:green_herb:2b` | `10C` (GREEN HOUSE) | acquire | Take `10C:green_herb:2b` (bench green 2/2; 3rd green/red = divert) |
| `pl69` | 63 | `10C->103` | `103` (F PASSAGE) | traverse | Walk `10C->103` into `103` (F PASSAGE) |
| `pl70` | 64 | `103->101` | `101` (1F LEFT STAIRS) | traverse | Walk `103->101` into `101` (1F LEFT STAIRS) |
| `pl71` | 65 | `101->102` | `102` (VACANT ROOM) | traverse | Walk `101->102` into `102` (VACANT ROOM) |
| `pl72` | 66 | `102:handgun_bullets:1` | `102` (VACANT ROOM) | acquire | Take `102:handgun_bullets:1` |
| `pl73` | 67 | `102:shotgun_shells:2` | `102` (VACANT ROOM) | acquire | Take `102:shotgun_shells:2` |
| `pl74` | 68 | `102->101` | `101` (1F LEFT STAIRS) | traverse | Walk `102->101` into `101` (1F LEFT STAIRS) |
| `pl75` | 69 | `101->100` | `100` (SAVE ROOM) | traverse | Walk `101->100` into `100` (SAVE ROOM) |
| `pl76` | 70 | `use_box` | `100` (SAVE ROOM) | use_box | Rearrange the 100 box to the leave_100 loadout, then close the box |
| `pl77` | 71 | `100->101` | `101` (1F LEFT STAIRS) | traverse | Walk `100->101` into `101` (1F LEFT STAIRS) |
| `pl78` | 72 | `101->201` | `201` (2F LEFT STAIRS) | traverse | Walk `101->201` into `201` (2F LEFT STAIRS) |
| `pl79` | 73 | `201->202` | `202` (DINING ROOM 2F) | traverse | Walk `201->202` into `202` (DINING ROOM 2F) |
| `pl80` | 74 | `202->203` | `203` (HALL 2F) | traverse | Walk `202->203` into `203` (HALL 2F) |
| `pl81` | 75 | `203->204` | `204` (C PASSAGE) | traverse | Walk `203->204` into `204` (C PASSAGE) |
| `pl82` | 76 | `armor_room_enter` | `205` (ARMOR ROOM) | traverse | Walk `204->205` into `205` (ARMOR ROOM) |
| `pl83` | 77 | `armor_vent_door` | `205` (ARMOR ROOM) | do_puzzle | `armor_vent_door` at `armor_vent_door` — east statue exactly on its vent |
| `pl84` | 78 | `armor_vent_far` | `205` (ARMOR ROOM) | do_puzzle | `armor_vent_far` at `armor_vent_far` — both east and west statues exactly on their vents |
| `pl85` | 79 | `sun_crest` | `205` (ARMOR ROOM) | acquire | Take `205:sun_crest:1` (activate center button, then take cabinet sun crest) |
| `pl86` | 80 | `205->204` | `204` (C PASSAGE) | traverse | Walk `205->204` into `204` (C PASSAGE) |
| `pl87` | 81 | `richard_approach` | `20D` (PILLAR PASSAGE) | traverse | Walk `204->20D` into `20D` (PILLAR PASSAGE) |
| _(none)_ | 82 | `richard_bleedout` | `20D` (PILLAR PASSAGE) | trigger_cutscene | `richard_bleedout` at `20D:richard` — start ~6 min Richard timer; cinema dumps to 204 — no cell mint |
| `pl88` | 83 | `204->207` | `207` (2F RIGHT STAIRS) | traverse | Walk `204->207` into `207` (2F RIGHT STAIRS) |
| `pl89` | 84 | `207->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `207->10B` into `10B` (1F RIGHT STAIRS) |
| `pl90` | 85 | `10B->10A` | `10A` (BACK PASSAGE) | traverse | Walk `10B->10A` into `10A` (BACK PASSAGE) |
| `pl91` | 86 | `10A->11A` | `11A` (ROOFED PASSAGE) | traverse | Walk `10A->11A` into `11A` (ROOFED PASSAGE) |
| `pl92` | 87 | `place_sun_crest` | `11A` (ROOFED PASSAGE) | objective | `place_sun_crest` at `sun_crest@11A_crest_slot` — place held sun_crest; burns Richard timer |
| `pl93` | 88 | `11A->10A` | `10A` (BACK PASSAGE) | traverse | Walk `11A->10A` into `10A` (BACK PASSAGE) |
| `pl94` | 89 | `10A->10B` | `10B` (1F RIGHT STAIRS) | traverse | Walk `10A->10B` into `10B` (1F RIGHT STAIRS) |
| `pl95` | 90 | `10B->207` | `207` (2F RIGHT STAIRS) | traverse | Walk `10B->207` into `207` (2F RIGHT STAIRS) |
| `pl96` | 91 | `207->204` | `204` (C PASSAGE) | traverse | Walk `207->204` into `204` (C PASSAGE) |
| `pl97` | 92 | `204->203` | `203` (HALL 2F) | traverse | Walk `204->203` into `203` (HALL 2F) |
| `pl98` | 93 | `dining_2f_enter` | `202` (DINING ROOM 2F) | traverse | Walk `203->202` into `202` (DINING ROOM 2F) |
| `pl99` | 94 | `push_statue_2f` | `202` (DINING ROOM 2F) | do_puzzle | `push_statue_2f` at `dining_statue_knocked` — push balcony statue down; blue jewel drops to dining hall 105 |
| `pl100` | 95 | `202->203` | `203` (HALL 2F) | traverse | Walk `202->203` into `203` (HALL 2F) |
| `pl101` | 96 | `203->106` | `106` (MAIN HALL) | traverse | Walk `203->106` into `106` (MAIN HALL) |
| `pl102` | 97 | `106->105` | `105` (DINING ROOM) | traverse | Walk `106->105` into `105` (DINING ROOM) |
| `pl103` | 98 | `blue_jewel` | `105` (DINING ROOM) | acquire | Take `105:blue_jewel:1` (statue drop puts jewel in dining hall 105 (not 202)) |
| `pl104` | 99 | `105->106` | `106` (MAIN HALL) | traverse | Walk `105->106` into `106` (MAIN HALL) |
| `pl105` | 100 | `106->203` | `203` (HALL 2F) | traverse | Walk `106->203` into `203` (HALL 2F) |
| `pl106` | 101 | `203->202` | `202` (DINING ROOM 2F) | traverse | Walk `203->202` into `202` (DINING ROOM 2F) |
| `pl107` | 102 | `202->201` | `201` (2F LEFT STAIRS) | traverse | Walk `202->201` into `201` (2F LEFT STAIRS) |
| `pl108` | 103 | `201->101` | `101` (1F LEFT STAIRS) | traverse | Walk `201->101` into `101` (1F LEFT STAIRS) |
| `pl109` | 104 | `101->103` | `103` (F PASSAGE) | traverse | Walk `101->103` into `103` (F PASSAGE) |
| `pl110` | 105 | `tiger_room_enter` | `10D` (TIGER STATUE ROOM) | traverse | Walk `103->10D` into `10D` (TIGER STATUE ROOM) |
| `pl111` | 106 | `tiger_jewel` | `10D` (TIGER STATUE ROOM) | objective | `tiger_jewel` at `blue_jewel@10D_tiger_eye` — insert blue jewel in tiger eye |
| `pl112` | 107 | `wind_crest` | `10D` (TIGER STATUE ROOM) | acquire | Take `10D:wind_crest:1` (acquire wind crest; continue to place_wind resource tail) |
| `pl113` | 108 | `10D->103` | `103` (F PASSAGE) | traverse | Walk `10D->103` into `103` (F PASSAGE) |
| `pl114` | 109 | `103->10E` | `10E` (EMPLOYEE ROOM) | traverse | Walk `103->10E` into `10E` (EMPLOYEE ROOM) |
| `pl115` | 110 | `10E:handgun_bullets:1` | `10E` (EMPLOYEE ROOM) | acquire | Take `10E:handgun_bullets:1` (on bed) |
| `pl116` | 111 | `10E:shotgun_shells:2` | `10E` (EMPLOYEE ROOM) | acquire | Take `10E:shotgun_shells:2` (in closet) |
| `pl117` | 112 | `10E->103` | `103` (F PASSAGE) | traverse | Walk `10E->103` into `103` (F PASSAGE) |
| `pl118` | 113 | `tea_unlock_103_104` | `104` (TEA ROOM) | traverse | Walk `103->104` into `104` (TEA ROOM) |
| `pl119` | 114 | `104->105` | `105` (DINING ROOM) | traverse | Walk `104->105` into `105` (DINING ROOM) |
| `pl120` | 115 | `105->106` | `106` (MAIN HALL) | traverse | Walk `105->106` into `106` (MAIN HALL) |
| `pl121` | 116 | `dressing_room_enter` | `111` (DRESSING ROOM) | traverse | Walk `106->111` into `111` (DRESSING ROOM) |
| `pl122` | 117 | `111:handgun_bullets:1` | `111` (DRESSING ROOM) | acquire | Take `111:handgun_bullets:1` (shelf clip) |
| `pl123` | 118 | `111:shotgun_shells:2` | `111` (DRESSING ROOM) | acquire | Take `111:shotgun_shells:2` (locked desk; one shell clip) |
| `pl124` | 119 | `111->112` | `112` (WARDROBE) | traverse | Walk `111->112` into `112` (WARDROBE) |
| `pl125` | 120 | `112:green_herb:1` | `112` (WARDROBE) | acquire | Take `112:green_herb:1` (SE corner plant 1/2) |
| `pl126` | 121 | `112:green_herb:2` | `112` (WARDROBE) | acquire | Take `112:green_herb:2` (SE corner plant 2/2) |
| `pl127` | 122 | `112->111` | `111` (DRESSING ROOM) | traverse | Walk `112->111` into `111` (DRESSING ROOM) |
| `pl128` | 123 | `111->106` | `106` (MAIN HALL) | traverse | Walk `111->106` into `106` (MAIN HALL) |
| `pl129` | 124 | `106->107` | `107` (GALLERY) | traverse | Walk `106->107` into `107` (GALLERY) |
| `pl130` | 125 | `107->108` | `108` (L PASSAGE) | traverse | Walk `107->108` into `108` (L PASSAGE) |
| `pl131` | 126 | `108->109` | `109` (TRAP PASSAGE) | traverse | Walk `108->109` into `109` (TRAP PASSAGE) |
| `pl132` | 127 | `109->10A` | `10A` (BACK PASSAGE) | traverse | Walk `109->10A` into `10A` (BACK PASSAGE) |
| `pl133` | 128 | `10A->11A` | `11A` (ROOFED PASSAGE) | traverse | Walk `10A->11A` into `11A` (ROOFED PASSAGE) |
| `pl134` | 129 | `place_wind_crest` | `11A` (ROOFED PASSAGE) | objective | `place_wind_crest` at `wind_crest@11A_crest_slot` — chunk end-anchor |

## Details

### Seed cells (not from this chunk)

### `pl00` — `fresh_start_105` (seed)

- **Room:** `105` (DINING ROOM)
- **Op:** `tip`
- **Objective:** Dining fresh start, no emblem (installed, never minted)
- **Items gained:** _(none)_

### `pl01` — `emblem_105` (seed)

- **Room:** `105` (DINING ROOM)
- **Op:** `acquire`
- **Objective:** Pick up the wooden emblem
- **Items gained:** `emblem`
- **Success:** acquire `emblem` in `105`

### `pl02` — `kenneth_104` (seed)

- **Room:** `104` (TEA ROOM)
- **Op:** `traverse`
- **Objective:** Enter the Tea Room (Kenneth)
- **Items gained:** _(none)_
- **Success:** enter `104` via `105->104`

### `pl03` — `barry_return_105` (seed)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Objective:** Return to Dining after Kenneth
- **Items gained:** _(none)_
- **Success:** enter `105` via `104->105`

### `pl04` — `main_hall_106` (seed)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Objective:** Reach Main Hall after Kenneth
- **Items gained:** _(none)_
- **Success:** enter `106` via `105->106`

### `pl05` — `upper_hall_203` (seed)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Objective:** Climb to Main Hall 2F
- **Items gained:** _(none)_
- **Success:** enter `203` via `106->203`

### `pl06` — `barry_hall_return_106` (seed)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Objective:** Return from 203 to Main Hall (lockpick tip)
- **Items gained:** _(none)_
- **Success:** enter `106` via `203->106`

### Chunk cells (`pl07`–`pl134`)

### `pl07` — `106->105` (step 1)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Edge:** `106->105`
- **Objective:** Walk `106->105` into `105` (DINING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->105` into `105` (DINING ROOM).
- **Success condition:** Enter room `105` via `106->105` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->105 got <room>` (−4).

### `pl08` — `105->104` (step 2)

- **Room:** `104` (TEA ROOM)
- **Op:** `traverse`
- **Edge:** `105->104`
- **Objective:** Walk `105->104` into `104` (TEA ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `105->104` into `104` (TEA ROOM).
- **Success condition:** Enter room `104` via `105->104` (already-there counts after cinema dump). Any other door is `wrong_traverse:105->104 got <room>` (−4).

### `pl09` — `104:handgun_bullets:1` (step 3)

- **Room:** `104` (TEA ROOM)
- **Op:** `acquire`
- **Pickup:** `104:handgun_bullets:1`
- **Objective:** Take `104:handgun_bullets:1`
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `104:handgun_bullets:1`.
- **Success condition:** Inventory gains `104:handgun_bullets:1` while this step is current

### `pl10` — `104:handgun_bullets:2` (step 4)

- **Room:** `104` (TEA ROOM)
- **Op:** `acquire`
- **Pickup:** `104:handgun_bullets:2`
- **Objective:** Take `104:handgun_bullets:2`
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `104:handgun_bullets:2`.
- **Success condition:** Inventory gains `104:handgun_bullets:2` while this step is current

### `pl11` — `104->10F` (step 5)

- **Room:** `10F` (BAR)
- **Op:** `traverse`
- **Edge:** `104->10F`
- **Objective:** Walk `104->10F` into `10F` (BAR)
- **Items gained:** _(none)_
- **How to achieve:** Walk `104->10F` into `10F` (BAR).
- **Success condition:** Enter room `10F` via `104->10F` (already-there counts after cinema dump). Any other door is `wrong_traverse:104->10F got <room>` (−4).

### `pl12` — `music_notes` (step 6)

- **Room:** `10F` (BAR)
- **Op:** `acquire`
- **Pickup:** `10F:music_notes:1`
- **Beat:** `music_notes`
- **Objective:** Take `10F:music_notes:1`
- **Items gained:** `music_notes`
- **How to achieve:** Take `10F:music_notes:1`.
- **Success condition:** Inventory gains `10F:music_notes:1` while this step is current

### `pl13` — `piano_play` (step 7)

- **Room:** `10F` (BAR)
- **Op:** `objective`
- **Site:** `music_notes@10F_piano`
- **Beat:** `piano_play`
- **Objective:** `piano_play` at `music_notes@10F_piano`
- **Items gained:** _(none)_
- **How to achieve:** `piano_play` at `music_notes@10F_piano`.
- **Success condition:** `story_use_success` == `music_notes@10F_piano` in room `10F`

### `pl14` — `gold_emblem` (step 8)

- **Room:** `10F` (BAR)
- **Op:** `acquire`
- **Pickup:** `10F:gold_emblem:2`
- **Beat:** `gold_emblem`
- **Objective:** Take `10F:gold_emblem:2`
- **Items gained:** `gold_emblem`
- **How to achieve:** Take `10F:gold_emblem:2`.
- **Success condition:** Inventory gains `10F:gold_emblem:2` while this step is current

### `pl15` — `emblem_swap_alcove` (step 9)

- **Room:** `10F` (BAR)
- **Op:** `objective`
- **Site:** `emblem@10F_alcove`
- **Beat:** `emblem_swap_alcove`
- **Objective:** `emblem_swap_alcove` at `emblem@10F_alcove`
- **Items gained:** _(none)_
- **How to achieve:** `emblem_swap_alcove` at `emblem@10F_alcove`.
- **Success condition:** `story_use_success` == `emblem@10F_alcove` in room `10F`

### `pl16` — `10F->104` (step 10)

- **Room:** `104` (TEA ROOM)
- **Op:** `traverse`
- **Edge:** `10F->104`
- **Objective:** Walk `10F->104` into `104` (TEA ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10F->104` into `104` (TEA ROOM).
- **Success condition:** Enter room `104` via `10F->104` (already-there counts after cinema dump). Any other door is `wrong_traverse:10F->104 got <room>` (−4).

### `pl17` — `104->105` (step 11)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Edge:** `104->105`
- **Objective:** Walk `104->105` into `105` (DINING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `104->105` into `105` (DINING ROOM).
- **Success condition:** Enter room `105` via `104->105` (already-there counts after cinema dump). Any other door is `wrong_traverse:104->105 got <room>` (−4).

### `pl18` — `gold_emblem_fireplace` (step 12)

- **Room:** `105` (DINING ROOM)
- **Op:** `objective`
- **Site:** `gold_emblem@105_fireplace`
- **Beat:** `gold_emblem_fireplace`
- **Objective:** `gold_emblem_fireplace` at `gold_emblem@105_fireplace`
- **Items gained:** _(none)_
- **How to achieve:** `gold_emblem_fireplace` at `gold_emblem@105_fireplace`.
- **Success condition:** `story_use_success` == `gold_emblem@105_fireplace` in room `105`

### `pl19` — `shield_key` (step 13)

- **Room:** `105` (DINING ROOM)
- **Op:** `acquire`
- **Pickup:** `105:shield_key:2`
- **Beat:** `shield_key`
- **Objective:** Take `105:shield_key:2`
- **Items gained:** `shield_key`
- **How to achieve:** Take `105:shield_key:2`.
- **Success condition:** Inventory gains `105:shield_key:2` while this step is current

### `pl20` — `105->106` (step 14)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `105->106`
- **Objective:** Walk `105->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `105->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `105->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:105->106 got <room>` (−4).

### `pl21` — `106->107` (step 15)

- **Room:** `107` (GALLERY)
- **Op:** `traverse`
- **Edge:** `106->107`
- **Note:** art room / gallery circuit toward 11A
- **Objective:** Walk `106->107` into `107` (GALLERY)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->107` into `107` (GALLERY).
- **Success condition:** Enter room `107` via `106->107` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->107 got <room>` (−4).

### `pl22` — `107->108` (step 16)

- **Room:** `108` (L PASSAGE)
- **Op:** `traverse`
- **Edge:** `107->108`
- **Objective:** Walk `107->108` into `108` (L PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `107->108` into `108` (L PASSAGE).
- **Success condition:** Enter room `108` via `107->108` (already-there counts after cinema dump). Any other door is `wrong_traverse:107->108 got <room>` (−4).

### `pl23` — `108:handgun_bullets:1` (step 17)

- **Room:** `108` (L PASSAGE)
- **Op:** `acquire`
- **Pickup:** `108:handgun_bullets:1`
- **Objective:** Take `108:handgun_bullets:1`
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `108:handgun_bullets:1`.
- **Success condition:** Inventory gains `108:handgun_bullets:1` while this step is current

### `pl24` — `108->109` (step 18)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `traverse`
- **Edge:** `108->109`
- **Objective:** Walk `108->109` into `109` (TRAP PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `108->109` into `109` (TRAP PASSAGE).
- **Success condition:** Enter room `109` via `108->109` (already-there counts after cinema dump). Any other door is `wrong_traverse:108->109 got <room>` (−4).

### `pl25` — `109:green_herb:1` (step 19)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `acquire`
- **Pickup:** `109:green_herb:1`
- **Objective:** Take `109:green_herb:1`
- **Items gained:** `green_herb`
- **How to achieve:** Take `109:green_herb:1`.
- **Success condition:** Inventory gains `109:green_herb:1` while this step is current

### `pl26` — `109->10A` (step 20)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `109->10A`
- **Objective:** Walk `109->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `109->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `109->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:109->10A got <room>` (−4).

### `pl27` — `10A->10B` (step 21)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10A->10B`
- **Objective:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `10A->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->10B got <room>` (−4).

### `pl28` — `10B:green_herb:1` (step 22)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `acquire`
- **Pickup:** `10B:green_herb:1`
- **Objective:** Take `10B:green_herb:1`
- **Items gained:** `green_herb`
- **How to achieve:** Take `10B:green_herb:1`.
- **Success condition:** Inventory gains `10B:green_herb:1` while this step is current

### `pl29` — `10B->118` (step 23)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Op:** `traverse`
- **Edge:** `10B->118`
- **Objective:** Walk `10B->118` into `118` (STAIRS UNDER ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->118` into `118` (STAIRS UNDER ROOM).
- **Success condition:** Enter room `118` via `10B->118` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->118 got <room>` (−4).

### `pl30` — `chemical` (step 24)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Op:** `acquire`
- **Pickup:** `118:chemical:1`
- **Beat:** `chemical`
- **Objective:** Take `118:chemical:1`
- **Items gained:** `chemical`
- **How to achieve:** Take `118:chemical:1`.
- **Success condition:** Inventory gains `118:chemical:1` while this step is current

### `pl31` — `use_box` (step 25)

- **Room:** `118` (STAIRS UNDER ROOM)
- **Op:** `use_box`
- **Objective:** Rearrange the 118 box to the leave_118 loadout, then close the box
- **Items gained:** _(none)_
- **How to achieve:** Rearrange the 118 box to the leave_118 loadout, then close the box.
- **Success condition:** Box closes and inventory matches `leave_118.held_on_exit`

### `pl32` — `118->10B` (step 26)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `118->10B`
- **Objective:** Walk `118->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `118->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `118->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:118->10B got <room>` (−4).

### `pl33` — `10B->10A` (step 27)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `10B->10A`
- **Objective:** Walk `10B->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `10B->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->10A got <room>` (−4).

### `pl34` — `10A->109` (step 28)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `traverse`
- **Edge:** `10A->109`
- **Objective:** Walk `10A->109` into `109` (TRAP PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->109` into `109` (TRAP PASSAGE).
- **Success condition:** Enter room `109` via `10A->109` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->109 got <room>` (−4).

### `pl35` — `109->115` (step 29)

- **Room:** `115` (TRAP ROOM)
- **Op:** `traverse`
- **Edge:** `109->115`
- **Objective:** Walk `109->115` into `115` (TRAP ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `109->115` into `115` (TRAP ROOM).
- **Success condition:** Enter room `115` via `109->115` (already-there counts after cinema dump). Any other door is `wrong_traverse:109->115 got <room>` (−4).

### `pl36` — `115->116` (step 30)

- **Room:** `116` (LIVING ROOM)
- **Op:** `traverse`
- **Edge:** `115->116`
- **Objective:** Walk `115->116` into `116` (LIVING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `115->116` into `116` (LIVING ROOM).
- **Success condition:** Enter room `116` via `115->116` (already-there counts after cinema dump). Any other door is `wrong_traverse:115->116 got <room>` (−4).

### `pl37` — `116:shotgun:1` (step 31)

- **Room:** `116` (LIVING ROOM)
- **Op:** `acquire`
- **Pickup:** `116:shotgun:1`
- **Objective:** Take `116:shotgun:1`
- **Items gained:** `shotgun`
- **How to achieve:** Take `116:shotgun:1`.
- **Success condition:** Inventory gains `116:shotgun:1` while this step is current

### `pl38` — `116->115` (step 32)

- **Room:** `115` (TRAP ROOM)
- **Op:** `traverse`
- **Edge:** `116->115`
- **Objective:** Walk `116->115` into `115` (TRAP ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `116->115` into `115` (TRAP ROOM).
- **Success condition:** Enter room `115` via `116->115` (already-there counts after cinema dump). Any other door is `wrong_traverse:116->115 got <room>` (−4).

### `pl39` — `115->109` (step 33)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `traverse`
- **Edge:** `115->109`
- **Objective:** Walk `115->109` into `109` (TRAP PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `115->109` into `109` (TRAP PASSAGE).
- **Success condition:** Enter room `109` via `115->109` (already-there counts after cinema dump). Any other door is `wrong_traverse:115->109 got <room>` (−4).

### `pl40` — `109->10A` (step 34)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `109->10A`
- **Objective:** Walk `109->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `109->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `109->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:109->10A got <room>` (−4).

### `pl41` — `gallery_enter` (step 35)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `traverse`
- **Edge:** `10A->117`
- **Beat:** `gallery_enter`
- **Objective:** Walk `10A->117` into `117` (LARGE GALLERY)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->117` into `117` (LARGE GALLERY).
- **Success condition:** Enter room `117` via `10A->117` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->117 got <room>` (−4).

### `pl42` — `gallery_portrait_1` (step 36)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_1`
- **Beat:** `gallery_portrait_1`
- **Note:** newborn
- **Objective:** `gallery_portrait_1` at `gallery_portrait_1` — newborn
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_1` at `gallery_portrait_1` — newborn.
- **Success condition:** Room `117` and gallery completed-steps >= 1

### `pl43` — `gallery_portrait_2` (step 37)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_2`
- **Beat:** `gallery_portrait_2`
- **Note:** infant
- **Objective:** `gallery_portrait_2` at `gallery_portrait_2` — infant
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_2` at `gallery_portrait_2` — infant.
- **Success condition:** Room `117` and gallery completed-steps >= 2

### `pl44` — `gallery_portrait_3` (step 38)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_3`
- **Beat:** `gallery_portrait_3`
- **Note:** boy
- **Objective:** `gallery_portrait_3` at `gallery_portrait_3` — boy
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_3` at `gallery_portrait_3` — boy.
- **Success condition:** Room `117` and gallery completed-steps >= 3

### `pl45` — `gallery_portrait_4` (step 39)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_4`
- **Beat:** `gallery_portrait_4`
- **Note:** young man
- **Objective:** `gallery_portrait_4` at `gallery_portrait_4` — young man
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_4` at `gallery_portrait_4` — young man.
- **Success condition:** Room `117` and gallery completed-steps >= 4

### `pl46` — `gallery_portrait_5` (step 40)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_5`
- **Beat:** `gallery_portrait_5`
- **Note:** middle-aged man
- **Objective:** `gallery_portrait_5` at `gallery_portrait_5` — middle-aged man
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_5` at `gallery_portrait_5` — middle-aged man.
- **Success condition:** Room `117` and gallery completed-steps >= 5

### `pl47` — `gallery_portrait_6` (step 41)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_portrait_6`
- **Beat:** `gallery_portrait_6`
- **Note:** old man
- **Objective:** `gallery_portrait_6` at `gallery_portrait_6` — old man
- **Items gained:** _(none)_
- **How to achieve:** `gallery_portrait_6` at `gallery_portrait_6` — old man.
- **Success condition:** Room `117` and gallery completed-steps >= 6

### `pl48` — `gallery_end_of_life` (step 42)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `do_puzzle`
- **Site:** `gallery_end_of_life`
- **Beat:** `gallery_end_of_life`
- **Note:** death painting / slot 8; spawns star crest
- **Objective:** `gallery_end_of_life` at `gallery_end_of_life` — death painting / slot 8; spawns star crest
- **Items gained:** _(none)_
- **How to achieve:** `gallery_end_of_life` at `gallery_end_of_life` — death painting / slot 8; spawns star crest.
- **Success condition:** `story_use_success` == `gallery_end_of_life` in room `117`

### `pl49` — `star_crest` (step 43)

- **Room:** `117` (LARGE GALLERY)
- **Op:** `acquire`
- **Pickup:** `117:star_crest:1`
- **Beat:** `star_crest`
- **Objective:** Take `117:star_crest:1`
- **Items gained:** `star_crest`
- **How to achieve:** Take `117:star_crest:1`.
- **Success condition:** Inventory gains `117:star_crest:1` while this step is current

### `pl50` — `117->10A` (step 44)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `117->10A`
- **Objective:** Walk `117->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `117->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `117->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:117->10A got <room>` (−4).

### `pl51` — `10A->11A` (step 45)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `traverse`
- **Edge:** `10A->11A`
- **Objective:** Walk `10A->11A` into `11A` (ROOFED PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->11A` into `11A` (ROOFED PASSAGE).
- **Success condition:** Enter room `11A` via `10A->11A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->11A got <room>` (−4).

### `pl52` — `place_star_crest` (step 46)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `objective`
- **Site:** `star_crest@11A_crest_slot`
- **Beat:** `place_star_crest`
- **Objective:** `place_star_crest` at `star_crest@11A_crest_slot`
- **Items gained:** _(none)_
- **How to achieve:** `place_star_crest` at `star_crest@11A_crest_slot`.
- **Success condition:** `story_use_success` == `star_crest@11A_crest_slot` in room `11A`

### `pl53` — `11A->10A` (step 47)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `11A->10A`
- **Objective:** Walk `11A->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `11A->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `11A->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:11A->10A got <room>` (−4).

### `pl54` — `10A->10B` (step 48)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10A->10B`
- **Objective:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `10A->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->10B got <room>` (−4).

### `pl55` — `10B->207` (step 49)

- **Room:** `207` (2F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10B->207`
- **Objective:** Walk `10B->207` into `207` (2F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->207` into `207` (2F RIGHT STAIRS).
- **Success condition:** Enter room `207` via `10B->207` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->207 got <room>` (−4).

### `pl56` — `207->204` (step 50)

- **Room:** `204` (C PASSAGE)
- **Op:** `traverse`
- **Edge:** `207->204`
- **Objective:** Walk `207->204` into `204` (C PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `207->204` into `204` (C PASSAGE).
- **Success condition:** Enter room `204` via `207->204` (already-there counts after cinema dump). Any other door is `wrong_traverse:207->204 got <room>` (−4).

### `pl57` — `204->203` (step 51)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `204->203`
- **Objective:** Walk `204->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `204->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->203 got <room>` (−4).

### `pl58` — `203->202` (step 52)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `traverse`
- **Edge:** `203->202`
- **Objective:** Walk `203->202` into `202` (DINING ROOM 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->202` into `202` (DINING ROOM 2F).
- **Success condition:** Enter room `202` via `203->202` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->202 got <room>` (−4).

### `pl59` — `202->201` (step 53)

- **Room:** `201` (2F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `202->201`
- **Objective:** Walk `202->201` into `201` (2F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `202->201` into `201` (2F LEFT STAIRS).
- **Success condition:** Enter room `201` via `202->201` (already-there counts after cinema dump). Any other door is `wrong_traverse:202->201 got <room>` (−4).

### `pl60` — `201->101` (step 54)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `201->101`
- **Objective:** Walk `201->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `201->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `201->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:201->101 got <room>` (−4).

### `pl61` — `101->103` (step 55)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `101->103`
- **Objective:** Walk `101->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `101->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->103 got <room>` (−4).

### `pl62` — `103->10C` (step 56)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `traverse`
- **Edge:** `103->10C`
- **Objective:** Walk `103->10C` into `10C` (GREEN HOUSE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->10C` into `10C` (GREEN HOUSE).
- **Success condition:** Enter room `10C` via `103->10C` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->10C got <room>` (−4).

### `pl63` — `greenhouse_pump` (step 57)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `objective`
- **Site:** `chemical@10C_greenhouse_pump`
- **Beat:** `greenhouse_pump`
- **Objective:** `greenhouse_pump` at `chemical@10C_greenhouse_pump`
- **Items gained:** _(none)_
- **How to achieve:** `greenhouse_pump` at `chemical@10C_greenhouse_pump`.
- **Success condition:** `story_use_success` == `chemical@10C_greenhouse_pump` in room `10C`

### `pl64` — `armor_key` (step 58)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:armor_key:1`
- **Beat:** `armor_key`
- **Objective:** Take `10C:armor_key:1`
- **Items gained:** `armor_key`
- **How to achieve:** Take `10C:armor_key:1`.
- **Success condition:** Inventory gains `10C:armor_key:1` while this step is current

### `pl65` — `10C:red_herb:3a` (step 59)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:red_herb:3a`
- **Note:** bench red 1/2
- **Objective:** Take `10C:red_herb:3a` (bench red 1/2)
- **Items gained:** `red_herb`
- **How to achieve:** Take `10C:red_herb:3a` (bench red 1/2).
- **Success condition:** Inventory gains `10C:red_herb:3a` while this step is current

### `pl66` — `10C:green_herb:2a` (step 60)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:green_herb:2a`
- **Note:** bench green 1/2
- **Objective:** Take `10C:green_herb:2a` (bench green 1/2)
- **Items gained:** `green_herb`
- **How to achieve:** Take `10C:green_herb:2a` (bench green 1/2).
- **Success condition:** Inventory gains `10C:green_herb:2a` while this step is current

### `pl67` — `10C:red_herb:3b` (step 61)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:red_herb:3b`
- **Note:** bench red 2/2
- **Objective:** Take `10C:red_herb:3b` (bench red 2/2)
- **Items gained:** `red_herb`
- **How to achieve:** Take `10C:red_herb:3b` (bench red 2/2).
- **Success condition:** Inventory gains `10C:red_herb:3b` while this step is current

### `pl68` — `10C:green_herb:2b` (step 62)

- **Room:** `10C` (GREEN HOUSE)
- **Op:** `acquire`
- **Pickup:** `10C:green_herb:2b`
- **Note:** bench green 2/2; 3rd green/red = divert
- **Objective:** Take `10C:green_herb:2b` (bench green 2/2; 3rd green/red = divert)
- **Items gained:** `green_herb`
- **How to achieve:** Take `10C:green_herb:2b` (bench green 2/2; 3rd green/red = divert).
- **Success condition:** Inventory gains `10C:green_herb:2b` while this step is current

### `pl69` — `10C->103` (step 63)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `10C->103`
- **Objective:** Walk `10C->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10C->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `10C->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:10C->103 got <room>` (−4).

### `pl70` — `103->101` (step 64)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `103->101`
- **Objective:** Walk `103->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `103->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->101 got <room>` (−4).

### `pl71` — `101->102` (step 65)

- **Room:** `102` (VACANT ROOM)
- **Op:** `traverse`
- **Edge:** `101->102`
- **Objective:** Walk `101->102` into `102` (VACANT ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->102` into `102` (VACANT ROOM).
- **Success condition:** Enter room `102` via `101->102` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->102 got <room>` (−4).

### `pl72` — `102:handgun_bullets:1` (step 66)

- **Room:** `102` (VACANT ROOM)
- **Op:** `acquire`
- **Pickup:** `102:handgun_bullets:1`
- **Objective:** Take `102:handgun_bullets:1`
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `102:handgun_bullets:1`.
- **Success condition:** Inventory gains `102:handgun_bullets:1` while this step is current

### `pl73` — `102:shotgun_shells:2` (step 67)

- **Room:** `102` (VACANT ROOM)
- **Op:** `acquire`
- **Pickup:** `102:shotgun_shells:2`
- **Objective:** Take `102:shotgun_shells:2`
- **Items gained:** `shotgun_shells`
- **How to achieve:** Take `102:shotgun_shells:2`.
- **Success condition:** Inventory gains `102:shotgun_shells:2` while this step is current

### `pl74` — `102->101` (step 68)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `102->101`
- **Objective:** Walk `102->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `102->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `102->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:102->101 got <room>` (−4).

### `pl75` — `101->100` (step 69)

- **Room:** `100` (SAVE ROOM)
- **Op:** `traverse`
- **Edge:** `101->100`
- **Objective:** Walk `101->100` into `100` (SAVE ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->100` into `100` (SAVE ROOM).
- **Success condition:** Enter room `100` via `101->100` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->100 got <room>` (−4).

### `pl76` — `use_box` (step 70)

- **Room:** `100` (SAVE ROOM)
- **Op:** `use_box`
- **Objective:** Rearrange the 100 box to the leave_100 loadout, then close the box
- **Items gained:** _(none)_
- **How to achieve:** Rearrange the 100 box to the leave_100 loadout, then close the box.
- **Success condition:** Box closes and inventory matches `leave_100.held_on_exit`

### `pl77` — `100->101` (step 71)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `100->101`
- **Objective:** Walk `100->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `100->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `100->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:100->101 got <room>` (−4).

### `pl78` — `101->201` (step 72)

- **Room:** `201` (2F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `101->201`
- **Objective:** Walk `101->201` into `201` (2F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->201` into `201` (2F LEFT STAIRS).
- **Success condition:** Enter room `201` via `101->201` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->201 got <room>` (−4).

### `pl79` — `201->202` (step 73)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `traverse`
- **Edge:** `201->202`
- **Objective:** Walk `201->202` into `202` (DINING ROOM 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `201->202` into `202` (DINING ROOM 2F).
- **Success condition:** Enter room `202` via `201->202` (already-there counts after cinema dump). Any other door is `wrong_traverse:201->202 got <room>` (−4).

### `pl80` — `202->203` (step 74)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `202->203`
- **Objective:** Walk `202->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `202->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `202->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:202->203 got <room>` (−4).

### `pl81` — `203->204` (step 75)

- **Room:** `204` (C PASSAGE)
- **Op:** `traverse`
- **Edge:** `203->204`
- **Objective:** Walk `203->204` into `204` (C PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->204` into `204` (C PASSAGE).
- **Success condition:** Enter room `204` via `203->204` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->204 got <room>` (−4).

### `pl82` — `armor_room_enter` (step 76)

- **Room:** `205` (ARMOR ROOM)
- **Op:** `traverse`
- **Edge:** `204->205`
- **Beat:** `armor_room_enter`
- **Objective:** Walk `204->205` into `205` (ARMOR ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->205` into `205` (ARMOR ROOM).
- **Success condition:** Enter room `205` via `204->205` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->205 got <room>` (−4).

### `pl83` — `armor_vent_door` (step 77)

- **Room:** `205` (ARMOR ROOM)
- **Op:** `do_puzzle`
- **Site:** `armor_vent_door`
- **Beat:** `armor_vent_door`
- **Note:** east statue exactly on its vent
- **Objective:** `armor_vent_door` at `armor_vent_door` — east statue exactly on its vent
- **Items gained:** _(none)_
- **How to achieve:** `armor_vent_door` at `armor_vent_door` — east statue exactly on its vent.
- **Success condition:** Room `205` and east OM-object target `(14035, 7340)` within ±8 in all three mirrors

### `pl84` — `armor_vent_far` (step 78)

- **Room:** `205` (ARMOR ROOM)
- **Op:** `do_puzzle`
- **Site:** `armor_vent_far`
- **Beat:** `armor_vent_far`
- **Note:** both east and west statues exactly on their vents
- **Objective:** `armor_vent_far` at `armor_vent_far` — both east and west statues exactly on their vents
- **Items gained:** _(none)_
- **How to achieve:** `armor_vent_far` at `armor_vent_far` — both east and west statues exactly on their vents.
- **Success condition:** Room `205`, east OM-object target `(14035, 7340)` within ±8 in all three mirrors, and west OM-object seat inside validated AABB X `4845..5195` / Z `7086..7336` (covers QS `(4895, 7186)`); both are mandatory (one shove-grid cell still covers the west vent AOT)

### `pl85` — `sun_crest` (step 79)

- **Room:** `205` (ARMOR ROOM)
- **Op:** `acquire`
- **Pickup:** `205:sun_crest:1`
- **Beat:** `sun_crest`
- **Note:** activate center button, then take cabinet sun crest
- **Objective:** Take `205:sun_crest:1` (activate center button, then take cabinet sun crest)
- **Items gained:** `sun_crest`
- **How to achieve:** Take `205:sun_crest:1` (activate center button, then take cabinet sun crest).
- **Success condition:** Inventory gains `205:sun_crest:1` while this step is current

### `pl86` — `205->204` (step 80)

- **Room:** `204` (C PASSAGE)
- **Op:** `traverse`
- **Edge:** `205->204`
- **Note:** leave armor room toward Richard / crest / dining
- **Objective:** Walk `205->204` into `204` (C PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `205->204` into `204` (C PASSAGE).
- **Success condition:** Enter room `204` via `205->204` (already-there counts after cinema dump). Any other door is `wrong_traverse:205->204 got <room>` (−4).

### `pl87` — `richard_approach` (step 81)

- **Room:** `20D` (PILLAR PASSAGE)
- **Op:** `traverse`
- **Edge:** `204->20D`
- **Beat:** `richard_approach`
- **Note:** armor_key gate into Pillar Passage
- **Objective:** Walk `204->20D` into `20D` (PILLAR PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->20D` into `20D` (PILLAR PASSAGE).
- **Success condition:** Enter room `20D` via `204->20D` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->20D got <room>` (−4).

### `(no cell)` — `richard_bleedout` (step 82, capture:false)

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

### `pl88` — `204->207` (step 83)

- **Room:** `207` (2F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `204->207`
- **Note:** leave C passage after Richard dump
- **Objective:** Walk `204->207` into `207` (2F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->207` into `207` (2F RIGHT STAIRS).
- **Success condition:** Enter room `207` via `204->207` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->207 got <room>` (−4).

### `pl89` — `207->10B` (step 84)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `207->10B`
- **Objective:** Walk `207->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `207->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `207->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:207->10B got <room>` (−4).

### `pl90` — `10B->10A` (step 85)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `10B->10A`
- **Objective:** Walk `10B->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `10B->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->10A got <room>` (−4).

### `pl91` — `10A->11A` (step 86)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `traverse`
- **Edge:** `10A->11A`
- **Objective:** Walk `10A->11A` into `11A` (ROOFED PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->11A` into `11A` (ROOFED PASSAGE).
- **Success condition:** Enter room `11A` via `10A->11A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->11A got <room>` (−4).

### `pl92` — `place_sun_crest` (step 87)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `objective`
- **Site:** `sun_crest@11A_crest_slot`
- **Beat:** `place_sun_crest`
- **Note:** place held sun_crest; burns Richard timer
- **Objective:** `place_sun_crest` at `sun_crest@11A_crest_slot` — place held sun_crest; burns Richard timer
- **Items gained:** _(none)_
- **How to achieve:** `place_sun_crest` at `sun_crest@11A_crest_slot` — place held sun_crest; burns Richard timer.
- **Success condition:** `story_use_success` == `sun_crest@11A_crest_slot` in room `11A`

### `pl93` — `11A->10A` (step 88)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `11A->10A`
- **Objective:** Walk `11A->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `11A->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `11A->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:11A->10A got <room>` (−4).

### `pl94` — `10A->10B` (step 89)

- **Room:** `10B` (1F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10A->10B`
- **Objective:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->10B` into `10B` (1F RIGHT STAIRS).
- **Success condition:** Enter room `10B` via `10A->10B` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->10B got <room>` (−4).

### `pl95` — `10B->207` (step 90)

- **Room:** `207` (2F RIGHT STAIRS)
- **Op:** `traverse`
- **Edge:** `10B->207`
- **Objective:** Walk `10B->207` into `207` (2F RIGHT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10B->207` into `207` (2F RIGHT STAIRS).
- **Success condition:** Enter room `207` via `10B->207` (already-there counts after cinema dump). Any other door is `wrong_traverse:10B->207 got <room>` (−4).

### `pl96` — `207->204` (step 91)

- **Room:** `204` (C PASSAGE)
- **Op:** `traverse`
- **Edge:** `207->204`
- **Objective:** Walk `207->204` into `204` (C PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `207->204` into `204` (C PASSAGE).
- **Success condition:** Enter room `204` via `207->204` (already-there counts after cinema dump). Any other door is `wrong_traverse:207->204 got <room>` (−4).

### `pl97` — `204->203` (step 92)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `204->203`
- **Objective:** Walk `204->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `204->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `204->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:204->203 got <room>` (−4).

### `pl98` — `dining_2f_enter` (step 93)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `traverse`
- **Edge:** `203->202`
- **Beat:** `dining_2f_enter`
- **Note:** Dining Room 2F
- **Objective:** Walk `203->202` into `202` (DINING ROOM 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->202` into `202` (DINING ROOM 2F).
- **Success condition:** Enter room `202` via `203->202` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->202 got <room>` (−4).

### `pl99` — `push_statue_2f` (step 94)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `do_puzzle`
- **Site:** `dining_statue_knocked`
- **Beat:** `push_statue_2f`
- **Note:** push balcony statue down; blue jewel drops to dining hall 105
- **Objective:** `push_statue_2f` at `dining_statue_knocked` — push balcony statue down; blue jewel drops to dining hall 105
- **Items gained:** _(none)_
- **How to achieve:** `push_statue_2f` at `dining_statue_knocked` — push balcony statue down; blue jewel drops to dining hall 105.
- **Success condition:** Room `202` and dining balcony statue knocked (`dining_statue_flag` bit 0x10 / `dining_statue_knocked`)

### `pl100` — `202->203` (step 95)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `202->203`
- **Note:** leave Dining 2F toward main hall / jewel
- **Objective:** Walk `202->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `202->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `202->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:202->203 got <room>` (−4).

### `pl101` — `203->106` (step 96)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `203->106`
- **Objective:** Walk `203->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `203->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->106 got <room>` (−4).

### `pl102` — `106->105` (step 97)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Edge:** `106->105`
- **Objective:** Walk `106->105` into `105` (DINING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->105` into `105` (DINING ROOM).
- **Success condition:** Enter room `105` via `106->105` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->105 got <room>` (−4).

### `pl103` — `blue_jewel` (step 98)

- **Room:** `105` (DINING ROOM)
- **Op:** `acquire`
- **Pickup:** `105:blue_jewel:1`
- **Beat:** `blue_jewel`
- **Note:** statue drop puts jewel in dining hall 105 (not 202)
- **Objective:** Take `105:blue_jewel:1` (statue drop puts jewel in dining hall 105 (not 202))
- **Items gained:** `blue_jewel`
- **How to achieve:** Take `105:blue_jewel:1` (statue drop puts jewel in dining hall 105 (not 202)).
- **Success condition:** Inventory gains `105:blue_jewel:1` while this step is current

### `pl104` — `105->106` (step 99)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `105->106`
- **Note:** back out; 104->103 still locked — do not tea-cut
- **Objective:** Walk `105->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `105->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `105->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:105->106 got <room>` (−4).

### `pl105` — `106->203` (step 100)

- **Room:** `203` (HALL 2F)
- **Op:** `traverse`
- **Edge:** `106->203`
- **Objective:** Walk `106->203` into `203` (HALL 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->203` into `203` (HALL 2F).
- **Success condition:** Enter room `203` via `106->203` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->203 got <room>` (−4).

### `pl106` — `203->202` (step 101)

- **Room:** `202` (DINING ROOM 2F)
- **Op:** `traverse`
- **Edge:** `203->202`
- **Objective:** Walk `203->202` into `202` (DINING ROOM 2F)
- **Items gained:** _(none)_
- **How to achieve:** Walk `203->202` into `202` (DINING ROOM 2F).
- **Success condition:** Enter room `202` via `203->202` (already-there counts after cinema dump). Any other door is `wrong_traverse:203->202 got <room>` (−4).

### `pl107` — `202->201` (step 102)

- **Room:** `201` (2F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `202->201`
- **Objective:** Walk `202->201` into `201` (2F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `202->201` into `201` (2F LEFT STAIRS).
- **Success condition:** Enter room `201` via `202->201` (already-there counts after cinema dump). Any other door is `wrong_traverse:202->201 got <room>` (−4).

### `pl108` — `201->101` (step 103)

- **Room:** `101` (1F LEFT STAIRS)
- **Op:** `traverse`
- **Edge:** `201->101`
- **Objective:** Walk `201->101` into `101` (1F LEFT STAIRS)
- **Items gained:** _(none)_
- **How to achieve:** Walk `201->101` into `101` (1F LEFT STAIRS).
- **Success condition:** Enter room `101` via `201->101` (already-there counts after cinema dump). Any other door is `wrong_traverse:201->101 got <room>` (−4).

### `pl109` — `101->103` (step 104)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `101->103`
- **Note:** skip 102 vacant — clip+shells already taken
- **Objective:** Walk `101->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `101->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `101->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:101->103 got <room>` (−4).

### `pl110` — `tiger_room_enter` (step 105)

- **Room:** `10D` (TIGER STATUE ROOM)
- **Op:** `traverse`
- **Edge:** `103->10D`
- **Beat:** `tiger_room_enter`
- **Note:** Tiger Statue Room
- **Objective:** Walk `103->10D` into `10D` (TIGER STATUE ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->10D` into `10D` (TIGER STATUE ROOM).
- **Success condition:** Enter room `10D` via `103->10D` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->10D got <room>` (−4).

### `pl111` — `tiger_jewel` (step 106)

- **Room:** `10D` (TIGER STATUE ROOM)
- **Op:** `objective`
- **Site:** `blue_jewel@10D_tiger_eye`
- **Beat:** `tiger_jewel`
- **Note:** insert blue jewel in tiger eye
- **Objective:** `tiger_jewel` at `blue_jewel@10D_tiger_eye` — insert blue jewel in tiger eye
- **Items gained:** _(none)_
- **How to achieve:** `tiger_jewel` at `blue_jewel@10D_tiger_eye` — insert blue jewel in tiger eye.
- **Success condition:** `story_use_success` == `blue_jewel@10D_tiger_eye` in room `10D`

### `pl112` — `wind_crest` (step 107)

- **Room:** `10D` (TIGER STATUE ROOM)
- **Op:** `acquire`
- **Pickup:** `10D:wind_crest:1`
- **Beat:** `wind_crest`
- **Note:** acquire wind crest; continue to place_wind resource tail
- **Objective:** Take `10D:wind_crest:1` (acquire wind crest; continue to place_wind resource tail)
- **Items gained:** `wind_crest`
- **How to achieve:** Take `10D:wind_crest:1` (acquire wind crest; continue to place_wind resource tail).
- **Success condition:** Inventory gains `10D:wind_crest:1` while this step is current

### `pl113` — `10D->103` (step 108)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `10D->103`
- **Note:** 10D to F passage
- **Objective:** Walk `10D->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10D->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `10D->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:10D->103 got <room>` (−4).

### `pl114` — `103->10E` (step 109)

- **Room:** `10E` (EMPLOYEE ROOM)
- **Op:** `traverse`
- **Edge:** `103->10E`
- **Note:** F passage to employee room
- **Objective:** Walk `103->10E` into `10E` (EMPLOYEE ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->10E` into `10E` (EMPLOYEE ROOM).
- **Success condition:** Enter room `10E` via `103->10E` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->10E got <room>` (−4).

### `pl115` — `10E:handgun_bullets:1` (step 110)

- **Room:** `10E` (EMPLOYEE ROOM)
- **Op:** `acquire`
- **Pickup:** `10E:handgun_bullets:1`
- **Note:** on bed
- **Objective:** Take `10E:handgun_bullets:1` (on bed)
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `10E:handgun_bullets:1` (on bed).
- **Success condition:** Inventory gains `10E:handgun_bullets:1` while this step is current

### `pl116` — `10E:shotgun_shells:2` (step 111)

- **Room:** `10E` (EMPLOYEE ROOM)
- **Op:** `acquire`
- **Pickup:** `10E:shotgun_shells:2`
- **Note:** in closet
- **Objective:** Take `10E:shotgun_shells:2` (in closet)
- **Items gained:** `shotgun_shells`
- **How to achieve:** Take `10E:shotgun_shells:2` (in closet).
- **Success condition:** Inventory gains `10E:shotgun_shells:2` while this step is current

### `pl117` — `10E->103` (step 112)

- **Room:** `103` (F PASSAGE)
- **Op:** `traverse`
- **Edge:** `10E->103`
- **Note:** back to F passage
- **Objective:** Walk `10E->103` into `103` (F PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10E->103` into `103` (F PASSAGE).
- **Success condition:** Enter room `103` via `10E->103` (already-there counts after cinema dump). Any other door is `wrong_traverse:10E->103 got <room>` (−4).

### `pl118` — `tea_unlock_103_104` (step 113)

- **Room:** `104` (TEA ROOM)
- **Op:** `traverse`
- **Edge:** `103->104`
- **Beat:** `tea_unlock_103_104`
- **Note:** first 103->104 after wind; opens tea both ways
- **Objective:** Walk `103->104` into `104` (TEA ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `103->104` into `104` (TEA ROOM).
- **Success condition:** Enter room `104` via `103->104` (already-there counts after cinema dump). Any other door is `wrong_traverse:103->104 got <room>` (−4).

### `pl119` — `104->105` (step 114)

- **Room:** `105` (DINING ROOM)
- **Op:** `traverse`
- **Edge:** `104->105`
- **Note:** tea to dining
- **Objective:** Walk `104->105` into `105` (DINING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `104->105` into `105` (DINING ROOM).
- **Success condition:** Enter room `105` via `104->105` (already-there counts after cinema dump). Any other door is `wrong_traverse:104->105 got <room>` (−4).

### `pl120` — `105->106` (step 115)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `105->106`
- **Note:** dining to main hall
- **Objective:** Walk `105->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `105->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `105->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:105->106 got <room>` (−4).

### `pl121` — `dressing_room_enter` (step 116)

- **Room:** `111` (DRESSING ROOM)
- **Op:** `traverse`
- **Edge:** `106->111`
- **Beat:** `dressing_room_enter`
- **Note:** armor_key door; dressing ammo then wardrobe herbs
- **Objective:** Walk `106->111` into `111` (DRESSING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->111` into `111` (DRESSING ROOM).
- **Success condition:** Enter room `111` via `106->111` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->111 got <room>` (−4).

### `pl122` — `111:handgun_bullets:1` (step 117)

- **Room:** `111` (DRESSING ROOM)
- **Op:** `acquire`
- **Pickup:** `111:handgun_bullets:1`
- **Note:** shelf clip
- **Objective:** Take `111:handgun_bullets:1` (shelf clip)
- **Items gained:** `handgun_bullets`
- **How to achieve:** Take `111:handgun_bullets:1` (shelf clip).
- **Success condition:** Inventory gains `111:handgun_bullets:1` while this step is current

### `pl123` — `111:shotgun_shells:2` (step 118)

- **Room:** `111` (DRESSING ROOM)
- **Op:** `acquire`
- **Pickup:** `111:shotgun_shells:2`
- **Note:** locked desk; one shell clip
- **Objective:** Take `111:shotgun_shells:2` (locked desk; one shell clip)
- **Items gained:** `shotgun_shells`
- **How to achieve:** Take `111:shotgun_shells:2` (locked desk; one shell clip).
- **Success condition:** Inventory gains `111:shotgun_shells:2` while this step is current

### `pl124` — `111->112` (step 119)

- **Room:** `112` (WARDROBE)
- **Op:** `traverse`
- **Edge:** `111->112`
- **Note:** wardrobe for both SE green herbs
- **Objective:** Walk `111->112` into `112` (WARDROBE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `111->112` into `112` (WARDROBE).
- **Success condition:** Enter room `112` via `111->112` (already-there counts after cinema dump). Any other door is `wrong_traverse:111->112 got <room>` (−4).

### `pl125` — `112:green_herb:1` (step 120)

- **Room:** `112` (WARDROBE)
- **Op:** `acquire`
- **Pickup:** `112:green_herb:1`
- **Note:** SE corner plant 1/2
- **Objective:** Take `112:green_herb:1` (SE corner plant 1/2)
- **Items gained:** `green_herb`
- **How to achieve:** Take `112:green_herb:1` (SE corner plant 1/2).
- **Success condition:** Inventory gains `112:green_herb:1` while this step is current

### `pl126` — `112:green_herb:2` (step 121)

- **Room:** `112` (WARDROBE)
- **Op:** `acquire`
- **Pickup:** `112:green_herb:2`
- **Note:** SE corner plant 2/2
- **Objective:** Take `112:green_herb:2` (SE corner plant 2/2)
- **Items gained:** `green_herb`
- **How to achieve:** Take `112:green_herb:2` (SE corner plant 2/2).
- **Success condition:** Inventory gains `112:green_herb:2` while this step is current

### `pl127` — `112->111` (step 122)

- **Room:** `111` (DRESSING ROOM)
- **Op:** `traverse`
- **Edge:** `112->111`
- **Note:** return from wardrobe
- **Objective:** Walk `112->111` into `111` (DRESSING ROOM)
- **Items gained:** _(none)_
- **How to achieve:** Walk `112->111` into `111` (DRESSING ROOM).
- **Success condition:** Enter room `111` via `112->111` (already-there counts after cinema dump). Any other door is `wrong_traverse:112->111 got <room>` (−4).

### `pl128` — `111->106` (step 123)

- **Room:** `106` (MAIN HALL)
- **Op:** `traverse`
- **Edge:** `111->106`
- **Note:** dressing to main hall
- **Objective:** Walk `111->106` into `106` (MAIN HALL)
- **Items gained:** _(none)_
- **How to achieve:** Walk `111->106` into `106` (MAIN HALL).
- **Success condition:** Enter room `106` via `111->106` (already-there counts after cinema dump). Any other door is `wrong_traverse:111->106 got <room>` (−4).

### `pl129` — `106->107` (step 124)

- **Room:** `107` (GALLERY)
- **Op:** `traverse`
- **Edge:** `106->107`
- **Note:** art room / gallery circuit toward 11A
- **Objective:** Walk `106->107` into `107` (GALLERY)
- **Items gained:** _(none)_
- **How to achieve:** Walk `106->107` into `107` (GALLERY).
- **Success condition:** Enter room `107` via `106->107` (already-there counts after cinema dump). Any other door is `wrong_traverse:106->107 got <room>` (−4).

### `pl130` — `107->108` (step 125)

- **Room:** `108` (L PASSAGE)
- **Op:** `traverse`
- **Edge:** `107->108`
- **Note:** gallery to L passage
- **Objective:** Walk `107->108` into `108` (L PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `107->108` into `108` (L PASSAGE).
- **Success condition:** Enter room `108` via `107->108` (already-there counts after cinema dump). Any other door is `wrong_traverse:107->108 got <room>` (−4).

### `pl131` — `108->109` (step 126)

- **Room:** `109` (TRAP PASSAGE)
- **Op:** `traverse`
- **Edge:** `108->109`
- **Note:** L passage to trap passage
- **Objective:** Walk `108->109` into `109` (TRAP PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `108->109` into `109` (TRAP PASSAGE).
- **Success condition:** Enter room `109` via `108->109` (already-there counts after cinema dump). Any other door is `wrong_traverse:108->109 got <room>` (−4).

### `pl132` — `109->10A` (step 127)

- **Room:** `10A` (BACK PASSAGE)
- **Op:** `traverse`
- **Edge:** `109->10A`
- **Note:** trap passage to back passage
- **Objective:** Walk `109->10A` into `10A` (BACK PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `109->10A` into `10A` (BACK PASSAGE).
- **Success condition:** Enter room `10A` via `109->10A` (already-there counts after cinema dump). Any other door is `wrong_traverse:109->10A got <room>` (−4).

### `pl133` — `10A->11A` (step 128)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `traverse`
- **Edge:** `10A->11A`
- **Note:** back passage to roofed passage
- **Objective:** Walk `10A->11A` into `11A` (ROOFED PASSAGE)
- **Items gained:** _(none)_
- **How to achieve:** Walk `10A->11A` into `11A` (ROOFED PASSAGE).
- **Success condition:** Enter room `11A` via `10A->11A` (already-there counts after cinema dump). Any other door is `wrong_traverse:10A->11A got <room>` (−4).

### `pl134` — `place_wind_crest` (step 129)

- **Room:** `11A` (ROOFED PASSAGE)
- **Op:** `objective`
- **Site:** `wind_crest@11A_crest_slot`
- **Beat:** `place_wind_crest`
- **Note:** chunk end-anchor
- **Objective:** `place_wind_crest` at `wind_crest@11A_crest_slot` — chunk end-anchor
- **Items gained:** _(none)_
- **How to achieve:** `place_wind_crest` at `wind_crest@11A_crest_slot` — chunk end-anchor.
- **Success condition:** `story_use_success` == `wind_crest@11A_crest_slot` in room `11A`

