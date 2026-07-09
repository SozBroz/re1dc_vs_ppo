# Capture Session Runbook

**Date:** 2026-07-04 · **Script:** `scripts/capture_session.py`

One playthrough can populate doors, pickup positions, SCD work flags, and RAM-hunt
journals. Passive logging runs at ~5 Hz; typed commands snapshot RAM for hunts
without stopping the game.

Related docs: `docs/privileged_obs_spec.md`, `docs/enemy_ram_hunt.md`,
`docs/privileged_obs_gap_matrix.md`.

---

## Prerequisites

1. **EmuHawk** with RE1 Director's Cut (SLUS-00551) loaded.
2. **Lua bridge:** `lua/re1_client.lua` listening on port **5555**.
3. Python venv at `D:\re1_rl\venv\Scripts\python.exe`.

---

## Launch

```powershell
cd D:\re1_rl
D:\re1_rl\venv\Scripts\python.exe scripts\capture_session.py
```

Optional flags: `--port 5555`, `--poll-frames 12` (frames between polls).

After Ctrl+C:

```powershell
D:\re1_rl\venv\Scripts\python.exe scripts\build_item_positions.py
```

---

## Outputs

| File | Contents |
|------|----------|
| `data/doors_empirical.json` | Door edges: exit pose → entry pose, room IDs |
| `data/pickups_empirical.json` | Item pickups + ammo stacks with pose |
| `data/scd_work_flags.json` | SCD bit flags saved via `fa save` |
| `data/capture_sessions/session_<ts>.jsonl` | Hunt event journal (all commands) |
| `data/enemy_ram_hunt_<ts>.json` | Enemy kill-diff clusters (on `ea`) |

`build_item_positions.py` merges pickups into `data/item_positions.json`
(empirical beats manual anchors).

---

## Passive logging (always on)

### Doors

On room change, the script records the **last in-control pose** in the exit room
and the **first in-control pose** in the entry room. Cutscene/modal poses are
skipped (`in_control` gate).

### Pickups

Inventory diffs each poll:

| Event | Trigger | Notes |
|-------|---------|-------|
| `new_item` | New inventory slot | Logged **once per item name** per session |
| `ammo_stack` | Beretta qty increase | Mapped to `ground_item: clip`; can repeat |

Pose = last in-control pose (pickup modals don't corrupt coordinates).

---

## Commands

Type in the **Python terminal** while playing. Non-blocking stdin thread.

| Command | Alias | Purpose |
|---------|-------|---------|
| `help` | `h`, `?` | Print command list |
| `status` | | Room, pose, buffer counts |
| `fb` | `flag-before` | SCD flag RAM snapshot **before** triggering event |
| `fa` | `flag-after` | SCD snapshot after; prints bit flips |
| `fa save N name [room] [unlocks]` | | Save flip index `N` to `scd_work_flags.json` |
| `pa` | `prompt-away` | Interaction-prompt hunt: snapshot **away** from object |
| `pt` | `prompt-at` | Snapshot **at** interactable (repeat 3+ each) |
| `pan` | `prompt-analyze` | Diff AWAY vs AT buffers; print consistent bytes |
| `eb` | `enemy-before` | Full MainRAM snapshot (~2 s) with enemy alive |
| `ea` | `enemy-after` | Full MainRAM after kill; cluster rank + JSON dump |
| `ep [0xADDR]` | `enemy-probe` | One-shot struct probe (default `0x801141FC`) |

### SCD flag workflow (`fb` / `fa`)

1. Stand at milestone, **do not trigger** the event yet.
2. `fb` — captures SCD work-flag byte range.
3. Trigger event (pick up item, open door, solve puzzle).
4. `fa` — prints bit transitions.
5. `fa save 0 dining_emblem 105 main_hall` — persist to `scd_work_flags.json`.

Good milestones: emblem pickup, gallery door, sword/shield doors, MO reader, etc.

### Interaction prompt hunt (`pa` / `pt` / `pan`)

Goal: find RAM bytes that differ when the "Examine / Pick up" prompt is visible.

1. Stand near object; `pa` (away — no prompt).
2. Walk to prompt range; `pt` (at — prompt visible).
3. Repeat 3+ pairs in the same room.
4. `pan` — bytes that flip consistently across pairs.

False positives: camera changes, enemy AI, timer ticks. Prefer same-room pairs.

### Enemy RAM hunt (`eb` / `ea` / `ep`)

**Requires a quiet single-enemy room** (e.g. tea room `104`, first zombie).

1. `eb` before kill (~2 s MainRAM read).
2. Kill enemy with one shot if possible.
3. `ea` — ranks changed-byte clusters near candidate `0x801141FC`.
4. `ep` — dumps guessed struct layout at candidate base.

See `docs/enemy_ram_hunt.md` for interpretation.

---

## Recommended one-playthrough route

Jill mansion Any% is enough for Phase 1 acceptance (≥20 pickups, ≥10 doors).

| Segment | Passive | Active commands |
|---------|---------|-----------------|
| Dining → main hall | emblem pickup, door edge | `fb`/`fa` emblem; `pa`/`pt` on table |
| Tea room | first zombie | `eb`/`ea` kill diff |
| Gallery / L passage | doors + items | SCD flags on locked doors |
| Courtyard gate | medal doors | door edges + flags |
| Guardhouse (optional) | more pickups | extend as time allows |

One session is **viable**, not foolish — passive data accumulates while you play;
only pause for typed hunts at milestones.

---

## Quirks and costs

| Topic | Detail |
|-------|--------|
| Full RAM read | `eb`/`ea` ~1–2 s; game frozen via frameadvance |
| Pickup pose | Uses pre-modal in-control pose; reliable for `item_positions` |
| Ammo stacks | Beretta clip stacks log as `ammo_stack`, not duplicate `new_item` |
| SCD addresses | `SCD_FLAG_LO/HI` in `re1_rl/ram_hunt.py` — verify on hardware |
| Enemy table | Still unmapped; hunts produce candidates only |
| Door timing | Exit pose captured on room-ID change; entry on next in-control frame |

---

## Acceptance criteria (Phase 1)

- [ ] `pickups_empirical.json` — **≥20** distinct pickups with pose
- [ ] `doors_empirical.json` — **≥10** new edges beyond seed data
- [ ] `item_positions.json` — rebuilt; empirical entries at `confidence: high`
- [ ] `scd_work_flags.json` — **≥3** verified flags with room + unlocks text
- [ ] `enemy_ram_hunt_*.json` — at least one tea-room kill diff
- [ ] Tests still pass: `pytest tests/`

---

## Legacy entry point

`scripts/log_door_transitions.py` still works standalone; its `__main__` delegates
to `capture_session.py`. Prefer the unified session for new runs.
