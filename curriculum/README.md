# Curriculum stages



Each JSON file under `curriculum/` defines one training/eval stage for the hierarchical agent.



## Schema



| Field | Type | Description |

|-------|------|-------------|

| `stage` | string | Unique stage id (e.g. `m0_dining_to_main_hall`) |

| `stage_index` | int | Numeric stage order (passed to `ObsEncoder` as `curriculum_stage_index`) |

| `init_savestate` | string | Path relative to project root to a BizHawk `.state` file |

| `route_steps` | list[int] | Indices into `data/route_jill_anypct.json` steps for `WaypointPlanner` (eval / human play; **not** checkpoint-path reward shaping) |

| `success_room` | string \| null | Terminal room id for eval truncation / logging (e.g. `"107"`). **Does not pay a reward** — `compute_reward` ignores `success_room` (checkpoint-path rewards are **OFF**). |

| `required_items` | list[string] | Item names that trigger pickup reward (empty = any) |

| `max_steps` | int | Truncation horizon for the env episode (`0` = no cap) |



Legacy field `waypoints` (room-id list) is still accepted by `WaypointPlanner` if present, but current stage JSON uses `route_steps` instead.



## Reward mode



Exploration training uses discovery rewards only (new room, cutscene, items, combat, etc.) per `docs/exploration_rewards.md`. Checkpoint-path shaping — waypoint bonus, PBRS, wrong-room / retreat penalties, and `success_room` bonus — is **disabled** in `re1_rl/reward.py`. `route_steps` and `success_room` remain for planner state, eval, and human play tooling.



## Example



See `m0_dining_to_main_hall.json`: spawn from `states/jill_control_fresh.State` with empty `route_steps` (open exploration stub).



`exp_m0_cap12k.json` / `exp_m0_cap24k.json` add `route_steps`, `success_room: "107"`, and a non-zero `max_steps` cap for bounded eval runs.



Stages are consumed by `re1_rl.env.RE1Env` on `reset()` and passed to `WaypointPlanner` for symbolic route state (not checkpoint-path reward shaping).


