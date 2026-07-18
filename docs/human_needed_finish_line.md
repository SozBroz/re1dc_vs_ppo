# Human-Needed Work — Finish Line

**Date:** 2026-07-17  
**Purpose:** Everything agents **cannot** finish without you (BizHawk, eyes, doctrine, fleet).  
**Counterpart:** Agents are executing automatable fixes in parallel on `feature/world-almanac-extractor`.

---

## How to use this doc

| Priority | Meaning |
|----------|---------|
| **H0** | Blocks map-aware training / any% truth — do before trusting the almanac graft |
| **H1** | Unlocks most puzzles / interact loop |
| **H2** | Sample efficiency / late game |
| **H3** | Doctrine / ops decisions only you own |

Each item lists: **why human**, **what to do**, **artifacts to produce**, **done when**.

---

## H0 — Map truth (BizHawk / your eyes)

### H0.1 Verify room hex IDs: `213`–`219`, `21A`–`21B`, `311`
- **Why human:** TapTalk-inferred IDs; no RDT door edges; agents must not guess.
- **Do:** Enter each room in-game (or from quicksaves), log `stage_id`+`room_id` → community code. Compare to `rooms.json` / `SOURCES.md`.
- **Produce:** Table in `data/SOURCES.md` or a new `data/room_id_verification.md`: `debug_name | claimed_hex | measured_hex | doors_ok`.
- **Done when:** Every claimed ID confirmed or renumbered; disconnected cluster fixed or removed from catalog.

### H0.2 Confirm `119` = Courtyard Study (+ return door)
- **Why human:** Helmet door `10A→119` exists; `rooms.json` says N/A; Study items unmatched.
- **Do:** Walk `10A` → helmet door → note room; walk back if possible; place `doom_book_1` / magnum if present.
- **Produce:** Rename in `rooms.json`, pickup rows, `119→*` edge note (or confirm one-way).
- **Done when:** Study is a named room with pickups; topology round-trips or one-way is documented.

### H0.3 Harvest / confirm doors for 2F west + mansion B1
- **Why human:** Empirical harvest needs playthrough transitions.
- **Do:** Run `scripts/log_door_transitions.py` (or capture_session) through library / elevator / basement path once doors exist.
- **Produce:** New edges in `doors_empirical.json` (or corrected RDT IDs).
- **Done when:** From dining `105`, BFS reaches library / shed / B1 rooms that exist in Standard Jill.

### H0.4 Resolve disputed placements (in-game spot-check)
| Claim | Options | Your call |
|-------|---------|-----------|
| `star_crest` | `117` (catalog/ER) vs `107` (route/GameFAQs Art Room) | One source of truth |
| `doom_book_2` | `305` vs `306` | ER/walkthrough |
| Medals | Desk/coffin floor loot vs book contents at fountain | Model + USE sites |
| `square_crank` | `11B` vs `305` | Pickup room |
| `mo_disc` first | `217` vs `213` | Primary for WorldCatalog |
| `serum` | `100` only vs also `102` | Drop duplicate |

- **Produce:** Signed rows in `docs/er_encoding_audit.md` (or append to `item_gates.md` Wrong-room table).
- **Done when:** Agents can regenerate JSON without guessing.

### H0.5 Patch route JSON after your decisions
- Agents can remove `sword_key` and apply **your** room resolutions.
- **You:** Approve the conflict table above (even a short “accept catalog” / “accept ER” list).

---

## H1 — RAM hunts (live play + scripts)

Tools already exist under `scripts/hunt_*.py`. Agents cannot flip in-game bits.

### H1.1 Interaction prompt (`INTERACTION_PROMPT`)
- **Why human:** `proprio[11]` is always 0; desks/buttons/cranks/MO need “Press X”.
- **Do:** `python scripts/hunt_interaction_prompt.py` at dining door, typewriter, desk, MO terminal.
- **Produce:** Address + confirm in `memory_map.py` (agent can wire once you give the address).
- **Done when:** Standing at interactable → `proprio[11]=1`.

### H1.2 SCD work flags campaign
- **Why human:** `scd_work_flags.json` is empty; ~29 event/puzzle gates stay hidden.
- **Do:** Ordered sessions with `hunt_scd_flags.py` — Kenneth → Barry → emblem → gallery → crests → Plant → books → MO → Tyrant rocket.
- **Produce:** Named bits in `data/scd_work_flags.json`.
- **Done when:** ≥20 named bits covering mansion + lab gates; B2 `flags` obs can ship.

### H1.3 DOOR_FLAGS bit → edge map
- **Why human:** Address `0x800C86B4` confirmed; bit meaning needs unlock experiments.
- **Do:** Unlock shield / armor / helmet / lab doors; diff bits; map to `(from,to)`.
- **Produce:** Table `bit → door edge` (JSON or markdown).
- **Done when:** Spatial can expose open/locked per exit.

### H1.4 Enemy `type_id` offset
- **Why human:** Live HP/x/z work; type always 0.
- **Do:** `hunt_enemy_ram.py` zombie vs hunter vs dog in known rooms.
- **Produce:** Offset in `ENEMY_FIELD_OFFSETS`.
- **Done when:** `spatial.enemy0_type_id ≠ 0` for hunters.

### H1.5 Poison byte verify
- **Why human:** `0x800C51A1` is candidate only.
- **Do:** Yawn bite → herb cure → confirm bit.
- **Done when:** `proprio.poisoned` trusted for masks/rewards.

---

## H2 — Data collection / demos (your time on the stick)

### H2.1 BC demonstration recordings
- **Why human:** `play_human` demos; agents can build `train_bc.py` but not play well.
- **Do:** ≥2 hours clean East Wing (Kenneth gate respected): dining → tea → hall → gallery corridor.
- **Produce:** Demo trajectories under an agreed path (e.g. `data/demos/east_wing/*.npz` or project convention).
- **Done when:** BC warm-start script has a real dataset.

### H2.2 Pickups empirical coordinates
- **Why human:** ~70% pickups lack bearings; logging needs a route play.
- **Do:** One Standard Jill pass with inventory-gain logging (`log_door_transitions` / capture_session).
- **Produce:** `data/pickups_empirical.json` → merge into `item_positions.json`.
- **Done when:** ≥80 positioned pickups (or mansion 1F complete).

### H2.3 Curriculum / Go-Explore savestates
- **Why human:** Only dining fresh state is trusted; `.State` binaries often local.
- **Do:** Capture wing starts: post-Kenneth hall, gallery entry, bar, guardhouse door, lab entry.
- **Produce:** `states/*.State` + short manifest JSON.
- **Done when:** Wing curricula / archive resets have real spawn points.

### H2.3b Review draft story USE sites
- **Why human:** Agents added 10 `_draft: true` rows (crests@11A, blue_jewel@10D, chemical@10C, v_jolt@40E, hex_crank×3) with room IDs only — no stand coords; masks ignore them until cleared.
- **Do:** Confirm room + add `x`/`z` (or drop bad rows); remove `_draft` when verified.
- **Produce:** Updated `data/story_item_use_sites.json`.
- **Done when:** Draft flag gone on sites you want live in the USE mask.

### H2.4 Live weapon probes (optional but valuable)
- **Why human:** Bazooka / rocket / flamethrower / magnum unvalidated in combat.
- **Do:** Run existing probe scripts against quicksaves; keep JSON evidence.
- **Done when:** CI or docs mark which weapons are HARD_FAIL-clean.

---

## H3 — Decisions only the imperator owns

### H3.1 Doctrine
| Decision | Default counsel from audit | Your call |
|----------|----------------------------|-----------|
| Re-enable route GPS in obs | **No** until day ~45 failure | Amend north star or hold |
| Puzzle button macros | **Never** for crow/piano/MO | Hold |
| Starve dense herb/zombie rewards | Audit recommends cut/clip | Approve magnitudes |
| Box cutscene pay | Skill: unvalidated | Pay / deny / ignore |
| Accept Arranged bleed in RDT graph | Filter to 116 rooms | Approve filter |

### H3.2 Fleet ops
- Stop/restart learners on graft zip (`ppo_re1_world_almanac_graft.zip`).
- WH2 `n_envs` cap — do not raise past 27 without pagefile green light.
- Rollback to `backup/pre-world-catalog-2026-07-17` if graft fails kill criterion.

### H3.3 Reward / skill changes
- Any change to *what pays* in exploration rewards requires your validation (awbw `re-exploration-rewards` skill rule).
- Agents may **copy** the skill into `docs/exploration_rewards.md`; they may not invent new paid events.

### H3.4 ER audit sign-off
- Dual-Composer disputed rows (square_crank, medals, red_jewel, hex_crank, …) need your tie-break (see plan `static_world_map_obs`).
- **No** shipping disputed rows as “verified” without you.

---

## Explicitly NOT needing you (agents handling)

These are in flight or queued without human:

- ~~Wire `file_*` / `combine_*` into `RE1WorldAwareExtractor.forward`~~ **DONE** (`features_dim=1587`)
- ~~Widen `WORLD_CONTEXT_DIM`~~ **DONE** (64→128)
- ~~Filter phantom RDT nodes in graph/catalog~~ **DONE**
- ~~Remove Chris `sword_key` from Jill affordances; fix shield notes~~ **DONE**
- ~~Drop serum@102 / map_1f@106 duplicates~~ **DONE** (veto in H0.4 if you want them back); `star_crest` 107 vs 117 still human
- ~~Doc hygiene: exploration_rewards copy, north_star shield≠helmet, curriculum README, SUPERSEDED banners~~ **DONE** (`docs/exploration_rewards.md`, `.cursor/skills/re-exploration-rewards/`)
- ~~Ammo mask fix (attack illegal when equipped slot qty=0)~~ **DONE**
- ~~Catalog singleton / spawn parse once~~ **DONE** (`lru_cache` on `WorldCatalog.from_files`)
- ~~Eval harness + Go-Explore + `train_bc` scaffolds~~ **DONE** (`scripts/eval_wing_harness.py`, `re1_rl/go_explore_archive.py`, `scripts/train_bc.py`; demos/savestates still human)
- ~~Draft expanded `story_item_use_sites.json`~~ **DONE** (10 `_draft:true` sites; excluded from USE mask until you verify coords — see H2)
- ~~Unit tests for unreachable rooms / Jill-only filters~~ **DONE** (phantom neighbors + sword_key)
- ~~`persistent=False` on catalog buffers~~ **DONE**; drop deprecated affordances path when safe

---

## Suggested human schedule (minimal wall-clock)

| Session | Focus | ~Time |
|---------|-------|-------|
| **1** | H0.2 Study/`119` + H0.4 quick tie-breaks (accept catalog vs ER list) | 1–2 h |
| **2** | H1.1 interaction prompt hunt | 1–2 h |
| **3** | H1.2 SCD mansion chain (Kenneth→gallery→bar) | 2–4 h |
| **4** | H2.1 BC demos East Wing | 2 h |
| **5** | H0.1/H0.3 door/ID pass when ready | 2–4 h |
| **Later** | Lab SCD, DOOR_FLAGS, pickups empirical, weapon probes | as needed |

---

## Contact points for agents

When you finish an item, drop results here or say “H0.2 done — 119 is Study” and agents will land the code/data patches.

**Backup if graft burns:** branch `backup/pre-world-catalog-2026-07-17` + ckpt under `backups/pre_world_catalog_2026-07-17/`.

---

## Plan cross-link

`c:\Users\phili\.cursor\plans\static_world_map_obs_57f02d1c.plan.md`  
`docs/finish_line_war_audit_2026-07-17.md`
