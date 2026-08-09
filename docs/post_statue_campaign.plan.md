# Post-statue campaign plan — three finish-line problems

## Context

After **Dining 2F statue** (`statue_202` / `cp54`), the Yawn rails chain is mostly navigation + a few puzzles. The remaining **hard** problems are strategic, not single-room skills:

| # | Problem | Route touchpoints | Status |
|---|---------|-------------------|--------|
| 1 | Long-term ammo management | Whole chain → `cp93` `yawn_moon_210` | Open |
| 2 | Inventory / item-box logistics | `cp57` `save_100`, later `cp84`–`cp85` box prep @ `118` | Open |
| 3 | Yawn fight | `cp92`–`cp93` room `210` | Open |

**North star:** Agent reaches Yawn with **shotgun shells + grenade launcher acid rounds** in reserve, handgun/knife as default, and a workable inventory layout after box trips.

---

## 1. Long-term ammo management

**Problem:** Episode length and weapon variety make “spray and pray” ruinous. GL / shotgun / magnum spend taxes are high; reserves are finite across many fights.

**Hypothesis (achievable without full-game ammo oracle):**

- **Short-term efficiency** already shaping via `ammo_spend` + deferred `ammo_waste` (hitscan + GL flight).
- **Policy bias** toward **knife + handgun** for trash mobs and probing.
- **Heavy weapons** reserved for high-value targets (crowds, Yawn, dogs with tight TTK).

**Open design questions:**

- [ ] Is current miss/spend tax enough, or do we need a **low-reserve multiplier** (already partial via `ammo_before` on waste)?
- [ ] Should rails `goal` expose **fireable ammo counts** more prominently at heavy-weapon checkpoints?
- [ ] Curriculum: pin resets with realistic ammo budgets before Yawn legs?

**Success criteria:** Agent completes midgame legs without draining GL/acid before `cp85`; arrives at `210` with shells + acid as above.

---

## 2. Inventory management

**Problem:** Jill has **6 item slots**. Late pre-Yawn loadout is tight:

- **4 weapons** (knife, handgun, shotgun, grenade launcher typical)
- **2 keys** (shield, armor, etc.)
- **2 emblems** (plant / tiger room — may need to be carried across legs)

No slots left for **ammo stacks** unless something is deposited.

**Working assumptions:**

- **Store grenade launcher** (and possibly other heavies) in item box when not needed.
- **Room `100` save** (`cp57` `save_100`) is a logistics anchor — may need **return trips** to deposit emblems from plant/tiger legs before continuing.
- First **mandatory box prep** leg is **`cp85` `yawn_box_prep_118`** (room `118`); `cp84` is box entry.

**North-star alignment:** Box-room **RAM deposit/withdraw is allowed** (`docs/north_star.md`). Nav macros for box menus are not — policy must learn box UI or we add sanctioned box actions scoped to box rooms.

**Open design questions:**

- [ ] Capture **`cp57` / `cp85`** cells with representative inventory pressure (weapons + keys + emblems + minimal ammo).
- [ ] Obs: does `box` + `inventory` + affordances give enough signal for “what to stash”?
- [ ] Reward: any shaping for successful deposit before Yawn prep, or rely on `checkpoint_success` only?
- [ ] Route: explicit **emblem round-trip** via `100` vs stash at `118` only — TBD after human play.

**Success criteria:** Agent passes `cp85` with legal inventory (timer wait + correct loadout) without illegal key drops or softlocks.

---

## 3. Yawn fight (`cp93` `yawn_moon_210`)

**Problem:** Highest-HP enemy so far; high damage per contact. First encounter that truly tests combat macro + resource prep.

**Hypothesis:** Fight is **manageable** if the agent arrives with:

- **Shotgun shells** (burst / stagger)
- **GL acid rounds** (chunk damage at range)
- Handgun/knife for cleanup only

**Existing infra:**

- `re1_rl/yawn_outcome.py` — outcome contract for room `210`
- `cp92` enter arena → `cp93` fight + moon crest

**Open design questions:**

- [ ] Minimum viable ammo at `cp92` spawn for curriculum pins?
- [ ] Combat obs / reward: any Yawn-specific shaping, or generic damage/kill + spend/waste only?
- [ ] Retreat vs kill: both valid per outcome enum — which does the route require for `checkpoint_success`?

**Success criteria:** `yawn_moon_210` checkpoint clears reliably from pinned `cp92` with prepared inventory.

---

## Payforward fight ammo audit (HG-eq manifest)

**Context:** Payforward fight CPs are auto-discovered when `ammo(cpN) > ammo(cpN+1)` on curated cells (`re1_rl/yawn_rails_payforward.py`). All numbers below use **manifest HG-equivalent ammo** (`quality[1]`) at the fight cell vs its successor.

**Beretta accounting convention (imperator):**

- Report spends in **beretta bullet equivalents**.
- **1 zombie = 7 beretta** (handgun-equivalent); **50% waste** → max **11** per zombie.
- **2-zombie room** ideal **14**, max **21**.
- Pickup legs: ideal **net Δ** = ammo gained − spend (e.g. cp19 clip **+15**).

**Current payforward fight CPs:** `18, 26, 37, 40, 44, 45, 53` (cp36/39/43 nav cliffs ignored).

### Leg-by-leg: observed vs ideal

| Fight CP | Leg (cpN → cpN+1) | What should happen | Ideal spend (beretta) | Ideal net Δ | Observed net Δ (manifest) | Δ vs ideal (beretta) | Verdict |
|----------|-------------------|--------------------|------------------------|-------------|---------------------------|----------------------|---------|
| **cp18** | `l_passage_enter_108` → `ammo_108` | Clear L Passage; **cp19 picks up +15 clip** | **~10** | **+5** | **−4** | **+9 overspend** | Fixable — should be a small net gain, not a loss |
| **cp26** | `back_passage_10A` → `crow_gallery_enter_117` | Kill **2** hallway zombies | **14** (2×7) | **−14** | **−6** | Bogus — **killed neither zombie**; cliff is noise | **Recapture / ignore**; do not trust ripple from here |
| **cp36** | `back_passage_return_10A` → `courtyard_enter_11A` | (nav after crest) — only a fight if cp26 stretch was real | — | — | **−6** | — | **Should not be a fight anchor** (cp26 bogus) |
| **cp37** | `courtyard_enter_11A` → `crest_gate_11A` | Handgun cleanup entering courtyard | **~5** | **−5** | **−31** | **~+26 overspend** | Major handgun spray |
| **cp39** | `back_passage_post_crest_10A` → `east_stairs_101` | Return through 10A after gate | modest | modest | **−14** | — | **Should not be a fight anchor** (cp26 bogus) |
| **cp40** | `east_stairs_101` → `storeroom_enter_118` | **1 zombie** en route to storeroom | **7** | **−7** | — | — | New fight anchor |
| **cp44** | `east_stairs_201` → `c_passage_204` | **2 zombies** on stairwell threat | **14** (2×7) | **−14** | **−31** | **~+17 overspend** | Using **31 beretta**, not 2×7 |
| **cp45** | `c_passage_204` → `upper_hall_enter_203` | **2 zombies** — starved but doable | **14** (2×7) | **−14** | **−22** | **~+8 overspend** | Burning **22 beretta** instead of 14 |
| **cp53** | `dining_2f_enter_202` → `statue_202` | Dining 2F clear + statue leg; **2 zombies** max | **14** (2×7) | **−14** | **−180** | **~+166 overspend** | Dumping **full grenade launcher** magazine |

### Implications for payforward ripple

1. **cp26 is poisoned** — successor quality implies a fight that did not happen (no zombie kills). Ripple/grind from cp26, and downstream cliffs at **cp36** and **cp39**, are **not trustworthy** until cp26–cp27 is recaptured with ~2 SG spent and both kills.
2. **cp18** — small fix; agent should net **+5** after cp19 clip, not **−4**.
3. **cp37, cp44, cp45** — handgun/GL spray where **5 beretta** or **2×7 zombie** spend should suffice; largest curriculum signal gaps before inventory/Yawn.
4. **cp53** — new fight edge is correct (big cliff into statue), but captures must not **empty GL**; treat as **2×7 zombie** room, not a grenade spam leg.

### Reset distribution (fight progression)

When ``RE1_YAWN_PAYFORWARD_RIPPLE=1`` (fleet default in ``go_explore_phase_c.env.cmd``):

- **40%** — load the **frontier fight cell** (first fight in progression whose successor
  has not met min-tolerated net ammo shift).
- **60%** — uniform over **all loadable cells from cp00** (no latest-cell bias).
- Frontier order: **cp18 → cp26 → cp37 → cp40 → cp44 → cp45 → cp53**.

### Open actions

- [ ] Recapture **cp26** (and audit **cp27**) with 2 SG + 2 kills; set `RE1_YAWN_PAYFORWARD_IGNORE_FIGHTS=36,39` until cp26 stretch is clean (or force-only cp26).
- [ ] Recapture **cp18/cp19**, **cp37/cp38**, **cp44/cp45**, **cp53/cp54** toward ideal spend bands above.
- [ ] Consider manifest **quality floor** checks before admitting a fight cliff (successor must reflect plausible combat, not just ammo drop from misses).

---

## Suggested order of attack

1. **Statue leg stable** — `cp54` captured; dense push reward gated (room `202`, `statue_202` only).
2. **Ammo** — keep tuning spend/waste visibility; watch memlog on GL spam legs.
3. **Inventory** — human-needed pass on emblem/plant/tiger → box → Yawn prep; capture `cp57`/`cp85` cells.
4. **Yawn** — pin `cp92`, fight harness, then fleet mix through `cp93`.

---

## References

| Doc | Use |
|-----|-----|
| [`yawn_checkpoint_cells.md`](yawn_checkpoint_cells.md) | Per-cell objectives (`cp57`, `cp85`, `cp93`) |
| [`north_star.md`](north_star.md) | Box RAM exception, no puzzle macros |
| [`exploration_rewards.md`](exploration_rewards.md) | Ammo spend / waste tax policy |
| [`data/yawn_checkpoint_route.json`](../data/yawn_checkpoint_route.json) | Canonical route |
| [`re1_rl/yawn_rails_payforward.py`](../re1_rl/yawn_rails_payforward.py) | Fight-cliff discovery + ripple |
