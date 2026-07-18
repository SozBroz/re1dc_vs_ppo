"""Shaped reward for hierarchical RE1 control.

Exploration mode (checkpoint path disabled):
  - +CHECKPOINT_REWARD once per new room entered per episode
  - +CHECKPOINT_REWARD once per same-room scripted cutscene (room:cam:sN) per episode
  - Room-change door skips do not pay new_cutscene (discovery is new_room only)
  - Goal-vector checkpoint compass is zeroed in obs (see obs_encoder.encode_goal)
  - No waypoint / PBRS / wrong-room / retreat / success_room shaping
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np

from re1_rl.item_todo import canonical_item
from re1_rl.key_items import KEY_ITEM_NAMES
from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS

if TYPE_CHECKING:
    from re1_rl.planner import WaypointPlanner
    from re1_rl.progress import ProgressTracker
    from re1_rl.room_graph import RoomGraph

_KEY_ITEM_NAME_SET: frozenset[str] = frozenset(KEY_ITEM_NAMES)
_WEAPON_NAME_SET: frozenset[str] = frozenset(
    ITEM_IDS[i] for i in WEAPON_ITEM_IDS if i in ITEM_IDS
)

# Human-scale reward units: one route checkpoint = +1.0; step = 1/4000 of that.
CHECKPOINT_REWARD = 1.0
STEPS_PER_CHECKPOINT = 5000

STEP_PENALTY = -CHECKPOINT_REWARD / STEPS_PER_CHECKPOINT  # -0.0002
REFERENCE_STEP_FRAMES = 8

# Exploration bonuses (same scale as the old per-waypoint checkpoint payout).
NEW_ROOM_BONUS = CHECKPOINT_REWARD
NEW_CUTSCENE_BONUS = CHECKPOINT_REWARD

# Legacy aliases kept for tests / telemetry that import old names.
WAYPOINT_ROOM_BONUS = NEW_ROOM_BONUS

# Junk / ammo / herbs: meaningful but well below a new room/cutscene.
ITEM_PICKUP_BONUS = 0.15 * CHECKPOINT_REWARD
# Keys / emblems / crests (room_items.json key_item=true).
KEY_ITEM_PICKUP_BONUS = 0.5 * CHECKPOINT_REWARD
# Story inventory USE at a curated site (piano, fireplace, …).
STORY_ITEM_USE_BONUS = CHECKPOINT_REWARD
# 10F alcove: put gold_emblem back without leaving the wooden emblem (anti-hack).
# Intended path is USE emblem (wooden) at the same stand → STORY_ITEM_USE_BONUS.
GOLD_EMBLEM_RETURN_PENALTY = -2.0 * CHECKPOINT_REWARD
# Every physical pickup of a gun/knife-class weapon (not ammo).
NEW_WEAPON_PICKUP_BONUS = CHECKPOINT_REWARD
# The wall rack can toggle forever: taking the shotgun pays; replacing it
# removes exactly that reward. Repeating the loop is net zero before step cost.
SHOTGUN_RETURN_PENALTY = -NEW_WEAPON_PICKUP_BONUS
SHOTGUN_RACK_ROOMS: frozenset[str] = frozenset({"115", "116"})
# Idle contempt: no new room / cutscene / key item / weapon for SOFTLOCK_FRAME_THRESHOLD
# emulated frames → episode truncation (env). Bulk softlock at timeout only
# (spread across n_steps on the learner; no per-step stagnant tax).
# 43200 = 12 min wall-clock @ 60 emulated fps (PS1 NTSC / BizHawk).
SOFTLOCK_FRAME_THRESHOLD = 12 * 60 * 60

JILL_FINE_HP = 96
# Survival budget: full Fine→1 chip + death terminal = −1× checkpoint.
# 2/3 on dense HP loss, 1/3 on episode-end death.
SURVIVAL_BUDGET_SCALED = 1.0 * CHECKPOINT_REWARD
NEAR_DEATH_DAMAGE_SCALED = (2.0 / 3.0) * SURVIVAL_BUDGET_SCALED  # ≈0.6667
DEATH_PENALTY_SCALED = (1.0 / 3.0) * SURVIVAL_BUDGET_SCALED  # ≈0.3333
DEATH_PENALTY = -DEATH_PENALTY_SCALED
# Sole Kenneth gate: illegal pre-Kenneth transition into Main Hall room 106.
MAIN_HALL_BEFORE_KENNETH_PENALTY = -3.0
# Doing-nothing contempt must not exceed death (else suicide beats softlock).
CONTEMPT_BUDGET_SCALED = DEATH_PENALTY_SCALED
SOFTLOCK_TIMEOUT_PENALTY = -CONTEMPT_BUDGET_SCALED

ENEMY_DAMAGE_REWARD = CHECKPOINT_REWARD / 200
ENEMY_KILL_REWARD = 0.2 * CHECKPOINT_REWARD
# Legacy names kept for imports/tests; miss penalties disabled (step scale only).
ATTACK_MISS_PENALTY = 0.0
KNIFE_MISS_PENALTY = 0.0
AMMO_WASTE_PENALTY = 0.0

REWARD_SCALE = 1.0

# Dual discount: dense/main rewards at RL_GAMMA; softlock spread per env-step @ γ=1
# so MC sums to the full contempt lump over the digest horizon (n_steps).
RL_GAMMA = 0.99
SOFTLOCK_GAMMA = 1.0

HP_LOSS_SCALE = NEAR_DEATH_DAMAGE_SCALED / (JILL_FINE_HP - 1)
# Heal recovers ~80% of the damage channel so chip-then-herb is not free.
HP_GAIN_SCALE = 0.8 * HP_LOSS_SCALE
# Log-shaped heal: small chips earn far less than linear; full Fine heal unchanged.
HEAL_LOG_CURVE_EXPONENT = 6.0


def hp_heal_reward(hp_delta: int) -> float:
    """Heal reward with log compression on small amounts; caps at linear full heal."""
    if hp_delta <= 0:
        return 0.0
    cap_delta = float(JILL_FINE_HP - 1)
    d = min(float(hp_delta), cap_delta)
    log_ratio = math.log1p(d) / math.log1p(cap_delta)
    return HP_GAIN_SCALE * cap_delta * (log_ratio ** HEAL_LOG_CURVE_EXPONENT)

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


def stagnation_episode_timeout(
    progress: ProgressTracker | None,
    *,
    threshold: int = SOFTLOCK_FRAME_THRESHOLD,
) -> bool:
    """True when idle frames hit the stagnation episode cap (caller sets truncated)."""
    if progress is None:
        return False
    return progress.stagnation_timed_out(threshold=threshold)


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
    softlock_threshold: int = SOFTLOCK_FRAME_THRESHOLD,
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
        "key_item": 0.0,
        "story_use": 0.0,
        "gallery": 0.0,
        "gold_emblem_return": 0.0,
        "shotgun_return": 0.0,
        "new_weapon": 0.0,
        "success_room": 0.0,
        "hp": 0.0,
        "death": 0.0,
        "main_hall_before_kenneth": 0.0,
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

    illegal_main_hall = False
    if progress is not None:
        from re1_rl.cutscene_reward import (
            illegal_main_hall_before_kenneth_transition,
        )

        illegal_main_hall = illegal_main_hall_before_kenneth_transition(
            prev_room,
            room,
            rewarded_cutscenes=progress.rewarded_cutscenes,
            visited_rooms=progress.visited_rooms,
        )

    if room_changed and is_new_room and not illegal_main_hall:
        bd["new_room"] = NEW_ROOM_BONUS

    cutscene_key = state.get("cutscene_key")
    if cutscene_key and progress is not None:
        if progress.claim_cutscene_bonus(str(cutscene_key)):
            bd["new_cutscene"] = NEW_CUTSCENE_BONUS

    if "new_items" in state:
        new_items = set(state["new_items"])
    else:
        new_items = set(state.get("inventory", [])) - set(prev_state.get("inventory", []))
    for raw in new_items:
        name = canonical_item(str(raw))
        if name in _KEY_ITEM_NAME_SET:
            bd["key_item"] += KEY_ITEM_PICKUP_BONUS
        elif name in _WEAPON_NAME_SET:
            bd["new_weapon"] += NEW_WEAPON_PICKUP_BONUS
        else:
            bd["item"] += ITEM_PICKUP_BONUS

    prev_inventory = {
        canonical_item(str(name)) for name in prev_state.get("inventory", [])
    }
    inventory = {
        canonical_item(str(name)) for name in state.get("inventory", [])
    }
    if progress is not None:
        bd["gallery"] = progress.gallery_step_reward(
            prev_room=prev_room,
            room=room,
            prev_raw=int(prev_state.get("gallery_progress", 0) or 0),
            raw=int(state.get("gallery_progress", 0) or 0),
            prev_confirm=int(prev_state.get("gallery_confirm", 0) or 0),
            confirm=int(state.get("gallery_confirm", 0) or 0),
            star_crest_held="star_crest" in inventory,
        )
    room = str(state.get("room_id", "") or "")
    shotgun_removed_at_rack = (
        room in SHOTGUN_RACK_ROOMS
        and "shotgun" in prev_inventory
        and "shotgun" not in inventory
        and not state.get("dead")
        and int(state.get("hp", 0) or 0) > 0
    )
    if progress is not None:
        if progress._shotgun_return_armed is None:
            progress._shotgun_return_armed = "shotgun" in prev_inventory
        if "shotgun" in inventory:
            progress._shotgun_return_armed = True
        shotgun_removed_at_rack = (
            shotgun_removed_at_rack and progress._shotgun_return_armed
        )
    if shotgun_removed_at_rack:
        bd["shotgun_return"] = SHOTGUN_RETURN_PENALTY
        if progress is not None:
            progress._shotgun_return_armed = False

    story_use_site = state.get("story_use_success")
    if story_use_site and progress is not None:
        if progress.claim_story_use_bonus(str(story_use_site)):
            bd["story_use"] = STORY_ITEM_USE_BONUS

    if state.get("gold_emblem_return"):
        bd["gold_emblem_return"] = GOLD_EMBLEM_RETURN_PENALTY

    prev_hp = int(prev_state.get("hp", 0))
    hp = int(state.get("hp", 0))
    hp_delta = hp - prev_hp
    if hp_delta < 0:
        bd["hp"] = HP_LOSS_SCALE * hp_delta
    elif hp_delta > 0 and prev_hp > 0:
        # Ignore bogus HP jumps from menu/cutscene init (prev_hp==0).
        bd["hp"] = hp_heal_reward(hp_delta)

    # Actual death owns the ordinary death channel. Otherwise the sole Kenneth
    # gate contributes exactly -3.0 once under its explicit telemetry key.
    if state.get("dead"):
        bd["death"] = DEATH_PENALTY
    elif illegal_main_hall:
        bd["main_hall_before_kenneth"] = MAIN_HALL_BEFORE_KENNETH_PENALTY

    enemy_damage = int(state.get("enemy_damage", 0) or 0)
    if enemy_damage > 0:
        bd["enemy_damage"] = ENEMY_DAMAGE_REWARD * enemy_damage
    enemy_kills = int(state.get("enemy_kills", 0) or 0)
    if enemy_kills > 0:
        bd["enemy_kill"] = ENEMY_KILL_REWARD * enemy_kills

    if progress is not None and not state.get("dead") and not illegal_main_hall:
        made_progress = (
            bd["new_room"] != 0.0
            or bd["new_cutscene"] != 0.0
            or bd["key_item"] != 0.0
            or bd["story_use"] != 0.0
            or bd["gallery"] > 0.0
            or bd["new_weapon"] != 0.0
        )
        # Pause idle clock during cutscenes / doors (not in_control).
        if made_progress or bool(state.get("in_control", True)):
            progress.note_stagnation_step(
                made_progress=made_progress,
                step_frames=step_frames,
            )
        if progress.stagnation_timed_out(threshold=softlock_threshold):
            bd["softlock"] = SOFTLOCK_TIMEOUT_PENALTY

    reward = float(sum(bd.values())) * REWARD_SCALE
    if return_breakdown:
        return reward, bd
    return reward


def softlock_reward_from_breakdown(breakdown: dict[str, float] | None) -> float:
    """Scaled softlock channel contribution (0 when absent)."""
    if not breakdown:
        return 0.0
    return float(breakdown.get("softlock", 0.0)) * REWARD_SCALE


def spread_softlock_contempt_over_horizon(
    rewards: np.ndarray,
    rewards_softlock: np.ndarray,
    dones: np.ndarray,
    *,
    horizon: int,
) -> None:
    """Spread terminal softlock lumps uniformly over the last ``horizon`` env-steps.

    Mutates arrays in place. Keeps ``rewards - rewards_softlock`` (main channel)
    unchanged while the softlock channel sums to the original lump per segment.
    """
    if horizon <= 0:
        return
    rewards = np.asarray(rewards, dtype=np.float32)
    rewards_softlock = np.asarray(rewards_softlock, dtype=np.float32)
    dones = np.asarray(dones, dtype=np.bool_)
    n_steps, n_envs = rewards.shape
    for env in range(n_envs):
        for t in range(n_steps):
            lump = float(rewards_softlock[t, env])
            if lump >= 0.0:
                continue
            seg_start = 0
            for k in range(t - 1, -1, -1):
                if dones[k, env]:
                    seg_start = k + 1
                    break
            win_start = max(seg_start, t - horizon + 1)
            span = t - win_start + 1
            per = lump / span
            for k in range(win_start, t):
                rewards_softlock[k, env] += per
                rewards[k, env] += per
            rewards_softlock[t, env] = per
            rewards[t, env] += per - lump
