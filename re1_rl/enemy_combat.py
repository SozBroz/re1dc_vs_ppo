"""Enemy HP deltas from live RAM table reads."""

from __future__ import annotations

from typing import Any


def enemy_hp_by_slot(enemies: list[dict[str, Any]]) -> dict[int, int]:
    """Map enemy table slot index -> HP (living enemies only)."""
    out: dict[int, int] = {}
    for ent in enemies:
        slot = int(ent.get("slot", -1))
        hp = int(ent.get("hp", 0))
        if slot >= 0 and hp > 0:
            out[slot] = hp
    return out


def enemy_combat_delta(
    prev: dict[int, int], curr: dict[int, int]
) -> tuple[int, int]:
    """Return (total_damage_dealt, kill_count) across enemy slots."""
    damage = 0
    kills = 0
    for slot in set(prev) | set(curr):
        before = int(prev.get(slot, 0))
        after = int(curr.get(slot, 0))
        if before <= 0:
            continue
        if after <= 0:
            kills += 1
            damage += before
        elif after < before:
            damage += before - after
    return damage, kills


def apply_combat_step_fields(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    *,
    knife: bool = False,
    attack: bool = False,
) -> dict[str, Any]:
    """Attach ``enemy_damage`` / ``enemy_kills`` (and miss flags) like ``env.step``."""
    out = dict(state)
    prev_enemy_hps = enemy_hp_by_slot(prev_state.get("enemies", []))
    curr_enemy_hps = enemy_hp_by_slot(out.get("enemies", []))
    enemy_damage, enemy_kills = enemy_combat_delta(prev_enemy_hps, curr_enemy_hps)
    out["enemy_damage"] = enemy_damage
    out["enemy_kills"] = enemy_kills
    if (knife or attack) and enemy_damage == 0 and enemy_kills == 0:
        out["knife_swing_missed"] = knife
        out["attack_missed"] = attack
    return out
