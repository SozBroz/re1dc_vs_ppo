# NN Architecture & Observation Encoding — RE1 Jill Any% PPO

> **SUPERSEDED (2026-07-17):** For the current flat MaskablePPO stack, obs keys, and `RE1WorldAwareExtractor`, see [world_aware_nn_architecture.md](world_aware_nn_architecture.md). Training policy: [exploration_rewards.md](exploration_rewards.md). Purity doctrine: [north_star.md](north_star.md). This file remains a field-level archive of the pre–world-catalog encoder.

**Status:** implemented and live-verified 2026-07-02 (`env_smoke.py` against EmuHawk).
**Code of record:** `re1_rl/obs_encoder.py` (field specs), `re1_rl/env.py` (assembly), `re1_rl/reward.py` (shaping). If this doc and the code disagree, the code wins — the field lists below are transcribed from `PROPRIO_FIELDS` / `GOAL_FIELDS`, which drive both encoding and the human-readable tools.

**World-aware architecture (2026-07-17):** The agreed next step is flat MaskablePPO with `RE1WorldAwareExtractor` — frozen Evil Resource almanac in policy `register_buffer`s, small dynamic `world_state` / `key_hints` on rollouts, no learned room-order head. See **[world_aware_nn_architecture.md](world_aware_nn_architecture.md)** for the full diagram, buffer inventory, obs-key table, and transplant notes.

**Ammo / weapon card (2026-07-20):** Inventory and box slot qty, `weapon_card.equipped_clip`, and `last_attack` clip/spent fields share `AMMO_QTY_NORM = 255` (`re1_rl/weapon_damage.py`). Do not reintroduce `/15`. New always-on `weapon_card` (nominal damage, round type, acid/flame boss-*room* bonus flags) and one-step `last_attack` memory (cleared at the start of the next env step; includes 3-d height one-hot: attack_neutral / attack_up / attack_down — weapon from equip, not duplicated). Old checkpoints see a qty distribution shift and new flatten slices — transplant or fresh run at restart.

---

## 1. Full stack

```
route_jill_anypct.json (52 steps, items_gained, required_items, action_type)
        │
curriculum/m*.json (stage waypoint subset + init_savestate + max_steps)
        │
WaypointPlanner ──── ItemTracker (ever-held set, key-item TODO)
        │                   │
        │            RoomItems (data/room_items.json — pickups per room)
        │                   │
        ▼                   ▼
┌────────────────────────────────────────────────────────────┐
│ RE1Env (env.py)                                            │
│  BizHawk bridge → RAM read → symbolic state                │
│  ObsEncoder → {frame, proprio, goal}                       │
│  compute_reward → PBRS + sparse, per-term breakdown        │
│  _skip_uncontrolled → cutscenes/doors excluded from policy │
└────────────────────────────┬───────────────────────────────┘
                             │
              SB3 PPO MultiInputPolicy
              ┌──────────────┴──────────────┐
              │ NatureCNN(frame 84×84×4)    │
              │ MLP(concat(proprio, goal))  │
              │        → fused → π, V       │
              └─────────────────────────────┘
```

RAM ground truth: PS1 MainRAM via BizHawk Lua bridge (`memory_map.py`, all addresses live-confirmed). The policy never sees raw addresses or event-flag bitfields — only the normalized features below.

---

## 2. Observation space (`spaces.Dict`)

| Key | Shape / dtype | Range | Role |
|-----|---------------|-------|------|
| `frame` | 84×84×4 uint8 | 0–255 | what the agent **sees** |
| `proprio` | (20,) float32 | [-1, 1] | what the agent's **body** knows |
| `goal` | (27,) float32 | [-2, 2] | what the planner **wants** (GPS + TODO as numbers) |
| `spatial` | (119,) float32 | [-2, 2] | egocentric items / enemies / exits (`spatial_encoder.SPATIAL_FIELDS`) |
| `visited` | 16×16×1 float32 | 0–1 | per-room visited-cell plane (episode-local mental map) |

**2026-07-04 privileged-obs expansion:** goal grew 24 → 27 (usage hints: `missing_prereq_count`, `use_item_hint`, `puzzle_macro_available`); new `spatial` and `visited` keys. Full field-by-field spec with provenance lives in `docs/privileged_obs_spec.md`; this doc keeps the summary. Old checkpoints predate these dims and need a fresh run or transplant.

### 2.1 `frame` — vision

4 stacked grayscale frames, 84×84, channels-last (Atari recipe). Grayscale is deliberate: RE1's pre-rendered backgrounds carry almost no color signal, and it cuts conv input 3×. Frames are captured after `frame_skip` (8) emulated frames per env step; cutscene/door-transition frames are skipped entirely and never enter the stack.

### 2.2 `proprio` — body state (20 floats)

| Idx | Field | Source (RAM) | Normalization |
|-----|-------|--------------|---------------|
| 0 | `hp` | `PLAYER_HP` 0x800C51AC | /140 (fine ≥ 96, danger < 25) |
| 1 | `hp_delta` | hp − prev_hp | /20, clipped ±1 |
| 2 | `x_local` | `PLAYER_X` 0x800C5158 | (x mod 4096)/4096, room-local |
| 3 | `z_local` | `PLAYER_Z` 0x800C5160 | (z mod 4096)/4096 |
| 4 | `y_norm` | `PLAYER_Y` 0x800C515C | /1024 (elevation/floor) |
| 5 | `facing_sin` | `PLAYER_FACING` 0x800C5198 | sin(2π·facing/4096) |
| 6 | `facing_cos` | 〃 | cos(〃) |
| 7 | `room_index` | `STAGE_ID`+`ROOM_ID` → "SRR" code | table index /128 |
| 8 | `cam_id` | `CAM_ID` 0x800C8662 | /16 (fixed-camera cut id) |
| 9 | `in_control` | `GAME_MODE` bit 0x80 | 0/1 |
| 10 | `enemy_count` | `state["enemies"]` (0 until enemy RAM hunt lands) | alive /10 |
| 11 | `interaction_prompt` | `INTERACTION_PROMPT` (0 until prompt RAM hunt) | 0/1 |
| 12 | `character_id` | 0x800C8669 | 0 = Chris, 1 = Jill |
| 13 | `inv_count` | inventory slots occupied | /8 |
| 14–19 | `pad_1..6` | reserved | 0 |

### 2.3 `goal` — planner compass & TODO (27 floats)

| Idx | Field | Source | Normalization |
|-----|-------|--------|---------------|
| 0 | `goal_room_index` | `planner.next_waypoint_room()` | /128 |
| 1 | `waypoint_index` | planner cursor | /total waypoints |
| 2 | `waypoints_remaining` | 〃 | /total |
| 3 | `route_hop_distance` | BFS on door graph | /20, 1.0 if unknown |
| 4 | `in_target_room` | room == goal | 0/1 |
| 5 | `door_delta_x` | door_x − player_x | /4096, clip ±2 |
| 6 | `door_delta_z` | door_z − player_z | /4096, clip ±2 |
| 7 | `door_distance` | hypot(Δx, Δz) | /4096, clip [0,2] |
| 8 | `door_bearing_sin` | egocentric angle to door | + = door to the left |
| 9 | `door_bearing_cos` | 〃 | 1 = dead ahead |
| 10–14 | `obj_*` one-hot | route step `action_type` | navigate / pickup / use_item / fight / scripted_macro |
| 15 | `curriculum_stage` | stage json `stage_index` | /10 |
| 16 | `item_todo_progress` | ItemTracker ever-held | acquired/total (35 route items) |
| 17 | `items_left_here` | RoomItems − ever_held | /8 |
| 18 | `key_items_left_here` | 〃, key items only | /4 |
| 19 | `has_required_items` | prereqs ⊆ ever_held | 0/1 (ever-held proxy; item box not yet modeled) |
| 20 | `wrong_room_flag` | room unreachable to goal in door graph | 0/1 |
| 21 | `doors_available` | door table has exit toward goal | 0/1 |
| 22 | `gated_items_here` | pickups here locked behind progression | /4 ("ignore for now, come back") |
| 23 | `reserved_1` | future boss phase | 0 |
| 24 | `missing_prereq_count` | required items for current waypoint not yet held | /4 |
| 25 | `use_item_hint` | item id to use on a `use_item` step (first known required item) | /0x46, else 0 |
| 26 | `puzzle_macro_available` | route step has `macro_name` (e.g. `crow_gallery`) | 0/1 |

**Door compass** (idx 5–9, 21) comes from `data/doors_empirical.json` via `RoomGraph` BFS — populated by `scripts/log_door_transitions.py` during a human route playthrough. Until a room's exit is logged, the compass reads zero and `doors_available` = 0; graph features degrade to the flat "unknown" plateau.

**Item fields** (idx 16–19) are ever-held-gated: banking an item keeps it counted, re-grabbing is never "new". Item names are canonicalized (`item_todo.ITEM_ALIASES`) so route names, Evil Resource names, and RAM `ITEM_IDS` all agree. `items_left_here` counts only items obtainable *now*: 48 pickups carry progression `gate` metadata (puzzle/item/event/trap — see `docs/item_gates.md`); item-gates unlock when requirements enter the ever-held set, untrackable puzzle/event gates stay hidden, trap items still count.

### 2.4 Deliberately NOT in the observation

- Raw event-flag bitfields (reward-only; avoids memorization)
- Absolute door coordinates without egocentric deltas
- "Correct button" hints of any kind — compass yes, actions no
- RNG state

---

## 3. Action space

`Discrete(10)` — tank controls, held for `frame_skip` = 8 frames per step (Lua re-applies every frame):

| # | Name | Buttons |
|---|------|---------|
| 0 | noop | — |
| 1 | forward | up |
| 2 | back | down |
| 3 | turn_left | left |
| 4 | turn_right | right |
| 5 | run_forward | up + square |
| 6 | quickturn | down + square (DC) |
| 7 | interact | cross |
| 8 | aim | r1 |
| 9 | fire | r1 + cross |

---

## 4. Reward (per-term breakdown in `info["reward_breakdown"]`)

Total = Σ terms × `REWARD_SCALE` (0.1). Shaping gamma = 1.0 (plateaus contribute exactly 0).

| Term | Type | Value | Anti-exploit guard |
|------|------|-------|--------------------|
| `step` | per-step | −0.01 | — |
| `pbrs_graph` | PBRS (Ng et al. 1999) | γΦ′−Φ on −BFS hops | closed loops sum to 0 (tested) |
| `pbrs_door` | PBRS | 〃 on −door distance/4096 | 〃; capped at 1 room-span |
| `waypoint` | sparse | +2.0 | once per room per episode, max-progress hysteresis (`ProgressTracker`) |
| `wrong_room` | sparse | −1.0 | once per off-route room per episode, on transition only |
| `item` | sparse | +10.0 per item | ever-held gated (`new_items` from ItemTracker) — bank/re-grab pays nothing |
| `hp` | shaped | 0.05 × hp loss | — |
| `death` | terminal | −50 | requires previously-seen hp > 0 |
| `softlock` | timeout | −10 | every 500 stagnant steps |

---

## 5. Network

SB3 `MultiInputPolicy`, widened from defaults via `re1_rl/policy_config.py` (`POLICY_KWARGS`, used by both training scripts and `tests/test_ppo_obs_compat.py`):

- `frame` → NatureCNN (3 conv layers) → **512-d** (`cnn_output_dim=512`, Nature DQN width; SB3 default is 256)
- `proprio` ⊕ `goal` ⊕ `spatial` ⊕ `visited` ⊕ `box` → flattened, concatenated with CNN output (512 + 20 + 27 + 119 + 256 + 34 = **968-d** features). `visited` stays float32 0–1 on purpose — uint8 would trip SB3's `is_image_space` and NatureCNN cannot take 16×16 input.
- → **2×256** MLP trunk (separate pi/vf; SB3 default is 2×64) → policy head (10 logits) + value head

Rationale: the default 2×64 trunk was the narrowest point fusing vision with the compass, and throughput is emulator-bound (BizHawk), not GPU-bound — the extra width is wall-clock-free.

**Checkpoint caveat:** `PPO.load()` restores the architecture saved in the zip — checkpoints from before the widening keep 256/2×64 when resumed, and checkpoints from before the 2026-07-04 obs expansion (goal 24-d, no spatial/visited) cannot consume the new obs dict; both need a fresh run or transplant.

**Planned v1.1:** replace `room_index`/`goal_room_index` scalars with an `nn.Embedding(128, 8)` in a custom features extractor. Visited mask landed 2026-07-04; `enemy_count`/`interaction_prompt` are wired end-to-end but read zero until their RAM hunts land (see `docs/enemy_ram_hunt.md`).

---

## 6. Episode lifecycle

1. `reset()` — load stage `init_savestate`, fast-forward until `in_control`, fresh `ProgressTracker` + `ItemTracker` (starting inventory is absorbed, not rewarded).
2. `step(a)` — hold buttons 8 frames → auto-skip any uncontrolled span (mash cross/start, capped) → capture frame → read RAM → encode obs → reward.
3. Termination: death. Truncation: stage `max_steps`.
4. Every step's `info` carries: room, hp, pos, waypoint state, action name, reward breakdown, inventory, new items, item-TODO progress, items left in room, frames skipped, full symbolic state.

---

## 7. Human-readability tools (same specs, zero drift)

Every float slot is named in `PROPRIO_FIELDS`/`GOAL_FIELDS`; the tools decode from the same lists the encoder writes:

| Tool | Output |
|------|--------|
| `obs_encoder.format_obs_table(obs)` | console table: index, name, value, plain-English meaning for every slot |
| `scripts/watch_env.py` | live cv2 HUD: door compass needle, reward bars, planner + item TODO state; `--policy x.zip` to watch a trained agent |
| `re1_rl/telemetry.py` `EpisodeLogger` | JSONL per episode: action names, reward breakdown, positions, items |
| `scripts/plot_episode.py --latest` | PNG: per-room top-down trajectory + door stars + reward-term timeline |
| `scripts/route_todo.py` | key-item TODO checklist (35 items, prereq chains); `--rooms` adds per-room pickup lists |
| `scripts/log_door_transitions.py` | human plays; logs door coords AND empirical item pickups (ground truth vs room_items.json) |

---

## 8. Data files feeding the encoding

| File | Contents | Provenance |
|------|----------|------------|
| `data/route_jill_anypct.json` | 52 waypoints, items, action types | speedrun sources (WitchRain/alexfung) |
| `data/rooms.json` | 116 room codes → names | community map |
| `data/doors_empirical.json` | door coords per transition | measured in RAM (grows with logging pass) |
| `data/room_items.json` | pickups per room (122 items, 43 key; 48 carry `gate` metadata) | Evil Resource scrape, GameFAQs cross-check; gates per `docs/item_gates.md` |
| `data/pickups_empirical.json` | item→room ground truth | written during route playthrough |
| `re1_rl/memory_map.py` | all RAM addresses + `ITEM_IDS` | GameShark DBs + autosplitter linear map, live-confirmed |

Known open dispute: 9 route items whose room codes disagree with Evil Resource (documented in `room_items.json` `_unmatched`); the empirical pickup log adjudicates.
