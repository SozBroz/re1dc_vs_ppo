# Finish-Line War Audit — RE1 Jill DC PPO

**Date:** 2026-07-17  
**Trigger:** World-almanac / `RE1WorldAwareExtractor` reshape + 126.6M graft  
**Method:** Parallel Composer 2.5 + Grok audits (architecture, puzzles, maps, fleet, docs/rewards, RAM, curriculum, combat, 30/60/90 strategy)  
**Doctrine:** [north_star.md](north_star.md) · [world_aware_nn_architecture.md](world_aware_nn_architecture.md) · awbw `re-exploration-rewards` skill

---

## Executive verdict

| Axis | Score | One-line |
|------|-------|----------|
| Architecture vs literature | **6/10** | Right hybrid (privileged + pixels + masks); capacity misallocated; MC not GAE; world path too thin |
| Map fidelity (Jill Standard DC) | **Fragile** | 1F/guardhouse/lab IDs mostly OK; 2F west / B1 / Courtyard Study / route JSON poison the almanac |
| Puzzle custom help | **~5% of any%** | Gallery + Kenneth + partial bar; rest is pixels |
| Combat trash | **Strong** | Knife/beretta/shotgun live; bosses/heavy weapons unvalidated |
| Fleet memory | **EmuHawk-bound** | Almanac obs delta negligible on wire; WH2 pagefile = 27×~900 MB emulators |
| Curriculum | **Prologue-only** | 9/53 route steps; no Guardhouse/Lab stages |
| Docs | **Dangerously stale** | Many still describe compass/macros as live |

**Strategic thesis:** Treat RE1 as Montezuma with a guidebook. Stabilize the graft, fix map truth, expand Gallery-pattern puzzle sensors, hunt SCD + interaction_prompt, then Go-Explore + BC. Do **not** add a learned room-order head or puzzle button macros.

---

## 1. NN / DRL health (literature)

**Sizing:** CNN 512 right · flatten 947 wasteful · world_context **64 undersized** · trunks 2×256 adequate.  
**Critical bug-shaped gap:** `file_*` / `combine_*` buffers registered but **unused in forward**.  
**Credit:** Prefer **GAE** over pure MC for long horizons (Schulman 2015).  
**Peers:** Go-Explore (Ecoffet 2019/2020), VPT BC→RL (Baker 2022), NLE masks (Küttler 2020), RND secondary (Burda 2018). Dreamer/MuZero/learned HRL = defer.

**Post-graft playbook:** freeze CNN → train world MLP → probe world_context → drop dead channels → GAE → Go-Explore lite → BC on hard rooms.

---

## 2. Map / almanac fidelity (must map RE 1996 DC)

### P0 defects
1. Unverified / disconnected rooms: `213`–`219`, `21A`–`21C`, `311` (no RDT door support from dining graph).
2. Courtyard Study unmapped; `119` = N/A but `10A→119` exists — likely Study; `doom_book_1` unmatched.
3. `119` one-way dead end (no return edge).
4. Route JSON Chris bleed (`sword_key`) + wrong rooms (`star_crest@107`, serum@102, etc.).
5. 170+ phantom RDT room codes pollute graph.

### Catalog poisons WorldCatalog will encode confidently
- `doom_book_2@305` disputed; medals as floor loot vs book contents.
- `mo_disc` first site collapses to `213` not `217`.
- `serum` / `map_1f` duplicates.
- `shield_key` notes still say “helmet doors” in places.

**Rule:** Fix room IDs + disputed pickups **before** trusting map-aware training. Add confidence / exclude unverified rows from tensors.

---

## 3. Puzzle / story custom-help gaps

| Severity | Obstacles |
|----------|-----------|
| **Crit** | Crest gate 11A, armor 205, hex crank / Enrico, medals/fountain/doom books, MO terminals 508, V-JOLT chain, poison gas / lab maze, Tyrant rocket event, `interaction_prompt` RAM, empty SCD flags |
| **High** | Piano bookcase, tiger jewel USE, square crank, Neptune drain, Plant 42, pool 345, private library 217, desk lockpicks (interact not USE) |
| **Working** | Gallery 117, Kenneth −3 gate, bar/dining emblem USE trio, combat macros, herb combine |

**`story_item_use_sites.json`:** only **3** sites — need ~15–25.  
**Cutscene ledger / milestones:** mansion-centric; zero lab/courtyard.  
**Route `crow_gallery` macro:** stale label; no solver (correct under north star).

---

## 4. RAM hunt campaign (finish-line blockers)

| Priority | Hunt | Why |
|----------|------|-----|
| P0 | SCD work flags → `scd_work_flags.json` | Unhides ~29 event/puzzle gates |
| P0 | `INTERACTION_PROMPT` | Desks, buttons, cranks, MO — “Press X” |
| P0 | `DOOR_FLAGS` bit→edge map | Open/locked in spatial |
| P1 | Enemy `type_id` | Hunter vs zombie |
| P1 | Poison verify | Yawn loop |
| P2 | `pickups_empirical` coords | ~70% items lack bearings |

---

## 5. Fleet / memory

| Finding | Action |
|---------|--------|
| Frame zlib dominates wire (~26 MB / 1024 steps / env); world_state +2.4 KB/step raw, ~0 on wire | Don’t optimize almanac wire first |
| WH2 dies on **27× EmuHawk ~900 MB** | Cap n_envs; stream epoch ingest |
| Each actor parses WorldCatalog JSON | Singleton / inject once per worker |
| NN 1523 vs 1336 | Immaterial vs emulation |
| GPU idle ~95% of wall time | Fix ingest / codec before bigger GPU |

---

## 6. Combat

| Ready | Not ready |
|-------|-----------|
| Knife, Beretta, Shotgun (+ aim-up evidence) | Bazooka/rocket/flame live combat validation |
| Enemy HP + x/z | Enemy type_id always 0 |
| Damage/kill rewards gated on combat action | Boss phases (Yawn, Plant 42, Neptune, Tyrant) |
| | Neptune absent from `room_enemies.json` |

---

## 7. Curriculum / actions

- Active curriculum ≈ route seq **1–9** of **53**; Guardhouse/Lab **untrained**.
- Stub `m0` has empty `route_steps`; no per-wing savestates in repo.
- Post-graft: shorter segments, temporary higher ent, **BC pipeline missing** (`train_bc.py` does not exist).
- Story USE mask only covers 3 sites → late game inventory USE mostly off.

---

## 8. Docs / rewards triage

**Fix now:** Sync `docs/exploration_rewards.md` from awbw skill; fix north_star shield≠helmet; amend north_star “current-room only” for static buffers; kill stale compass/macro docs or stamp SUPERSEDED.

**Reward health:** Shotgun loop, Kenneth gate, gallery clawback, cutscene anti-farm largely fixed. Open: herb/ammo repeat loops, box cutscene pay rule, heal chip loops.

---

## 9. Bold 30 / 60 / 90 (north-star clean)

### Days 0–30 — Recover graft, seal East Wing
Freeze further obs widens. Nightly wing evals. Freeze CNN / train world path. Go-Explore lite `(room, tile)` + `.State`. BC demos East Wing. Ablate dead channels. Combat gate before promote.

### Days 31–60 — Mansion + Guardhouse
Wing curricula with hard eval gates. Door open/locked. Gallery-pattern geometry for piano/crests/MO (sensors, not macros). Expand story USE sites + SCD. Planner-as-staff for stage selection only.

### Days 61–90 — Lab → Tyrant → Helipad
Lab constants as sensors. Boss competence gates. Best-of-N full-route eval. Optional **GPS amendment** only if mansion+guardhouse still stuck after archive+BC — never puzzle macros first.

**Victory:** ≥1 held-out completion without puzzle macros or default route compass.

---

## 10. Do this week (top 12)

1. Freeze architecture — no more obs reshape until recovery metrics.
2. Nightly East Wing + Gallery + combat eval harness.
3. Graft recovery schedule (freeze world MLP → unfreeze).
4. Wire `file_*` / `combine_*` into extractor forward **or** drop buffers.
5. Widen `WORLD_CONTEXT_DIM` 64→128/256.
6. Fix P0 map defects (`119`/Courtyard Study, disconnect 213–219, filter phantom RDT).
7. Patch route JSON (no `sword_key`; room conflicts).
8. Expand `story_item_use_sites.json` (crests, tiger, V-JOLT, cranks…).
9. Hunt `interaction_prompt` + start SCD campaign.
10. Go-Explore lite skeleton + 20% archive resets.
11. Record ≥2h clean `play_human` East Wing; scaffold `train_bc.py`.
12. Copy exploration-rewards skill into `docs/exploration_rewards.md`; supersede stale docs.

---

## Agent digest archive

Raw digests: `D:\re1_rl\_tmp_re1_audit\agent_digests\`  
(`drl_arch`, `puzzle_gap`, `maps_dc`, `fleet_mem`, `docs_reward`, `ram_hooks`, `action_curric`, `finish_plan`, `combat`)

---

## Plan file

Related plan: `c:\Users\phili\.cursor\plans\static_world_map_obs_57f02d1c.plan.md`
