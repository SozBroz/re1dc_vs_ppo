# Curriculum stages

Each JSON file under `curriculum/` defines one training/eval stage for the hierarchical agent.

## Schema

| Field | Type | Description |
|-------|------|-------------|
| `stage` | string | Unique stage id (e.g. `m0_dining_to_main_hall`) |
| `init_savestate` | string | Path relative to project root to a BizHawk `.state` file |
| `waypoints` | list[string] | Room IDs along the segment (116-room graph codes) |
| `required_items` | list[string] | Item names that trigger pickup reward (empty = any) |
| `max_steps` | int | Truncation horizon for the env episode |

## Example

See `m0_dining_to_main_hall.json`: spawn in dining room (105), objective reach main hall (106).

Stages are consumed by `re1_rl.env.RE1Env` on `reset()` and passed to `WaypointPlanner` for reward shaping.
