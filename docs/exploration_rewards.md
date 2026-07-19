# RE exploration rewards

> **Canonical policy source:** adapted from `D:\awbw\.cursor\skills\re-exploration-rewards\SKILL.md` (2026-07-18). Rewrite for clarity only. Any change to *what* pays, magnitudes, exceptions, or status (implemented / not) requires **explicit imperator validation** before it goes into code or this doc.

Policy source: imperator.

## When this applies

- Touching exploration reward shaping / cutscene / room / item / combat pay
- Debugging hacks (e.g. spamming main-hall door for Wesker, interact→cutscene pay)
- Judging whether a log line (`rewarded_cutscenes`, `unpaid_reason`, `ep_rew`) is correct

## Paid events

| # | Event | Magnitude | Episode | Status |
|---|--------|-----------|---------|--------|
| 1 | New room entered | **+3.0** | Extends **+6 min** idle cap | In force |
| 2 | New **story-driven** cutscene | **+1.0** | Resets stagnation clock | In force |
| 3 | New key item | **+3.0** | Extends **+6 min** idle cap | In force |
| 4 | Using key item | **+3.0** | Extends **+6 min** idle cap | In force |
| 5 | Weapon pickup (including wall shotgun) | **+3.0** | Extends **+6 min** idle cap (first acquire of that weapon this episode) | In force |
| 6 | Every non-key-item pickup | Modest: **0.15** | (no special rule stated) | In force |
| 7 | Hitting an enemy | Modest: **typically below 0.5** | (no special rule stated) | In force |
| 8 | Killing an enemy | Modest: **typically below 0.5** | (no special rule stated) | In force |
| 9 | Story-driven interaction (Gallery portrait sequence) | Large: **+0.5 per correct switch** | Extends | In force |

Buckets:

- **1, 3, 4, 5**: **+3.0** and raise softlock idle truncate floor to **6 min** (weapons: first acquire of that name this episode)
- **2**: **+1.0**; resets stagnation but does **not** by itself raise the 6 min floor
- **6–8**: modest
- **9**: large; each correct Gallery portrait switch pays +0.5 and extends

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

## Soft Kenneth gate (no episode failure)

**Imperator-approved (soft Kenneth gate):** Pre-Kenneth Main Hall entry is a
dense penalty, not an episode reset:

- On a **transition into** Main Hall room **106** before the canonical Kenneth
  tea-room cutscene (`104:*:sN`) has occurred/paid this episode → apply exactly
  **−0.1 once** under `main_hall_before_kenneth`, **do not end the episode**,
  and **do not** mark 106 as visited (so a later legal entry after Kenneth can
  still earn `new_room` +3.0).
- Pre-Kenneth cutscenes in 106 (Wesker talk, etc.) **do not pay** `new_cutscene`.
- Do **not** trigger the soft gate when an episode starts in 106, while remaining
  in 106, or when entering 106 after Kenneth has paid.
- If Jill is actually dead on the same step, the real death path owns the
  ordinary global `death` penalty and the −0.1 term does not apply.

Kenneth remains an **ordinary curated story cutscene** that rewards once under
normal qualification. Other valid pre-Kenneth dining/Barry beats may still pay.

## Cutscene detector (heuristic, not a free paycheck)

Anything that **stops Jill from moving** can look like a “cutscene” to the detector. That is **necessary but not sufficient** for pay.

**Only story-driven cutscenes pay (#2).** Many freeze / text / transition events must **not** pay.

### Must not pay as cutscenes (Kenneth-independent)

| ID | Non-paying “cutscene-like” event |
|----|----------------------------------|
| a | Picking up an item (item pickup has its own channel: #3 / #5 / #6). Same-skip inventory growth never pays cutscene; after a key/weapon pickup, further same-room cutscene settles stay suppressed until Jill leaves that room (covers fragmented pickup cinema). |
| b | Opening a menu (e.g. HP text while menu open) |
| c | Interact that only puts text on screen |
| d | Door / stairs transitions that load a new room (room pay is #1, not #2) |

Short same-room idle/examine skips remain blocked globally. Long dining
idle-settle (≥ story floor) pays, including first Barry near spawn — there is
**no** dining→106 door-radius carve-out.

### Not yet validated

| ID | Notes |
|----|--------|
| f | Using an item box — need a macro to close the box; inventory in box rooms can use direct memory writes |

Do not treat (f) as a decided pay/deny rule until validated.

## Exceptions to room pay (#1)

Illegal pre-Kenneth transition into 106 withholds visit credit and `new_room`
(soft −0.1 instead). After Kenneth pays, the first real 106 entry may pay #1.

## Combat pay (#7 / #8)

Hit / kill pay only when the step is an actual **knife** or **attack** action. Enemy HP flicker on interact / door / cutscene without a combat action must **not** pay.

## HP damage / heal

- Taking damage: linear per-HP penalty (`HP_LOSS_SCALE`).
- Healing: **exact inverse** of that punishment (same scale, opposite sign). No
  0.8× haircut and no log compression.

## Item pickup pay (#5 / #6)

- Every physical non-key-item pickup pays, including repeated pickups of the
  same type (another herb, ammunition box, etc.).
- Key items remain once-per-episode.
- **Gold emblem put-back (10F alcove):** putting `gold_emblem` back on the stand
  pays **−3.0** (`gold_emblem_return`) — exact inverse of key-item pickup.
  Intended path is USE wooden `emblem` at the stand (+3.0 story use); that keeps
  gold and does not trip the put-back penalty.
- The wall shotgun pays **+3.0** whenever Jill takes it and **−3.0** whenever
  she replaces it on the rack. A repeated take/replace loop is therefore net
  zero before step cost; leaving with the shotgun preserves the pickup reward.
  Re-takes after a return do **not** re-raise the 6 min idle floor or reset
  stagnation (blocks rack idle-clock farms).
- Weapon ammunition increases caused by reloading are not weapon pickups.
- New rooms, cutscenes, key items, story uses, gallery pays, and **first**
  weapon acquires reset the stagnation clock (junk/ammo/shotgun re-takes do not).
- Idle contempt: **3 min grace**. Pre-Kenneth truncate at **3 min**; after
  Kenneth (`104:*:sN`) pays, truncate doubles to **6 min** with a **3→6 min**
  ramp. **New room / key pickup / key use / first weapon acquire floor the idle
  cap at 6 min** (even pre-Kenneth). Contempt budget is **1/5** of death
  (~0.0667). Dense in scalar reward under main γ (**0.9925**) — no separate
  softlock MC channel.

## Agent rules

1. **No silent policy edits.** New paid events, new exceptions, magnitude changes, or changes to item-box rule (f) need imperator sign-off, then update this doc and `.cursor/skills/re-exploration-rewards/SKILL.md`.
2. **Prefer the right channel.** Item pickup → item/key/gun rewards, not cutscene. Door/stairs load → room reward (if allowed), not cutscene. Text-only interact → no cutscene pay (and #9 only when implemented for true story interacts).
3. **Soft Kenneth gate is illegal 106 entry.** Transition into 106 before `104:*:sN` paid → exactly −0.1 once, episode continues, 106 not marked visited; pre-Kenneth hall cutscenes (Wesker) do not pay. Other story cutscenes (including valid pre-Kenneth dining/Barry beats) may pay under normal qualification. Kenneth itself pays once as an ordinary curated story beat.
4. **Reward-hack hunts:** assume the agent will farm anything that pays. When spam appears (main-hall door, interacts), gate the **specific** signal; log unpaid reasons that match this table; total reward in diagnostics should come from **what enters the training data pool**, not a parallel counter.
5. **When unsure** whether an event is story-driven vs (a–d): **do not guess a new exception** — ask.

## Quick decision

```
Event fired?
├─ Transition into 106 before Kenneth paid? → −0.1 once; continue episode; no visit/new_room (legal +3.0 later)
├─ New room (legal)? → pay #1 (+3.0, 6m idle floor)
├─ Freeze / text / “cutscene”?
│  ├─ Story-driven new beat under normal qual (incl. Kenneth 104:*:sN)? → pay #2 (+1.0)
│  ├─ Pickup / menu / text-only interact / door-stairs load? → do NOT pay #2
│  └─ Unclear? → ask; do not invent
├─ Key item get / use? → #3 / #4 (+3.0, 6m idle floor)
├─ Weapon get? → #5 (+3.0; first acquire → 6m idle floor); wall shotgun return → −3.0
├─ Other non-key item get? → #6 every pickup
├─ Hit / kill on knife|attack step? → #7 / #8
└─ Gallery portrait sequence? → +0.5 per correct ordered switch; claw back partial attempt on wrong input/exit
```
