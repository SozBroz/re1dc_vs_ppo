# Doc 04 Medium campaign — fresh training from torched checkpoints

**Branch:** `feature/doc04-medium-extractor`  
**Backup:** `backup/pre-doc04-medium-2026-07-22` @ `885ec61`  
**Archive manifest:** `backup/manifests/pre_doc04_medium_2026-07-22.json` (on backup branch)

## What shipped

| Item | Value |
|------|-------|
| Extractor | `RE1Doc04MediumExtractor` — typed towers, concat+LN → **1280-d** |
| Trunks | `pi/vf [512, 512]` |
| Vision | NatureCNN **512-d** (CNN-only transplant from pre-campaign zips if needed) |
| Dropped from forward | `goal`, `affordances` |
| Distributed `batch_size` | **2048** (VRAM) |
| WH2 `learner_n_envs` | **24** (was 32; RAM headroom) |

## Torched (all fleet hosts)

- `data/checkpoints/` — full `reward_tune_1040k` run removed
- `states/pb/champions/` — PB bundles removed (sidecars were already absent on disk)

## Archived checkpoints (restore from backup archive dir)

| Host | Latest zip | Steps |
|------|------------|-------|
| WH2 | `ppo_re1_104126634_steps.zip` | 104,126,634 |
| WH1 | `ppo_re1_28680354_steps.zip` | 28,680,354 |
| pking | — | no local learner ckpt |

Physical archive: `D:\re1_rl\backup\archive\pre_doc04_medium_2026-07-22\`

---

## IMPERATOR REVIEW (deferred decisions)

These need your call before we tune further — implementation proceeds with defaults:

1. **Gallery hint tail in `goal`** — still in obs wire but omitted from Doc04 forward. Keep ablating vs relocate to spatial puzzle token?
2. **GRU / IMPALA upgrade path** — deferred; NatureCNN transplant only for v1.
3. **Auxiliary heads** (pickup-active, reward-part) — not in v1; add after coverage telemetry?
4. **Resume / BC warm-start** — old 1615-d checkpoints incompatible; optional CNN-only transplant script not wired to fleet launch yet.
5. **PB champion curriculum** — champions torched; re-capture or restore from backup archive if you want PB-weighted resets immediately.
6. **C7 almanac audit** — still recommended before scaling training; not blocking code merge.
7. **λ=0.95 GAE** — still rejected per Doc 06; retain MC / λ=1.

## RAM / VRAM notes

- **WH2:** 24 local envs + `batch_size=2048` targets ~8 GB VRAM learner fit. If OOM at epoch train, drop to `batch_size=1024` or envs 20.
- **Host RAM:** epoch ingest still scales with `n_steps × fleet_envs`; monitor first 6-minute epoch before raising WH2 envs.

## Next ops (when ready to train)

1. `git pull` on WH1/WH2/pking to `feature/doc04-medium-extractor`
2. Fresh learner start — **no** `--resume auto` (architecture break)
3. Optional: `scripts/transplant` CNN-only from backup zip before learn (not automated yet)
