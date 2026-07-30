# Go-Explore pre-canary fix plan

**Status:** Items 1–5 implemented on pking (uncommitted); WH2 archive reconcile pending git sync.  
**Context:** P1 disk guards shipped (`dadbd32`); fleet cells purged; capture still off fleet-default. GPT 5.6 fleet audit (2026-07-30) found functional gaps that make a canary misleading or unsafe.

**Goal:** Make capture canary actually exercise learner ingest, with honest budgets and coherent archive state — without yet tuning capture worthiness (P2) or fleet-wide enable.

---

## Checklist

### 1. Fix proposal transport

- [x] Preserve `go_explore_capture` in `slim_progress_info()` (or equivalent side channel that desync actors use).
- [x] Confirm `async_fleet._actor_process` episode_infos reach `rollout_codec` / learner ingest with proposals intact (via `test_slim_progress_info_transport_to_merge`).
- [x] Add end-to-end test on the **desync worker path** (not just direct merge/HTTP unit tests): env emits proposal → slim/s transport → rollout payload → `go_explore_merge.ingest_proposals`.
- [ ] Verify learner `/status` shows `go_explore_accepted > 0` in a smoke run.

**Files:** `re1_rl/training_progress.py`, `re1_rl/async_fleet.py`, `tests/test_training_progress.py`, new or extended `tests/test_go_explore_http.py` / distributed parity test.

---

### 2. Decouple Go-Explore from PB capture gate

- [x] Move `_maybe_capture_go_explore()` out from behind `if not pb_capture_enabled(): return` in `RE1Env._after_reward_step()`.
- [x] PB milestone capture and Go-Explore cell capture must be independently gated by their own env flags (`RE1_PB_CAPTURE`, `RE1_GO_EXPLORE_CAPTURE`).
- [x] Test: with `RE1_PB_CAPTURE=0` and `RE1_GO_EXPLORE_CAPTURE=1`, proposals still emit.

**Files:** `re1_rl/env.py`, `tests/test_pb_capture.py`.

---

### 3. Reconcile WH2 archive after purge

- [x] After `fleet_purge_go_explore_cells.ps1 --nuke-all`, compare `archive.json` cell entries vs files under `data/go_explore/cells/<record_id>/`.
- [x] Remove archive rows whose bundles are missing (`reconcile_archive_missing_bundles` + `--reconcile-archive` on purge script).
- [ ] Run `scripts/validate_go_explore_archive.py` on WH2 before canary (after git pull).
- [ ] Document expected canonical cell count (~0 immediately post-purge).

**Files:** `scripts/purge_go_explore_orphans.py`, `re1_rl/go_explore_capture.py`, `scripts/validate_go_explore_archive.py`, WH2 `data/go_explore/archive.json`.

---

### 4. Harden byte budget

- [x] Reserve capture budget using actual bundle byte estimate (or upper bound) before emitting proposal, not `nbytes=0`.
- [x] Fail closed: if post-write bytes would exceed `RE1_GO_MAX_CAPTURE_BYTES_DAY`, reject ingest and refund count/bytes.
- [x] Add learner-side caps: max bundle size, free-space floor, optional daily canonical bytes on WH2 ingest.
- [x] Tests: concurrent capture attempts, byte cap enforcement, corrupt `capture_budget.json` fails closed.

**Files:** `re1_rl/go_explore_capture.py`, `re1_rl/go_explore_merge.py`, `tests/test_go_explore_capture.py`.

---

### 5. CPU quick wins (headroom before capture load)

- [x] **MMF-only frames:** end-of-step capture via inline MMF tag in Lua `step` response; Python mmap read (no PNG base64 in JSON). Knife macro uses `ring_stride=0` + `capture_final` like env.
- [x] **Single extractor forward:** share features between masked policy and value in `predict_masked_batch()` (`distributed/inference_policy.py`).
- [x] **`cv2.setNumThreads(1)`** in async fleet actor processes (reduces OpenCV thread oversubscription).
- [ ] Measure fleet SPS before/after on one worker; defer BizHawk RPC consolidation (mask reads) to post-canary if needed.

**Files:** `re1_rl/env.py`, `lua/re1_client.lua`, `re1_rl/bizhawk_bridge.py`, `re1_rl/distributed/inference_policy.py`, `re1_rl/async_fleet.py`.

---

## After items 1–5

1. Commit + push + fleet pull (WH2 reconcile + validate).
2. Canary on pking only: `fleet/local/start_worker_detached_pking_capture_canary.cmd`.
3. Conservative soak knobs: 10–20 captures/day, cooldown ≥600 steps, 12–16 visible envs (not 20).
4. 24h metrics: pking `cells/` ≈ 0, WH2 canonical growth ≤ budget, `go_explore_accepted` rising, capture_budget.json monotonic.
5. Fleet-wide `go_explore_capture_on.env.cmd` only after soak passes.

---

## Key references

| Doc | Role |
|-----|------|
| `docs/go_explore_disk_efficiency.plan.md` | P0/P1 disk policy, re-enable checklist |
| `fleet/local/go_explore_capture_on.env.cmd` | Capture on overlay |
| `fleet/local/start_worker_detached_pking_capture_canary.cmd` | pking canary launcher |
