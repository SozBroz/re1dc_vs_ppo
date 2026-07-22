# 09 — Almanac shield_key/attic defects & PB savestate progress markers

**Date:** 2026-07-21  
**Scope:** What is actually wrong with the almanac (esp. shield_key → attic), and what episode-side state a PB savestate must carry so NN obs match a from-start run.

---

## 1. What's wrong with the almanac? (executive)

Two different things get conflated:

| Layer | Shield_key / attic status |
|-------|---------------------------|
| **Machine data the NN reads** (`item_affordances.json`, `room_items.json` gate, `doors_rdt.json` `20E→210`, world MLP / spatial tests) | **Mostly correct** — shield opens **only** Attic Entry → Attic |
| **Human docs / route / harvest gaps** | **Poisoned or incomplete** — still teach agents “helmet doors,” wrong crest room, no empirical attic poses |

So C7 is real, but for this slice it is **not** “the JSON says the wrong door.” It is **docs + route + missing empirical harvest + builder regen traps** that can re-poison the chain.

---

## 2. Shield_key → attic — concrete defects

### Ground truth (Evil Resource / north_star / shipped JSON)

| Fact | Correct |
|------|---------|
| Wooden `emblem` pickup | Dining **`105`** (table) |
| `emblem` USE | Bar alcove **`10F`** → `gold_emblem` |
| `gold_emblem` USE | Dining fireplace **`105`** → clock slides |
| `shield_key` pickup | **`105`** behind clock, gated on `gold_emblem` |
| `shield_key` unlock | **Only** **`20E` → `210`** (FRONT OF ATTIC → ATTIC) |
| `helmet_key` unlock | **`10A→119`**, **`201→215`**, **`20B→20C`** (different key) |

Shipped `data/item_affordances.json` → `shield_key` matches that. Encoder tests assert affordant-in-`20E`, unlock-hint → `210`.

### What is specifically wrong

| Severity | Defect | Citation |
|----------|--------|----------|
| **P0 docs** | `shield_key` still described as opening **helmet doors** | [`docs/item_gates.md`](../item_gates.md) L115: *“Requires gold_emblem; **helmet doors** and moon crest path”*; [`.cursor/skills/re1-rl-north-star/SKILL.md`](../../.cursor/skills/re1-rl-north-star/SKILL.md) L24: *“shield key → helmet doors”* |
| **P0 route** | `star_crest` at wrong room | `route_jill_anypct.json` seq 9: **`107`**; data/ER: **`117`** Gallery |
| **P1 harvest** | No empirical door poses for attic approach | `doors_empirical.json`: **zero** `20D`/`20E`/`20F`/`210` edges (RDT-only graph) |
| **P1 builder trap** | Regenerating affordances can flip emblem pickup/use | `scripts/build_item_affordances.py` `_MANUAL["emblem"]`: pickup `10F` / use `105` — **inverted** vs truth; shipped JSON is OK only because room_items override today |
| **P2 notes** | Emblem “fireplace” wording | `room_items.json` / affordances notes say fireplace; pickup is **table** |
| **P2 doors** | RDT edge has no key bit | `doors_rdt.json` `20E→210` `"gated": false` — key only via affordances layer; no live `DOOR_FLAGS` |
| **P2 route thin** | No pre-Yawn `20E` waypoint | seq 19 jumps to `210` with `shield_key` required; entry door inferred from graph |
| **P3** | Yawn enemy unverified; moon_crest event-gated empty requires | `room_enemies.json` `210` `"unverified": true`; crest hidden until SCD |

**Bottom line for attic:** Do not “fix shield_key door edges in JSON” first — **fix the docs/skill that still say helmet doors**, certify the emblem→gold→shield notes, harvest empirical `20E` poses, and lock the builder so regen cannot invert emblem.

---

## 3. Other early-mansion poisons on the Yawn path (brief)

- **2F west unharvested** (`207` vs `203`) — empirical doors missing.  
- **`119` Courtyard Study** unmapped (`N/A`) — post-Yawn / helmet path.  
- **Phantom RDT room codes** pollute neighbor gathers (finish-line audit).  
- **Route aliases** (`wooden_emblem`, `piano_notes`) — code often normalizes; raw route misleads tools.

---

## 4. PB savestates — what must be shared for NN consistency

You are right: BizHawk `.State` alone is **not** enough. The policy Dict obs mixes:

1. **Emulator Markov state** (RAM → HP, xyz, inventory, room, flags, box, maps_files, …) — restored by savestate.  
2. **Episode-side Python memory** (progress trackers, history deques, visited masks, anti-farm sets) — **not** in the savestate; if left empty/fresh, the NN sees a “time traveler” with attic inventory but zero history / zero ever-held / unpaid cutscenes, which **does not** match a from-start trajectory.

### 4.1 Principle

A **PB bundle** = `{savestate file} + {episode_sidecar.json}` such that after `load_savestate` + `apply_sidecar`, every obs key that depends on episode memory matches what `_build_obs` would produce if the agent had walked there legally (same inventory, rooms visited, keys ever-held, cutscenes claimed, etc.).

Anti-farm and reward claim sets must be reconstructed so we **do not re-pay** rooms/keys/cutscenes already earned on the path to the PB — otherwise reverse curriculum becomes a reward farm.

### 4.2 What the sidecar must contain (minimum)

Derived from live env / reward / obs builders:

| Sidecar field | Feeds obs / reward | Source today |
|---------------|--------------------|--------------|
| `visited_rooms` | `rooms_visited`, softlock / Kenneth gate logic | `ProgressTracker.visited_rooms` |
| `rewarded_cutscenes` | `cutscene_ledger`, Kenneth / Wesker bits, cutscene anti-farm | `ProgressTracker.rewarded_cutscenes` |
| `ever_held` key/item set | `keys_held`, `world_state` pickup masks, affordance joins | `ItemTracker.ever_held` |
| `weapons_acquired_this_ep` (or equivalent) | first-weapon +4 / shotgun return rules | reward / progress |
| `documents_examined_rooms` | doc +4 once-per-room | progress / reward claim set |
| `story_uses_claimed` | story USE +4 sites already paid | progress |
| `gallery_*` (progress, pending claw, lock) | Gallery obs tail + reward clawback | gallery puzzle state |
| `history` room deque | `history` obs | `EpisodeHistory` / `RoomTransitionDeque` |
| `acquisitions` log | `acquisitions` obs | `AcquisitionLog` |
| `milestones` inputs | derived booleans | usually recomputed from above |
| `visited` mask per room (or rebuild policy) | `visited` 16×16 | `VisitedMask` — either serialize planes or **reset to empty in current room only** with documented policy |
| `softlock` / idle clock fields | contempt timing | `ProgressTracker` extension frames, last-progress frame |
| `box_cache` (optional) | `box` obs outside box rooms | env `_box_cache` |
| `pickup_cutscene_block_room` | anti double-pay | `ProgressTracker` |

**Usually NOT needed in sidecar** (come from RAM after load):

- Live inventory slots, HP, pose, facing, room_id, cam, maps_files bits, box contents (if in box room), enemy HP (if live table works).

**Static almanac** stays global (JSON / WorldCatalog buffers) — same for all PBs; sidecar must not embed a private catalog.

### 4.3 Capture trigger (your PB idea)

On first episode achievement of a **milestone** (examples):

- new key item ever-held (`lockpick`, `emblem`, `gold_emblem`, `shield_key`, …)  
- story USE claimed  
- first entry to attic foyer `20E` / attic `210`  
- first Yawn contact (separate combat PB)

**Enable flag (fleet default off):** set `RE1_PB_CAPTURE=1` in the worker environment. Without it, `maybe_capture_pb` is a no-op.

**Hand-crafted v1 taxonomy** (`re1_rl/pb_milestones.py`):

| Kind | Trigger id prefix | Archive set |
|------|-------------------|-------------|
| Key ever-held | `key:<item>` | `lockpick`, `emblem`, `music_notes`, `gold_emblem`, `shield_key`, `armor_key`, `wind_crest`, `sun_crest`, `moon_crest`, `star_crest` |
| First room visit | `room:<id>` | `20E`, `210` |
| Story USE (verified sites) | `story_use:<site_id>` | `music_notes@10F_piano`, `emblem@10F_alcove`, `gold_emblem@105_fireplace` |

Discrete milestones only for v1 — no continuous “run is going well” score as the sole trigger.

**Capture procedure:**

1. Emulator: write `states/pb/<trigger_id>_<timestamp>.State` via `re1_rl/pb_capture.maybe_capture_pb`.  
2. Env: dump sidecar from trackers via `re1_rl/pb_sidecar.dump_episode_sidecar`.  
3. Manifest row in `states/pb/manifest.jsonl`: `{trigger_id, state_path, sidecar_path, room_id, inventory_fingerprint, schema_version, captured_at_iso}`.  
4. Curriculum: sample mix of fresh + intermediate PBs; on reset pass `options={"pb_bundle": {"state_path": ..., "sidecar_path": ...}}` → `load_savestate` → `apply_episode_sidecar` → rebuild obs once. Default reset still uses `states/jill_control_fresh.State`.

### 4.4 Consistency checks (fail closed)

After load+sidecar, assert:

- `state["room_id"]` matches manifest room  
- inventory IDs ⊆ expected for milestone (e.g. shield_key PB holds shield or has it in box)  
- `shield_key ∈ ever_held` if milestone ≥ shield pickup  
- `visited_rooms` contains dining `105` and path rooms you require  
- `encode_world_state` pickup_active for shield is **off** if already taken  
- No route/waypoint fields written into obs (north star)

### 4.5 What this is not

- Not a compass / route index in obs  
- Not autoplay to the PB  
- Not skipping learning of buttons — only **reset distribution** so the NN sees attic/Yawn states more often, with honest episode memory

### 4.6 Implemented API (`re1_rl/pb_sidecar.py`)

Episode-side capture/restore is implemented in **`re1_rl/pb_sidecar.py`** (`SIDECAR_SCHEMA_VERSION = 1`). Env capture hooks are not wired yet; callers dump/apply explicitly.

| Function | Role |
|----------|------|
| `dump_episode_sidecar(env_or_parts, *, captured_room_id=..., captured_at_iso=...)` | Full sidecar dict from env (`_progress`, `_items`, `_episode_history`, optional `_box_cache`) or `EpisodeSidecarParts` |
| `apply_episode_sidecar(env_or_parts, data)` | Restore trackers; raises `SidecarSchemaError` on version mismatch |
| `progress_to_sidecar` / `apply_progress_sidecar` | `ProgressTracker` only (unit tests) |
| `item_tracker_to_sidecar` / `apply_item_tracker_sidecar` | `ever_held` only |
| `history_to_sidecar` / `apply_history_sidecar` | Room deque + acquisition log |

**Not serialized:** waypoint index, route seq, compass / next-room fields. **Tests:** `tests/test_pb_sidecar.py`.

---

## 5. Recommended next actions (narrow)

1. **Fix docs/skill** helmet-door wording for `shield_key` (P0, minutes).  
2. **Fix route** `star_crest` `107` → `117`.  
3. **Lock** `build_item_affordances.py` emblem manual block to match shipped JSON.  
4. **Harvest** empirical doors for `20E`/`210` when a human run is available.  
5. ~~**Spec + implement** PB bundle schema~~ — landed: sidecar + typewriter champion (see Doc 10 §5). Softlock on curriculum apply: **`reset_softlock=True`** clears stagnation and sets full `SOFTLOCK_EXTENSION_FRAMES` (dump still records capture-time values for forensics).

---

## Addendum — shared champion layout (2026-07-21)

```
states/pb/champions/mainhall_typewriter/
  champion.State
  champion.sidecar.json
  champion.json          # score + project-relative paths
```

Async fleet sync: local `RE1_PB_ROOT` ↔ `RE1_PB_SHARED_ROOT` via `pb_sync.py` (delayed background; training does not block).

---

*Document version: 1.1 — PB softlock reset + typewriter champion paths.*
