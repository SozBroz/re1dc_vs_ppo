# Planner-loyal one-cell hop-score redesign

> Author: GPT 5.6 (implementation plan), revised 2026-09-07 after imperator
> feedback. Status: **proposal — not live**.
> One cell = one episode; terminal hop score with effective γ=1 backup;
> HP/ammo/kills >> time; timeout harsher than divert; 6 min default /
> 12 min boss timeouts. Confirmed kills count in the hop score so a clean
> clear beats a lucky suicide-run past live enemies.

## 0. Executive summary

- Make every planner cell exactly one episode: one reset, one planner hop, one terminal outcome.
- Replace mid-episode `+8.0` checkpoint pulses and dense resource rewards with one terminal hop score.
- Broadcast that terminal score directly into every transition’s return target with effective `γ=1.0`; never emit it as reward on every step.
- Score successful hops from `+0.25` through `+4.0`, weighted toward HP,
  ammunition preservation, and **clearing live hostiles**.
- Make every abrupt failure worth `-4.0`; make timeout strictly worse at `-6.0`.
- Use `21600` emulated frames for ordinary cells and `43200` only for explicit `boss` cells.
- Do **not** pay dense per-step `enemy_damage` / `enemy_kill`. Confirmed kills
  settle only into the terminal hop score (`q_kill`), so a clean clear beats a
  lucky no-hit run-past while ammo chips still punish spray.
- Retain small same-step combat-misuse taxes and heavily reduced statue potential shaping.
- Reduce BC dominance while oversampling demonstrated `interact`/`use` decisions.
- Align minted-cell replacement with the same HP, ammo, healing, and time score used for training.

## 1. Goals and non-goals

### Goals

1. Give every action in a cell equal-horizon access to the final hop outcome.
2. Make completing the requested planner operation the only substantial positive event.
3. Prefer a clean, resource-preserving completion over a fast but wasteful completion.
4. Make HP, ammunition, and kill-clear quality dominate successful-hop mass
   (`0.30 + 0.25 + 0.25 = 0.80`); healing and time share the remaining `0.20`.
5. Prefer clearing the hostiles present at hop start over a lucky no-hit dodge;
   a one-in-a-million suicide run must not outscore an efficient clear.
6. Preserve healing supplies and discourage avoidable healing without rewarding low HP.
7. Retain time pressure as a weak successful-hop quality term (not a speedrun objective).
8. Make passive timeout unequivocally worse than attempting an interaction and diverting.
9. End the episode immediately on every success or policy-relevant failure.
10. Remove the current mismatch where death is only `-1.3333333333333333` while other cell failures are `-4.0`.
11. Produce bounded, stationary critic targets in `[-6.50, +4.05]` under ordinary same-step shaping.
12. Preserve planner guidance, learned navigation, learned interaction, and learned puzzle solving.
13. Preserve legal combat macros and box-room inventory RAM writes.

### Non-goals

- No navigation macro, auto-walk, door traversal macro, interaction macro, or puzzle solver.
- No automatic Gallery, piano, crest, statue, or item-use actuation.
- No multi-hop episode.
- No post-success continuation.
- No `+12` minute extension after a hop.
- No per-step positive for room entry, pickup, cutscene, enemy **damage chips**,
  reload, or planner progress (kills settle only via terminal `q_kill`).
- No attempt to optimize speedrun time ahead of survival resources and clears.
- No change to non-planner-loyal reward modes unless required by shared rollout plumbing.
- No kill farming across rooms that were empty at hop start (only hostiles
  present at stable reset count toward `E_start`).

## 2. Episode contract

At reset, the selected `plNN` cell restores its state and seeks the planner queue to exactly the next uncompleted planner step. That single step is the episode’s hop. The queue may retain future steps for observation lookahead, but no future step may be executed or rewarded in the same episode.

A successful predicate must terminate the episode even when the completed step is not the final step in the authored chunk. Capture is attempted before reset, but successful termination must not depend on capture I/O succeeding.

| Outcome | Detection | Hop score | Gym flag | Capture | Precedence |
|---|---|---:|---|---|---:|
| `hop_success` | Current planner step’s completion predicate becomes true and terminal state passes integrity checks | `S_success` | `terminated=True`, `truncated=False` | Eligible | 3 |
| `death` | Jill is dead or HP reaches the validated death state | `-4.0` | `terminated=True`, `truncated=False` | Forbidden | 1 |
| `gallery_wrong` | Confirmed incorrect Gallery input | `-4.0` | `terminated=True`, `truncated=False` | Forbidden | 2 |
| `armor_gas` | Non-death HP loss in room `205` from the gas state | `-4.0` | `terminated=True`, `truncated=False` | Forbidden | 2 |
| `armor_inplace_statue_push` | Existing seated-statue re-push detector breaches | `-4.0` | `terminated=True`, `truncated=False` | Forbidden | 2 |
| `main_hall_before_kenneth` | Illegal transition into `106` before current-leg Kenneth evidence | `-4.0` | `terminated=True`, `truncated=False` | Forbidden | 2 |
| `planner_divert` | Wrong room, unplanned pickup, unplanned box use, typewriter use, forbidden item, shotgun return, wrong planned object, or other validated route divergence | `-4.0` | `terminated=True`, `truncated=False` | Forbidden | 4 |
| `capture_invalid` | Hop predicate fired, but the resulting state is dead, uncontrolled, corrupt, polluted, or otherwise semantically ineligible | `-4.0` | `terminated=True`, `truncated=False` | Forbidden | 2 |
| `planner_timeout` | Cell emulated-frame budget expires without an earlier terminal event | `-6.0` | `terminated=True`, `truncated=False` | Forbidden | 5 |
| `infrastructure_abort` | Emulator loss, malformed observation, transport failure, or operator shutdown | No score | Discard sample | Forbidden | Outside policy contract |

Precedence is evaluated as follows:

1. Death owns a same-transition death.
2. Safety/puzzle-invalid terminals own their transition.
3. A valid hop completion at the exact budget edge succeeds.
4. An action-caused divert at the budget edge receives `-4.0`.
5. Timeout applies only if no semantic outcome occurred first.

Allowing a last-moment attempted interaction to divert for `-4.0` rather than time out for `-6.0` is intentional. It makes trying preferable to stalling. Infrastructure failures are never converted into policy failures.

Capture save errors are infrastructure telemetry, not `capture_invalid`: once a valid game state satisfied the hop, failure to write the state file must not punish the policy.

## 3. Timeout policy

### Frame budgets

- Default cell timeout: `6 * 60 * 60 = 21600` emulated frames at `60` fps.
- Boss cell timeout: `12 * 60 * 60 = 43200` emulated frames at `60` fps.
- Nominal decision-count backstop at `8` frames per policy step:
  - Default: `21600 / 8 = 2700` policy decisions.
  - Boss: `43200 / 8 = 5400` policy decisions.

The emulated-frame timer is authoritative. The decision limit exists only as a backstop if a runtime repeatedly reports zero or malformed frame deltas. Reaching either limit produces `planner_timeout` with `-6.0`, never a bootstrapped Gym truncation.

### What consumes the timer

`elapsed_frames` begins at `0` after the restored cell reaches stable control and then monotonically accumulates:

- normal action frames;
- door and room-transition frames;
- async text, examine, pickup, and cutscene skip frames;
- attack-macro execution frames;
- inventory/menu frames;
- box-operation frames.

No event resets or extends this timer. Statue progress, interactions, pickups, enemy hits, cutscenes, and planner predicate progress do not buy more time.

### Boss identification

A cell receives `43200` frames if and only if its current planner step has:

```json
{"op": "boss", "boss_id": "<canonical-id>", "room_id": "<room>"}
```

Allowed `boss_id` values are:

- `yawn_1` in room `210`;
- `black_tiger` in room `30C`;
- `plant_42` in room `40C`;
- `tyrant_t002` in room `513`;
- `super_tyrant` in room `303`.

The chunk validator must require `boss_id` and `room_id` for every `op == "boss"` step and reject unknown pairs. Room identity alone never grants a longer timeout. A traverse into a boss room, a boss cutscene, or post-boss pickup remains a `21600`-frame cell unless that exact hop is authored as `op: "boss"`.

Any future hard fight receives `43200` frames by being explicitly authored and validated as `op: "boss"`. There is no arbitrary per-cell timeout override.

## 4. Signal taxonomy

### 4.1 γ=1 hop-score channels

These are episode meters. They do not produce scalar reward when observed. They settle once into terminal `S`.

| Channel | Episode meter | Settlement |
|---|---|---|
| Planner success | Current step completion predicate | Chooses `S_success` |
| Planner failure | Terminal failure reason | Chooses fixed `S_fail` |
| HP preservation | Sum of all positive HP losses | `q_hp` |
| Ammo preservation | Weighted sum of every fired round | `q_ammo` |
| Heal preservation | Weighted count of consumed healing resources | `q_heal` |
| Kill clear | Confirmed kills vs hostiles present at hop start | `q_kill` |
| Time | Total emulated frames since stable reset | `q_time` |

Rules:

- HP loss is gross damage: healing later does not erase prior damage.
- Ammo spend is gross firing expenditure: pickups, reloads, and box withdrawals do not erase it.
- Heal usage is gross item consumption: restored HP does not erase it.
- Kill clear counts only hostiles that were alive at stable reset (`E_start`);
  kills of enemies that spawn mid-hop still increment `K` but cannot raise
  `q_kill` above `1.0` (no spawn-farm incentive). Gallery crows / pest slots
  with combat pay scale `0` are excluded from `E_start` and `K`.
- Time is monotonic and includes async-skip frames.
- On success, all five quality meters settle into one `S_success`.
- On failure, resource / kill / time meters remain telemetry only; the reason selects the fixed `S_fail`.
- Every transition in the completed episode receives the same terminal `S` in its outcome target with effective `γ=1.0`.

### 4.2 Localized γ≈0 channels

These channels affect only the action transition on which they occur. They are not accumulated backward and do not alter `S`.

Localized combat taxes:

- `attack_miss` for a knife whiff;
- `ammo_waste` for each confirmed missed gun round;
- `combat_overkill`;
- `shotgun_dog_hit`;
- `heavy_weapon_fodder_hit`;
- `attack_dry_fire`;
- `attack_macro_failure`.

Localized hard-puzzle potential shaping:

- `armor_statue_progress`;
- `armor_approach`;
- `dining_statue_progress`.

Let the raw same-step localized sum be `L_raw_t`. The value entering the learning target is:

```text
L_t = clip(L_raw_t, -0.50, +0.05)
```

This cap prevents one combat event or shaping detector from approaching the magnitude of a hop outcome.

### 4.3 Zeroed / removed channels

Under `RE1_PLANNER_HOP_SCORE_V1=1`, remove these from scalar reward:

- `step = 0.0`;
- `softlock = 0.0`;
- dense `hp = 0.0`;
- legacy dense `death = 0.0`;
- dense `ammo_spend = 0.0`;
- dense `heal_use_tax = 0.0`;
- dense `enemy_damage = 0.0` (hits are not a separate crumb; clears settle via `q_kill`);
- dense `enemy_kill = 0.0` (same — kill credit is only in terminal `S`);
- `weapon_reload = 0.0`;
- mid-episode `planner_step_success = 0.0`;
- legacy `planner_divert = 0.0` as a directly summed reward;
- legacy `planner_timeout = 0.0` as a directly summed reward;
- legacy `gallery_wrong = 0.0` as a directly summed reward;
- legacy `armor_gas = 0.0` as a directly summed reward;
- legacy `armor_inplace_statue_push = 0.0` as a directly summed reward;
- legacy `main_hall_before_kenneth = 0.0` as a directly summed reward.

Those keys may remain in `reward_breakdown` as telemetry aliases, but only `hop_score` and `local_reward` enter planner-loyal learning.

Dense per-hit / per-kill crumbs stay off on purpose: they recreate spray-and-pray
and mid-episode combat farming. Kill **clear quality** is restored as a γ=1
meter (`q_kill`) so finishing the hop after clearing the tip's hostiles beats a
lucky dodge, while `q_ammo` still taxes wasteful sprays.

## 5. The hop score S

### 5.1 Success

Define:

```text
S_success = 0.25 + 3.75 * (
  0.30*q_hp + 0.25*q_ammo + 0.25*q_kill + 0.10*q_heal + 0.10*q_time
)
```

All qualities are clipped to `[0.0, 1.0]`. Therefore:

```text
0.25 <= S_success <= 4.0
```

A success is always positive. HP + ammo + kill-clear jointly own `0.80` of
quality mass (`0.30 + 0.25 + 0.25`). Healing owns `0.10`; time owns `0.10`.

Rationale for `q_kill`: without it, a one-in-a-million no-hit dodge past three
zombies can score near the top of the band and is almost impossible for a real
clear to beat. With `0.25` kill weight, clearing those three hostiles is worth
up to `3.75 * 0.25 = 0.9375` score — enough that an efficient clear beats a
perfect dodge, while `q_ammo` still makes shotgun-spray clears lose to handgun
clears.

#### HP quality

Let gross episode damage be:

```text
D_hp = sum over t of max(HP_{t-1} - HP_t, 0)
```

Exclude initialization/decode values outside Jill's validated `1..96` HP band. Then:

```text
q_hp = clip(1.0 - D_hp / 95.0, 0.0, 1.0)
```

Consequences:

- `0` damage gives `q_hp = 1.0`.
- `47.5` gross damage gives `q_hp = 0.5`.
- `95` or more gross damage gives `q_hp = 0.0`.
- Taking damage and healing does not restore `q_hp`.

#### Ammo quality

Convert fired rounds to ammo-cost units:

| Weapon | Cost per fired round |
|---|---:|
| Knife | `0.00` |
| Flamethrower | `0.00` |
| Handgun/Beretta `0x02` | `0.04` |
| Shotgun `0x03` | `0.25` |
| Magnum/dumdum `0x04`, `0x05` | `0.40` |
| Grenade/bazooka `0x07`, `0x08`, `0x09` | `0.40` |
| Rocket launcher `0x0A` | `0.75` |

Let:

```text
A_spent = sum over fired rounds i of c_i
```

Use:

```text
B_ammo = 4.0 if current op is a validated boss, else 1.0
```

Then:

```text
q_ammo = clip(1.0 - A_spent / B_ammo, 0.0, 1.0)
```

A default cell's full budget is equivalent to `25` handgun rounds, `4` shotgun shells, or `2.5` heavy rounds. A boss cell's full budget is `100` handgun rounds, `16` shotgun shells, or `10` heavy rounds.

The initial budgets are deliberately permissive enough to explore combat while still distinguishing clean from wasteful play. After at least `200` successful episodes per boss, report the median and `75th` percentile `A_spent`. Change a boss budget only if its `75th` percentile among successful, top-half-HP episodes exceeds `4.0`; set the replacement budget to that measured `75th` percentile rounded up to the nearest `0.25`. Never calibrate from failed or low-HP episodes.

#### Kill-clear quality

At the first stable controlled frame after tip restore, count live hostiles in
the episode's combat-relevant set:

```text
E_start = number of living non-pest hostiles visible to the combat audit
          at hop start (exclude gallery crows / combat-scale-0 pests)
K       = confirmed kills of hostiles during this hop
          (same kill detector as today's enemy_kill channel)
```

Then:

```text
if E_start == 0:
    q_kill = 1.0          # empty room / already-cleared tip: vacuous full credit
else:
    q_kill = clip(K / E_start, 0.0, 1.0)
```

Notes:

- Boss hops normally have `E_start = 1` (the boss). Killing it yields `q_kill = 1.0`.
- Leaving all three tip zombies alive and sprinting out yields `q_kill = 0.0`.
- Killing 2 of 3 yields `q_kill = 0.666...`.
- Extra kills beyond `E_start` do not raise `q_kill` above `1.0` (no farm).
- Partial chip damage with no kill does **not** raise `q_kill` (we want kills,
  not endless tickling).
- Knife kills still count fully in `K` and cost `0.00` in `A_spent`.

#### Heal quality

Count consumed healing resources in full-heal-equivalent units:

| Consumed item | Heal units |
|---|---:|
| `blue_herb` | `0.17` |
| `green_herb` | `0.33` |
| `red_herb` | `0.66` |
| `mixed_herbs_gb` | `0.52` |
| `mixed_herbs_gg` | `0.68` |
| `mixed_herbs_ggb` | `0.87` |
| `first_aid_spray` | `1.00` |
| `first_aid_spray_alt` | `1.00` |
| `mixed_herbs_gr` | `1.01` |
| `mixed_herbs_ggg` | `1.03` |
| `mixed_herbs_grb` | `1.20` |

Let `H_used` be their sum. Then:

```text
q_heal = clip(1.0 - H_used / 1.0, 0.0, 1.0)
```

A green herb reduces the final score by at most `3.75 * 0.10 * 0.33 = 0.12375`. A full-heal-equivalent item reduces it by `0.375`. Gross HP damage remains separately charged, so healing cannot turn damage into a net positive.

#### Time quality

Let `F_elapsed` be elapsed emulated frames and `F_budget` be `21600` or `43200`:

```text
q_time = clip(1.0 - F_elapsed / F_budget, 0.0, 1.0)
```

Completing halfway through the budget gives `q_time = 0.5` and costs only `0.1875` score relative to an instantaneous completion. This is intentionally weaker than losing roughly `11` HP or spending roughly half a default ammo budget.

### 5.2 Failure ladder

| Failure reason | `S_fail` |
|---|---:|
| `death` | `-4.0` |
| `planner_divert` | `-4.0` |
| `capture_invalid` | `-4.0` |
| `gallery_wrong` | `-4.0` |
| `armor_gas` | `-4.0` |
| `armor_inplace_statue_push` | `-4.0` |
| `main_hall_before_kenneth` | `-4.0` |
| `forbidden_item` | `-4.0` |
| `shotgun_return` | `-4.0` |
| `unplanned_box` | `-4.0` |
| `unplanned_typewriter_save` | `-4.0` |
| `planner_timeout` | `-6.0` |

All abrupt policy failures share `-4.0`. Timeout is strictly worse at `-6.0` because passivity must not be a safer policy than pressing `interact`, `use`, or a door input. Earlier localized misuse taxes remain orthogonal action feedback, but the terminal outcome component is identical across abrupt failure reasons.

### 5.3 How S is written into the trajectory

The environment exposes two separate values:

- `local_reward = L_t`;
- `hop_score = S` only on the terminal transition, otherwise absent.

For observability, the sparse scalar reward stream is:

```text
r_t = L_t                 if t < T
r_t = L_T + S             if t = T
```

The learner must not run the standard discounted-return recurrence over that stream for planner-hop episodes. Once the complete episode is available, it directly constructs:

```text
Y_t = S + L_t
A_t = Y_t - V(s_t)
return_t = Y_t
```

for every `t` in the episode.

This is terminal-outcome advantage broadcasting, not `r_t = S` at every step. `S` appears once in the environment reward stream and once per transition only as a direct Monte Carlo target. There is no recurrence that can produce `T * S`.

The planner-hop branch therefore has:

- terminal outcome discount: effective `γ_outcome = 1.0`;
- terminal outcome trace: effective `λ_outcome = 1.0`;
- localized channel discount: effective `γ_local = 0.0`;
- existing model `gae_lambda = 1.0` retained for configuration compatibility;
- existing `RL_GAMMA` retained for non-planner modes;
- standard `compute_episode_mc_returns()` bypassed for scored planner episodes.

Complete episodes must remain intact across the current approximately `1125`-step rollout horizon. Workers buffer a planner episode until terminal, then attach `S` to every transition target. No prefix may be trained before its terminal outcome is known.

#### Worked trajectory: 200-step success

Assume `S = +3.50` and no localized events.

Sparse environment rewards:

- steps `0..198`: `0.00`;
- step `199`: `+3.50`.

Direct learning targets:

- steps `0..199`: `Y_t = +3.50`.

If step `73` had a `-0.20` misuse tax:

- step `73`: `Y_73 = +3.30`;
- every other step: `Y_t = +3.50`.

The return is never `200 * 3.50 = 700.00`.

#### Worked trajectory: 50-step divert

Sparse environment rewards:

- steps `0..48`: `0.00`;
- step `49`: `-4.00`.

Direct learning targets:

- steps `0..49`: `Y_t = -4.00`.

An action at step `20` with `L_20 = -0.10` receives `Y_20 = -4.10`; other transitions remain `-4.00`.

#### Worked trajectory: 2000-step timeout

Sparse environment rewards:

- steps `0..1998`: `0.00`;
- step `1999`: `-6.00`.

Direct learning targets:

- all `2000` transitions: `Y_t = -6.00`.

The first `1125` transitions receive the same `-6.00` outcome target as the final segment. There is no critic bootstrap across the segment boundary and no `RL_GAMMA` decay.

### 5.4 Wasteful versus clean completion (and dodge versus clear)

#### A. Same hop, 3 tip zombies, 4 minutes (`14400` frames)

**Lucky suicide-run (0 damage, 0 ammo, 0 kills):**

```text
q_hp=1.0  q_ammo=1.0  q_kill=0.0  q_heal=1.0  q_time=0.3333
Q = 0.30*1.0 + 0.25*1.0 + 0.25*0.0 + 0.10*1.0 + 0.10*0.3333 = 0.6833
S ≈ 0.25 + 3.75*0.6833 = 2.81
```

**Efficient clear (10 HP, 15 handgun, 3 kills, no heal):**

```text
q_hp=0.8947  q_ammo=0.40  q_kill=1.0  q_heal=1.0  q_time=0.3333
Q = 0.30*0.8947 + 0.25*0.40 + 0.25*1.0 + 0.10*1.0 + 0.10*0.3333 = 0.7518
S ≈ 0.25 + 3.75*0.7518 = 3.07
```

Clear beats perfect dodge by ~`0.26`. That is the intended meta.

**Wasteful clear (60 HP, 7 SG + 15 HG, 3 kills, one spray heal):**

```text
A_spent = 7*0.25 + 15*0.04 = 2.35 → q_ammo = 0.0
q_hp=0.3684  q_kill=1.0  q_heal=0.0  q_time=0.3333
Q = 0.30*0.3684 + 0.25*0.0 + 0.25*1.0 + 0.10*0.0 + 0.10*0.3333 = 0.3939
S ≈ 0.25 + 3.75*0.3939 = 1.73
```

Still clears, but scores well below an efficient clear — and below a perfect dodge.
Spray-clear is not the optimum; **efficient** clear is.

#### B. Empty-room hop (no hostiles at tip)

`E_start = 0` → `q_kill = 1.0` automatically. Combat weight does not punish
peaceful door/item hops.

#### C. Prior clean/wasteful numeric check (no enemies)

Consider the same non-boss empty hop completed in `7200` frames.

**Clean:** 5 HP, 4 handgun (`A_spent=0.16`), no heal:

```text
q_hp=0.9474  q_ammo=0.84  q_kill=1.0  q_heal=1.0  q_time=0.6667
Q = 0.30*0.9474 + 0.25*0.84 + 0.25*1.0 + 0.10*1.0 + 0.10*0.6667 = 0.8936
S ≈ 3.60
```

**Wasteful:** 60 HP, 10 SG (`A_spent=2.50`), one FAS:

```text
q_hp=0.3684  q_ammo=0.0  q_kill=1.0  q_heal=0.0  q_time=0.6667
Q = 0.30*0.3684 + 0.25*0.0 + 0.25*1.0 + 0.10*0.0 + 0.10*0.6667 = 0.4272
S ≈ 1.85
```

Clean still beats wasteful by ~`1.75` at identical time.

## 6. Localized combat taxes

### Exact combat keep list

| Signal | Magnitude |
|---|---:|
| Knife whiff | `-0.01` per confirmed whiff |
| Handgun missed round | `-0.04` per missed round |
| Shotgun missed round | `-0.10` per missed round |
| Magnum/grenade/bazooka missed round | `-0.15` per missed round |
| Rocket-launcher missed round | `-0.25` per missed round |
| Wasted potential (`combat_overkill`) | miss-tax × wasted fraction per damaging hit |
| Shotgun hit on Cerberus | `-0.20` per confirmed hit event |
| Heavy weapon hit on zombie or Cerberus | `-0.30` per confirmed hit event |
| Dry fire | `-0.02` per press |
| Attack macro failure | `-0.04` per rejected/failed macro |

For wasted potential (channel name `combat_overkill`):

```text
wasted_damage_fraction = clip(
  (nominal_max_damage - actual_hp_damage) / nominal_max_damage,
  0.0,
  1.0
)
penalty = wasted_damage_fraction * per_round_miss_tax
```

Applies on **any** damaging combat event (hit or kill), not only finishing blows.
Covers classic overkill (chip HP left) and under-performance (shotgun at long
range dealing far below nominal max). Damage ≥ nominal → zero waste.
Magnitude uses the same per-round miss tax as `ammo_waste` (ammo-pressure scaled).

A fired round is counted in `A_spent` whether it hits or misses. A confirmed miss additionally receives its same-step tax because the ammo meter teaches conservation only after terminal, while the localized tax identifies the responsible fire action.

Bosses are exempt only from `heavy_weapon_fodder_hit`; miss, dry-fire, macro-failure, overkill, and ammo-meter accounting remain active.

### Retained hard-puzzle shaping

Armor room `205` remains the sole special hard-puzzle shaping exception, with Dining `202` retaining its equivalent statue signal:

- `armor_statue_progress = 0.05 * legacy_armor_statue_progress`;
  - legacy per-step clip `±0.50` becomes `±0.025`;
  - a full successful shove telescopes to approximately `+0.50`.
- `armor_approach = 0.20 * legacy_armor_approach`;
  - legacy per-step clip `±0.10` becomes `±0.020`;
  - full approach potential is capped at approximately `+0.10`.
- `dining_statue_progress = 0.05 * legacy_dining_statue_progress`;
  - per-step clip becomes `±0.025`;
  - a full successful shove telescopes to approximately `+0.50`.

Potential rebaselining on target changes remains zero-pay. Backtracking remains negative. These signals affect only the current action target through `L_t`; they do not extend time, reset time, or enter `S`.

## 7. Interact-avoidance cure

The present policy can avoid a likely `-4.0` divert by rubbing walls until an equally valued timeout. That makes hesitation locally rational, especially under a sticky BC prior.

The redesign changes that comparison:

- active but wrong attempt: `S = -4.0`;
- passive timeout: `S = -6.0`;
- valid interaction sequence: `S ∈ [+0.25, +4.0]`.

Because `S` is broadcast with effective `γ=1.0`, an early turn toward an interactable, the final alignment movement, and the eventual `interact` press all receive the same episode outcome. There is no `25`-second credit horizon separating alignment from the button press.

Required telemetry:

- legal `interact` presses per episode;
- legal `use` presses per episode;
- first `interact` frame;
- distance and bearing to target at each `interact`;
- seconds spent within the target’s near-interaction radius;
- wall-contact movement frames while inside that radius;
- episodes with no `interact` or `use`;
- outcome conditioned on whether an interaction was attempted.

### BC changes

Keep BC, but reduce its control over the mature policy:

- `RE1_BC_COEF = 0.10`;
- `RE1_BC_COEF_DECAY = 1.00`;
- `RE1_BC_COEF_MIN = 0.10`;
- `RE1_BC_BATCH = 128`;
- `RE1_BC_INCLUDE_FAILS = 0`.

Change demo sampling so each `128`-sample BC minibatch contains:

- `64` samples uniformly drawn from successful demos;
- `64` samples drawn from successful-demo transitions whose recorded action is `interact` or `use`.

If fewer than `64` distinct interaction samples exist, sample that pool with replacement. Keep recorded legal-action masks. Do not synthesize interaction labels from coordinates.

The expected BC accuracy may fall below the current approximately `98%`; that is acceptable. The release metric is improved valid interaction behavior, not preservation of near-perfect imitation accuracy.

## 8. Critic and PPO implications

### Return construction

Add a planner-hop return path that consumes complete scored episodes and writes `returns` and `advantages` directly. It must not call the existing discounted `compute_episode_mc_returns()`.

Use:

```text
return_t = S + L_t
advantage_t = return_t - V(s_t)
```

Retain minibatch advantage normalization. Do not normalize or rescale the bounded returns themselves.

### Episode buffering

A `21600`-frame episode can last approximately `2700` decisions and therefore exceed the current approximately `1125`-step segment. A `43200`-frame boss episode can last approximately `5400` decisions.

For planner-hop mode:

1. Buffer transitions by environment until terminal.
2. Pin the worker’s inference-policy snapshot at episode reset.
3. Defer activation of newly downloaded weights until that environment terminates.
4. Emit one complete `ScoredEpisode` packet with `T` transitions.
5. Never train an unresolved prefix.
6. Discard an incomplete episode after infrastructure abort.
7. Batch complete episodes from the same policy version until at least `2048` samples or `120.0` wall-clock seconds have elapsed.
8. Flatten variable-length episodes without padding or fake transitions.
9. Permit one episode longer than `2048` to form its own PPO batch.

This preserves on-policy ownership and avoids assigning one policy-version label to actions sampled by multiple versions.

### Critic migration

The existing critic has learned targets containing `+8.0` pulses, dense combat positives, and discounted prefixes; its negative explained variance is evidence that it should not be carried unchanged.

At migration:

- preserve the visual, spatial, goal, planner, and actor parameters;
- reinitialize the value MLP branch and final value head;
- reset optimizer state;
- keep actor logits and action masks unchanged;
- keep `vf_coef = 0.50`;
- keep `n_epochs = 4`;
- keep learner `batch_size = 2048`;
- keep planner-hop `gae_lambda = 1.00` in configuration, although direct targets bypass the GAE recurrence;
- keep the existing PPO clip range and entropy coefficient for the first rollout;
- keep auxiliary world/combat heads at `AUX_COEF = 0.02`;
- do not add a second value head in version 1.

The single critic predicts expected `S + L_t`. Since `L_t` is small and same-step, the dominant target is the terminal hop outcome.

### Diagnostics

Log:

- `train/hop_score_mean`;
- `train/hop_score_success_mean`;
- `train/hop_score_fail_mean`;
- `train/hop_return_min`;
- `train/hop_return_max`;
- `train/hop_episode_steps_mean`;
- `train/hop_episode_steps_p95`;
- `train/hop_buffered_unresolved`;
- `train/hop_discarded_infra`;
- `train/hop_value_ev`;
- `train/hop_local_abs_mean`;
- `train/hop_policy_version_lag`.

Assert finite targets and enforce:

- `-6.50 <= return_t <= +4.05` for episodes without recognized statue shaping anomalies;
- exactly one terminal hop score per emitted episode;
- no emitted episode without a terminal reason;
- no transition belonging to two episodes.

## 9. Capture and mint quality alignment

Only `hop_success` may mint a planner cell. Capture occurs after score settlement and before reset.

Store in the sidecar and `meta.json`:

- `hop_score`;
- `q_hp`;
- `q_ammo`;
- `q_heal`;
- `q_time`;
- `gross_hp_loss`;
- `ammo_cost_spent`;
- `heal_units_used`;
- `elapsed_frames`;
- `timeout_frames`;
- `boss_id`;
- terminal absolute HP;
- terminal damage-weighted ammo;
- terminal healing-resource centi-units;
- local tax totals by key.

For competing captures of the same cell:

1. Require normal integrity and predecessor checks.
2. Prefer the capture whose `hop_score` is at least `0.02` higher.
3. If scores differ by less than `0.02`, compare:
   1. higher `q_kill` (more of the tip hostiles cleared);
   2. higher terminal HP;
   3. higher total damage-weighted ammo across inventory and box;
   4. higher healing-resource centi-units;
   5. lower elapsed frames.
4. Kill count enters only through `hop_score` / `q_kill` — do not add a separate
   raw kill lexicographic that ignores ammo.
5. Do not let a faster state replace a materially healthier, better-armed, or
   better-cleared state unless the weighted `hop_score` is actually higher.

This replaces the current strictly lexicographic behavior where a one-HP difference can dominate every ammunition and healing difference. Existing quality tuples remain readable for compatibility, but new planner-hop captures use the scored metadata as the primary replacement key.

A valid success remains a success if local state-file writing fails. Log `capture_io_error`, retain the positive training episode, and leave the existing cell untouched.

## 10. Implementation plan

### Feature flag

Use:

```text
RE1_PLANNER_HOP_SCORE_V1
```

Accepted values:

- unset or `0`: unchanged current planner-loyal behavior;
- `shadow`: compute and log the new score without changing learning rewards or episode continuation;
- `1`: enforce the new episode, score, timeout, rollout, and return contract.

Require `RE1_PLANNER_LOYAL=1`. Fail startup if learner and worker protocol capabilities disagree.

### Phase 1: constants and score model

Modify `re1_rl/planner_loyal.py`:

- add all score weights, budgets, failure values, ammo costs, heal costs, and local-tax constants;
- add canonical boss IDs and room validation;
- replace the planner scalar-key contract with `hop_score` and `local_reward`;
- preserve old constants only for flag-off compatibility.

Add `re1_rl/planner_hop_score.py`:

- `PlannerHopMeters`;
- gross HP accounting;
- gross ammo accounting;
- heal-use accounting;
- time accounting;
- quality formulas;
- terminal failure mapping;
- `S_success` settlement;
- localized-tax calculation;
- score telemetry serialization.

Modify `re1_rl/progress.py`:

- own one `PlannerHopMeters` instance per episode;
- reset it exactly once after stable cell load;
- expose terminal settlement as an idempotent operation;
- prohibit timer extensions after reset.

### Phase 2: one-cell episode lifecycle

Modify `re1_rl/reward.py`:

- under flag `1`, stop summing legacy planner-loyal channels;
- update meters every transition;
- produce localized `L_t`;
- settle `S` only at terminal;
- zero dense enemy damage / enemy kill crumbs (kills still meter into `q_kill`);
- zero reload, dense HP, step, softlock, ammo-spend, and heal positives;
- retain legacy behavior when the flag is off;
- compute shadow telemetry when the flag is `shadow`.

Modify `re1_rl/env.py`:

- terminate on any current planner-step success, not only chunk completion;
- remove mid-chunk continuation, timer rearming, softlock extension, and `max_steps_bonus`;
- evaluate the precedence table;
- turn both frame and decision caps into terminated `planner_timeout`;
- attach `hop_score`, `local_reward`, meters, and terminal reason to `info`;
- attempt capture without making success depend on capture I/O.

Modify `re1_rl/yawn_cell_timeout.py` or add planner-specific timeout helpers:

- define `PLANNER_DEFAULT_TIMEOUT_FRAMES = 21600`;
- define `PLANNER_BOSS_TIMEOUT_FRAMES = 43200`;
- define nominal action caps `2700` and `5400`;
- select only from validated current planner op;
- leave non-planner timeout behavior unchanged.

Modify chunk validation in `re1_rl/planner_loyal.py`:

- require canonical `boss_id` and matching room on `boss` steps;
- reject unknown boss metadata;
- prohibit arbitrary timeout overrides.

### Phase 3: rollout protocol and exact return targets

Modify:

- `re1_rl/distributed/rollout_types.py`;
- `re1_rl/distributed/rollout_codec.py`;
- `re1_rl/distributed/rollout_collect.py`;
- `re1_rl/distributed/worker_runtime.py`;
- `re1_rl/distributed/async_worker_runtime.py`;
- `re1_rl/distributed/learner_train.py`;
- `re1_rl/distributed/packed_train.py`;
- planner portions of `re1_rl/async_fleet.py`.

Add a codec-versioned `ScoredEpisode` representation containing:

- observations;
- actions;
- old values;
- old log probabilities;
- legal-action masks;
- local rewards;
- terminal hop score;
- terminal reason;
- policy version;
- auxiliary targets;
- modality-drop masks.

Protocol behavior:

- hold planner episodes until terminal;
- pin policy version for the episode;
- activate pulled weights at next reset;
- encode variable episode length;
- flatten only complete same-version episodes;
- write `return_t = S + L_t`;
- write `advantage_t = return_t - old_value_t`;
- bypass discounted MC/GAE;
- retain existing rollout behavior for every flag-off curriculum;
- increment codec version and fail closed on old workers.

### Phase 4: PPO and BC migration

Modify `re1_rl/combat_ppo.py`:

- accept directly populated planner-hop returns;
- preserve ordinary training for legacy buffers;
- add bounded-target and finite-value assertions;
- add hop-score diagnostics.

Modify `re1_rl/demo_bc.py`:

- add the `64` uniform plus `64` interaction/use sampler;
- preserve successful-only filtering and action masks;
- log interaction pool size and sampling fraction.

Modify fleet learner launch configuration:

- set `RE1_BC_COEF=0.10`;
- set `RE1_BC_COEF_DECAY=1.00`;
- set `RE1_BC_COEF_MIN=0.10`;
- set `RE1_BC_BATCH=128`;
- set `RE1_BC_INCLUDE_FAILS=0`;
- set `RE1_PLANNER_HOP_SCORE_V1=1` only after protocol deployment.

Create a migration utility that loads the current checkpoint, retains actor/shared extractor weights, reinitializes the critic branch, clears optimizer state, and writes a distinct hop-score checkpoint.

### Phase 5: capture alignment

Modify `re1_rl/planner_loyal_cells.py`:

- attach meter and score metadata including `q_kill`, `E_start`, `K`;
- compare hop score before the absolute resource tie-break;
- use the `0.02` replacement margin;
- remove raw kill-count lexicographic (use `q_kill` via `hop_score` instead);
- preserve cross-runtime state files;
- treat save failures as capture telemetry rather than policy failure.

Update capture proposal serialization in the planner-loyal sync path with the same fields.

### Phase 6: tests

Extend `tests/test_planner_loyal.py` with:

- every success op terminates immediately;
- no next planner step executes in the same episode;
- no `+12` minute extension;
- every abrupt failure maps to `-4.0`;
- timeout maps to `-6.0`;
- death is exactly `-4.0`;
- Kenneth gate is exactly `-4.0`;
- success wins at the exact time edge;
- divert wins over timeout when the same action causes it;
- capture I/O failure does not change success.

Add `tests/test_planner_hop_score.py`:

- formula boundaries;
- all `q_*` stay in `[0.0, 1.0]`;
- success stays in `[0.25, 4.0]`;
- gross damage is not erased by healing;
- pickups do not erase ammo spend;
- each healing item has the exact weight above;
- default versus boss ammo budgets;
- default versus boss time budgets;
- clean/wasteful worked examples.

Extend timeout tests:

- `21600` default frames;
- `43200` validated boss frames;
- `2700` and `5400` action backstops;
- cutscene and async-skip billing;
- no reset on progress;
- no room-based boss inference.

Add distributed tests:

- a `2000`-step timeout assigns `-6.0` to all `2000` targets;
- a `2700`-step success crosses multiple old horizons without bootstrap;
- localized tax changes only its own target;
- terminal score is not recursively summed;
- variable-length packet codec round-trip;
- incomplete infrastructure-aborted episodes are discarded;
- policy weights activate only after episode reset;
- mixed protocol versions fail closed.

Add capture tests:

- score replacement margin;
- resource / `q_kill` tie-break order;
- raw kill-count lexicographic removed;
- capture write failure preserves successful training outcome.

## 11. Rollout and evaluation metrics

### Deployment order

1. Run all unit and distributed protocol tests.
2. Run `shadow` scoring for at least `2000` completed episodes.
3. Validate score bounds, meters, terminal classification, and boss timeout selection.
4. Start a separate canary learner with one worker and a critic-reset checkpoint.
5. Run the canary for `4.0` hours without mixing old and new reward packets.
6. If canary gates pass, restart the complete fleet on one commit and one codec version.
7. Evaluate the first `24.0` hours and again at `48.0` hours.

### Required 24–48 hour improvements

Against the current approximately `59%` divert, `30%` timeout, and `1%` episode-ending completion baseline:

- timeout rate at or below `15%` by `24.0` hours;
- timeout rate at or below `10%` by `48.0` hours;
- valid hop-success rate at or above `15%` by `24.0` hours;
- valid hop-success rate at or above `20%` by `48.0` hours;
- no-interaction timeout rate reduced by at least `50%`;
- median frame-to-first-`interact` near an interaction target reduced by at least `40%`;
- legal `interact` or `use` attempts per eligible episode increased by at least `50%`;
- non-boss successful-hop median gross HP loss at or below `10`;
- non-boss successful-hop median ammo cost at or below `0.25`;
- at least `75%` of successful non-boss episodes consume `0.00` heal units;
- successful-hop median score at or above `3.00`;
- at least `50%` of sampled cells in the late reset quartile produce one success during the `48.0`-hour window;
- hop critic explained variance above `0.00` by `24.0` hours and above `0.10` by `48.0` hours;
- localized absolute reward mean below `0.10` per affected transition;
- infrastructure-discard rate below `1%`;
- scored-episode buffering throughput at least `70%` of pre-change policy steps per wall-clock hour.

Also report distributions by `plNN`, planner op, terminal reason, worker, and boss ID. Fleet-wide averages alone may hide continued late-cell starvation.

## 12. Falsifiers and rollback triggers

Stop the rollout and return to the feature-flag-off checkpoint if any of these occurs:

1. Any return target contains NaN/Inf, exceeds `+4.05` without logged statue shaping, or falls below `-6.50`.
2. Timeout rate fails to improve by at least `30%` relative after `5000` scored episodes.
3. Valid hop-success rate falls below the flag-off baseline after `5000` scored episodes.
4. Near-target `interact`/`use` attempt rate fails to improve by at least `25%` after `2000` eligible episodes.
5. Median successful-hop HP loss or ammo cost worsens by more than `20%`.
6. Critic explained variance remains below `-0.20` for `10` consecutive training epochs.
7. More than `1%` of packets are incomplete, multiply terminated, cross-policy-version, or codec-invalid.
8. Wall-clock policy-step throughput remains below `70%` for `2.0` consecutive hours.
9. Any episode continues after a valid hop success.
10. Any non-boss operation receives `43200` frames without `op: "boss"`.

Rollback means disabling `RE1_PLANNER_HOP_SCORE_V1`, restoring the pre-migration checkpoint, and rejecting incompatible scored packets. Do not transplant the newly trained critic into the legacy checkpoint.

## 13. Open risks

1. **Complete-episode buffering increases memory and delays learner updates.**  
   Mitigation: variable-length compressed packets, `5400`-decision hard cap, same-version batching, `120.0`-second learner flush, and explicit throughput rollback gate.

2. **A `-6.0` timeout may initially increase intentional late diverts.**  
   Mitigation: this is preferable to passive wall-rubbing; measure early versus last-quartile diverts and address detector/observation faults rather than weakening timeout.

3. **Terminal-only completion may still be sparse in the armor and Dining statue cells.**  
   Mitigation: retain the reduced, telescoping `armor_statue_progress`, `armor_approach`, and `dining_statue_progress` signals with strict same-step caps.

4. **The fixed `1.0`/`4.0` ammo budgets may misprice a validated boss.**  
   Mitigation: calibrate only after `200` successful, top-half-HP episodes using the specified successful `75th`-percentile procedure; never loosen budgets based on failures.

5. **Reduced BC weight can temporarily destabilize familiar navigation.**  
   Mitigation: preserve actor and shared extractor weights, retain successful-only BC, oversample demonstrated interaction decisions, deploy through a separate canary learner, and roll back on completion-rate regression.
