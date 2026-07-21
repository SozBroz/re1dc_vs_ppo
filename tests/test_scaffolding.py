"""Offline tests for the v1 scaffolding stack (no emulator required).

Covers the acceptance criteria in docs/progress_scaffolding_design.md sec. 9:
obs shapes, compass sanity, PBRS sign, waypoint re-entry farm resistance.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.obs_encoder import GOAL_DIM, GOAL_FIELDS, PROPRIO_DIM, ObsEncoder, explain_obs, format_obs_table
from re1_rl.planner import OBJECTIVE_TYPES, WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import NEW_CUTSCENE_BONUS, NEW_ROOM_BONUS, compute_reward
from re1_rl.memory_map import IN_CONTROL_MASK
from re1_rl.room_graph import RoomGraph

ROOMS = PROJECT_ROOT / "data" / "rooms.json"
ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"

GOAL_IDX = {name: i for i, (name, _) in enumerate(GOAL_FIELDS)}


def make_state(room="105", x=30000, z=7500, facing=0, hp=96, step=1, **kw):
    s = {"room_id": room, "x": x, "y": 0, "z": z, "facing": facing, "hp": hp,
         "cam_id": 0, "character_id": 1, "in_control": True,
         "game_mode": IN_CONTROL_MASK, "game_state": 0,
         "scene_flag": 0, "msg_flag": 0, "stage_id": 0,
         "inventory": [], "dead": False, "step": step}
    if len(str(room)) >= 3 and str(room)[0].isdigit():
        s["room_byte"] = int(str(room)[2:], 16)
    s.update(kw)
    return s


def make_planner(waypoints=("106",), route_steps=None):
    return WaypointPlanner(ROUTE, waypoints=list(waypoints) if route_steps is None else None,
                           route_steps=list(route_steps) if route_steps else None)


def test_graph_loads_and_bfs():
    g = RoomGraph(DOORS)
    assert g.hop_distance("105", "106") == 1
    assert g.hop_distance("105", "105") == 0
    assert g.hop_distance("105", "999") is None
    door = g.exit_toward("105", "106")
    assert door is not None and door.to_room == "106"


def test_obs_shapes_and_finite():
    g = RoomGraph(DOORS)
    enc = ObsEncoder(ROOMS, g)
    planner = make_planner()
    s = make_state()
    proprio = enc.encode_proprio(s, prev_hp=96)
    goal = enc.encode_goal(s, planner)
    assert proprio.shape == (PROPRIO_DIM,)
    assert goal.shape == (GOAL_DIM,)
    assert np.all(np.isfinite(proprio)) and np.all(np.isfinite(goal))
    assert np.all(goal == 0.0)


def test_goal_vector_is_zeroed():
    g = RoomGraph(DOORS)
    enc = ObsEncoder(ROOMS, g)
    planner = make_planner()
    door = g.exit_toward("105", "106")
    s = make_state(x=door.x - 1000, z=door.z, facing=0)
    goal = enc.encode_goal(s, planner)
    assert np.all(goal == 0.0)


def test_new_room_bonus_once_per_episode():
    g = RoomGraph(DOORS)
    planner = make_planner()
    progress = ProgressTracker()
    in_105 = make_state(room="105", step=1)
    in_106 = make_state(room="106", step=2)
    progress.first_visit("105")
    # Legal 106 entry requires Kenneth ledger mark under the sole Kenneth gate.
    progress.rewarded_cutscenes.add("104:0:s0")

    _, bd0 = compute_reward(
        in_105, in_106, planner, progress=progress, graph=g, return_breakdown=True,
    )
    assert bd0["new_room"] == NEW_ROOM_BONUS
    assert bd0["waypoint"] == 0.0

    total = bd0["new_room"]
    prev, cur = in_106, in_105
    for _ in range(6):
        _, bd = compute_reward(
            prev, cur, planner, progress=progress, graph=g, return_breakdown=True,
        )
        total += bd["new_room"]
        prev, cur = cur, prev
    assert total == NEW_ROOM_BONUS


def test_new_cutscene_bonus_once_per_episode():
    from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES, qualify_cutscene_reward

    planner = make_planner()
    progress = ProgressTracker()
    prev = make_state(room="105", cam_id=2, step=1, hp=96, scene_flag=0x93)
    cur = make_state(room="105", cam_id=2, step=2, hp=96, scene_flag=0x91)
    key = qualify_cutscene_reward(
        skip_frames=MIN_CUTSCENE_SKIP_FRAMES,
        prev_state=prev,
        new_state=cur,
        episode_start_hp=96,
        rewarded_cutscenes=progress.rewarded_cutscenes,
    )
    assert key == "105:2:s0"
    cur["cutscene_key"] = key
    _, bd0 = compute_reward(
        prev, cur, planner, progress=progress, return_breakdown=True,
    )
    assert bd0["new_cutscene"] == NEW_CUTSCENE_BONUS

    cur2 = make_state(room="105", cam_id=2, step=3, cutscene_key="105:2:s0")
    _, bd1 = compute_reward(
        cur, cur2, planner, progress=progress, return_breakdown=True,
    )
    assert bd1["new_cutscene"] == 0.0

    # Pre-Kenneth Main Hall cinema is suppressed (poisoned Kenneth gate).
    prev_hall = make_state(room="106", cam_id=0, step=3, hp=96, scene_flag=0x93)
    cur_hall = make_state(room="106", cam_id=0, step=4, hp=96, scene_flag=0x91)
    assert (
        qualify_cutscene_reward(
            skip_frames=MIN_CUTSCENE_SKIP_FRAMES,
            prev_state=prev_hall,
            new_state=cur_hall,
            episode_start_hp=96,
            rewarded_cutscenes=progress.rewarded_cutscenes,
        )
        is None
    )

    # A distinct story beat in another room still pays once.
    prev3 = make_state(room="104", cam_id=0, step=5, hp=96, scene_flag=0x93)
    cur3 = make_state(room="104", cam_id=0, step=6, hp=96, scene_flag=0x91)
    key3 = qualify_cutscene_reward(
        skip_frames=MIN_CUTSCENE_SKIP_FRAMES,
        prev_state=prev3,
        new_state=cur3,
        episode_start_hp=96,
        rewarded_cutscenes=progress.rewarded_cutscenes,
    )
    assert key3 == "104:0:s0"
    cur3["cutscene_key"] = key3
    _, bd2 = compute_reward(
        cur2, cur3, planner, progress=progress, return_breakdown=True,
    )
    assert bd2["new_cutscene"] == NEW_CUTSCENE_BONUS


def test_checkpoint_path_shaping_disabled():
    g = RoomGraph(DOORS)
    planner = make_planner()
    door = g.exit_toward("105", "106")
    start = make_state(x=door.x - 2000, z=door.z, step=1)
    toward = make_state(x=door.x - 1000, z=door.z, step=2)
    away = make_state(x=door.x - 3000, z=door.z, step=2)
    _, bd_t = compute_reward(
        start, toward, planner, progress=ProgressTracker(), graph=g,
        return_breakdown=True,
    )
    _, bd_a = compute_reward(
        start, away, planner, progress=ProgressTracker(), graph=g,
        return_breakdown=True,
    )
    assert bd_t["pbrs_door"] == 0.0
    assert bd_a["pbrs_door"] == 0.0
    assert bd_t["waypoint"] == 0.0


def test_objective_one_hot():
    planner = make_planner(route_steps=[2])  # Kenneth cutscene = scripted_macro
    vec = planner.objective_one_hot()
    assert vec.shape == (len(OBJECTIVE_TYPES),)
    assert vec.sum() == 1.0
    assert vec[OBJECTIVE_TYPES.index("scripted_macro")] == 1.0


def test_goal_usage_hints_disabled_with_zero_goal():
    from re1_rl.item_todo import ItemTracker

    g = RoomGraph(DOORS)
    enc = ObsEncoder(ROOMS, g)
    planner = make_planner(route_steps=[11])
    tracker = ItemTracker(todo=[])
    s = make_state(room="10C")
    goal = enc.encode_goal(s, planner, item_tracker=tracker)
    assert np.all(goal == 0.0)


def test_goal_puzzle_macro_disabled_with_zero_goal():
    g = RoomGraph(DOORS)
    enc = ObsEncoder(ROOMS, g)
    s = make_state(room="107")
    goal_macro = enc.encode_goal(s, make_planner(route_steps=[9]))
    goal_plain = enc.encode_goal(s, make_planner(route_steps=[2]))
    assert np.all(goal_macro == 0.0)
    assert np.all(goal_plain == 0.0)


def test_explain_obs_names_every_slot():
    g = RoomGraph(DOORS)
    enc = ObsEncoder(ROOMS, g)
    planner = make_planner()
    s = make_state()
    obs = {
        "frame": np.zeros((84, 77, 4), dtype=np.uint8),
        "proprio": enc.encode_proprio(s, prev_hp=96),
        "goal": enc.encode_goal(s, planner),
    }
    ex = explain_obs(obs)
    assert len(ex["proprio"]) == PROPRIO_DIM
    assert len(ex["goal"]) == GOAL_DIM
    table = format_obs_table(obs)
    assert "door_bearing_cos" in table and "hp" in table


def test_damage_and_death_calibrated_to_waypoint():
    from re1_rl.reward import (
        CHECKPOINT_REWARD,
        DEATH_PENALTY_SCALED,
        HP_GAIN_SCALE,
        HP_LOSS_SCALE,
        JILL_FINE_HP,
        NEAR_DEATH_DAMAGE_SCALED,
        REWARD_SCALE,
        SOFTLOCK_TIMEOUT_PENALTY,
        STEPS_PER_CHECKPOINT,
        STEP_PENALTY,
        SURVIVAL_BUDGET_SCALED,
        WAYPOINT_ROOM_BONUS,
        compute_reward,
    )

    planner = make_planner()
    progress = ProgressTracker()
    assert CHECKPOINT_REWARD == 1.2
    assert NEW_ROOM_BONUS == pytest.approx(12.0)
    assert NEW_CUTSCENE_BONUS == pytest.approx(6.0)
    assert WAYPOINT_ROOM_BONUS == NEW_ROOM_BONUS
    assert STEP_PENALTY * REWARD_SCALE == pytest.approx(-CHECKPOINT_REWARD / STEPS_PER_CHECKPOINT)
    assert SURVIVAL_BUDGET_SCALED == pytest.approx(1.0 * CHECKPOINT_REWARD)
    assert NEAR_DEATH_DAMAGE_SCALED == pytest.approx((2.0 / 3.0) * CHECKPOINT_REWARD)
    assert DEATH_PENALTY_SCALED == pytest.approx((1.0 / 3.0) * CHECKPOINT_REWARD)
    assert SOFTLOCK_TIMEOUT_PENALTY == pytest.approx(-DEATH_PENALTY_SCALED / 5.0)
    assert HP_GAIN_SCALE == pytest.approx(HP_LOSS_SCALE)

    full = make_state(hp=JILL_FINE_HP, step=1)
    near_death = make_state(hp=1, step=2)
    _, bd_chip = compute_reward(
        full, near_death, planner, progress=progress, return_breakdown=True,
    )
    assert bd_chip["hp"] * REWARD_SCALE == pytest.approx(-NEAR_DEATH_DAMAGE_SCALED)

    dead_prev = make_state(hp=JILL_FINE_HP, step=10)
    dead_now = make_state(hp=0, step=11, dead=True)
    _, bd_death = compute_reward(
        dead_prev, dead_now, planner, progress=ProgressTracker(),
        return_breakdown=True,
    )
    assert bd_death["death"] * REWARD_SCALE == pytest.approx(-DEATH_PENALTY_SCALED)
    assert bd_death["hp"] * REWARD_SCALE == pytest.approx(
        -NEAR_DEATH_DAMAGE_SCALED * JILL_FINE_HP / (JILL_FINE_HP - 1)
    )

    # Full Fine→1 chip then full heal: exact inverse of the chip magnitude.
    hurt = make_state(hp=1, step=20)
    healed = make_state(hp=JILL_FINE_HP, step=21)
    _, bd_heal = compute_reward(
        hurt, healed, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd_heal["hp"] * REWARD_SCALE == pytest.approx(NEAR_DEATH_DAMAGE_SCALED)

    from re1_rl.reward import hp_heal_reward

    assert hp_heal_reward(10) == pytest.approx(HP_GAIN_SCALE * 10.0)
    assert hp_heal_reward(80) == pytest.approx(HP_GAIN_SCALE * 80.0)
    assert hp_heal_reward(10) == pytest.approx(-HP_LOSS_SCALE * (-10.0))


def test_rl_gamma_half_life_includes_step_contempt():
    """γ_eff := γ + STEP_PENALTY; half-life ≈ 45s at 8-frame @ 60fps steps."""
    from re1_rl.reward import REFERENCE_STEP_FRAMES, RL_GAMMA, STEP_PENALTY

    dt_s = REFERENCE_STEP_FRAMES / 60.0
    n_steps_45s = 45.0 / dt_s  # 337.5
    assert n_steps_45s == pytest.approx(337.5)
    assert STEP_PENALTY == pytest.approx(-0.00024)
    assert RL_GAMMA == 0.998188

    gamma_eff = RL_GAMMA + STEP_PENALTY
    half_life_steps = math.log(0.5) / math.log(gamma_eff)
    half_life_s = half_life_steps * dt_s
    assert half_life_s == pytest.approx(45.0, abs=0.02)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
