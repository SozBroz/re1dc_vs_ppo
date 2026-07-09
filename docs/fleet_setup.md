# Distributed fleet — three-machine layout

**Repo:** `https://github.com/SozBroz/re1dc_vs_ppo.git`

| Machine | Host | Role | Script |
|---------|------|------|--------|
| **workhorse2** | `192.168.0.111` | **Learner** — PPO train, checkpoints, HTTP weights | `C:\Users\sshuser\re1_rl` (no D: drive) |
| **workhorse1** | `192.168.0.160` | Remote **worker** — BizHawk rollouts only | `D:\re1_rl` |
| **pking** (dev) | local | Remote **worker** | `D:\re1_rl` |

Workers **never** load policy from disk; they pull weights from the learner at warmup and hot-sync after each train step.

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
| workhorse2 (learner) | 5555 | — | GPU train bursts; `--no-local-worker` |
| workhorse2 (local worker) | 5555 | 16 | ~32 GB RAM, 28 threads |
| workhorse1 | 5655 | 8 | **8 CPU threads** (~90% target) |
| pking | 5755 | 12 | **~48 GB RAM** (~900 MB/EmuHawk) |

Weight sync: workers poll every **360 s** (`--weight-sync-poll-s`) and sync at each rollout boundary. Learner trains when **20480** queued steps (~6+ rollouts).

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
set LEARNER_HOST=192.168.0.111
.\fleet\local\run_distributed_worker.cmd
```

Or edit `fleet/local/run_distributed_worker.cmd` and set `MACHINE_NAME`.

---

## Health checks

Learner status: `http://192.168.0.111:8765/status`  
Worker warmup: blocks until `GET /weights` succeeds (no disk fallback).

TensorBoard: `logs/tb/<run-name>/` on learner host only.

Metrics JSONL: `logs/training_metrics_<run-name>.jsonl`

---

## Parity with `train_parallel.py`

Distributed workers use the same `make_env()` factory (training speed, skip chunk, async cutscene skip, capture checkpoints). Obs dict matches guidebook keys (`inventory`, `history`, `acquisitions`, `room_enemies`, `keys_held`). PPO hyperparams come from `re1_rl.async_fleet.PPO_HYPERPARAMS`.

See `tests/test_distributed_parity.py`.
