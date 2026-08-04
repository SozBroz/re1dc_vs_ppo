# Guidebook & Episode History — Implementation TODO

**North star:** [`docs/north_star.md`](north_star.md)  
**Audit date:** 2026-07-08  
**Policy:** Check items off when shipped to training obs. If skipped, mark **Won't do** or **Deferred** and write **why** in the Status column.

**Legend:** `[ ]` pending · `[x]` done · `[-]` won't do / deferred

**Architecture note:** New obs keys need a **checkpoint transplant** or fresh PPO run (`scripts/transplant_widen.py` or successor). Batch Tier A into one widen pass when possible.

---

## Current baseline (audit summary)

| Signal | In obs today | Gap |
|--------|--------------|-----|
| Room pickup ids (≤8) | `spatial` | ~70% lack coordinates; event gates hidden |
| On-person inventory | count only | No item ids held |
| Key-item affordances | none | No `item_affordances.json` |
| Enemy signature | HP count only | No positions/types in spatial |
| Door exits | bearing/dist | No open/locked; `door_flags` read but discarded |
| Episode history | `rooms_visited` set | No order, cutscenes, or milestones |
| Goal compass | live in Yawn rails | Allowed only by explicit goal-conditioned curriculum contract |

---

## Tier A — High ROI, minimal blocking hunts

*Guidebook identity + history basics. No scripted path.*

### Implementation rank (2026-07-08) — what we can do **now**

All items below use existing RAM reads, `ItemTracker`, `ProgressTracker`, `room_enemies.json`, `room_items.json`, `item_gates.md`, and `rooms.json` — **no new hunts**.

| Rank | ID | Do now? | Blocker |
|------|-----|---------|---------|
| **1** | A5 | ✅ Agent | Room deque — env-only, ~16 dims |
| **2** | A6 | ✅ Agent | Pickup log — `ItemTracker.update()` deltas |
| **3** | A7 | ✅ Agent | Key bitmask — `ever_held` + `room_items` key_item flags |
| **4** | A1 | ✅ Agent | Inventory obs — copy `encode_box` pattern from live inv RAM |
| **5** | A4 | ✅ Agent | Enemy roster — aggregate `room_enemies.json` by type |
| **6** | A9 | ✅ Agent | Tests — ship alongside encoders (partial per item) |
| **7** | A2 | 🟡 Agent drafts, **you verify** | Auto-build v1 from gates + route; gaps on key→**door** mapping |
| **8** | A3 | ✅ Agent after A1+A2 | Affordances obs encoder |
| **9** | A8 | 🟡 Agent scripts, **you restart fleet** | Transplant + policy_config; invalidates current ckpt arch |

**Recommended build order:** A5 → A6 → A7 → A1 → A4 → (A2 draft) → A3 → A9 → A8.

**Needs imperator (cannot fully close without you):**

| ID | What we need from you |
|----|------------------------|
| **A2** | Spot-check `data/item_affordances.json` v1 — especially key→room lists (shield/helmet/armor keys, emblem→`10F`, doom books→fountain). We can derive ~80% from repo data; Evil Resource nuance and disputed rows in `item_gates.md` need human eyes. |
| **A8** | Decision to **tear down fleet + transplant + resume** (or fresh run). Code is automatable; GPU time and checkpoint choice are yours. |
| *(not Tier A)* | **B1/B3/C3** — live play / `capture_session` for SCD flags, door bits, empirical pickups. |

| Status | ID | Task | Est. | Notes / why not |
|--------|-----|------|------|-----------------|
| `[x]` | A1 | **`inventory` obs key** — 8×(`item_id`, `qty`) normalized, mirror `box` layout | S | Shipped 2026-07-08; +16 dims |
| `[x]` | A2 | **`data/item_affordances.json`** — key item → rooms (+ door edges when known); Evil Resource + `item_gates.md` | M | `scripts/build_item_affordances.py` |
| `[x]` | A3 | **`affordances` obs block** — per held key item: top-N room indices / affordant-here bit in current room | M | `re1_rl/item_affordances.py` |
| `[x]` | A4 | **Static room enemy roster** — from `room_enemies.json`: type counts for current room (no RAM) | S | Shipped 2026-07-08; `room_enemies` +12 dims |
| `[x]` | A5 | **Rolling room deque** — last K=32 `(room_idx, steps_since)` in `history` | S | Shipped 2026-07-08; 65 dims (was spec'd 6–8, imperator chose 32) |
| `[x]` | A6 | **Pickup acquisition log** — last K=4 `(item_id, room_idx)` on `ItemTracker` delta | S | Shipped 2026-07-08; `acquisitions` +9 dims |
| `[x]` | A7 | **Ever-held key bitmask** — ~32 key items → compact float vector | S | Shipped 2026-07-08; `keys_held` +37 dims |
| `[x]` | A8 | **Obs widen + transplant** — wire A1,A3–A7 into `observation_space`, `policy_config`, transplant script | M | `transplant_guidebook_obs.py`; fusion 1243; fleet restarted |
| `[x]` | A9 | **Tests** — encoder roundtrip, obs dict shape, north-star path-leakage guards | S | + `test_keys_held`, guidebook encoder tests |

---

## Tier B — Runtime state & richer guidebook

*Needs flag hunts or moderate engineering. Unlocks hidden gates + door truth.*

| Status | ID | Task | Est. | Notes / why not |
|--------|-----|------|------|-----------------|
| `[ ]` | B1 | **`scd_work_flags.json` campaign** — capture_session / hunt_scd_flags for mansion milestones (Barry, emblem, crests…) | L | Stub `data/scd_work_flags.json` seeded; hunt via `scripts/hunt_scd_flags.py` |
| `[ ]` | B2 | **`flags` obs vector** — sparse named bits from B1 (+ live RAM read each step) | M | Depends B1; unhide `item_gates` event rows |
| `[ ]` | B3 | **Door-edge bit map** — correlate `DOOR_FLAGS` bits to `doors_rdt` edges | L | Required before open/locked means anything |
| `[ ]` | B4 | **Exit `open` / `locked` dims** — +1–2 per spatial exit slot from B3 | M | Depends B3 |
| `[x]` | B5 | **Cutscene ledger obs** — milestone bits for `room:cam` keys (Kenneth, Barry dining, Barry 2F return…) | M | `re1_rl/cutscene_ledger.py` |
| `[x]` | B6 | **`rdt_interactables` in spatial** — nearest 2 typewriter/box/trigger: kind + bearing | M | `re1_rl/rdt_interactables.py`; spatial 128-d |
| `[ ]` | B7 | **Show event-gated pickups** — when B2 bit set OR prereq held: surface in spatial with `gated` | M | Depends B2 |
| `[x]` | B8 | **Derived milestone features** — `milestones` obs from deque + ledger + keys | M | `re1_rl/milestone_features.py` |

---

## Tier C — Hunts & data collection (longer pole)

| Status | ID | Task | Est. | Notes / why not |
|--------|-----|------|------|-----------------|
| `[ ]` | C1 | **`interaction_prompt` RAM hunt** → `proprio[11]` live | M | Tool exists; address unmapped |
| `[ ]` | C2 | **Enemy RAM hunt** — x/z/type on `ENEMY_TABLE_BASE` | L | HP-only today; bearings useless |
| `[ ]` | C3 | **`pickups_empirical.json` logging run** — grow `item_positions` from 37 → coverage | L | `log_door_transitions` / capture_session |
| `[ ]` | C4 | **RDT item position validation** — merge tier after empirical cross-check | M | `rdt_pipeline_feasibility.md` trust ladder |
| `[ ]` | C5 | **Ablation A/B/C** — pixels-only vs privileged vs hybrid on dining→gallery curriculum | L | `privileged_obs_spec.md` §6; after A8 |
| `[x]` | C6 | **`maps_files_flags` in obs** — which maps obtained | S | `re1_rl/maps_files.py`; 16-bit RAM field |

---

## Deferred / Won't do (unless north star amended)

| Status | ID | Item | Reason |
|--------|-----|------|--------|
| `[-]` | D1 | Re-enable `encode_goal()` compass / waypoint index | **Path leakage** — north star forbids during exploration |
| `[-]` | D2 | Puzzle macros (piano, crow, MO terminal…) | **Defiles DRL** — policy must learn interact puzzles |
| `[-]` | D3 | Nav macros / auto-walk door graph | **Forbidden actuation** |
| `[-]` | D4 | Full ordered log of all 116 rooms | **Too wide + redundant** with deque + set |
| `[-]` | D5 | Per-step action n-gram history | **Noise** — not human scratch-pad semantics |
| `[ ]` | D6 | Expand box RAM magic beyond box rooms | **Owner exception is narrow** — needs explicit amendment |

---

## Suggested execution order

```
A1 → A2 → A3 ─┐
A4 ────────────┼→ A8 (transplant) → fleet retrain → C5 ablation
A5 → A6 → A7 ─┘
B1 → B2 → B7
B1 → B3 → B4
A5 + B5 → B8
C1, C2, C3 in parallel with training when bandwidth allows
```

**First sprint (imperator approval):** A1, A2, A5, A6, A7 — inventory + affordances data + history deque/log without RAM hunts.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-08 | Initial tracker from north-star audit; history elevated to Tier A |
| 2026-07-08 | A7 + A8 transplant; fusion 1243; fleet restart from 6.76M guidebook resume |
