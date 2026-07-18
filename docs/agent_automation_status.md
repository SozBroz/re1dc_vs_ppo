# Agent Automation Status

**Branch:** `feature/world-almanac-extractor`  
**Date:** 2026-07-17  
**Scope:** Scaffolds landed without human play / BizHawk sessions.

---

## Landed without human

| Artifact | Status | Notes |
|----------|--------|-------|
| `scripts/eval_wing_harness.py` | **Scaffold** | `--wing east\|gallery\|combat`; graft/`--ckpt`; `--dry-run` plan JSON; nightly pass-rate schema stubbed |
| `re1_rl/go_explore_archive.py` | **Scaffold** | `(room_id, tile_bin)` cells; JSON save/load; `select_frontier`; noop `save_state` callback |
| `scripts/train_bc.py` | **Scaffold** | Demo format documented; exits 1 if no demos; MaskablePPO BC loop `NotImplemented` |
| `docs/agent_automation_status.md` | **This file** | Automation ledger |

### Same campaign (other squads)

- `file_*` / `combine_*` wired; `WORLD_CONTEXT_DIM=128`; `features_dim=1587`; catalog buffers `persistent=False`
- Graft re-transplanted: `data/ppo_re1_world_almanac_graft.zip` (from 126.6M backup → 1587-d)
- Doc hygiene: `docs/exploration_rewards.md`, north_star shield≠helmet, SUPERSEDED banners
- Ammo mask: attack gated on equipped-slot rounds; WorldCatalog process cache; 10 draft story USE sites
- Map hygiene: RDT phantom filter; sword_key out; serum@102 / map_1f@106 dropped; pickups 119 → `world_state` 471-d
- Human ledger: `docs/human_needed_finish_line.md`

---

## Still requires human

| Item | Why |
|------|-----|
| Live wing eval rollouts | BizHawk + wing savestates (`wp_gallery_117`, `wp_combat_zombie`, …) |
| Go-Explore `.State` files | Capture during play; wire real `save_state` callback |
| BC demo `.npz` export | ≥2 h `play_human` East Wing; export pipeline not built |
| Map / RAM / doctrine | See `docs/human_needed_finish_line.md` (H0–H3) |

---

## Smoke commands (no emu)

```powershell
python -c "from re1_rl.go_explore_archive import GoExploreArchive, tile_bin"
python scripts/eval_wing_harness.py --help
python scripts/eval_wing_harness.py --wing east --dry-run
python scripts/train_bc.py --help
python scripts/train_bc.py
```
