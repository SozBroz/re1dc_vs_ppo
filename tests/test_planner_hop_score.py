"""Unit tests for planner hop-score shadow meters (no live reward change)."""

from __future__ import annotations

import pytest

from re1_rl.planner_hop_score import (
    FAIL_SCORE_DEFAULT,
    FAIL_SCORE_TIMEOUT,
    PLANNER_DEFAULT_MAX_STEPS,
    PLANNER_DEFAULT_TIMEOUT_FRAMES,
    PlannerHopMeters,
    count_scorable_hostiles,
    format_hop_score_shadow_line,
    hop_score_mode,
    planner_timeout_frames,
    resolve_shadow_outcome,
    room_transition_bogus_kills,
    scored_kill_count,
)


def test_default_timeout_is_six_minutes() -> None:
    assert PLANNER_DEFAULT_TIMEOUT_FRAMES == 21600
    assert PLANNER_DEFAULT_MAX_STEPS == 2700
    assert planner_timeout_frames(boss=False) == 21600
    assert planner_timeout_frames(boss=True) == 43200


def test_hop_score_mode_defaults_to_shadow_when_planner_loyal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RE1_PLANNER_HOP_SCORE_V1", raising=False)
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    assert hop_score_mode() == "shadow"
    monkeypatch.setenv("RE1_PLANNER_HOP_SCORE_V1", "0")
    assert hop_score_mode() == "off"


def test_room_transition_kills_are_bogus_not_scored() -> None:
    prev = {
        "room_id": "105",
        "enemies": [{"slot": 0, "hp": 40, "alive": True, "type_name": "zombie"}],
    }
    curr = {
        "room_id": "106",
        "enemies": [],
        "enemy_kills": 0,
        "combat_events": [],
    }
    assert room_transition_bogus_kills(prev, curr) == 1
    assert scored_kill_count(curr) == 0


def test_scorable_hostiles_exclude_crows() -> None:
    enemies = [
        {"slot": 0, "hp": 40, "alive": True, "type_name": "zombie"},
        {
            "slot": 1,
            "hp": 10,
            "alive": True,
            "type_name": "crow",
            "active_byte": 0x04,
        },
    ]
    assert count_scorable_hostiles(enemies, room_id="117") == 1


def test_success_score_empty_room_full_kill_credit() -> None:
    meters = PlannerHopMeters.begin(
        {"room_id": "106", "enemies": [], "hp": 96},
        tip="pl10",
        boss=False,
        budget_frames=21600,
    )
    assert meters.e_start == 0
    meters.frames = 0
    report = meters.settle(outcome="hop_success")
    assert report["success"] is True
    assert report["q_kill"] == 1.0
    assert report["S"] == pytest.approx(4.0)


def test_success_score_partial_clear() -> None:
    start = {
        "room_id": "105",
        "hp": 96,
        "enemies": [
            {"slot": 0, "hp": 40, "alive": True, "type_name": "zombie"},
            {"slot": 1, "hp": 40, "alive": True, "type_name": "zombie"},
            {"slot": 2, "hp": 40, "alive": True, "type_name": "zombie"},
        ],
    }
    meters = PlannerHopMeters.begin(start, tip="pl20", budget_frames=21600)
    assert meters.e_start == 3
    meters.note_step(
        start,
        {
            "room_id": "105",
            "hp": 96,
            "step_emulated_frames": 8,
            "ammo_spent": 0,
            "enemies": [
                {"slot": 0, "hp": 0, "alive": False, "type_name": "zombie"},
                {"slot": 1, "hp": 40, "alive": True, "type_name": "zombie"},
                {"slot": 2, "hp": 40, "alive": True, "type_name": "zombie"},
            ],
            "combat_events": [
                {
                    "killed": True,
                    "reward_denied": False,
                    "is_crow": False,
                    "damage": 40,
                }
            ],
            "enemy_kills": 1,
            "inventory_slots": [],
        },
    )
    # Room-change vanish must not add to K.
    meters.note_step(
        {
            "room_id": "105",
            "hp": 96,
            "enemies": [
                {"slot": 1, "hp": 40, "alive": True, "type_name": "zombie"},
                {"slot": 2, "hp": 40, "alive": True, "type_name": "zombie"},
            ],
        },
        {
            "room_id": "106",
            "hp": 96,
            "step_emulated_frames": 8,
            "ammo_spent": 0,
            "enemies": [],
            "combat_events": [],
            "enemy_kills": 0,
            "inventory_slots": [],
        },
    )
    report = meters.settle(outcome="hop_success")
    assert report["K"] == 1
    assert report["K_transition_bogus"] == 2
    assert report["q_kill"] == pytest.approx(1.0 / 3.0)
    assert 0.25 < report["S"] < 4.0


def test_fail_timeout_worse_than_divert() -> None:
    meters = PlannerHopMeters.begin({"room_id": "106", "enemies": [], "hp": 96})
    assert meters.fail_score("planner_divert") == FAIL_SCORE_DEFAULT
    assert meters.fail_score("planner_timeout") == FAIL_SCORE_TIMEOUT
    assert meters.fail_score("hp_death") == FAIL_SCORE_DEFAULT


def test_resolve_shadow_outcome_truncated_is_timeout() -> None:
    outcome, failure = resolve_shadow_outcome(
        episode_failure=None,
        terminated=False,
        truncated=True,
    )
    assert outcome == "planner_timeout"
    assert failure == "planner_timeout"


def test_format_shadow_line_greppable() -> None:
    line = format_hop_score_shadow_line(
        {
            "tip": "pl42",
            "outcome": "planner_divert",
            "S": -4.0,
            "success": False,
            "q_hp": 1.0,
            "q_ammo": 1.0,
            "q_kill": 0.0,
            "q_heal": 1.0,
            "q_time": 0.5,
            "E_start": 2,
            "K": 0,
            "K_raw_vanish": 3,
            "K_transition_bogus": 2,
            "D_hp": 0.0,
            "A_spent": 0.0,
            "H_used": 0.0,
            "F_elapsed": 1000,
            "F_budget": 21600,
            "boss": False,
        }
    )
    assert line.startswith("[hop_score_shadow]")
    assert "K_bogus_transition=2" in line
    assert "S=-4.0000" in line
