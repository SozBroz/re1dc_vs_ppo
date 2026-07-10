# Distributed fleet failure analysis (2026-07-09)

Tear-down + root-cause audit after overnight distributed PPO looked slow and “retarded” vs monolithic `train_parallel.py` (async fleet).

**Status:** training killed on pking / workhorse1 / workhorse2. Fixes tracked below.

---

## Executive summary

Not a worse checkpoint. Learner resumed **11.16M** (`ppo_re1_11160000_steps.zip`). Policy looked worse because:

1. **Only one of three machines was grinding** (pking). WH2 had **zero envs** (`--no-local-worker`). WH1 never produced rollouts (SSH spawn failure).
2. **Critical parity bug:** distributed workers **do not apply action masks at inference**; monolithic async fleet does.
3. **Workers use synced `SubprocVecEnv`**, not desync `async_fleet` actors — throughput + RAM regression.
4. **Network is not per-step** (design OK), but **every rollout** does a weight pull + a **large rollout upload** (frame tensors), blocking the worker until ACK.

---

## Intended architecture (spec)

Sources:

- [docs/distributed_learner_worker_architecture.md](distributed_learner_worker_architecture.md)
- [docs/fleet_setup.md](fleet_setup.md)

| Machine | Role |
|---------|------|
| **workhorse2** (`192.168.0.111`) | Learner **+** local BizHawk fleet |
| **workhorse1** (`192.168.0.160`) | Remote worker |
| **pking** | Remote worker |

Rules:

- Local inference on every worker (`predict` on-box).
- Network: rollout batch upload + periodic weight pull after learner `train()`.
- **No** per-env-step RPC for actions.
- Learner host always contributes envs (no `--no-local-worker` in steady state).

---

## What actually ran overnight

| Machine | Intended | Actual |
|---------|----------|--------|
| workhorse2 | Learner + local worker | Local 8 envs re-enabled; `--resume auto` |
| workhorse1 | ~8 envs | Registered; **0 rollouts** (SubprocVecEnv/EmuHawk fails over non-interactive SSH) |
| pking | ~12 envs | **24 envs**, ~90% RAM, **only feeder** |

Overnight ~**24 envs total**, not ~44. Global `model.num_timesteps` (~12.65M) is learner-global, not per-agent. With one worker: ~**1.5M new steps** overnight (11.16M → ~12.65M).

Learner log (SSH session that died on teardown): resumed 11.16M, `batch_threshold=3072` early, trained on pking only, `best_wp=0` / `ep_rew=nan` on progress callback, exit 1 from kill — not a bad resume.

---

## Network frequency

**Per env step:** local `policy.predict_batch(obs)` in `rollout_collect.py`. No HTTP. ✓

**Per rollout** (256 × N envs), remote worker:

1. `GET /weights?min_version=...` at rollout boundary
2. `POST /rollout` — full compressed batch including frames
3. Background poll every `--weight-sync-poll-s` (launchers: **360s**; early runs used **1s**)

Rough pking upload (24 envs): frames alone `256×24×84×84×4` ≈ **165 MB raw**. Worker blocks on upload before next rollout.

---

## P0 — action masks missing in distributed rollouts

Monolithic async fleet (`re1_rl/async_fleet.py`):

```python
if hasattr(env, "action_masks"):
    req["action_masks"] = env.action_masks()
act, val, lp = policy.predict_masked(obs_batch, masks)
```

Distributed `re1_rl/distributed/rollout_collect.py`:

```python
act, val, lp = policy.predict_batch(obs)  # no masks
```

`make_env()` wraps `ActionMasker`, but `collect_rollout` never reads masks / never calls `predict_masked`. Illegal actions (knife without weapon, attack in recovery, etc.) get sampled. Matches “retarded” play without a worse checkpoint.

`InferencePolicy.predict_masked` exists — **not wired**.

---

## P0 — fleet deployment vs spec

1. Remove `--no-local-worker` from WH2 learner launchers; run local fleet on learner host.
2. WH1 worker must launch **interactively on the box** (RDP/console), not bare SSH.
3. Cap pking at **12 envs** until RAM headroom is confirmed (24 was tight).

---

## P1 — SubprocVecEnv vs async_fleet — **FIXED (async on every box)**

Distributed workers now use `re1_rl/distributed/async_worker_runtime.py` (same `_actor_process` as monolithic). Expect ~30% faster step collection vs the old synced SubprocVecEnv path on the same box.

Remaining follow-up: shrink rollout HTTP payload (frames still uploaded).

---

## P1 — resume / transplant parity

Monolithic `load_async_learner` has:

- Obs-space transplant (`_transplant_into_current_spaces`)
- MaskablePPO → PPO weight import
- `TrainingProgressTracker`

Distributed `_build_learner_model` in `scripts/distributed_train_parallel.py` lacks transplant + Maskable fallback + progress tracker.

---

## P2 — docs / ops

- `fleet_setup.md` documents `--no-local-worker` as normal for WH2 — contradicts architecture MD.
- Wire `TrainingProgressTracker` into distributed learner for rooms/waypoints.

---

## Ranked root causes

1. **Action masks not used in distributed rollouts** (behavior regression)
2. **WH2 `--no-local-worker` + WH1 dead** (1/3 machines contributing)
3. **SubprocVecEnv + fat rollout uploads** (slow / network-heavy feel)
4. Missing resume transplant / progress tracking (parity + visibility)
5. Early `weight-sync-poll-s=1` / low `batch_threshold` (ops, partially fixed in launchers)

---

## Fix plan (execution)

| ID | Fix | Owner path |
|----|-----|------------|
| masks | Wire masks into `rollout_collect` + tests | `re1_rl/distributed/rollout_collect.py` |
| resume | Port `load_async_learner` into distributed learner build | `scripts/distributed_train_parallel.py` |
| progress | `TrainingProgressTracker` on distributed learner | `scripts/distributed_train_parallel.py` |
| launchers | Drop `--no-local-worker`; fix `fleet_setup.md` | `fleet/local/*`, `docs/fleet_setup.md` |
| sync | Commit, push, `git pull` on pking / WH1 / WH2 | all three hosts |

**Out of scope this pass (follow-up):** full async_fleet worker rewrite; rollout payload shrink.

---

## Restart order (after fixes land)

1. Confirm action-mask path + tests green on one box.
2. WH2: learner **with** local worker.
3. WH1: worker from interactive session.
4. pking: 12 envs.
5. Prefer `batch_threshold ≈ n_steps × total_fleet_envs` once all hosts contribute.

---

## Plan file

`D:\re1_rl\docs\distributed_fleet_failure_analysis.md`
