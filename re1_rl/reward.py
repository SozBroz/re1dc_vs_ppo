"""Shaped reward for hierarchical RE1 control."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from re1_rl.planner import WaypointPlanner

STEP_PENALTY = -0.01
WAYPOINT_ROOM_BONUS = 5.0
WRONG_ROOM_PENALTY = -1.0
ITEM_PICKUP_BONUS = 10.0
HP_LOSS_SCALE = 0.05
DEATH_PENALTY = -50.0
SOFTLOCK_TIMEOUT_PENALTY = -10.0
SOFTLOCK_STEP_THRESHOLD = 500


def compute_reward(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    planner: WaypointPlanner,
    *,
    softlock_threshold: int = SOFTLOCK_STEP_THRESHOLD,
) -> float:
    """Compute scalar reward from symbolic state dicts.

    Expected keys in ``state`` / ``prev_state``:
      - ``room_id`` (int or str)
      - ``hp`` (int)
      - ``inventory`` (list of item ids or names)
      - ``dead`` (bool, optional)
      - ``step`` (int, optional — env step counter)
    """
    reward = STEP_PENALTY

    prev_room = str(prev_state.get("room_id", ""))
    room = str(state.get("room_id", ""))
    target = planner.next_waypoint_room()

    if target is not None and room == str(target) and room != prev_room:
        reward += WAYPOINT_ROOM_BONUS
        planner.advance_if_success(state)
    elif target is not None and room != str(target) and room != prev_room:
        reward += WRONG_ROOM_PENALTY

    prev_inv = set(prev_state.get("inventory", []))
    cur_inv = set(state.get("inventory", []))
    new_items = cur_inv - prev_inv
    required = set(planner.required_items())
    for item in new_items:
        if not required or item in required:
            reward += ITEM_PICKUP_BONUS

    prev_hp = int(prev_state.get("hp", 0))
    hp = int(state.get("hp", 0))
    hp_delta = hp - prev_hp
    if hp_delta < 0:
        reward += HP_LOSS_SCALE * hp_delta

    if state.get("dead") or hp <= 0:
        reward += DEATH_PENALTY

    # Softlock: no room change for many steps while still alive
    if (
        room == prev_room
        and not state.get("dead")
        and int(state.get("step", 0)) > 0
        and int(state.get("step", 0)) % softlock_threshold == 0
    ):
        reward += SOFTLOCK_TIMEOUT_PENALTY

    return float(reward)
