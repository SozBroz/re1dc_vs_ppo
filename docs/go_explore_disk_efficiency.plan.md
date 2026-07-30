# Go-Explore disk efficiency — deferred plan

**Status:** Capture disabled fleet-wide (2026-07-30). Training continues with PB sidecars only; archive cells are not written and not used for resets (`RE1_GO_EXPLORE_RESET_WEIGHT=0`).

**Problem:** Shadow capture was writing ~330 GB/day fleet-wide (~13.8 GB/h) into `data/go_explore/cells` — ~100× the `<< 1 GB/day per machine` comment in `fleet/local/go_explore_phase_c.env.cmd`. Canonical archive should cap at ~680 cells (~1 GB); worker-local bundles accumulated without cleanup.

**Evidence:** `data/_re1_folder_growth_15m.json`, `data/_fleet_monitor_disk_health_15m.json` (2026-07-30).

---

## P0 — done

- [x] Set `RE1_GO_EXPLORE_CAPTURE=0` in `fleet/local/go_explore_phase_c.env.cmd` (all launchers source this).
- [x] Restart fleet with capture off.
- [ ] Optional: purge existing `data/go_explore/cells` on each machine when convenient (`scripts/fleet_purge_go_explore_cells.ps1`) — reclaims GB already on disk; not required for training.

---

## P1 — before re-enabling capture

### 1. Machine-wide persistent capture budget

- Track **total bundles written per machine per calendar day** in a file under `data/go_explore/` (survives episode reset).
- Env: e.g. `RE1_GO_MAX_CAPTURES_DAY=200` or `RE1_GO_MAX_CAPTURE_BYTES_DAY=250MB`.
- `maybe_capture_cell()` returns `None` when budget exhausted — applies to **new and replace** writes.

### 2. Fix per-episode budget reset bug

- `_go_capture_budget` in `env.py` must **not** reset `replaces_today` / `replace_day` on every `reset()` if we keep per-env cooldown state; or move all budgeting to machine-wide file (preferred).

### 3. Learner-canonical storage; workers ephemeral

- Workers emit proposals in rollouts only; **do not retain** installed cell dirs after upload/epoch ack.
- Learner (`go_explore_merge.py`) is sole writer to durable `data/go_explore/cells/`.
- On ingest reject/duplicate: delete worker staging, do not leave orphans.

### 4. Dedupe before disk write

- Poll learner manifest (`GET /go_explore/manifest` or local cache) before `save_state`.
- Skip capture if cell key exists and quality does not beat canonical.

### 5. Raise free-space floor (when capture returns)

- `RE1_GO_MIN_FREE_GB=100` (or 200 on pking) — belt-and-suspenders until retention is proven.

**Target after P1:** ≤0.5 GB/day fleet net growth; canonical archive ~1–5 GB.

---

## P2 — tuning after P1 stable

- Lower `RE1_GO_MAX_CELLS_PER_ROOM` (40 → 10–20) after frontier diversity check.
- Narrow `YAWN_PATH_ROOMS` to active curriculum slice only.
- Single capture actor per machine (or learner-only) for canary before fleet-wide re-enable.
- Cooldown 600+ steps during canary.
- Evaluate NTFS compression on `.State` bundles.
- Delta savestates — only if full-state retention still too heavy (high complexity).

---

## Separate: pking C: pagefile / commit

Not caused by `data/go_explore` on D:. Monitor `pagefile.sys` vs C: free space.

- Reduce pking `--n-envs` if commit pegged (RAM vs disk tradeoff).
- Move pagefile to roomier drive if needed.
- Do not disable paging entirely.

---

## Re-enable checklist

1. P1 items 1–4 implemented and tested on pking only.
2. 24h soak: `data/go_explore/cells` growth ≤ budget on all machines.
3. Set `RE1_GO_EXPLORE_CAPTURE=1`; keep `RE1_GO_EXPLORE_RESET_WEIGHT=0` until archive resets validated.
4. Only then consider `RESET_WEIGHT > 0` for training integration.

---

## Key files

| File | Role |
|------|------|
| `fleet/local/go_explore_phase_c.env.cmd` | Capture on/off switch |
| `re1_rl/go_explore_capture.py` | Capture gates, disk lock, bundle write |
| `re1_rl/env.py` | `_go_capture_budget`, `_maybe_capture_go_explore` |
| `re1_rl/go_explore_merge.py` | Learner ingest |
| `re1_rl/go_explore_worker_cache.py` | Worker manifest poll |
| `scripts/fleet_purge_go_explore_cells.ps1` | One-time cleanup |
