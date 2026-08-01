"""Human-readable decoding for memlog observations and policy decisions.

The decoder deliberately accepts plain JSON values rather than numpy arrays so
the browser dashboard can inspect snapshots without importing the environment.
Known observation keys get repository-backed names and units; unknown keys are
still emitted as indexed rows.
"""

from __future__ import annotations

import base64
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from re1_rl.cutscene_ledger import CUTSCENE_MILESTONE_KEYS
from re1_rl.episode_history import ACQUISITION_LOG_K, ROOM_DEQUE_K
from re1_rl.key_items import KEY_ITEM_NAMES
from re1_rl.maps_files import MAPS_FILES_DIM
from re1_rl.memory_map import ITEM_IDS
from re1_rl.milestone_features import MILESTONE_FEATURE_NAMES
from re1_rl.obs_encoder import BOX_FIELDS, GOAL_FIELDS, PROPRIO_FIELDS
from re1_rl.room_signature import ENEMY_ROSTER_TYPES
from re1_rl.spatial_encoder import SPATIAL_FIELDS
from re1_rl.weapon_damage import LAST_ATTACK_FIELDS, WEAPON_CARD_FIELDS

ROOT = Path(__file__).resolve().parents[1]
FRAME_SHAPE = (4, 63, 84)


@lru_cache(maxsize=1)
def _rooms() -> tuple[tuple[str, str], ...]:
    path = ROOT / "data" / "rooms.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(
        (room_id, str(raw[room_id].get("name", "")))
        for room_id in sorted(raw)
        if isinstance(raw[room_id], dict)
    )


def _room_label(index: int) -> str:
    rooms = _rooms()
    if 0 <= index < len(rooms):
        room_id, name = rooms[index]
        return f"{room_id} — {name}" if name else room_id
    return f"unknown room index {index}"


def _flatten(value: Any) -> list[Any]:
    if isinstance(value, dict):
        for key in ("values", "data", "array"):
            if key in value and not isinstance(value[key], str):
                return _flatten(value[key])
        return [value]
    if isinstance(value, (list, tuple)):
        out: list[Any] = []
        for item in value:
            if isinstance(item, (list, tuple)):
                out.extend(_flatten(item))
            else:
                out.append(item)
        return out
    return [value]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _item_from_norm(value: float) -> str:
    item_id = int(round(value * 0x4B))
    return f"0x{item_id:02X} {ITEM_IDS.get(item_id, 'unknown')}"


def _room_from_norm(value: float) -> str:
    return _room_label(int(round(value * 128.0)))


def _qty_from_norm(value: float) -> str:
    return f"{int(round(value * 255.0))}"


def _plain(value: float) -> str:
    return f"{value:.5g}"


def _field_display(key: str, name: str, value: float) -> str:
    if name.endswith("item_id") or name == "equipped_weapon":
        return _item_from_norm(value)
    if name in {"room_index", "goal_room_index", "to_room"} or name.endswith("_room_index"):
        return _room_from_norm(value)
    if name.endswith("_qty") or name in {
        "equipped_clip", "clip_before", "clip_after", "ammo_spent"
    }:
        return _qty_from_norm(value)
    if name == "hp":
        return f"{value * 140.0:.1f} HP"
    if name.endswith("_hp_before") or name.endswith("_hp_after"):
        return f"{value * 255.0:.1f} HP"
    if name == "total_damage":
        return f"{value * 255.0:.1f} damage"
    if name == "type_id":
        return f"enemy type {int(round(value * 32.0))}"
    if name == "requires_key":
        idx = int(round(value * 128.0))
        if idx == 127:
            return "no known key requirement"
        if 0 <= idx < len(KEY_ITEM_NAMES):
            return KEY_ITEM_NAMES[idx]
        return f"key index {idx}"
    if name == "kind_id":
        kinds = ("none", "item box", "typewriter", "trigger")
        idx = int(round(value * 3.0))
        return kinds[idx] if 0 <= idx < len(kinds) else f"kind {idx}"
    return _plain(value)


def _row(
    index: int,
    name: str,
    meaning: str,
    raw: Any,
    *,
    group: str = "fields",
    padding: bool = False,
    display: str | None = None,
) -> dict[str, Any]:
    number = _number(raw)
    return {
        "index": index,
        "name": name,
        "raw": number if number is not None else raw,
        "display": display if display is not None else (
            _plain(number) if number is not None else str(raw)
        ),
        "meaning": meaning,
        "group": group,
        "zero": number == 0.0,
        "padding": bool(padding),
    }


def _named_rows(
    key: str,
    value: Any,
    fields: Iterable[tuple[str, str]],
) -> list[dict[str, Any]]:
    values = _flatten(value)
    specs = list(fields)
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(values):
        if index < len(specs):
            name, meaning = specs[index]
        else:
            name, meaning = f"{key}[{index}]", "Unrecognized trailing field"
        number = _number(raw)
        prefix = name.split("_", 1)[0]
        is_slot = bool(prefix and prefix[-1:].isdigit())
        group = prefix if is_slot else "summary"
        rows.append(
            _row(
                index,
                name,
                meaning,
                raw,
                group=group,
                padding=is_slot and number == 0.0,
                display=_field_display(key, name, number) if number is not None else None,
            )
        )
    return rows


def _inventory_rows(value: Any) -> list[dict[str, Any]]:
    values = _flatten(value)
    rows = []
    for index, raw in enumerate(values):
        slot = index // 2
        is_item = index % 2 == 0
        name = f"slot{slot}_{'item_id' if is_item else 'qty'}"
        number = _number(raw)
        rows.append(
            _row(
                index,
                name,
                "inventory item id / 0x4B" if is_item else "quantity / 255",
                raw,
                group=f"slot{slot}",
                padding=number == 0.0,
                display=(
                    _item_from_norm(number) if is_item else _qty_from_norm(number)
                ) if number is not None else None,
            )
        )
    return rows


def _history_rows(value: Any) -> list[dict[str, Any]]:
    values = _flatten(value)
    rows = [_row(0, "valid_fraction", f"entries used / {ROOM_DEQUE_K}", values[0] if values else 0)]
    for index in range(1, len(values)):
        slot = (index - 1) // 2
        is_room = (index - 1) % 2 == 0
        number = _number(values[index])
        rows.append(
            _row(
                index,
                f"entry{slot}_{'room' if is_room else 'age'}",
                "room index / 128" if is_room else "steps since entry / episode limit",
                values[index],
                group=f"entry{slot}",
                padding=number == 0.0,
                display=_room_from_norm(number) if is_room and number is not None else None,
            )
        )
    return rows


def _acquisition_rows(value: Any) -> list[dict[str, Any]]:
    values = _flatten(value)
    rows = [
        _row(0, "valid_fraction", f"entries used / {ACQUISITION_LOG_K}", values[0] if values else 0)
    ]
    for index in range(1, len(values)):
        slot = (index - 1) // 2
        is_item = (index - 1) % 2 == 0
        number = _number(values[index])
        rows.append(
            _row(
                index,
                f"pickup{slot}_{'item' if is_item else 'room'}",
                "item id / 0x4B" if is_item else "room index / 128",
                values[index],
                group=f"pickup{slot}",
                padding=number == 0.0,
                display=(
                    _item_from_norm(number) if is_item else _room_from_norm(number)
                ) if number is not None else None,
            )
        )
    return rows


def _bit_rows(
    value: Any,
    names: Iterable[str],
    *,
    meaning: str,
    prefix: str,
) -> list[dict[str, Any]]:
    values = _flatten(value)
    labels = list(names)
    return [
        _row(
            i,
            labels[i] if i < len(labels) else f"{prefix}[{i}]",
            meaning,
            raw,
            padding=_number(raw) == 0.0,
        )
        for i, raw in enumerate(values)
    ]


def _world_state_rows(value: Any) -> list[dict[str, Any]]:
    values = _flatten(value)
    rows: list[dict[str, Any]] = []
    room_labels = [rid for rid, _ in _rooms()]
    n_keys = len(KEY_ITEM_NAMES)
    key_start = 250 + len(room_labels)
    for i, raw in enumerate(values):
        if i < 125:
            name, group, meaning = f"pickup_active[{i}]", "pickup active", "catalog pickup remains active"
        elif i < 250:
            name, group, meaning = f"pickup_gated[{i - 125}]", "pickup gated", "catalog pickup blocked by tracked requirements"
        elif i < key_start:
            ri = i - 250
            room = room_labels[ri] if ri < len(room_labels) else str(ri)
            name, group, meaning = f"room_remaining[{room}]", "room remaining", "remaining pickups / 4"
        elif i < key_start + n_keys:
            ki = i - key_start
            name, group, meaning = f"{KEY_ITEM_NAMES[ki]}:pickup_pending", "key hints", "key pickup still pending"
        elif i < key_start + 2 * n_keys:
            ki = i - key_start - n_keys
            name, group, meaning = f"{KEY_ITEM_NAMES[ki]}:use_pending", "key hints", "held key has a pending use"
        elif i < key_start + 3 * n_keys:
            ki = i - key_start - 2 * n_keys
            name, group, meaning = f"{KEY_ITEM_NAMES[ki]}:affordant_here", "key hints", "key applies in current room"
        else:
            name, group, meaning = f"world_state[{i}]", "unknown", "Unrecognized trailing field"
        rows.append(_row(i, name, meaning, raw, group=group, padding=_number(raw) == 0.0))
    return rows


def _affordance_rows(value: Any) -> list[dict[str, Any]]:
    fields = (
        ("item_name_length_proxy", "legacy key-name length / 32 (identity is not recoverable)"),
        ("primary_use_room", "primary use room index / 128"),
        ("affordant_here", "1 = applies in current room"),
        ("unlock_or_path_hint_room", "legacy next-hop/unlock room index / 128"),
        ("in_inventory", "1 = currently held"),
    )
    return _named_rows("affordances", value, [
        (f"slot{slot}_{name}", meaning)
        for slot in range(8)
        for name, meaning in fields
    ])


def decode_frame_planes(frame: Any) -> dict[str, Any]:
    """Validate a base64 uint8 4x63x84 frame and return browser-ready metadata."""
    if not isinstance(frame, dict):
        raise ValueError("frame must be an object with base64 data and shape")
    encoded = frame.get("base64", frame.get("data"))
    shape = frame.get("shape")
    dtype = str(frame.get("dtype", "uint8")).lower()
    if not isinstance(encoded, str):
        raise ValueError("frame base64 data is missing")
    if dtype not in {"uint8", "u8", "|u1"}:
        raise ValueError(f"frame dtype must be uint8, got {dtype}")
    try:
        dims = tuple(int(x) for x in shape)
    except (TypeError, ValueError):
        raise ValueError("frame shape is invalid") from None
    layout = "CHW"
    if dims == (63, 84, 4):
        layout = "HWC"
    elif dims != FRAME_SHAPE:
        raise ValueError(f"frame shape must be {FRAME_SHAPE} or (63, 84, 4), got {dims}")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("frame base64 data is invalid") from exc
    expected = math.prod(FRAME_SHAPE)
    if len(raw) != expected:
        raise ValueError(f"frame has {len(raw)} bytes; expected {expected}")
    return {
        "base64": encoded,
        "shape": list(dims),
        "dtype": "uint8",
        "layout": layout,
        "plane_shape": [63, 84],
        "plane_count": 4,
        "labels": ["oldest", "older", "newer", "newest"],
    }


Decoder = Callable[[Any], list[dict[str, Any]]]


def explain_observation(obs: Any) -> dict[str, Any]:
    """Return every observation key, with no silent drops."""
    if not isinstance(obs, dict):
        return {"_observation": {"rows": [_row(0, "value", "Malformed observation", obs)]}}
    decoders: dict[str, Decoder] = {
        "proprio": lambda v: _named_rows("proprio", v, PROPRIO_FIELDS),
        "goal": lambda v: _named_rows("goal", v, GOAL_FIELDS),
        "spatial": lambda v: _named_rows("spatial", v, SPATIAL_FIELDS),
        "box": lambda v: _named_rows("box", v, BOX_FIELDS),
        "inventory": _inventory_rows,
        "weapon_card": lambda v: _named_rows("weapon_card", v, WEAPON_CARD_FIELDS),
        "last_attack": lambda v: _named_rows("last_attack", v, LAST_ATTACK_FIELDS),
        "history": _history_rows,
        "acquisitions": _acquisition_rows,
        "room_enemies": lambda v: _bit_rows(
            v, ("total", *ENEMY_ROSTER_TYPES), meaning="static room enemy roster", prefix="enemy"
        ),
        "keys_held": lambda v: _bit_rows(
            v, KEY_ITEM_NAMES, meaning="1 = key item obtained this episode", prefix="key"
        ),
        "affordances": _affordance_rows,
        "world_state": _world_state_rows,
        "cutscene_ledger": lambda v: _bit_rows(
            v, CUTSCENE_MILESTONE_KEYS, meaning="1 = milestone seen", prefix="cutscene"
        ),
        "milestones": lambda v: _bit_rows(
            v, MILESTONE_FEATURE_NAMES, meaning="derived episode milestone", prefix="milestone"
        ),
        "maps_files": lambda v: _bit_rows(
            v, (f"maps_files_bit_{i}" for i in range(MAPS_FILES_DIM)),
            meaning="raw maps/files RAM bit (semantics not yet mapped)", prefix="maps_files"
        ),
        "rooms_visited": lambda v: _bit_rows(
            v, (_room_label(i) for i in range(128)),
            meaning="1 = room visited this episode", prefix="room"
        ),
    }
    out: dict[str, Any] = {}
    for key, value in obs.items():
        if key == "frame":
            try:
                out[key] = {"kind": "frame", "frame": decode_frame_planes(value)}
            except ValueError as exc:
                out[key] = {"kind": "frame", "error": str(exc)}
            continue
        if key == "visited":
            flat = _flatten(value)
            out[key] = {
                "kind": "grid",
                "shape": value.get("shape") if isinstance(value, dict) else None,
                "cells_seen": sum(1 for x in flat if (_number(x) or 0.0) > 0.0),
                "rows": [
                    _row(i, f"cell[{i // 16},{i % 16}]", "allocentric visited grid", raw,
                         group=f"row {i // 16}", padding=_number(raw) == 0.0)
                    for i, raw in enumerate(flat)
                ],
            }
            continue
        decoder = decoders.get(key)
        rows = decoder(value) if decoder else [
            _row(i, f"{key}[{i}]", "Unknown observation key; generic indexed fallback", raw)
            for i, raw in enumerate(_flatten(value))
        ]
        note = None
        if key == "goal":
            note = (
                "Route/compass fields are zeroed by the environment; the active Doc04 "
                "extractor omits this entire key. Gallery tail fields may still be populated."
            )
        elif key == "affordances":
            note = (
                "Compatibility vector retained for checkpoints; the active Doc04 extractor "
                "omits it. Active key affordances are represented in world_state."
            )
        out[key] = {"kind": "vector", "rows": rows, "note": note}
    return out


def action_presentation(snapshot: dict[str, Any], action_names: Iterable[str]) -> dict[str, Any]:
    """Normalize the policy payload in canonical PPO-index order."""
    pre_step = snapshot.get("pre_step") if isinstance(snapshot.get("pre_step"), dict) else {}
    action = snapshot.get("action") if isinstance(snapshot.get("action"), dict) else {}
    names = list(action_names)
    chosen_raw = action.get(
        "index",
        snapshot.get("action_index", pre_step.get("action", -1)),
    )
    try:
        chosen = int(chosen_raw)
    except (TypeError, ValueError):
        chosen = -1
    logits = _flatten(
        action.get("raw_logits", snapshot.get("raw_logits", pre_step.get("raw_logits", [])))
    )
    probs = _flatten(
        action.get("masked_probs", snapshot.get("masked_probs", pre_step.get("masked_probs", [])))
    )
    mask = _flatten(
        snapshot.get(
            "legal_mask",
            action.get("legal_mask", pre_step.get("action_mask", [])),
        )
    )
    rows = []
    row_count = max(len(names), len(logits), len(probs), len(mask))
    for i in range(row_count):
        logit = _number(logits[i]) if i < len(logits) else None
        prob = _number(probs[i]) if i < len(probs) else None
        legal = bool(mask[i]) if i < len(mask) else True
        rows.append({
            "index": i,
            "name": names[i] if i < len(names) else f"action_{i}",
            "raw_logit": logit,
            "probability": prob,
            "legal": legal,
            "masked": not legal,
            "chosen": i == chosen,
        })
    ranked = sorted(
        (row for row in rows if row["probability"] is not None),
        key=lambda row: row["probability"],
        reverse=True,
    )
    rank = next((i + 1 for i, row in enumerate(ranked) if row["index"] == chosen), None)
    entropy = -sum(
        float(row["probability"]) * math.log(float(row["probability"]))
        for row in rows
        if row["probability"] is not None and float(row["probability"]) > 0.0
    )
    return {
        "rows": rows,
        "chosen_index": chosen,
        "chosen_name": (
            action.get("name")
            or snapshot.get("action_name")
            or (names[chosen] if 0 <= chosen < len(names) else f"action_{chosen}")
        ),
        "value": action.get("value", snapshot.get("value", pre_step.get("value"))),
        "logprob": action.get("logprob", snapshot.get("logprob", pre_step.get("logprob"))),
        "entropy": entropy,
        "chosen_rank": rank,
        "policy_version": snapshot.get("policy_version", pre_step.get("policy_version")),
    }


def filtered_reward_breakdown(value: Any) -> dict[str, float]:
    """Keep nonzero human reward events, excluding step and softlock/contempt."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for name, raw in value.items():
        number = _number(raw)
        lower = str(name).lower()
        if number and lower not in {"step", "step_penalty", "softlock", "contempt"}:
            out[str(name)] = number
    return out
