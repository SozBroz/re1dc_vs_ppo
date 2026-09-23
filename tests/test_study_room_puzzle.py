"""Unit tests for study room 20A drain + phase compass."""
from __future__ import annotations

from re1_rl.study_room_puzzle import (
    STUDY_AMMO_XZ,
    STUDY_CUPBOARD_APPROACH_XZ,
    STUDY_INSECT_APPROACH_XZ,
    STUDY_TANK_APPROACH_XZ,
    STUDY_TANK_PUSH_ENDPOINT_XZ,
    encode_study_room_compass,
    study_phase,
    study_room_goal_target,
    study_tank_drained_from_state,
)


class _Q:
    def __init__(self, step: dict) -> None:
        self.current = step


def test_drain_bit():
    assert not study_tank_drained_from_state({"study_tank_drained_flag": 0})
    assert study_tank_drained_from_state({"study_tank_drained_flag": 0x20})
    assert study_tank_drained_from_state({"study_tank_drained": True})


def test_phase_mega_acquire():
    q = _Q({"op": "acquire", "pickup_id": "20A:explosive_rounds:1"})
    assert (
        study_phase(
            {"room_id": "20A", "x": 2600, "z": 5500, "study_tank_drained_flag": 0},
            queue=q,
        )
        == "insect"
    )
    assert (
        study_phase(
            {
                "room_id": "20A",
                "x": 7359,
                "z": 7392,
                "study_tank_drained_flag": 0x20,
            },
            queue=q,
        )
        == "tank"
    )
    assert (
        study_phase(
            {
                "room_id": "20A",
                "x": 7359,
                "z": 5592,
                "study_tank_drained_flag": 0x20,
            },
            queue=q,
        )
        == "cupboard"
    )
    assert (
        study_phase(
            {
                "room_id": "20A",
                "x": 5103,
                "z": 7203,
                "study_tank_drained_flag": 0x20,
            },
            queue=q,
        )
        == "ammo"
    )


def test_tank_done_rejects_overshoot_wedge():
    from re1_rl.study_room_puzzle import study_tank_push_done

    assert study_tank_push_done(
        {
            "room_id": "20A",
            "x": 7359,
            "z": 5592,
            "study_tank_drained_flag": 0x20,
        }
    )
    assert not study_tank_push_done(
        {
            "room_id": "20A",
            "x": 7350,
            "z": 4308,
            "study_tank_drained_flag": 0x20,
        }
    )
    assert not study_tank_push_done(
        {
            "room_id": "20A",
            "x": 7350,
            "z": 5058,
            "study_tank_drained_flag": 0x20,
        }
    )


def test_cupboard_done_rejects_east_wall_wander():
    """Softlocked pl158 sat at x≈6458 — past real cupboard endpoint."""
    from re1_rl.study_room_puzzle import study_cupboard_push_done

    assert study_cupboard_push_done(
        {
            "room_id": "20A",
            "x": 4303,
            "z": 7123,
            "study_tank_drained_flag": 0x20,
        }
    )
    assert study_cupboard_push_done(
        {
            "room_id": "20A",
            "x": 5103,
            "z": 7203,
            "study_tank_drained_flag": 0x20,
        }
    )
    assert not study_cupboard_push_done(
        {
            "room_id": "20A",
            "x": 6458,
            "z": 7530,
            "study_tank_drained_flag": 0x20,
        }
    )


def test_compass_targets():
    q = _Q({"op": "acquire", "pickup_id": "20A:explosive_rounds:1"})
    insect = study_room_goal_target(
        {"room_id": "20A", "x": 2600, "z": 5500, "study_tank_drained_flag": 0},
        queue=q,
    )
    assert insect == (float(STUDY_INSECT_APPROACH_XZ[0]), float(STUDY_INSECT_APPROACH_XZ[1]))
    tank = study_room_goal_target(
        {
            "room_id": "20A",
            "x": 5000,
            "z": 5000,
            "facing": 0,
            "study_tank_drained_flag": 0x20,
        },
        queue=q,
    )
    assert tank == (float(STUDY_TANK_APPROACH_XZ[0]), float(STUDY_TANK_APPROACH_XZ[1]))
    # Near approach pad → push endpoint.
    tank_push = study_room_goal_target(
        {
            "room_id": "20A",
            "x": STUDY_TANK_APPROACH_XZ[0],
            "z": STUDY_TANK_APPROACH_XZ[1],
            "facing": 1024,
            "study_tank_drained_flag": 0x20,
        },
        queue=q,
    )
    assert tank_push == (
        float(STUDY_TANK_PUSH_ENDPOINT_XZ[0]),
        float(STUDY_TANK_PUSH_ENDPOINT_XZ[1]),
    )
    cup = study_room_goal_target(
        {
            "room_id": "20A",
            "x": 7359,
            "z": 5592,
            "facing": 0,
            "study_tank_drained_flag": 0x20,
        },
        queue=q,
    )
    assert cup == (
        float(STUDY_CUPBOARD_APPROACH_XZ[0]),
        float(STUDY_CUPBOARD_APPROACH_XZ[1]),
    )
    ammo = study_room_goal_target(
        {
            "room_id": "20A",
            "x": 5103,
            "z": 7203,
            "facing": 0,
            "study_tank_drained_flag": 0x20,
        },
        queue=q,
    )
    assert ammo == (float(STUDY_AMMO_XZ[0]), float(STUDY_AMMO_XZ[1]))
    vec = encode_study_room_compass(
        {
            "room_id": "20A",
            "x": 2600,
            "z": 5500,
            "facing": 0,
            "study_tank_drained_flag": 0,
        },
        queue=q,
    )
    assert vec is not None and vec.shape == (5,)
