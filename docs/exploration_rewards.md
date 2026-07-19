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
| 2 | Uncontrolled freeze lasting **≥7.5s** (450 emulated frames), unless excluded below | **+1.5** | Resets stagnation clock | In force |
| 3 | New key item | **+3.0** | Extends **+6 min** idle cap | In force |
| 4 | Using key item | **+3.0** | Extends **+6 min** idle cap | In force |
| 5 | Weapon pickup (including wall shotgun) | **+3.0** | Extends **+6 min** idle cap (first acquire of that weapon this episode) | In force |
| 6 | Every non-key-item pickup | Modest: **0.15** | (no special rule stated) | In force |
| 7 | Hitting an enemy | Modest: **typically below 0.5** | (no special rule stated) | In force |
| 8 | Killing an enemy | Modest: **typically below 0.5** | (no special rule stated) | In force |
| 9 | Story-driven interaction (Gallery portrait sequence) | Large: **+0.5 per correct switch** | Extends | In force |

Buckets:

- **1, 3, 4, 5**: **+3.0** and raise softlock idle truncate floor to **6 min** (weapons: first acquire of that name this episode)
- **2**: **+1.5**; resets stagnation but does **not** by itself raise the 6 min floor
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

## Poisoned Kenneth gate (episode continues)

**Imperator-approved:** Pre-Kenneth Main Hall entry irreversibly poisons the
rest of that episode:

- On a **transition into** Main Hall room **106** before the canonical Kenneth
  tea-room cutscene (`104:*:sN`) has occurred/paid this episode → apply exactly
  **−1.6 once** under `main_hall_before_kenneth` and **do not end the episode**.
- From that transition onward, **all positive reward terms are forced to zero**
  for the rest of the episode. Negative rewards remain active.
- Reward-driven stagnation resets and six-minute episode extensions are
  disabled; any extension already earned is revoked and the idle cap remains
  at the three-minute pre-Kenneth threshold.
- Do **not** mark 106 as visited on an illegal transition. Pre-Kenneth cutscenes
  in 106 (Wesker talk, etc.) do not pay `new_cutscene`.
- Do **not** trigger the gate when an episode starts in 106, while remaining
  in 106, or when entering 106 after Kenneth has paid.
- If Jill is actually dead on the same step, the real death path owns the
  ordinary global `death` penalty and the Kenneth gate term does not apply.

Kenneth rewards once when its tea-room freeze reaches the same 450-frame
duration gate. It does not require scene/message peak evidence.

## Cutscene duration gate

Runtime turbo skip and menu dismiss behavior are unchanged. Cutscene reward
qualification uses the total uninterrupted uncontrolled session, including all
segments before and after a room crossing.

An uncontrolled freeze pays #2 when it lasts **at least 450 emulated frames**
(7.5 seconds at 60fps), subject only to these exclusions:

| ID | Non-paying “cutscene-like” event |
|----|----------------------------------|
| a | Picking up an item (item pickup has its own channel: #3 / #5 / #6). Same-skip inventory growth never pays cutscene; after a key/weapon pickup, further same-room cutscene settles stay suppressed until Jill leaves that room (covers fragmented pickup cinema). |
| b | Opening a menu (e.g. HP text while menu open) |
| c | Death or opening/title sequences |
| d | Pre-Kenneth Main Hall (106) scripts; the −1.6 hall gate owns that beat |

There are no examine, idle-settle, dining↔tea, or room-change special cases in
the pay path. A short door/examine freeze is unpaid; a door load lasting at
least 450 frames may pay. This is an intentional simplicity tradeoff.

### Not yet validated

| ID | Notes |
|----|--------|
| f | Using an item box — need a macro to close the box; inventory in box rooms can use direct memory writes |

Do not treat (f) as a decided pay/deny rule until validated.

## Exceptions to room pay (#1)

Illegal pre-Kenneth transition into 106 withholds visit credit and poisons all
positive rewards/extensions for the episode (−1.6 once).

**Spawn room (dining 105 on m0):** marked visited at episode reset; the +3.0
`new_room` (and 6 min idle floor) pays on the **first** `compute_reward` of the
episode. That way dining discovery is not attributed to a later Wesker/door
settle. Re-entering dining never pays again.

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
2. **Cutscene duration owns the channel.** Any uninterrupted uncontrolled session ≥450 frames may pay unless it is a menu, pickup/post-pickup fragment, death/opening span, or pre-Kenneth hall script. Long doors may pay both room and cutscene channels.
3. **Kenneth gate poisons the episode.** Transition into 106 before `104:*:sN` paid → exactly −1.6 once; episode continues, but every later positive reward and reward-driven extension is disabled. Existing extensions are revoked, the idle cap stays at three minutes, and 106 is not marked visited. Negative rewards remain active.
4. **Reward-hack hunts:** assume the agent will farm anything that pays. When spam appears (main-hall door, interacts), gate the **specific** signal; log unpaid reasons that match this table; total reward in diagnostics should come from **what enters the training data pool**, not a parallel counter.
5. **When unsure** whether an event belongs to an explicit exclusion: **do not guess a new exception** — ask.

## Quick decision

```
Event fired?
├─ Transition into 106 before Kenneth paid? → −1.6 once; continue episode poisoned
│  └─ Thereafter: zero all positive rewards; no stagnation resets/extensions; 3m idle cap
├─ New room (legal)? → pay #1 (+3.0, 6m idle floor)
├─ Freeze / text / “cutscene”?
│  ├─ Total uninterrupted freeze <450 frames? → do NOT pay #2
│  ├─ Menu / pickup / death / opening / pre-Kenneth hall? → do NOT pay #2
│  └─ Otherwise → pay #2 (+1.5) once per key (long doors included)
├─ Key item get / use? → #3 / #4 (+3.0, 6m idle floor)
├─ Weapon get? → #5 (+3.0; first acquire → 6m idle floor); wall shotgun return → −3.0
├─ Other non-key item get? → #6 every pickup
├─ Hit / kill on knife|attack step? → #7 / #8
└─ Gallery portrait sequence? → +0.5 per correct ordered switch; claw back partial attempt on wrong input/exit
```
