"""Per-CP emulated-frame fail budgets (1x human times → 60 fps walls)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import RAILS_CELL_TIMEOUT_PENALTY, compute_reward
from re1_rl.yawn_cell_timeout import (
    cell_timeout_frames,
    created_checkpoint_index,
    frames_from_human_time,
    parse_mmss,
)
from tests.test_scaffolding import make_planner, make_state

YAWN_ROUTE = ROOT / "data" / "yawn_checkpoint_route.json"
CAP = 12 * 60 * 60


def test_parse_mmss_accepts_seconds_and_minutes() -> None:
    assert parse_mmss("49.57") == pytest.approx(49.57)
    assert parse_mmss("17.93") == pytest.approx(17.93)
    assert parse_mmss("2:00") == pytest.approx(120.0)
    assert parse_mmss("3:21.61") == pytest.approx(201.61)
    assert parse_mmss("1:04.41") == pytest.approx(64.41)
    assert parse_mmss("1:47.00") == pytest.approx(107.0)


def test_frames_use_60fps_and_clamp_to_twelve_minutes() -> None:
    assert frames_from_human_time("1.00", multiplier=2.0) == 120
    assert frames_from_human_time("2:00", multiplier=1.5) == 10800
    over = frames_from_human_time("10:00", multiplier=4.0)
    assert over == CAP
    assert over <= CAP


def test_cp_budgets_match_imperator_multipliers() -> None:
    assert cell_timeout_frames(0, ROOT) == 5948  # 49.57s * 2
    assert cell_timeout_frames(1, ROOT) == 2152  # 17.93s * 2
    assert cell_timeout_frames(2, ROOT) == 10800  # 2:00 * 1.5 cutscene
    assert cell_timeout_frames(3, ROOT) == 5000  # 55.55s * 1.5 cutscene
    assert cell_timeout_frames(5, ROOT) == 18145  # 3:21.61 * 1.5 cutscene
    assert cell_timeout_frames(8, ROOT) == 6175  # 25.73s * 4 hard
    assert cell_timeout_frames(9, ROOT) == 25680  # 1:47 * 4 hard
    assert cell_timeout_frames(18, ROOT) == 2513  # 20.94s * 2
    assert cell_timeout_frames(19, ROOT) == 0
    assert cell_timeout_frames(8, ROOT) < CAP
    assert cell_timeout_frames(9, ROOT) < CAP


def test_created_index_is_seq_minus_one() -> None:
    planner = WaypointPlanner(
        YAWN_ROUTE,
        route_steps=list(range(1, 20)),
        start_index=0,
    )
    assert created_checkpoint_index(planner) == 0
    planner = WaypointPlanner(
        YAWN_ROUTE,
        route_steps=list(range(1, 20)),
        start_index=17,
    )
    assert created_checkpoint_index(planner) == 17
    assert planner.current_objective()["checkpoint_id"] == "gallery_107"


def test_timeout_pays_cell_fail_and_zeros_positives() -> None:
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    progress.arm_cell_timeout(8)
    _, bd = compute_reward(
        make_state("105", step=1),
        make_state("105", step=2, step_emulated_frames=8, in_control=True),
        make_planner(),
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_timeout"] == RAILS_CELL_TIMEOUT_PENALTY
    assert bd["checkpoint_timeout"] == pytest.approx(-4.0)
    assert progress.cell_timeout_breached is True
    assert bd["new_room"] == 0.0


def test_same_step_checkpoint_success_does_not_timeout() -> None:
    planner = WaypointPlanner(
        YAWN_ROUTE,
        route_steps=[2],
        start_index=0,
    )
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    progress.arm_cell_timeout(8)
    _, bd = compute_reward(
        make_state("105", step=1),
        make_state("104", step=2, step_emulated_frames=8, in_control=True),
        planner,
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] > 0.0
    assert bd["checkpoint_timeout"] == 0.0
    assert progress.cell_timeout_breached is False


def test_synthetic_reward_calls_without_step_frames_do_not_tick() -> None:
    progress = ProgressTracker()
    progress.arm_cell_timeout(8)
    compute_reward(
        make_state("105", step=1),
        make_state("105", step=2, in_control=True),
        make_planner(),
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert progress.leg_emulated_frames == 0
    assert progress.cell_timeout_breached is False
