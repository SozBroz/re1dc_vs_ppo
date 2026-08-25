# Planner-loyal PPO architecture (planning notes — do not full-send)

Branch: `feature/planner-loyal-ppo`

## Status (2026-08-25)

### Wired now
- Env flag `RE1_PLANNER_LOYAL=1` loads a chunk into `PlannerLoyalQueue` on env init and **resets the queue each episode**.
- Optional `RE1_PLANNER_CHUNK=path` (absolute, or relative to project root). Default: `data/planner_chunks/cp05_shield_key.json`.
- Every `compute_reward` call site in `re1_rl/env.py` passes `planner_loyal_queue=` + `box_opened=` (box-UI rising edge).
- Obs key `planner_steps` (182-d) is added **only when the flag is on**. `RE1WorldAwareExtractor` flattens it automatically; `RE1CombatEfficientExtractor` adds a zero-init residual into the goal tower.
- Under planner-loyal only, route-admin goal scalars are zeroed: `waypoint_index`, `waypoints_remaining`, `curriculum_stage`, `item_todo_progress`, `wrong_room_flag`.
- Divert (−4) latches `progress.wrong_room_breached` → env `terminated` (`episode_failure=planner_divert`).
- Step success (+8) mints a cell under **`states/planner_loyal/cells/plNN`** (`pl06+` after tip).
- Seed cells **`pl00`..`pl05`** copied from **`backups/Crystals_in_time`** (`cp00`..`cp05`). Training tip / earliest start = **`pl05` `barry_hall_return_106`** (Main Hall + lockpick).
- NN sizing (fresh ckpt): `FEATURES_DIM=1024`, pi/vf `[512,512]`, WH2 batch **3072**, `GOAL_TOWER_DIM=256` + `planner_steps`. No history/world towers → concat ~1472→1024; **~5.0M** params. Doc-04 vacuum / IMPALA-3 deferred.
- Fleet learner host: **WH2** `192.168.0.116`; WH3 = dense remote worker (24 envs). Thin cells (`RE1_YAWN_LEG_REPLAY=0`). Shield-key chunk complete ends the episode; mid-CP continues.
- Obs under planner-loyal: **physically drop** strategy/almanac keys (`history`, `acquisitions`, `rooms_visited`, `cutscene_ledger`, `milestones`, `maps_files`, `affordances`, **`world_state`**) — history + world towers not built. Keep pixels / spatial / visited grid / inventory / combat / `named_state` / `planner_steps`. Queue pops on step success and slides remaining orders forward.

### Deferred
- IMPALA-3 vision backbone.
- WH3 dual-use ops automation (stop PPO ↔ Muse ↔ train).
- Fleet training run with `RE1_PLANNER_LOYAL=1` end-to-end.

## Reward (implemented skeleton)

See `re1_rl/planner_loyal.py` + `compute_reward(..., planner_loyal_queue=...)`.

- Keep: step contempt, HP damage/heal, enemy damage/kill taxes as already coded.
- Heal-use tax: green/blue herb −0.05; stronger heals −0.10.
- Planner step complete: +8 (`planner_step_success` / `checkpoint_success`).
- Divert (wrong room / unplanned pickup / unplanned box): −4 + episode terminal.

Enable via `RE1_PLANNER_LOYAL=1` + chunk JSON under `data/planner_chunks/`.

## Obs: up to 20 steps

`encode_planner_queue` → 182-d flat vector (current rich + 19 compact future).
Wired as obs key `planner_steps` when the flag is on (extractor fusion above).

## GPT 5.6 plan only (2026-08-25) — NN prune / VRAM

### Top obs prunes once planner owns strategy
1. Drop route-admin scalars in `goal`. **(wired)**
2. Asymmetric 20-step `planner_steps` queue; pops+slides on step success. **(wired)**
3. **Remove** from Dict (not zero): `history`, `acquisitions`, `rooms_visited`, `cutscene_ledger`, `milestones`, `maps_files`, `affordances`, **`world_state`**. History + world towers omitted.
4. Keep local `visited` grid (tactical nav).

### Keep
Pixels, current-room spatial, visited grid, immediate compass, inventory/box, combat, named flags, planner queue.

### WH3 / NN capacity (planner-loyal)
1. Packed batch **4096** (shipped); `FEATURES_DIM=1024`; goal **256**; pi/vf `[512,512]`. Fallback 3072 if OOM.
2. Params fall naturally with omitted towers (~5.0M); do not shrink execution capacity further without an ablation.
3. IMPALA-3 deferred.
4. Learner launcher: `fleet/local/run_distributed_learner_wh3_stack.cmd` (+ 8 local envs on 5855).

Sources: `docs/nn_architecture_review/04_gpt56_vacuum_architecture.md`, `05_current_vs_recommended.md`, `docs/world_aware_nn_architecture.md`, `docs/guidebook_obs_todo.md`.

### WH3 dual-use
Planner phase: stop PPO at boundary → start vLLM → author/validate/pin plan.  
Train phase: kill vLLM, verify CUDA free → PPO on immutable plan.  
Never hot-swap mid-rollout.

## Muse experiment

Meta **Muse Glimmer 30B** (Apache 2.0, American open weights) — see `scripts/probe_muse_phase1.py`.

### Status (2026-08-25 WH3) — Muse UP

**Serving now:** Windows CUDA `llama-server` b10621 + Meta **Q4_K_M 17GB GGUF** (`Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`) on `0.0.0.0:8000`, alias `muse-glimmer`. Survives SSH via WMI start (`_tmp/wh3_muse_wmi_start.ps1`).

**Phase-1 Pass-1 A/B (same prompt):** Muse returned the **same 13-step shield_key path** as Qwen (Kenneth clips, emblem swap, fireplace → `105:shield_key`). Score both with:

```text
python scripts/probe_muse_phase1.py --score _tmp/muse_phase1_response.json
python scripts/probe_muse_phase1.py --score _tmp/cp05_phase1_pass1_response.json
```

Restart Muse after reboot:

```text
powershell -File C:\Users\sshuser\re1_rl\_tmp\wh3_muse_wmi_start.ps1
```

### Earlier blockers (resolved)

| Check | Result |
| --- | --- |
| GPU | RTX 5090 32 GB |
| Fit path | GGUF Q4_K_M ~16.5 GB RSS (not BF16 / not stock vLLM) |
| Runtime | llama.cpp win-cuda-13.3 (no Docker) |
| SSH kill | Fixed with Win32_Process WMI Create |
