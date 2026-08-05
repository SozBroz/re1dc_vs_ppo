"""Yawn outcome contract (no BizHawk)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.yawn_hp import YAWN_RAW_FULL, yawn_logical_hp
from re1_rl.yawn_outcome import (
    YAWN_ROOM,
    YawnOutcome,
    detect_yawn_outcome,
    yawn_contact_edge,
    yawn_poison_active,
    yawn_retreat_detected,
    yawn_telemetry,
)


def _yawn_ent(
    *,
    hp_raw: int = YAWN_RAW_FULL,
    in_room: int = 1,
    active_byte: int = 2,
    translate: bool = True,
) -> dict:
    logical = yawn_logical_hp(hp_raw)
    ent: dict = {
        "slot": 0,
        "hp_raw": hp_raw,
        "hp": logical if translate else hp_raw,
        "in_room": in_room,
        "alive": in_room,
        "active_byte": active_byte,
        "x": 4500,
        "z": 12000,
    }
    if translate:
        ent["yawn_translated"] = True
    return ent


def test_yawn_room_constant() -> None:
    assert YAWN_ROOM == "210"


def test_yawn_poison_active() -> None:
    # Candidate poison RAM disabled — always inactive until verified.
    assert not yawn_poison_active({"poisoned": True})
    assert not yawn_poison_active({"player_poison": 1})
    assert not yawn_poison_active({"poisoned": False, "player_poison": 0})


def test_contact_edge_on_first_in_combat() -> None:
    prev = {"room_id": "210", "hp": 96}
    state = {"room_id": "210", "hp": 96}
    enemies = [_yawn_ent()]
    assert yawn_contact_edge(state, prev, enemies=enemies, prev_enemies=[]) is True
    # Already in combat — not an edge.
    assert (
        yawn_contact_edge(
            state, prev, enemies=enemies, prev_enemies=[_yawn_ent()]
        )
        is False
    )


def test_contact_ignored_outside_210() -> None:
    state = {"room_id": "20E", "hp": 96}
    assert (
        yawn_contact_edge(
            state, {"room_id": "20E"}, enemies=[_yawn_ent()], prev_enemies=[]
        )
        is False
    )


def test_damaged_and_retreat() -> None:
    progress = ProgressTracker()
    prev_state = {"room_id": "210", "hp": 96, "dead": False}
    state = {"room_id": "210", "hp": 96, "dead": False}
    prev_enemies = [_yawn_ent(hp_raw=YAWN_RAW_FULL)]
    # Chip Yawn.
    damaged_enemies = [_yawn_ent(hp_raw=YAWN_RAW_FULL - 45)]
    mid = detect_yawn_outcome(
        state,
        prev_state,
        progress,
        enemies=damaged_enemies,
        prev_enemies=prev_enemies,
    )
    assert mid.damaged
    assert mid.outcome == YawnOutcome.DAMAGED

    # Yawn leaves combat after damage → retreat.
    left = [_yawn_ent(hp_raw=YAWN_RAW_FULL - 45, in_room=0, active_byte=0)]
    ret = detect_yawn_outcome(
        state,
        prev_state,
        progress,
        enemies=left,
        prev_enemies=damaged_enemies,
    )
    assert ret.retreat
    assert yawn_retreat_detected(
        state, prev_state, enemies=left, prev_enemies=damaged_enemies
    )
    assert ret.outcome == YawnOutcome.RETREAT


def test_retreat_when_yawn_despawns_after_damage() -> None:
    prev_state = {"room_id": "210", "hp": 80, "dead": False}
    state = {"room_id": "210", "hp": 80, "dead": False}
    prev_enemies = [_yawn_ent(hp_raw=YAWN_RAW_FULL - 60)]
    assert yawn_retreat_detected(
        state, prev_state, enemies=[], prev_enemies=prev_enemies
    )


def test_player_death_in_210() -> None:
    progress = ProgressTracker()
    prev = {"room_id": "210", "hp": 20, "dead": False}
    state = {"room_id": "210", "hp": 0, "dead": True}
    enemies = [_yawn_ent(hp_raw=YAWN_RAW_FULL - 10)]
    out = detect_yawn_outcome(
        state, prev, progress, enemies=enemies, prev_enemies=enemies
    )
    assert out.player_death
    assert out.outcome == YawnOutcome.PLAYER_DEATH


def test_poisoned_outcome() -> None:
    progress = ProgressTracker()
    prev = {"room_id": "210", "hp": 70, "poisoned": False}
    state = {"room_id": "210", "hp": 70, "poisoned": True}
    enemies = [_yawn_ent()]
    out = detect_yawn_outcome(
        state, prev, progress, enemies=enemies, prev_enemies=enemies
    )
    # Poison outcome gated on TRUST_PLAYER_POISON_RAM (currently False).
    assert not out.poisoned
    assert out.outcome != YawnOutcome.POISONED


def test_killed_when_raw_hp_hits_zero() -> None:
    progress = ProgressTracker()
    prev = {"room_id": "210", "hp": 50, "dead": False}
    state = {"room_id": "210", "hp": 50, "dead": False}
    prev_enemies = [_yawn_ent(hp_raw=500)]
    # Corpse keeps yawn_translated so the detector still sees the slot.
    cur_enemies = [
        {
            "slot": 0,
            "hp": 0,
            "hp_raw": 0,
            "yawn_translated": True,
            "in_room": 0,
            "alive": 0,
            "active_byte": 0,
        }
    ]
    out = detect_yawn_outcome(
        state,
        prev,
        progress,
        enemies=cur_enemies,
        prev_enemies=prev_enemies,
    )
    assert out.killed
    assert out.outcome == YawnOutcome.KILLED


def test_telemetry_dict_keys() -> None:
    progress = ProgressTracker()
    state = {"room_id": "210", "hp": 96, "poisoned": False}
    out = detect_yawn_outcome(
        state,
        {"room_id": "210", "hp": 96},
        progress,
        enemies=[_yawn_ent()],
        prev_enemies=[],
    )
    tel = yawn_telemetry(out)
    assert tel["yawn_outcome"] == YawnOutcome.CONTACT.value
    assert tel["yawn_contact"] is True
    assert "yawn_logical_hp" in tel
    assert tel["yawn_room"] == "210"
