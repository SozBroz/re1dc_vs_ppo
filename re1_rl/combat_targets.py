"""Aligned combat / whole-game auxiliary targets for combat-efficient PPO.

Targets are derived from *post-action* telemetry and packed against the
*pre-action* observation already stored in the rollout. Predictor inputs must
never include the same-transition post-action outcome vector.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION, ATTACK_UP_ACTION
from re1_rl.weapon_damage import (
    AMMO_QTY_NORM,
    DMG_NORM,
    KILLS_NORM,
    LAST_ATTACK_MACRO_DOWN,
    LAST_ATTACK_MACRO_NEUTRAL,
    LAST_ATTACK_MACRO_UP,
    last_attack_macro_from_action,
)

# Per attack height: hit, wasted_round, expected_damage, kill, ammo_spent, macro_failure
OUTCOMES_PER_HEIGHT = 6
N_ATTACK_HEIGHTS = 3
COMBAT_OUTCOME_DIM = OUTCOMES_PER_HEIGHT * N_ATTACK_HEIGHTS  # 18

# Compact rollout vector — no unsupervised channels (player_damage removed).
COMBAT_TARGET_FIELDS: tuple[str, ...] = (
    "selected_height",  # 0=neutral, 1=up, 2=down, -1=non-attack
    "hit",
    "wasted_round",
    "damage",
    "kill",
    "ammo_spent",
    "macro_failure",
    "mask",  # 1 = supervise this transition
)
COMBAT_TARGET_DIM = len(COMBAT_TARGET_FIELDS)  # 8
COMBAT_TARGET_MASK_INDEX = 7

WORLD_EVENT_FIELDS: tuple[str, ...] = (
    "room_transition",
    "item_pickup",
    "story_item_use",
    "document_or_cutscene",
    "menu_or_action_success",
    "damage_taken",
    "combat_hit",
    "combat_kill",
    "combat_wasted",
    "combat_macro_fail",
) + tuple(f"reserved_{i}" for i in range(34))
# First 10 are active; remaining 34 pad to 44 for the aux head.
WORLD_EVENT_DIM = 44
WORLD_EVENT_ACTIVE = 10

_ATTACK_ACTIONS = frozenset({ATTACK_ACTION, ATTACK_UP_ACTION, ATTACK_DOWN_ACTION})


def empty_combat_target() -> np.ndarray:
    v = np.zeros(COMBAT_TARGET_DIM, dtype=np.float32)
    v[0] = -1.0
    return v


def empty_world_event_target() -> np.ndarray:
    return np.zeros(WORLD_EVENT_DIM, dtype=np.float32)


def empty_world_event_mask() -> np.ndarray:
    """1 for active factual channels; 0 for reserved pads."""
    m = np.zeros(WORLD_EVENT_DIM, dtype=np.float32)
    m[:WORLD_EVENT_ACTIVE] = 1.0
    return m


def is_attack_action(action_id: int) -> bool:
    return int(action_id) in _ATTACK_ACTIONS


def pack_combat_target(
    *,
    action_id: int,
    hit: bool = False,
    damage: float = 0.0,
    kills: float = 0.0,
    ammo_spent: float = 0.0,
    macro_failure: bool = False,
    knife: bool = False,
) -> np.ndarray:
    """Pack supervised combat outcomes for one transition.

    Non-attack actions return ``mask=0`` (no counterfactual height labels).
    """
    v = empty_combat_target()
    macro = last_attack_macro_from_action(int(action_id))
    if macro is None:
        return v
    hit_f = 1.0 if hit else 0.0
    spent = float(ammo_spent)
    wasted = 0.0
    if not knife and spent > 0.0 and not hit:
        wasted = 1.0
    elif knife and not hit:
        wasted = 1.0
    v[0] = float(macro)
    v[1] = hit_f
    v[2] = wasted
    v[3] = float(np.clip(float(damage) / DMG_NORM, 0.0, 1.0))
    v[4] = float(np.clip(float(kills) / KILLS_NORM, 0.0, 1.0))
    v[5] = float(np.clip(spent / AMMO_QTY_NORM, 0.0, 1.0))
    v[6] = 1.0 if macro_failure else 0.0
    v[COMBAT_TARGET_MASK_INDEX] = 1.0
    return v


def pack_combat_target_from_info(
    action_id: int,
    info: dict[str, Any] | None,
    *,
    prev_hp: float | None = None,
) -> np.ndarray:
    """Build combat targets from env ``info`` after a step."""
    del prev_hp  # player HP delta is a world-event channel, not combat 18-d
    info = info or {}
    if not is_attack_action(action_id):
        return empty_combat_target()

    report = info.get("attack_report") or {}
    knife_report = info.get("knife_anim_report")
    state = info.get("state") or {}
    damage = float(state.get("enemy_damage", report.get("enemy_damage", 0)) or 0)
    kills = float(state.get("enemy_kills", report.get("enemy_kills", 0)) or 0)
    ammo_spent = float(state.get("ammo_spent", report.get("ammo_spent", 0)) or 0)
    hit = damage > 0 or kills > 0
    outcome = str(report.get("outcome", "ok") or "ok")
    macro_failure = outcome not in ("ok", "", "none") and outcome != "ok"
    if knife_report and knife_report.get("failed"):
        macro_failure = True
    knife = bool(report.get("weapon") == "knife" or knife_report is not None)
    equipped = state.get("equipped_weapon_id")
    if equipped == 0x01:
        knife = True
    return pack_combat_target(
        action_id=int(action_id),
        hit=hit,
        damage=damage,
        kills=kills,
        ammo_spent=ammo_spent,
        macro_failure=macro_failure,
        knife=knife,
    )


def pack_world_event_target_from_info(
    action_id: int,
    info: dict[str, Any] | None,
    *,
    prev_room: str | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Factual whole-game consequences; masks unavailable / reserved channels."""
    info = info or {}
    y = empty_world_event_target()
    m = empty_world_event_mask()
    state = info.get("state") or {}
    room = str(info.get("room_id") or state.get("room_id") or "")
    if prev_room is not None and room and str(prev_room) != room:
        y[0] = 1.0
    new_items = info.get("new_items") or state.get("new_items") or []
    y[1] = 1.0 if new_items else 0.0
    bd = info.get("reward_breakdown") or {}
    y[2] = 1.0 if float(bd.get("story_item_use", 0.0) or 0.0) > 0 else 0.0
    y[3] = 1.0 if (
        float(bd.get("document_examine", 0.0) or 0.0) > 0
        or float(bd.get("cutscene", 0.0) or 0.0) > 0
        or int(info.get("frames_skipped", 0) or 0) > 0
    ) else 0.0
    magic = info.get("magic_report") or {}
    y[4] = 1.0 if (
        magic
        or float(bd.get("inventory_combine", 0.0) or 0.0) > 0
        or str(info.get("action_name", "")).startswith(("deposit_", "withdraw_", "select_slot"))
    ) else 0.0
    y[5] = 1.0 if info.get("damage_taken") else 0.0
    combat = pack_combat_target_from_info(action_id, info)
    if combat[COMBAT_TARGET_MASK_INDEX] > 0.5:
        y[6] = combat[1]
        y[7] = 1.0 if combat[4] > 0 else 0.0
        y[8] = combat[2]
        y[9] = combat[6]
    m[WORLD_EVENT_ACTIVE:] = 0.0
    return y, m


def combat_outcome_supervision_mask(selected_height: np.ndarray | float) -> np.ndarray:
    """(18,) mask: ones only on the executed height's 6 outcomes."""
    mask = np.zeros(COMBAT_OUTCOME_DIM, dtype=np.float32)
    h = int(selected_height)
    if 0 <= h < N_ATTACK_HEIGHTS:
        base = h * OUTCOMES_PER_HEIGHT
        mask[base : base + OUTCOMES_PER_HEIGHT] = 1.0
    return mask


def combat_target_to_outcome_vector(target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Expand compact target → (18,) values and (18,) height mask."""
    y = np.zeros(COMBAT_OUTCOME_DIM, dtype=np.float32)
    m = np.zeros(COMBAT_OUTCOME_DIM, dtype=np.float32)
    if float(target[COMBAT_TARGET_MASK_INDEX]) < 0.5:
        return y, m
    h = int(target[0])
    if h < 0 or h >= N_ATTACK_HEIGHTS:
        return y, m
    base = h * OUTCOMES_PER_HEIGHT
    # hit, wasted, damage, kill, ammo_spent, macro_failure
    y[base + 0] = float(target[1])
    y[base + 1] = float(target[2])
    y[base + 2] = float(target[3])
    y[base + 3] = float(target[4])
    y[base + 4] = float(target[5])
    y[base + 5] = float(target[6])
    m[base : base + OUTCOMES_PER_HEIGHT] = 1.0
    return y, m


def height_index_name(height: int) -> str:
    return {
        LAST_ATTACK_MACRO_NEUTRAL: "neutral",
        LAST_ATTACK_MACRO_UP: "up",
        LAST_ATTACK_MACRO_DOWN: "down",
    }.get(int(height), "none")
