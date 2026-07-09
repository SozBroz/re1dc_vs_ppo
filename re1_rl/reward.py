"""Shaped reward for hierarchical RE1 control.

Exploration mode (checkpoint path disabled):
  - +CHECKPOINT_REWARD once per new room entered per episode
  - +CHECKPOINT_REWARD once per new cutscene (room:cam skip segment) per episode
  - Goal-vector checkpoint compass is zeroed in obs (see obs_encoder.encode_goal)
  - No waypoint / PBRS / wrong-room / retreat / success_room shaping
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from re1_rl.planner import WaypointPlanner
    from re1_rl.progress import ProgressTracker
    from re1_rl.room_graph import RoomGraph

# Human-scale reward units: one route checkpoint = +1.0; step = 1/4000 of that.
CHECKPOINT_REWARD = 1.0
STEPS_PER_CHECKPOINT = 5000

STEP_PENALTY = -CHECKPOINT_REWARD / STEPS_PER_CHECKPOINT  # -0.0002
REFERENCE_STEP_FRAMES = 4

# Exploration bonuses (same scale as the old per-waypoint checkpoint payout).
NEW_ROOM_BONUS = CHECKPOINT_REWARD
NEW_CUTSCENE_BONUS = CHECKPOINT_REWARD

# Legacy aliases kept for tests / telemetry that import old names.
WAYPOINT_ROOM_BONUS = NEW_ROOM_BONUS

ITEM_PICKUP_BONUS = CHECKPOINT_REWARD / 20
SOFTLOCK_TIMEOUT_PENALTY = -2.5 * CHECKPOINT_REWARD
SOFTLOCK_STEP_THRESHOLD = 2400

ENEMY_DAMAGE_REWARD = CHECKPOINT_REWARD / 200
ENEMY_KILL_REWARD = CHECKPOINT_REWARD / 50
ATTACK_MISS_PENALTY = STEP_PENALTY
KNIFE_MISS_PENALTY = ATTACK_MISS_PENALTY
AMMO_WASTE_PENALTY = 2.0 * STEP_PENALTY

JILL_FINE_HP = 96
NEAR_DEATH_DAMAGE_SCALED = 0.5 * CHECKPOINT_REWARD
DEATH_PENALTY_SCALED = 7.0 * CHECKPOINT_REWARD
DEATH_PENALTY = -DEATH_PENALTY_SCALED

REWARD_SCALE = 1.0

HP_LOSS_SCALE = NEAR_DEATH_DAMAGE_SCALED / (JILL_FINE_HP - 1)

# Disabled checkpoint-path terms (exported for tests that assert they stay off).
WRONG_ROOM_PENALTY = -0.5 * CHECKPOINT_REWARD
RETREAT_PENALTY = -0.5 * CHECKPOINT_REWARD
SUCCESS_ROOM_BONUS = 100.0 * CHECKPOINT_REWARD
PBRS_GRAPH_WEIGHT = CHECKPOINT_REWARD / 20
PBRS_DOOR_WEIGHT = 0.5 * CHECKPOINT_REWARD
SHAPING_GAMMA = 1.0
UNKNOWN_HOPS = 8.0
DIST_NORM = 4096.0

# When False, compute_reward ignores checkpoint-path shaping and planner advances.
ENABLE_CHECKPOINT_PATH = False


def potential(
    state: dict[str, Any],
    planner: WaypointPlanner,
    graph: RoomGraph | None,
) -> tuple[float, float]:
    """(phi_graph, phi_door) for a state. Higher = closer to objective."""
    if not ENABLE_CHECKPOINT_PATH or graph is None:
        return 0.0, 0.0
    room = str(state.get("room_id", ""))
    goal = planner.next_waypoint_room()
    if goal is None:
        return 0.0, 0.0

    hops = graph.hop_distance(room, str(goal))
    if hops is None:
        phi_g = -max(UNKNOWN_HOPS, float(graph.diameter) + 2.0)
    else:
        phi_g = -float(hops)

    phi_d = 0.0
    door = graph.exit_toward(room, str(goal))
    if door is not None and "x" in state and "z" in state:
        dist = math.hypot(door.x - state["x"], door.z - state["z"])
        phi_d = -min(dist / DIST_NORM, 1.0)

    return PBRS_GRAPH_WEIGHT * phi_g, PBRS_DOOR_WEIGHT * phi_d


def compute_reward(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    planner: WaypointPlanner,
    *,
    progress: ProgressTracker | None = None,
    graph: RoomGraph | None = None,
    softlock_threshold: int = SOFTLOCK_STEP_THRESHOLD,
    success_room: str | None = None,
    return_breakdown: bool = False,
) -> float | tuple[float, dict[str, float]]:
    """Compute scalar reward from symbolic state dicts."""
    del success_room  # checkpoint success_room bonus disabled

    step_frames = int(state.get("step_emulated_frames", REFERENCE_STEP_FRAMES))
    ref_frames = int(state.get("reference_step_frames", REFERENCE_STEP_FRAMES))
    step_scale = max(step_frames, 0) / max(ref_frames, 1)

    bd: dict[str, float] = {
        "step": STEP_PENALTY * step_scale,
        "pbrs_graph": 0.0,
        "pbrs_door": 0.0,
        "waypoint": 0.0,
        "new_room": 0.0,
        "new_cutscene": 0.0,
        "retreat": 0.0,
        "wrong_room": 0.0,
        "item": 0.0,
        "success_room": 0.0,
        "hp": 0.0,
        "death": 0.0,
        "softlock": 0.0,
        "enemy_damage": 0.0,
        "enemy_kill": 0.0,
        "attack_miss": 0.0,
        "ammo_waste": 0.0,
    }

    prev_room = str(prev_state.get("room_id", ""))
    room = str(state.get("room_id", ""))
    room_changed = room != prev_room

    is_new_room = False
    if progress is not None:
        is_new_room = progress.first_visit(
            room,
            at_waypoint=0,
            at_route_seq=None,
        )

    if ENABLE_CHECKPOINT_PATH and graph is not None:
        pg_prev, pd_prev = potential(prev_state, planner, graph)
        pg_now, pd_now = potential(state, planner, graph)
        bd["pbrs_graph"] = SHAPING_GAMMA * pg_now - pg_prev
        bd["pbrs_door"] = SHAPING_GAMMA * pd_now - pd_prev

        while True:
            completed_idx = planner.waypoint_index
            if not planner.advance_if_success(
                state, progress=progress, prev_state=prev_state
            ):
                break
            if progress is not None:
                progress.on_waypoint_advanced()
            claimed = progress.claim_waypoint_bonus(completed_idx) \
                if progress is not None else True
            if claimed:
                bd["waypoint"] += WAYPOINT_ROOM_BONUS

        target = planner.next_waypoint_room()
        if room_changed and target is not None:
            if prev_room == str(target) and room != str(target) \
                    and planner.next_waypoint_room() == str(target):
                bd["retreat"] = RETREAT_PENALTY
            elif room != str(target):
                off_route = graph.hop_distance(room, str(target)) is None
                if off_route and graph.knows_room(str(target)):
                    claimed = progress.claim_offroute_penalty(room) \
                        if progress is not None else True
                    if claimed:
                        bd["wrong_room"] = WRONG_ROOM_PENALTY

    if room_changed and is_new_room:
        bd["new_room"] = NEW_ROOM_BONUS

    cutscene_key = state.get("cutscene_key")
    if cutscene_key and progress is not None:
        if progress.claim_cutscene_bonus(str(cutscene_key)):
            bd["new_cutscene"] = NEW_CUTSCENE_BONUS

    if "new_items" in state:
        new_items = set(state["new_items"])
    else:
        new_items = set(state.get("inventory", [])) - set(prev_state.get("inventory", []))
    for _item in new_items:
        bd["item"] += ITEM_PICKUP_BONUS

    prev_hp = int(prev_state.get("hp", 0))
    hp = int(state.get("hp", 0))
    hp_delta = hp - prev_hp
    if hp_delta < 0:
        bd["hp"] = HP_LOSS_SCALE * hp_delta

    if state.get("dead"):
        bd["death"] = DEATH_PENALTY

    enemy_damage = int(state.get("enemy_damage", 0) or 0)
    if enemy_damage > 0:
        bd["enemy_damage"] = ENEMY_DAMAGE_REWARD * enemy_damage
    enemy_kills = int(state.get("enemy_kills", 0) or 0)
    if enemy_kills > 0:
        bd["enemy_kill"] = ENEMY_KILL_REWARD * enemy_kills

    attack_missed = bool(
        state.get("attack_missed") or state.get("knife_swing_missed")
    )
    if attack_missed:
        bd["attack_miss"] = ATTACK_MISS_PENALTY
        ammo_spent = int(state.get("ammo_spent", 0) or 0)
        if ammo_spent > 0:
            bd["ammo_waste"] = AMMO_WASTE_PENALTY * min(ammo_spent, 4)

    if (
        room == prev_room
        and not state.get("dead")
        and int(state.get("step", 0)) > 0
        and int(state.get("step", 0)) % softlock_threshold == 0
    ):
        bd["softlock"] = SOFTLOCK_TIMEOUT_PENALTY

    reward = float(sum(bd.values())) * REWARD_SCALE
    if return_breakdown:
        return reward, bd
    return reward
