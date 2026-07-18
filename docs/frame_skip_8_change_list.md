# Frame skip 4 → 8 — change list

**Status:** P0 landed in working tree · commit + fleet relaunch next  
**Branch:** `feature/world-almanac-extractor`

---

## [Sticky/env](3be96eb6-3f83-4dd2-ae35-2e5c3fc01c66) — DONE

### Safe (already holds full batch)
- RL `forward` / `run_forward`: sticky + `bridge.step(n=frame_skip)` → **full 8** once default flips
- Interact: `pulse_hold` every frame; `hold_n = frame_skip + 10` → **18** at skip=8
- Human movement: one press = one `frame_skip` chunk
- **Do NOT** multiply `hold_n` by 2 (that would burn 16 per step)

### Must change
| Pri | File | Action |
|-----|------|--------|
| P0 | `re1_rl/env.py` | `frame_skip` default **4 → 8** |
| P0 | `scripts/play_human.py` | `--frame-skip` default **4 → 8** |
| P0 | `env` quickturn path | 2-on/2-off period-4 → **two** quickturns at n=8; fix to **one pulse per step** (e.g. `pulse_off=6` or cap n=4 for QT) |
| P0 | `re1_rl/frame_ring.py` | `STRIDE = 4` → **8** (or bind to `frame_skip`) so stack stays `[t-3Δ…t]` |
| P1 | `re1_rl/sticky_input.py` | Rewrite “two 4-frame chunks” comments |
| P1 | `re1_rl/pushable.py` | Comment only; keep `PUSHABLE_HOLD_FRAMES=30` |
| P1 | `re1_rl/reward.py` | `REFERENCE_STEP_FRAMES = 4` → **8** (env already writes live `reference_step_frames`) |
| P2 | shelf probes / docs | stale skip=4; re-check interact EXTRA at 18f |

---

## [Softlock/rewards](2157471b-5170-4c32-9ccd-58301d221e05) — DONE

### Safe (no rescale)
- Softlock `43200` emulated frames, stagnation accumulator, cutscene skip gates, pushable 30f

### Decide / change
| Pri | Item | Action |
|-----|------|--------|
| P1 | Curriculum / eval `max_steps` | **Halve** if caps were tuned for skip=4 emulated horizon (e.g. 24000→12000) |
| P1 | Step contempt | Keep per-decision (`reference_step_frames=frame_skip`) **or** halve `STEPS_PER_CHECKPOINT` 5000→2500 for per-emu-frame parity |
| P1 | `REFERENCE_STEP_FRAMES` | Align fallback **4→8** with env default |
| P2 | Softlock spread over `n_steps` | Credit-assignment density only; no terminate change |

**Counsel default:** keep per-decision contempt + update `REFERENCE_STEP_FRAMES=8`; only halve episode caps if exploration mode still uses finite `max_steps`.

---

## [Tests/docs/CLI](e01a120d-f2be-4a8f-b68e-e938c2b6e656) — DONE

### Defaults (fleet inherits env — no launcher CLI today)
| Pri | File | Action |
|-----|------|--------|
| P0 | `env.py:245` | default **8** |
| P0 | `reward.py:37` | `REFERENCE_STEP_FRAMES` **8** |
| P0 | `play_human.py` | `--frame-skip` default **8** |
| P1 | `watch_shelf_agent.py`, `probe_shelf_push.py` | hardcode **8** (or drop override) |
| P1 | `probe_shelf_min_frames.py` | print math for skip=8 |
| P1 | Tests: `test_pushable`, `test_async_cutscene_skip`, `test_story_item_use`, reward harnesses | expect 8 / stop forcing 4 |
| P2 | sticky/pushable comments | rewrite two-chunk / skip=4 notes |

### Keep at 4 (explicit)
- Combat boss profile JSON example (future override)
- Cutscene `skip_frames=4` door tests
- Knife macro phases / `ring_stride=4` sampling
- `PUSHABLE_HOLD_FRAMES=30`
- Post-load `frameadvance(4)` settles
- Knife A/B vs old ckpts: **pin** `frame_skip=4` when comparing

### Docs
- Several already say 8 (`nn_architecture`, `memory_hooks` BC) — will match code after flip
- Fix sticky/pushable comments that still say 4

---

## [Macros/screenshot](9e338c43-0e88-42b3-9a8d-f0228dd194a1) — DONE

### Do NOT change (true emu-frame schedules)
- Knife / attack / inventory / options / equip macros phase lengths
- RAM-gated knife path; AttackFramePins entry/swing/end captures
- Normal training: `ring_stride=0` + `capture_final=True` (end-of-hold only)
- Softlock threshold (emu frames)

### Must change
| Pri | File | Action |
|-----|------|--------|
| P0 | `env.py` + `make_env` | default / pass **8**; fleet inherits |
| P0 | `frame_ring.py` STRIDE | **8** so stack spacing matches RL step |
| P0 | `reward.REFERENCE_STEP_FRAMES` | **8** |
| P0 | quickturn pulse | one pulse per step at n=8 |
| P1 | `knife_macro.py:1360` legacy fixed `ring_stride=4` | use `frame_skip` / 8 |
| P1 | Magic box `step(n=frame_skip)` | already dynamic — OK |
| P2 | Menu ack contempt with fake `step_emulated_frames=frame_skip` (no emu burn) | review 2× at skip=8 |

---

## Agreed implementation (this landing)

1. `frame_skip` default **8** (`env`, `play_human`, shelf probes)
2. `REFERENCE_STEP_FRAMES` / `progress.note_stagnation_step` default **8**
3. `FrameRingBuffer.STRIDE` **8** + tests
4. Quickturn: single pulse in 8-frame batch (`pulse_off=6` or equivalent)
5. Comments + tests aligned; knife A/B pins stay optional
6. Contempt: keep per-decision (`reference_step_frames=frame_skip`); do **not** halve `STEPS_PER_CHECKPOINT` unless asked
7. Curriculum `max_steps`: leave (exploration often uncapped); note in doc

Then commit → fleet relaunch.
