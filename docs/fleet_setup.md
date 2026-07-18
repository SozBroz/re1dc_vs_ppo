# Distributed fleet — three-machine layout

**Repo:** `https://github.com/SozBroz/re1dc_vs_ppo.git`

| Machine | Host | Role | Script |
|---------|------|------|--------|
| **workhorse2** | `192.168.0.116` | **Learner** — PPO train, checkpoints, HTTP weights | `C:\Users\sshuser\re1_rl` |
| **workhorse1** | `192.168.0.203` | Remote **worker** — BizHawk rollouts only | `D:\re1_rl` |
| **pking** (dev) | local | Remote **worker** | `D:\re1_rl` |

Workers **never** load policy from disk; they pull weights from the learner at warmup and hot-sync after each train step.

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
python scripts/distributed_train_parallel.py --role learner --machine-name workhorse2 ^
  --run-name reward_tune_1040k --resume auto
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
| workhorse2 (learner + local worker) | 5555 | **27** | ~32 GB RAM — keep headroom for epoch ingest spike |
| workhorse1 | 5655 | **8** | **8 CPU threads** — launch from **RDP/console only** |
| pking | 5755 | **20** | **~48 GB RAM** (~900 MB/EmuHawk) |

**BizHawk visibility (fleet default):** only **pking** runs `--no-headless` with `--tile-windows` (4×3 grid) for savestate/screenshot/debug. WH2 learner + WH1 worker use `--headless`.

Weight sync / experience: **6-minute epochs**. Remotes buffer rollouts, then once per `--sync-interval-s` (default **360**) upload a burst and pull weights. Learner **waits for all live workers** (heartbeat registry) to contribute that epoch, with `--epoch-grace-s` (default 120) so a dead box cannot stall forever. Remotes heartbeat every ~30s; no heartbeat for `--worker-liveness-s` (default 90) drops them from the expected set (pking can leave/rejoin freely). Gentler large-batch hyperparams (`DISTRIBUTED_EPOCH_HYPERPARAMS`). `max_staleness` default **1**.

**WH2 RAM budget (~32 GB):** 27 local EmuHawks + learner/Python + **epoch ingest spike** must stay off the pagefile. Do not raise WH2 `--n-envs` without measuring free RAM at flush.

---

## Static LAN addresses

DHCP drift broke fleet wiring when workhorse1 moved `.160` → `.203` and workhorse2 `.111` → `.116`. Canonical addresses live in `fleet/fleet_hosts.cmd` and `fleet/fleet_hosts.json`.

| Machine | Static IP |
|---------|-----------|
| workhorse1 | `192.168.0.203` |
| workhorse2 | `192.168.0.116` |

**Pin once per box** (elevated PowerShell on that machine):

```powershell
cd D:\re1_rl   # or C:\Users\sshuser\re1_rl on WH2
powershell -ExecutionPolicy Bypass -File tools\set_fleet_static_ip.ps1 -Role workhorse1
powershell -ExecutionPolicy Bypass -File tools\set_fleet_static_ip.ps1 -Role workhorse2
```

Idempotent — skips if already static. Also set **DHCP reservations** on the router (`192.168.0.1`) for the machines' MAC addresses as a belt-and-suspenders backup.

Adjust if a box runs monolithic `train_parallel` instead of distributed worker.

---

## Launch commands

**workhorse2 — learner + local fleet:**

```powershell
cd D:\re1_rl
.\fleet\local\run_distributed_learner.cmd
```

**workhorse1 / pking — remote workers:**

```powershell
cd D:\re1_rl
set LEARNER_HOST=%FLEET_LEARNER_HOST%
.\fleet\local\run_distributed_worker.cmd          # WH1 headless
.\fleet\local\run_distributed_worker_pking.cmd    # pking visible 4x3 grid
```

Or edit `fleet/local/run_distributed_worker.cmd` and set `MACHINE_NAME`.

**workhorse1 / workhorse2 headless desktop:** BizHawk needs an always-on interactive console session (not SSH Session 0). Configure once per box:

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_always_on_desktop.ps1 -Role worker   # WH1
powershell -ExecutionPolicy Bypass -File tools\setup_always_on_desktop.ps1 -Role learner  # WH2
```

Then reboot. Prefer an HDMI dummy plug if no monitor. Full notes: [docs/always_on_desktop.md](always_on_desktop.md).

**workhorse1 (until always-on is configured):** start from RDP/console, or after autologon use the at-logon task. Bare SSH registers over HTTP but **EmuHawk/Lua never connects**. Manual:

```bat
D:\re1_rl\fleet\local\prime_check_workhorse1.cmd
D:\re1_rl\fleet\local\start_worker_detached_workhorse1.cmd
```

---

## Health checks

Learner status: `http://192.168.0.116:8765/status`  
Worker warmup: blocks until `GET /weights` succeeds (no disk fallback).

TensorBoard: `logs/tb/<run-name>/` on learner host only.

Metrics JSONL: `logs/training_metrics_<run-name>.jsonl`

---

## Parity with `train_parallel.py`

Distributed workers use the same `make_env()` factory via async actors (training speed, skip chunk, async cutscene skip, capture checkpoints, action masks). Obs dict matches guidebook keys. PPO hyperparams come from `re1_rl.async_fleet.PPO_HYPERPARAMS`.

See `tests/test_distributed_parity.py`.
