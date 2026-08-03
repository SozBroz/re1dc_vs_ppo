# Curriculum stages



Each JSON file under `curriculum/` defines one training/eval stage for the hierarchical agent.



## Schema



| Field | Type | Description |

|-------|------|-------------|

| `stage` | string | Unique stage id (e.g. `m0_dining_to_main_hall`) |

| `stage_index` | int | Numeric stage order (passed to `ObsEncoder` as `curriculum_stage_index`) |

| `init_savestate` | string | Path relative to project root to a BizHawk `.state` file |

| `route_path` | string | Route contract relative to project root (Yawn rails uses `data/yawn_checkpoint_route.json`) |
| `route_steps` | list[int] | Ordered route `seq` values consumed by `WaypointPlanner` |

| `mode` | string | `yawn_rails` enables live goal features, dominant checkpoint reward, and curated route-cell resets |
| `episode_mode` | string | `one_leg`: start at one curated cell, complete one successor checkpoint, terminate |
| `cells_manifest` | string | Curated route-cell manifest; missing cells are skipped without falling back to PB/archive |
| `success_room` | string \| null | Final route room for eval/logging |

| `required_items` | list[string] | Item names that trigger pickup reward (empty = any) |

| `max_steps` | int | Truncation horizon for the env episode (`0` = no cap) |



Legacy field `waypoints` (room-id list) is still accepted by `WaypointPlanner` if present, but current stage JSON uses `route_steps` instead.



## Reward mode



`yawn_rails_one_leg.json` is the active architecture contract: checkpoint
completion pays an explicit terminal `+1.2`; other positive signals are scaled
auxiliary hints. Goal compass/progress features are live and fused into the
policy. PB/Go-Explore capture remains enabled for archival use, but those
sidecars are not sampled as rails reset sources.



## Example



See `yawn_rails_one_leg.json`.



`exp_m0_cap12k.json` / `exp_m0_cap24k.json` add `route_steps`, `success_room: "107"`, and a non-zero `max_steps` cap for bounded eval runs.



Stages are consumed by `re1_rl.env.RE1Env` on `reset()` and passed to
`WaypointPlanner`. The outer reset wrapper samples only the initial route state
or a complete curated route-cell bundle when `mode == "yawn_rails"`.


