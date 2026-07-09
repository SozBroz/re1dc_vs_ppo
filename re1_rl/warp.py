"""RAM warp to a known room code (privileged dev / human play only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from re1_rl.memory_map import (
    CAM_ID,
    GAME_MODE,
    GAME_STATE,
    IN_CONTROL_MASK,
    MESSAGE_FLAG,
    PLAYER_FACING,
    PLAYER_HP,
    PLAYER_X,
    PLAYER_Y,
    PLAYER_Z,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
)

# Typical in-mansion control dword (matches dining-room live play).
_IN_CONTROL_GAME_STATE = 0x80800004

_DEFAULT_SPAWNS: dict[str, dict[str, int]] = {
    # Mansion save room — 101->100 door entry (doors_rdt.json).
    "100": {"x": 3500, "z": 2600, "facing": 3072, "cam_id": 0, "y": 0},
    # Dining room — 106->105 (doors_empirical.json).
    "105": {"x": 30700, "z": 7200, "facing": 1024, "cam_id": 0, "y": 0},
    # Mansion store room — 11A->11B (doors_rdt.json).
    "11B": {"x": 3500, "z": 6600, "facing": 0, "cam_id": 0, "y": 0},
}


def _load_empirical_spawns(data_dir: Path) -> dict[str, dict[str, int]]:
    path = data_dir / "doors_empirical.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, int]] = {}
    for _key, edge in raw.items():
        if not isinstance(edge, dict) or "to_room" not in edge:
            continue
        dest = str(edge["to_room"]).upper()
        out[dest] = {
            "x": int(edge.get("entry_x", 4000)),
            "z": int(edge.get("entry_z", 4000)),
            "facing": int(edge.get("entry_facing", 0)),
            "cam_id": int(edge.get("entry_cam_id", 0)),
            "y": 0,
        }
    return out


def parse_room_code(room_code: str) -> tuple[int, int]:
    """'100' -> (stage_id=0, room_byte=0x00)."""
    code = str(room_code).strip().upper()
    if len(code) < 2:
        raise ValueError(f"invalid room code: {room_code!r}")
    return int(code[0]) - 1, int(code[1:], 16)


def _load_rdt_spawns(data_dir: Path) -> dict[str, dict[str, int]]:
    path = data_dir / "doors_rdt.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, int]] = {}
    for _key, edge in raw.items():
        if not isinstance(edge, dict) or "to_room" not in edge:
            continue
        dest = str(edge["to_room"])
        out[dest] = {
            "x": int(edge.get("entry_x", 4000)),
            "z": int(edge.get("entry_z", 4000)),
            "facing": int(edge.get("entry_facing", 0)),
            "cam_id": int(edge.get("entry_cam_id", 0)),
            "y": 0,
        }
    return out


def spawn_pose(room_code: str, *, data_dir: Path | None = None) -> dict[str, int]:
    code = str(room_code).strip().upper()
    if data_dir is not None:
        empirical = _load_empirical_spawns(data_dir)
        if code in empirical:
            return dict(empirical[code])
        rdt = _load_rdt_spawns(data_dir)
        if code in rdt:
            return dict(rdt[code])
    if code in _DEFAULT_SPAWNS:
        return dict(_DEFAULT_SPAWNS[code])
    raise KeyError(f"no spawn pose for room {code!r}")


def warp_sequence(
    bridge: Any,
    room_codes: list[str],
    *,
    data_dir: Path | None = None,
    hp: int | None = None,
    settle_frames: int = 45,
) -> dict[str, int]:
    """Warp through a list of rooms; returns RAM snapshot after the last hop."""
    snap: dict[str, int] = {}
    for code in room_codes:
        snap = warp_to_room(
            bridge,
            code,
            data_dir=data_dir,
            hp=hp,
            settle_frames=settle_frames,
        )
    return snap


def warp_to_room(
    bridge: Any,
    room_code: str,
    *,
    data_dir: Path | None = None,
    hp: int | None = None,
    settle_frames: int = 45,
) -> dict[str, int]:
    """Teleport Jill into ``room_code`` and force player control."""
    stage_id, room_byte = parse_room_code(room_code)
    pose = spawn_pose(room_code, data_dir=data_dir)

    fields: list[tuple[str, int, str, int]] = [
        ("stage_id", STAGE_ID, "u8", stage_id),
        ("room_id", ROOM_ID, "u8", room_byte),
        ("cam_id", CAM_ID, "u8", int(pose.get("cam_id", 0))),
        ("player_x", PLAYER_X, "s16", int(pose["x"])),
        ("player_y", PLAYER_Y, "s16", int(pose.get("y", 0))),
        ("player_z", PLAYER_Z, "s16", int(pose["z"])),
        ("player_facing", PLAYER_FACING, "u16", int(pose["facing"])),
        ("game_mode", GAME_MODE, "u8", IN_CONTROL_MASK),
        ("game_state", GAME_STATE, "u32", _IN_CONTROL_GAME_STATE),
        ("scene_flag", SCENE_FLAG, "u8", 0),
        ("msg_flag", MESSAGE_FLAG, "u8", 0),
    ]
    if hp is not None:
        fields.append(("player_hp", PLAYER_HP, "u16", int(hp)))

    bridge.write_ram(fields)
    bridge.frameadvance(max(1, int(settle_frames)))

    ram = bridge.read_ram(
        [
            ("stage_id", STAGE_ID, "u8"),
            ("room_id", ROOM_ID, "u8"),
            ("player_x", PLAYER_X, "s16"),
            ("player_z", PLAYER_Z, "s16"),
            ("player_hp", PLAYER_HP, "u16"),
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
        ]
    )
    return {k: int(ram[k]) for k in ram}
