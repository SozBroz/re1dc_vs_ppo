# Muse chunk log

Append one section per Muse `next_chunk`. Live queue is `cp05_shield_key.json` (appended tails). Raw replies live in `muse_raw/`.

## 2026-08-25 — shield_key (Pass-1)

- **Tip:** pl05, room 106 (Barry / lockpick). Wooden emblem already held.
- **Model:** muse-glimmer
- **End anchor:** `shield_key`
- **Why:** same 13-step piano / gold emblem / fireplace path as Qwen Pass-1.
- **Raw:** `muse_raw/2026-08-25_shield_key.json`
- **Pinned as:** `cp05_shield_key.json` steps 1–13

| n | op | what |
|---|---|---|
| 1 | traverse | 106→105 |
| 2 | traverse | 105→104 |
| 3 | acquire | 104:handgun_bullets:1 |
| 4 | acquire | 104:handgun_bullets:2 |
| 5 | traverse | 104→10F |
| 6 | acquire | 10F:music_notes:1 |
| 7 | objective | music_notes@10F_piano |
| 8 | acquire | 10F:gold_emblem:2 |
| 9 | objective | emblem@10F_alcove |
| 10 | traverse | 10F→104 |
| 11 | traverse | 104→105 |
| 12 | objective | gold_emblem@105_fireplace |
| 13 | acquire | 105:shield_key:2 |

## 2026-08-26 — chemical (pl18)

- **Tip:** pl18, room 105, just got shield_key.
- **Model:** muse-glimmer (1715 completion / 9266 prompt)
- **End anchor:** `chemical`
- **Why:** shield_key cannot open attic yet (`attic_enter` also needs `richard_timer_elapsed` → armor_key → chemical). Unlocked frontier was gallery / chemical / dining 2F; Muse picked chemical.
- **Raw:** `muse_raw/2026-08-26_chemical.json`
- **Pinned as:** `cp05_shield_key.json` steps 14–24
- **Operator edits:** added `108:handgun_bullets:1` after 107→108 (Muse walked past the L-passage clip; catalog row is a null `clip`). Shotgun 116 still skipped.

| n | op | what | Muse n |
|---|---|---|---|
| 14 | traverse | 105→106 | 1 |
| 15 | traverse | 106→107 | 2 |
| 16 | traverse | 107→108 | 3 |
| 17 | acquire | 108:handgun_bullets:1 | — |
| 18 | traverse | 108→109 | 4 |
| 19 | acquire | 109:green_herb:1 | 5 |
| 20 | traverse | 109→10A | 6 |
| 21 | traverse | 10A→10B | 7 |
| 22 | acquire | 10B:green_herb:1 | 8 |
| 23 | traverse | 10B→118 | 9 |
| 24 | acquire | 118:chemical:1 | 10 |

Muse remaining beat_order after this: gallery portraits → star_crest → greenhouse_pump → armor_key → … → four crests at 11A.

## 2026-09-03 — place_wind_crest (pl110)

- **Tip:** pl110, room 10D, wind_crest held, HP 36 Caution. cp05 end-anchor wind_crest complete (chunk_final).
- **Model:** muse-glimmer (resource-first pass2; operator rejected 2F skip)
- **End anchor:** `place_wind_crest`
- **Why:** Loot 10E + unlock tea 103->104 + first-floor 111 ammo + wardrobe 112 greens + art circuit to 11A before attic/Yawn.
- **Raw:** `muse_raw/2026-09-03_place_wind.json` (base); wardrobe herb patch via Muse 2026-09-04 (`_tmp/pl111_muse_wardrobe_herbs_response.json`)
- **Pinned as:** `cp05_shield_key.json` steps 107–128
- **Operator edits:** 2026-09-04: acquire `111:handgun_bullets:1` before desk shells; after shells go `111->112` take **both** wardrobe greens (`112:green_herb:1` then `:2`), return `112->111->106` (skip ink).

| n | op | what |
|---|---|---|
| 107 | traverse | 10D→103 |
| 108 | traverse | 103→10E |
| 109 | acquire | 10E:handgun_bullets:1 |
| 110 | acquire | 10E:shotgun_shells:2 |
| 111 | traverse | 10E→103 |
| 112 | traverse | 103→104 (tea unlock) |
| 113 | traverse | 104→105 |
| 114 | traverse | 105→106 |
| 115 | traverse | 106→111 |
| 116 | acquire | 111:handgun_bullets:1 |
| 117 | acquire | 111:shotgun_shells:2 |
| 118 | traverse | 111→112 |
| 119 | acquire | 112:green_herb:1 |
| 120 | acquire | 112:green_herb:2 |
| 121 | traverse | 112→111 |
| 122 | traverse | 111→106 |
| 123 | traverse | 106→107 |
| 124 | traverse | 107→108 |
| 125 | traverse | 108→109 |
| 126 | traverse | 109→10A |
| 127 | traverse | 10A→11A |
| 128 | objective | wind_crest@11A_crest_slot (place_wind_crest) |

Muse beat_order after this: attic_enter → yawn_intro → yawn_1 → moon_crest → place_moon_crest.

## 2026-09-09 — attic_enter (pl134)

- **Tip:** pl134, room 11A, `place_wind_crest` done. HP 68 Fine; beretta 7 + shotgun 4; armor_key + green_herb held; box has shield_key, mixed_herbs_gg, knife, acid_rounds×6.
- **Model:** muse-glimmer (operator rejected naked attic; required GL + 208/209/20A)
- **End anchor:** `attic_enter`
- **Why:** Stage Yawn kit via 118 box + deer-wing loot + terrace `bazooka_acid`, then enter attic with `shield_key` held.
- **Raw:** `_tmp/pl134_muse_next_response.json`
- **Pinned as:** `cp05_shield_key.json` steps 130–159
- **Operator edits:** `use_box` `held_on_exit` deposits `green_herb` and withdraws `shield_key` + `acid_rounds` (Muse omitted held_on_exit); second 20D green fixed to `20D:green_herb:2` (Muse duplicated `:1`). Inventory stays tight after 209/20A/212 — combine/reload may be required in the wild.

| n | op | what |
|---|---|---|
| 130 | traverse | 11A→10A |
| 131 | traverse | 10A→10B |
| 132 | traverse | 10B→118 |
| 133 | go_to_box | 118 |
| 134 | use_box | withdraw shield_key + acid_rounds (deposit green) |
| 135 | traverse | 118→10B |
| 136 | traverse | 10B→207 |
| 137 | traverse | 207→208 |
| 138 | traverse | 208→209 |
| 139 | acquire | 209:red_herb:1 |
| 140 | acquire | 209:handgun_bullets:2 |
| 141 | acquire | 209:lighter:3 |
| 142 | traverse | 209→208 |
| 143 | traverse | 208→20A |
| 144 | acquire | 20A:explosive_rounds:1 |
| 145 | traverse | 20A→208 |
| 146 | traverse | 208→207 |
| 147 | traverse | 207→204 |
| 148 | traverse | 204→203 |
| 149 | traverse | 203→211 |
| 150 | traverse | 211→212 |
| 151 | acquire | 212:bazooka_acid:1 |
| 152 | traverse | 212→211 |
| 153 | traverse | 211→203 |
| 154 | traverse | 203→204 |
| 155 | traverse | 204→20D |
| 156 | acquire | 20D:green_herb:1 |
| 157 | acquire | 20D:green_herb:2 |
| 158 | traverse | 20D→20E |
| 159 | traverse | 20E→210 (`attic_enter`) |

Muse beat_order after this: yawn_intro → yawn_1 → moon_crest → place_moon_crest.

## 2026-09-10 — box before 20A (pl147)

- **Tip:** pl147, room 208 deer hub after 209 lighter loot. Inv FULL.
- **Model:** muse-glimmer (v3 pushback: herb brick + lighter-must-use + attic optional)
- **End anchor:** staging acquire `20A:explosive_rounds:1` (no DAG beat; attic deferred)
- **Why:** Box at 118 before study; bank armor_key + lighter; skip 20D greens this staging.
- **Raw:** `_tmp/pl147_muse_v3_response.json` / `muse_raw/2026-09-10_box_before_20A.json`
- **Pinned as:** `cp05_shield_key.json` steps 143–152 (replaced prior 143–159 attic path)
- **Operator edits:** formal `held_on_exit` on use_box (Muse note-only); truncated attic/GL/20D greens pending next Muse.

| n | op | what |
|---|---|---|
| 143 | traverse | 208→207 |
| 144 | traverse | 207→10B |
| 145 | traverse | 10B→118 |
| 146 | go_to_box | 118 |
| 147 | use_box | bank armor_key + lighter |
| 148 | traverse | 118→10B |
| 149 | traverse | 10B→207 |
| 150 | traverse | 207→208 |
| 151 | traverse | 208→20A |
| 152 | acquire | 20A:explosive_rounds:1 |

## 2026-09-10 — study 20A puzzle PLs + GL/yawn/moon (pl156)

- **Tip:** pl156, room 20A after `208->20A`. Study mega-acquire replaced by three `do_puzzle` beats + acquire.
- **Model:** muse-glimmer (`_tmp/pl157_muse_next_response.json`) then operator-fixed kit/path.
- **End anchor:** `place_moon_crest`
- **Why:** Drain→fishtank→cupboard→explosive as separate PLs; then box→212 GL→20D greens→yawn mint→place moon.
- **Raw:** `muse_raw/2026-09-10_pl157_gl_yawn_moon.json`
- **Pinned as:** `cp05_shield_key.json` steps 152–188
- **Operator edits:** formal `held_on_exit`/`banked_in_box`; withdraw `armor_key` for `204->20D`; bank shotgun+explosive+red for **3 free slots** (GL + 2 greens); keep `handgun_bullets` so Richard corpse clip stacks; add `20D:handgun_bullets:2`; `20D:green_herb:2`; explicit `yawn_intro`/`yawn_1`; short terrace path (no gallery detour).

| n | op | what |
|---|---|---|
| 152 | do_puzzle | study_insect_switch |
| 153 | do_puzzle | study_push_fishtank |
| 154 | do_puzzle | study_push_cupboard |
| 155 | acquire | 20A:explosive_rounds:1 |
| 156–161 | … | box at 118 (armor_key out; shotgun/explosive/red banked; **3 empty**) |
| 162–168 | … | short terrace → 212:bazooka_acid |
| 169–175 | … | 20D Richard clip (stacks) + greens → 20E→210 attic_enter |
| 176–179 | … | yawn_intro, yawn_1, shells, moon_crest |
| 180–189 | … | walk to 11A place_moon_crest |

## 2026-09-10 — pl164 118 box deposit fix + Muse reconfirm

- **Tip:** pl164, room 118 (already at box after `10B->118`).
- **Model:** muse-glimmer (`_tmp/pl164_muse_v3_response.json`); kit matches prior operator leave_118.
- **End anchor:** `place_moon_crest` (unchanged tail)
- **Why / bugs fixed (code, not route rewrite):**
  1. Room-118 deposits of shotgun/explosive were `not_allowlisted` while box-target mask still asked for them → UI stuck (often on shield_key, wrongly allowlisted via stale chunk `leave_118`).
  2. Step-level `banked_in_box` now owns deposit overrides (guns/ammo included); stale leave_118 keys no longer union in.
  3. `go_to_box` after inbound traverse: `capture: false` so it does **not** consume a plNN (use_box mints as **pl165**). Same skip when already in box room mid-episode (`prev_room`).
  4. Validator now applies `use_box.held_on_exit` for edge gates + inventory pressure.
- **Raw:** `muse_raw/2026-09-10_pl164_box_yawn_moon.json`
- **Pinned:** live `cp05` steps 160+ already held this kit; no step renumber beyond `capture:false` on go_to_box n=160.
- **PL note:** former hole pl165(go_to_box) is now `(no cell)`; use_box is pl165; 20D HG clip is pl177 (was planned pl178 in old capturing count).

| n | op | what |
|---|---|---|
| 160 | go_to_box | capture:false (already in 118) |
| 161 | use_box | bank shotgun+explosive+red; withdraw armor_key; 3 empty; shield held |
| 162–188 | … | short terrace → GL → 20D loot → yawn → place_moon_crest |

