## Status

**Code landed 2026-09-09 (mandate cutover):** F2048 + `[1024]×3` + FiLM on, PPO knobs,
`S_core` + local combat + 4-step hit backfeed, `scripts/scalpel_mandate_expansion.py`.
IMPALA / SimBa / ModDrop still deferred.

---

# Mandate plan: bigger NN + FiLM + combat credit + anti-forget

GPT 5.6 synthesis · 80-env C-RE1 fleet · use WH3 5090 VRAM · planner-loyal hop-score

**Highlights:** ~13.8M + FiLM · batch 8192 · clip 0.10 / target_kl 0.006 · `S_core` + local combat

## Mandate

Bigger network and FiLM are **required**. Batch/VRAM headroom with 80 envs supports ~14M now. Combat credit must become stepwise (γ≈0 damage/kill; 4-step pre-hit tax) without rebroadcasting the fight budget through every transition via terminal `S` alone.

| Metric | From → To |
|--------|-----------|
| Unique params | 5.0M → **13.8M** |
| Learner VRAM | ~4GB → **14–20GB** target (ceiling 24GB) |
| PPO minibatch | 2048 → **8192** |
| clip / target_kl | default-ish → **0.10 / 0.006** |

---

## 1. New NN shape

| Piece | Today | Mandated |
|-------|-------|----------|
| NatureCNN | 512 | 512 (keep; transplant) |
| Towers | typed planner-loyal | unchanged widths |
| Concat | 1472 | 1472 |
| Fusion | → 1024 | → **2048** |
| π trunk | `[512,512]` | **`[1024,1024,1024]`** |
| vf trunk | `[512,512]` | **`[1024,1024,1024]` + reinit** |
| FiLM | OFF | **ON:** goal→vision + goal→spatial (identity init) |
| Share FE | True | True (do **not** unshare) |
| `PARAM_TARGET` / `HARD_CAP` | 5M / 8M | **14M / 16M** |

Estimated unique params ≈ **13.76M** with FiLM.

- Transplant CNN / towers / actor prefix.
- Identity FiLM at start (γ=1, β=0).
- **Reinit critic** because reward targets change.
- **Defer** IMPALA-3 / SimBa residual trunks to a later fresh-ckpt campaign.

---

## 2. PPO knobs (anti-forget + bigger net)

### Train harder, update gentler

| Knob | Value |
|------|-------|
| `n_steps` | 1125 (keep transport horizon) |
| Min resolved transitions / update | ≥ **65,536** |
| `batch_size` | **8192** (try 12288 if VRAM peak &lt;14GB) |
| `n_epochs` | **2** (was 4) |
| `clip_range` | **0.10** |
| `target_kl` | **0.006** (early stop ~0.009) |
| `ent_coef` | 0.005 → anneal **0.003** |
| `max_grad_norm` | **0.30** |

### Discriminative LRs + rehearsal

| Group | LR |
|-------|-----|
| New / fusion / FiLM | **3e-5** |
| Typed towers | **~1.6e-5** |
| Mature CNN | **9e-6** (freeze first 2 updates) |
| Schedule | decay toward ~**1e-5** base |

**Per-update env mix (anti-forget):**

- 40 frontier / hard cells
- 24 recent trailing cells
- 16 mastered anchors (uniform)

> **Forgetting is not only clip/LR.** Tighter clip + lower LR + fewer epochs help, but a long pl chain also needs on-policy rehearsal of mastered cells every update. Knobs alone will not hold early hops.

---

## 3. Combat credit: `S_core` + `local_t`

**Problem today:** kill/damage quality is baked into terminal hop score `S`. Under γ=1 hop training that does not mark which fight steps earned the fighting budget.

**Fix:** strip combat from the broadcast core; attach causal local terms on the responsible steps; write returns directly.

| Signal | Rule | Magnitude (GPT start) |
|--------|------|------------------------|
| Damage dealt | γ≈0 on causal attack step (pending-fire FIFO) | **+0.014 / enemy HP** (Beretta rules kept) |
| Kill | γ≈0 once per room/slot; crow 0; boss ×4 | **+0.50** fodder / **+2.0** boss |
| Damage taken | Tax over up to **4** prior eligible steps | **−0.028 / HP** · weights **10% / 20% / 30% / 40%** |
| `S_core` | `S_old` minus old `B_kill` and gross `D_hp` fight terms | Resources / hop outcome without fight broadcast |
| Target | `return_t = S_core + combat_local_t + other_local_t` | No MC/GAE rebroadcast of fight budget |

**Details:**

- Zombie grabs: punish approach / failed-avoidance decisions, **not** frozen bite-animation frames.
- Exclude cutscene / menu steps from the 4-step backfeed; renormalize if fewer than 4 eligible steps.
- Keep anti-farm gates (no crow, no room-transition bogus HP, pending-fire causality).
- Do **not** also leave old `B_kill` / fight `D_hp` inside terminal `S` (double-count).

---

## 4. Other tech

### Ship with this migration

- FiLM
- 2048 fusion
- 3×1024 trunks
- Discriminative LR
- Direct episode returns
- Mastered-cell rehearsal
- Modality / FiLM diagnostics
- Existing combat aux @ 0.02

### Defer (later / fresh ckpt)

- IMPALA-3
- SimBa residual trunks
- ModDrop
- Unshared extractors
- GRU
- PPG / EWC
- New aux heads

These break clean transplant or add noise during an already large cutover.

---

## 5. Phased rollout

| Phase | What | Gate |
|-------|------|------|
| **0 Shadow** | Recompute combat ledger on ≥10k episodes; profile batch 8192/12288 | Zero duplicate kill/damage; VRAM profile OK |
| **1 Arch canary** | Transplant 13.8M + FiLM + new PPO knobs; combat shadow-only | Actor KL &lt;1e-6 at t0; retention holds 2–5M steps |
| **2 Combat canary** | Enable `S_core`+local returns; reinit critic; 20–40 envs | Attribution ≥95%; EV recovers; no farm |
| **3 Full 80** | Promote fleet-wide; keep rollback zip | Two consecutive eval passes |

---

## 6. Kill / rollback criteria

Roll back immediately if any of:

- Mastered sentinel **−10pp** twice, or **−15pp** once
- Aggregate mastered success **−5pp**
- Mean KL **&gt;0.012**, or clip fraction stuck **&gt;0.25**
- VRAM reserved **&gt;24GB** / OOM; update wall **&gt; experience +20%**
- Combat attribution **&lt;95%**, duplicate credit, or damage farm
- FiLM γ p99 outside **[0.5, 1.5]** persistently

---

## Notes

Source: GPT 5.6 mandate consult. Reward magnitudes are proposed starting points aligned with existing `enemy_damage` (+0.014/HP) scale; confirm against hop-score channel budgets before live cutover.

Canvas twin: `nn-mandate-expansion-plan.canvas.tsx`
