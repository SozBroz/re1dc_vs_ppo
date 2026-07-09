# Privileged Obs — Phase 0 Gap Matrix

**Date:** 2026-07-04. Inventory of desired spatial/semantic observation features vs current
repo state. Companion to `docs/privileged_obs_spec.md` (field-level spec) and
`docs/memory_hooks_and_observation_design.md` (purity rules, §0).

Sources legend: **RAM** = live PS1 MainRAM hunt/read · **static** = curated JSON from
walkthroughs/Evil Resource · **empirical** = logged from human/policy play ·
**RDT** = parsed from `room1XX0.rdt` disc files.

| Desired feature | Current state | Blocker | Recommended source |
|---|---|---|---|
| Egocentric item obs (top-K per room) | `room_items.json`: 121 items, 48 gates, **no coordinates**. `pickups_empirical.json` absent; `log_door_transitions.py` already writes it on inventory gain. | Human route run not yet done | **empirical** (primary) → `item_positions.json`; RDT ITEM_SET (phase 1b); ER prose only as low-confidence anchors |
| Enemy relative positions (top-5, off-camera "hearing") | `proprio[10]` hardcoded 0. PC ASL: Enemy1..6 HP `0x8353BC` + `0x18C`/slot (MediaKite). PS1 block unmapped; GOG→PS1 linear offset `-0x7211C0` verified only for save block, **unverified** for enemy heap. | PS1 RAM hunt required | **RAM** hunt (`scripts/hunt_enemy_ram.py`, protocol in `docs/enemy_ram_hunt.md`); encoder ready ahead of addresses |
| Interaction prompt bit | `proprio[11]` hardcoded 0. `MESSAGE_FLAG 0x800C8665` = message *open*, not prompt. | RAM hunt required | **RAM** diff near `0x800C86xx` (`scripts/hunt_interaction_prompt.py`) |
| SCD work flags (emblem placed, Barry lockpick, crow puzzle…) | None mapped. Anchor `DOOR_FLAGS 0x800C86B4` confirmed. 31 puzzle/event gates with empty `requires` hidden conservatively from `items_left_here`. | Save-diff campaign | **RAM** save-diff (`scripts/hunt_scd_flags.py`) → `data/scd_work_flags.json`; biohazard-utils wiki for bit semantics |
| Room walls / occupancy grid | Nothing. `doors_empirical.json`: 22 edges with door/entry poses. | RDT collision parser (16–24h, phase 4c) | **empirical** visited-mask now; **RDT** collision later |
| Visited-mask channel | Designed (`progress_scaffolding_design.md` §1.6), not implemented. | None — pure code | **empirical** (player trace, per-room 16×16) |
| Per-room enemy spawn lists | None (`room_enemies.json` absent). | ER scrape may be Cloudflare-blocked | **static** — `scripts/build_room_enemies.py` (ER scrape + manual transcription), positions stay RAM/RDT |
| All-exits obs (not just BFS next hop) | `RoomGraph` stores all edges but obs exposes only `exit_toward(goal)`. | None — code | **empirical** door table |
| Key-item usage hints (missing prereqs, suggested use) | `has_required_items` bit only. Route JSON has `required_items` + `macro_name`. | None — code | **static** route JSON |
| Gate visibility flip (visible-but-gated) | Empty-`requires` gates hidden (conservative, correct per brief). | SCD flags must be mapped first | **RAM** flags → then flip in `RoomItems` |
| Pixel ablation harness | Not implemented. | Needs richer privileged obs first | training config (Train A/B/C plan) |

## Priority order (per briefing)

1. `pickups_empirical.json` human route run → item bearings (tooling ready, needs play time)
2. Enemy RAM hunt with ASL spacing hypothesis → enemy obs
3. Interaction prompt RAM
4. SCD flags → correct `items_left_here`
5. Visited mask per room (cheap geometry, no blockers)
6. RDT doors/items/collision (long pole)
7. Obs fusion + pixel ablation eval
