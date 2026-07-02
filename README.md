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
│  frame stack + RAM vector → PPO policy (future)           │
└──────────────┬────────────────────────────┬───────────────┘
               │ PRIMARY                     │ FALLBACK
┌──────────────▼──────────────┐  ┌───────────▼──────────────┐
│  BizHawk + Lua TCP bridge   │  │  pymem + mss + pydirect  │
│  SLUS-00170 (PS1 DC)        │  │  ResidentEvil.exe (GOG)  │
└─────────────────────────────┘  └──────────────────────────┘
```

**Low-level actions (tank controls):** noop, forward, back, turn_left, turn_right, run_forward, quickturn, interact, aim, fire.

**Reward shaping:** step penalty, waypoint room transitions, item pickups, HP loss, death, softlock timeout (`reward.py`).

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
