# Memory Hooks & Observation Design — RE1 Jill Any% (Director's Cut)

**Project:** Deep RL for *Resident Evil* (1996), PS1 Director's Cut, serial **SLUS-00551**  
**Emulator:** BizHawk 2.11.1, Nymashock PSX core  
**Bridge:** Lua ↔ Python socket  
**Target:** Jill Any% → helipad escape  
**Hardware:** RTX 4070, solo Python dev  
**Endgame algorithm:** PPO (current phase: hierarchical hybrid — planner + learned navigator + scripted puzzle macros)

**BizHawk address convention:** PS1 bus address `0x80XXXXXX` → MainRAM offset = `addr - 0x80000000`.

---

## 0. Design thesis

We resolve the central tension by **separating sensors from actuators**:

| Layer | May use privileged RAM? | May script actions? |
|-------|-------------------------|---------------------|
| **Observation** (sensors) | Yes — "better eyes/ears" | No |
| **Reward / curriculum** | Yes — training signal only | No |
| **Planner** (high-level) | Yes — symbolic goals | Chooses *waypoints*, not button presses |
| **Puzzle / inventory macros** | Yes — finite scripted sequences | Yes — sanctioned automation |
| **Low-level policy** (navigation/combat) | Receives obs; never reads RAM directly for actions | **Must learn** — no lookup tables |

**One-sentence purity rule (preview):** *Privileged RAM may inform what the agent **knows** and what we **grade**, but only the learned policy may decide **which buttons to press** during navigation and combat.*

This is defensible DRL: the agent still learns \( \pi(a \mid s) \) and \( V(s) \) (or \( Q(s,a) \)) from experience. Privileged observations are standard in sim-to-real and Atari-with-RAM baselines; they change sample efficiency, not the definition of policy learning. What would defile DRL is using RAM to **select actions** in the nav/combat loop (e.g., hard-coded path to coordinates, auto-aim snap, inventory solver inside the policy forward pass).

---

## 1. Exhaustive memory-hook catalog

### 1.1 Confirmed anchors (use for pointer chains & save-diff)

| Symbol | PS1 address | Type | Notes | Status |
|--------|-------------|------|-------|--------|
| `PLAYER_HP` | `0x800C51AC` | u16 | Max 0x8C (140) Jill, **Original** difficulty | **CONFIRMED** |
| `PLAYER_HP_ADV` | `~0x800B8BC6` | u16 | Advanced mode alternate | **CONFIRMED** (mode split) |
| `GAME_TIMER` | `0x800C867C` | u32 | Global play timer | **CONFIRMED** |
| `LAB_TIMER` | `0x800C867A` | u16/u32 | Lab section timer | **CONFIRMED** |
| `DOOR_FLAGS` | `0x800C86B4` | u32 bitfield | Door unlock state | **CONFIRMED** (exists) |
| `ITEM_BOX_BASE` | `0x800C8724` | 2 B/slot | `(id, qty)` per slot; code uses 16 slots today | **CONFIRMED** base; **NOTE 2026-07-12 (deferred):** live dump shows **48** slots contiguous to `INVENTORY_BASE` (`0x800C8784`); UI scroll can park items past index 15 |
| `MAPS_FILES_FLAGS` | `0x800C8714` | bitfield | Map pickup flags | **CONFIRMED** |
| `PLAYER_X` / `PLAYER_Y` / `PLAYER_Z` | `0x800C5158/5C/60` | s16 | world units; ~64–162/frame walking | **CONFIRMED** (verify_pos.py walk trace) |
| `PLAYER_FACING` | `0x800C5198` | u16 | 0–4095 angle (0x1000 = full circle) | **CONFIRMED** |
| `STAGE_ID` / `ROOM_ID` / `CAM_ID` | `0x800C8660/61/62` | u8 | from RE1-Autosplitter GOG map | **CONFIRMED** (boot logs) |
| `CHARACTER_ID` | `0x800C8669` | u8 | 0=Chris, 1=Jill | **CONFIRMED** |

### 1.2 Full hook catalog

**How to find (general recipes):**

1. **Anchor-relative scan:** With BizHawk Lua, freeze `PLAYER_HP` or `GAME_TIMER`, change one game quantity (enter new room, take damage, open inventory), search MainRAM for increased/decreased/equals patterns. Narrow with repeated actions.
2. **Save-state diff:** Two saves in same room differing by one event (pick up item, kill one zombie, open one door). XOR or bytewise diff MainRAM (`0x00000000`–`0x001FFFFF`); repeat across 3+ pairs to isolate stable fields. RE1 save data often mirrors live RAM regions — cross-check [biohazard-utils](https://github.com/seedhartha/biohazard-utils) RE1 PS1 structures.
3. **SCD / work-flag table:** Per-room event bits documented on biohazard-utils wiki. Search for bit flips when triggering known events (emblem pickup, crest placement, boss death). Anchor: room entry then single event.
4. **Enemy table:** Kill one enemy in empty-ish room; diff RAM. Repeat with second enemy type. Look for contiguous structs (x, y, z, type id, HP, state byte).
5. **Room ID:** Transition across 5–10 known distinct rooms with fixed timer; search for monotonic or enumerated u16/u8 that changes only on room load. Validate with door round-trip (A→B→A).

| Hook | Est. address / location | Dtype | Find recipe | Primary routing | Status |
|------|---------------------------|-------|-------------|-----------------|--------|
| **Room / stage ID** | Unknown; often near map/flag cluster `0x800C86xx` | u16 or u8 | Room-hop scan anchored on `GAME_TIMER` freeze; save-diff between Mansion 1F hall vs dining room | **OBS** (embedding), **CURRICULUM**, **REWARD** (potential), **TELEMETRY** | **UNKNOWN** — must search |
| **Player X** | `~0x800C8784` +0 | s16 or fixed-point | Move along one axis only; narrow search | **OBS**, **REWARD** (progress proxy), **TELEMETRY** | **LIKELY** |
| **Player Y** | +2 or +4 | s16 | Same, Y-only movement | **OBS**, **TELEMETRY** | **LIKELY** |
| **Player Z** | +4 or +8 | s16 | Stairs / elevation changes | **OBS**, **TELEMETRY** | **LIKELY** |
| **Player facing / rotation** | +8..+12 | u16 angle or u8 dir | Rotate in place without translation | **OBS** | **LIKELY** |
| **Player HP** | `0x800C51AC` | u16 | Confirmed | **OBS**, **REWARD** (delta), **CURRICULUM** (death reset), **TELEMETRY** | **CONFIRMED** |
| **Poison / virus / status** | Near HP block or status byte `~0x800C51xx` | bitfield/u8 | Inflict poison (Yawn / blue herb test); diff | **OBS**, **REWARD** (penalty), **ACTION_MASK** (heal macro trigger) | **UNKNOWN** |
| **Equipped weapon** | Inventory-adjacent `~0x800C87xx` | u8 id | Switch weapon in inventory | **OBS**, **TELEMETRY** | **UNKNOWN** |
| **Ammo (handgun)** | Per-weapon counters near inventory | u8/u16 | Fire N shots; search decreased-by-N | **OBS**, **REWARD** (waste penalty, optional), **TELEMETRY** | **UNKNOWN** |
| **Ammo (shotgun, grenades, etc.)** | Same table | u8/u16 | Per-weapon fire tests | **OBS**, **TELEMETRY** | **UNKNOWN** |
| **Inventory slots (on-person)** | Near `ITEM_BOX_BASE` or `0x800C8700` region | 2 B × N slots | Pick up / drop / combine items | **OBS**, **REWARD** (pickup diff), **PLANNER**, **TELEMETRY** | **LIKELY** (layout) |
| **Item box contents** | `0x800C8724` | 2 B/slot | Deposit/withdraw | **PLANNER**, **REWARD** (deposit milestones), **TELEMETRY** | **CONFIRMED** base |
| **Item ID table** | N/A (lookup) | enum | Known 0x01–0x46 | **PLANNER**, decode hooks | **CONFIRMED** |
| **Key items bitmask** | Flag region / inventory | bits | Pick up key; diff | **OBS**, **PLANNER**, **REWARD** | **UNKNOWN** |
| **Enemy array base** | Room-loaded struct heap | array | Kill-one-enemy diff in single-enemy room | **OBS** (count + summary), **REWARD** (kill diff), **TELEMETRY** | **UNKNOWN** |
| **Enemy slot: active/alive** | per-slot byte | u8 | Kill enemy; slot flag 1→0 | **OBS**, **REWARD** | **UNKNOWN** |
| **Enemy slot: type/id** | per-slot | u8 | Different enemy types | **OBS** (embedding), **TELEMETRY** | **UNKNOWN** |
| **Enemy slot: X/Y/Z** | per-slot | s16×3 | Enemy movement / corpse | **OBS** (relative offsets), **TELEMETRY** | **UNKNOWN** |
| **Enemy slot: HP** | per-slot | u16 | Damage enemy without kill | **OBS**, **REWARD** (damage shaping) | **UNKNOWN** |
| **Enemy count (active)** | Derived or global | u8 | Room clear | **OBS**, **REWARD** | **UNKNOWN** |
| **Room clear flag** | SCD work-flag | bit | Clear room of enemies; event fires | **REWARD**, **CURRICULUM**, **TELEMETRY** | **UNKNOWN** (table location) |
| **Door lock flags** | `0x800C86B4` | bitfield | Unlock door from key side | **OBS**, **PLANNER**, **ACTION_MASK** (macro doors) | **CONFIRMED** exists |
| **Puzzle / event flags** | SCD flag table per room | bits | Each puzzle step once | **REWARD**, **CURRICULUM**, **PLANNER**, **TELEMETRY** | **UNKNOWN** (per-bit map) |
| **Cutscene active** | Game mode byte | u8 | Trigger cutscene; search stable "inactive control" pattern | **CURRICULUM** (pause policy), **ACTION_MASK**, **TELEMETRY** | **UNKNOWN** |
| **Menu / UI mode** | Status screen, inventory, map | u8 enum | Open/close inventory | **CURRICULUM**, **ACTION_MASK** | **UNKNOWN** |
| **Text box / message active** | Dialog state | u8 | Talk to Rebecca / read file | **CURRICULUM**, **ACTION_MASK** (auto-advance macro) | **UNKNOWN** |
| **Player in control** | Composite or mode byte | bool | Inverse of above + door anim | **CURRICULUM**, **ACTION_MASK** | **UNKNOWN** (derive) |
| **Aiming / ready stance** | Player state | u8 | Hold aim button | **OBS**, **TELEMETRY** | **UNKNOWN** |
| **Interaction prompt** | "Press X" flag | u8 | Stand by door/item | **OBS**, **PLANNER** (macro trigger), **TELEMETRY** | **UNKNOWN** |
| **Damage this frame** | HP delta or flag | u8 | Take hit once | **REWARD**, **TELEMETRY** | Derive from **HP** diff |
| **I-frames / invuln** | Status | u8 | Post-hit flash | **OBS** (optional), **TELEMETRY** | **UNKNOWN** |
| **Camera ID / fixed cam index** | Per-room camera | u8/u16 | Move to trigger camera swap | **OBS** (critical for pixels), **TELEMETRY** | **UNKNOWN** |
| **Door transition state** | Loading flag | u8 | Use door | **CURRICULUM**, **ACTION_MASK** | **UNKNOWN** |
| **Game timer** | `0x800C867C` | u32 | Confirmed | **REWARD** (time penalty optional), **TELEMETRY**, **CURRICULUM** | **CONFIRMED** |
| **Lab timer** | `0x800C867A` | u16 | Confirmed | **TELEMETRY**, segment curriculum | **CONFIRMED** |
| **Map files flags** | `0x800C8714` | bitfield | Pick up map | **REWARD**, **PLANNER** | **CONFIRMED** |
| **Save count / scenario** | Save metadata | — | New game vs continue | **CURRICULUM** only | Low priority |
| **Difficulty mode** | Global | u8 | Original vs Advanced | **CURRICULUM** (lock Original) | **CONFIRMED** split |
| **RNG seed** | Global | u32 | TAS comparison | **TELEMETRY** only — do not feed policy | **UNKNOWN** |

---

## 2. Routing decision matrix (summary)

**Legend:** OBS = observation vector; policy may consume. REWARD_ONLY = shaping/terminal only. CURRICULUM = reset/segment/goals. PLANNER = high-level symbolic layer. ACTION_MASK = legal buttons / pause stepping. TELEMETRY = overlay + logging only.

| Category | Route | Justification |
|----------|-------|---------------|
| Position, facing, room ID, camera ID | **OBS** | Egocentric navigation needs state; still must learn motor mapping (tank controls). |
| HP, ammo, inventory, equipped weapon | **OBS** | Combat resource management is learnable; not action-prescriptive. |
| Enemy summaries (count, relative positions, types) | **OBS** (compressed) | Better than parsing sprites; agent still learns when to shoot/dodge. |
| Aiming stance, i-frames, interaction prompt | **OBS** (small) | Legitimate game state; avoids pixel-only ambiguity. |
| Event flags, door flags, puzzle completion | **REWARD_ONLY** + **CURRICULUM** + **PLANNER** | Progress signal & waypoint graph; not needed every frame in obs if goal-conditioned. |
| Item pickup / enemy kill (inventory & HP diffs) | **REWARD_ONLY** | Potential-based shaping (Ng et al., 1999). |
| Room ID on reset | **CURRICULUM** | Savestate library indexing (Go-Explore, Ecoffet et al., 2019). |
| Cutscene / menu / text / door anim | **ACTION_MASK** + **CURRICULUM** | Pause policy learning; run skip macros — not obs (would leak "can't act" into policy inputs unnecessarily if gating is external). |
| Waypoint selection, key-item needs | **PLANNER** | Symbolic — outputs goal embedding, not D-pad. |
| Puzzle solutions (shield, bee, piano) | **PLANNER → macro** | Sanctioned scripting; finite enumerated puzzles. |
| Optimal path coordinates | **Never → policy** | Would defile DRL if fed as action targets. |
| RNG seed | **TELEMETRY** | Reproducibility; feeding it enables stochastic memorization. |

---

## 3. Recommended observation vector (low-level nav/combat policy)

**Design principles (named practices):**

- **Privileged sensing** — RAM augments pixels (common in Procgen, DMlab, and "Atari RAM" baselines).
- **Goal-conditioned RL** (Schaul et al., 2015; Andrychowicz et al., 2017) — planner injects goal without scripting motion.
- **Egocentric + allocentric split** — relative enemy offsets + room embedding (allocentric) stabilizes fixed-camera switches.
- **Frame stacking** — 4× grayscale or RGB downsampled frames for short-term motion (Mnih et al., 2015).

### 3.1 Visual channel

| Field | Shape | Dtype | Normalization | Rationale |
|-------|-------|-------|---------------|-----------|
| `frame_stack` | `(4, 84, 84)` or `(4, 96, 96)` | uint8 → float32 | `/255` | Keep pixels: fixed-camera disambiguation (same room geometry looks different per camera angle), enemy telegraph animations, environmental hazards (fire, crimson head). Symbolic RAM alone misses visual threat cues. CNN trunk (Impala or Nature DQN) → 256-d embedding. |

**Alternative ablation (video transparency):** train with RAM+minimal pixels (e.g., 64×64 center crop); report pixel-ablation as honesty segment.

### 3.2 Proprioceptive / privileged state vector

Concatenate to `state_vec` (dim ≈ 120–200 before embeddings). All float32 unless noted.

| Field | Dim | Encoding | Normalization |
|-------|-----|----------|---------------|
| `hp` | 1 | scalar | `/140` |
| `status_flags` | 4 | poison, fine, danger, etc. one-hot or multi-hot | binary |
| `equipped_weapon_id` | 1 | int → **embedding** (8-d) | learned emb table size ~20 |
| `ammo_vector` | 6 | per-weapon counts (handgun, shotgun, mag, fuel, explosive, knife) | `/max_clip_or_inventory_cap` |
| `inventory_compact` | 24 | 12 slots × (item_id_emb 0-d → use 12×4-d multi-hot top-K ids) OR 12 normalized ids `/0x46` | bounded |
| `player_x`, `player_y`, `player_z` | 3 | allocentric within room | `(x - room_min) / room_span` per-room bounds table |
| `facing` | 2 | sin/cos angle | unit circle |
| `room_id` | 8 | **embedding** (not one-hot — ~120 rooms) | `nn.Embedding(128, 8)` |
| `camera_id` | 4 | embedding | helps pixel fusion |
| `enemy_summary` | 40 | top-5 enemies: each (rel_x, rel_y, rel_z, type_emb 4-d, hp_norm, alive) × 5 | relative to player; pad empty slots with 0 |
| `enemy_count` | 1 | scalar | `/10` cap |
| `aiming` | 1 | binary | 0/1 |
| `interaction_prompt` | 1 | binary | 0/1 |
| `i_frames` | 1 | binary | 0/1 |
| `goal_waypoint` | 8 | from planner: waypoint embedding or (Δx, Δy, Δz, room_id_match, priority) | see §3.3 |
| `goal_room_id` | 8 | embedding (shared table with room_id) | planner output |

**Total (approx):** CNN 256 + state ~100 + goal 16 → fusion MLP → policy + value heads.

**Why not pure symbolic?** RE1's fixed cameras break Markovian pixel-free state: same `(x,y)` can imply different visuals and different threats. RAM+goal without pixels trains faster but generalizes poorly to visual cues (crimson heads, hunter leaps). Hybrid is the Pareto point for sample efficiency *and* defensible "the agent still sees the game."

### 3.3 Goal-conditioning without scripting movement

**Planner output (slow timescale, every 1–5 s or on room change):**

- `goal_room_id` — target room along shortest path in known graph.
- `goal_waypoint` — doorway coordinate or interactable anchor in current room (symbolic graph node).
- Encoded as **egocentric offset** `(Δx, Δy)` to waypoint + `room_match` bit.

**Policy learns:** tank-control motor skills to reduce egocentric error and survive en route. Planner never outputs "press Up+Right for 12 frames."

This is **hierarchical RL** (options / feudal RL, Dayan & Hinton, 1993; Nachum et al., 2018) with a **non-learned high-level** initially (hand-authored graph), later optionally learned from flags.

### 3.4 Egocentric vs allocentric

| Signal | Frame | Use |
|--------|-------|-----|
| Enemy offsets | Egocentric | Combat reactions |
| Player xyz | Allocentric (room-local) | Navigation across camera cuts |
| Goal Δx, Δy | Egocentric | Unified control target |
| Room / camera id | Allocentric context | Disambiguate |

### 3.5 Room ID encoding

Use **learned embedding** (dim 8–16), not one-hot (~100+ rooms with variants). Add **room graph distance** to goal as optional scalar feature (planner-computed, changes slowly) — legitimate potential-based hint in obs (like GPS distance), not a path script.

---

## 4. "In-control" gating & auto-skip

**Problem:** Acting during cutscenes, menus, text, door loads produces garbage transitions and wastes env steps (major sample-efficiency leak).

### 4.1 Detection signals (priority order)

1. **Composite `in_control` byte** (search target) — single hook if found.
2. **Derived gate:** `in_control = NOT (cutscene OR menu OR text OR door_load OR inventory OR map_screen)`.
3. **Fallback heuristics:** input ignored counter (press D-pad, RAM unchanged for N frames); HP/timer frozen; full-screen overlay pixel detector (last resort).

### 4.2 Runtime behavior

```
each env step:
  if not in_control:
    do NOT call policy.forward()
    do NOT append to replay buffer (or mark "null transition" masked out in loss)
    execute skip macro (mash X, Confirm, shoulder skip if DC supports)
    fast-forward emulator (frame-skip burst, throttle off) until in_control
    optional: curriculum snapshot on room-stable frame after transition
  else:
    policy selects action at decision Hz
```

**RL practice:** This is **action masking** + **temporal abstraction** (options). Masked transitions excluded from replay — equivalent to "only learn from decision states" (Sutton & Barto Ch. 13).

### 4.3 Auto-skip macros (sanctioned, not policy)

| Mode | Macro | DRL impact |
|------|-------|------------|
| Text box | Mash `X`/`Confirm` at 10 Hz until text clear | None on policy — outside learning |
| Cutscene | Skip combo if available; else fast-forward | Same |
| Inventory (planner-driven) | Scripted item use/combine | Planner layer only |
| Door transition | Hold `X` + walk vector from macro until room_id changes | Macro selects pre-door pose; walk-in still learned or scripted per hierarchy choice |

**Transparency overlay:** show `GATED: CUTSCENE` in red when policy is paused.

### 4.4 What the policy must never see during gating

Do not record (s, a, r, s') tuples where `a` is a skip-macro action attributed to the policy. Prevents **off-policy contamination**.

---

## 5. Reward-shaping design

### 5.1 Philosophy

Use **potential-based reward shaping** (PBRS): \( F(s,s') = \gamma \Phi(s') - \Phi(s) \) preserves optimal policies under standard conditions (Ng, Harada, Russell, 1999).

Define potential \( \Phi \) from privileged state:

- \( \Phi_{\text{room}} \) = negative graph distance to planner goal room (precomputed on mansion graph).
- \( \Phi_{\text{item}} \) = sum of key-item flags acquired × weight.
- \( \Phi_{\text{puzzle}} \) = event-flag milestones for current segment.

### 5.2 Reward terms (dense + sparse)

| Term | Source | Formula / trigger | Weight |
|------|--------|-------------------|--------|
| **Room progress** | `room_id` change | PBRS on graph distance | Medium |
| **Waypoint reach** | position vs planner waypoint | one-time bonus + PBRS | Medium |
| **Item pickup** | inventory diff | `+w` per new key item id | High (sparse) |
| **Event flag** | SCD bit flip | `+w` milestone | High |
| **Enemy kill** | enemy HP → 0 or count↓ | `+w_kill` | Medium |
| **Damage taken** | ΔHP < 0 | `w_dmg * ΔHP` | Medium penalty |
| **Ammo waste** | optional Δammo while no hit | small penalty | Low (tune carefully) |
| **Death** | HP = 0 | `-W_death` terminal | High |
| **Segment complete** | helipad, Tyrant, etc. | large sparse | Very high |
| **Time** | ΔGAME_TIMER | `-w_t` per decision step | Low (optional) |

### 5.3 Keep OUT of reward (reward hacking / deceptive gradients)

| Do NOT reward | Why |
|---------------|-----|
| **Raw coordinate progress toward wall** | Bypasses doors/puzzles; encourages clipping |
| **Standing on interaction prompt without correct item** | Farmable |
| **Menu open/close** | No-op loops |
| **Saving game** | Meta-exploit |
| **Map screen idle** | AFK farming |
| **Damage dealt without threat reduction** | Chip reward on invuln enemies |
| **Negative-only shaping on timer** without completion bonus | Policy learns to die fast |
| **Per-frame constant alive bonus** | Dominates signal |
| **Inventory rearrangement** | Accidental diffs |
| **Helipad without prior Tyrant flag** | Sequence break unless curriculum allows |

### 5.4 Terminal rewards

- **Helipad escape** (Any% end): `+R_win` only when `event_helipad_escape` flag set.
- **Game over:** `−R_death`.

### 5.5 Reward routing vs obs

Event flags in **REWARD_ONLY** (via PBRS) even if also in planner — avoids policy directly memorizing bitfield tables while still shaping. Optionally add `flags_delta` summary to obs (last-K bits changed) as ablation.

---

## 6. Speed levers ranked by ROI

| Lever | Est. impact | Implementation notes | DRL-purity caveat |
|-------|-------------|----------------------|-------------------|
| **1. In-control gating + auto-skip** | **High** | Cuts ~30–60% wasted steps in RE1 | **Clean** — masks non-decision states |
| **2. Savestate curriculum resets** | **High** | Per-room/per-segment `.state` library; reset to frontier | **Clean** — standard Go-Explore / curriculum |
| **3. Parallel emulator instances** | **High** | 8–16 BizHawk headless workers on 4070 (CPU-bound; GPU for batch infer) | **Clean** |
| **4. Frame-skip / action-repeat @ 5–10 Hz** | **High** | RE1 reacts on ~100 ms scale; decide every 6–10 frames @ 60 FPS | **Mild** — loses fine motor timing; acceptable for nav |
| **5. BC warm-start (human + TAS demos)** | **High** | Behavioral cloning on (obs, action) from gated in-control frames only | **Clean** if actions are human, not scripted bot |
| **6. Go-Explore archive** | **High** | On new `room_id`, save state to archive; prioritize rare rooms | **Clean** — exploration aid |
| **7. Reduced obs preprocessing** | **Med** | 84×84 grayscale, fused RAM vector, small CNN | **Clean** |
| **8. Headless / no audio / throttle off** | **Med** | BizHawk `turbo` + mute | **Clean** |
| **9. Shaped reward (PBRS)** | **Med** | Faster than sparse-only | **Clean** if potential-based |
| **10. Distillation from planner-guided rollouts** | **Med** | DAgger optional | **Mild** — distribution shift if planner weak |
| **11. PPO vs current algo** | **Med** (later) | After replay quality good | **Clean** |
| **12. Pixel-only ablation** | **Low** (for speed) | Slower training | **Prestige / purity benchmark** |

**Recommended decision Hz:** **10 Hz** (every 6 frames at 60 FPS) for navigation; **15–20 Hz** in boss/combat segments via segment-specific config.

**Parallelism target:** 8 envs × batch inference on 4070; expect CPU bottleneck — prefer lightweight Lua bridge, local sockets, avoid per-frame PNG where possible (raw framebuffer or small JPEG).

---

## 7. Honesty / purity rubric ("rules I gave myself")

Use on-camera as a scored checklist:

### Tier A — Legitimate (green)

1. **Better sensors:** RAM in observation = GPS + health monitor, not autopilot.
2. **Curriculum:** Resets to learned frontiers; no action selection.
3. **PBRS:** Reward from potentials; proven not to alter optimal policy under assumptions.
4. **Goal conditioning:** Planner says *where*; policy learns *how*.
5. **Puzzle macros:** Finite human-known puzzles outside nav/combat skill.
6. **Gating:** Don't learn from cutscenes = excluding non-Markov noise.

### Tier B — Gray (disclose prominently)

1. **Planner with full game graph** — strong prior; still learn low-level if planner only sets waypoints.
2. **Combat aim-assist in macro** — if any; should be minimal or learned.
3. **TAS BC bootstrap** — disclose demo source.
4. **Action-repeat** — coarse timing; still learned proportions.

### Tier C — Defiling (red line — do not ship)

1. **RAM → direct button output** in nav/combat (coordinate PID to target tile).
2. **Auto-aim snap** from enemy RAM in policy forward pass.
3. **Inventory solver inside policy** (optimal herb combine each frame).
4. **Room-to-room teleport actions** in action space.
5. **Lookup table policy** per room_id.

### Hierarchy line (quotable)

> **The planner picks the next waypoint; the macros solve discrete puzzles; the neural network presses the buttons to walk, aim, and shoot. If RAM tells the network which button to press, we've cheated.**

### Transparency overlay (recommended fields)

`room_id | hp | goal | gated? | reward Δ | planner | purity tier`

---

## 8. Open questions & risks (blocking rank)

| Rank | Question / risk | Blocks | Mitigation |
|------|-----------------|--------|------------|
| **P0** | `ROOM_ID` address unknown | Curriculum, PBRS room potential, graph | Save-diff campaign first week |
| **P0** | `in_control` / UI mode bytes | Sample efficiency, replay quality | Derive from input-echo + pixel fallback |
| **P0** | Player position struct layout | Nav obs, waypoint error | Verify `0x800C8784` with axis scans |
| **P1** | SCD event flag table map | Milestone rewards, Jill Any% sequence | Mine biohazard-utils; systematic event diffs |
| **P1** | Enemy array layout | Combat obs, kill rewards | Per-room kill diffs |
| **P1** | BizHawk headless stability @ 8+ instances | Throughput | Process pool; restart watchdog |
| **P2** | Camera ID address | Pixel-RAM fusion | Pixel-only camera classifier fallback |
| **P2** | Advanced vs Original HP split | Wrong reads if misconfigured | Lock Original; assert max HP 140 |
| **P2** | Director's Cut skip differences | Cutscene timing | Version-specific macro table |
| **P3** | Legal/ROM distribution | Publishing video | Local-only ROM; don't bundle |
| **P3** | Crimson head / RNG-heavy fights | Variance | Curriculum order; optional damage cap shaping |

---

## 9. Missing specs (needed next)

These are not memory hooks, but they **define the MDP** the hooks feed. Without them, parallel envs, BC, and PPO will disagree on what a step is.

### 9.1 Episode boundary

**Problem:** “Episode” means different things for curriculum segments, death, and full Any% evaluation. Mixing them corrupts advantage estimates and BC labels.

| Mode | `reset()` loads | `terminated` | `truncated` | Next episode |
|------|-----------------|--------------|-------------|--------------|
| **Segment curriculum** (default training) | Stage `init_savestate` from `curriculum/*.json` | `HP = 0` or planner `segment_complete` | `step >= max_steps` | Same stage savestate (optionally Go-Explore archive slot) |
| **Death in segment** | Same stage savestate | `terminated=True`, `info.death=True` | — | Immediate `reset()` to **same** stage unless `advance_on_death: false` in stage JSON |
| **Segment success** | Next stage savestate or frontier pool | `terminated=True`, `info.segment_complete=True` | — | Curriculum index += 1 |
| **Full Any% eval** (held-out) | New-game or fixed TAS start state | Helipad escape flag **or** death | Wall-clock / step cap (e.g. 200k frames) | No mid-run curriculum teleport; log full trajectory |

**Rules:**

1. **Training default:** one curriculum JSON = one episode distribution. Death → reset to **that** stage’s `init_savestate`, not mansion start.
2. **PPO rollout:** treat `segment_complete` and `death` both as terminal; bootstrap value from reset state, not from a savestate loaded mid-trajectory without a full `reset()`.
3. **Gated time excluded:** steps while `not in_control` do **not** increment `step` toward `max_steps` or per-episode timers (see §4).
4. **Eval metric:** report **segment success rate** and **cumulative Any% progress** separately — do not average them.

**Stage JSON additions (proposed):**

```json
{
  "advance_on_death": true,
  "episode_mode": "segment",
  "on_success": "next_stage"
}
```

### 9.2 Savestate determinism

**Problem:** BizHawk `.state` load + Lua `frameadvance` count changes RAM, RNG, and animation phase. Inconsistent warmup makes BC and parallel workers see different worlds from the same file.

**Pinned environment (record in every dataset manifest):**

| Field | Value |
|-------|-------|
| BizHawk version | **2.11.1** (match header) |
| Core | **Nymashock** (PSX); document if fallback to Octoshock |
| Serial | **SLUS-00551**, Original difficulty, Jill |
| Lua script | `lua/re1_client.lua` git SHA |
| Turbo / throttle | Off for recording; configurable for training |

**Load contract (canonical):**

```
load_savestate(path)
frameadvance(WARMUP_FRAMES)   # default WARMUP_FRAMES = 1 (current env.py)
read_ram + screenshot         # first obs = post-warmup
```

**Requirements:**

1. **Freeze `WARMUP_FRAMES`** per stage; bump only with a new savestate file suffix (e.g. `105_dining_v2.state`).
2. **After load, assert invariants** before first policy call: `HP == expected`, `room_id == expected` (once hook exists), `in_control == true` (or run gate macro first).
3. **RNG:** log first 4 bytes at a chosen seed address (when found) on reset; fail reset if mismatch across workers.
4. **Re-record savestates** when BizHawk major version or core changes — do not assume binary compatibility.
5. **Path:** absolute paths from Python; Lua `savestate.load` must receive the same path the manifest lists.

**Acceptance test:** 10 consecutive `reset()` calls on one stage → identical `(room_id, hp, pos)` and identical first-frame hash after warmup.

### 9.3 Bridge bandwidth (vision path)

**Problem:** §6 mentions avoiding per-frame PNG; the live bridge (`bizhawk_bridge.py` / `re1_client.lua`) uses **base64 PNG over TCP JSON**. At 8 envs × 10 Hz × 320×240 RGB, encode + socket dominates before the GPU sees a batch.

| Option | Payload | Est. bytes/step | Pros | Cons |
|--------|---------|-----------------|------|------|
| **A — PNG base64** (current) | JSON + PNG | ~30–80 KB | Simple, works today | CPU hell at scale |
| **B — Raw RGB565/RGB888** | Length-prefixed binary blob | ~150 KB (320×240×2) or ~230 KB ×3 | No PNG encode; fast decode | Custom protocol; larger wire size if uncompressed |
| **C — Lua JPEG** | base64 JPEG q=80 | ~8–20 KB | Drop-in JSON RPC | Lossy; extra encode on Lua side |
| **D — Downsample in Lua** | 84×84 RGB raw | ~21 KB | Matches obs size | Still binary framing needed |
| **E — RAM-only steps** | No frame on gated/macro steps | ~0 | Huge win with §4 gating | Policy never sees cutscene frames (intended) |

**Decision (v1):**

- **Training:** **D + E** — Lua returns **84×84 RGB888 raw** (binary side channel or hex in JSON for prototype); **skip screenshot** when `not in_control` and during fast-forward bursts.
- **BC recording:** **A or C** at full res optional for human review; training consumes downsampled D.
- **Parallelism:** one BizHawk process per env; target **≤25 KB/step** average on wire before scaling past 4 workers.

**Protocol note:** add `cmd: "screenshot", format: "rgb888", w: 84, h: 84` alongside existing PNG for backward compatibility.

### 9.4 BC data contract (`frame_skip` alignment)

**Problem:** `env.step(action)` holds buttons and advances `frame_skip` frames (default 8). The `(s, a)` pair is ambiguous unless obs and action share the same time index.

**Canonical step (decision-time indexing):**

```
s_t     = obs after previous step (or reset warmup)
a_t     = policy action chosen from s_t
hold a_t for frame_skip emulator frames
s_{t+1} = obs read after frameadvance(frame_skip)
```

| Field | Rule |
|-------|------|
| **BC label `a`** | Action chosen at **`s_t`** (start of bundle) |
| **BC input** | **`s_t`** frame stack + RAM — **not** `s_{t+1}` |
| **Next row** | `(s_{t+1}, a_{t+1})` — no overlap leak of future frames into current label |
| **Gated steps** | **No BC rows** — macros are unlabeled |
| **Decision Hz** | `60 / frame_skip` at 60 FPS (e.g. skip 6 → 10 Hz) |

**Demo ingest (human / TAS):**

1. Downsample human recordings to decision Hz by taking every Nth frame **after** aligning button edges to frame boundaries.
2. Store `frame_skip`, `decision_hz`, and `label_phase: "pre_hold"` in dataset manifest.
3. **Reward is not stored in BC** — BC is `(obs, action)` only; PPO may add reward later from replay.

**Sanity check:** train BC → run greedy policy → action distribution KL vs demo should be < threshold on **same** `frame_skip`; mismatch usually means end-of-bundle labeling.

### 9.5 Boss segment overrides

**Problem:** §6 sets 10 Hz nav / 15–20 Hz combat but does not wire overrides to curriculum or env config. Boss fights (Yawn, Tyrant, etc.) need different MDP parameters without forking the codebase.

**Stage JSON extensions:**

```json
{
  "segment_profile": "boss",
  "frame_skip": 4,
  "decision_hz": 15,
  "obs_profile": "combat",
  "reward_profile": "combat",
  "action_mask_profile": "combat"
}
```

| Profile | `frame_skip` | Obs deltas | Reward deltas | Action mask |
|---------|--------------|------------|---------------|-------------|
| **`nav`** (default) | 6–8 (~10 Hz) | Standard §3.2 | PBRS room + waypoint | Block interact without prompt (optional) |
| **`combat`** | 3–4 (~15–20 Hz) | Enforce full `enemy_summary`; add `aiming`, `i_frames` | Enable `kill` / `damage_taken` terms; **disable** room PBRS | Allow fire; optional block fire at ammo=0 |
| **`boss`** | 3–4 | Same as combat; optional **drop** `goal_waypoint` coord hints | Sparse phase milestones only (flag bits); reduce dense room shaping | No interact mask (prompt timing matters) |
| **`puzzle`** | gated | Minimal — macro owns stepping | Event-flag sparse only | Policy **not invoked** (`macro_only: true`) |

**Runtime:** `RE1Env` reads profile from active curriculum stage on `reset()`; profiles are **not** learned — they are curriculum metadata.

**Eval:** boss segments reported separately in metrics (`tyrant_phase_reached`, `deaths`, `time_to_kill`); do not merge into mansion nav learning curves.

---

## Appendix A — Action space & masking (low-level)

**Actions (discrete, 18–24):** D-pad 8-way, aim+walk combos (optional factored: move × aim × fire), stand turn L/R, knife, fire, reload, interact (X), run (optional), quickturn if mapped.

**ACTION_MASK hooks:**

- Block all during `not in_control`.
- Block fire when ammo = 0 (optional — can let policy learn waste).
- Block interact when no prompt (reduces spam).

**Do not mask** movement toward wrong walls — let reward teach.

---

## Appendix B — Planner interface (symbolic layer)

**Inputs:** inventory, key flags, door flags, item box, event flags, room graph.  
**Outputs:** `goal_room_id`, `goal_waypoint_id`, `macro_request` enum (none, solve_puzzle_X, use_item_Y).  
**Frequency:** event-driven + 2 s heartbeat.

Planner may read all RAM. Planner **must not** output button presses to the env except by invoking a **named macro** with success criteria checked against RAM.

---

## Appendix C — Recommended phased rollout

1. **Week 1:** Find `ROOM_ID`, position block, `in_control`; build gated step loop.
2. **Week 2:** Mansion segment curriculum + PBRS room potential; BC warm-start.
3. **Week 3:** Hybrid obs + PPO/IMPALA; parallel envs.
4. **Week 4+:** Go-Explore archive; expand to lab/helipad; reduce privileged features ablation for video.

---

*Document version: 1.1 — internal design / video seed. See `re1_rl/` for skeleton implementation.*
