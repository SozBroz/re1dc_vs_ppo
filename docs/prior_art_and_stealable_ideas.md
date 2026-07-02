# Prior Art & Stealable Ideas — RE1 Jill Any% RL (BizHawk Bridge)

Research scout report for the hierarchical hybrid RE1 project: symbolic 116-room planner + BC-warm-started PPO navigator + scripted puzzle macros, targeting Jill Any% on PS1 Director's Cut (SLUS-00551) via BizHawk Lua↔Python socket bridge.

**Scope:** 22 distinct projects surveyed across 7 categories. Primary sources preferred; repos verified where linked.

---

## Executive summary — top 10 stealable ideas (ranked)

1. **Room-ID + coordinate novelty reward** (PokemonRedExperiments v2 / pokerl): replace pixel-KNN exploration with `(room_id, tile)` visitation counts and a local visited-mask channel — maps directly onto RE1's fixed-camera rooms.
2. **Go-Explore-style savestate archive per room** (uber-research/go-explore): load savestate → explore locally → archive best trajectory to next waypoint; BizHawk makes this trivial and is the single biggest fix for sparse-reward horror navigation.
3. **BC warm-start → PPO fine-tune on short segments** (VPT, BASALT, RE Requiem video): record your own Any% route segments; LSTM/GRU policy copies tank controls before RL touches sparse helipad reward.
4. **Reward scaling + decomposed logging** (retro-baselines `RewardScaler`, Pokemon `state_scores` dict): multiply shaped rewards by ~0.01 for PPO stability; log every component separately to catch exploitation early.
5. **Allow-backtracking / max-progress reward** (Sonic Retro Contest): reward `max(room_progress)` not raw delta-x so fixed-camera backtracking through doors is not punished.
6. **Anti-loop + anti-spam wrappers** (PokeRL): penalize repeated `(room, action)` tuples and menu-button spam — critical for RE1 inventory/dialogue.
7. **Hybrid RAM + downsampled frame stack** (StreetFighter2-BizHawk, SMB RAM-PPO, our memory-hooks doc): train on 16–64 RAM features + 84×84×4 frames; 10–50× faster than pixels-only on a 4070.
8. **Length-prefixed JSON socket + frame-blocked I/O** (BrainHawk, GymBizHawk, bizhawk-luasocket): Python server, Lua client; one request/response per `frame_skip` batch; emulator blocks until Python replies.
9. **Scripted macro layer as "skill library"** (Voyager pattern, RE puzzle design): store password entry, crest placement, inventory combine as verified Lua macros the planner invokes — not learned.
10. **Failure-first video arc + live telemetry overlays** (Whidden Pokemon, RE Requiem BC video): open with relatable failures (door loops, Jill death animations), overlay room graph progress + reward meter + planner intent text.

### Single best steal per axis

| Axis | Best steal | Why |
|------|------------|-----|
| **(a) Training speed** | RAM+frame hybrid obs + `frame_skip=8` + per-room savestate resets | Cuts env step cost and sample complexity vs pixels-only; matches `env.py` scaffold and memory-hooks doc. |
| **(b) Exploration** | Go-Explore cell archive keyed by `(room_id, coarse_tile)` with savestate restore | RE1's combinatorial mansion is Montezuma-shaped; emulator savestates are free deterministic resets. |
| **(c) Video** | Whidden-style "5 years of simulated time" progression montage + pokerl-map-viz-style room visitation heatmap | Proven viral structure; RE1's mansion graph is visually compelling. |

---

## 1. Emulator + RL bridges

### Summary table

| Project | Link | Tag | Transfer confidence |
|---------|------|-----|---------------------|
| BrainHawk | https://github.com/TylerLandowski/BrainHawk | useful-for-code | **High** |
| StreetFighter2-DeepRL-on-Bizhawk | https://github.com/RuochenLiu/StreetFighter2-DeepRL-Model-on-Bizhawk | useful-for-code | **High** |
| GymBizHawk (pocokhc) | https://qiita.com/pocokhc/items/031fbb3e65b0450bd2a3 | useful-for-architecture | **High** |
| bizhawk-luasocket | https://github.com/RemiChavance/bizhawk-luasocket | useful-for-code | **High** |
| BirdsEye | https://github.com/SkiHatDuckie/birds-eye | useful-for-architecture | **Medium** |
| SuperMarioWorld_env | https://github.com/cspace-000/SuperMarioWorld_env | useful-for-code | **Medium** |
| nitrogen-bizhawk-dataset-generator | https://github.com/artryazanov/nitrogen-bizhawk-dataset-generator | useful-for-code | **High** |
| PyBoy (PokemonRedExperiments) | https://github.com/Baekalfen/PyBoy | reference-only | **Low** (wrong emulator; patterns transfer) |
| stable-retro / gym-retro | https://github.com/Farama-Foundation/stable-retro | useful-for-architecture | **Medium** |
| gym-super-mario-bros RAM mode | https://github.com/yumouwei/super-mario-bros-reinforcement-learning | useful-for-code | **High** |
| MarNEO | https://github.com/GameAISchool2022members/MarNEO | reference-only | **N/A** (RAM-writing novelty) |

---

### BrainHawk
**What:** Python TCP server + minimal Lua `BHClient` for screenshots, RAM, controls, reset/exit.

**STEALABLE:**
- **Server-first boot:** Python binds port → Lua connects → explicit `UPDATE` round-trip per frame batch (`bizhawk_bridge.py`).
- **Structured commands:** JSON payloads for `screenshot`, `variables`, `controls`, `restart` — extend with `loadstate` / `savestate` slots.
- **Thin Lua:** game-specific logic stays in one `UserProcessor`-style module; matches our `lua/re1_client.lua` plan.

**Tag:** useful-for-code | **Confidence:** High

---

### StreetFighter2-DeepRL-on-Bizhawk (RuochenLiu)
**What:** Online DQN/PPO with TensorFlow; Lua client sends screenshot + RAM vector; Python returns buttons.

**STEALABLE:**
- **Dual observation:** screenshot (clipboard/base64 PNG) + RAM vector from BizHawk RAM Watch addresses — same split as `env.py` Dict obs.
- **Action space pruning:** discrete combo-free button set; map to our 10-action tank-control set, not full 2^14 PSX bitmask.
- **RAM Watch workflow:** document every address in a table before training — mirrors `memory_map.py` + hooks doc.

**Tag:** useful-for-code | **Confidence:** High

---

### GymBizHawk (pocokhc, Qiita writeup)
**What:** Gym×BizHawk framework: socket server, `BizHawk` Python wrapper, `GymEnv` Lua `UserProcessor`, optional `gym.Env` subclass.

**STEALABLE:**
- **Startup handshake:** server → Lua init (spaces, modes) → Lua replies space info — add to `BizHawkClient.wait_for_client()`.
- **Separate `BizHawk` transport from `RE1Env`:** already mirrored in `bizhawk_bridge.py` vs `env.py`; keep it.
- **Game logic in Lua `UserProcessor`:** RAM read + button apply per step; Python only trains.

*Note:* No standalone public repo found under pocokhc; Qiita article is the primary source.

**Tag:** useful-for-architecture | **Confidence:** High

---

### bizhawk-luasocket (RemiChavance)
**What:** Minimal Windows reference: Lua sends frame counter, Python echoes — proves socket.dll placement for BizHawk 2.9+.

**STEALABLE:**
- **socket.core.dll next to EmuHawk.exe** (not only in Lua folder) — document in project README for BizHawk 2.11.
- **Configurable host/port** in both `Main.py` and `CustomSocket.lua` — we use 5555 in `bizhawk_bridge.py`.

**Tag:** useful-for-code | **Confidence:** High

---

### BirdsEye (SkiHatDuckie)
**What:** C# ExternalTool DLL + `birds-eye-lib` Python; TCP middleware; Manual vs Commandeer input modes.

**STEALABLE:**
- **I/O-blocked stepping:** emulator waits for Python response each frame — same contract as our bridge (BizHawk wiki confirms).
- **Reconnect without restart:** Python process survives BizHawk restart — useful for long training runs.
- **Alternative to raw Lua** if socket.lua becomes painful on 2.11 — fallback architecture only.

**Tag:** useful-for-architecture | **Confidence:** Medium (extra dependency vs pure Lua)

---

### SuperMarioWorld_env
**What:** OpenAI Gym SMW env; BizHawk 2.3.1; paste Lua in console; PPO via Stable Baselines.

**STEALABLE:**
- **Per-level `.state` save files** in working directory for curriculum — one `.state` per RE1 training room/segment.
- **`run.py` wrapper** pattern: launch training script, user loads ROM + Lua manually — matches solo-dev workflow.

**Tag:** useful-for-code | **Confidence:** Medium

---

### nitrogen-bizhawk-dataset-generator
**What:** Export `.bk2` TAS movies to frames + per-frame controller JSONL → Parquet for NitroGen BC.

**STEALABLE:**
- **Record demos from BizHawk movies:** play human Any% segments, export `(frame, buttons, png)` triples for BC — no custom recorder needed.
- **Pair `.mp4`/frames with `.jsonl` actions** — standard BC dataset layout for our Jill route.

**Tag:** useful-for-code | **Confidence:** High

---

### PyBoy (via PokemonRedExperiments)
**What:** Python-native Game Boy emulator with Gymnasium API; headless, fast, no socket.

**STEALABLE:**
- **Headless + turbo:** not available on BizHawk PSX the same way; compensate with `frame_skip`, smaller obs, RAM-heavy policy.
- **Windowed `render()` for video capture** while training headless metrics separately.

**Tag:** reference-only | **Confidence:** Low for PSX; patterns High for env API design

---

### stable-retro (OpenAI Retro)
**What:** Libretro integration; `data.json` / `scenario.json` map RAM variables → reward/done; `Integration` docs for custom games.

**STEALABLE:**
- **Declarative reward from RAM deltas:** `scenario.json` pattern → our `reward.py` YAML/JSON config keyed by `memory_map` symbols.
- **`AllowBacktracking` + `RewardScaler` wrappers** in retro-baselines — port to Gymnasium wrappers on `RE1Env`.
- **Savestate in game data folder** for deterministic resets — analog to BizHawk `.state` per segment.

**Tag:** useful-for-architecture | **Confidence:** Medium (different emulator stack)

---

### gym-super-mario-bros RAM (yumouwei / Kautenja)
**What:** Custom `ObservationWrapper` reads NES tile grid `0x0500–0x069F`, entity positions; stacks frames with skip.

**STEALABLE:**
- **Semantic RAM grid vs raw bytes:** normalize tiles to {empty, solid, actor, enemy} — for RE1 use {floor, wall, door, enemy, item} from room layout + enemy table hooks.
- **Custom frame stack with `(n_skip)` between stack slots** — align with `frame_skip=8` in `env.py`.
- **Why RAM beats pixels for training:** explicit statement in repo README; cite in video when showing 4070 throughput.

**Tag:** useful-for-code | **Confidence:** High

---

### MarNEO
**What:** RL agent writes NES RAM directly instead of buttons — BizHawk gym env.

**STEALABLE (framing only):**
- **Viral hook:** "AI cheats by editing memory" — explicitly **do not** use for RE1 DRL purity rule in memory-hooks doc.
- **Novelty + progress combined reward** — idea transfers to *observation* novelty, not RAM writes.

**Tag:** reference-only / not-applicable for actions | **Confidence:** N/A for controls

---

## 2. Famous "AI plays game" projects

| Project | Link | Tag | Transfer confidence |
|---------|------|-----|---------------------|
| PokemonRedExperiments | https://github.com/PWhiddy/PokemonRedExperiments | useful-for-architecture | **High** |
| pokerl / arxiv 2502.19920 | https://arxiv.org/abs/2502.19920 | useful-for-architecture | **High** |
| pokerl-map-viz | https://github.com/pwhiddy/pokerl-map-viz | useful-for-narrative | **High** |
| MarI/O (SethBling) | https://www.youtube.com/watch?v=qv6UVOQ0F44 | useful-for-narrative | **Medium** |
| Code Bullet | https://www.youtube.com/CodeBullet | useful-for-narrative | **Medium** |
| OpenAI Sonic Retro Contest | https://github.com/openai/retro-baselines | useful-for-architecture | **Medium** |
| DeepMind Capture The Flag (FTW) | https://deepmind.google/blog/capture-the-flag-the-emergence-of-complex-cooperative-agents/ | reference-only | **Low** |
| Voyager | https://github.com/MineDojo/Voyager | useful-for-architecture | **Medium** |
| MineRL BASALT / BEDD | https://github.com/minerllabs/basalt-benchmark | useful-for-architecture | **Medium** |

---

### PokemonRedExperiments (Peter Whidden)
**What:** Viral "AI plays Pokémon Red" — PyBoy env, shaped rewards, exploration, stream map, 7k+ GitHub stars.

**STEALABLE:**
- **Coordinate-based exploration** replacing frame KNN (v2): `seen_coords[f"x:{x} y:{y} m:{map}"]` + penalty after 600 revisits — use `room_id` instead of `map_n`.
- **Visited-mask observation channel** (72×80 local crop) — RE1: per-room 2D mask centered on spawn, fed as extra obs plane.
- **`state_scores` dict** logging every reward term — copy into `reward.py` return `(total, breakdown)`.
- **StreamWrapper → global map viz** for training broadcast — adapt to 116-room graph overlay.
- **Viral narrative:** "starts pressing random buttons" → "5 years simulated game time" → gym leader moment; mirror with "first night in mansion" milestone.

**Tag:** useful-for-architecture + narrative | **Confidence:** High

---

### pokerl (arxiv 2502.19920, neroRL)
**What:** Academic follow-up; <10M param PPO beats much of Red; dense 25+ reward components; ablations on exploitation.

**STEALABLE:**
- **Hyperparameter table:** `reward_scale=4`, `explore_weight=3`, screen explore flag — starting grid for RE1 sweeps.
- **Document reward exploits explicitly** (grind battles, skip story) — RE1 analog: farming hallway zombies, wrong-door loops.
- **Hierarchical / curriculum extensions** (community PR #223): `milestones.py`, `state_store.py`, `scripted_helpers.py` module layout — maps to `planner.py` + puzzle macros.

**Tag:** useful-for-architecture | **Confidence:** High

---

### MarI/O (SethBling)
**What:** NEAT in BizHawk Lua; local tile/sprite grid from RAM around Mario; fitness = max X; 34 generations to clear level.

**STEALABLE:**
- **Egocentric local grid obs** from RAM (`getTile`, `getSprites` in Lua) — RE1: egocentric "danger cone" from enemy slots relative to Jill.
- **Savestate per level** (`DP1.state`) — per-room BizHawk states for curriculum.
- **Video trope:** generation counter + "species" visualization — use for PPO checkpoint generations instead of NEAT.

**Tag:** useful-for-narrative + code (Lua RAM grid) | **Confidence:** Medium (NEAT itself Low for long-horizon RE1)

---

### Code Bullet
**What:** Comedy + education; NEAT/Q-learning; incremental difficulty; batch populations.

**STEALABLE:**
- **Incremental learning:** train navigation in empty room → one zombie → full room — matches curriculum stages.
- **Batch learning:** 750 agents evaluated in groups of 50 — on 4070, parallel envs are env instances with separate savestates, not population NEAT.
- **Tone:** self-deprecating failure ("pushing my computer too hard") — fits solo-dev honesty.

**Tag:** useful-for-narrative | **Confidence:** Medium

---

### OpenAI Gym Retro Contest (Sonic)
**What:** PPO/DQN on 58 Sonic levels; horizontal progress reward; winners used backtracking + human trajectory rewards.

**STEALABLE:**
- **`AllowBacktracking`:** reward = `max(0, max_x - prev_max_x)` — for RE1: `max(0, waypoint_index_progress - prev)`.
- **`RewardScaler(0.01)`** — apply in `reward.py` before PPO.
- **Human trajectory waypoint reward** (AurelianTactics) — steal route: reward progress along `route_jill_anypct.json` ordered room list, not Euclidean position.
- **Transfer learning across levels** — train on mansion 1F subset, fine-tune on basement.

**Tag:** useful-for-architecture | **Confidence:** High for reward; Medium for transfer

---

### DeepMind Capture The Flag (FTW)
**What:** Population-based training; learned intrinsic rewards; two-timescale RNN; pixel-only Quake III CTF.

**STEALABLE:**
- **Two-timescale memory:** fast LSTM for combat reflexes, slow for room-to-room intent — consider stacked LSTM or Transformer-XL chunk for fixed-camera context switches.
- **Population-based hyperparam evolution** — overkill for solo 4070; **skip** full PBT, but steal **dual value heads** (extrinsic shaping + intrinsic novelty) from RND paper overlap.

**Tag:** reference-only | **Confidence:** Low (compute budget)

---

### Voyager (MineDojo)
**What:** LLM automatic curriculum + executable skill library + iterative code repair.

**STEALABLE:**
- **Skill library as code macros:** each verified puzzle script (shield combo, V-JOLT, crests) is a named skill with pre/post conditions — planner retrieves by room_id embedding.
- **Automatic curriculum from "what's unfinished":** `planner.py` next waypoint = curriculum task; no GPT needed.
- **Self-verification before adding skill:** macro success = RAM flag check (door open, item acquired).

**Tag:** useful-for-architecture | **Confidence:** Medium (LLM parts optional; library pattern High)

---

### MineRL BASALT / BEDD
**What:** 26M frame-action pairs; fuzzy tasks; BC on VPT embeddings; `imitation` library.

**STEALABLE:**
- **BC on foundation embeddings, not raw pixels:** small CNN on our 84×84 stack → 256-d embedding → BC head; then unfreeze for PPO.
- **Remove no-op actions from demo dataset** — RE1: collapse "run into wall" duplicates.
- **Human eval pairwise comparison** for video: show human Any% vs agent side-by-side clips.

**Tag:** useful-for-architecture | **Confidence:** Medium

---

## 3. Sparse-reward / long-horizon / navigation RL

| Project | Link | Tag | Transfer confidence |
|---------|------|-----|---------------------|
| Go-Explore | https://github.com/uber-research/go-explore | useful-for-architecture | **High** |
| RND | https://arxiv.org/abs/1810.12894 | useful-for-architecture | **Medium** |
| h-DQN | https://proceedings.neurips.cc/paper/2016/file/f442d33fa06832082290ad8544a8da27-Paper.pdf | reference-only | **Low** |
| HER | (concept) | useful-for-architecture | **Medium** |

---

### Go-Explore (Uber / Nature)
**What:** Archive of cells + trajectories; return without exploration noise; then explore; optional robustification imitation phase.

**STEALABLE:**
- **Cell = `(room_id, discretized_x, discretized_z)`** or room-only for coarse archive — store BizHawk `.state` per cell.
- **Selection weight:** prefer cells with high score, few visits, or frontier of `route_jill_anypct.json`.
- **Return step:** `savestate.load(slot)` + replay action prefix from archive — no learned goal policy needed in phase 1.
- **Phase 2 robustification:** BC on archive trajectories → PPO fine-tune with stochastic enemy RNG.

**Tag:** useful-for-architecture | **Confidence:** High (BizHawk is ideal restorable simulator)

---

### RND (Random Network Distillation)
**What:** Intrinsic reward = prediction error of fixed random net on obs features; dual PPO value heads; obs normalize clip [-5,5].

**STEALABLE:**
- **Intrinsic bonus on visual embedding only** (not RAM) to avoid "novelty farming" via menu screens — or on `(room_id, tile)` hash only.
- **Separate discount γ_I < γ_E** for curiosity vs waypoint shaping.
- **Use when Go-Explore archive is too heavy** for a given week; RND is lighter to implement in `reward.py`.

**Tag:** useful-for-architecture | **Confidence:** Medium (fixed-camera rooms may collapse novelty quickly)

---

### h-DQN (goal-driven intrinsic motivation)
**What:** Top-level picks intrinsic goals; low-level DQN achieves them — demonstrated on Montezuma.

**STEALABLE:**
- **Planner as top-level:** waypoint = goal; PPO = low-level — we already have this hierarchy; don't add second RL layer.
- **Intrinsic goal = "reach adjacent room R"** when extrinsic helipad reward is zero.

**Tag:** reference-only | **Confidence:** Low (planner supersedes)

---

### Hindsight Experience Replay (HER)
**What:** Relabel failed trajectories with achieved goal as intended goal.

**STEALABLE:**
- **Goal-conditioned nav policy:** `π(a | s, g)` where `g` = next waypoint room embedding; relabel when agent stumbles into different room.
- **Implement in PPO with goal vector in obs** — concat `planner.next_waypoint_room()` one-hot.

**Tag:** useful-for-architecture | **Confidence:** Medium

---

## 4. Imitation / BC for games

| Project | Link | Tag | Transfer confidence |
|---------|------|-----|---------------------|
| VPT | https://github.com/openai/Video-Pre-Training | useful-for-architecture | **Medium** |
| RE Requiem BC/HG-DAgger | https://github.com/paulo101977/notebooks-rl/tree/main/re_requiem | useful-for-architecture | **High** |
| BASALT BC baseline | https://github.com/minerllabs/basalt-2022-behavioural-cloning-baseline | useful-for-code | **Medium** |
| PokeRL | https://github.com/reddheeraj/PokemonRL | useful-for-architecture | **High** |
| Action Chunking (ACT theory) | https://arxiv.org/html/2507.09061v4 | useful-for-architecture | **Medium** |
| imitation / DAgger docs | https://imitation.readthedocs.io/en/stable/tutorials/2_train_dagger.html | useful-for-code | **High** |

---

### VPT (OpenAI)
**What:** IDM labels YouTube → BC at scale → RL fine-tune for diamond pickaxe; 70k hours.

**STEALABLE:**
- **Warm-start RL:** BC-only can't finish long horizon; RL fine-tune with sparse milestone reward works — our recipe: BC on mansion → PPO with `reward.py` shaping.
- **Demo format:** paired video + jsonl actions — use nitrogen-bizhawk exporter on our recordings.
- **Skip IDM** — we have controller labels from BizHawk.

**Tag:** useful-for-architecture | **Confidence:** Medium (data volume smaller than Minecraft)

---

### RE Requiem BC + HG-DAgger (paulo101977)
**What:** YouTube-facing survival horror imitation; LSTM BC 24 epochs → human-gated DAgger corrections 25–76.

**STEALABLE:**
- **LSTM for fixed-camera context:** camera cuts break Markov assumption; **RecurrentPPO** or LSTM BC for navigation segments.
- **HG-DAgger for tank controls:** record yourself correcting agent for 5–10 min per problem room; merge into demo set — highest ROI in RE domain.
- **Video structure:** Phase 1 accelerated training montage → Phase 2 uncut failure → final gameplay — copy beat sheet.
- **Honest outcome:** "equal parts funny and humbling" — set viewer expectations for Jill dying to dogs.

**Tag:** useful-for-architecture + narrative | **Confidence:** High

---

### BASALT BC baseline
**What:** Fine-tune VPT-1x with `imitation` library; embed trajectories first for VRAM.

**STEALABLE:**
- **Two-stage train:** embed all demo frames offline → BC train on embeddings — fits 4070 8GB.
- **`imitation` + `sb3-contrib` RecurrentPPO** same stack as amcheste/pokemon-red-ai.

**Tag:** useful-for-code | **Confidence:** Medium

---

### PokeRL
**What:** Modular curriculum; anti-loop; per-map visited mask; three sequences (house exit, explore, battle).

**STEALABLE:**
- **Sequence-based training configs** — one JSON per RE1 act (mansion, guardhouse, lab).
- **Loop detection:** if `revisit_ratio > threshold` terminate episode — add to `env.py`.
- **Frame skip 4 + 72×80×8 obs** — validate our `frame_skip=8` against their ablation logic.

**Tag:** useful-for-architecture | **Confidence:** High

---

### Action Chunking (ACT, 2025 theory paper)
**What:** Predict `k` actions open-loop; reduces BC compounding error in long horizons.

**STEALABLE:**
- **Chunk size 4–8** for tank control (hold forward + tap turn) — policy outputs short button sequences per forward pass.
- **Especially for BC warm-start** before switching to single-step PPO.

**Tag:** useful-for-architecture | **Confidence:** Medium (tank controls are discrete-hold; test chunk=4 first)

---

## 5. Survival horror / RE-specific / adventure-game agents

| Project | Link | Tag | Transfer confidence |
|---------|------|-----|---------------------|
| RE Requiem (above) | https://www.youtube.com/watch?v=b3tCWlyWyg8 | useful-for-narrative | **High** |
| REviewer | https://github.com/Namsku/REviewer | useful-for-code | **High** |
| RE1 Autosplitter | https://github.com/deserteagle417/RE1-Autosplitter | useful-for-architecture | **High** |
| RE1 TAS (BizHawk) | https://tasvideos.org/5378M | reference-only | **Medium** |
| KG-DQN (text adventures) | https://github.com/rajammanabrolu/KG-DQN | useful-for-architecture | **Medium** |
| re-engine-mcp | https://github.com/praydog/re-engine-mcp | reference-only | **N/A** (RE Engine only) |

---

### REviewer
**What:** Speedrun tool for classic RE; real-time HP, enemy tracker, inventory, key items, IGT.

**STEALABLE:**
- **Validate RAM hooks:** cross-check our `PLAYER_HP`, inventory, enemy HP against REviewer's live reads.
- **Overlay layout ideas** for YouTube: HP bar, enemy count, key item checklist — reuse in stream HUD.
- **Key item tracker** mirrors `planner.required_items()` UI.

**Tag:** useful-for-code | **Confidence:** High for hook validation

---

### RE1 Autosplitter (LiveSplit)
**What:** Door splits, checkpoint holds, key-item splits for all main categories.

**STEALABLE:**
- **Waypoint list = autosplitter door route** — diff `route_jill_anypct.json` against split definitions for coverage audit.
- **Checkpoint hold splits** — natural curriculum segment boundaries.

**Tag:** useful-for-architecture | **Confidence:** High

---

### RE1 TAS (arandomgameTASer, BizHawk 2.9)
**What:** Human-optimized Jill Any%; input recording; not learned.

**STEALABLE:**
- **Upper-bound reference** for video contrast ("TAS vs learned agent").
- **`.bk2` → BC dataset** via nitrogen exporter — literal optimal-ish button timing for segments.

**Tag:** reference-only | **Confidence:** Medium (TAS inputs ≠ human-learnable without frame-perfect penalty)

---

### KG-DQN (text adventure)
**What:** Knowledge graph from observations prunes action space; QA-style action selection.

**STEALABLE:**
- **116-room graph as static KG** — planner already has this; expose as obs embedding for policy (GNN or room-id lookup table).
- **Action pruning:** in combat rooms allow {fire, aim, turn}; in puzzle rooms delegate to macro — dynamic `action_mask` in `env.py`.

**Tag:** useful-for-architecture | **Confidence:** Medium

---

### re-engine-mcp
**What:** MCP server for RE2/RE9/etc. memory — not PS1 RE1.

**STEALABLE:**
- **Telemetry schema ideas:** player, enemies, inventory, game_info endpoints — mirror in our Lua→JSON `state` dict for logging.

**Tag:** reference-only | **Confidence:** N/A for PS1

---

## 6. Memory-hacking-as-RL novelty

### MarNEO (see §1)
**Framing for video only:** contrast "legitimate" button-learning Jill vs meme RAM-hack — 30-second gag, not training path.

**Tag:** useful-for-narrative only | **Confidence:** N/A

---

## 7. Production / narrative

| Source | Link | Steal |
|--------|------|-------|
| Whidden Pokémon video | https://www.youtube.com/watch?v=DcYLT37ImBY | Cold open on parallel games → failure montage → milestone punch → technical appendix |
| RE Requiem BC video | https://www.youtube.com/watch?v=b3tCWlyWyg8 | Two-phase structure (BC montage / DAgger uncut) + "never read a tutorial" hook |
| pokerl-map-viz | https://github.com/pwhiddy/pokerl-map-viz | Live room visitation heatmap during training |
| Code Bullet Hill Climb | https://www.youtube.com/watch?v=SO7FFteErWs | Incremental difficulty episode beat |
| MarI/O | https://www.youtube.com/watch?v=qv6UVOQ0F44 | Generation/evolution visual even for gradient-based training |

### Concrete overlay / edit techniques

1. **Planner intent bubble:** text overlay `NEXT: Graveyard → Kennel` from `planner.next_waypoint_room()` (Voyager-style interpretability).
2. **Reward meter:** stacked bar of `reward.py` components (pokemon `state_scores` pattern).
3. **Room graph minimap:** 116-node graph with current node highlighted (pokerl-map-viz).
4. **Visited heatmap per room:** PokeRL per-map mask as picture-in-picture.
5. **Honesty frame:** early card stating "privileged RAM in obs/reward; learned policy presses buttons; puzzles scripted."
6. **Failure compilation before breakthrough:** 60–90s of door loops, death animations, inventory fumbles — Whidden's "surprisingly relatable failures."
7. **Speed comparison corner:** IGT timer vs human Any% pace (REviewer-style).
8. **Checkpoint title cards:** "Day 12: First tyrant encounter" between training phases (Code Bullet incremental learning narrative).

**Tag:** useful-for-narrative | **Confidence:** High

---

## Directly actionable this week

Mapped to existing repo files (`D:\re1_rl\`).

| Priority | Action | File(s) |
|----------|--------|---------|
| P0 | Validate length-prefixed JSON round-trip with live BizHawk; add `loadstate`/`savestate` commands | `bizhawk_bridge.py`, `lua/re1_client.lua` |
| P0 | Build per-room `.state` files for first 5 mansion waypoints in `route_jill_anypct.json` | `data/states/`, `env.py` `reset()` |
| P0 | Add `reward_breakdown: dict` return from `compute_reward`; scale total by `0.01` before PPO | `reward.py` |
| P1 | Implement `(room_id, tile_x, tile_z)` visitation set + novelty bonus; anti-revisit penalty after N | `reward.py`, `env.py` |
| P1 | Extend RAM dict in step: `room_id`, inventory diff, `dead` — wire planner advance | `memory_map.py`, `env.py`, `planner.py` |
| P1 | Record 30 min of your own Jill mansion segment via BizHawk movie → nitrogen export pipeline | `data/demos/`, new `scripts/export_demos.py` |
| P2 | BC warm-start script (RecurrentPPO or LSTM) on demos; load weights in PPO | new `scripts/train_bc.py` |
| P2 | Go-Explore lite: JSON archive `{cell_id: {state_path, trajectory, best_reward}}` | new `re1_rl/archive.py`, `env.py` |
| P2 | Stream overlay JSON (room, waypoint, reward terms) for OBS | `env.py` info dict |
| P3 | Dynamic `action_mask` per room type (combat vs cutscene) | `env.py` |
| P3 | Cross-validate HP/inventory addresses against REviewer or save-diff | `docs/memory_hooks_and_observation_design.md`, `memory_map.py` |

---

## Explicitly decided against

| Idea | Who did it | Why NOT for us |
|------|------------|----------------|
| RAM-write action space | MarNEO | Violates purity rule; not real "plays RE1"; meme only |
| Pure pixel end-to-end to helipad | linyiLYi Street Fighter retro | Sample-inefficient; fixed-camera + menus need symbols |
| NEAT / genetic algorithms | MarI/O, Code Bullet | Long-horizon RE1 needs credit assignment across inventory puzzles |
| Population-based training (FTW-scale) | DeepMind CTF | Solo dev, one 4070 — compute prohibitive |
| Full-game RL without hierarchy | Many Pokémon forks | RE1 Any% is 60+ min; planner + macros are the winning architecture |
| Frame KNN exploration | PokemonRedExperiments v1 | Superseded by coordinate novelty; rooms are finite graphs |
| 700GB demo corpus + VPT-1x from scratch | BASALT | Overkill; record hours not hundreds of GB |
| LLM automatic curriculum (GPT-4) | Voyager | Cost/complexity; `route_jill_anypct.json` is the curriculum |
| Training on horizontal position only | Sonic default reward | RE1 requires backtracking, vertical floors, door graph |
| BizHawk BirdsEye DLL dependency | BirdsEye | Extra moving part; raw Lua socket already scaffolded |
| Expecting TAS frame-perfect BC | RE1 TAS | Agent should learn robust human-tempo play for video watchability |

---

## Sources appendix

### Repos & code
- BrainHawk: https://github.com/TylerLandowski/BrainHawk
- StreetFighter2-DeepRL-on-Bizhawk: https://github.com/RuochenLiu/StreetFighter2-DeepRL-Model-on-Bizhawk
- bizhawk-luasocket: https://github.com/RemiChavance/bizhawk-luasocket
- BirdsEye: https://github.com/SkiHatDuckie/birds-eye
- BizHawk Python wiki: https://github.com/TASEmulators/BizHawk-ExternalTools/wiki/Python
- SuperMarioWorld_env: https://github.com/cspace-000/SuperMarioWorld_env
- nitrogen-bizhawk-dataset-generator: https://github.com/artryazanov/nitrogen-bizhawk-dataset-generator
- Go-Explore: https://github.com/uber-research/go-explore
- retro-baselines: https://github.com/openai/retro-baselines
- stable-retro integration docs: https://github.com/Farama-Foundation/stable-retro/blob/master/docs/integration.rst
- gym-super-mario-bros RAM: https://github.com/yumouwei/super-mario-bros-reinforcement-learning
- PokemonRedExperiments: https://github.com/PWhiddy/PokemonRedExperiments
- pokerl-map-viz: https://github.com/pwhiddy/pokerl-map-viz
- PokeRL: https://github.com/reddheeraj/PokemonRL
- pokemon-red-ai: https://github.com/amcheste/pokemon-red-ai
- VPT: https://github.com/openai/Video-Pre-Training
- Voyager: https://github.com/MineDojo/Voyager
- basalt-benchmark: https://github.com/minerllabs/basalt-benchmark
- notebooks-rl (RE Requiem): https://github.com/paulo101977/notebooks-rl
- KG-DQN: https://github.com/rajammanabrolu/KG-DQN
- REviewer: https://github.com/Namsku/REviewer
- RE1-Autosplitter: https://github.com/deserteagle417/RE1-Autosplitter
- MarNEO: https://github.com/GameAISchool2022members/MarNEO

### Papers
- Go-Explore: https://arxiv.org/abs/1901.10995 (Nature: https://arxiv.org/abs/2004.12919)
- RND: https://arxiv.org/abs/1810.12894
- VPT: NeurIPS 2022 / https://openai.com/index/vpt/
- Pokemon Red RL: https://arxiv.org/abs/2502.19920
- PokeRL: https://arxiv.org/html/2604.10812v1
- BASALT BEDD: https://arxiv.org/abs/2312.02405
- Voyager: https://arxiv.org/abs/2305.16291
- DeepMind CTF: https://arxiv.org/abs/1807.01281
- h-DQN: NeurIPS 2016
- Action Chunking BC: https://arxiv.org/html/2507.09061v4
- KG-DQN: https://aclanthology.org/N19-1358/

### Videos & writeups
- GymBizHawk (Qiita): https://qiita.com/pocokhc/items/031fbb3e65b0450bd2a3
- Whidden Pokémon: https://www.youtube.com/watch?v=DcYLT37ImBY
- MarI/O: https://www.youtube.com/watch?v=qv6UVOQ0F44
- RE Requiem BC: https://www.youtube.com/watch?v=b3tCWlyWyg8
- Felix Yu Sonic RL: https://flyyufelix.github.io/2018/06/11/sonic-rl.html
- AurelianTactics Sonic reward: https://medium.com/aureliantactics/creating-a-custom-reward-function-in-retro-gym-and-other-utilities-33c9f783bd1a
- RE1 TAS: https://tasvideos.org/5378M

---

*Document generated: 2026-07-02. Scout pass — verify BizHawk 2.11.1 socket behavior and PS1 room_id hook before betting training schedule on Go-Explore archive.*
