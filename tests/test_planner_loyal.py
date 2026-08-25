"""Unit tests for planner-loyal queue + encoding."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from re1_rl.env import RE1Env
from re1_rl.planner import WaypointPlanner
from re1_rl.planner_loyal import (
    HEAL_USE_TAX_LIGHT,
    PLANNER_DIVERT_PENALTY,
    PLANNER_QUEUE_DIM,
    PLANNER_STEP_SUCCESS_REWARD,
    PlannerLoyalQueue,
    apply_planner_loyal_obs,
    encode_planner_queue,
    load_chunk,
    planner_loyal_enabled,
    prune_route_admin_goal,
)
from re1_rl.room_graph import RoomGraph
from re1_rl.progress import ProgressTracker
from re1_rl.reward import STEP_PENALTY, compute_reward

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"


def _planner() -> WaypointPlanner:
    return WaypointPlanner(ROUTE, waypoints=["105"])


def _reward(prev, cur, queue, *, progress=None, box_opened=False):
    return compute_reward(
        prev,
        cur,
        _planner(),
        progress=progress,
        planner_loyal_queue=queue,
        box_opened=box_opened,
        return_breakdown=True,
    )


def test_load_cp05_chunk_has_emblem_swap_and_clips():
    chunk = load_chunk()
    steps = chunk["steps"]
    pickups = [s.get("pickup_id") for s in steps]
    sites = [s.get("site_id") for s in steps]
    assert "104:handgun_bullets:1" in pickups
    assert "104:handgun_bullets:2" in pickups
    # Tip already holds wooden emblem — never re-acquire in this chunk.
    assert not any(
        str(p or "").startswith("105:emblem") for p in pickups
    )
    assert "emblem@10F_alcove" in sites
    assert steps[-1]["pickup_id"].startswith("105:shield_key")


def test_queue_pops_on_correct_traverse():
    q = PlannerLoyalQueue()
    assert q.current["edge_id"] == "106->105"
    prev = {"room_id": "106", "inventory_slots": []}
    cur = {"room_id": "105", "inventory_slots": []}
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is True
    assert q.current["edge_id"] == "105->104"


def test_queue_divert_on_wrong_room():
    q = PlannerLoyalQueue()
    prev = {"room_id": "106", "inventory_slots": []}
    cur = {"room_id": "107", "inventory_slots": []}
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is True


def test_ink_ribbon_pickup_does_not_divert_on_traverse():
    q = PlannerLoyalQueue()
    prev = {"room_id": "106", "inventory_slots": []}
    cur = {
        "room_id": "106",
        "inventory_slots": [{"name": "ink_ribbon"}],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is False
    assert q.current["edge_id"] == "106->105"


def test_already_held_acquire_is_skipped():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "test",
            "steps": [
                {"n": 1, "op": "acquire", "pickup_id": "106:ink_ribbon:1"},
                {"n": 2, "op": "traverse", "edge_id": "106->105"},
            ],
        }
    )
    q.note_start_inventory(
        {"inventory_slots": [{"name": "ink_ribbon"}]}
    )
    prev = {
        "room_id": "106",
        "inventory_slots": [{"name": "ink_ribbon"}],
    }
    cur = {
        "room_id": "105",
        "inventory_slots": [{"name": "ink_ribbon"}],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is True
    assert q.done is True


def test_planner_loyal_curriculum_is_not_yawn_rails():
    import json

    stage = json.loads(
        (PROJECT_ROOT / "curriculum" / "planner_loyal_one_leg.json").read_text(
            encoding="utf-8"
        )
    )
    assert stage["mode"] == "planner_loyal"
    assert stage["route_steps"] == []


def test_reset_wrapper_skips_yawn_sampler_when_planner_loyal(monkeypatch):
    import gymnasium as gym
    from gymnasium import spaces

    from re1_rl.go_explore_reset_wrapper import GoExploreResetWrapper

    sampled: list[int] = []
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    monkeypatch.setattr(
        "re1_rl.yawn_rails.sample_one_leg_options",
        lambda *a, **k: sampled.append(1) or {"route_start_index": 121},
    )

    class _StubEnv(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self) -> None:
            self.observation_space = spaces.Discrete(1)
            self.action_space = spaces.Discrete(1)
            self.curriculum_path = (
                PROJECT_ROOT / "curriculum" / "yawn_rails_one_leg.json"
            )
            self.project_root = PROJECT_ROOT
            self.last_options = None

        def reset(self, *, seed=None, options=None):
            self.last_options = options
            return 0, {}

        def step(self, action):
            return 0, 0.0, False, False, {}

    inner = _StubEnv()
    wrapper = GoExploreResetWrapper(inner, project_root=PROJECT_ROOT)
    wrapper.reset()
    assert sampled == []
    assert "route_start_index" not in (inner.last_options or {})


def test_encode_dim_stable():
    q = PlannerLoyalQueue()
    vec = encode_planner_queue(q)
    assert len(vec) == PLANNER_QUEUE_DIM
    assert vec[0] == 1.0


def test_reward_step_success_pays_eight_and_pops():
    q = PlannerLoyalQueue()
    progress = ProgressTracker(leg_span=1)
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_step_success"] == PLANNER_STEP_SUCCESS_REWARD
    assert bd["checkpoint_success"] == PLANNER_STEP_SUCCESS_REWARD
    assert q.current["edge_id"] == "105->104"
    assert progress.checkpoint_success is True
    assert reward == STEP_PENALTY + PLANNER_STEP_SUCCESS_REWARD
    # Mid-chunk rearms a fresh 12m wall after the pulse.
    from re1_rl.yawn_cell_timeout import FLAT_CELL_TIMEOUT_FRAMES

    assert progress.cell_timeout_frames == FLAT_CELL_TIMEOUT_FRAMES
    assert progress.leg_emulated_frames == 0


def test_reward_step_success_scales_with_leftover_time():
    q = PlannerLoyalQueue()
    progress = ProgressTracker(leg_span=1)
    from re1_rl.yawn_cell_timeout import FLAT_CELL_TIMEOUT_FRAMES

    progress.arm_cell_timeout(1000)
    progress.note_leg_frames(500)
    prev = {
        "room_id": "106",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
    }
    cur = {
        "room_id": "105",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "step_emulated_frames": 0,
    }
    _reward_total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_step_success"] == pytest.approx(4.0)
    assert bd["checkpoint_success"] == pytest.approx(4.0)
    # Next step gets a fresh full 12m budget.
    assert progress.cell_timeout_frames == FLAT_CELL_TIMEOUT_FRAMES
    assert progress.leg_emulated_frames == 0


def test_reward_wrong_room_pays_minus_four_and_is_terminal():
    q = PlannerLoyalQueue()
    progress = ProgressTracker()
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "107", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["wrong_room"] == PLANNER_DIVERT_PENALTY
    assert progress.wrong_room_breached is True
    assert reward == STEP_PENALTY + PLANNER_DIVERT_PENALTY
    terminated, _truncated, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "yawn_rails", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=False,
            _episode_failure_override=None,
            _planner_loyal_queue=q,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "planner_divert"


def test_rails_mode_loyal_traverse_does_not_breach_wrong_room():
    """106→105 must credit planner_step_success even if legacy planner is on 210."""
    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    yawn_route = PROJECT_ROOT / "data" / "yawn_checkpoint_route.json"
    planner = WaypointPlanner(
        yawn_route,
        route_steps=list(range(1, 123)),
        start_index=121,
    )
    assert planner.next_waypoint_room() == "210"
    q = PlannerLoyalQueue()
    progress = ProgressTracker(leg_span=1)
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = compute_reward(
        prev,
        cur,
        planner,
        progress=progress,
        graph=graph,
        rails_mode=True,
        planner_loyal_queue=q,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == 0.0
    assert progress.wrong_room_breached is False
    assert bd["planner_step_success"] == PLANNER_STEP_SUCCESS_REWARD
    assert bd["checkpoint_success"] == PLANNER_STEP_SUCCESS_REWARD
    assert reward == STEP_PENALTY + PLANNER_STEP_SUCCESS_REWARD


def test_divert_alias_net_is_minus_four_under_rails_mode():
    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    planner = WaypointPlanner(ROUTE, waypoints=["210"])
    q = PlannerLoyalQueue()
    progress = ProgressTracker()
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "107", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = compute_reward(
        prev,
        cur,
        planner,
        progress=progress,
        graph=graph,
        rails_mode=True,
        planner_loyal_queue=q,
        return_breakdown=True,
    )
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["wrong_room"] == PLANNER_DIVERT_PENALTY
    assert reward == STEP_PENALTY + PLANNER_DIVERT_PENALTY


def test_episode_failure_context_includes_divert_and_target():
    q = PlannerLoyalQueue()
    q.divert_reason = "unplanned_pickup:['ink_ribbon']"
    env = SimpleNamespace(
        _planner_loyal_queue=q,
        _planner=SimpleNamespace(next_waypoint_room=lambda: "210"),
    )
    ctx = RE1Env._episode_failure_context(env, "planner_divert")
    assert ctx["planner_divert_reason"] == "unplanned_pickup:['ink_ribbon']"
    assert ctx["failure_target"] == "105"
    assert RE1Env._episode_failure_context(env, None) == {}


def test_reward_heal_use_tax_fires():
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "106",
        "inventory_slots": [{"name": "green_herb"}],
        "hp": 40,
        "in_control": True,
    }
    cur = {
        "room_id": "106",
        "inventory_slots": [],
        "hp": 60,
        "in_control": True,
    }
    _reward_total, bd = _reward(prev, cur, q)
    assert bd["heal_use_tax"] == HEAL_USE_TAX_LIGHT


def test_apply_obs_drops_strategy_keys_and_scalpels_world():
    from re1_rl.obs_encoder import GOAL_DIM, PROPRIO_DIM, PROPRIO_FIELDS
    from re1_rl.planner_loyal import PLANNER_LOYAL_OMIT_OBS_KEYS

    q = PlannerLoyalQueue()
    room_idx = next(i for i, (name, _) in enumerate(PROPRIO_FIELDS) if name == "room_index")
    proprio = np.zeros(PROPRIO_DIM, dtype=np.float32)
    proprio[room_idx] = 0.5

    obs = apply_planner_loyal_obs(
        {
            "goal": np.ones(GOAL_DIM, dtype=np.float32),
            "proprio": proprio,
            "world_state": np.ones(8, dtype=np.float32),
            "rooms_visited": np.ones(8, dtype=np.float32),
            "history": np.ones(8, dtype=np.float32),
            "acquisitions": np.ones(8, dtype=np.float32),
            "cutscene_ledger": np.ones(8, dtype=np.float32),
            "maps_files": np.ones(8, dtype=np.float32),
            "milestones": np.ones(8, dtype=np.float32),
            "affordances": np.ones(8, dtype=np.float32),
            "spatial": np.ones(8, dtype=np.float32),
            "named_state": np.ones(8, dtype=np.float32),
        },
        q,
    )
    for key in PLANNER_LOYAL_OMIT_OBS_KEYS:
        assert key not in obs
    assert "planner_steps" in obs
    assert obs["planner_steps"].shape == (PLANNER_QUEUE_DIM,)
    assert np.allclose(obs["spatial"], 1.0)
    assert np.allclose(obs["named_state"], 1.0)


def test_queue_pop_slides_remaining_encoding():
    q = PlannerLoyalQueue()
    first = encode_planner_queue(q)
    assert first[0] == 1.0
    q._index = 1
    second = encode_planner_queue(q)
    assert second[0] == 1.0
    assert first != second
    assert len(q.remaining) == len(q._steps) - 1


def test_apply_obs_adds_planner_steps_and_prunes_admin():
    q = PlannerLoyalQueue()
    from re1_rl.obs_encoder import GOAL_DIM, GOAL_FIELDS

    admin = {
        name: i
        for i, (name, _) in enumerate(GOAL_FIELDS)
        if name
        in {
            "waypoint_index",
            "waypoints_remaining",
            "curriculum_stage",
            "item_todo_progress",
            "wrong_room_flag",
        }
    }
    goal = np.ones(GOAL_DIM, dtype=np.float32)
    obs = apply_planner_loyal_obs({"goal": goal}, q)
    assert obs["planner_steps"].shape == (PLANNER_QUEUE_DIM,)
    assert obs["planner_steps"][0] == 1.0
    for index in admin.values():
        assert obs["goal"][index] == 0.0
    assert apply_planner_loyal_obs({"goal": goal.copy()}, None)["goal"][admin["waypoint_index"]] == 1.0
    pruned = prune_route_admin_goal(np.ones(GOAL_DIM, dtype=np.float32))
    assert pruned[admin["waypoint_index"]] == 0.0
    assert pruned[0] == 1.0


def test_planner_loyal_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("RE1_PLANNER_LOYAL", raising=False)
    assert planner_loyal_enabled() is False
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    assert planner_loyal_enabled() is True


def test_finish_capture_hook_records_step_and_does_not_stick(monkeypatch, tmp_path):
    q = PlannerLoyalQueue()
    q._index = 1
    progress = ProgressTracker(leg_span=1)
    progress.checkpoint_success = True

    def _fake_capture(env, state, breakdown):
        return {
            "source": "planner_loyal",
            "checkpoint_index": 6,
            "room_id": state.get("room_id"),
        }

    monkeypatch.setattr(
        "re1_rl.planner_loyal_cells.capture_planner_loyal_cell", _fake_capture
    )
    env = SimpleNamespace(
        project_root=str(tmp_path),
        _planner_loyal_queue=q,
        _progress=progress,
        _checkpoint_freeze_pending=True,
        _checkpoint_captured=False,
        _macro_active=True,
        _yawn_rails_capture_pending=None,
        _apply_yawn_capture_ineligibility_penalty=lambda _bd: None,
        _planner_loyal_last_success=None,
    )
    env._maybe_capture_planner_loyal_cell = (
        lambda state, breakdown: RE1Env._maybe_capture_planner_loyal_cell(
            env, state, breakdown
        )
    )
    RE1Env._finish_checkpoint_capture(
        env, {"room_id": "105"}, {"checkpoint_success": 8.0}
    )
    assert env._planner_loyal_last_success["chunk_id"] == "cp05_shield_key"
    assert env._planner_loyal_last_success["completed_index"] == 0
    assert env._checkpoint_captured is False
    assert env._progress.checkpoint_success is False
    assert env._yawn_rails_capture_pending
    assert env._yawn_rails_capture_pending[0]["source"] == "planner_loyal"


def test_finish_capture_ends_episode_on_chunk_complete(monkeypatch, tmp_path):
    q = PlannerLoyalQueue()
    q._index = len(q._steps)
    progress = ProgressTracker(leg_span=1)
    progress.checkpoint_success = True

    monkeypatch.setattr(
        "re1_rl.planner_loyal_cells.capture_planner_loyal_cell",
        lambda *a, **k: {"source": "planner_loyal", "chunk_final": True},
    )
    env = SimpleNamespace(
        project_root=str(tmp_path),
        _planner_loyal_queue=q,
        _progress=progress,
        _checkpoint_freeze_pending=True,
        _checkpoint_captured=False,
        _macro_active=True,
        _yawn_rails_capture_pending=None,
        _apply_yawn_capture_ineligibility_penalty=lambda _bd: None,
        _planner_loyal_last_success=None,
        _episode_failure_override=None,
        _stage={"mode": "yawn_rails", "max_steps": 3000},
        _step_count=10,
        _episode_truncated=lambda: False,
    )
    env._maybe_capture_planner_loyal_cell = (
        lambda state, breakdown: RE1Env._maybe_capture_planner_loyal_cell(
            env, state, breakdown
        )
    )
    RE1Env._finish_checkpoint_capture(
        env, {"room_id": "105"}, {"checkpoint_success": 8.0}
    )
    assert q.done
    assert env._checkpoint_captured is True
    assert env._progress.checkpoint_success is True
    terminated, _trunc, reason = RE1Env._termination_flags(env, {"dead": False})
    assert terminated is True
    assert reason == "planner_chunk_complete"


def test_combat_extractor_fuses_planner_steps_without_changing_features_dim():
    from gymnasium import spaces

    from re1_rl.combat_efficient_extractor import (
        FEATURES_DIM,
        RE1CombatEfficientExtractor,
        TOWER_OUT_DIM_PLANNER,
    )
    from re1_rl.planner_loyal import PLANNER_LOYAL_OMIT_OBS_KEYS
    from tests.test_doc04_medium_extractor import _stub_obs_space

    base = _stub_obs_space(with_world_state=True)
    spaces_map = dict(base.spaces)
    for key in PLANNER_LOYAL_OMIT_OBS_KEYS:
        spaces_map.pop(key, None)
    spaces_map["planner_steps"] = spaces.Box(
        -1.0, 1.0, shape=(PLANNER_QUEUE_DIM,), dtype=np.float32
    )
    obs_space = spaces.Dict(spaces_map)
    extractor = RE1CombatEfficientExtractor(obs_space, project_root=PROJECT_ROOT)
    assert extractor.planner_steps_proj is not None
    assert extractor.history_encoder is None
    assert extractor.world_context is None
    assert extractor._history_enabled is False
    assert extractor._world_enabled is False
    assert extractor._tower_out_dim == TOWER_OUT_DIM_PLANNER
    assert extractor.features_dim == FEATURES_DIM
    plain = RE1CombatEfficientExtractor(base, project_root=PROJECT_ROOT)
    assert plain.planner_steps_proj is None
    assert plain.history_encoder is not None
    assert plain.world_context is not None
    assert plain.features_dim == FEATURES_DIM
