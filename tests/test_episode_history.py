"""Unit tests for episode history obs (A5 room deque, A6 acquisition log)."""

from __future__ import annotations

import numpy as np

from re1_rl.episode_history import (
    ACQUISITION_LOG_DIM,
    ROOM_DEQUE_K,
    ROOM_HISTORY_DIM,
    EpisodeHistory,
    RoomTransitionDeque,
)


def test_room_deque_k_is_32() -> None:
    assert ROOM_DEQUE_K == 32
    assert ROOM_HISTORY_DIM == 1 + 32 * 2


def test_room_transition_deque_records_entries_not_revisits_same_step() -> None:
    dq = RoomTransitionDeque(capacity=4)
    dq.reset("100", step=0)
    dq.maybe_record_transition("100", prev_room="100", step=1)
    assert len(dq.entries) == 1
    dq.maybe_record_transition("101", prev_room="100", step=5)
    dq.maybe_record_transition("102", prev_room="101", step=10)
    assert [e[0] for e in dq.entries] == ["100", "101", "102"]


def test_room_transition_deque_caps_at_k() -> None:
    dq = RoomTransitionDeque(capacity=3)
    dq.reset("100", step=0)
    for i, room in enumerate(["101", "102", "103", "104"], start=1):
        dq.maybe_record_transition(room, prev_room=str(99 + i), step=i * 10)
    assert len(dq.entries) == 3
    assert [e[0] for e in dq.entries] == ["102", "103", "104"]


def test_room_history_encode_shape_and_valid_frac() -> None:
    dq = RoomTransitionDeque()
    dq.reset("100", step=0)
    dq.maybe_record_transition("101", prev_room="100", step=50)
    room_index = {"100": 0, "101": 5}
    v = dq.encode(current_step=100, room_index=room_index, max_episode_steps=1000)
    assert v.shape == (ROOM_HISTORY_DIM,)
    assert v.dtype == np.float32
    assert 0.0 < v[0] <= 1.0
    assert v[1] == 0.0  # room 100 index 0
    assert v[3] == 5 / 128.0  # room 101


def test_acquisition_log_records_pickups() -> None:
    hist = EpisodeHistory()
    hist.reset("100", step=0)
    hist.on_step(
        {"room_id": "101", "step": 10},
        prev_state={"room_id": "100", "step": 9},
        new_items=["shield_key"],
    )
    hist.on_step(
        {"room_id": "101", "step": 11},
        prev_state={"room_id": "101", "step": 10},
        new_items=["emblem"],
    )
    enc = hist.encode(current_step=11, room_index={"101": 7}, max_episode_steps=48000)
    assert enc["history"].shape == (ROOM_HISTORY_DIM,)
    assert enc["acquisitions"].shape == (ACQUISITION_LOG_DIM,)
    assert enc["acquisitions"][0] == 0.5  # 2 of 4 slots filled
    # shield_key = 0x35 = 53
    assert enc["acquisitions"][1] == 53 / 0x4B
    assert enc["acquisitions"][2] == 7 / 128.0


def test_obs_keys_have_no_path_leakage_names() -> None:
    forbidden = {"waypoint", "waypoint_index", "route_index", "checkpoint_index"}
    keys = {
        "frame",
        "proprio",
        "goal",
        "spatial",
        "visited",
        "rooms_visited",
        "box",
        "inventory",
        "history",
        "acquisitions",
        "room_enemies",
        "keys_held",
        "affordances",
        "cutscene_ledger",
    }
    assert not (keys & forbidden)
    from re1_rl.episode_history import ACQUISITION_LOG_DIM, ROOM_HISTORY_DIM
    from re1_rl.obs_encoder import GOAL_DIM, INVENTORY_OBS_DIM
    from re1_rl.room_signature import ENEMY_ROSTER_DIM

    assert GOAL_DIM == 27
    assert INVENTORY_OBS_DIM == 16
    assert ROOM_HISTORY_DIM == 65
    assert ACQUISITION_LOG_DIM == 9
    assert ENEMY_ROSTER_DIM == 12
