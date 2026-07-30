"""Yawn first-encounter outcome contract for room ``210``.

Detects contact, poison, damage, retreat, kill, and player death from RAM
enemy rows + player state — not the static ``snake_yawn`` roster alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from re1_rl.yawn_hp import (
    YAWN_LOGICAL_MAX_DEFAULT,
    YAWN_ROOM,
    is_yawn_raw_hp,
    yawn_logical_hp,
)

__all__ = (
    "YAWN_ROOM",
    "YawnOutcome",
    "YawnOutcomeResult",
    "find_yawn_entities",
    "yawn_poison_active",
    "yawn_contact_edge",
    "yawn_retreat_detected",
    "detect_yawn_outcome",
    "yawn_telemetry",
)


class YawnOutcome(str, Enum):
    """Primary Yawn-fight outcome for one step transition."""

    NONE = "none"
    CONTACT = "contact"
    POISONED = "poisoned"
    DAMAGED = "damaged"
    RETREAT = "retreat"
    KILLED = "killed"
    PLAYER_DEATH = "player_death"


@dataclass
class YawnOutcomeResult:
    """Structured detector output + info-log telemetry."""

    outcome: YawnOutcome = YawnOutcome.NONE
    contact: bool = False
    poisoned: bool = False
    damaged: bool = False
    retreat: bool = False
    killed: bool = False
    player_death: bool = False
    yawn_logical_hp: int | None = None
    yawn_logical_hp_prev: int | None = None
    yawn_in_combat: bool = False
    yawn_in_combat_prev: bool = False
    telemetry: dict[str, Any] = field(default_factory=dict)


def _room_id(state: dict[str, Any] | None) -> str:
    if not state:
        return ""
    return str(state.get("room_id", "") or "").strip().upper()


def _player_alive(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    if bool(state.get("dead")):
        return False
    return int(state.get("hp", 0) or 0) > 0


def _raw_hp(ent: dict[str, Any]) -> int:
    if "hp_raw" in ent:
        return int(ent.get("hp_raw") or 0)
    return int(ent.get("hp") or 0)


def _logical_hp(ent: dict[str, Any]) -> int:
    if ent.get("yawn_translated"):
        return max(0, int(ent.get("hp") or 0))
    raw = _raw_hp(ent)
    return yawn_logical_hp(raw, logical_max=YAWN_LOGICAL_MAX_DEFAULT)


def _yawn_in_combat(ent: dict[str, Any]) -> bool:
    """True when Yawn is an active combatant (in-room / active, HP remaining)."""
    logical = _logical_hp(ent)
    raw = _raw_hp(ent)
    if logical <= 0 and raw <= 0:
        return False
    active = ent.get("active_byte")
    if active is not None and int(active) == 0:
        return False
    in_room = int(ent.get("in_room", ent.get("alive", 0)) or 0)
    return bool(in_room)


def find_yawn_entities(
    enemies: list[dict[str, Any]] | None,
    *,
    room_id: str | None,
) -> list[dict[str, Any]]:
    """Enemy rows that are Yawn (room ``210`` + raw-HP / translate flag)."""
    room = str(room_id or "").strip().upper()
    if room != YAWN_ROOM:
        return []
    out: list[dict[str, Any]] = []
    for ent in enemies or []:
        if not isinstance(ent, dict):
            continue
        if ent.get("yawn_translated"):
            out.append(ent)
            continue
        raw = _raw_hp(ent)
        if is_yawn_raw_hp(raw, room_id=room):
            out.append(ent)
    return out


def _primary_yawn(
    enemies: list[dict[str, Any]] | None,
    *,
    room_id: str | None,
) -> dict[str, Any] | None:
    found = find_yawn_entities(enemies, room_id=room_id)
    if not found:
        return None
    # Prefer in-combat, then highest remaining logical HP.
    found.sort(
        key=lambda e: (1 if _yawn_in_combat(e) else 0, _logical_hp(e)),
        reverse=True,
    )
    return found[0]


def yawn_poison_active(state: dict[str, Any] | None) -> bool:
    """True when player poison RAM / state flag is set."""
    if not state:
        return False
    if "poisoned" in state:
        return bool(state.get("poisoned"))
    return bool(int(state.get("player_poison", 0) or 0))


def yawn_contact_edge(
    state: dict[str, Any] | None,
    prev_state: dict[str, Any] | None,
    *,
    enemies: list[dict[str, Any]] | None,
    prev_enemies: list[dict[str, Any]] | None = None,
) -> bool:
    """Rising edge: Yawn enters combat in room ``210``."""
    room = _room_id(state)
    if room != YAWN_ROOM:
        return False
    cur = _primary_yawn(enemies, room_id=room)
    if cur is None or not _yawn_in_combat(cur):
        return False
    prev_room = _room_id(prev_state)
    prev_list = prev_enemies
    if prev_list is None and prev_state is not None:
        prev_list = prev_state.get("enemies")  # type: ignore[assignment]
    prev = _primary_yawn(prev_list, room_id=prev_room or room)
    if prev is None:
        return True
    return not _yawn_in_combat(prev)


def yawn_retreat_detected(
    state: dict[str, Any] | None,
    prev_state: dict[str, Any] | None,
    *,
    enemies: list[dict[str, Any]] | None,
    prev_enemies: list[dict[str, Any]] | None = None,
) -> bool:
    """Yawn took damage, left combat, and the player is still alive.

    Attic fight-1 ends in retreat (not a true kill). Detected when logical HP
    dropped versus the previous step (or was already chipped) and Yawn is no
    longer in combat (left the table or inactive).
    """
    if not _player_alive(state):
        return False
    room = _room_id(state)
    prev_room = _room_id(prev_state)
    # Retreat may settle while still in 210, or on the leave transition.
    if room != YAWN_ROOM and prev_room != YAWN_ROOM:
        return False

    prev_list = prev_enemies
    if prev_list is None and prev_state is not None:
        prev_list = prev_state.get("enemies")  # type: ignore[assignment]
    prev = _primary_yawn(prev_list, room_id=prev_room or YAWN_ROOM)
    if prev is None or not _yawn_in_combat(prev):
        return False
    prev_hp = _logical_hp(prev)
    damaged_before = prev_hp < YAWN_LOGICAL_MAX_DEFAULT

    cur = _primary_yawn(
        enemies, room_id=room if room == YAWN_ROOM else YAWN_ROOM
    )
    if cur is None:
        # Yawn left the table after taking damage → attic scripted retreat.
        return damaged_before

    cur_hp = _logical_hp(cur)
    if _yawn_in_combat(cur):
        return False
    # Inactive / left combat after a chip this step or earlier in the fight.
    return cur_hp < prev_hp or damaged_before


def yawn_telemetry(result: YawnOutcomeResult) -> dict[str, Any]:
    """Flatten detector result for ``info`` logging."""
    tel = dict(result.telemetry)
    tel.update(
        {
            "yawn_outcome": result.outcome.value,
            "yawn_contact": bool(result.contact),
            "yawn_poisoned": bool(result.poisoned),
            "yawn_damaged": bool(result.damaged),
            "yawn_retreat": bool(result.retreat),
            "yawn_killed": bool(result.killed),
            "yawn_player_death": bool(result.player_death),
            "yawn_logical_hp": result.yawn_logical_hp,
            "yawn_logical_hp_prev": result.yawn_logical_hp_prev,
            "yawn_in_combat": bool(result.yawn_in_combat),
            "yawn_in_combat_prev": bool(result.yawn_in_combat_prev),
            "yawn_room": YAWN_ROOM,
        }
    )
    return tel


def detect_yawn_outcome(
    state: dict[str, Any] | None,
    prev_state: dict[str, Any] | None,
    progress: Any = None,
    *,
    enemies: list[dict[str, Any]] | None = None,
    prev_enemies: list[dict[str, Any]] | None = None,
) -> YawnOutcomeResult:
    """Classify the Yawn transition for this step.

    ``progress`` is accepted for call-site uniformity (Kenneth / gallery hooks
    stay elsewhere); outcomes are driven by room ``210`` enemy + player RAM.
    """
    _ = progress  # reserved for future episode-side Yawn flags
    room = _room_id(state)
    prev_room = _room_id(prev_state)

    if enemies is None and state is not None:
        enemies = state.get("enemies")  # type: ignore[assignment]
    if prev_enemies is None and prev_state is not None:
        prev_enemies = prev_state.get("enemies")  # type: ignore[assignment]

    result = YawnOutcomeResult()
    player_death = bool(state and state.get("dead")) or (
        room == YAWN_ROOM and not _player_alive(state) and _player_alive(prev_state)
    )
    result.player_death = bool(player_death)

    prev = _primary_yawn(prev_enemies, room_id=prev_room or YAWN_ROOM)
    cur = _primary_yawn(enemies, room_id=room if room == YAWN_ROOM else None)

    result.yawn_logical_hp = _logical_hp(cur) if cur is not None else None
    result.yawn_logical_hp_prev = _logical_hp(prev) if prev is not None else None
    result.yawn_in_combat = bool(cur is not None and _yawn_in_combat(cur))
    result.yawn_in_combat_prev = bool(prev is not None and _yawn_in_combat(prev))

    contact = yawn_contact_edge(
        state, prev_state, enemies=enemies, prev_enemies=prev_enemies
    )
    result.contact = contact

    poisoned = bool(
        (room == YAWN_ROOM or prev_room == YAWN_ROOM)
        and yawn_poison_active(state)
        and (result.yawn_in_combat or result.yawn_in_combat_prev or contact)
    )
    # Also count poison if already fighting / previously contacted Yawn.
    if room == YAWN_ROOM and yawn_poison_active(state) and (
        cur is not None or prev is not None
    ):
        poisoned = True
    result.poisoned = poisoned

    damaged = False
    if (
        cur is not None
        and prev is not None
        and result.yawn_logical_hp is not None
        and result.yawn_logical_hp_prev is not None
        and result.yawn_logical_hp < result.yawn_logical_hp_prev
    ):
        damaged = True
    result.damaged = damaged

    # True kill: raw HP pool hits 0 (library). Attic retreat keeps raw >> 0.
    killed = False
    if (
        prev is not None
        and result.yawn_in_combat_prev
        and _raw_hp(prev) > 0
        and room == YAWN_ROOM
    ):
        if cur is not None and _raw_hp(cur) <= 0:
            killed = True
        elif cur is None and _logical_hp(prev) <= 0 and _raw_hp(prev) <= YAWN_LOGICAL_MAX_DEFAULT:
            # Degenerate: only raw-collapsed rows omit the entity entirely.
            killed = _raw_hp(prev) <= 0
    result.killed = killed

    retreat = False
    if not player_death and not killed:
        retreat = yawn_retreat_detected(
            state, prev_state, enemies=enemies, prev_enemies=prev_enemies
        )
    result.retreat = retreat

    # Priority: death > kill > retreat > damage > poison > contact > none
    if player_death and (room == YAWN_ROOM or prev_room == YAWN_ROOM):
        outcome = YawnOutcome.PLAYER_DEATH
    elif killed:
        outcome = YawnOutcome.KILLED
    elif retreat:
        outcome = YawnOutcome.RETREAT
    elif damaged:
        outcome = YawnOutcome.DAMAGED
    elif poisoned:
        outcome = YawnOutcome.POISONED
    elif contact:
        outcome = YawnOutcome.CONTACT
    else:
        outcome = YawnOutcome.NONE
    result.outcome = outcome
    result.telemetry = yawn_telemetry(result)
    return result
