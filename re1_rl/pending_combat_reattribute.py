"""Move delayed dog/gun combat pay onto the fire that armed the window.

HP often posts after the attack macro returns. Env pays ``enemy_damage`` /
``enemy_kill`` on the later nav step (``credited_from_pending``). PPO then
trains “walk after shooting.” This FIFO puts that pay on the oldest unmatched
armed attack still in the current rollout buffer.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableSequence

import numpy as np

from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION, ATTACK_UP_ACTION

ATTACK_ACTION_IDS: frozenset[int] = frozenset(
    {ATTACK_UP_ACTION, ATTACK_ACTION, ATTACK_DOWN_ACTION}
)
COMBAT_PAY_KEYS: tuple[str, ...] = ("enemy_damage", "enemy_kill")
_FAILED_MACRO: frozenset[str] = frozenset(
    {
        "aim_timeout",
        "ammo_timeout",
        "settle_timeout",
        "aborted_interrupt",
        "dry_fire",
        "illegal_attack",
        "no_weapon",
    }
)
KNIFE_WEAPON_ID = 0x01


def combat_pay_from_breakdown(breakdown: Mapping[str, Any] | None) -> float:
    """Positive ``enemy_damage`` + ``enemy_kill`` only (leave taxes on the fire)."""
    if not breakdown:
        return 0.0
    pay = 0.0
    for key in COMBAT_PAY_KEYS:
        value = float(breakdown.get(key) or 0.0)
        if value > 0.0:
            pay += value
    return pay


def is_armed_attack(action: int, info: Mapping[str, Any] | None) -> bool:
    """True when this step opened a pending HP window (gun spend or knife swing)."""
    if int(action) not in ATTACK_ACTION_IDS:
        return False
    report = (info or {}).get("attack_report") or {}
    outcome = str(report.get("outcome") or "")
    if outcome in _FAILED_MACRO:
        return False
    if int(report.get("ammo_spent") or 0) > 0:
        return True
    audit = (info or {}).get("combat_audit") or {}
    weapon_id = int(
        report.get("weapon_id")
        or audit.get("equipped_weapon_id")
        or 0
    )
    weapon_name = str(report.get("weapon") or "")
    return weapon_id == KNIFE_WEAPON_ID or weapon_name == "knife"


def reattribute_pending_combat(
    *,
    fire_queue: MutableSequence[int],
    current_index: int,
    reward: float,
    rewards: np.ndarray,
    credited_from_pending: bool,
    combat_pay: float,
) -> tuple[float, float, int | None]:
    """Move ``combat_pay`` from this step onto the oldest queued fire.

    Returns ``(current_reward, moved, target_index)``. If the fire is no
    longer in this buffer, pay stays here so we do not drop it.
    """
    if (
        not credited_from_pending
        or combat_pay <= 0.0
        or not fire_queue
    ):
        return float(reward), 0.0, None
    target = int(fire_queue[0])
    if target < 0 or target >= int(current_index):
        return float(reward), 0.0, None
    fire_queue.pop(0)
    rewards[target] = float(rewards[target]) + float(combat_pay)
    return float(reward) - float(combat_pay), float(combat_pay), target
