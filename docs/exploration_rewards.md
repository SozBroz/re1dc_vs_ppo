# RE exploration rewards

> **Canonical policy source:** adapted from `D:\awbw\.cursor\skills\re-exploration-rewards\SKILL.md` (2026-07-17). Rewrite for clarity only. Any change to *what* pays, magnitudes, exceptions, or status (implemented / not) requires **explicit imperator validation** before it goes into code or this doc.

Policy source: imperator.

## When this applies

- Touching exploration reward shaping / cutscene / room / item / combat pay
- Debugging hacks (e.g. spamming main-hall door for Wesker, interact→cutscene pay)
- Judging whether a log line (`rewarded_cutscenes`, `unpaid_reason`, `ep_rew`) is correct

## Paid events

| # | Event | Magnitude | Episode | Status |
|---|--------|-----------|---------|--------|
| 1 | New room entered | Large: **≥ +0.5** | Extends | In force |
| 2 | New **story-driven** cutscene | Large: **≥ +0.5** | Extends | In force |
| 3 | New key item | Large: **≥ +0.5** | Extends | In force |
| 4 | Using key item | Large: **≥ +0.5** | Extends | In force |
| 5 | Weapon pickup (including wall shotgun) | Large: **+1.0** | Extends | In force |
| 6 | Every non-key-item pickup | Modest: **typically below 0.5** | (no special rule stated) | In force |
| 7 | Hitting an enemy | Modest: **typically below 0.5** | (no special rule stated) | In force |
| 8 | Killing an enemy | Modest: **typically below 0.5** | (no special rule stated) | In force |
| 9 | Story-driven interaction (Gallery portrait sequence) | Large: **+0.5 per correct switch** | Extends | In force |

Buckets:

- **1–5**: large (≥ +0.5), **extends the episode**
- **6–8**: modest (typically below 0.5)
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

## Sole Kenneth gate (episode failure)

**Imperator-approved (Kenneth-gate revamp):** The only Kenneth-related hard gate is:

- On a **transition into** Main Hall room **106** before the canonical Kenneth
  tea-room cutscene (`104:*:sN`) has occurred/paid this episode → **immediately
  end the episode** and apply exactly **−0.1 once** under the dedicated
  `main_hall_before_kenneth` reward term (also the failure reason).
- Do **not** trigger when an episode starts in 106, while remaining in 106, or
  when entering 106 after Kenneth has paid.
- If Jill is actually dead on the same step, the real death path owns the
  ordinary global `death` penalty and the −0.1 Kenneth-gate term does not apply.
- The illegal-transition termination itself must prevent `new_room` reward
  (no separate pre-Kenneth Main Hall new-room gate).

Kenneth remains an **ordinary curated story cutscene** that rewards once under
normal qualification. There is **no** blanket suppression of unrelated
pre-Kenneth cutscene rewards, **no** special pre-Kenneth Wesker-talk cutscene
suppression, and **no** separate pre-Kenneth Main Hall `new_room` gate.

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

No Kenneth-specific room-pay exception. Illegal pre-Kenneth transition into 106
ends the episode before `new_room` can pay.

## Combat pay (#7 / #8)

Hit / kill pay only when the step is an actual **knife** or **attack** action. Enemy HP flicker on interact / door / cutscene without a combat action must **not** pay.

## Item pickup pay (#5 / #6)

- Every physical non-key-item pickup pays, including repeated pickups of the
  same type (another herb, ammunition box, etc.).
- Key items remain once-per-episode.
- The wall shotgun pays **+1.0** whenever Jill takes it and **−1.0** whenever
  she replaces it on the rack. A repeated take/replace loop is therefore net
  zero before step cost; leaving with the shotgun preserves the pickup reward.
- Weapon ammunition increases caused by reloading are not weapon pickups.
- New rooms, cutscenes, key items, story uses, gallery pays, and weapon pickups
  reset the stagnation clock (junk/ammo loops do not). Idle contempt: **3 min
  grace** matching the truncate cap (no ramp room → death-budget lump on the
  timeout step, ~0.333); progress resets the clock. Truncate at **3 min** of
  no progress. Dense in scalar reward under main γ (**0.99**) — no separate
  softlock MC channel.

## Agent rules

1. **No silent policy edits.** New paid events, new exceptions, magnitude changes, or changes to item-box rule (f) need imperator sign-off, then update this doc and `.cursor/skills/re-exploration-rewards/SKILL.md`.
2. **Prefer the right channel.** Item pickup → item/key/gun rewards, not cutscene. Door/stairs load → room reward (if allowed), not cutscene. Text-only interact → no cutscene pay (and #9 only when implemented for true story interacts).
3. **Sole Kenneth gate is illegal 106 entry.** Transition into 106 before `104:*:sN` paid → episode failure + exactly −0.1 once. Other story cutscenes (including valid pre-Kenneth dining/Barry beats) may pay under normal qualification. Kenneth itself pays once as an ordinary curated story beat.
4. **Reward-hack hunts:** assume the agent will farm anything that pays. When spam appears (main-hall door, interacts), gate the **specific** signal; log unpaid reasons that match this table; total reward in diagnostics should come from **what enters the training data pool**, not a parallel counter.
5. **When unsure** whether an event is story-driven vs (a–d): **do not guess a new exception** — ask.

## Quick decision

```
Event fired?
├─ Transition into 106 before Kenneth paid? → end episode; exactly −0.1 once; no new_room
├─ New room (legal)? → pay #1
├─ Freeze / text / “cutscene”?
│  ├─ Story-driven new beat under normal qual (incl. Kenneth 104:*:sN)? → pay #2
│  ├─ Pickup / menu / text-only interact / door-stairs load? → do NOT pay #2
│  └─ Unclear? → ask; do not invent
├─ Key item get / use? → #3 / #4
├─ Weapon get? → #5 (+1.0 and extend); wall shotgun return → −1.0
├─ Other non-key item get? → #6 every pickup
├─ Hit / kill on knife|attack step? → #7 / #8
└─ Gallery portrait sequence? → +0.5 per correct ordered switch; claw back partial attempt on wrong input/exit
```
