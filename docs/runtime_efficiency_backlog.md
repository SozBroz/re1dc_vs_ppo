# Runtime efficiency backlog (left on the table)

**Date:** 2026-08-18  
**Why this exists:** the live-code RAM/CPU sweep shipped only changes that cannot rewrite or reinterpret already-captured Yawn cells (`cell.State`, sidecar, `leg_replay.json`, joypad tape format, SHA fail-closed load). Everything else stays here until we are ready to A/B it.

Shipped in `885443e` (do not redo): actor `Monitor`/`ActionMasker` replaced so children skip Torch; pinned Yawn reset is O(1) after warmup with mtime/text caches; SHA-256 cached by path+mtime+size; `_load_stage` reuses planner/encoder; dead `affordances` encode zeroed; anim history + box obs reuse the post-step snapshot; `FrameRingBuffer` pruned to `KEEP_BEHIND=32`; zlib `memoryview`; env-vectorized MC returns; `np.stack` inference batches; empty aux-target broadcast fill; aux heads skipped on eval inference and reused after the training forward.

**2026-08-26 (local, restart pending):** planner-loyal WH2 launcher `max_pending_steps=160000`, `min_host_free_gb=16`; learner `/rollout` preflight via `peek_rollout_timesteps` (skip decode on `capacity_full`); pack-path copies reduced (`obs_preprocess` CHW skip, single-segment concat fast path, frame `ascontiguousarray` only when needed, merge optional targets without broadcast copy).

Do **not** change Lua tape packing, savestate load, or hash comparison semantics to chase any item below.

---

## Hold — can change captured-leg timing or playback

These are the largest remaining emulator wins. They stay parked until we can prove old tapes still play and new captures still match kill / dry-fire / death-abort / skip-frame accounting.

| Item | Where | Expected win | Gate |
|------|--------|--------------|------|
| Batch RAM-gated combat macros (`bridge.step(n=1)` per frame) | `knife_macro.py`, `attack_macro.py` | 10–100× fewer socket RTTs on attack actions | A/B kill, dry-fire, facing restore, death abort, and `replay_leg` vs incumbent tapes |
| Batch `read_knife_hooks` inside knife loops | `knife_macro.py` | Many 3-field RTTs per swing | Same combat/replay parity |
| Fewer attack-pin MMF screenshots | `frame_ring.py` `AttackFramePins` | 2–4 captures per combat step | Obs pin channels stay entry/windup/swing/end |
| Event-driven cutscene skip (drop 3 ms poll) | `env.py` `_bg_skip_worker` | Idle CPU + lock fights across ~56 actors | `skip_frames` on new tapes stay honest; old tapes still replay |
| Trust Lua `fast_forward` without extra Python HP polls | `ram_skip.py` | 1–2 RTTs per 600-frame chunk | Death-abort during skip unchanged |
| Lua HP double-read fold | `lua/re1_client.lua` `step` / `fast_forward` | Emulator-local CPU only | Same abort-on-zero-HP |

---

## Next cheap CPU / bridge wins (replay-neutral if masks stay identical)

| Item | Where | Expected win | Notes |
|------|--------|--------------|------|
| Build routine `action_masks()` from the post-step state | `env.py` | 4–7 RTTs per decision; combat can double that | Fresh fail-closed read only before irreversible macros |
| Drop the combat `_execution_action_legal` remask when the pre-step mask is still live | `env.py` | One extra mask fan-out | Fail closed if UI/anim can change mid-macro |
| Cache `_skip_poll_ram` / `_probe_episode_failure` on the step boundary | `env.py` | 2–5 RTTs on menu/skip paths | Snapshot timing must still see death/menu |
| Room-aware enemy table (6 slots, 16 in Yawn) | `memory_map.py` | ~67% of enemy JSON is unused outside Yawn | Decode already uses 6; the read still fetches 16 |
| Compact `read_block` (hex/base64 instead of JSON int array) | `lua/re1_client.lua`, `item_box.py` | Smaller box/inventory RTTs | Parity test only |
| Cached RAM schemas + piggyback final state on `step` | `bizhawk_bridge.py`, Lua | Prototype: 95% protocol bytes | Does not touch tapes; still a protocol change |
| Persistent MMF handle | `bizhawk_bridge.py` | Open/read/close 35.5 → 3.1 µs | Pixel-identical captures |
| Default-off `ATTACK_LOG` / `KNIFE_ANIM_LOG` / `KNIFE_BUDGET_LOG` | `attack_telemetry.py`, fleet env | Console/log I/O on fight rooms | Fleet `.cmd` / `.env` only |

---

## Learner / IPC (no tape format change)

| Item | Where | Expected win | Notes |
|------|--------|--------------|------|
| Reuse extractor features for grouped entropy | `combat_ppo.py` | Third full CNN+tower pass when `RE1_USE_GROUPED_ENTROPY=1` | Flag is off on current fleet |
| Vectorize `combat_auxiliary_loss` (one GPU upload) | `combat_ppo.py` | Host sync + per-row CUDA allocs | Training-only |
| Emit real async combat/world targets, or disable aux until they exist | `async_fleet.py`, `learner_train.py` | Stops supervising all-zero world events with active masks | Correctness first |
| Sparse `episode_infos`; strip `bundle_b64` after successful immediate ingest | `training_progress.py`, yawn ingest | Multi-MB capture payloads in rollout JSON | Keep hashes; replay files themselves are untouched |
| Cache Go-Explore budget + manifest index; apply 600-step cooldown first | `go_explore_capture.py` | ~630 µs and two file reads per capture-enabled step | GE HTTP sync is off; capture path still live |
| In-memory learner Yawn manifest; serve prebuilt bundle zips | `yawn_rails_sync.py` | SHA + ZIP under lock on every worker poll | Version / mtime invalidation |
| Key worker `slot_matches_content` by manifest version + row hashes | `yawn_rails_worker_cache.py` | ~70 full-file hashes per poll even on hits | mtime cache already helps the hash function |
| Rate-limit pin march / parse pin once per reset | `yawn_pin_march.py` | Extra JSON on every reset | March already minute-scale |
| ~~Drop `_emit_rollout` `.copy()` / pack-while-retained / merge slabs~~ (partial: single-segment concat, optional-target merge) | `async_fleet.py`, `packed_train.py`, `learner_train.py` | Multi-GiB transient per 100k-step train | Ownership across pipe send is the hard part |
| ~~Store CHW once (or transpose once at pack)~~ | `obs_preprocess.py` | Extra 2.12 GiB frame slab at 100k steps | Env space stays HWC today |
| Shared-memory actor obs slots | `async_fleet.py` | Avoid pickling 27,904 B/decision | Inference must not read while actor overwrites |
| ~~Preflight cohort admission before zlib/npz decode~~ | `learner_server.py` | Skip expand of `capacity_full` POSTs | Header or tiny preflight |
| Compact rollout-only dtypes (`uint8` flags, `float16` world_state) | `rollout_codec.py` | ~10% of obs storage | Cast at PPO buffer fill; do not change env/model spaces |
| Binary weight HTTP (`application/octet-stream`) | `learner_server.py` | Drop base64 33% + copies | Version header required |
| Learner train-snapshot vs inbound queue overlap | `distributed_train_parallel.py` | Peak RSS during train | Cap or pause admission |
| Pin-memory / preallocated inference numpy buffers | `inference_policy.py` | PCIe sync per step | Hardware-dependent |

---

## Encoder micro-opts (obs semantics must stay bit-identical)

Safe only with a frozen-state byte compare. Do **not** drop `goal`, `world_state`, `spatial`, `logistics`, or history.

- Incremental `encode_world_state` masks when room / `ever_held` / inventory are unchanged (~263 µs/step today)
- Index spatial exits by `from_room` instead of scanning the full door table
- Cache `encode_logistics` / `encode_goal` BFS on `(waypoint_index, inventory, room)`
- Dense `rooms_visited` bitvector updated on first visit only
- Preallocated episode-history planes instead of `list(deque)` each step
- Share one `RoomGraph` / `room_enemies.json` across env, catalog, and affordances
- Skip `dict(state)` in `_build_obs`; pass gallery flags as args

---

## Do not bother yet

Direct grayscale PNG decode was not faster. Reduced-resolution decode changed pixels for ~26 µs. Buffered/custom chunked bridge parsers lost to the current decoder. Cython/Numba will not remove socket RTTs, PNG work, rollout copies, or duplicated Torch graphs.

---

## Suggested next slice

1. Action-mask + probe snapshot from `_read_state` (parity tests on masks).  
2. Sparse `episode_infos` / drop `bundle_b64` after ingest.  
3. Learner pack/transpose copies + real async aux targets.  
4. Combat batching only after `replay_leg` A/B against current cells.
