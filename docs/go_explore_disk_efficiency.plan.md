# Go-Explore disk efficiency — deferred plan

**Status:** P1 shipped; capture still **off** fleet-default. Canary enable ready on pking only (`go_explore_capture_on.env.cmd`).

**Problem:** Shadow capture was writing ~330 GB/day fleet-wide (~13.8 GB/h) into `data/go_explore/cells` — ~100× the `<< 1 GB/day per machine` comment in `fleet/local/go_explore_phase_c.env.cmd`. Canonical archive should cap at ~680 cells (~1 GB); worker-local bundles accumulated without cleanup.

**Evidence:** `data/_re1_folder_growth_15m.json`, `data/_fleet_monitor_disk_health_15m.json` (2026-07-30).

---

## P0 — done

- [x] Set `RE1_GO_EXPLORE_CAPTURE=0` in `fleet/local/go_explore_phase_c.env.cmd` (all launchers source this).
- [x] Restart fleet with capture off.
- [x] Purge `data/go_explore/cells` fleet-wide (`scripts/fleet_purge_go_explore_cells.ps1`).

---

## P1 — before re-enabling capture

- [x] Machine-wide persistent capture budget (`capture_budget.json`, `RE1_GO_MAX_CAPTURES_DAY`, optional bytes cap).
- [x] Fix per-episode budget reset — `env.py` keeps only `last_capture_step` in memory; daily caps live on disk.
- [x] Learner-canonical storage — workers emit `bundle_b64` proposals; no local `cells/` install unless `RE1_GO_CANONICAL_STORE=1`.
- [x] Dedupe before disk write — `manifest_index_by_cell_key` + worker heartbeat manifest poll.
- [x] Raise free-space floor — `RE1_GO_MIN_FREE_GB=100` in `go_explore_phase_c.env.cmd`.

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

1. [x] P1 items implemented and tested (`tests/test_go_explore_capture.py`).
2. [x] Fleet cells purged; stale worker `cells/` removed.
3. [ ] **Canary (pking only):** restart pking with `fleet/local/start_worker_detached_pking_capture_canary.cmd` (sources `go_explore_capture_on.env.cmd` after phase_c defaults). WH1/WH2 keep `RE1_GO_EXPLORE_CAPTURE=0`.
4. [ ] **24h soak:** pking `cells/` stays ~0 (ephemeral); WH2 learner `cells/` growth ≤ `RE1_GO_MAX_CAPTURE_BYTES_DAY` (250 MB/day) × accepted ingest rate.
5. [ ] Fleet-wide: add `call go_explore_capture_on.env.cmd` to WH1/WH2 worker launchers (or set in phase_c).
6. Keep `RE1_GO_EXPLORE_RESET_WEIGHT=0` until archive resets validated.
7. Only then consider `RESET_WEIGHT > 0` for training integration.

### Canary commands (pking)

```cmd
REM after git pull on all boxes:
fleet\local\start_worker_detached_pking_capture_canary.cmd
```

Monitor: `data/go_explore/capture_budget.json`, WH2 `data/go_explore/cells/`, learner log `go_explore_accepted`.

---

## Key files

| File | Role |
|------|------|
| `fleet/local/go_explore_phase_c.env.cmd` | Capture off + budget knobs (fleet default) |
| `fleet/local/go_explore_capture_on.env.cmd` | Flip capture on (canary overlay) |
| `fleet/local/start_worker_detached_pking_capture_canary.cmd` | pking-only capture canary restart |
| `re1_rl/go_explore_capture.py` | Capture gates, disk lock, bundle write |
| `re1_rl/env.py` | `_go_capture_budget`, `_maybe_capture_go_explore` |
| `re1_rl/go_explore_merge.py` | Learner ingest |
| `re1_rl/go_explore_worker_cache.py` | Worker manifest poll |
| `scripts/fleet_purge_go_explore_cells.ps1` | One-time cleanup |
