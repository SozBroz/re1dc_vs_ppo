# RE1 Director's Cut — North Star

**Project:** Deep RL for *Resident Evil* Director's Cut (PS1, SLUS-00551), Jill, Standard layout  
**Terminal algorithm:** PPO (MaskablePPO today)  
**Mission:** A learned policy that **completes the game** — mansion through escape — without scripted navigation or puzzle solvers.

**Read this before** obs changes, reward shaping, new macros, curriculum design, or privileged-RAM hunts. Companion detail: `docs/memory_hooks_and_observation_design.md`, `docs/privileged_obs_spec.md`, `docs/item_gates.md`.

---

## Fixed goal (do not drift)

**Beat RE1 Director's Cut via DRL.** The policy chooses movement, camera, interact, combat, and inventory menu actions. It must **learn** navigation and puzzles from pixels + privileged sensors — not follow a baked-in any% route.

When evaluating any proposal, ask:

1. Does the policy still have to **learn** which buttons to press for movement and puzzles?
2. Does it improve **sample efficiency** without handing the agent the solution path?
3. Does it stay faithful to the **Evil Resource mental model** (room guidebook + item affordances), not a walkthrough script?

---

## DRL purity — what is allowed

We follow the **sensors vs actuators** split (`docs/memory_hooks_and_observation_design.md` §0):

| Layer | Privileged RAM / static data? | Scripted button sequences? |
|-------|------------------------------|----------------------------|
| **Observation** | **Yes** — better eyes/ears | No |
| **Reward / curriculum** | Yes — grading only | No |
| **Low-level policy** (nav, interact, puzzles) | Receives obs only | **Must learn** — no autopilot |
| **Combat macros** | May read hooks for timing | **Allowed** — attack / knife swing phasing |
| **Box / inventory magic writes** | **Explicitly allowed** (see below) | N/A |

**One-sentence rule:** Privileged data may tell the agent **what the world is** and **what items mean**; only the policy may decide **how to move and solve** — except sanctioned combat macros and the box-room inventory exception.

### Allowed macros (combat & input phasing)

- Standing gun attack (`attack` action → weapon-specific macro)
- Crouch knife (`attack` / `knife_swing` → phased aim/swing/recovery)
- Short input holds where the engine requires multi-frame buttons (e.g. interact pulse) — **not** room traversal scripts

### Forbidden macros (navigation & puzzles)

- Auto-walk to coordinates or through door graphs
- Scripted puzzle solvers (crow gallery, piano, crest buttons, MO terminal, etc.)
- “Use item X on object Y” sequences that replace learning the interact loop
- Planner output that directly selects low-level actions during play

If a macro could be replaced by the policy learning `forward` / `turn` / `interact` / `use` over time, it does **not** belong in the actuation layer.

### Explicit exception — item box RAM writes

**Direct memory writes for item-box deposit/withdraw and inventory layout while in a box room are allowed** and are the one deliberate departure from “pure” environment interaction the project owner accepts today.

Rationale: menu navigation for box management is low-signal tedium, not the strategic skill we care about. This must **not** expand into general inventory solving, auto-combine, or teleporting items outside box rooms without an explicit north-star amendment.

---

## No scripted path

Humans speedrun with a **route**, but they do not receive a compass vector telling them the next room every frame.

Therefore:

- **`encode_goal()` stays zeroed** during exploration training — no waypoint index, door bearing to next checkpoint, or `items_left_here` in the goal channel.
- `data/route_jill_anypct.json` is for **human reference, eval, and optional reward experiments** — not a mandatory policy cheat sheet.
- Rewards may use sparse discovery signals (new room, cutscene, damage) without encoding the full any% graph into obs.

Re-enabling goal/compass fields requires an explicit north-star change and an ablation plan.

---

## Evil Resource analogy — the guidebook, not the walkthrough

Humans use community references like [Evil Resource — Resident Evil](https://www.evilresource.com/resident-evil) as a **static world almanac**, not as GPS. We mirror that in privileged obs.

### 1. Room signatures (current room only)

**Like:** opening a map section and seeing what can appear in *this* room before you commit.

**Expose for the room the agent is in:**

| Signal | Source (today / target) | Notes |
|--------|-------------------------|-------|
| Pickups (id, key-item, gated, bearing) | `data/room_items.json` + `spatial` encoder | Per-item ids in up to 8 slots; not just counts |
| Enemy types / positions | Live RAM hunt → `spatial` enemy slots | Static spawn table as fallback |
| Interactables (box, typewriter, triggers) | `data/rdt_interactables.json` | Positions + kind |
| Door exits (bearing, distance) | `doors_empirical.json` / `doors_rdt.json` | Add **open/locked** when `DOOR_FLAGS` + bit map land |
| Gated-but-hidden pickups | `docs/item_gates.md` + SCD flags | Show when requirements are trackable |

**Do not** dump the entire mansion item/enemy table every step — only **current-room signature**, analogous to reading one Evil Resource room page.

### 2. Key-item affordances (what items are *for*)

**Like:** clicking [Shield Key](https://www.evilresource.com/resident-evil) on Evil Resource and seeing which doors it opens.

**Expose associations between inventory key items and where they apply:**

| Granularity | Example | Prefer when |
|-------------|---------|-------------|
| **Item → rooms** | `shield_key` → helmet-door rooms (`10D`, `117`, …) | Item-centric obs channel; “what can I do with what I hold?” |
| **Item → door edges** | `shield_key` → `105→10C` edge | Best if `DOOR_FLAGS` bit map exists per edge |
| **Room → required items** | `10F` secret passage → needs `emblem` + `music_notes` | Room-centric; complements spatial `gated` bit |

**Canonical examples to encode in data (Jill Standard):**

- `emblem` (wooden) → bar piano chain → `10F` secret alcove (`gold_emblem` swap)
- `gold_emblem` → dining fireplace `105` → reveals `shield_key`
- `shield_key` → helmet-key doors across mansion
- `lockpick` → locked desks (`102`, `111`, `401`, …)
- Crest / jewel / crank items → puzzle rooms listed in `docs/item_gates.md`

**Preferred data shape:** `data/item_affordances.json` (to build) — each key item id maps to `{rooms: [...], door_edges: [...], notes: "..."}` sourced from Evil Resource + `room_items.json` gates. Obs can include:

- For each held key item (or top-K in inventory): normalized room indices where it is relevant
- Optionally: for current room, which held items are **affordant here**

If door-level mapping is unknown, **room lists are good enough** — better than silence.

### Item-centric vs room-centric — default strategy

Use **both**, split by question:

| Question | Channel | Rationale |
|----------|---------|-----------|
| “What is in this room?” | **Room-centric** (`spatial`, room signature) | Matches Evil Resource room pages; hooks: `room_items.json`, RDT, live enemies |
| “What is this key for?” | **Item-centric** (inventory affordance block) | Matches Evil Resource item pages; static table + ever-held inventory |
| “Can I open this door **now**?” | **Runtime RAM** (`DOOR_FLAGS`, SCD work flags) | Static data knows prerequisites; RAM knows current unlock state |
| “What do I need before pickup works?” | **Room item `gate.requires`** | Already in `item_gates.md`; show `gated=1` when prereqs trackable |

Do **not** choose one encoding exclusively — room signatures and item affordances answer different questions.

---

## Episode progress history — fair game

Humans do not only know **what room they are in** and **what the guidebook says** — they remember **what they already did this run** when it matters: *I talked to Barry, went upstairs, haven't come back down yet; doors won't work until I trigger the return scene.*

**Episode-local history in obs is allowed** and encouraged. It is **not** a scripted path: it records **past facts**, not **where to go next**.

### What counts as “matters”

Prefer **discrete milestones** over raw action logs:

| Event type | Example | Why it matters |
|------------|---------|----------------|
| **Room entered** (ordered) | `105 → 104 → 105 → 106 → 203` | Return-from-2F patterns; backtracking intent |
| **Key item acquired** (ordered) | `emblem`, then `lockpick` | Gates and affordances change |
| **Cutscene / dialogue seen** | `104:kenneth`, `106:barry_return` | Story flags; doors unlock |
| **SCD / door flag flipped** | `dining_emblem_placed` | Runtime world state persistence |
| **Key item used** (if detectable) | `gold_emblem` on fireplace | Puzzle progress without macro |

**Avoid:** full button replay, per-step action n-grams, or history that encodes the any% **route index** (that is compass leakage).

### History vs compass (critical distinction)

| Allowed | Forbidden |
|---------|-----------|
| “I have visited room 203 this episode” | “My next waypoint is room 106” |
| “I picked up emblem before entering 10F” | “Door bearing to next checkpoint” |
| “Cutscene `106:cam3` already played” | `waypoint_index / total` from route JSON |
| “Last 6 rooms: …” | BFS hop distance to `success_room` |

### Implementation patterns (explore in priority order)

1. **Rolling room deque** — last K room ids + optional steps-since; cheap; captures go-up-then-down.
2. **Acquisition log** — last K pickups `(item_id, room_idx)` from `ItemTracker` deltas.
3. **Ever-held key bitmask** — compact “what I’ve obtained” without route order (complements log).
4. **Cutscene ledger** — sparse bits for milestone `room:cam` keys (reuse `ProgressTracker.rewarded_cutscenes` / reward keys).
5. **SCD flag vector** — named bits from `data/scd_work_flags.json` once hunted (persistent, not deque).
6. **Smarter milestones** (later) — derived events: `visited_2f_before_barry_return`, `has_lockpick`, from flags + deque + inventory.

`rooms_visited` (128 one-hot set) and `visited` (16×16 grid) are **weak history** — they lack order and milestone semantics. New work should add **ordered** and **event** channels.

### Intelligent filtering principle

When adding a history signal, ask: *Would a human jot this on a scratch note, or is it noise?*  
Prefer signals that **change affordances** (flags, key items, cutscenes) over signals that merely **change pose** (every door crossed).

---

## What we are not building

- A hard-coded any% bot with RL veneer
- Full autoplay through cutscenes beyond async skip (skip ≠ solve)
- Magic equip everywhere (weapon quick-switch RAM is a training convenience; keep it from becoming general “solve inventory”)
- Goal-vector compass that tells the agent the next checkpoint room during exploration training

---

## Decision framework for agents

Before implementing:

1. **Classify** — Observation (guidebook), **episode history**, reward, combat macro, or actuation (forbidden)?
2. **Guidebook test** — Would a human with Evil Resource open **one room page** or **one item page** — not a turn-by-turn route?
3. **History test** — Does this record **what happened**, without saying **what to do next**?
4. **Path test** — Does this tell the agent **where to go next** on the speedrun route? If yes, reject or gate behind an ablation flag.
4. **Purity test** — Does the policy still choose buttons for this skill? Combat macros and box RAM are the only broad exceptions.
5. **Data provenance** — Prefer `room_items.json`, RDT extracts, RAM hunts, and Evil Resource cross-checks over hand-wavy route notes.

### Anti-patterns

- Re-enabling full `goal` compass without owner sign-off
- Puzzle macros “just to unblock training”
- Hiding all gated items instead of showing `gated=1` with known prereqs
- Using `route_jill_anypct.json` step index as obs during exploration
- Expanding RAM magic beyond box-room inventory management

---

## Current implementation map

| North-star pillar | Status | Location |
|-------------------|--------|----------|
| Room pickup signatures (id, gated, bearing) | **Partial** | `re1_rl/spatial_encoder.py`, `data/room_items.json` |
| Room enemy signatures | **Blocked** on PS1 enemy RAM | `docs/enemy_ram_hunt.md` |
| Door open/locked | **Not in obs** | `DOOR_FLAGS` in `memory_map.py`; hunt ongoing |
| Key-item affordances | **In obs** | `data/item_affordances.json`, `re1_rl/item_affordances.py` |
| Episode progress history | **Deque + ledger + milestones** | `history`, `acquisitions`, `cutscene_ledger`, `milestones` |
| RDT interactables | **In spatial** | `rdt_interactables.json`, nearest box/typewriter/trigger |
| Map/file flags | **In obs** | `maps_files` u16 bitfield |
| Goal / checkpoint compass | **Disabled** | `obs_encoder.encode_goal()` → zeros |
| Combat macros | **Live** | `attack_macro.py`, `knife_macro.py` |
| Box inventory RAM | **Allowed** | `item_box.py`, magic deposit/withdraw actions |
| Story / SCD flags | **Sparse** | `scripts/hunt_scd_flags.py` → `data/scd_work_flags.json` |
| Static room/door topology | **Live** | `doors_rdt.json`, `RoomGraph` |
| Evil Resource alignment | **Source of truth for static tables** | [evilresource.com/resident-evil](https://www.evilresource.com/resident-evil) |
| **Implementation tracker** | Living doc | `docs/guidebook_obs_todo.md` |

---

## Related docs

| Doc | Role |
|-----|------|
| `docs/memory_hooks_and_observation_design.md` | RAM catalog, purity thesis (note: puzzle macros there are **superseded** by this north star) |
| `docs/privileged_obs_spec.md` | Field-level obs spec |
| `docs/item_gates.md` | Per-room pickup prerequisites |
| `docs/rdt_pipeline_feasibility.md` | RDT / SCD static extraction |
| `data/route_jill_anypct.json` | Human route reference — **not** policy compass |
| `docs/guidebook_obs_todo.md` | Prioritized obs/history work tracker |
| `.cursor/skills/re1-rl-north-star/SKILL.md` | Agent skill pointer to this file |

---

## Reporting template

When briefing progress against the north star:

```markdown
## North-star alignment
- Guidebook: [room signatures / item affordances / door state — what changed]
- Episode history: [what milestone signals added — deque / ledger / flags]
- Purity: [policy still learns X; macros/RAM exceptions unchanged or justified]
- Path leakage: [none / risk — describe]
- Evil Resource parity: [which tables updated]

## Blockers
- [RAM hunts, data gaps, architecture transplant for new obs keys]
```

---

*Document version: 1.1 — 2026-07-08. Adds episode progress history as allowed obs. Authoritative over older docs that allow puzzle macros or goal-compass exploration training.*
