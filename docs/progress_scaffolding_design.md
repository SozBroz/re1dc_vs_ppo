# Progress Scaffolding Design — RE1 Jill Any% RL

**Project:** `D:\re1_rl` — PPO agent for Resident Evil 1 Director's Cut (PS1, BizHawk)  
**Audience:** Solo dev, YouTube deadline, pragmatic shortcuts encouraged  
**Status:** Design doc only — no code changes in this pass  
**Related:** `memory_hooks_and_observation_design.md`, `prior_art_and_stealable_ideas.md`

---

## Executive summary

Progress scaffolding = every explicit signal that tells the agent **what to do next** and **which direction to go**, without scripting button presses. We separate:

| Layer | Role | May read privileged RAM? | May script buttons? |
|-------|------|--------------------------|---------------------|
| **Observation goal features** | "GPS + TODO text as numbers" | Yes | No |
| **Reward shaping (PBRS + sparse)** | Dense gradient toward subgoals | Yes | No |
| **Planner (`planner.py`)** | Symbolic waypoint stack | Yes | No (only sets goals) |
| **Curriculum** | Where episodes start / promote | Yes | No |
| **Macros** | Door interact, puzzles, cutscene skip | Yes | Yes (sanctioned) |
| **Low-level PPO policy** | Tank nav + combat | Consumes obs only | **Must learn** |

**Core thesis:** Feed the policy a compact **objective vector** every step (next room, direction to exit door, objective type, route progress) plus **potential-based shaping** on graph/door distance. Keep sparse milestone bonuses for room entry and item pickup. Use curriculum savestates and door macros to buy sample efficiency.

### Mechanism priority table

| Priority | Mechanism | Expected impact | Impl. cost (hrs) | Risk | Notes |
|----------|-----------|-----------------|------------------|------|-------|
| **1** | Goal-conditioned obs vector (compass + route progress + objective type) | **High** | 8–12 | Low | Biggest sample-efficiency win; maps to `env.py` `ram` → `goal` dict |
| **2** | PBRS on route-graph distance + door distance | **High** | 6–10 | Med | Replace raw waypoint bonus dominance; use Ng et al. form |
| **3** | Per-room savestate curriculum + reverse curriculum | **High** | 4–8 | Low | BizHawk `.state` is free; already have `m0_dining_to_main_hall.json` |
| **4** | Max-progress hysteresis (AllowBacktracking-style) | **Med–High** | 2–3 | Low | Fixes room re-entry farming + backtrack punishment |
| **5** | Door-interact macro (planner-triggered) | **High** (doors) | 6–10 | Med | YouTube-deadline lever; policy learns approach only |
| **6** | Empirical door-coordinate table | **High** (enables #1,#2) | 4–6 | Low | One human route logging pass beats RDT parser this week |
| **7** | Action-type masks per objective (`navigate`/`fight`/…) | **Med** | 3–5 | Low | KG-DQN / Voyager skill routing without LLM |
| **8** | Visited-tile mask per room (PokeRL-style) | **Med** | 6–8 | Low | 16×16 local mask as extra obs plane |
| **9** | Go-Explore lite archive `(room_id, tile)` | **Med** | 8–12 | Low | Complements curriculum; not required for v1 |
| **10** | HER / goal relabeling in replay | **Med** | 6–10 | Med | PPO + HER is awkward; defer until nav works |
| **11** | RDT-based door extraction | **Med** (long-term) | 16–24 | Med | Correct but slow; phase 2 completeness |
| **12** | Learned room embedding (128 rooms) | **Med** | 4–6 | Low | Better than one-hot; needs stable `room_id` (have it) |
| **13** | Combat aim-assist macro | **Med** (boss segments) | 4–6 | **High** (purity) | Tier B disclose; only if Yawn/Tyrant block schedule |
| **14** | Quickturn macro | **Low–Med** | 1–2 | Low | Already action 6; optional scripted 180° |
| **15** | Hierarchical RL (learned options) | **Low** | 40+ | Med | Planner supersedes; skip for v1 |
| **16** | LLM curriculum (Voyager-style) | **Low** | N/A | N/A | `route_jill_anypct.json` *is* the curriculum |

---

## 1. Objective conditioning in the observation

### 1.1 Design principles

1. **Goal-conditioned policy** π(a | s, g) — standard in HER (Andrychowicz et al., 2017) and universal value functions (Schaul et al., 2015). Our `g` is planner output, not learned.
2. **Egocentric direction** for motor control; **allocentric** room IDs for camera disambiguation.
3. **No action prescriptions** — never put "press X" or raw target coordinates the policy could memorize as a lookup table. Compass vectors are fine (same as GPS navigation benchmarks).
4. **Expand `observation_space` Dict** — keep `frame` unchanged; split current 16-float `ram` blob into `proprio` + `goal` (+ optional `context`).

### 1.2 Room ID encoding

| Option | Dim | Verdict |
|--------|-----|---------|
| One-hot over 116 rooms | 116 | Too sparse; wastes params |
| Integer `/116` | 1 | Too weak; collisions across stages |
| **Learned embedding** | 8 | **Recommended** — `nn.Embedding(128, 8)` on room code index |
| Fourier features on room index | 16 | OK ablation; embedding preferred |

**Implementation:** Map `room_id` string (`"105"`, `"106"`, …) → int index via `data/rooms.json` keys. Same table for `goal_room_id`.

### 1.3 Direction-to-target features

Requires **door coordinates** in current room pointing toward `goal_room_id`. See §7 (door acquisition plan).

Per step, planner resolves **active exit** = door in `current_room` whose `target_room == next_waypoint_room()` (or next hop on route BFS if multi-hop).

| Feature | Dim | Formula | Normalization |
|---------|-----|---------|---------------|
| `delta_x` | 1 | `door_x - player_x` | `/ 4096` (typical room span) |
| `delta_z` | 1 | `door_z - player_z` | `/ 4096` |
| `distance` | 1 | `sqrt(dx²+dz²)` | `/ 4096`, clip [0, 2] |
| `bearing_sin` | 1 | `sin(angle_to_door - facing)` | already [-1, 1] |
| `bearing_cos` | 1 | `cos(angle_to_door - facing)` | already [-1, 1] |
| `in_target_room` | 1 | `room_id == goal_room` | binary |
| `door_visible_hint` | 1 | optional interaction prompt RAM | binary when found |

**Angle convention:** RE1 facing `0..4095` → `θ = 2π * facing / 4096`. Bearing = `atan2(dz, dx) - θ`, wrapped to [-π, π].

**Feasibility without doors:** Fallback to **graph-only** features: `route_hop_distance`, `wrong_room_flag`. Agent still learns from room-entry rewards but nav is slower — acceptable for day 1, not week 1.

### 1.4 Objective type flags

From `route_jill_anypct.json` field `action_type` on current waypoint:

| `action_type` | One-hot index | Policy implication |
|---------------|---------------|-------------------|
| `navigate` (default) | 0 | Standard nav + door macro |
| `pickup` | 1 | Bias toward interaction prompt; item pickup reward |
| `use_item` | 2 | Planner may invoke inventory macro |
| `fight` | 3 | Enable combat reward terms; relax door compass |
| `scripted_macro` | 4 | **Policy masked off** — macro owns episode slice |

Encode as **5-d one-hot** in `goal` vector. Also expose `waypoint_seq_norm = waypoint_index / 52` (1 scalar).

### 1.5 Route progress / TODO stack features

| Feature | Dim | Description |
|---------|-----|-------------|
| `waypoint_index_norm` | 1 | `planner.waypoint_index / len(waypoints)` |
| `waypoints_remaining_norm` | 1 | `(total - index) / total` |
| `next_room_index` | 8 | embedding of `planner.next_waypoint_room()` |
| `route_hop_distance` | 1 | BFS hops on mansion graph from current room to goal room / max_hops |
| `required_item_flags` | 8 | multi-hot over key items for current objective (when inventory wired) |
| `stage_id_norm` | 1 | curriculum stage index / num_stages |

This is the **TODO list as numbers**: the policy sees *which* of 52 steps it's on and *what kind* of step, not natural language.

### 1.6 Visited-mask channel (optional v1.1)

Steal from [PokeRL](https://arxiv.org/html/2604.10812) / [PokemonRedExperiments v2](https://github.com/PWhiddy/PokemonRedExperiments): per-room `16×16` binary mask centered on room entry, allocentric. Stack as 5th frame channel or separate `visited` plane `(16, 16, 1)`.

**Why:** RE1 backtracking through doors is correct behavior; mask prevents re-exploring same corner loops.

### 1.7 What NOT to put in obs

- Raw event-flag bitfield (reward-only → avoids memorization)
- RNG seed
- Absolute door coordinate without egocentric delta (policy could learn room-id → coord table *if* combined with open-loop actions — deltas are safer)
- "Correct button" one-hot

---

## 2. Potential-based reward shaping (PBRS)

### 2.1 Theory

Use Ng–Harada–Russell potential-based shaping (ICML 1999):

\[
F(s, s') = \gamma \Phi(s') - \Phi(s)
\]

Under standard MDP assumptions, optimal policies are **unchanged** vs sparse reward only. Critical: never add raw `-distance` per step (that rewards hovering near goal).

Paper: [Policy Invariance Under Reward Transformations](https://people.eecs.berkeley.edu/~pabbeel/cs287-fa09/readings/NgHaradaRussell-shaping-ICML1999.pdf)

### 2.2 Potential functions for RE1

Define **composite potential** (sum is valid):

#### Φ_graph — route graph distance

```python
# reward.py — sketch
def phi_graph(room_id: str, planner: WaypointPlanner, graph: RoomGraph) -> float:
    goal = planner.next_waypoint_room()
    if goal is None:
        return 0.0
    hops = graph.shortest_path_hops(room_id, goal)  # BFS on 116-room graph
    if hops is None:  # wrong wing / unreachable until flag
        return -10.0  # flat penalty plateau, not gradient
    return -float(hops)
```

- **When in wrong room:** Φ is constant (no per-step gradient to farm wrong rooms).
- **When on route:** each hop toward goal increases Φ.

#### Φ_door — distance to active exit door

```python
def phi_door(state: dict, door_table: DoorTable, planner: WaypointPlanner) -> float:
    door = door_table.get_exit(state["room_id"], planner.next_waypoint_room())
    if door is None:
        return 0.0  # fall back to graph-only
    dx = door.x - state["x"]
    dz = door.z - state["z"]
    return -math.hypot(dx, dz) / 4096.0
```

Weight: `Φ = w_g * Φ_graph + w_d * Φ_door` with `w_d > w_g` inside correct room, `w_g` dominant when in wrong room.

#### Φ_progress — max waypoint index (AllowBacktracking)

```python
# Hysteresis state in env, not in Φ — use for sparse bonus only:
# max_waypoint_reached = max(max_waypoint_reached, planner.waypoint_index)
# bonus = SCALE * (max_waypoint_reached - prev_max)  # one-sided
```

Steal from [retro-baselines `AllowBacktracking`](https://github.com/openai/retro-baselines/blob/master/agents/sonic_util.py): reward **increment** in max progress, not raw position delta.

### 2.3 Integrating PBRS into `reward.py`

Current terms (`STEP_PENALTY`, `WAYPOINT_ROOM_BONUS`, etc.) stay; **restructure**:

| Term | Type | Change |
|------|------|--------|
| `step` | per-step | keep small `-0.01` |
| `pbrs_graph` | PBRS | **new** — replaces most of `wrong_room` gradient |
| `pbrs_door` | PBRS | **new** — dense nav inside room |
| `waypoint` | sparse | **reduce** to one-time `+2.0` on *first* entry per episode |
| `wrong_room` | sparse | one-time small penalty on entering off-route room |
| `item`, `hp`, `death`, `softlock` | unchanged | |

```python
# reward.py — compute_reward sketch
def compute_reward(prev, state, planner, pbrs_state, *, gamma=0.99):
    phi_prev = potential(prev, planner, pbrs_state)
    phi_now = potential(state, planner, pbrs_state)
    bd["pbrs"] = gamma * phi_now - phi_prev

    # Waypoint: only if room transition AND first visit this episode
    if room_changed and room == target and room not in pbrs_state.visited_waypoint_rooms:
        bd["waypoint"] = WAYPOINT_ROOM_BONUS
        pbrs_state.visited_waypoint_rooms.add(room)
        planner.advance_if_success(state)
    ...
```

**REWARD_SCALE:** keep `0.1`; PBRS terms are already small (distance in ~[0,1]).

### 2.4 PPO γ alignment

PBRS theory uses same γ as RL. SB3 PPO default `gamma=0.99` — pass to `compute_reward(gamma=...)`.

---

## 3. TODO-list / task-stack designs from prior art

### 3.1 What each project did (stealable at our scale)

| Project | How objectives are encoded | Reward / curriculum | Steal for RE1 |
|---------|---------------------------|---------------------|---------------|
| **[PokemonRedExperiments v2](https://github.com/PWhiddy/PokemonRedExperiments)** | Obs: screens, HP, badges, **event bits**, explore map, recent actions. No explicit goal vector — milestones via reward. | Coordinate novelty `seen_coords[(x,y,map)]`; `state_scores` dict per term | Event bits → route milestones; `state_scores` → `reward_breakdown` (have it); coord novelty → `(room_id, tile_x, tile_z)` |
| **[pokerl / neroRL](https://arxiv.org/abs/2502.19920)** | **Visited mask** channel; inventory; event completion binary vector | 25+ shaped terms; `reward_scale=4`; ablations on exploit | Visited mask per room; decomposed logging; milestone curriculum |
| **[PokeRL](https://arxiv.org/html/2604.10812)** | 3-sequence curriculum JSON; per-map visited mask; anti-loop | Micro/meso/macro reward tiers; map transition +10 | Sequence = our `curriculum/*.json`; anti-loop on `(room, action)` |
| **[Go-Explore](https://github.com/uber-research/go-explore)** | Cell = `(room, x, y, level, …)`; archive + return | Trajectory score per cell; random explore after return | Cell = `(room_id, tile)` + BizHawk savestate; frontier = route waypoints |
| **[Voyager](https://github.com/MineDojo/Voyager)** | LLM proposes next task; **skill library** indexed by embedding | Code execution verified in env | **No LLM** — `route_jill_anypct.json` = fixed curriculum; puzzle scripts = skill library |
| **[MineRL BASALT](https://github.com/minerllabs/basalt-benchmark)** | Fuzzy human goals ("make waterfall") | BC on 26M frames; pairwise human eval | BC on our BizHawk demos; fuzzy goals → waypoint `objective` string is for logging only |
| **[HER](https://papers.nips.cc/paper/2017/file/453fadbd8a1a3af50a9df4df899537b5-Paper.pdf)** | `achieved_goal` + `desired_goal` in obs dict | Relabel failures as successes for achieved goal | Goal vector = `desired_goal`; relabel when agent reaches wrong room — phase 2 |
| **[h-DQN / feudal](https://proceedings.neurips.cc/paper/2016/file/f442d33fa06832082290ad8544a8da27-Paper.pdf)** | Manager sets subgoals | Intrinsic reward for subgoal completion | **Planner = non-learned manager** — already hierarchical |
| **[retro-baselines Sonic](https://github.com/openai/retro-baselines)** | Level progress variable | `AllowBacktracking` max-x; `RewardScaler(0.01)` | Max waypoint index; reward scale (have `REWARD_SCALE`) |
| **[RE Requiem BC](https://github.com/paulo101977/notebooks-rl)** | LSTM over visual context | HG-DAgger human fixes | RecurrentPPO for camera cuts; DAgger for tank control — parallel track |

### 3.2 Recommended task-stack architecture (no second RL layer)

```
route_jill_anypct.json (52 steps)
        ↓
curriculum/*.json (stage waypoints subset + init_savestate)
        ↓
WaypointPlanner (current index, action_type, required_items)
        ↓
┌───────────────────────────────────────┐
│  goal vector in obs (every step)      │
│  PBRS + sparse rewards                │
│  optional: macro_request if scripted  │
└───────────────────────────────────────┘
        ↓
PPO MultiInputPolicy (frame CNN + MLP(goal, proprio))
```

**Not doing:** learned option network, LLM task generator, separate high-level PPO.

### 3.3 Anti-loop (from PokeRL)

```python
# env.py — LoopDetector
class LoopDetector:
  def __init__(self, window=20, threshold=0.6):
      self.history: deque[tuple[str,int]] = deque(maxlen=window)

  def step(self, room_id: str, action: int) -> float:
      key = (room_id, action)
      self.history.append(key)
      if len(self.history) < window:
          return 0.0
      repeats = sum(1 for k in self.history if k == key)
      if repeats / len(self.history) > threshold:
          return -5.0  # loop penalty in reward breakdown
      return 0.0
```

---

## 4. Macro-actions / scripted skills as scaffolding

### 4.1 When macros beat learned control (YouTube deadline)

| Macro | Trigger | Frames saved | Learn vs script |
|-------|---------|--------------|-----------------|
| **Cutscene / door skip** | `not in_control` | **Huge** (~30–60% steps) | **Script** — already in `env._skip_uncontrolled` |
| **Door interact** | planner: in room, `distance < ε`, facing aligned OR prompt flag | High | **Script** walk-in; policy learns coarse approach |
| **Inventory puzzle** | `action_type: scripted_macro` / `use_item` | Very high | **Script** — finite puzzles (V-JOLT, crests) |
| **Password / typewriter** | room + item condition | Very high | **Script** |
| **Quickturn 180°** | action 6 exists | Low | Either; script is 1 frame perfect |
| **Auto-aim snap** | combat rooms | Med | **Avoid v1** — Tier B; learn aim from pixels+enemy RAM |
| **Run-forward hold** | long corridors | Low | Learn |

### 4.2 Door-interact macro (recommended v1)

```python
# re1_rl/macros/door.py — sketch
def door_interact_macro(bridge, door, *, max_frames=120) -> bool:
    """Returns True if room_id changed."""
    for _ in range(max_frames):
        # Phase A: coarse align (could delegate to policy beforehand)
        state = read_state(bridge)
        if abs(bearing_error(state, door)) > 0.15:
            bridge.step(turn_toward(state, door), 4)
            continue
        # Phase B: walk + interact
        bridge.step({"up": True, "cross": True}, 8)
        if room_changed(state):
            return True
    return False
```

**Planner API extension:**

```python
# planner.py
def macro_request(self, state: dict) -> str | None:
    obj = self.current_objective()
    if obj and obj.get("action_type") == "scripted_macro":
        return obj.get("macro_name", "default_cutscene")
    if self._near_door_and_aligned(state):
        return "door_interact"
    return None
```

**Env integration:** If `macro_request` is set, policy not called; macro runs; transitions after macro count as env steps but **exclude from PPO buffer** (same rule as cutscene skip).

### 4.3 Voyager-style skill library (static)

| Skill name | Precondition (RAM) | Postcondition | File |
|------------|-------------------|---------------|------|
| `dining_emblem_pickup` | room 105, item flag | emblem in inventory | `macros/pickup_emblem.lua` |
| `shield_bee_puzzle` | room 202, items | flag bit | `macros/shield_bee.py` |
| `vjolt` | lab | flag | `macros/vjolt.py` |

Retrieve by `room_id` + `action_type` from route JSON — no vector DB needed.

---

## 5. Curriculum mechanics

### 5.1 Auto-generating per-room savestates

**Procedure:**

1. Load human/TAS save at mansion start (have `states/jill_control.State` in dining room 105).
2. Walk route with scripted assistance or human; at each of 52 waypoints:
   - Assert `room_id` matches route step
   - `bridge.save_savestate(f"data/states/wp_{seq:02d}_{room_id}.state")`
3. Record manifest `data/states/manifest.json`:

```json
{
  "wp_01_105": {"room_id": "105", "hp": 96, "waypoint_seq": 1},
  "wp_02_106": {"room_id": "106", "hp": 96, "waypoint_seq": 2}
}
```

**Automation script:** `scripts/record_route_savestates.py` — human drives, presses hotkey to snapshot.

### 5.2 Reverse curriculum

Start near goal, move start backward (Berkeley CS287 classic + Code Bullet incremental difficulty):

| Phase | `init_savestate` | `waypoints` | `max_steps` |
|-------|------------------|-------------|-------------|
| R0 | `wp_52_helipad` | `["helipad"]` | 200 |
| R1 | `wp_48_lab` | last 4 rooms | 800 |
| … | … | … | … |
| F | `wp_01_105` | full 52 | 200k eval |

**Promotion:** success rate > 70% over 50 episodes → prepend earlier waypoint to stage.

### 5.3 Success-gated stage promotion

```json
{
  "stage": "m1_dining_to_kennel",
  "init_savestate": "data/states/wp_01_105.state",
  "waypoints": ["106", "103", "108", "115"],
  "max_steps": 2000,
  "promotion": {
    "min_success_rate": 0.6,
    "eval_episodes": 30,
    "on_success": "m2_extend_to_graveyard"
  }
}
```

### 5.4 One policy vs per-stage policies

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Single policy + stage_id in obs** | Transfer; one video narrative | Catastrophic forgetting if stages too diverse | **Recommended** |
| Per-stage policy | Faster per segment | 52 models; no transfer | No |
| Single policy, reset weights per stage | Simple | Wastes learning | No |

Encode `curriculum_stage_norm` (1 scalar) + `waypoint_index_norm` so one network knows "where in training" and "where on route."

**BC warm-start:** train on demonstrations from **all** stages mixed, weighted by current curriculum distribution.

---

## 6. Anti-scaffolding risks and mitigations

| Mechanism | Exploit | Symptom | Mitigation |
|-----------|---------|---------|------------|
| Waypoint room bonus | Re-enter target room via door loop | Score farm | **Once per episode per waypoint** (`visited_waypoint_rooms` set); max-progress hysteresis |
| Wrong-room penalty | Avoid all exploration | Stuck at spawn | Penalty only on **transition into** off-route room, not per-step |
| Door distance PBRS | Wall-hug along iso-lines | Orbits walls | Combine with **graph PBRS**; cap `|pbrs_door|`; visited-mask penalty |
| Door distance (non-PBRS) | Hover near door without entering | Vibration at threshold | Use PBRS form only; require room transition for sparse bonus |
| Item pickup reward | Pick/drop dupe (if possible) | Inventory oscillation | Reward only on **new** item id in episode set |
| Coordinate exploration bonus | Explore wrong floor | Basement before keys | Gate novelty on `planner.is_on_route(room)` |
| Combat kill reward | Farm respawning enemies | Infinite kills | Once per enemy slot id; room clear flag |
| Softlock penalty | — | Good | Keep; triggers menu/wall loops |
| Macro door | Policy never learns approach | Works until macro range shrinks | Phase macro out: reduce align tolerance over training |
| Graph distance leak | Shortest-path without doors | Walk through walls | Graph edges only through **known doors** |
| `scripted_macro` overuse | Policy atrophies | No learning on puzzles | OK for video; disclose Tier B |
| Stage obs leakage | Memorize stage→action | Overfit curriculum | Randomize init position noise ±64 units; shuffle waypoint subsets |

**Hysteresis pattern (general):**

```python
class ProgressTracker:
    max_waypoint: int = 0
    visited_rooms: set[str] = field(default_factory=set)

    def update(self, planner, room_id):
        self.max_waypoint = max(self.max_waypoint, planner.waypoint_index)
        first_visit = room_id not in self.visited_rooms
        self.visited_rooms.add(room_id)
        return first_visit
```

---

## 7. Door-coordinate acquisition plan

### 7.1 Options compared

| Approach | Time | Accuracy | Coverage | Blockers |
|----------|------|----------|----------|----------|
| **A. Empirical logging** | 4–6 hrs | **Exact** in RAM space | Route doors only (~60 transitions) | One human Any% route playthrough |
| **B. RDT `DOOR_SET` parse** | 16–24 hrs | High if parser correct | All doors in all rooms | PS1 disc extract; RE1 SCD format; coord system alignment |
| **C. Manual TAS wiki / maps** | 8–12 hrs | Medium | Sparse | RE1 maps aren't grid-aligned to RAM |
| **D. RL discover doors** | Ongoing | Noisy | — | Circular — need doors to train |

### 7.2 **Decision: Empirical logging (v1)** — RDT as phase 2

**Justification:**

1. **RAM alignment for free** — logging `player_x/z` at the frame *before* room transition and `room_id` after yields exit pose + target room in **native coordinates** the policy already sees.
2. **Route-only scope** — we need ~1 door per waypoint step, not all 116×N doors.
3. **RDT risk** — format is documented for RE1/RE2 ([Just Solve wiki](http://justsolve.archiveteam.org/wiki/RDT_(Resident_Evil_1997)); `DOOR_SET` opcode `0x0C` has trigger x,y and target room x,y,z) but PS1 RE1 DC file layout vs RAM world space requires validation tooling; [classicremodification RDTool](https://classicremodification.com/doku.php?id=re2_tools) targets RE2 primarily.
4. **One afternoon** — script `scripts/log_door_transitions.py` hooks env step; human plays route once.

**Logging protocol:**

```python
# On room_id change from A → B:
#   record exit_door: {from_room, to_room, x, z, facing, cam_id}
#   record entry_pose: {room: B, x, z, facing}  # validates target spawn
# Write: data/doors_empirical.json
```

**Schema:**

```json
{
  "105->106": {
    "from_room": "105",
    "to_room": "106",
    "door_x": 1234,
    "door_z": 5678,
    "entry_x": 890,
    "entry_z": 1234,
    "notes": "dining to main hall"
  }
}
```

**Phase 2 (RDT):** Build `scripts/parse_rdt_doors.py` on extracted `room1XX0.rdt` from SLUS-00551; cross-validate against empirical table; fill non-route rooms for Go-Explore.

---

## 8. Recommended v1 stack (implement this week)

### 8.1 Build order (≈35–45 hrs)

| Day | Deliverable | Files |
|-----|-------------|-------|
| 1 | Empirical door log + `data/doors_empirical.json` | `scripts/log_door_transitions.py` |
| 1–2 | `RoomGraph` BFS + `DoorTable` loader | `re1_rl/room_graph.py`, `data/room_graph.json` |
| 2 | Expand obs: `proprio` + `goal` dicts | `env.py`, `planner.py` |
| 3 | PBRS terms + progress hysteresis | `reward.py`, `re1_rl/progress.py` |
| 3 | 5 curriculum stages (reverse order OK) | `curriculum/m*.json`, `data/states/` |
| 4 | Door macro + planner `macro_request` | `re1_rl/macros/door.py`, `env.py` |
| 4–5 | BC smoke test on dining→hall demo | `scripts/train_bc.py` |
| 5 | PPO fine-tune 4–8 parallel envs | training script |

### 8.2 `env.py` observation_space (v1)

Replace flat `ram: (16,)` with:

```python
self.observation_space = spaces.Dict({
    "frame": spaces.Box(0, 255, shape=(84, 84, 4), dtype=np.uint8),
    "proprio": spaces.Box(-1.0, 1.0, shape=(20,), dtype=np.float32),
    "goal": spaces.Box(-1.0, 1.0, shape=(24,), dtype=np.float32),
})
```

### 8.3 Field-by-field spec — `proprio` (20 floats)

| Index | Name | Source | Norm |
|-------|------|--------|------|
| 0 | hp | `PLAYER_HP` | `/140` |
| 1 | hp_delta | `hp - prev_hp` | `/20`, clip |
| 2–3 | x, z | player pos | `(val % 4096) / 4096` room-local wrap |
| 4 | y | elevation | `/1024` |
| 5–6 | facing_sin, facing_cos | `PLAYER_FACING` | unit circle |
| 7 | room_emb_0 | embedding | learned — **or** use indices 7–14 for 8-d emb |
| 8–14 | room_emb_1..7 | embedding | (if not using nn.Embedding in policy, use 8 floats) |
| 15 | cam_id | `CAM_ID` | `/16` |
| 16 | in_control | game mode bit | `0/1` |
| 17 | enemy_count | future hook | `/10` |
| 18 | interaction_prompt | future hook | `0/1` |
| 19 | character_id | Jill=1 | `0/1` |

**Note:** If using `nn.Embedding` in policy for room_id, pass **room_index** as single float in proprio and embed in network; don't duplicate 8 floats in vector.

**Simpler v1 proprio (16 floats):** hp, hp_delta, x, z, y, sin, cos, room_index/128, cam/16, in_control, enemy_count, prompt, char_id, padding×3.

### 8.4 Field-by-field spec — `goal` (24 floats)

| Index | Name | Source | Norm |
|-------|------|--------|------|
| 0 | goal_room_index | `next_waypoint_room()` | `/128` |
| 1 | waypoint_index_norm | planner index | `/52` |
| 2 | waypoints_remaining | count left | `/52` |
| 3 | route_hop_distance | BFS | `/20`, clip |
| 4 | in_target_room | bool | `0/1` |
| 5–6 | delta_x, delta_z | door - player | `/4096`, clip [-2,2] |
| 7 | distance_to_door | hypot | `/4096`, clip [0,2] |
| 8–9 | bearing_sin, bearing_cos | egocentric | [-1,1] |
| 10–14 | objective_type one-hot | route `action_type` | 5 dims |
| 15 | curriculum_stage_norm | stage id | `/10` |
| 16–19 | required_item_hash | placeholder | 4 dims multi-hot later |
| 20 | wrong_room_flag | not on route subgraph | `0/1` |
| 21 | doors_available | door table has exit | `0/1` |
| 22–23 | reserved | future boss phase | 0 |

### 8.5 `planner.py` API extensions

```python
class WaypointPlanner:
    def next_hop_room(self, current_room: str, graph: RoomGraph) -> str | None:
        """Next room on shortest path toward waypoint (may != final waypoint)."""

    def objective_one_hot(self) -> np.ndarray:  # 5-d
        ...

    def route_hop_distance(self, current_room: str, graph: RoomGraph) -> int:
        ...

    def macro_request(self, state: dict, door_table: DoorTable) -> str | None:
        ...
```

### 8.6 `reward.py` v1 terms

```python
bd = {
    "step": -0.01,
    "pbrs_graph": 0.0,   # gamma * phi_g(s') - phi_g(s)
    "pbrs_door": 0.0,    # gamma * phi_d(s') - phi_d(s)
    "waypoint": 0.0,     # sparse, once per room per ep
    "wrong_room": 0.0,   # sparse on transition
    "item": 0.0,
    "hp": 0.0,
    "death": 0.0,
    "softlock": 0.0,
    "loop": 0.0,         # optional PokeRL anti-loop
}
```

### 8.7 Policy network

Keep SB3 `MultiInputPolicy`; CNN on `frame`; MLP on `concat(proprio, goal)` → 256-d fusion. No architecture change required beyond input dims.

---

## 9. Testing & acceptance criteria

| Test | Pass condition |
|------|----------------|
| Obs shape | `reset()` returns `goal.shape == (24,)`, finite values |
| Compass sanity | Standing north of door → `bearing_sin` ≈ 0, `distance` decreases when walking toward door |
| PBRS sign | Step toward door increases total reward vs step away (ceteris paribus) |
| Re-entry farm | Re-entering waypoint room 10× → cumulative `waypoint` bonus ≤ 1× |
| Curriculum reset | 10× `reset()` identical `(room_id, hp, x, z)` |
| Macro door | Dining → Main Hall macro succeeds ≥90% from savestate `jill_control.State` |
| PPO smoke | 100k steps: mean `waypoint_index` > 0 vs random baseline |

---

## Appendix A — Prior art links

### Repos

| Project | URL |
|---------|-----|
| PokemonRedExperiments | https://github.com/PWhiddy/PokemonRedExperiments |
| pokerl / neroRL | https://github.com/MarcoMeter/neroRL/tree/poke_red |
| PokeRL paper | https://arxiv.org/html/2604.10812 |
| Go-Explore | https://github.com/uber-research/go-explore |
| retro-baselines | https://github.com/openai/retro-baselines |
| Voyager | https://github.com/MineDojo/Voyager |
| MineRL BASALT | https://github.com/minerllabs/basalt-benchmark |
| RE Requiem notebooks | https://github.com/paulo101977/notebooks-rl |
| RE1 Autosplitter | https://github.com/deserteagle417/RE1-Autosplitter |
| REviewer | https://github.com/Namsku/REviewer |
| pokerl observations docs | https://drubinstein.github.io/pokerl/docs/chapter-2/observations/ |
| nitrogen-bizhawk dataset | https://github.com/artryazanov/nitrogen-bizhawk-dataset-generator |

### Papers

| Paper | URL |
|-------|-----|
| PBRS (Ng et al., ICML 1999) | https://people.eecs.berkeley.edu/~pabbeel/cs287-fa09/readings/NgHaradaRussell-shaping-ICML1999.pdf |
| HER (NeurIPS 2017) | https://papers.nips.cc/paper/2017/file/453fadbd8a1a3af50a9df4df899537b5-Paper.pdf |
| Go-Explore (Nature 2020) | https://arxiv.org/abs/2004.12919 |
| Pokemon Red RL (arxiv 2502.19920) | https://arxiv.org/abs/2502.19920 |
| Voyager (2023) | https://arxiv.org/abs/2305.16291 |
| RND | https://arxiv.org/abs/1810.12894 |
| Universal Value Functions (Schaul et al.) | https://arxiv.org/abs/1506.03179 |

### RE modding / formats

| Resource | URL |
|----------|-----|
| RDT format (RE1997) | http://justsolve.archiveteam.org/wiki/RDT_(Resident_Evil_1997) |
| RE2 RDT door bytes reference | https://www.tapatalk.com/groups/residentevil123/re2-rdt-information-t1330.html |
| Classic RE tools (RDTool) | https://classicremodification.com/doku.php?id=re2_tools |

---

## Appendix B — Quick reference: current → v1 file touch map

| File | Change |
|------|--------|
| `re1_rl/env.py` | Split obs; build goal/proprio; `ProgressTracker`; macro gating |
| `re1_rl/reward.py` | PBRS; hysteresis; loop penalty |
| `re1_rl/planner.py` | `objective_one_hot`, `route_hop_distance`, `macro_request` |
| `re1_rl/memory_map.py` | No change v1; add inventory hooks v1.1 |
| `data/doors_empirical.json` | **New** — logged transitions |
| `data/room_graph.json` | **New** — adjacency from doors + route |
| `re1_rl/room_graph.py` | **New** — BFS |
| `re1_rl/progress.py` | **New** — hysteresis state |
| `re1_rl/macros/door.py` | **New** |
| `curriculum/*.json` | Expand stages + promotion rules |
| `scripts/log_door_transitions.py` | **New** |

---

*Document version: 1.0 — 2026-07-02. Implements scaffolding design for `D:\re1_rl` Jill Any% PPO track.*
