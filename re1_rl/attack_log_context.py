"""Room/enemy context for ``[attack_swing]`` fleet logs (no emulator)."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from re1_rl.enemy_combat import enemy_combat_events, format_enemy_table

_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"

# Partial map from RDT model ids (see scripts/merge_rdt_into_data.py).
_MODEL_TO_TYPE: dict[int, str] = {
    1: "zombie",
    2: "zombie",
    3: "zombie_dog",
    4: "zombie_dog",
    17: "zombie",
    18: "zombie",
}


@lru_cache(maxsize=1)
def _rooms_table() -> dict[str, dict[str, Any]]:
    path = _DATA_ROOT / "rooms.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _room_enemies_table() -> dict[str, dict[str, Any]]:
    path = _DATA_ROOT / "room_enemies.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def room_display_name(room_id: str | None) -> str:
    if not room_id:
        return "?"
    info = _rooms_table().get(str(room_id), {})
    return str(info.get("name") or room_id)


def room_roster_summary(room_id: str | None) -> str:
    """e.g. ``zombie×2,crow×1`` from static room_enemies.json."""
    if not room_id:
        return "-"
    block = _room_enemies_table().get(str(room_id))
    if not isinstance(block, dict):
        return "-"
    tallies: dict[str, int] = {}
    for row in block.get("enemies", []):
        etype = str(row.get("enemy_type", "")).strip().lower()
        if not etype:
            continue
        n = int(row.get("count", 1))
        tallies[etype] = tallies.get(etype, 0) + max(n, 1)
    if not tallies:
        return "-"
    return ",".join(f"{k}×{v}" for k, v in sorted(tallies.items()))


def _static_spawns(room_id: str) -> list[dict[str, Any]]:
    block = _room_enemies_table().get(str(room_id))
    if not isinstance(block, dict):
        return []
    out: list[dict[str, Any]] = []
    for row in block.get("enemies", []):
        if "x" not in row or "z" not in row:
            continue
        out.append(row)
    return out


def _infer_type_for_slot(room_id: str, slot: int) -> str:
    block = _room_enemies_table().get(str(room_id))
    if not isinstance(block, dict):
        return "?"
    rows = block.get("enemies", [])
    roster_types = {
        str(r.get("enemy_type", "")).strip().lower()
        for r in rows
        if r.get("enemy_type")
    }
    roster_types.discard("")
    if len(roster_types) == 1:
        return next(iter(roster_types))
    # Multiple types: best-effort by model_id order in static table.
    typed = [
        r for r in rows
        if r.get("enemy_type") and int(r.get("count", 1)) > 0
    ]
    if 0 <= slot < len(typed):
        return str(typed[slot].get("enemy_type", "?"))
    mid = None
    for r in rows:
        if int(r.get("slot", -1)) == slot and "model_id" in r:
            mid = int(r["model_id"])
            break
    if mid is not None:
        return _MODEL_TO_TYPE.get(mid, f"model{mid}")
    return "?"


def nearest_static_enemy(
    room_id: str,
    px: int,
    pz: int,
) -> tuple[str, int, int, float] | None:
    """(enemy_type, x, z, dist) of closest static spawn in room, if any."""
    best: tuple[float, str, int, int] | None = None
    for row in _static_spawns(room_id):
        ex = int(row["x"])
        ez = int(row["z"])
        dist = math.hypot(ex - px, ez - pz)
        etype = str(row.get("enemy_type", "?"))
        if best is None or dist < best[0]:
            best = (dist, etype, ex, ez)
    if best is None:
        return None
    dist, etype, ex, ez = best
    return etype, ex, ez, dist


def format_combat_events(
    events: list[dict[str, Any]],
    *,
    room_id: str,
) -> str:
    if not events:
        return "-"
    parts: list[str] = []
    for ev in events:
        slot = int(ev["slot"])
        etype = _infer_type_for_slot(room_id, slot)
        before = int(ev["hp_before"])
        after = int(ev["hp_after"])
        dmg = int(ev["damage"])
        killed = bool(ev.get("killed"))
        tag = "KILL" if killed else "HIT"
        parts.append(
            f"s{slot}:{etype}:{before}->{after}(-{dmg},{tag})"
        )
    return "|".join(parts)


def build_attack_log_context(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Structured context attached to each attack log line."""
    prev_state = prev_state or {}
    room_id = str(state.get("room_id", "?"))
    px = int(state.get("x", 0))
    pz = int(state.get("z", 0))
    prev_enemies = list(prev_state.get("enemies") or [])
    curr_enemies = list(state.get("enemies") or [])
    events = enemy_combat_events(prev_enemies, curr_enemies)

    nearest = nearest_static_enemy(room_id, px, pz)
    target_guess = "-"
    if events:
        slot = int(events[0]["slot"])
        target_guess = _infer_type_for_slot(room_id, slot)
    elif nearest is not None:
        target_guess = f"near_{nearest[0]}"

    ctx = {
        "room_id": room_id,
        "room_name": room_display_name(room_id),
        "room_roster": room_roster_summary(room_id),
        "player_hp": int(state.get("hp", 0)),
        "player_hp_before": int(prev_state.get("hp", 0)),
        "facing": int(state.get("facing", 0)),
        "cam_id": int(state.get("cam_id", 0)),
        "in_control": bool(state.get("in_control", True)),
        "equipped_weapon_id": int(state.get("equipped_weapon_id", 0)),
        "attack_weapon": state.get("attack_weapon"),
        "pos": (px, pz),
        "enemies_before": format_enemy_table(prev_enemies),
        "enemies_after": format_enemy_table(curr_enemies),
        "combat_events": events,
        "combat_summary": format_combat_events(events, room_id=room_id),
        "target_guess": target_guess,
        "nearest_static": nearest,
    }
    return ctx


def format_attack_context_line(ctx: dict[str, Any]) -> str:
    """Second log line with room/enemy detail."""
    room_id = ctx.get("room_id", "?")
    room_name = ctx.get("room_name", "?")
    roster = ctx.get("room_roster", "-")
    px, pz = ctx.get("pos", (0, 0))
    nearest = ctx.get("nearest_static")
    near_s = "-"
    if nearest is not None:
        etype, ex, ez, dist = nearest
        near_s = f"{etype}@({ex},{ez}) dist={dist:.0f}"

    return (
        f"[attack_ctx] room={room_id}({room_name}) roster={roster} "
        f"player_hp={ctx.get('player_hp_before', '?')}->{ctx.get('player_hp', '?')} "
        f"facing={ctx.get('facing', '?')} cam={ctx.get('cam_id', '?')} "
        f"in_control={int(bool(ctx.get('in_control', True)))} "
        f"eq_id=0x{int(ctx.get('equipped_weapon_id', 0)):02X} "
        f"target_guess={ctx.get('target_guess', '?')} nearest_static={near_s} "
        f"before=[{ctx.get('enemies_before', '-')}] "
        f"after=[{ctx.get('enemies_after', '-')}] "
        f"events={ctx.get('combat_summary', '-')}"
    )
