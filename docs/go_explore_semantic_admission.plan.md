# Go-Explore semantic admission — implementation plan

**Status:** Planned (post-canary ingest fix `e28190c`, manifest analytics shipped)  
**Depends on:** [go_explore_disk_efficiency.plan.md](go_explore_disk_efficiency.plan.md) P1, [go_explore_milestone_digest.plan.md](go_explore_milestone_digest.plan.md)  
**Date:** 2026-07-30

---

## Problem

Live canary archive (v21, ~57 cells, ~88 MB) shows **semantic pose spam**, not exact duplicates:

| Bucket | Poses | Storage | Issue |
|--------|-------|---------|-------|
| `105` / `gallery:idle` | 24 | ~37 MB | Same milestone, tile scatter from dining wander |
| `106` / emblem+kenneth digest | 12 | ~18 MB | Main Hall pose scatter |
| **Total flagged (>8 poses)** | 2 buckets | ~63 MB (~72% of archive) | |

Current keys are `v2|r=ROOM|x=TX|z=TZ|m=DIGEST` — **correct for restore**, but admission treats every tile as a new cell. With 20 parallel envs and 60-step cooldown, the **250 MB/day budget fills in minutes** on prologue rooms before frontier rooms (117, 20E, 210) get coverage.

Pre-P1 unbounded capture hit **~330 GB/day**. P1 budgets stopped the bleed; **semantic admission** stops the waste.

---

## Sizing (skeptic-friendly tiers)

GPT 5.6 suggested ~300–1,000 cells (~0.5–1.5 GB) for a curated Yawn archive. That may be tight. This plan uses **tiered caps** so we can absorb headroom without Montezuma-scale blow-up.

| Tier | Cells | Storage @ 1.5 MB | Role |
|------|-------|------------------|------|
| **A — Yawn target** | 500–1,500 | ~0.8–2.2 GB | Expected sufficient with semantic admission |
| **B — Comfortable** | 3,000 | ~4.5 GB | Headroom for multi-pose entrances, resource Pareto variants |
| **C — Hard stop** | 8,000 | ~12 GB | Fleet safety ceiling (~5–8× tier A, **not** 50–100×) |
| **Rejected** | 50,000+ | ~75 GB+ | Native Go-Explore pixel-cell scale — wrong for RE1 savestates |

**Daily write (after admission):**

| Phase | `RE1_GO_MAX_CAPTURE_BYTES_DAY` | Rationale |
|-------|----------------------------------|-----------|
| Canary (now) | 250 MB | Proved ingest; admission not yet live |
| Post-admission soak | 500 MB | ~300 new semantic facts/day at 1.5 MB |
| Steady Yawn build | 1 GB | ~650 cells/day upper bound if every capture is novel |

**Falsification:** If Yawn reach plateaus below tier B with archive resets enabled, revisit caps — not before measured reset yield.

---

## Design principles

1. **Keep v2 cell keys** `(room, tile, digest)` — loadstate truth requires exact pose; do not coarsen keys globally yet.
2. **Cap at semantic bucket** `(room_id, milestone_digest)` — Go-Explore domain-cell literature (Ecoffet Nature 2020).
3. **Learner is authoritative** — workers pre-filter; learner admits/replaces/evicts under global caps.
4. **Evict, don't reject blindly** — when bucket full, drop weakest pose (low quality, redundant tile) to make room for frontier-improving cells.
5. **Always admit milestone transitions** — new digest in room, first cell in new Yawn room, quality-dominant replace on same key.
6. **BizHawk savestates stay canonical** — no RAM-only or trajectory-only cells in v1.

---

## Semantic bucket model

```text
semantic_key = (room_id, milestone_digest)     # e.g. ("105", "gallery:idle")
cell_key     = v2|r=105|x=3|z=2|m=gallery:idle  # one pose champion per slot
```

**Pose cap per bucket:** default **6** (env `RE1_GO_MAX_POSES_PER_BUCKET`). GPT 5.6 suggested 4–8; 6 matches current analytics threshold.

**Eviction score (lower = evict first):**

1. Lowest lexicographic `quality` tuple  
2. Smallest tile distance from bucket centroid (keep spatial spread)  
3. Lowest `visit_count` / never selected for reset (once telemetry exists)  
4. Oldest `captured_at_iso`

**Admission order (learner ingest):**

```text
1. Same cell_key exists → existing quality replace rules (unchanged)
2. New cell_key, bucket under pose cap → admit
3. New cell_key, bucket at cap → admit only if beats worst incumbent (evict incumbent bundle)
4. New cell_key, global archive at hard cap → admit only if beats global worst semantic cell OR new Yawn room
5. Else → reject (increment visit_count on nearest incumbent if keyed)
```

**Worker pre-check (before `save_state` + budget consume):**

- Build `manifest_index_by_semantic_bucket()` from polled `local_manifest.json`.
- If bucket at cap and proposal cannot beat weakest incumbent quality → **return None** (no zip, no budget).
- If same `cell_key` and replace gates fail → return None (already partially done).

---

## Implementation phases

### Phase 0 — Analytics baseline (done)

- [x] `re1_rl/go_explore_analytics.py` — semantic buckets, pose flags, Yawn coverage
- [x] `scripts/go_explore_manifest_analytics.py` — learner / local / archive sources
- [x] `tests/test_go_explore_analytics.py`

**Gate:** Run after each soak; buckets > pose cap should trend down after Phase 2.

---

### Phase 1 — Shared semantic helpers (code, no behavior change)

**New module:** `re1_rl/go_explore_semantic.py`

| Function | Purpose |
|----------|---------|
| `semantic_bucket_key(room_id, milestone_digest)` | `(str, str)` tuple |
| `semantic_bucket_key_from_cell_key(cell_key)` | parse v2 key |
| `manifest_index_by_semantic_bucket(manifest)` | `{semantic_key: [rows...]}` |
| `bucket_pose_count(index, semantic_key)` | int |
| `weakest_incumbent(rows)` | row to evict |
| `pose_cap()` / `max_archive_cells()` / `max_poses_per_bucket()` | env readers |

**Env knobs** (add to `fleet/local/go_explore_phase_c.env.cmd` defaults, tunable in capture overlay):

```cmd
set RE1_GO_MAX_POSES_PER_BUCKET=6
set RE1_GO_MAX_ARCHIVE_CELLS=8000
set RE1_GO_POSE_EVICT=1
```

**Tests:** `tests/test_go_explore_semantic.py` — bucket grouping, evict pick, cap readers.

**Ship:** commit + fleet pull; no restart required (unused until Phase 2).

---

### Phase 2 — Worker pre-filter (stop budget burn)

**File:** `re1_rl/go_explore_capture.py` — `maybe_capture_cell()`

After computing `key`, `quality`, `digest`, before `save_state`:

```python
if not semantic_admission_allowed(
    room, digest, key, quality,
    manifest_index=manifest_index,
    semantic_index=semantic_index,  # optional cache on env
):
    return None
```

**File:** `re1_rl/go_explore_worker_cache.py`

- Add `manifest_semantic_index(local_root)` cached beside `manifest_index_by_cell_key`.
- Refresh on each manifest poll (same 60s interval).

**File:** `re1_rl/env.py`

- Optional: cache semantic index on env to avoid rebuild every step (invalidate on manifest poll — worker runtime already polls).

**Tests:** extend `tests/test_go_explore_capture.py`:

- Bucket at cap + weaker quality → no proposal, budget unchanged
- Bucket at cap + stronger quality + evict enabled → proposal (learner completes evict)
- New digest in same room → always passes worker pre-filter

**Gate:** 1h canary — `capture_budget.json` should grow **slowly**; manifest analytics shows ≤6 poses per bucket for new captures.

---

### Phase 3 — Learner authoritative admission + eviction

**File:** `re1_rl/go_explore_merge.py` — `_ingest_one_unlocked()`

Before writing bundle:

1. Group archive cells by semantic bucket.
2. If new key and bucket full → select evictee, delete `cells/<old_record_id>/`, remove from `archive.cells`.
3. If global `len(archive.cells) >= max_archive_cells()` → global evict or reject per rules above.
4. Log: `go_explore evicted {record_id} bucket={room}/{digest} for {new_record_id}`.

**File:** `re1_rl/distributed/learner_server.py`

- Extend `/status` with optional `go_explore_stats`: `{admitted, rejected_semantic, evicted, cells_total, bytes_total}`.

**File:** `re1_rl/go_explore_archive.py`

- Helper: `cells_by_semantic_bucket()` for merge + analytics.
- Helper: `remove_cell(record_id)` — drop from JSON + disk.

**Tests:** `tests/test_go_explore_merge.py`:

- Ingest 8 poses same bucket → 6 remain after evict chain
- Ingest new room at global cap → admits if evictable global worst
- Evicted bundle dir removed from disk

**Gate:** Re-run manifest analytics on learner — no bucket >6 poses; total cells stable under cap during continued capture.

---

### Phase 4 — One-time archive compaction

**New script:** `scripts/go_explore_compact_archive.py`

- Load WH2 `archive.json` + `cells/`.
- For each semantic bucket over pose cap, keep best 6 by evict score.
- Delete orphan bundles; bump `archive_version`.
- Dry-run `--report` mode using analytics output.

**Run once on WH2** after Phase 3 deploy, before raising daily byte cap.

Expected result on current archive: **57 → ~20–25 cells**, **~88 MB → ~35 MB**, preserving best pose per bucket + all multi-digest tiles.

---

### Phase 5 — Budget + soak tuning

**Adjust** `fleet/local/go_explore_phase_c.env.cmd`:

```cmd
set RE1_GO_MAX_CAPTURES_DAY=400
set RE1_GO_MAX_CAPTURE_BYTES_DAY=524288000
set RE1_GO_CAPTURE_COOLDOWN_STEPS=120
set RE1_GO_MAX_CELLS_PER_ROOM=20
```

(Room cap becomes secondary to semantic cap; lower from 40.)

**24h soak checklist:**

| Metric | Pass |
|--------|------|
| pking `cells/` | ~0 (ephemeral) |
| WH2 archive growth | ≤ 500 MB/day |
| Manifest buckets >6 poses | 0 |
| Yawn rooms in manifest | ≥ 8/17 (progress) |
| `go_explore_accepted` / rejected ratio | logged |
| Analytics report | committed to `data/go_explore/manifest_analytics.txt` daily |

---

### Phase 6 — Archive reset canary (after compaction + motion P0)

Only after [go_explore_milestone_digest.plan.md](go_explore_milestone_digest.plan.md) motion/restore gates:

- Enable `RE1_GO_EXPLORE_RESET_WEIGHT=0.02` on pking.
- Measure: restore validity, descendant rooms/milestones per 100 resets.
- Compare compact vs full archive if falsification needed.

---

## Files touched (summary)

| File | Phase | Change |
|------|-------|--------|
| `re1_rl/go_explore_semantic.py` | 1 | **new** — bucket keys, indexes, evict score |
| `re1_rl/go_explore_analytics.py` | 0 | done |
| `re1_rl/go_explore_capture.py` | 2 | worker pre-filter |
| `re1_rl/go_explore_worker_cache.py` | 2 | semantic manifest index |
| `re1_rl/go_explore_merge.py` | 3 | learner evict + global cap |
| `re1_rl/go_explore_archive.py` | 3 | bucket helpers, remove_cell |
| `re1_rl/distributed/learner_server.py` | 3 | stats in `/status` |
| `fleet/local/go_explore_phase_c.env.cmd` | 5 | new env defaults |
| `scripts/go_explore_compact_archive.py` | 4 | **new** — one-time prune |
| `scripts/go_explore_manifest_analytics.py` | 0 | done |
| `tests/test_go_explore_semantic.py` | 1 | **new** |
| `tests/test_go_explore_capture.py` | 2 | pre-filter cases |
| `tests/test_go_explore_merge.py` | 3 | evict cases |

---

## Rollout order

```text
Phase 1 (helpers + tests)
    → Phase 2 (worker pre-filter) + restart pking canary only
    → Phase 3 (learner evict) + restart WH2 learner
    → Phase 4 (compact existing archive on WH2)
    → Phase 5 (raise daily cap, 24h soak)
    → Phase 6 (archive reset weight, separate gate)
```

**Do not** raise `RE1_GO_MAX_CAPTURE_BYTES_DAY` until Phase 2+3 are live — otherwise budget burns on proposals the learner will reject.

---

## Success criteria

| Criterion | Target |
|-----------|--------|
| Semantic bucket pose cap | ≤ 6 poses per `(room, digest)` |
| Archive hard cap | ≤ 8,000 cells (~12 GB) |
| Yawn-first coverage | ≥ 10/17 Yawn rooms represented before Phase 6 |
| Daily fleet archive growth | ≤ 1 GB/day steady state |
| Pose spam regression | analytics `--json` shows 0 buckets over threshold |
| Reset yield (Phase 6) | TBD — descendant milestone rate vs PB-only baseline |

---

## Explicitly deferred

| Item | Why |
|------|-----|
| Coarsen `DEFAULT_TILE_SPAN` (4096 → 8192+) | Changes cell keys; do after semantic cap proven insufficient |
| Trajectory prefix storage | Savestate restore is faster + more reliable on RE1 |
| RAM-only cells | PS1 state not fully in game RAM |
| Delta / compressed savestates | Phase 5+ only if tier C approached with good admission |
| Fleet-wide capture ON | pking canary until Phase 5 soak passes |
| LLM / learned latent cells | RE1 already has milestone digest + guidebook |

---

## Quick commands

```cmd
REM Analytics (learner)
python scripts/go_explore_manifest_analytics.py --learner http://192.168.0.116:8765

REM Analytics JSON for diff
python scripts/go_explore_manifest_analytics.py --learner http://192.168.0.116:8765 --json --output data/go_explore/manifest_analytics.json

REM Compact (dry run, post Phase 4)
python scripts/go_explore_compact_archive.py --archive data/go_explore/archive.json --dry-run
```

---

## Open questions (resolve during Phase 5 soak)

1. **Pose cap 6 vs 8** — if Yawn resets show entrance sensitivity, bump to 8 for rooms `106`, `20E`, `210` only.
2. **Global cap 8k vs 5k** — if disk comfortable and coverage lagging, keep 8k; if WH2 disk tight, lower to 5k.
3. **Per-room cap** — may remove entirely once semantic cap works; keep as backstop at 20.
