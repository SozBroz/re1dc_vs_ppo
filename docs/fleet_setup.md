# Distributed fleet — four-machine layout

**Repo:** `https://github.com/SozBroz/re1dc_vs_ppo.git`

| Machine | Host | Role | Script |
|---------|------|------|--------|
| **workhorse3** | `192.168.0.229` | **Learner** — PPO train, checkpoints, HTTP weights | `C:\Users\sshuser\re1_rl` |
| **workhorse2** | `192.168.0.116` | Remote **worker** — BizHawk rollouts (24 envs) | `C:\Users\sshuser\re1_rl` |
| **workhorse1** | `192.168.0.203` | Remote **worker** — BizHawk rollouts only | `D:\re1_rl` |
| **pking** (dev) | local | Remote **worker** | `D:\re1_rl` |

Workers **never** load policy from disk; they pull weights from the learner at warmup and hot-sync after each train step.

**Learner pointer:** `fleet/fleet_hosts.cmd` → `FLEET_LEARNER_HOST=192.168.0.229` (WH3). Planner-loyal stack: `run_distributed_learner_wh3_planner_loyal_stack.cmd` + WH2/WH1/pking `*_planner_loyal.cmd`. Thin cells (`RE1_YAWN_LEG_REPLAY=0`). WH2 learner scripts remain as emergency fallback.

**Collection path:** each worker box runs the same **desync async actors** as monolithic `train_parallel.py` (`re1_rl/distributed/async_worker_runtime.py` + `async_fleet._actor_process`). Inference is local; only completed rollouts cross the network. Synced `SubprocVecEnv` is no longer used for distributed workers.

Monolithic single-box training remains: `scripts/train_parallel.py` (async fleet) or `scripts/launch_fleet_grid.py`.

---

## Install (each Windows box)

```powershell
cd D:\re1_rl
git clone https://github.com/SozBroz/re1dc_vs_ppo.git .
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
# PyTorch CUDA: https://pytorch.org/get-started/locally/
```

Copy locally (not in git):

- `roms/` — RE1 DC cue/bin
- `tools/BizHawk-2.11.1/` — EmuHawk
- `states/jill_control_fresh.State` — curriculum init savestate

Firewall: workers need **outbound** TCP to learner port **8765**. Learner binds `0.0.0.0:8765` on LAN.

---

## Checkpoints

Canonical layout:

```
data/checkpoints/
  latest.json              # global pointer (highest-step run)
  reward_tune_1040k/
    latest.json            # run-local pointer
    ppo_re1_*_steps.zip    # keep latest 5 via scripts/prune_checkpoints.py
```

Resume on learner:

```powershell
python scripts/distributed_train_parallel.py --role learner --machine-name workhorse3 ^
  --run-name planner_loyal_shield_key --resume auto
```

`resolve_resume_path()` reads `latest.json` in the run dir, then newest mtime. Workers ignore checkpoints.

Prune old saves:

```powershell
python scripts/prune_checkpoints.py --keep 5
```

---

## Port plan (avoid collisions)

| Machine | `--base-port` | `--n-envs` | Bottleneck |
|---------|---------------|------------|------------|
| workhorse3 (learner + local worker) | 5855 | **24** | RTX 5090 32 GB; **96 GB RAM**; batch 4096; Muse must be down |
| workhorse2 | 5555 | **24** | 64 GB remote worker (28 wedged when this box was learner) |
| workhorse1 | 5655 | **8** | 8 CPU threads — launch from RDP/console |
| pking | 5755 | **24** | **72 GB RAM** (~1.5–2 GB/EmuHawk); 20 was the 48 GB cap |

**BizHawk visibility (fleet default):** only **pking** runs `--no-headless` with `--tile-windows` (6×4 grid) for savestate/screenshot/debug. WH3 learner + WH1/WH2 workers use `--headless`.

Weight sync / experience: **6-minute epochs**. Remotes buffer rollouts, then once per `--sync-interval-s` (default **360**) upload a burst and pull weights. Learner **waits for all live workers** (heartbeat registry) to contribute that epoch, with `--epoch-grace-s` (default 120) so a dead box cannot stall forever. Remotes heartbeat every ~30s; no heartbeat for `--worker-liveness-s` (default 90) drops them from the expected set (pking can leave/rejoin freely). WH3 planner-loyal launcher uses `--batch-size 4096` and `--max-pending-steps 220000`. `max_staleness` default **1**.

**WH3:** stop Muse/llama-server before launching the learner + 24 local envs. Do not raise WH3 local past 24 until the first 6-minute epoch keeps ≥16 GB host free. Do not raise pking past 24 without watching the watchdog (24 failed on 48 GB).

---

## Static LAN addresses

DHCP drift broke fleet wiring when workhorse1 moved `.160` → `.203` and workhorse2 `.111` → `.116`. Canonical addresses live in `fleet/fleet_hosts.cmd` and `fleet/fleet_hosts.json`.

| Machine | Static IP |
|---------|-----------|
| workhorse1 | `192.168.0.203` |
| workhorse2 | `192.168.0.116` |
| workhorse3 | `192.168.0.229` |

**Pin once per box** (elevated PowerShell on that machine):

```powershell
cd D:\re1_rl   # or C:\Users\sshuser\re1_rl on WH2
powershell -ExecutionPolicy Bypass -File tools\set_fleet_static_ip.ps1 -Role workhorse1
powershell -ExecutionPolicy Bypass -File tools\set_fleet_static_ip.ps1 -Role workhorse2
powershell -ExecutionPolicy Bypass -File tools\set_fleet_static_ip.ps1 -Role workhorse3
```

Idempotent — skips if already static. Also set **DHCP reservations** on the router (`192.168.0.1`) for the machines' MAC addresses as a belt-and-suspenders backup.

Adjust if a box runs monolithic `train_parallel` instead of distributed worker.

---

## Launch commands

**workhorse3 — planner-loyal learner + local fleet:**

```powershell
cd C:\Users\sshuser\re1_rl
.\fleet\local\start_learner_detached_wh3_planner_loyal.cmd
```

**workhorse2 / workhorse1 / pking — planner-loyal remote workers:**

```powershell
.\fleet\local\run_distributed_worker_workhorse2_planner_loyal.cmd   # WH2, 24 envs
.\fleet\local\run_distributed_worker_workhorse1_planner_loyal.cmd
.\fleet\local\run_distributed_worker_pking_planner_loyal.cmd         # pking, 24 envs
```

**workhorse1 / workhorse2 / workhorse3 headless desktop:** BizHawk needs an always-on interactive console session (not SSH Session 0). Configure once per box:

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_always_on_desktop.ps1 -Role worker
powershell -ExecutionPolicy Bypass -File tools\setup_always_on_desktop.ps1 -Role learner  # WH3
```

Then reboot. Prefer an HDMI dummy plug if no monitor. Full notes: [docs/always_on_desktop.md](always_on_desktop.md).

**workhorse1 (until always-on is configured):** start from RDP/console, or after autologon use the at-logon task. Bare SSH registers over HTTP but **EmuHawk/Lua never connects**. Manual:

```bat
D:\re1_rl\fleet\local\prime_check_workhorse1.cmd
D:\re1_rl\fleet\local\start_worker_detached_workhorse1.cmd
```

---

## Health checks

Learner status: `http://192.168.0.229:8765/status`  
Worker warmup: blocks until `GET /weights` succeeds (no disk fallback).

TensorBoard: `logs/tb/<run-name>/` on learner host only.

Metrics JSONL: `logs/training_metrics_<run-name>.jsonl`

---

## Parity with `train_parallel.py`

Distributed workers use the same `make_env()` factory via async actors (training speed, skip chunk, async cutscene skip, capture checkpoints, action masks). Obs dict matches guidebook keys. PPO hyperparams come from `re1_rl.async_fleet.PPO_HYPERPARAMS`.

See `tests/test_distributed_parity.py`.
