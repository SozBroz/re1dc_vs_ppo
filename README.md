# re1_rl — Resident Evil 1 (1996) Deep RL

Hierarchical reinforcement-learning stack for **Resident Evil 1** (PS1 Director's Cut primary, PC GOG fallback). A symbolic waypoint planner over the 116-room mansion graph sits above a BC-warm-started PPO low-level policy; scripted macros handle deterministic interactions (doors, puzzles, menus).

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Waypoint planner (planner.py)                          │
│  route JSON → room objectives → success conditions      │
└──────────────────────────┬──────────────────────────────┘
                           │ next_waypoint_room(), rewards
┌──────────────────────────▼──────────────────────────────┐
│  Gymnasium env (env.py)                                   │
│  frame stack + proprio/goal vectors → PPO MultiInputPolicy │
└──────────────┬────────────────────────────┬───────────────┘
               │ PRIMARY                     │ FALLBACK
┌──────────────▼──────────────┐  ┌───────────▼──────────────┐
│  BizHawk + Lua TCP bridge   │  │  pymem + mss + pydirect  │
│  SLUS-00170 (PS1 DC)        │  │  ResidentEvil.exe (GOG)  │
└─────────────────────────────┘  └──────────────────────────┘
```

**Low-level actions (tank controls):** noop, forward, back, turn_left, turn_right, run_forward, quickturn, interact, aim, fire.

**Reward shaping (`reward.py`):** PBRS on room-graph hops + door distance (Ng et al. potential form), one-shot waypoint bonuses gated by max-progress hysteresis (`progress.py`), one-time wrong-room penalty, item pickups, HP loss, death, softlock timeout. Every step exposes a per-term `reward_breakdown` in `info`.

## Observation encoding (NN input)

Full spec with every field, address, normalization, and the reward table: **`docs/nn_architecture_and_encoding.md`**.

`env.py` emits a Dict obs — every float slot is **named** in `re1_rl/obs_encoder.py`:

| Key | Shape | Contents |
|-----|-------|----------|
| `frame` | 84×84×4 uint8 | grayscale stack (what the agent sees) |
| `proprio` | 20 float32 | body state: hp, hp_delta, room-local x/z, elevation, facing sin/cos, room index, cam, in_control, character (`PROPRIO_FIELDS`) |
| `goal` | 24 float32 | planner compass/TODO: goal room, route progress, door delta/distance/bearing, 5-d objective one-hot, item-TODO progress, pickups left in room, has-required-items, wrong-room flag (`GOAL_FIELDS`) |

Door compass and graph features come from `data/doors_empirical.json` via `re1_rl/room_graph.py` (BFS). Populate more doors with `scripts/log_door_transitions.py` (human plays, script records every room transition).

## Human-readability tools

| Tool | What it shows |
|------|---------------|
| `re1_rl.obs_encoder.format_obs_table(obs)` | console table: every obs slot with name, value, meaning |
| `scripts/watch_env.py` | live cv2 HUD: game frame + door compass, reward bars, planner state; `--policy model.zip` to watch a trained agent |
| `re1_rl.telemetry.EpisodeLogger` | env wrapper → one JSONL per episode (state, action names, reward breakdown) |
| `scripts/plot_episode.py --latest` | PNG: top-down per-room trajectory with door stars + reward-term timeline |
| `scripts/log_door_transitions.py` | records door coordinates while a human plays the route |
| `scripts/route_todo.py` | key-item TODO checklist for the route; `--rooms` adds per-room pickup lists |

## Items

Live inventory is read from PS1 RAM (`INVENTORY_BASE` in `memory_map.py`, 8 slots × (id, qty), confirmed against a fresh Jill start). `re1_rl/item_todo.py` derives the **key-item TODO** from the route JSON (35 items with prerequisites) and tracks acquisition by **ever-held** set, so banking an item keeps it checked and re-grabbing pays no reward. `data/room_items.json` (compiled from Evil Resource) lists every pickup per room and powers the `items_left_here` / `key_items_left_here` obs fields.

## Platform tracks

| Track | Emulator / binary | Integration |
|-------|-------------------|-------------|
| **PRIMARY** | BizHawk, ROM `SLUS-00170` (Director's Cut) | `lua/re1_client.lua` ↔ `re1_rl/bizhawk_bridge.py` TCP socket |
| **FALLBACK** | GOG PC `ResidentEvil.exe` | `re1_rl/pc_track/` — process memory, window capture, scancode input |

RAM constants for PS1 live in `re1_rl/memory_map.py` (BizHawk `MainRAM` domain). PC addresses differ; see `pc_track/process_memory.py`.

## Quickstart

```powershell
cd D:\re1_rl
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Install PyTorch with CUDA: https://pytorch.org/get-started/locally/

# Smoke test (no emulator required)
python scripts/smoke_test.py

# BizHawk (manual):
# 1. Load SLUS-00170 in BizHawk (octoshock core).
# 2. Tools → Lua Console → load lua/re1_client.lua
# 3. Python: from re1_rl.bizhawk_bridge import BizHawkClient; ...
```

## Directory layout

```
re1_rl/
├── re1_rl/           # Python package
│   ├── bizhawk_bridge.py
│   ├── env.py
│   ├── memory_map.py
│   ├── planner.py
│   ├── reward.py
│   ├── save_parser.py
│   └── pc_track/     # GOG PC fallback stubs
├── lua/              # BizHawk Lua client
├── curriculum/       # Per-stage JSON (savestate, waypoints, items)
├── scripts/          # smoke_test, training entrypoints (future)
├── states/           # BizHawk savestates (*.state) — gitignored
├── recordings/       # rollout captures — gitignored
├── data/             # route graphs, BC datasets — do not commit
└── tools/            # external tooling — do not commit
```

## Curriculum

Stage files under `curriculum/` define `init_savestate`, waypoint room IDs, `required_items`, and `max_steps`. See `curriculum/README.md`.

## Status

Skeleton only: working import plumbing, bridge protocol stubs, env/reward/planner interfaces. No training loop yet.
