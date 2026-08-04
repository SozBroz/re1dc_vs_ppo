# NN Learning-Efficiency Research — RE1 Director's Cut PPO

**Status:** research synthesis (2026-08-04)  
**Companion:** [north_star.md](north_star.md), [exploration_rewards.md](exploration_rewards.md), [world_aware_nn_architecture.md](world_aware_nn_architecture.md)  
**Implementation plan:** `.cursor/plans/nn_efficiency_roadmap_82416d00.plan.md` (short actionable checklist)

This document captures the full research memo from the NN efficiency investigation: current architecture and training audit, measured bottlenecks, ranked interventions, and literature with sources. The small implementation roadmap is the execution checklist; **this file is the reference library**.

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Current network architecture](#2-current-network-architecture)
3. [Training regimen and what an epoch means](#3-training-regimen-and-what-an-epoch-means)
4. [Goals, curriculum, and rewards](#4-goals-curriculum-and-rewards)
5. [Measured bottlenecks and bugs](#5-measured-bottlenecks-and-bugs)
6. [Recommended experiment sequence](#6-recommended-experiment-sequence)
7. [Acceptance criteria and metrics](#7-acceptance-criteria-and-metrics)
8. [Technique catalog](#8-technique-catalog)
   - [8.1 Baseline repair and PPO hygiene](#81-baseline-repair-and-ppo-hygiene)
   - [8.2 Curriculum and level replay](#82-curriculum-and-level-replay)
   - [8.3 Modality utilization and goal conditioning](#83-modality-utilization-and-goal-conditioning)
   - [8.4 Dropout, freezing, and learning-rate schedules](#84-dropout-freezing-and-learning-rate-schedules)
   - [8.5 Auxiliary losses and phasic training](#85-auxiliary-losses-and-phasic-training)
   - [8.6 Self-supervised and predictive representations](#86-self-supervised-and-predictive-representations)
   - [8.7 Plasticity and representation collapse](#87-plasticity-and-representation-collapse)
   - [8.8 Imitation, demonstrations, and hindsight](#88-imitation-demonstrations-and-hindsight)
   - [8.9 Distributed RL and off-policy correction](#89-distributed-rl-and-off-policy-correction)
   - [8.10 Recurrence and memory](#810-recurrence-and-memory)
   - [8.11 Normalization and value scaling](#811-normalization-and-value-scaling)
   - [8.12 Data augmentation](#812-data-augmentation)
   - [8.13 Gradient surgery and multi-task conflict](#813-gradient-surgery-and-multi-task-conflict)
   - [8.14 Action masking requirements](#814-action-masking-requirements)
9. [Bibliography](#9-bibliography)

---

## 1. Executive summary

### Judgment

The policy is **not obviously under- or over-parameterized** (~5.8M params, hard cap 5.8M). The dominant near-term inefficiency is **learner batching**: one fleet update is fragmented into ~154 independent `model.train()` calls grouped by `(policy_version, n_steps)`, producing ~4.5× optimizer overhead and giving tiny rollout groups disproportionate influence.

The next constraints, in order:

1. **Curriculum coverage** — 53 Yawn checkpoints defined, only 4 reset cells manifested (`cp00`–`cp02`, `cp04`; `cp03` missing).
2. **Goal compression** — 108-dim goal + 17-dim logistics fused into a 48-dim tower (2.7% of fusion width).
3. **Auxiliary overhead** — extractor recomputed after `evaluate_actions`; combat targets GPU→CPU→GPU per minibatch.
4. **No held-out evaluation** — training reward alone is insufficient diagnostic.
5. **Ordinary dropout is unsafe** in PPO; freezing alone does not force alternative inputs.

### Highest-confidence interventions

| Priority | Intervention | Evidence |
|----------|--------------|----------|
| 1 | Pack fleet PPO updates into one sample-weighted buffer | Implementation + PPO studies |
| 2 | Fix curriculum launcher parity + restore `cp03` | Project-specific |
| 3 | Enable `target_kl` + log policy lag / ratio ESS | PPO practice |
| 4 | GAE λ sweep (0.99–1.0) vs current λ=1 MC segments | GAE, Andrychowicz et al. |
| 5 | Per-tower utilization diagnostics before architectural changes | Multimodal RL literature |
| 6 | Single-network PPG if policy/value/aux interference measured | Cobbe et al. ICML 2021 |
| 7 | PLR-style fresh-rollout cell sampler | Jiang et al. ICML 2021 |
| 8 | Policy-consistent structured modality dropout (not `nn.Dropout`) | Consistent Dropout, ModDrop |

### What to defer

PopArt, recurrent PPO, V-trace/IMPALA, GePPO/LASER replay, HPG, world models, RND, PBT — until the corresponding bottleneck is **measured** (value-scale drift, observation aliasing, actor lag, goal relabelling need, dynamics modelling need).

---

## 2. Current network architecture

**Policy class:** `CombatEfficientPPO` (extends `MaskablePPO`)  
**Extractor:** `RE1CombatEfficientExtractor` (`re1_rl/combat_efficient_extractor.py`)  
**Config:** `re1_rl/policy_config.py` — `net_arch=[512,512]` pi/vf, `AUX_COEF=0.02`

### Tower dimensions

| Module | Dim | Notes |
|--------|-----|-------|
| Vision (NatureCNN) | 512 | 63×84×4 frames, `normalized_image=False` |
| Control / proprio | 64 | |
| Spatial tower | 192 | items, enemies, exits |
| Inventory tower | 160 | inventory + box |
| History tower | 192 | room deque + acquisition log |
| Flags tower | 64 | milestones, key items |
| Goal tower | 48 | base + lookahead + logistics |
| Joint combat | 128 | |
| World context (almanac) | 320 | static Evil Resource buffers |
| Persistent (optional) | 96 | |
| **Concat (TOWER_OUT_DIM)** | **1776** | stale comment says 1728 |
| Fusion | LayerNorm → Linear 1776→1024 → ReLU | ~1.82M params |
| Actor MLP | 1024→512→512 | |
| Critic MLP | 1024→512→512 | |
| Action head | 45 masked categorical | |
| Value head | scalar | |
| Aux heads | combat outcome, world events | |

**Total parameters:** 5,799,931 (69 below `PARAM_HARD_CAP = 5_800_000`)

**Parameter budget:** ~58.5% in fusion_proj + actor/critic MLPs; NatureCNN ~996k.

### Architectural strengths

- Typed embeddings for items, enemies, rooms, cameras.
- Masked pooling over variable entity sets.
- Explicit inventory, box, flags, milestones, episode history.
- Static almanac world context (no learned room-order head).
- 4-frame grayscale stack; action masking at collection and training.
- Joint combat latent + supervised combat/world auxiliary heads.
- No hidden recurrent state to go stale across distributed actors or savestate resets.

### Architectural concerns

**Goal bottleneck.** `GOAL_DIM` is 108 (30 base + 6×13 lookahead) plus 17 logistics, but all compress to 48 dims before fusion — only 2.7% of the 1776-dim concat. Counterfactual test required: fix physical state, change checkpoint, measure policy KL and value shift.

**History partly orderless.** Room history has ages; 60-entry acquisition log is mean/max pooled without position — the policy sees a set, not a sequence. Position/age embeddings are cheaper than full recurrence.

**Auxiliary shortcut risk.** World-event head sees full privileged tower; BCE not positive-balanced; gradients hit shared features every minibatch.

---

## 3. Training regimen and what an epoch means

Three distinct "epoch" concepts:

| Term | Meaning | Current value |
|------|---------|---------------|
| **Fleet epoch** | Wall-clock collection window + learner train | `sync_interval_s=360`, grace ~120s |
| **PPO epoch** | Shuffled passes over learner buffer | `n_epochs=4` |
| **Rollout segment** | MC/bootstrap horizon per env | `n_steps=1125` (~6× γ half-life) |

### Distributed hyperparameters (`DISTRIBUTED_EPOCH_HYPERPARAMS`)

```python
n_steps=1125         # 6 × discount half-life at RL_GAMMA (≈150s emulated)
batch_size=2048
n_epochs=4
learning_rate=1e-4
gamma=0.99631        # half-life ≈187.5 steps ≈25s emulated @ frame_skip=8
ent_coef=0.006
clip_range=0.2
vf_coef=0.5
max_grad_norm=0.5
target_kl=None       # implemented but not enabled
aux_coef=0.02
```

**Credit assignment:** `compute_episode_mc_returns` in `learner_train.py` — segment Monte Carlo with endpoint bootstrap (λ=1 GAE equivalent), **not** standard GAE with λ<1.

**Advantage normalization:** global whitening once per policy-version group; per-minibatch re-normalization disabled (avoids size-1 NaN batches).

### Fleet topology (observed)

- WH2 learner: 28 envs
- WH1: 8 envs
- pking: 19 envs (+ memlog canary)
- **Total:** 56 envs
- Frame skip / action repeat: 8
- `max_staleness=1`, `--relevance-gate` active

### Monolithic path (not fleet default)

`PPO_HYPERPARAMS`: `n_steps=1024`, `batch_size=512`, `lr=3e-4` — used by non-distributed async, not current fleet learner.

### Latest inspected fleet update (Epoch 7 snapshot)

| Metric | Value |
|--------|-------|
| Accepted transitions | 73,583 |
| Training groups `(policy_version, n_steps)` | 154 |
| Group size range | 32 – 2,996 (avg ~478) |
| Adam steps | 648 |
| Packed equivalent (2048 batch) | ~144 steps |
| Optimizer multiplier | **4.5×** |
| Effective avg minibatch | ~454 (not 2048) |
| Train wall time | ~8m19s post-collection |
| Episodes completed | 379 |
| Checkpoint success | 25 (6.6%) |
| Truncation (stagnation) | 335 (88.4%) |
| Illegal pre-Kenneth MH | 19 (5.0%) |
| Mean ep length | 197.8 steps |
| Successful ep length | 115.4 steps |
| Policy version | 7 |
| Cumulative steps | 66,397,368 (includes transplants) |

---

## 4. Goals, curriculum, and rewards

### North Star (unchanged)

- Beat full game via learned primitives on **goal-conditioned rails**.
- Rails supply checkpoint + compass; policy chooses all buttons.
- Combat macros allowed; navigation/puzzle macros forbidden.
- Box RAM inventory transfers explicitly allowed.
- `goal` fields are **required live NN inputs** — not reward-only hints.

### Yawn curriculum

- **Route:** 53 checkpoints in `data/yawn_checkpoint_route.json`
- **Breakdown:** 30 rooms, 35 navigation, 12 pickups, 5 uses, 1 fight
- **One-leg cap:** 2,700 steps; chaining target 6 legs (16,200 steps)
- **Manifested cells:** `cp00`, `cp01`, `cp02`, `cp04` only — **`cp03` missing**
- **Active curriculum file:** `curriculum/yawn_rails_one_leg.json`

### Launcher hazard

Learner and most workers pass `--curriculum curriculum/yawn_rails_one_leg.json`, but:

- `scripts/distributed_train_parallel.py` defaults to `curriculum/m0_dining_to_main_hall.json`
- `fleet/local/run_distributed_worker_pking.cmd` (20-env) omits `--curriculum`

Rollout ingestion does not carry curriculum identity — silent mixing is possible.

### Yawn rails reward contract (`re1_rl/reward.py`)

Active curriculum: `curriculum/yawn_rails_one_leg.json` (`rails_mode=True`). Canonical policy: [exploration_rewards.md](exploration_rewards.md).

#### Scaling tiers (rails only)

| Tier | Scale | Terms |
|------|-------|-------|
| **Checkpoint terminal** | unscaled | `checkpoint_success` **+12.0** (`RAILS_CHECKPOINT_REWARD`) — ends one-leg episode on atomic waypoint |
| **Navigation milestones** | **×1.0** (full) | `new_room`, `new_cutscene`, `document_examine`, `key_item`, `story_use`, `dining_statue`, `new_weapon`, `ammo_pickup`, `gallery` |
| **Minor crumbs** | **×0.05** | `item`, `pbrs_graph`, `pbrs_door`, `typewriter_save` |
| **Combat positives** | unscaled | `enemy_damage`, `enemy_kill` (miss taxes also unscaled so combat stays learnable) |
| **Negatives / clawbacks** | full magnitude | `wrong_room`, `retreat`, `death`, `main_hall_before_kenneth`, `hp`, `attack_*`, `ammo_waste`, `softlock`; clawbacks `shotgun_return`, `gold_emblem_return`, `key_item_return` at nav scale |

Non-rails exploration contract uses `checkpoint_success=+1.2` (`CHECKPOINT_REWARD`) and no positive scaling.

#### Positive signals (base magnitudes before rails scale)

| Signal | Magnitude | Notes |
|--------|-----------|-------|
| `new_room` | **+4.0** | First visit per episode; extends 6 min idle cap |
| `document_examine` | **+4.0** | Rising edge into examine UI; once per room per episode |
| `new_cutscene` | **+1.2** | Only when paired with a newly rewarded room entry on same transition |
| `key_item` | **+4.0** | Once per key name per episode |
| `story_use` | **+4.0** | Verified story-use sites |
| `new_weapon` | **+4.0** | First acquire per weapon type; shotgun re-takes pay but don't extend idle clock |
| `ammo_pickup` | **+2.0** | Ammunition stacks |
| `gallery` | **+0.5** per correct switch | Room 117 ordered portraits; clawback on wrong/exit |
| `dining_statue` | **+4.0** | Statue knocked down |
| `typewriter_save` | **+0.3** | Completed save edge; no idle extension |
| `item` | **+0.15** | Junk/herbs (non-key, non-ammo) |
| `enemy_damage` | **+0.007** / HP | Knife/attack actions only |
| `enemy_kill` | **+0.24** / kill | Knife/attack actions only |
| `pbrs_graph` / `pbrs_door` | PBRS potentials | Weights 0.02 / 0.05; `SHAPING_GAMMA=1.0` |

#### Negative signals (full magnitude on rails)

| Signal | Magnitude | Notes |
|--------|-----------|-------|
| `step` | **−0.00024** / ref step (8 frames) | Living cost |
| `wrong_room` | **−1.0** | Off-rails room change (once per room) |
| `retreat` | **−0.6** | Leave target waypoint room before completing |
| `death` | **−0.333** | Survival budget 1/3 |
| `main_hall_before_kenneth` | **−0.05** | Illegal pre-Kenneth 106 entry; terminates episode |
| `hp` (damage) | **−0.00702** / HP | Linear; heal is exact inverse |
| `attack_miss` (knife) | **−0.001** | Knife whiff |
| `ammo_waste` | clip-inverse tax | `−AMMO_PICKUP_BONUS/clip × 0.10` per missed round; ramps on last rounds |
| `attack_dry_fire` | **−0.005** | |
| `attack_macro_failure` | **−0.01** | |
| `gold_emblem_return` | **−4.0** | Put-back farm |
| `shotgun_return` | **−4.0** | Rack replace |
| `key_item_return` | **−4.0** | Key leaving inventory (except story use / box) |
| `softlock` | contempt ramp | 3 min grace → 3–6 min ramp; extensions on nav milestones |

#### Behavioral gates

- **Cutscene ledgers:** `observed_cutscenes` (progression, incl. Kenneth) vs `rewarded_cutscenes` (payout). Same-room interact/cutscene spam never pays.
- **Kenneth gate:** first illegal 106 transition → −0.05 + terminate; all further positive rewards zeroed for episode (`kenneth_gate_breached`).
- **Spawn room 105:** first-step `new_room` pays dining discovery without waiting for a later settle.

**PBRS note:** `PBRS_GRAPH_WEIGHT=0.02`, `PBRS_DOOR_WEIGHT=0.05`, but `SHAPING_GAMMA=1.0` while PPO uses `RL_GAMMA=0.99631`. This is **not** policy-invariant PBRS per Ng et al.; treat as intentional directional shaping (minor tier on rails: ×0.05). Log against 88% truncation rate before changing. **Reward edits require explicit approval.**

### Reliability math

Even 95% per-leg success ⇒ `0.95^53 ≈ 6.6%` full-route success — matches observed aggregate success rate; per-cell evaluation is essential.

---

## 5. Measured bottlenecks and bugs

### 5.1 Fragmented PPO training (critical)

`group_rollouts_for_train` → one `model.train()` per `(policy_version, n_steps)` group. Tiny groups get equal optimizer weight to large ones. Advantages whitened per group, not fleet-wide.

**Fix:** compute returns at true boundaries → flatten accepted transitions → single fleet buffer → one `model.train()` → weighted metrics.

### 5.2 Duplicate auxiliary forward pass

`evaluate_actions` already runs extractor; `predict_aux` runs it again each minibatch.

**Fix:** cache tower activations from policy forward; precompute combat/world target vectors once per fleet update.

### 5.3 Per-minibatch GPU sync

Combat targets copied GPU→CPU in Python loop→GPU every minibatch × 4 epochs. Multiple `.cpu()` metric reads per minibatch.

### 5.4 Loadout scorer semantics

`frozen_loadout_scorer` refreshed per group, not per fleet epoch — contradicts "frozen-per-learner-epoch" intent.

### 5.5 Logger overwrite

Custom train logger may record only the last small group's metrics per fleet update.

### 5.6 Relevance gate

Filters stale rollouts but does **not** correct off-policy returns, advantages, or clip center. For Epoch 7, all rollouts were kept.

### 5.7 `target_kl` disabled

KL early stopping is implemented in `combat_ppo.py` but `target_kl=None` in hyperparams.

### 5.8 Curriculum / eval gaps

- Missing `cp03` breaks contiguous early route
- No periodic held-out per-cell evaluation
- One anomalous success with zero rooms/items — telemetry audit needed

### 5.9 Stale documentation

- `TOWER_OUT_DIM` comment says 1728, actual 1776
- `env.py` frame shape comments may disagree with `FRAME_SHAPE=(63,84,4)`

---

## 6. Recommended experiment sequence

1. **Baseline repair** — pack updates, cache aux activations, vectorize targets, curriculum/schema identity on rollouts, fleet-weighted metrics.
2. **Curriculum trust** — restore `cp03`, equal per-cell held-out eval, uniform-floor PLR-style cell sampler.
3. **Three-seed baseline** — same checkpoint, same reset distribution, report IQM + probability of improvement ([RLiable](https://agarwl.github.io/rliable/)).
4. **PPO safeguards** — enable `target_kl` sweep (0.01/0.02/0.03); GAE λ sweep (0.99, 0.995, 1.0) independent of batching fix.
5. **Modality diagnostics** — tower rank, dormant fraction, gradient norms, counterfactual ablation KL.
6. **Goal conditioning** — identity-init FiLM/gated fusion **only if** goal counterfactuals show weak influence.
7. **Structured modality dropout** — rollout-consistent masks, ~5% branch outage, never unstored `nn.Dropout`.
8. **PPG auxiliary phase** — if gradient conflict measured; clone KL on masked policy.
9. **Conditional** — PFO/ReDo (plasticity), V-trace (actor lag), recurrence (aliasing), imitation (rare legs).

---

## 7. Acceptance criteria and metrics

### Acceptance

- No regression in per-cell success or previously learned cells.
- Each accepted change improves **checkpoint-success AUC per environment step** or **per wall-clock hour** with uncertainty intervals across seeds/cells.
- Goal-conditioned rails remain observational; reward changes require explicit approval.

### Primary metrics

| Category | Metrics |
|----------|---------|
| Learning | Checkpoint-success AUC/step, AUC/hour, macro-avg and worst-decile cell success |
| Curriculum | Peak-to-current forgetting, 2/4/6-leg completion, full-route progress |
| PPO health | Policy KL, clip fraction, importance-ratio ESS, completed epochs, early-stop count |
| System | Learner time, effective minibatch, optimizer steps, policy-version lag |
| Modality | Tower activation rank, dormant %, gradient norms, counterfactual action KL |

Report with **≥3 seeds**, preferably 5; use IQM and stratified bootstrap CIs, not single-run means.

---

## 8. Technique catalog

Evidence labels used throughout:

- **Established/direct** — PPO/actor-critic evidence close to mechanism + maintained implementations.
- **Supported transfer** — strong primary evidence, different domain/architecture.
- **Speculative** — plausible, not validated for masked goal-conditioned pixel PPO.

---

### 8.1 Baseline repair and PPO hygiene

#### Pack fleet PPO updates

**Mechanism.** Flatten all accepted transitions after per-segment return computation; single advantage whitening; shuffle into 2048-sample minibatches; one `model.train()`; one weight snapshot.

**Evidence:** Established/direct (implementation correctness + PPO batch semantics).

**Project fit:** **Highest priority.** Addresses 4.5× optimizer overhead and misleading metrics.

**Files:** `re1_rl/distributed/learner_train.py`, `re1_rl/combat_ppo.py`

---

#### Target-KL early stopping

**Mechanism.** After each minibatch, estimate `D_KL(π_old || π_new)`; stop epochs when `KL > 1.5 × target_kl`. Already in `CombatEfficientPPO.train()`.

**Sources:** [PPO](https://arxiv.org/abs/1707.06347), [Spinning Up PPO](https://spinningup.openai.com/en/latest/algorithms/ppo.html), SB3/MaskablePPO docs.

**Evidence:** Established implementation practice.

**Project fit:** Very high — config-only after batching fix. Sweep `target_kl ∈ {0.01, 0.02, 0.03}`.

**Risks:** Stopping policy epochs also stops value/aux optimization in combined loss; noisy minibatch can trigger early stop.

---

#### GAE vs λ=1 segment MC returns

**Mechanism.** GAE: `Â_t = Σ_l (γλ)^l δ_{t+l}` where `δ_t = r_t + γV(s_{t+1}) - V(s_t)`. Lower λ → more critic, less variance.

**Sources:** [GAE](https://arxiv.org/abs/1506.02438), [Andrychowicz et al. ICLR 2021](https://openreview.net/forum?id=nIAxjsniDzg).

**Evidence:** Established/direct.

**Project fit:** High. Current `compute_episode_mc_returns` is λ=1. Conservative sweep: `λ ∈ {0.99, 0.995, 1.0}` — **not** 0.95 first (too short at γ=0.99631: λ=0.95 half-life ~13 steps).

**Prerequisites:** Contiguous trajectories; correct terminal bootstrap at checkpoint/death; preserve episode boundaries across merged workers.

---

#### Rollout size, batch size, epochs, learning rate

**Mechanism.** Samples revisited ~`n_epochs` times; optimizer steps ≈ `n_epochs × ⌈N/B⌉`. Larger `n_steps` → longer credit fragments; larger `K` → more off-policy within update.

**Sources:** [Andrychowicz et al.](https://openreview.net/forum?id=nIAxjsniDzg), [Implementation Matters](https://openreview.net/forum?id=r1etN1rtPB).

**Evidence:** Established/direct (MuJoCo; domain caveat for pixel games).

**Project fit:** Keep `n_epochs=4` baseline; test `{1,2,4}` **after** KL guard. Joint LR×batch sweep. Do not increase epochs before measuring policy lag.

**Note:** `n_steps=1125` ≈ 6 half-lives; `γ^1125 ≈ 0.016`. Further increase unlikely to help first.

---

#### Value clipping

**Mechanism.** Clip value updates analogous to policy ratio clipping.

**Evidence:** **Against** — Andrychowicz et al. found harmful on all 5 MuJoCo tasks.

**Project fit:** Keep `clip_range_vf=None` (current default).

---

#### Entropy schedules

**Mechanism.** Add `c_H H[π]` to policy objective.

**Sources:** [Ahmed et al. ICML 2019](https://proceedings.mlr.press/v97/ahmed19a.html), Andrychowicz et al.

**Evidence:** Fixed entropy established; schedules heuristic.

**Project fit:** Keep `ent_coef=0.006`; sweep fixed values before schedules. Log entropy normalized by `log(valid_action_count)`. Monotonic decay risky when later checkpoints expose new states.

---

#### Adaptive KL penalty (PPO variant)

**Mechanism.** Adjust β in KL penalty term based on measured KL.

**Evidence:** Original PPO paper showed clipped PPO beat adaptive-KL variants on continuous control.

**Project fit:** Low — use target-KL stopping first.

---

### 8.2 Curriculum and level replay

#### Prioritized Level Replay (PLR)

**Mechanism.** Treat `(checkpoint cell, segment span, reset variant)` as a level; prioritize by learning progress, staleness, or regression; **fresh rollouts** under current policy (not old PPO batch replay).

**Sources:** [Jiang et al. ICML 2021](https://proceedings.mlr.press/v139/jiang21b.html).

**Evidence:** Supported transfer.

**Project fit:** High after per-cell eval exists. Retain **uniform atomic-cell floor** to prevent forgetting.

---

#### ALP-GMM

**Mechanism.** Gaussian-mixture model over learning progress to adapt reset distribution.

**Sources:** [Portelas et al. ICML 2020](https://proceedings.mlr.press/v100/portelas20a.html).

**Evidence:** Supported transfer.

**Project fit:** Medium — alternative to hand-tuned PLR weights.

---

#### Reverse Curriculum Generation

**Mechanism.** Start near goal, gradually move start state backward.

**Sources:** [Florensa et al. ICML 2017](https://proceedings.mlr.press/v78/florensa17a.html).

**Evidence:** Supported transfer.

**Project fit:** Medium — Yawn route is already forward-ordered; useful for hard individual legs.

---

#### Go-Explore / savestate archive

**Mechanism.** Archive promising states; reset to archived cells for local exploration.

**Sources:** [Ecoffet et al.](https://arxiv.org/abs/1812.03381), project Go-Explore phase docs.

**Evidence:** Established for sparse exploration; **already partially implemented** in fleet.

**Project fit:** High for discovery; separate from PPO efficiency but feeds curriculum cells.

---

#### Curriculum widening

**Mechanism.** Expand `max_legs` independently per endpoint: 1 → 2 → 3 → 4 → 6.

**Project fit:** High — avoid global jump to 6-leg episodes before single-leg cells are reliable.

---

### 8.3 Modality utilization and goal conditioning

#### Per-tower diagnostics

**Measure:** activation RMS, effective rank, ReLU dormant fraction, gradient RMS at fusion interface, policy/value/aux gradient norms, update-to-weight ratio, counterfactual action KL with tower zeroed/ablated, closed-loop per-cell success with modality removed.

**Sources:** [Wu et al. ICML 2022 multimodal](https://proceedings.mlr.press/v162/wu22d.html), [Gradient Starvation NeurIPS 2021](https://proceedings.neurips.cc/paper/2021/file/0987b8b338d6c90bbedd8631bc499221-Paper.pdf).

**Project fit:** **Required before** dropout/freezing/FiLM experiments.

---

#### Universal Value Function Approximators (UVFA)

**Mechanism.** Condition policy/value on goal; generalize across goals.

**Sources:** [Schaul et al. ICML 2015](https://proceedings.mlr.press/v37/schaul15.html).

**Evidence:** Established/direct.

**Project fit:** **Already aligned** — goal tower + compass + lookahead. Question is whether 48-dim compression is sufficient.

---

#### FiLM / gated goal conditioning

**Mechanism.** Goal embedding produces scale/shift for spatial/visual features: `γ(g) ⊙ h + β(g)`. Identity initialization preserves baseline at start.

**Sources:** [Perez et al. AAAI 2018](https://ojs.aaai.org/index.php/AAAI/article/view/11671).

**Evidence:** Supported transfer.

**Project fit:** Medium — test only if goal counterfactual KL is low. Keep all live goal fields.

---

### 8.4 Dropout, freezing, and learning-rate schedules

#### Ordinary dropout in PPO — **do not use**

**Mechanism.** Random neuron dropout during forward pass.

**Sources:** [Consistent Dropout for Policy Gradient RL](https://arxiv.org/abs/2202.11818).

**Evidence:** Established/direct — **mismatch between rollout and update masks breaks importance ratios** even before parameter change.

**Project fit:** **Forbidden** without storing mask in rollout.

---

#### Policy-consistent structured modality dropout

**Mechanism.** Drop one semantic branch (not goal/compass); expose presence bit; mask chosen before action; fixed for episode/segment; stored in rollout; same mask at all PPO epochs; majority full-input episodes; start ~5% outage.

**Sources:** [ModDrop TPAMI 2015](https://doi.org/10.1109/TPAMI.2015.2461544), Consistent Dropout arXiv 2022.

**Evidence:** Supported transfer with PPO consistency requirement.

**Project fit:** Medium after diagnostics. Never drop goal/compass.

---

#### Layer freezing

**Mechanism.** Set `requires_grad=False` on selected modules for N updates.

**Caveat:** Frozen branch outputs still feed fusion — **does not force use of other branches**.

**Sources:** [ULMFiT ACL 2018](https://aclanthology.org/P18-1031/), [Alternating Unimodal Adaptation CVPR 2024](https://openaccess.thecvf.com/content/CVPR2024/papers/Zhang_Multimodal_Representation_Learning_by_Alternating_Unimodal_Adaptation_CVPR_2024_paper.pdf).

**Evidence:** Supported transfer for transfer learning; speculative for online PPO.

**Project fit:** Use to **protect** mature CNN/world tower during curriculum transition, not to "squeeze" other inputs. Combine with weaker-branch objective if reliance is the goal.

---

#### Discriminative learning rates

**Mechanism.** Mature towers 0.1–0.3× LR; goal/history/fusion full LR.

**Project fit:** Medium — safer than cyclic freezing for balancing modalities.

---

### 8.5 Auxiliary losses and phasic training

#### Current auxiliary heads

Combat outcome + world event prediction (`AUX_COEF=0.02`). UNREAL-style shared representation.

**Sources:** [UNREAL](https://arxiv.org/abs/1611.05397).

**Issue:** Gradients on every minibatch; world head can shortcut via privileged tower.

---

#### Phasic Policy Gradient (PPG)

**Mechanism.**

1. **Policy phase:** PPO on policy; detach value gradients from shared features.
2. **Accumulate** states + value targets across updates.
3. **Auxiliary phase:** train value (+ optional aux) through shared features; constrain policy with BC KL to pre-phase distribution.

**Sources:** [Cobbe et al. ICML 2021](https://proceedings.mlr.press/v139/cobbe21a.html).

**Evidence:** Supported transfer — Procgen sample efficiency gains.

**Project fit:** **High** — single-network PPG fits 5.8M param budget; addresses policy/value/aux interference without doubling encoders. Infrequent auxiliary phases; masked-policy clone KL.

---

#### Gradient-similarity auxiliary gating

**Mechanism.** Scale or drop auxiliary loss when aux gradient conflicts with policy gradient.

**Sources:** [Liu et al. arXiv 2018](https://arxiv.org/abs/1812.02224).

**Evidence:** Supported transfer.

**Project fit:** Medium — after gradient cosine diagnostics.

---

#### DAAC / IDAAC

**Mechanism.** Separate policy/value encoders; advantage prediction auxiliary; IDAAC adds temporal invariance.

**Sources:** [Raileanu & Fergus ICML 2021](https://proceedings.mlr.press/v139/raileanu21a.html).

**Evidence:** Supported for **generalization**; speculative for fixed-game efficiency.

**Project fit:** Low — doubles visual encoder over budget. IDAAC invariance may erase needed history/route signal. Prefer single-network PPG.

---

### 8.6 Self-supervised and predictive representations

#### Self-Predictive Representations (SPR)

**Mechanism.** Predict latent state under action sequence; contrastive or BYOL-style.

**Sources:** [Schwarzer et al. ICLR 2021](https://openreview.net/forum?id=uCQfPZwRaUu).

**Evidence:** Supported transfer (Atari).

**Project fit:** Medium — predict control-relevant events, not pixels.

---

#### Horde / GVFs

**Mechanism.** Many pseudo-reward value functions sharing representation.

**Sources:** [Sutton et al. AAMAS 2011](http://incompleteideas.net/papers/horde-aamas-11.pdf).

**Project fit:** Medium — room transition, pickup, damage GVFs at multiple horizons.

---

#### CPC / ATC / CURL

**Mechanism.** Contrastive learning on augmented temporal pairs.

**Sources:** [CPC arXiv 2018](https://arxiv.org/abs/1807.03748), [ATC](https://arxiv.org/abs/2005.02149), [CURL](https://arxiv.org/abs/2004.04136).

**Project fit:** Low-medium — static backgrounds, small objects; mild identical shifts across 4 frames safer than aggressive crop.

---

#### DrQ / DrAC

**Mechanism.** Data-regularized Q-learning / actor-critic with augmentation.

**Sources:** [DrQ](https://arxiv.org/abs/2004.08136), [DrAC](https://arxiv.org/abs/2007.02447).

**Project fit:** Low — augmentation risk on RE1 UI elements and items.

---

### 8.7 Plasticity and representation collapse

#### Proximal Feature Optimization (PFO)

**Mechanism.** Regularize feature drift from reference network during PPO.

**Sources:** [arXiv 2024](https://arxiv.org/abs/2405.00662).

**Evidence:** Supported transfer for PPO representation collapse.

**Project fit:** First choice if feature rank / dormant metrics deteriorate through curriculum expansion.

---

#### ReDo (Reducing Dormant Neurons)

**Mechanism.** Periodically reinitialize dead neurons.

**Sources:** [Sokar et al. ICML 2023](https://proceedings.mlr.press/v202/sokar23a.html).

**Project fit:** Second choice after PFO.

---

#### InFeR (Initial Feature Regularization)

**Mechanism.** Penalize deviation from initial feature activations.

**Sources:** [arXiv 2022](https://arxiv.org/abs/2204.09560).

**Project fit:** Alternative plasticity intervention.

---

#### Continual Backpropagation (CBP)

**Mechanism.** Replace low-utility hidden units during training.

**Sources:** [Nature 2024](https://www.nature.com/articles/s41586-024-07711-7).

**Project fit:** Speculative — high implementation cost.

---

#### Primacy bias

**Mechanism.** Early transitions dominate; resetting heads can help with replay agents.

**Sources:** [Nikishin et al.](https://arxiv.org/abs/2205.11543).

**Project fit:** Low for pure on-policy PPO without replay.

---

### 8.8 Imitation, demonstrations, and hindsight

#### Self-Imitation Learning (SIL)

**Mechanism.** Replay past trajectories with positive advantages as BC targets.

**Sources:** [Oh et al. ICML 2018](https://proceedings.mlr.press/v80/oh18b.html).

**Project fit:** Medium — 25 recent successful legs are valuable; on-policy compatible variant.

---

#### DAPG / Kickstarting

**Mechanism.** Behavior cloning initialization + decaying demonstration auxiliary.

**Sources:** [Rajeswaran et al. RSS 2018](https://www.roboticsproceedings.org/rss14/p49.html), [Kickstarting](https://arxiv.org/abs/1803.03835).

**Project fit:** Medium for persistently hard legs; confirm purity boundary with user.

---

#### Hindsight Experience Replay (HER)

**Mechanism.** Relabel goals in replay buffer.

**Sources:** [Andrychowicz et al. NeurIPS 2017](https://arxiv.org/abs/1707.01495).

**Project fit:** Low — off-policy; not MaskablePPO add-on.

---

#### Hindsight Policy Gradients (HPG)

**Mechanism.** On-policy goal relabelling with importance weights.

**Sources:** [Rauber et al. ICLR 2019](https://openreview.net/forum?id=Bkg2viA5FQ).

**Project fit:** **Low** — rails rewards depend on sanctioned route; relabelling detours as success opposes curriculum. Only in deliberately isolated subproblems.

---

### 8.9 Distributed RL and off-policy correction

#### V-trace / IMPALA

**Mechanism.** Importance-weighted TD with truncated ρ and c for actor-learner lag.

**Sources:** [Espeholt et al. ICML 2018](https://proceedings.mlr.press/v80/espeholt18a.html).

**Evidence:** Established for distributed lag.

**Project fit:** High **if** actor lag remains after packed updates. Relevance gate ≠ V-trace.

---

#### APPO (Sample Factory / IMPACT)

**Mechanism.** PPO clipping + V-trace; IMPACT adds target policy and bounded buffer passes.

**Sources:** [Petrenko et al. ICML 2020](https://proceedings.mlr.press/v119/petrenko20a.html), [IMPACT arXiv 2019](https://arxiv.org/abs/1912.00167).

**Project fit:** Medium — port V-trace into existing learner rather than RLlib migration.

---

#### GePPO

**Mechanism.** Multi-policy sample reuse with generalized clipping and V-trace GAE.

**Sources:** [Queeney et al. NeurIPS 2021](https://proceedings.neurips.cc/paper/2021/hash/63c4b1baf3b4460fa9936b1a20919bec-Abstract.html).

**Project fit:** Medium-later — requires full off-policy correction package.

---

#### ACER / CLEAR / LASER

**Sources:** [ACER ICLR 2017](https://arxiv.org/abs/1611.01224), [CLEAR NeurIPS 2019](https://papers.nips.cc/paper/2019/hash/fa7cdfad1a5aaf8370ebeda47a1ff1c3-Abstract.html), [LASER ICML 2020](https://proceedings.mlr.press/v119/schmitt20a.html).

**Project fit:** Low — major algorithm redesign; CLEAR if curriculum forgetting demonstrated.

---

### 8.10 Recurrence and memory

#### Recurrent PPO

**Mechanism.** LSTM/GRU in policy; sequence fragments; stored hidden states.

**Sources:** [RecurrentPPO SB3](https://sb3-contrib.readthedocs.io/en/master/modules/ppo_recurrent.html), [Ni et al. ICML 2022](https://proceedings.mlr.press/v162/ni22a.html), [R2D2 ICLR 2019](https://openreview.net/forum?id=r1lyTjAqYX).

**Evidence:** Supported transfer.

**Project fit:** Low-medium now. Explicit goal/history/frames already externalize much state. **No MaskableRecurrentPPO** in sb3-contrib. Test acquisition-order embeddings first. More compelling for multi-leg episodes.

---

### 8.11 Normalization and value scaling

#### VecNormalize

**Mechanism.** Running obs/reward normalization.

**Sources:** [SB3 VecNormalize](https://stable-baselines3.readthedocs.io/en/master/guide/vec_envs.html#vecnormalize).

**Project fit:** Low for blanket use — typed bounded fields + LayerNorm; reward magnitudes deliberately calibrated.

---

#### PopArt

**Mechanism.** Normalize value targets; rescale value head to preserve predictions.

**Sources:** [van Hasselt et al. NeurIPS 2016](https://proceedings.neurips.cc/paper/2016/hash/5227b6aaf294f5f027273aebf16015f2-Abstract.html), [Hessel et al. AAAI 2019](https://ojs.aaai.org/index.php/AAAI/article/view/4266).

**Project fit:** Medium only if return RMS drifts by checkpoint/mode.

---

### 8.12 Data augmentation

#### Network Randomization / DrAC-style aug

**Mechanism.** Random conv noise or mild shifts at train time.

**Project fit:** Low — static pre-rendered backgrounds; risk to small item sprites.

---

### 8.13 Gradient surgery and multi-task conflict

#### PCGrad

**Mechanism.** Project conflicting task gradients to remove negative dot products.

**Sources:** [Yu et al. NeurIPS 2020](https://proceedings.neurips.cc/paper/2020/hash/3fe78a8acf5fda99de95303940a2420c-Abstract.html).

**Project fit:** Low until measured conflict; compare against PPG.

---

#### GradNorm / CAGrad

**Mechanism.** Balance gradient magnitudes across tasks.

**Sources:** [Chen et al. ICML 2018](https://proceedings.mlr.press/v80/chen18a.html).

**Project fit:** Diagnostic + possible aux weighting.

---

### 8.14 Action masking requirements

**Source:** [Huang & Ontañón FLAIRS 2022](https://doi.org/10.32473/flairs.v35i.130584).

For every technique:

- Store behavior-time mask in rollout.
- Compute behavior log-prob **after** masking.
- Reapply identical mask at training time.
- Use masked support for PPG clone KL and V-trace ratios.
- Sequence-align masks if recurrence added.
- Track valid-action count with entropy.

---

## 9. Bibliography

### Core PPO and advantage estimation

- Schulman et al., "Proximal Policy Optimization Algorithms," 2017. https://arxiv.org/abs/1707.06347
- Schulman et al., "High-Dimensional Continuous Control Using Generalized Advantage Estimation," ICLR 2016. https://arxiv.org/abs/1506.02438
- Andrychowicz et al., "What Matters for On-Policy Deep Actor-Critic Methods?" ICLR 2021. https://openreview.net/forum?id=nIAxjsniDzg
- Engstrom et al., "Implementation Matters in Deep RL," ICLR 2020. https://openreview.net/forum?id=r1etN1rtPB
- Wang et al., "Truly Proximal Policy Optimization," ICML 2020. https://proceedings.mlr.press/v115/wang20b.html

### Phasic / decoupled actor-critic

- Cobbe et al., "Phasic Policy Gradient," ICML 2021. https://proceedings.mlr.press/v139/cobbe21a.html
- Raileanu & Fergus, "Decoupling Value and Policy for Generalization," ICML 2021. https://proceedings.mlr.press/v139/raileanu21a.html

### Distributed and off-policy correction

- Espeholt et al., "IMPALA," ICML 2018. https://proceedings.mlr.press/v80/espeholt18a.html
- Petrenko et al., "Sample Factory," ICML 2020. https://proceedings.mlr.press/v119/petrenko20a.html
- Queeney et al., "GePPO," NeurIPS 2021. https://proceedings.neurips.cc/paper/2021/hash/63c4b1baf3b4460fa9936b1a20919bec-Abstract.html
- Wang et al., "ACER," ICLR 2017. https://arxiv.org/abs/1611.01224

### Curriculum and exploration

- Jiang et al., "Prioritized Level Replay," ICML 2021. https://proceedings.mlr.press/v139/jiang21b.html
- Portelas et al., "ALP-GMM," ICML 2020. https://proceedings.mlr.press/v100/portelas20a.html
- Florensa et al., "Reverse Curriculum Generation," ICML 2017. https://proceedings.mlr.press/v78/florensa17a.html
- Ecoffet et al., "Go-Explore," 2019. https://arxiv.org/abs/1812.03381

### Representation, auxiliary, and plasticity

- Jaderberg et al., "UNREAL," 2016. https://arxiv.org/abs/1611.05397
- Liu et al., "Adapting Auxiliary Losses Using Gradient Similarity," 2018. https://arxiv.org/abs/1812.02224
- Schwarzer et al., "SPR," ICLR 2021. https://openreview.net/forum?id=uCQfPZwRaUu
- Sokar et al., "ReDo," ICML 2023. https://proceedings.mlr.press/v202/sokar23a.html
- PFO, 2024. https://arxiv.org/abs/2405.00662

### Goal conditioning and hindsight

- Schaul et al., "UVFA," ICML 2015. https://proceedings.mlr.press/v37/schaul15.html
- Andrychowicz et al., "HER," NeurIPS 2017. https://arxiv.org/abs/1707.01495
- Rauber et al., "HPG," ICLR 2019. https://openreview.net/forum?id=Bkg2viA5FQ
- Perez et al., "FiLM," AAAI 2018. https://ojs.aaai.org/index.php/AAAI/article/view/11671

### Dropout and multimodal learning

- Galashov et al., "Consistent Dropout for Policy Gradient RL," 2022. https://arxiv.org/abs/2202.11818
- Neverova et al., "ModDrop," TPAMI 2015. https://doi.org/10.1109/TPAMI.2015.2461544
- Wu et al., "Greedy Lazy Training," ICML 2022. https://proceedings.mlr.press/v162/wu22d.html

### Imitation

- Oh et al., "Self-Imitation Learning," ICML 2018. https://proceedings.mlr.press/v80/oh18b.html
- Rajeswaran et al., "DAPG," RSS 2018. https://www.roboticsproceedings.org/rss14/p49.html

### Recurrence

- Ni et al., "Recurrent Model-Free RL Can Be a Strong Baseline," ICML 2022. https://proceedings.mlr.press/v162/ni22a.html
- Kapturowski et al., "R2D2," ICLR 2019. https://openreview.net/forum?id=r1lyTjAqYX

### Reward shaping and evaluation

- Ng et al., "Policy Invariance Under Reward Transformations," ICML 1999. https://mlanthology.org/icml/1999/ng1999icml-policy/
- Huang & Ontañón, "Invalid Action Masking," FLAIRS 2022. https://doi.org/10.32473/flairs.v35i.130584
- Agarwal et al., "Deep RL at the Edge of the Statistical Precipice," NeurIPS 2021 (RLiable). https://agarwl.github.io/rliable/

### Value normalization

- van Hasselt et al., "PopArt," NeurIPS 2016. https://proceedings.neurips.cc/paper/2016/hash/5227b6aaf294f5f027273aebf16015f2-Abstract.html

### Gradient surgery

- Yu et al., "PCGrad," NeurIPS 2020. https://proceedings.neurips.cc/paper/2020/hash/3fe78a8acf5fda99de95303940a2420c-Abstract.html
- Chen et al., "GradNorm," ICML 2018. https://proceedings.mlr.press/v80/chen18a.html

---

*Generated from fleet audit, architecture review, and literature survey (conversation 2026-08-04). Update this doc when experiments confirm or reject hypotheses.*
