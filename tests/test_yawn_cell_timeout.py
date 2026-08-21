"""Per-CP emulated-frame fail budgets (1x human times → 60 fps walls)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    RAILS_CELL_TIMEOUT_PENALTY,
    RAILS_CHECKPOINT_REWARD,
    RAILS_CHECKPOINT_REWARD_MIN,
    compute_reward,
    rails_checkpoint_success_reward,
)
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
    assert cell_timeout_frames(19, ROOT) == 20154  # 1:07.18 * 5 (2.5x buffer)
    assert cell_timeout_frames(20, ROOT) == 2797  # 23.31s * 2
    assert cell_timeout_frames(25, ROOT) == 21240  # 3:56 * 1.5 cutscene
    assert cell_timeout_frames(27, ROOT) == 20242  # 1:24.34 * 4 hard
    assert cell_timeout_frames(34, ROOT) == 4234  # 35.28s * 2
    assert cell_timeout_frames(35, ROOT) == 2940  # 24.50s * 2
    assert cell_timeout_frames(37, ROOT) == 9780  # 32.6s * 5 (2.5x buffer)
    assert cell_timeout_frames(40, ROOT) == 10872  # 45.30s * 4 hard
    assert cell_timeout_frames(44, ROOT) == 15430  # 1:04.29 * 4 hard
    assert cell_timeout_frames(45, ROOT) == 28800  # flat 8 min, not 4:20.72 * 1.5
    assert cell_timeout_frames(46, ROOT) == 11647  # 1:37.06 * 2
    assert cell_timeout_frames(47, ROOT) == 1814  # 15.12s * 2
    assert cell_timeout_frames(53, ROOT) == CAP  # flat 12 min, not 1:20.77 * 4
    assert cell_timeout_frames(60, ROOT) == 31668  # 2:11.95 * 4 hard
    assert cell_timeout_frames(84, ROOT) == 12473  # 2:18.59 * 1.5 cutscene
    assert cell_timeout_frames(85, ROOT) == 5383  # 22.43s * 4 hard (Richard-wait)
    assert cell_timeout_frames(91, ROOT) == 5021  # 20.92s * 4 hard (Richard-wait)
    assert cell_timeout_frames(95, ROOT) == 6600  # 55.00s * 2 main-hall wait
    assert cell_timeout_frames(112, ROOT) == 6709  # 55.91s * 2 attic
    assert cell_timeout_frames(200, ROOT) == 0
    assert cell_timeout_frames(8, ROOT) < CAP
    assert cell_timeout_frames(9, ROOT) < CAP
    assert cell_timeout_frames(19, ROOT) < CAP
    assert cell_timeout_frames(25, ROOT) < CAP
    assert cell_timeout_frames(27, ROOT) < CAP
    assert cell_timeout_frames(45, ROOT) < CAP
    assert cell_timeout_frames(53, ROOT) == CAP
    assert cell_timeout_frames(60, ROOT) < CAP
    assert cell_timeout_frames(84, ROOT) < CAP


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


def test_cell_timeout_remaining_frac_clips_and_counts_extra() -> None:
    progress = ProgressTracker()
    assert progress.cell_timeout_remaining_frac() == 1.0
    progress.arm_cell_timeout(1000)
    assert progress.cell_timeout_remaining_frac() == pytest.approx(1.0)
    progress.note_leg_frames(250)
    assert progress.cell_timeout_remaining_frac() == pytest.approx(0.75)
    assert progress.cell_timeout_remaining_frac(250) == pytest.approx(0.50)
    progress.note_leg_frames(750)
    assert progress.cell_timeout_remaining_frac() == 0.0
    assert progress.cell_timeout_remaining_frac(8) == 0.0


def test_checkpoint_success_scales_with_remaining_timeout() -> None:
    assert rails_checkpoint_success_reward(None) == RAILS_CHECKPOINT_REWARD
    untimed = ProgressTracker()
    assert rails_checkpoint_success_reward(untimed) == RAILS_CHECKPOINT_REWARD

    progress = ProgressTracker()
    progress.arm_cell_timeout(1000)
    assert rails_checkpoint_success_reward(progress) == pytest.approx(
        RAILS_CHECKPOINT_REWARD
    )
    progress.note_leg_frames(500)
    assert rails_checkpoint_success_reward(progress) == pytest.approx(6.0)
    progress.note_leg_frames(500)
    assert rails_checkpoint_success_reward(progress) == pytest.approx(
        RAILS_CHECKPOINT_REWARD_MIN
    )


def test_live_checkpoint_success_uses_leftover_budget() -> None:
    planner = WaypointPlanner(
        YAWN_ROUTE,
        route_steps=[2],
        start_index=0,
    )
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    progress.arm_cell_timeout(1000)
    progress.note_leg_frames(250)
    _, bd = compute_reward(
        make_state("105", step=1),
        make_state("104", step=2, step_emulated_frames=250, in_control=True),
        planner,
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    # used = 250 noted + 250 this step → leftover 0.50 → 4 + 4*0.50 = 6
    assert bd["checkpoint_success"] == pytest.approx(6.0)
    assert bd["checkpoint_timeout"] == 0.0


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
