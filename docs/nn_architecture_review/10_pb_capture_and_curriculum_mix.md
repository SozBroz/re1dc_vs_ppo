# 10 — PB capture policy & curriculum mix

**Date:** 2026-07-21  
**Scope:** How personal-best (PB) bundles are captured, stored, and mixed into reset distribution — without route leakage or opaque score-only gating.

Related: [08_yawn_without_restart.md](08_yawn_without_restart.md) (mixed curriculum), [09_almanac_shield_attic_and_pb_savestates.md](09_almanac_shield_attic_and_pb_savestates.md) (sidecar contract).

---

## 1. Handcraft criteria vs auto-detect exceptional runs

| Layer | Who decides | What it controls |
|-------|-------------|------------------|
| **Milestone taxonomy** | Human (design/docs) | *Which* achievements may produce a PB row (`lockpick_held`, `gold_emblem_used`, `shield_key_held`, `attic_foyer_20E`, `attic_210_yawn_contact`, …) |
| **Capture trigger** | Agent / worker (automatic) | On first legal hit of a taxonomy milestone during training, write `{State}` + sidecar + manifest row |
| **Within-milestone rank** | Agent (optional) | `meta["score"]` or visit depth — **only** to pick the best duplicate for the same `milestone_id`, not to invent new milestones |
| **Reset mix** | Curriculum config (automatic) | `fresh_weight` + uniform PB pool via `sample_reset_bundle()` |

**Reject:** using a single continuous “exceptionally well” episode return as the *sole* gate for archive membership. High return without a verified milestone is not a curriculum cell — it is noise and invites farming.

**Accept:** return / survival / coverage as *secondary* signals inside a milestone bucket (e.g. keep the `210` PB with highest post-contact survival among rows that already passed the `attic_210_yawn_contact` milestone check).

---

## 2. Recommended v1

1. **Handcrafted milestone taxonomy** — enumerated in manifest + capture hooks (Doc 09 §4.3 examples).
2. **Automatic capture** — worker detects milestone, validates integrity (Jill in control, room match, anti-farm sets), writes bundle, appends manifest.
3. **Automatic mix** — `re1_rl/pb_curriculum.py`:
   - `load_pb_manifest(path)` → `list[PbBundle]`
   - `sample_reset_bundle(bundles, fresh_weight=…, rng=…)` → `PbBundle | None`
   - `None` → env uses curriculum `init_savestate` (today: `states/jill_control_fresh.State`)
   - `PbBundle` → `load_savestate(state_path)` + `apply_sidecar(sidecar_path)` (env wiring is a follow-on; module stays BizHawk-free)

Default mix: retain a **substantial** `fresh_weight` (e.g. 0.5–0.7) so discovery and recovery stay in distribution; PB starts are the minority frontier boost (Doc 08 §2).

---

## 3. How this differs from full Go-Explore score

| | **Go-Explore archive** (`go_explore_archive.py`) | **PB curriculum mix** (`pb_curriculum.py`) |
|---|--------------------------------------------------|--------------------------------------------|
| Cell key | `(room_id, tile_bin)` today | Discrete `milestone_id` (human taxonomy) |
| Admission | Any pose with `score` on visit | Only whitelisted milestones + integrity checks |
| Score role | Primary ranking within cell | Optional tie-break among same milestone |
| Sidecar | Not modeled (state path only) | Required — episode memory must match RAM (Doc 09) |
| Reset role | Frontier under-visited cells | Named progress markers (emblem, attic, Yawn, …) |

Go-Explore answers: “where in the mansion have we been, tile-granular?”  
PB mix answers: “which *story gates* should some workers reopen from?”

They complement each other; neither may inject route index, waypoint, or button replay into obs or action selection.

---

## 4. Manifest schema (v1)

```json
{
  "version": 1,
  "bundles": [
    {
      "milestone_id": "shield_key_held",
      "state_path": "states/pb/shield_key_a1b2.State",
      "sidecar_path": "states/pb/shield_key_a1b2.sidecar.json",
      "meta": {
        "room_id": "105",
        "score": 12.0,
        "captured_run": "worker3_ep42"
      }
    }
  ]
}
```

`PbBundle`: `state_path`, `sidecar_path`, `milestone_id`, `meta`.

---

## 5. Env reset + typewriter champion (landed)

**Env** (`re1_rl/env.py`):

- `_load_stage()` reads `curriculum_path` JSON (`init_savestate`, `route_steps`, …).
- `reset(options={"pb_bundle": {state_path, sidecar_path}})` loads that State + applies sidecar with **`reset_softlock=True`** (full 12m budget; ignore capture-time stagnation).
- Without `pb_bundle`: fresh `init_savestate` + seed progress from spawn (unchanged).

**v1 champion** — Main Hall typewriter save (`typewriter_save:106`):

| Piece | Module / flag |
|-------|----------------|
| Detector | `typewriter_save.py` (ink_ribbon drop in 106 → control restored) |
| Gates | Kenneth seen, not breached; `visited ⊆ {105,104,106}` and all three present |
| Score | valuable slots → HP → handgun ammo → fewer ink_ribbons (`pb_champion.py`) |
| Capture | `RE1_PB_CAPTURE=1` (default **off**); other milestones suppressed while `RE1_PB_V1_TYPEWRITER_ONLY=1` |
| Mix | `PbChampionResetWrapper` in `make_env`; `RE1_PB_FRESH_WEIGHT` default 0.5 |
| Shared sync | `RE1_PB_SHARED_ROOT` (e.g. Samba); background push/pull ~`RE1_PB_SYNC_INTERVAL_S` (default 30s); lag OK |

Multi-milestone manifest mix (`load_pb_manifest` / `sample_reset_bundle`) remains available for later ladder stages.

---

## 6. Extending Go-Explore cell key (design only)

Today: `cell_key(room_id, tile_bin)` → `"105:3,1"`.

**Proposed v2 key** (not implemented):

```
cell_key = f"{room_id}:{tx},{tz}:{milestone_digest}"
```

Where `milestone_digest` is a stable hash of *achieved* milestone set (sorted `milestone_id` strings from taxonomy), e.g. `sha256("emblem_held|gold_emblem_used")[:8]`.

Effects:

- Same room/tile with different inventory gates → different archive cells (fixes conflation of dining pre- vs post-fireplace).
- Capture still requires passing a taxonomy milestone; digest is derived from facts, not return.
- `select_frontier()` unchanged — still ranks by `visit_count`, then score within cell.
- PB manifest remains the human-auditable manifest; Go-Explore JSON remains the exploration bookkeeping overlay.

Migration: existing v1 cells get `milestone_digest=""` (legacy bucket); new captures write digest-aware keys.

---

## 7. North-star checklist

- [x] Milestones handcrafted; capture and mix automatic  
- [x] No route / waypoint / compass in obs from PB path  
- [x] Sidecar prevents reward re-pay and obs “time travel” (Doc 09)  
- [x] Module testable without BizHawk  
- [x] Env `apply_sidecar` + typewriter capture + champion mix + async shared sync  
- [ ] Broader milestone ladder beyond typewriter v1  
- [ ] Go-Explore `milestone_digest` in cell key (design above)

---

## 8. Addendum — multi-room typewriter PB (2026-07-21)

v1 typewriter champion is no longer Main-Hall-only. Any RDT typewriter room may capture a per-room slot; prologue allowlist is **not** a capture gate.

| Piece | Policy |
|-------|--------|
| Detector | Ink-ribbon drop in any `TYPEWRITER_ROOMS` id (e.g. `106`, `118`) → save cinema → stable control |
| Capture gate | `typewriter_save_capture_ok`: still in room `r` and Kenneth gate not breached (no `visited ⊆ {105,104,106}`) |
| Milestone id | `typewriter_save:{room}` — 106 keeps slot dir `champions/mainhall_typewriter`; others `champions/typewriter_{room}` |
| Score | `champion_score_v2`: herb unit table + physical loot + ever-held key credit (no double-count) → HP → handgun ammo → fewer ribbons → **visited count** tie-break |
| Cutscene | Ribbon drop in any typewriter room disqualifies exploration cutscene pay (`typewriter_save_cutscene_disqualified`) |
| Mix | `sample_typewriter_start` / `typewriter_mix_weights(N)`: **N=0** fresh only; **N=1** ½ fresh / ½ sidecar; **N≥2** fresh pinned **⅓**, sidecars share **⅔** equally. `RE1_PB_FRESH_WEIGHT` ignored for this sampler |
| Capture flag | Still `RE1_PB_CAPTURE` default **off**; `RE1_PB_V1_TYPEWRITER_ONLY=1` suppresses non-typewriter milestones |

---

*Document version: 1.2 — multi-room typewriter PB + score v2 mix weights.*

