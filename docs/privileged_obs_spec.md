# Privileged Spatial & Semantic Observation Spec

**Date:** 2026-07-04 · **Code of record:** `re1_rl/spatial_encoder.py`,
`re1_rl/obs_encoder.py`, `re1_rl/env.py`. If this doc and the code disagree,
the code wins.

Design goal: give the policy what a skilled human gets from Evil Resource
maps, audio cues, and a mental room map — as **sensors only**. The purity
rule from `docs/memory_hooks_and_observation_design.md` §0 holds: privileged
RAM informs what the agent *knows* and what we *grade*; only the learned
policy picks buttons. No optimal-path coordinates, no auto-aim, no cheats.

---

## 1. Observation dict (post-expansion)

| Key | Shape / dtype | Range | Provenance |
|-----|---------------|-------|------------|
| `frame` | 84×84×4 uint8 | 0–255 | screenshots (unchanged) |
| `proprio` | (20,) f32 | [-1,1] | live RAM (unchanged layout; slots 10/11 now wired) |
| `goal` | (27,) f32 | [-2,2] | planner + route JSON (**24 → 27**, §3) |
| `spatial` | (119,) f32 | [-2,2] | static tables + live RAM (§2) |
| `box` | (34,) f32 | [0,1] | item-box RAM + room flag (box §) |
| `visited` | (16,16,1) f32 | 0–1 | episode-local player trace (§4) |

Pretty-printing: `obs_encoder.format_obs_table(obs)` now includes `spatial`
and `box` (zero slot-rows suppressed) plus a `visited` summary line; used by
`scripts/watch_env.py`. The HUD (`re1_rl/overlay.py`) draws a nearest-item
compass, enemy summary, and visited-cell count.

## 2. `spatial` — 119 named floats (`spatial_encoder.SPATIAL_FIELDS`)

Layout: `1 + 8×8 (items) + 1 + 5×8 (enemies) + 1 + 4×3 (exits)`.

### 2.1 Items (slots sorted nearest-first; ever-held items removed)

| Field (×8 slots) | Normalization | Notes |
|---|---|---|
| `itemN_rel_x/rel_z` | Δ/4096, clip ±2 | zero when position unknown |
| `itemN_dist` | /4096, clip [0,2] | |
| `itemN_bearing_sin/cos` | egocentric; + sin = left, cos 1 = dead ahead | same convention as door compass |
| `itemN_item_id` | /0x46 | `memory_map.ITEM_IDS` |
| `itemN_key_item` | 0/1 | |
| `itemN_gated` | 0/1 | present but locked behind **tracked** requirements |
| `items_obtainable_here` | count/8 | obtainable, never-held (finer sibling of goal `items_left_here`) |

Visibility rule (conservative, per `docs/item_gates.md`): obtainable items
always show; gated items show with `gated=1` **only** when their gate names
concrete item requirements we track. Puzzle/event gates with empty
`requires` stay hidden until SCD work flags are mapped.

Positions come from `data/item_positions.json`
(`"<room>:<item>" → {x, z, source, confidence}`), built by
`scripts/build_item_positions.py` from:
1. `data/pickups_empirical.json` — ground truth logged by
   `scripts/log_door_transitions.py` at inventory gain (pose = last
   in-control pose, so pickup modals don't corrupt it). **Highest trust;
   always wins the merge.**
2. `MANUAL_ANCHORS` — landmark-derived guesses, confidence-annotated.
3. (Phase 1b, future) RDT `ITEM_SET` opcodes — validate against ≥1
   empirical pickup before trusting.

Items without coordinates still surface id/key/gated bits with zero
geometry — the agent knows *that* something is here before *where*.

### 2.2 Enemies (the audio substitute; slots sorted nearest-first)

| Field (×5 slots) | Normalization |
|---|---|
| `enemyN_rel_x/rel_z` | Δ/4096, clip ±2 |
| `enemyN_dist` | /4096, clip [0,2] |
| `enemyN_bearing_sin/cos` | egocentric |
| `enemyN_type_id` | /32 |
| `enemyN_hp` | /255 |
| `enemyN_alive` | 0/1 (dead slots skipped, tail zero-padded) |
| `enemy_count` | alive/10 (also mirrored in `proprio[10]`) |

Source: `state["enemies"]` from `memory_map.decode_enemy_table()`. The PS1
enemy table base is **still unmapped** — `ENEMY_TABLE_BASE = None`, so the
env reads no enemy fields and all slots are zero. The whole path
(env → state → both encoders → overlay → tests) is wired; filling in
`ENEMY_TABLE_BASE` + `ENEMY_FIELD_OFFSETS` after the hunt
(`docs/enemy_ram_hunt.md`, `scripts/hunt_enemy_ram.py`) lights it up with
no further code changes.

### 2.3 Exits (all known exits, not just the BFS next hop)

| Field | Normalization |
|---|---|
| `num_known_exits` | /8 |
| `exitN_bearing_sin/cos, exitN_dist` (×4) | as above |

Source: every `doors_empirical.json` edge leaving the current room (22
edges today; grow with `scripts/harvest_doors.py`). The goal-directed door
compass in `goal` is unchanged; this section is the allocentric "I know
this room has three doors" sense.

## box — 34 named floats (`obs_encoder.BOX_FIELDS`)

Shape `(34,)`: 16 slots × (`boxN_item_id`, `boxN_qty`) + `box_free_slots` +
`in_box_room`.

| Field (×16 slots) | Normalization | Notes |
|---|---|---|
| `boxN_item_id` | /0x46 | `memory_map.ITEM_IDS`; 0 = empty slot |
| `boxN_qty` | /15, clip [0,1] | stack size per slot |
| `box_free_slots` | empty slots / 16 | count of `item_id == 0` among 16 |
| `in_box_room` | 0/1 | 1 when current room has an item box |

Source: privileged read of `ITEM_BOX_BASE` (`0x800C8724`) — 2 bytes per
slot `(item_id, qty)` × 16. `in_box_room` is derived from the eight RDT
item-box rooms: `100`, `118`, `30E`, `403`, `502`, `50E`, `600`, `618`.

## 3. `goal` additions (24 → 27)

| Idx | Field | Meaning |
|---|---|---|
| 24 | `missing_prereq_count` | required items for current waypoint not yet held, /4 |
| 25 | `use_item_hint` | on `use_item` steps: id of the first tracked required item, /0x46 |
| 26 | `puzzle_macro_available` | 1 when the route step carries `macro_name` (e.g. `crow_gallery`) |

All from static route JSON — hints about *what*, never *how*.

## 4. `visited` — per-room 16×16 mask (PokeRL-style)

`spatial_encoder.VisitedMask`: allocentric grid anchored on the first pose
seen in each room this episode; 256 world-units per cell (grid spans
±2048). Cell under the player marked on reset and every step; reset per
episode. Not true walls — the cheap "where have I been" layer until the
RDT collision parser (Phase 4c) exists. `VisitedMask.update()` returns
cell-novelty, available to reward shaping later (not used yet).

## 5. RAM hunts (pending, tooling shipped)

| Hook | Tool | Wired obs slot |
|---|---|---|
| Enemy table (x/z/type/hp/state ×6 slots, 0x18C stride hypothesis) | `scripts/hunt_enemy_ram.py` | `spatial` enemies, `proprio[10]` |
| Interaction prompt | `scripts/hunt_interaction_prompt.py` | `proprio[11]` via `memory_map.INTERACTION_PROMPT` |
| SCD work flags | `scripts/hunt_scd_flags.py` → `data/scd_work_flags.json` | future: ungate puzzle/event items, room-clear reward |

## 6. Ablation plan (required before trusting the expansion)

- **A (baseline):** pixels + old-style obs.
- **B (privileged only):** zeroed/black frames, full spatial+goal+visited.
- **C (hybrid):** everything.
- Success: B reaches checkpoint 4+ faster than random; C ≥ A on the
  gallery-gated metric. Old checkpoints cannot consume the new dict —
  fresh runs or `scripts/transplant_widen.py`-style surgery.

## 7. Provenance & trust ladder

empirical RAM log > confirmed live RAM > RDT (after empirical validation)
> Evil Resource / walkthrough prose (confidence-tagged) > nothing.
PC addresses (ASL / CE tables) are hunt *seeds*, never shipped as PS1 truth.

Tests: `tests/test_spatial_encoder.py`, `tests/test_enemy_encoder.py`,
`tests/test_scaffolding.py` (goal hints), `tests/test_ppo_obs_compat.py`
(SB3 consumes the new dict).
