# Non-Pixel Observation Catalog — RE1 Jill PS1 (SLUS-00551)

**Purpose:** Single reference for what the agent can observe today, what new signals exist in RDT/static data but are not yet wired, which RAM hooks we read but do not expose, and what is still required for an agent to **beat the game in theory without relying on the 84×84×4 pixel stack**.

**Scope:** Jill, Director's Cut, **Standard** layout, any% route (`route_jill_anypct.json`). PS1 BizHawk bridge unless noted.

**Related docs:** `privileged_obs_spec.md`, `rdt_pipeline_feasibility.md`, `capture_session_runbook.md`, `item_gates.md`, `enemy_ram_hunt.md`.

---

## 1. Executive summary

| Layer | Status | Blind-play verdict |
|-------|--------|-------------------|
| **Pixels (`frame`)** | 84×84×4 uint8, 4-frame stack | Optional for learning; not required if privileged stack is complete |
| **Proprio (20-d)** | Position, HP, facing, inventory count, room one-hot, control bit | **Partial** — enemy count and interaction prompt slots exist but read **zero** (addresses unset) |
| **Goal (27-d)** | Route planner, item TODO, gates, compass to next door | **Strong** for macro navigation; weak on puzzles, combat, and scripted macros |
| **Spatial (119-d)** | Egocentric items, enemies, exits in 16×16 grid | **Partial** — items/exits good where data exists; live enemies usually empty; 15/116 rooms have spawn coords |
| **Visited (256-d)** | 16×16 room-local exploration mask | **Good** for coverage; no global map memory |
| **RDT parse (198 rooms)** | 440 doors, 187 pickables, 129 spawns, 473 interactables, flag tests | **~40% wired** into obs; interactables, collision, camera, most slot IDs still unused |
| **RAM beyond DEFAULT_RAM_FIELDS** | Door flags, map flags, item box, timers, full inventory slots | **Read from RAM** in bridge but **not encoded** into obs (except inventory count + item names via diff) |

**Bottom line:** The agent can already learn **room-to-room routing and pickup targeting** without pixels, but cannot yet reliably **fight, interact, solve puzzles, or execute scripted sequences** from privileged obs alone. Closing that gap requires RAM hunts (enemies, prompts, event flags), wiring parsed interactables, empirical pickup calibration, and optional collision/nav meshes.

---

## 2. Current observation stack (934-d fusion)

Policy input = CNN(`frame`) ⊕ MLP(`proprio` + `goal` + `spatial` + `visited`). See `policy_config.py`, `env.py`, `obs_encoder.py`, `spatial_encoder.py`.

### 2.1 `frame` — 84 × 84 × 4 (uint8)

| Property | Detail |
|----------|--------|
| Source | BizHawk screenshot → resize → 4-frame stack |
| Blind substitute | Not needed if proprio + spatial + goal + visited are complete |
| Risk | CNN trunk was pretrained on pixels; ablation Train B/C (`privileged_obs_spec.md` §6) needed to prove pixel-free viability |

### 2.2 `proprio` — 20 (float32)

| Index | Field | Source | Notes |
|-------|-------|--------|-------|
| 0 | hp_norm | RAM `player_hp` / 200 | |
| 1 | hp_delta | vs previous step | |
| 2–4 | x, y, z | RAM position (s16) | Game units; y is height |
| 5 | facing_norm | RAM `player_facing` / 4096 | |
| 6 | inv_count_norm | decoded inventory slots / 8 | **Not** per-item one-hots |
| 7–9 | room one-hot prefix | `room_id` → index in `rooms.json` | Truncated encoding |
| 10 | enemy_count_norm | `len(state["enemies"])` | **Always ~0** — `ENEMY_TABLE_BASE = None` |
| 11 | interaction_prompt | RAM bit at `INTERACTION_PROMPT` | **Always 0** — address `None` |
| 12 | in_control | `game_mode & IN_CONTROL_MASK` | |
| 13–19 | reserved / padding | zeros | Headroom for future RAM fields |

**RAM read every step but not in proprio:** `game_timer`, `lab_timer`, `door_flags`, `maps_files_flags`, per-slot `inv_slot_0..7` (full item_id + qty), `character_id`, `cam_id`, `stage_id`, `room_id` (raw bytes used only to build room code).

### 2.3 `goal` — 27 (float32)

| Group | Fields | Source |
|-------|--------|--------|
| Route | room index, waypoint index, hops remaining, hop distance, in-target-room | `RoutePlanner` + `route_jill_anypct.json` |
| Compass | door Δx, Δz, distance, sin/cos bearing | `RoomGraph` next hop |
| Objective | 5-way one-hot (`pickup`, `door`, `use_item`, `puzzle`, `combat`) | Planner + item tracker |
| Curriculum | stage index norm | `curriculum.json` |
| Items | todo progress, items left, key items left, has required, wrong room, doors available, gated count, missing prereq count | `ItemTracker`, `room_items.json`, `item_gates` |
| Hints | use_item hint, puzzle_macro_available | Static tables + inventory |

**Not in goal today:** cutscene/script step index, Barry scene flags, puzzle state (statue positions, button presses), combat target selection, typewriter/save state.

### 2.4 `spatial` — 119 (float32)

Egocentric 16×16 cells, player-centered; each channel is distance-weighted presence in cell.

| Channel block | Dim | Source | Coverage |
|---------------|-----|--------|----------|
| Items (obtainable) | 37 | `item_positions.json` (RDT + manual merge) | **37 named** of **187** RDT pickables |
| Key items | 16 | same, filtered | subset of above |
| Enemies | 32 | Live RAM table **or** `StaticEnemySpawns` | Live: **broken**; static: **15 rooms** with x,z |
| Exits / doors | 34 | `RoomGraph` (empirical + RDT fallback) | **436** RDT edges + empirical |

Coordinate transform: RDT units → room-local grid via `spatial_encoder` (same scale as player x/z).

### 2.5 `visited` — 16 × 16 × 1 (float32)

| Property | Detail |
|----------|--------|
| Update | Each step, mark cell at player (x,z) in current `room_id` |
| Reset | Episode reset |
| Use | Exploration bonus, anti-loop; no cross-room memory |

---

## 3. New observations from RDT / static pipeline

Generated by `extract_rdt_from_disc.py` → `parse_rdt_scd.py` → `merge_rdt_into_data.py` → `build_item_positions.py`.

### 3.1 Parse totals (`rdt_extracted.json`)

| Asset | Count | In NN obs? |
|-------|-------|------------|
| Rooms parsed | 198 | indirect (room graph, spawns) |
| `DOOR_SET` doors | 440 → 436 edges in `doors_rdt.json` | **Yes** — `RoomGraph` fallback, spatial exits |
| `ITEM_SET` pickables | 187 | **Partial** — 37 merged to `item_positions.json` |
| `EM_SET` enemy spawns | 129 | **Partial** — 15 coords attached to `room_enemies.json` |
| Interactables (boxes, writers, triggers, messages) | 473 in 138 rooms → `rdt_interactables.json` | **No** |
| SCD `flag_tests` | thousands (per-room) | **No** — needs RAM flag addresses |
| SCA collision | per-room blobs | **No** |
| RVD camera zones | per-room | **No** |

### 3.2 `rdt_interactables.json` (not wired)

Sample kinds (138 rooms): `message` (~101), `trigger` (~57), `typewriter` (~6), `item_box` (~4), plus doors/items already split out.

**What an agent could get if wired:**

| Kind | Blind-play utility |
|------|-------------------|
| `typewriter` | Save-point proximity → risk management, ribbon economy |
| `item_box` | Storage routing (what to bank before puzzle rooms) |
| `trigger` | Script boundaries (Kenneth scare, statue puzzles, cutscene volumes) |
| `message` | Examine prompts, clue objects (less critical if route is fixed) |

**Suggested encoding:** 8–16 spatial channels (egocentric, same grid as items) + 1–2 proprio bits (“typewriter in range”, “box in range”).

### 3.3 `doors_rdt.json` + `doors_empirical.json`

| Source | Edges | Priority |
|--------|-------|----------|
| Empirical (human capture) | preferred when present | highest trust |
| RDT fallback | 436 | used when empirical missing |

**Gap:** Door **lock state** (emblem, keys, story locks) is not in obs — only static topology. Need `door_flags` RAM or per-door event flags.

### 3.4 `item_positions.json` / RDT pickables

| Metric | Value |
|--------|-------|
| RDT pickables parsed | 187 |
| Named / merged positions | 37 |
| Gap | 150 slots — zip-by-order heuristic unreliable; need slot→item map (BIOS tables, CE, or empirical pickup capture) |

**`pickups_empirical.json`:** pipeline ready (`capture_session.py`); **not yet populated** (≥20 pickups recommended to calibrate x/z scale).

### 3.5 `room_enemies.json` + static spawns

| Metric | Value |
|--------|-------|
| Rooms with enemy tables | 116 |
| Entries with RDT x,z | 15 |
| Live enemy obs | `decode_enemy_table` — needs `ENEMY_TABLE_BASE` |

Static spawns help **ambush awareness** for training priors; live table needed for **combat** (HP, state, position updates).

### 3.6 `room_items.json` + `item_gates.md`

| Metric | Value |
|--------|-------|
| Total item entries | 121 |
| Gated entries | 48 (16 puzzle, 15 item, 15 event, 2 trap) |

**Wired:** `gated_items_here`, `items_left_here`, `missing_prereq_count`, `use_item_hint`.

**Not wired:** SCD `flag_tests` that gate puzzles — `puzzle`/`event` gates with empty `requires` stay hidden until flags exist.

### 3.7 `route_jill_anypct.json`

| Property | Detail |
|----------|--------|
| Steps | ~70+ macro objectives |
| `action_type` values | `pickup`, `door`, `use_item`, `puzzle`, `combat`, `scripted_macro`, … |
| In obs | Planner compass + objective one-hot |
| Gap | **No step executor** — `scripted_macro` steps (Kenneth, Barry talks) need triggers or option macros |

### 3.8 Other static files

| File | Contents | In obs? |
|------|----------|---------|
| `rooms.json` | Room names, curriculum grouping | room index encoding |
| `curriculum.json` | Stage boundaries | goal curriculum field |
| `scd_work_flags.json` | Sparse worked flag names | hunt targets only |
| `pc_addresses.json` | 406 CE entries (mostly PC) | reference for PS1 hunts |

---

## 4. Memory hooks — full catalog

Addresses from `memory_map.py` (PS1 main RAM bus `0x80000000+`). “Bridge read” = in `DEFAULT_RAM_FIELDS` or env `_read_state`.

### 4.1 Confirmed and used in obs / logic

| Symbol | Address | Used for |
|--------|---------|----------|
| `PLAYER_HP` | 0x800C8690 | proprio, death |
| `PLAYER_X/Y/Z` | 0x800C8694–0x800C8698 | proprio, spatial, visited |
| `PLAYER_FACING` | 0x800C869C | proprio |
| `STAGE_ID`, `ROOM_ID` | 0x800C869E–0x800C869F | room code |
| `CAM_ID` | 0x800C86A0 | state dict only |
| `CHARACTER_ID` | 0x800C86A1 | state dict only |
| `GAME_MODE` | 0x800C86A2 | in_control |
| `INVENTORY_BASE` | 0x800C86A8 | inv count, item tracker, rewards |

### 4.2 Confirmed, bridge-read, **not** in observation vector

| Symbol | Address | Type | Why it matters for blind play |
|--------|---------|------|-------------------------------|
| `GAME_TIMER` | 0x800C868C | u32 | Rankings; optional pressure |
| `LAB_TIMER` | 0x800C8692 | u16 | Lab section pacing |
| `DOOR_FLAGS` | 0x800C86B4 | u32 | Which doors unlocked globally |
| `MAPS_FILES_FLAGS` | 0x800C8714 | u16 | Map pickup flags |
| `ITEM_BOX_BASE` | 0x800C8724 | 8×u16 | Box contents — planner/reward via diff only |
| Per-slot inventory | `INVENTORY_BASE + 0..0xE` | 8×(id,qty) | **Ammo, herbs, key items** — critical for combat/puzzle |
| `MESSAGE_FLAG` | 0x800C8665 | u8 | Cutscene/dialogue active — skip logic only today |

**Recommendation:** Add compact inventory encoding (8× item_id one-hot bucket or learned embedding) to proprio or a new 32-d `inventory` vector; add `door_flags` bitmask slice to goal.

### 4.3 Placeholders — env reads if set, currently **None**

| Symbol | Purpose | Hunt script |
|--------|---------|-------------|
| `ENEMY_TABLE_BASE` | Live enemy positions, HP, state | `scripts/hunt_enemy_ram.py` |
| `INTERACTION_PROMPT` | “Press X” examine/action | `scripts/hunt_interaction_prompt.py` |

Until found, proprio slots 10–11 and spatial enemy channels (live path) carry no signal.

### 4.4 Parsed SCD flag tests — not mapped to RAM

`rdt_extracted.json` per room: `flag_tests[]` with `flag_id`, `op`, `value`. Examples: puzzle completion, item picked, door opened.

| Need | Action |
|------|--------|
| Flag address table | `hunt_scd_flags.py` + `capture_session.py` correlation |
| Obs | 16–64-d “recent flag deltas” or per-room puzzle state |

Without this, **48 gated items** and many `puzzle`/`event` route steps are invisible to the agent until items appear in inventory.

### 4.5 Known gaps — not in `memory_map.py`

| Signal | Blind-play need | Typical approach |
|--------|-----------------|------------------|
| Aiming / knife / gun stance | Combat | Animation or input-state RAM |
| Equipped weapon | Combat | Inventory + “equipped index” |
| Ammo per type | Combat | Inventory slots or ammo globals |
| Poison / fine status | Survival | Status byte |
| Player damage invuln frames | Combat timing | Optional |
| Room enemy count (authoritative) | Combat | Enemy table |
| Puzzle actor positions (statues, cranks) | Puzzles | Object table or flags only |
| Cutscene / script PC | Macros | `game_mode`, `MESSAGE_FLAG`, triggers from RDT |

`pc_addresses.json` may hint PC-side names; PS1 offsets require verification per hunt doc.

---

## 5. What “beat the game without pixels” requires

Organized by game competency. “Have” = wired today; “Need” = missing or broken.

### 5.1 Navigation (room graph)

| Capability | Have | Need |
|------------|------|------|
| Know current room | ✓ proprio / state | — |
| Adjacent rooms | ✓ goal compass + spatial exits | — |
| Global route | ✓ planner | — |
| Local obstacle avoidance | ✗ | SCA collision → grid mask **or** privileged pathfinder |
| Wrong-door / locked door | partial topology | `door_flags` + per-edge lock metadata |
| Ladder / vertical links | partial in graph | empirical edge validation |

### 5.2 Exploration & pickups

| Capability | Have | Need |
|------------|------|------|
| Item locations (static) | ✓ 37 items | Remaining 150 RDT slots named |
| Obtainable vs gated | ✓ goal fields | Event flags for empty-require puzzles |
| Pickup confirmation | ✓ inventory diff | — |
| Interaction prompt | ✗ | `INTERACTION_PROMPT` RAM |
| Calibrated coordinates | partial | `pickups_empirical.json` |

### 5.3 Combat

| Capability | Have | Need |
|------------|------|------|
| Enemy positions | static only, 15 rooms | Live enemy table |
| Enemy HP / alive | ✗ | Enemy struct |
| Player ammo / weapon | inventory in RAM, not obs | Encode inventory + equipped |
| Aim / dodge timing | ✗ | Pixels or animation state |
| Safe routing | ✗ | Threat map from live enemies |

**Verdict:** Combat is the **largest blind-play hole**. Macro route can skip many fights; any% still requires boss kills and ammo management.

### 5.4 Puzzles & scripted events

| Capability | Have | Need |
|------------|------|------|
| Puzzle room identification | ✓ room + gated items | — |
| Puzzle internal state | ✗ | Flags and/or object positions |
| `scripted_macro` route steps | planner label only | Trigger volumes from RDT + flag completion |
| Cutscene skip | `MESSAGE_FLAG` + skip patch | Not obs — OK if engine skips |

### 5.5 Inventory & economy

| Capability | Have | Need |
|------------|------|------|
| Item acquired | ✓ tracker | — |
| Item locations in box | RAM at `ITEM_BOX_BASE` | Encode for planning |
| Ink ribbon / save | typewriter positions (data only) | Wire interactables + save heuristic |
| Key item use on door | `use_item` hint | Confirm use success via flags |

### 5.6 Terminal objective

| Capability | Have | Need |
|------------|------|------|
| Lab / Tyrant rooms in route | ✓ | — |
| Success room / timer | curriculum + route | Lab timer in obs optional |

---

## 6. Priority roadmap (privileged-only agent)

Ordered by leverage for **pixel-free** any% Jill.

### Tier A — Unblock existing tensor slots (high ROI)

1. **Enemy table RAM** → proprio[10], spatial enemy channels live.
2. **Interaction prompt RAM** → proprio[11]; enables examine/pickup without pixels.
3. **Inventory vector in obs** — 8 slots × (item_id norm, qty norm) from already-read RAM.

### Tier B — Static data completion

4. **`pickups_empirical.json`** — calibrate RDT x/z; expand trustworthy item layer.
5. **Slot→item mapping** for remaining 150 RDT pickables (datamine or systematic capture).
6. **Wire `rdt_interactables.json`** — typewriters, boxes, triggers into spatial channels.
7. **Attach remaining EM_SET coords** to `room_enemies.json` (129 spawns).

### Tier C — Story / puzzle state

8. **SCD flag RAM hunt** — map `flag_tests` to addresses; 16–32-d flag delta obs.
9. **`door_flags` / per-door state** in goal vector.
10. **Puzzle executor options** — high-level `scripted_macro` actions for top-N scripted steps.

### Tier D — Navigation fidelity

11. **SCA collision** → unwalkable mask in spatial or separate 16×16 binary channel.
12. **Camera zones (RVD)** — optional; helps explain control loss near room transitions.

### Tier E — Prove pixel-free learning

13. **Ablation** per `privileged_obs_spec.md` §6:
    - Train A: pixels only (baseline)
    - Train B: privileged only (zero `frame` or frozen zero CNN)
    - Train C: full fusion
14. **Metrics:** room coverage, pickup success, deaths per stage, route step completion without frame entropy.

---

## 7. Unused-but-available observation budget (sizing)

If expanding beyond 934-d:

| Candidate block | Dims | Source |
|-----------------|------|--------|
| Inventory slots | 16–24 | RAM `inv_slot_*` |
| Door / map flags | 8–16 | RAM bitmasks |
| Flag deltas | 16–32 | RAM hunt |
| Interactables spatial | 16–32 | `rdt_interactables.json` |
| Collision grid | 256 | SCA per room |
| Script / cutscene phase | 4–8 | `game_mode`, `MESSAGE_FLAG` |
| Ammo & weapon | 8–12 | RAM |

Policy `policy_config.py` would need MLP input resize + transplant script (pattern established in `transplant_privileged_obs.py`).

---

## 8. Data regeneration

```powershell
cd D:\re1_rl
venv\Scripts\python.exe scripts\extract_rdt_from_disc.py
venv\Scripts\python.exe scripts\parse_rdt_scd.py
venv\Scripts\python.exe scripts\merge_rdt_into_data.py
venv\Scripts\python.exe scripts\build_item_positions.py
```

Human capture (empirical doors/pickups):

```powershell
venv\Scripts\python.exe scripts\capture_session.py --help
```

Checkpoint after obs shape change:

```powershell
venv\Scripts\python.exe scripts\transplant_privileged_obs.py --backup-src
```

---

## 9. File index

| Path | Role |
|------|------|
| `re1_rl/env.py` | Obs dict assembly, RAM read |
| `re1_rl/obs_encoder.py` | proprio + goal |
| `re1_rl/spatial_encoder.py` | spatial + visited |
| `re1_rl/memory_map.py` | Addresses, `DEFAULT_RAM_FIELDS`, `ITEM_IDS` |
| `re1_rl/room_graph.py` | Doors empirical + RDT |
| `re1_rl/rdt_parser.py` | SCD walker |
| `data/rdt_extracted.json` | Full parse |
| `data/rdt_interactables.json` | Interactables (unwired) |
| `data/doors_rdt.json` | Door edges |
| `data/item_positions.json` | Merged item coords |
| `data/room_items.json` | Logical items + gates |
| `data/room_enemies.json` | Enemy design + sparse coords |
| `data/route_jill_anypct.json` | Macro route |
| `docs/privileged_obs_spec.md` | Original spec (update §2.1 RDT status) |

---

## 10. Stale doc note

`privileged_obs_spec.md` still lists RDT ingest as “Phase 1b future” in places — **implementation is live** as of the RDT pipeline merge. Treat **this catalog** as the authoritative inventory of wired vs unwired signals until that spec is reconciled.

---

*Last updated: 2026-07-04 — post RDT merge, privileged obs transplant, 934-d fusion training.*
