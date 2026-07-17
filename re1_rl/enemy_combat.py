"""Enemy HP deltas from live RAM table reads."""

from __future__ import annotations

from typing import Any


def alive_enemy_count(enemies: list[dict[str, Any]] | None) -> int:
    """Enemies with in-room coordinates (excludes off-map pool ghosts)."""
    n = 0
    for ent in enemies or []:
        if not ent.get("alive", True):
            continue
        if int(ent.get("hp", 0)) > 0:
            n += 1
    return n


def combat_enemy_count(enemies: list[dict[str, Any]] | None) -> int:
    """Enemies near enough to justify knife/attack (in-room + within combat range)."""
    n = 0
    for ent in enemies or []:
        if int(ent.get("hp", 0)) <= 0:
            continue
        if int(ent.get("combat_near", 0)):
            n += 1
    return n


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


def enemy_combat_events(
    prev_enemies: list[dict[str, Any]] | None,
    curr_enemies: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Per-slot HP changes: slot, hp_before, hp_after, damage, killed."""
    prev_hps = enemy_hp_by_slot(prev_enemies or [])
    curr_hps = enemy_hp_by_slot(curr_enemies or [])
    events: list[dict[str, Any]] = []
    for slot in sorted(set(prev_hps) | set(curr_hps)):
        before = int(prev_hps.get(slot, 0))
        after = int(curr_hps.get(slot, 0))
        if before <= 0:
            continue
        if after <= 0:
            events.append({
                "slot": slot,
                "hp_before": before,
                "hp_after": 0,
                "damage": before,
                "killed": True,
            })
        elif after < before:
            events.append({
                "slot": slot,
                "hp_before": before,
                "hp_after": after,
                "damage": before - after,
                "killed": False,
            })
    return events


def format_enemy_table(enemies: list[dict[str, Any]] | None) -> str:
    """Compact RAM enemy table: ``s0:hp61 s1:hp48``."""
    if not enemies:
        return "-"
    parts: list[str] = []
    for ent in sorted(enemies, key=lambda e: int(e.get("slot", 99))):
        slot = int(ent.get("slot", -1))
        hp = int(ent.get("hp", 0))
        if slot < 0 or hp <= 0:
            continue
        extra = ""
        if "x" in ent and "z" in ent:
            extra = f"@{int(ent['x'])},{int(ent['z'])}"
        if "type_id" in ent:
            extra += f",t{int(ent['type_id'])}"
        parts.append(f"s{slot}:hp{hp}{extra}")
    return " ".join(parts) if parts else "-"


def apply_combat_step_fields(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    *,
    knife: bool = False,
    attack: bool = False,
) -> dict[str, Any]:
    """Attach ``enemy_damage`` / ``enemy_kills`` (and miss flags) like ``env.step``.

    Room changes unload the previous room's enemy table — that must not count as
    kills (door-loop farm: exit tea room → Kenneth slot vanishes → +damage/+kill).
    """
    out = dict(state)
    prev_room = str(prev_state.get("room_id", "") or "")
    curr_room = str(out.get("room_id", "") or "")
    if prev_room and curr_room and prev_room != curr_room:
        out["enemy_damage"] = 0
        out["enemy_kills"] = 0
        out["combat_events"] = []
        return out

    prev_enemies = list(prev_state.get("enemies", []) or [])
    curr_enemies = list(out.get("enemies", []) or [])
    prev_enemy_hps = enemy_hp_by_slot(prev_enemies)
    curr_enemy_hps = enemy_hp_by_slot(curr_enemies)
    enemy_damage, enemy_kills = enemy_combat_delta(prev_enemy_hps, curr_enemy_hps)
    combat_events = enemy_combat_events(prev_enemies, curr_enemies)
    out["enemy_damage"] = enemy_damage
    out["enemy_kills"] = enemy_kills
    out["combat_events"] = combat_events
    if (knife or attack) and enemy_damage == 0 and enemy_kills == 0:
        out["knife_swing_missed"] = knife
        out["attack_missed"] = attack
    return out
