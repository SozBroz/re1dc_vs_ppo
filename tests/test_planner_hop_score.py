"""Unit tests for planner hop-score ±1 band (live + shadow telemetry)."""

from __future__ import annotations

import pytest

from re1_rl.planner_hop_score import (
    B_KILL_MAX,
    FAIL_SCORE_DEATH,
    FAIL_SCORE_DIVERT,
    FAIL_SCORE_TIMEOUT,
    HOP_SUCCESS_FLOOR,
    K_BUDGET,
    PLANNER_DEFAULT_MAX_STEPS,
    PLANNER_DEFAULT_TIMEOUT_FRAMES,
    PlannerHopMeters,
    apply_live_hop_score,
    compose_hop_learning_target,
    count_scorable_hostiles,
    format_hop_score_shadow_line,
    hop_score_live_enabled,
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


def test_hop_score_mode_defaults_to_live_when_planner_loyal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RE1_PLANNER_HOP_SCORE_V1", raising=False)
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    assert hop_score_mode() == "live"
    assert hop_score_live_enabled() is True
    monkeypatch.setenv("RE1_PLANNER_HOP_SCORE_V1", "0")
    assert hop_score_mode() == "off"
    monkeypatch.setenv("RE1_PLANNER_HOP_SCORE_V1", "shadow")
    assert hop_score_mode() == "shadow"
    monkeypatch.setenv("RE1_PLANNER_HOP_SCORE_V1", "1")
    assert hop_score_mode() == "live"


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


def test_empty_room_success_no_kill_bonus() -> None:
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
    assert report["B_kill"] == 0.0
    assert report["q_kill"] == 0.0
    assert report["S"] == pytest.approx(1.0)  # perfect base
    assert report["S"] <= 1.0 + 1e-9


def test_two_kills_full_overshoot() -> None:
    start = {
        "room_id": "10A",
        "hp": 96,
        "enemies": [
            {"slot": 0, "hp": 40, "alive": True, "type_name": "zombie"},
            {"slot": 1, "hp": 40, "alive": True, "type_name": "zombie"},
        ],
    }
    meters = PlannerHopMeters.begin(start, tip="pl26", budget_frames=21600)
    assert meters.e_start == 2
    assert K_BUDGET == 2

    def _kill(slot: int, enemies: list[dict]) -> None:
        meters.note_step(
            {"room_id": "10A", "hp": 96, "enemies": enemies},
            {
                "room_id": "10A",
                "hp": 96,
                "step_emulated_frames": 8,
                "ammo_spent": 0,
                "enemies": [
                    {**e, "hp": 0, "alive": False} if int(e["slot"]) == slot else e
                    for e in enemies
                ],
                "combat_events": [
                    {
                        "slot": slot,
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

    alive = list(start["enemies"])
    _kill(0, alive)
    _kill(1, [{"slot": 0, "hp": 0, "alive": False, "type_name": "zombie"}, alive[1]])
    report = meters.settle(outcome="hop_success")
    assert report["K"] == 2
    assert report["B_kill"] == pytest.approx(B_KILL_MAX)
    assert report["S"] == pytest.approx(report["S_base"] + B_KILL_MAX)
    assert report["S"] > 1.0


def test_one_kill_half_overshoot() -> None:
    start = {
        "room_id": "10A",
        "hp": 96,
        "enemies": [
            {"slot": 0, "hp": 40, "alive": True, "type_name": "zombie"},
            {"slot": 1, "hp": 40, "alive": True, "type_name": "zombie"},
        ],
    }
    meters = PlannerHopMeters.begin(start, tip="pl26", budget_frames=21600)
    meters.note_step(
        start,
        {
            "room_id": "10A",
            "hp": 96,
            "step_emulated_frames": 8,
            "ammo_spent": 0,
            "enemies": [
                {"slot": 0, "hp": 0, "alive": False, "type_name": "zombie"},
                {"slot": 1, "hp": 40, "alive": True, "type_name": "zombie"},
            ],
            "combat_events": [
                {
                    "slot": 0,
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
    report = meters.settle(outcome="hop_success")
    assert report["K"] == 1
    assert report["B_kill"] == pytest.approx(0.25)
    assert report["B_kill_raw"] == pytest.approx(0.25)


def test_fail_ladder() -> None:
    meters = PlannerHopMeters.begin({"room_id": "106", "enemies": [], "hp": 96})
    assert meters.fail_score("planner_divert") == FAIL_SCORE_DIVERT
    assert meters.fail_score("planner_timeout") == FAIL_SCORE_TIMEOUT
    assert meters.fail_score("hp_death") == FAIL_SCORE_DEATH
    assert FAIL_SCORE_TIMEOUT < FAIL_SCORE_DEATH < FAIL_SCORE_DIVERT < 0


def test_raw_quality_logged_when_overshoot() -> None:
    meters = PlannerHopMeters.begin(
        {"room_id": "106", "enemies": [], "hp": 96}, budget_frames=100
    )
    meters.d_hp = 200.0  # far past denom — clip would hide without raw
    meters.frames = 0
    report = meters.settle(outcome="hop_success")
    assert report["q_hp_raw"] < 0.0
    assert report["q_hp"] == 0.0
    assert "q_hp_lo" in str(report["clip_flags"])


def test_same_slot_kill_flicker_counts_once() -> None:
    start = {
        "room_id": "10A",
        "hp": 96,
        "enemies": [
            {"slot": 0, "hp": 40, "alive": True, "type_name": "zombie"},
            {"slot": 1, "hp": 40, "alive": True, "type_name": "zombie"},
        ],
    }
    meters = PlannerHopMeters.begin(start, tip="pl26", budget_frames=21600)

    def _kill_step(slot: int, enemies: list[dict]) -> None:
        meters.note_step(
            {"room_id": "10A", "hp": 96, "enemies": enemies},
            {
                "room_id": "10A",
                "hp": 96,
                "step_emulated_frames": 8,
                "ammo_spent": 0,
                "enemies": [
                    {**e, "hp": 0, "alive": False} if int(e["slot"]) == slot else e
                    for e in enemies
                ],
                "combat_events": [
                    {
                        "slot": slot,
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

    alive = list(start["enemies"])
    _kill_step(0, alive)
    flickered = [
        {"slot": 0, "hp": 5, "alive": True, "type_name": "zombie"},
        {"slot": 1, "hp": 40, "alive": True, "type_name": "zombie"},
    ]
    _kill_step(0, flickered)
    _kill_step(1, flickered)
    report = meters.settle(outcome="planner_timeout", failure="planner_timeout")
    assert report["K"] == 2
    assert report["K_raw_vanish"] >= 3
    assert report["S"] == FAIL_SCORE_TIMEOUT


def test_apply_live_hop_score_zeros_legacy() -> None:
    meters = PlannerHopMeters.begin({"room_id": "106", "enemies": [], "hp": 96})
    bd = {
        "planner_divert": -4.0,
        "enemy_kill": 2.0,
        "hp": -0.5,
        "hop_score": 0.0,
    }
    report = apply_live_hop_score(
        bd, meters, outcome="planner_divert", failure="planner_divert"
    )
    assert bd["hop_score"] == FAIL_SCORE_DIVERT
    assert bd["planner_divert"] == 0.0
    assert bd["enemy_kill"] == 0.0
    assert bd["hp"] == 0.0
    assert report["S"] == FAIL_SCORE_DIVERT


def test_apply_live_keeps_checkpoint_success_capture_gate() -> None:
    """Live hop_score must not clear the mint/freeze gate."""
    meters = PlannerHopMeters.begin({"room_id": "106", "enemies": [], "hp": 96})
    bd = {
        "planner_step_success": 8.0,
        "checkpoint_success": 8.0,
        "hop_score": 0.0,
    }
    apply_live_hop_score(
        bd, meters, outcome="planner_step_success", failure=None
    )
    assert bd["planner_step_success"] == 0.0  # reward channel replaced
    assert bd["checkpoint_success"] == 8.0  # capture gate retained
    assert bd["hop_score"] > 0.0


def test_resolve_shadow_outcome_truncated_is_timeout() -> None:
    outcome, failure = resolve_shadow_outcome(
        episode_failure=None,
        terminated=False,
        truncated=True,
        breakdown={},
    )
    assert outcome == "planner_timeout"
    assert failure == "planner_timeout"


def test_format_line_includes_raw_math(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RE1_PLANNER_HOP_SCORE_V1", "1")
    meters = PlannerHopMeters.begin({"room_id": "106", "enemies": [], "hp": 96})
    report = meters.settle(outcome="hop_success")
    line = format_hop_score_shadow_line(report)
    assert "[hop_score_live]" in line
    assert "S_base=" in line
    assert "B_kill=" in line
    assert "q_hp_raw=" in line
    assert "clip_flags=" in line
    assert "K_budget=" in line


def test_success_floor_constant() -> None:
    assert HOP_SUCCESS_FLOOR == 0.20


def test_compose_hop_learning_target_negative_overpowers_positive_s() -> None:
    # Any negative local on a positive hop overrides S on that step.
    assert compose_hop_learning_target(0.96, -1.4) == pytest.approx(-1.4)
    assert compose_hop_learning_target(0.96, -0.96) == pytest.approx(-0.96)
    assert compose_hop_learning_target(0.96, -0.20) == pytest.approx(-0.20)
    assert compose_hop_learning_target(0.96, -0.029) == pytest.approx(-0.029)
    # Positive locals stay additive with negative S unless statue-push override.
    assert compose_hop_learning_target(-1.0, 0.50) == pytest.approx(-0.50)
    assert compose_hop_learning_target(-1.0, -0.20) == pytest.approx(-1.20)
    assert compose_hop_learning_target(0.96, 0.0) == pytest.approx(0.96)


def test_compose_hop_learning_target_positive_push_overrides_failed_s() -> None:
    # pl82→83 / pl83→84: right-way statue shove on a failed hop overrides S.
    assert compose_hop_learning_target(
        -4.0, 0.50, positive_push_override=True
    ) == pytest.approx(0.50)
    assert compose_hop_learning_target(
        -1.0, 0.25, positive_push_override=True
    ) == pytest.approx(0.25)
    # Without the push flag, still additive.
    assert compose_hop_learning_target(
        -4.0, 0.50, positive_push_override=False
    ) == pytest.approx(-3.50)
    # Push flag does not override a successful hop (stay additive).
    assert compose_hop_learning_target(
        0.96, 0.50, positive_push_override=True
    ) == pytest.approx(1.46)
    # Zero / non-positive local does not override even with the flag.
    assert compose_hop_learning_target(
        -4.0, 0.0, positive_push_override=True
    ) == pytest.approx(-4.0)
    assert compose_hop_learning_target(
        -4.0, -0.20, positive_push_override=True
    ) == pytest.approx(-4.20)
