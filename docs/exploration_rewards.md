# RE exploration rewards

> **Canonical policy source:** adapted from `D:\awbw\.cursor\skills\re-exploration-rewards\SKILL.md` (2026-07-20). Rewrite for clarity only. Any change to *what* pays, magnitudes, exceptions, or status (implemented / not) requires **explicit imperator validation** before it goes into code or this doc.

Policy source: imperator.

## Yawn rails override (approved 2026-08-03, credit update 2026-08-04)

`curriculum/yawn_rails_one_leg.json` uses a different, goal-conditioned reward
contract:

- completing the active atomic checkpoint pays `checkpoint_success = +12.0` and
  terminates the one-leg episode successfully;
- navigation milestones (new room, key get/use, weapon, ammo, cutscene, document,
  gallery puzzle steps) keep **full** exploration magnitudes (+4 / +2 ammo / +0.5
  per gallery switch);
- minor positives (junk pickup, PBRS door/graph shaping, typewriter) stay at `×0.05`;
- confirmed enemy damage/kill stay unscaled so unscaled miss taxes do not make
  correct combat systematically unattractive;
- negative terms retain their magnitudes and anti-farm behavior;
- PPO `RL_GAMMA` targets a **~25s** emulated pure-discount half-life (20–30s
  band). Longer navigation credit comes from stacked nav crumbs + dominant
  checkpoint, not an extreme γ;
- distributed `n_steps` ≈ 6 half-lives (~1125 steps ≈ 150s emulated);
- negative terms retain their magnitudes and anti-farm behavior;
- a qualified cutscene can pay only on the same transition as a newly rewarded
  room entry. Same-room interact/message/cutscene spam never pays;
- `observed_cutscenes` and `rewarded_cutscenes` are separate ledgers. Kenneth
  progression reads the observed ledger; reward accounting reads the paid ledger.

The exploration magnitudes below remain the archival/non-rails contract.

## When this applies

- Touching exploration reward shaping / cutscene / room / item / combat pay
- Debugging hacks (e.g. spamming main-hall door for Wesker, interact→cutscene pay)
- Judging whether a log line (`rewarded_cutscenes`, `unpaid_reason`, `ep_rew`) is correct

## Paid events

| # | Event | Magnitude | Episode | Status |
|---|--------|-----------|---------|--------|
| 1 | New room entered | **+4.0** | Extends **+6 min** idle cap | In force |
| 2 | Qualified ≥7.5s freeze paired with a newly rewarded room entry | **+1.2** | Resets stagnation clock | In force |
| 3 | New key item | **+4.0** | Extends **+6 min** idle cap | In force |
| 4 | Using key item | **+4.0** | Extends **+6 min** idle cap | In force |
| 5 | Weapon pickup (including wall shotgun) | **+4.0** | Extends **+6 min** idle cap (first acquire of that weapon this episode) | In force |
| 6 | Every non-key-item pickup | Modest: **0.15** | (no special rule stated) | In force |
| 7 | Hitting an enemy | Modest: **+0.007 per HP** | (no special rule stated) | In force |
| 8 | Killing an enemy | Modest: **+0.24** | (no special rule stated) | In force |
| 9 | Story-driven interaction (Gallery portrait sequence) | Modest: **+0.5 per correct switch** | Extends | In force |
| 10 | Document / book examine UI entered | **+4.0** | Extends **+6 min** idle cap (same path as new room) | In force |
| 11 | Typewriter save completed | Modest: **+0.3** | (no 6 min floor raise; PB sidecar start holdoff) | In force |

Buckets:

- **1, 3, 4, 5, 10**: **+4.0** and reset the softlock idle truncate budget to **6 min** (weapons: first acquire of that name this episode; documents: first rising edge into examine UI per room this episode)
- **2**: **+1.2** only when paired with new-room pay; same-room freezes are observed but unpaid
- **6–8, 11**: modest crumbs / combat / save
- **9**: **+0.5** per correct Gallery portrait switch; extends

Typewriter save (#11):

- Same detector as PB capture (`TypewriterSaveDetector`): ink_ribbon drop in a
  typewriter room → save cinema → stable control. Pays **+0.3** on that complete
  edge (`bd["typewriter_save"]`). Does not reset the 6 min idle budget.
- **Sidecar / PB starts:** holdoff until stable in_control + unchanged ribbon
  count so load settle cannot pay. Real saves after holdoff still pay.

Document examine (#10):

- Detector: exact `mode=0x40` + `gs=0x40808100` (`document_examine_ui_from_ram`). Assumes all books share that signature until a per-document ID is hunted.
- Pays on the **rising edge** into that UI (not every frame while reading).
- Anti-farm: **once per room per episode**. Leaving and reopening the same book in the same room does not re-pay; a first open in a different room can.
- Extends the idle truncate floor via the same `note_softlock_extension(SOFTLOCK_EXTENSION_FRAMES)` path as new room.

Gallery room 117 policy:

- Correct order is RDT slots `3 → 5 → 6 → 4 → 2 → 7`, detected from the
  confirmed `0x800C3008` one-hot progression.
- Each of those six switches pays +0.5 and resets the stagnation clock.
- A wrong confirmed switch or leaving room 117 claws back the full sum of
  Gallery-step rewards still pending in that attempt.
- After a wrong switch, Gallery rewards remain locked and the observation hint
  points to the room-117 exit. The lock clears only after Jill exits and
  reenters the room, at which point a precise fresh sequence can earn rewards.
- Pending Gallery rewards become permanent only when the Star Crest is
  acquired. The crest itself pays only through key-item channel #3; the final
  “end of life” switch does not also pay channel #9.
- Text/examine opens, proximity, Yes/No without confirmation, duplicate RAM
  observations, and `0x800C3009` do not pay. The `0x800C3009` confirmation
  edge is used only to detect a wrong first portrait when progress remains 0.
- Observation guidance is next-target bearing/distance plus sequence progress.

## Terminal Kenneth gate

**Imperator-approved:** Pre-Kenneth Main Hall entry terminates the episode:

- On a **transition into** Main Hall room **106** before the canonical Kenneth
  tea-room cutscene (`104:*:sN`) has been observed this episode → apply exactly
  **−0.05 once** under `main_hall_before_kenneth`, mark the terminal observation,
  and **end the episode immediately**.
- The 16-wide cutscene ledger's dormant opening slot becomes the persistent
  `wesker_pre_kenneth` bit in that terminal observation. This is observation
  only: it does not count as a rewarded cutscene or increase cutscene count.
- Do **not** mark 106 as visited on an illegal transition. Pre-Kenneth cutscenes
  in 106 (Wesker talk, etc.) do not pay `new_cutscene`.
- Do **not** trigger the gate when an episode starts in 106, while remaining
  in 106, or when entering 106 after Kenneth has paid.
- If Jill is actually dead on the same step, the real death path owns the
  ordinary global `death` penalty and the Kenneth gate term does not apply.

Kenneth marks progression when its tea-room freeze reaches the 450-frame gate.
As a same-room freeze it is observed but does not itself pay cutscene reward.

## Cutscene duration gate

Runtime turbo skip and menu dismiss behavior are unchanged. Cutscene reward
qualification uses the total uninterrupted uncontrolled session, including all
segments before and after a room crossing.

Same-room freezes mint keys `room:cam:sN` with
`MAX_SAME_ROOM_CUTSCENE_INDEX = 4` (paid indices `s0`…`s3`; further same-camera
settles stop paying `new_cutscene`).

An uncontrolled freeze pays #2 when it lasts **at least 450 emulated frames**
(7.5 seconds at 60fps), subject only to these exclusions:

| ID | Non-paying “cutscene-like” event |
|----|----------------------------------|
| a | Picking up an item (item pickup has its own channel: #3 / #5 / #6). Same-skip inventory growth never pays cutscene; after a key/weapon pickup, further same-room cutscene settles stay suppressed until Jill leaves that room (covers fragmented pickup cinema). |
| b | Opening a menu (e.g. HP text while menu open) |
| c | Death or opening/title sequences |
| d | Pre-Kenneth Main Hall (106) scripts; the −0.05 hall gate owns that beat |

There are no examine, idle-settle, dining↔tea, or room-change special cases in
the pay path. A short door/examine freeze is unpaid; a door load lasting at
least 450 frames may pay. This is an intentional simplicity tradeoff.

### Item box (validated 2026-08-07)

| Event | Magnitude | Notes |
|-------|-----------|-------|
| Successful open-box withdraw | **+1.0** (`BOX_WITHDRAW_BONUS`) | Per completed transfer; full magnitude on Yawn rails. Deposit still policy-off. |

### Not yet validated

| ID | Notes |
|----|--------|
| f | Cutscene/settle pay while using an item box (separate from withdraw +1) |

Do not invent further box pay/deny rules without imperator validation.

## Exceptions to room pay (#1)

Illegal pre-Kenneth transition into 106 withholds visit credit, applies −0.05,
marks `wesker_pre_kenneth`, and terminates the episode.

**Spawn room (dining 105 on m0):** marked visited at episode reset; the +4.0
`new_room` (and 6 min idle budget) pays on the **first** `compute_reward` of the
episode. That way dining discovery is not attributed to a later Wesker/door
settle. Re-entering dining never pays again.

## Combat pay (#7 / #8)

Hit / kill pay only when the step is an actual **knife** or **attack** action. Enemy HP flicker on interact / door / cutscene without a combat action must **not** pay. Magnitudes are independent statics: **+0.007** per enemy HP damaged, **+0.24** per kill (not × `CHECKPOINT_REWARD`). These combat positives retain the same magnitude in Yawn rails.

## Miss / ammo waste

**Ammo expenditure** (`ammo_spend`) applies on every spent gun round (hit or
miss): handgun **−0.03**, shotgun **−0.25**, magnum/GL/bazooka **−0.40**,
rocket **−0.75**. Deferred miss expiry skips a second spend charge
(`pending_combat_expired`).

On `attack_missed` with `ammo_spent > 0` only, an extra miss waste tax also
applies (knife has no clip tax):

`per_missed_round = −AMMO_PICKUP_BONUS / clip_size × 0.10`

Full inverse of one junk/ammo pickup, split across the magazine / pack. No 0.5×
halve (clip amortization is the adjustment).

| Weapon | clip_size | per missed round |
|--------|-----------|------------------|
| Beretta / handgun | 15 | ≈ −0.013333 |
| Shotgun | 7 | ≈ −0.028571 |
| Magnum / dumdum | 6 | ≈ −0.033333 |

The tax ramps toward `−0.15` for the last remaining round. Knife whiffs pay
`−0.001`; dry fire pays `−0.005`; rejected/failed attack macros pay `−0.01`.
All remain unscaled in Yawn rails.
| Grenade launcher / bazooka / rocket (acid/flame/explosive) | 6 (pack size; chamber holds 1) | −0.025 |

`ATTACK_MISS_PENALTY` / `KNIFE_MISS_PENALTY` / `AMMO_WASTE_PENALTY` remain 0.0
stubs; live waste writes `bd["ammo_waste"]` via the clip helpers; spend writes
`bd["ammo_spend"]`.

## HP damage / heal

All live reward/punishment magnitudes in `reward.py` are **independent static
floats** — not derived from `CHECKPOINT_REWARD` (legacy label only). Survival
budget **4.0**: Fine→1 chip **−8/3**, death **−4/3**. Per-HP scale
`HP_LOSS_SCALE` = **(8/3)/95** ≈ **0.02807017543859649** (Jill Fine
`JILL_FINE_HP=96`, not RAM ceiling 140). Living step cost **−0.00024**.

- Taking damage: linear per-HP penalty (`HP_LOSS_SCALE`).
- Healing: **exact inverse** of that punishment (same scale, opposite sign).
- Heal **USE** mask: legal when `hp <= 0.70 * JILL_FINE_HP` (integer HP ≤ 67);
  illegal above 70%. Poison-cure USE legal at any HP.

## Item pickup pay (#5 / #6)

- Every physical non-key-item pickup pays, including repeated pickups of the
  same type (another herb, ammunition box, etc.).
- Key items remain once-per-episode.
- **Gold emblem put-back (10F alcove):** putting `gold_emblem` back on the stand
  pays **−4.0** (`gold_emblem_return`) — exact inverse of key-item pickup.
  Intended path is USE wooden `emblem` at the stand (+4.0 story use); that keeps
  gold and does not trip the put-back penalty.
- The wall shotgun pays **+4.0** whenever Jill takes it and **−4.0** whenever
  she replaces it on the rack. A repeated take/replace loop is therefore net
  zero before step cost; leaving with the shotgun preserves the pickup reward.
  Re-takes after a return do **not** reset the 6 min idle budget or reset
  stagnation (blocks rack idle-clock farms).
- Weapon ammunition increases caused by reloading are not weapon pickups.
- New rooms, document examine, cutscenes, key items, story uses, gallery pays,
  and **first** weapon acquires reset the stagnation clock (junk/ammo/shotgun
  re-takes and document reopen in an already-paid room do not).
- Idle contempt: **3 min grace**, then a **3→6 min** ramp. **Starting play
  budget and every progress extension** (new room / document examine / key
  pickup / key use / first weapon / gallery, via `SOFTLOCK_EXTENSION_FRAMES`)
  are **6 min** emulated — one clock. Contempt budget is the independent
  static **|death|/5 ≈ 0.26666666666666666** (`CONTEMPT_BUDGET_SCALED`).
  Dense in scalar reward under main γ (**~0.996314**, ~25s pure-discount
  half-life) — no separate softlock MC channel.

## Agent rules

1. **No silent policy edits.** New paid events, new exceptions, magnitude changes, or changes to item-box rule (f) need imperator sign-off, then update this doc and `.cursor/skills/re-exploration-rewards/SKILL.md`.
2. **Cutscene duration owns the channel.** Any uninterrupted uncontrolled session ≥450 frames may pay unless it is a menu, pickup/post-pickup fragment, death/opening span, or pre-Kenneth hall script. Long doors may pay both room and cutscene channels.
3. **Kenneth gate terminates the episode.** Transition into 106 before `104:*:sN` paid → set terminal-observation ledger bit `wesker_pre_kenneth`, apply exactly −0.05 once, and terminate immediately. Do not mark 106 visited.
4. **Reward-hack hunts:** assume the agent will farm anything that pays. When spam appears (main-hall door, interacts), gate the **specific** signal; log unpaid reasons that match this table; total reward in diagnostics should come from **what enters the training data pool**, not a parallel counter.
5. **When unsure** whether an event belongs to an explicit exclusion: **do not guess a new exception** — ask.

## Quick decision

```
Event fired?
├─ Transition into 106 before Kenneth paid? → mark Wesker ledger bit; −0.05; terminate
├─ New room (legal)? → pay #1 (+4.0, reset 6m idle budget)
├─ Rising edge into document examine UI (0x40808100), unpaid room? → pay #10 (+4.0, reset 6m idle budget)
├─ Freeze / text / “cutscene”?
│  ├─ Total uninterrupted freeze <450 frames? → do NOT pay #2
│  ├─ Menu / pickup / death / opening / pre-Kenneth hall? → do NOT pay #2
│  └─ Otherwise → pay #2 (+1.2) once per key (long doors included)
├─ Key item get / use? → #3 / #4 (+4.0, reset 6m idle budget)
├─ Weapon get? → #5 (+4.0; first acquire → reset 6m idle budget); wall shotgun return → −4.0
├─ Other non-key item get? → #6 every pickup
├─ Hit / kill on knife|attack step? → #7 / #8
├─ Gallery portrait sequence? → +0.5 per correct ordered switch; claw back partial attempt on wrong input/exit
└─ Typewriter save complete (not during PB sidecar holdoff)? → #11 (+0.3)
```
