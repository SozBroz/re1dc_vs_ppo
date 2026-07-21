# Distributed PPO — learner / worker architecture

How we scale RE1 PPO training across multiple Windows PCs without fleet-wide step barriers, without per-action RPC, and without workers ever loading policy weights from local disk.

**Status:** implemented. Entry point: `scripts/distributed_train_parallel.py`. Monolithic training remains in `scripts/train_parallel.py`.

---

## Roles

| Role | Count | Holds full PPO? | Runs BizHawk? | Policy source |
|------|-------|-----------------|---------------|---------------|
| **Learner host** | 1 (fixed machine, GPU preferred) | Yes — policy, value, optimizer | **Yes** — local worker fleet grinds envs like any other machine | Canonical PPO in RAM on that machine |
| **Remote worker** | 0–N (any other PC, join/leave anytime) | No — **inference-only** mirror | Yes — `n_envs` EmuHawk instances | **Always** from learner over the network |
| **Local worker** | 1 (co-located on learner host) | No — inference mirror only | Yes — `n_envs` EmuHawk on same box | **In-process** from learner RAM (no disk, no HTTP loopback) |

Naming:

- **Learner** — the process on the learner host that holds PPO in memory, runs `train()`, and publishes weights.
- **Worker** — any machine (including the learner host) that farms rollouts. Workers are not peer trainers.
- **Learner host** — always runs **both** the learner process **and** a local worker fleet. It is not inference-only; it grinds agents the same way remote workers do.

A remote worker may run anywhere from 0 envs (offline) to a full fleet (e.g. 12) without affecting correctness. A desktop used for miscellaneous work can start and stop its worker process arbitrarily. The learner host’s local worker is expected to stay up whenever training runs.

---

## Design goals

1. **One policy** — single optimizer, on-policy PPO updates on merged experience.
2. **No fleet barrier** — workers do not wait for each other. Each machine collects rollouts on its own schedule.
3. **Local inference** — workers never RPC per env step. They `predict()` locally, then upload whole rollout batches.
4. **Hot weight sync** — weights move as in-memory bytes (`state_dict`), not periodic full checkpoint reload from zip on workers.
5. **Workers never read policy from local disk** — warmup and every resync pull from the learner. Game assets (ROM, savestates, curriculum JSON) remain local on each worker; only **neural weights** are learner-sourced.
6. **Optional workers** — learner trains whenever it has at least one complete rollout; missing workers do not stall training.

---

## High-level topology

```
┌──────────────────────────────────────────────────────────────────┐
│                        LEARNER HOST                              │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Learner process                                           │  │
│  │  PPO (MultiInputPolicy) in RAM                             │  │
│  │  rollout queue ← local + remote                            │  │
│  │  train() → policy_version++ → publish weights              │  │
│  │  checkpoints to disk (learner host only)                   │  │
│  └───────────────────────────┬────────────────────────────────┘  │
│                              │                                     │
│         in-process rollouts  │  hot weight share (no disk, no HTTP)│
│                              ▼                                     │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Local worker (same machine)                               │  │
│  │  12 × EmuHawk · inference mirror · grinds rollouts         │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────┘
                               │
              weights (pull)     │     rollouts (push)
              policy_version   │     tagged policy_version
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
         ▼                     ▼                     ▼
  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
  │  Worker B    │      │  Worker C    │      │  (optional)  │
  │  12 × EmuHawk│      │  4 × EmuHawk │      │  more hosts  │
  │  remote      │      │  desktop     │      │              │
  │  on/off      │      │  on/off      │      │              │
  └──────────────┘      └──────────────┘      └──────────────┘
```

Ethernet (or Samba for **game files only**). Policy bytes never come from Samba or worker `data/checkpoints/`. The learner host’s local worker never reads policy from disk either — it shares the learner’s in-memory weights directly.

---

## What stays on each machine

### Learner host (learner process + local worker)

**Learner process**

- Full Stable-Baselines3 `PPO` instance (`MultiInputPolicy`, see `re1_rl/policy_config.py`).
- Rollout ingestion queue (from local worker + all remote workers).
- TensorBoard, atomic checkpoints (`re1_rl/checkpoint_io.py`) — **only the learner host** writes `ppo_re1_*.zip`.

**Local worker (same machine, separate process or thread pool)**

- BizHawk + `lua/re1_client.lua` + `RE1Env` per env — same factory as `make_env()` in `train_parallel.py`.
- **Inference-only** policy mirror, fed by **in-process** weight handoff from the learner (shared `state_dict` buffer or direct `load_state_dict` under lock after each `train()`).
- Does **not** use `GET /weights` over loopback; does **not** read checkpoint zip from disk.
- Enqueues rollouts into the learner queue locally (no network hop for rollout upload).

The learner host is expected to contribute a full env fleet (e.g. 12 agents) whenever training is running. GPU on this machine serves both `train()` and local batched `predict()`.

### Remote worker

- BizHawk + `lua/re1_client.lua` + `RE1Env` per env (same as `make_env()` in `train_parallel.py`).
- **Inference-only** policy: same architecture as learner (`POLICY_KWARGS`), `eval()` mode, no optimizer.
- Two threads per worker process (minimum):
  - **Rollout thread** — `SubprocVecEnv` or equivalent; steps envs; fills rollout buffer.
  - **Network thread** — warmup weight fetch from learner host, periodic weight pull, rollout upload, version checks.
- Local disk: ROM, savestates, curriculum, screenshots — **not** policy checkpoints for training.

**Hard rule (all workers, local and remote):** must not call `PPO.load()`, `torch.load()` on a checkpoint zip, or read `data/ppo_re1_final.zip` / `data/checkpoints/*.zip` for policy weights. Remote workers block or exit if the learner is unreachable at warmup; they do not fall back to disk. The local worker on the learner host gets weights only from the learner process in RAM.

---

## Warmup sequence (worker)

Every **remote** worker start, including after an arbitrary stop/restart:

1. **Connect** to learner host (`--learner-host`, `--learner-port`).
2. **`GET /weights`** (or equivalent RPC) — receive:
   - `policy_version` (int, monotonic)
   - `policy_bytes` — serialized `state_dict` (e.g. `torch.save` to bytes, or raw ordered tensors)
   - optional metadata: `obs_space` hash, `policy_class`, param count (sanity check)
3. **Build inference module** — construct `MultiInputPolicy` with `POLICY_KWARGS`, `load_state_dict`, `eval()`, move to worker device (CUDA if available, else CPU).
4. **Launch EmuHawk fleet** — staggered ports `base_port + rank` (unchanged from today).
5. **Register** (optional): `POST /register` with `{ worker_id, n_envs, hostname }` for logging only; not required for training.
6. **Enter rollout loop** — only after step 3 succeeds.

No env may call `policy.predict` until weights are loaded from the learner.

**Local worker on learner host:** skip steps 1–2 over the network. On start, block until the learner process has published an initial `policy_version` and hands off the first `state_dict` in-process (shared memory, queue, or locked reference). Still no disk read.

---

## Steady-state loop

### Worker (per machine, async)

Applies to **local and remote** workers. Remote workers upload over the network; the learner host’s local worker pushes to the in-process queue.

```
loop forever:
    collect n_steps × n_envs locally using current inference policy
    tag rollout with policy_version used at collection start
    deliver rollout to learner (in-process queue OR POST /rollout)
    in parallel (background):
        if policy_version on learner > local:
            sync weights (in-process handoff OR GET /weights → hot-swap)
```

Workers **never** wait for other workers between steps. Slow cutscenes on one PC do not block others.

### Learner

```
loop forever:
    wait for any complete rollout on queue (local worker + remotes; timeout OK)
    reject rollouts where rollout.policy_version < current - max_staleness
    append to rollout buffer (or train immediately per batch — see Training batching)
    when enough timesteps accumulated:
        PPO.train() on merged buffer
        policy_version += 1
        publish new weights to local worker (in-process) + remote workers (network)
        optional: atomic checkpoint to disk (learner host only)
```

---

## `policy_version` and staleness

Every weight blob and rollout carries `policy_version`.

| Event | Rule |
|-------|------|
| Worker collects rollout | Stamp with `policy_version` at **start** of collection (or per-step if we need finer correction later). |
| Worker uploads rollout | Learner accepts iff `version >= current - K` (small `K`, e.g. 0–1). |
| Worker offline across updates | On reconnect, warmup fetch gets latest version; first rollout after sync is valid. |
| Mid-rollout learner updates | Acceptable: entire rollout tagged with version at collection start; minor off-policy lag within one rollout. |
| Worker hard-killed mid-upload | Drop incomplete rollout; no learner corruption. |

Rejected rollouts are discarded (worker may retry after pulling fresh weights). This is cheaper than corrupting PPO with mismatched logprobs.

---

## Weight sync (hot, no worker disk)

### Format

- Serialize **policy parameters only** (and optionally value head if shared extractor — SB3 `MultiInputPolicy` shares features extractor; ship full `policy` + `policy.optimizer` is **not** needed on workers).
- Preferred: `torch.save(model.policy.state_dict(), BytesIO)` or learner-native tensor list + shapes.
- Size ballpark: ~10–30 MB (≈2.1M params at fp32). Gigabit LAN: sub-second.

### Transport

- **Local worker (learner host):** in-process `state_dict` handoff after each `train()`; no TCP, no disk.
- **Remote workers — pull (required):** `GET /weights?min_version=N` during warmup and when local version lags.
- **Remote workers — push (optional):** learner notifies connected workers after `train()`; workers pull if `advertised_version > local`.
- **No zip files on any worker** — no `PPO.load` path on worker processes.

### Hot-swap on worker

1. Receive bytes or in-process `state_dict` (network thread or learner callback).
2. Deserialize if needed.
3. `inference_policy.load_state_dict(state_dict, strict=True)` under a lock.
4. Rollout thread takes lock only for `predict` (microseconds); emulation never blocks on network.

---

## Rollout payload

Workers upload **one batch per local collection** (`n_steps` per env, same hyperparams as learner).

Aligned with current monolithic config (`scripts/train_parallel.py`):

| Hyperparameter | Value |
|----------------|-------|
| `n_steps` | 256 per env |
| `n_envs` | worker-local (e.g. 12, or 4 on desktop) |
| Steps per upload | `256 × n_envs` (e.g. 3072 for 12 envs) |

### Tensors per rollout (per env, length `n_steps`)

| Field | Shape / type | Notes |
|-------|----------------|-------|
| `obs` | dict of arrays | Keys: `frame`, `proprio`, `goal`, `spatial`, `visited` (see `RE1Env`) |
| `actions` | `(n_steps,)` int64 | discrete 0–9 |
| `rewards` | `(n_steps,)` float32 | |
| `dones` | `(n_steps,)` bool | terminated \| truncated |
| `values` | `(n_steps,)` float32 | from worker inference at collect time |
| `log_probs` | `(n_steps,)` float32 | from worker inference at collect time |
| `episode_infos` | list | room, waypoint, reward_breakdown — for logging |

### Compression

Dominant size is `frame` (84×84×4 uint8 × `n_steps` × `n_envs`).

| Strategy | Rough size (12 envs × 256 steps) |
|----------|----------------------------------|
| Raw uint8 | ~170 MB |
| JPEG per frame | ~15–25 MB |
| uint8 + zlib on stack | ~40–80 MB |

**v1 recommendation:** JPEG per frame column or lz4 on raw frames. Target **≤25 MB** per worker per rollout on wire (matches prior bandwidth estimates).

Learner decompresses before `train()`.

---

## Training batching (learner)

Two viable modes (pick one at implementation):

### A. Micro-batch queue (simpler v1)

- Each worker rollout is one training example bundle.
- Learner runs `train()` when `sum(timesteps in queue) >= batch_threshold` (e.g. ≥3072, or ≥2 worker rollouts).
- Closest to current SB3 `n_steps` × `n_envs` semantics scaled fleet-wide.

### B. Fixed global rollout (stricter on-policy)

- Learner waits until `sum(timesteps) >= n_steps × target_envs` but **does not** wait for a specific worker — any combination of workers that sums to the threshold triggers update.

Both avoid “all machines step in lockstep.” Mode A is easier for arbitrary worker count.

Hyperparameters stay learner-owned (same as today):

```python
n_steps=256          # per-env horizon in each worker rollout
batch_size=512
n_epochs=4
learning_rate=3e-4
gamma=0.998188  # RL_GAMMA; ~45s half-life with step contempt (c=-0.00024)
ent_coef=0.02
```

---

## Network protocol (sketch)

HTTP/1.1 or a single TCP JSON+length framing (reuse `bizhawk_bridge` style) — implementation choice. Minimum surface:

| Method | Path | Direction | Body |
|--------|------|-----------|------|
| `GET` | `/health` | worker → learner | — |
| `GET` | `/weights` | worker → learner | Response: `{ policy_version, policy_bytes, obs_hash }` |
| `POST` | `/rollout` | worker → learner | `{ worker_id, policy_version, n_envs, n_steps, payload }` |
| `POST` | `/register` | worker → learner | optional metadata |
| `GET` | `/status` | ops | queue depth, version, connected workers |

Learner may add `GET /weights/version` (int only) for cheap polling.

All policy bytes flow **learner → worker** only.

---

## Checkpoints and recovery

| Artifact | Who writes | Who reads |
|----------|------------|-----------|
| `data/checkpoints/ppo_re1_*.zip` | Learner only | Learner only (resume after crash) |
| `data/ppo_re1_final.zip` | Learner only | Learner only |
| `latest.json` pointer | Learner only | Learner only |

Worker crash recovery: restart worker → warmup `GET /weights` → continue. No checkpoint path on worker.

Learner crash recovery: restart learner → `resolve_resume_path()` from disk (existing `checkpoint_io`) → workers block on `/weights` until learner is back → workers hot-sync to restored version.

---

## Arbitrary worker lifecycle (desktop scenario)

```
Desktop off / gaming     → 0 workers, learner trains on other machines
Desktop starts worker    → warmup fetch, 4 envs, uploads rollouts
Desktop Ctrl+C           → TCP closes, in-flight rollout dropped
Desktop restarts later   → warmup fetch latest version, no disk policy read
```

Learner never depends on a fixed worker set. TensorBoard / progress metrics should label contributions by `worker_id`, not assume uniform `n_envs`.

---

## Relationship to monolithic `train_parallel.py`

| Today | Distributed |
|-------|-------------|
| Main process: PPO + `SubprocVecEnv` on one machine | Learner host: learner process + local worker (`SubprocVecEnv`) |
| Subprocess: EmuHawk + `RE1Env` | Same on learner host and on remote workers |
| `model.predict` in main on batched obs | Local worker: same; remote: `predict` on worker, batched locally |
| One rollout then `train()` | Queue rollouts from local + remote workers, then `train()` |
| Sticky input, async cutscene skip | Unchanged on all workers |

---

## Planned entrypoints (implementation)

| Script | Role |
|--------|------|
| `distributed_train_parallel.py`.  It will build off of train_parallel.py with all the same arguments.  It will have additional arguments for whether it is a worker or learner.  It will support single machine setups where its a worker and the learner, or just a worker.  This same python script will be ran on every machine with its respective arguments.  Have logging include the machines name, as provided as an argument to this script, to be logged alongside whatever else is being logged.  Also with a human readable EST timestamp.

---

## Failure modes

| Failure | Behavior |
|---------|----------|
| Learner down | Remote workers retry `/weights`; do not read disk; exit after timeout if configured. Local worker on learner host stops with learner. |
| Remote worker down | Learner continues with local worker + other remotes |
| Stale rollout uploaded | Learner rejects; worker pulls new weights |
| Partial rollout upload | Learner discards incomplete message |
| Version skew after learner resume from old checkpoint | `policy_version` reset on learner must bump; workers detect `local > remote` and force resync |
| Network partition | Worker keeps collecting with **stale** policy until partition heals or max staleness exceeded; then pause collect until fresh weights |

---

## Security / ops notes

- Bind learner to LAN (`192.168.x.x`), not `0.0.0.0` on the public internet.
- No authentication in v1 (trusted home LAN). Add token header if needed later.
- Workers need firewall allowance **outbound** to learner port.

---

## Open decisions (implementation time)

1. **Rollout codec:** MessagePack + JPEG vs custom binary.
2. **Push vs pull-only** for weight notifications.
3. **Max staleness `K`** for accepted rollouts (0 vs 1).
4. **Importance sampling** if lag grows (V-trace) — defer until needed.
5. **Whether learner runs a single eval env** for human monitoring — optional, separate from workers.

---

## Summary

- **Learner host** = one machine running **both** the learner (PPO in RAM, sole trainer, sole checkpoint writer) **and** a local worker fleet that grinds rollouts like any remote box.
- **Remote worker** = BizHawk fleet + inference mirror; always loads weights from learner host at warmup and hot-syncs thereafter; never reads policy from its own disk.
- **Local worker** = same grinding role on the learner host; weights via in-process handoff only (no disk, no HTTP loopback).
- Workers join and leave freely (except the learner host local worker, which stays up during training); no fleet-wide step sync; rollouts are async and version-tagged.
- Weight transfer is hot in-memory bytes, not periodic zip reload on workers.

This is the target architecture for scaling beyond one machine without giving up on-policy PPO discipline.
