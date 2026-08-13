"""Minimal per-leg input tapes for yawn cell replay (YouTube footage gate).

Stored as ``states/yawn_rails/cells/cpNN/leg_replay.json`` — sibling to
``cell.State``, not inside the sidecar (sidecar is parsed on every reset).
"""

from __future__ import annotations

import array
import json
import subprocess
from pathlib import Path
from typing import Any

from re1_rl.go_explore_merge import CELL_META_NAME, CELL_REPLAY_NAME

ACTION_MAP_VERSION = 1
SCHEMA_VERSION = 1
_UINT16_MAX = 65535
# Packed LSB-first; must match lua/re1_client.lua TAPE_BUTTON_ORDER.
JOYPAD_BUTTON_ORDER = (
    "up",
    "down",
    "left",
    "right",
    "cross",
    "triangle",
    "square",
    "circle",
    "start",
    "select",
    "r1",
    "l1",
    "r2",
    "l2",
)


class LegReplayBuffer:
    """In-memory action + emu-frame tape. ~3 bytes/step."""

    __slots__ = ("actions", "emu_frames")

    def __init__(self) -> None:
        self.actions = bytearray()
        self.emu_frames = array.array("H")

    def append(self, action: int, emu_frames: int) -> None:
        a = max(0, min(255, int(action)))
        frames = max(0, min(_UINT16_MAX, int(emu_frames)))
        self.actions.append(a)
        self.emu_frames.append(frames)

    def __len__(self) -> int:
        return len(self.actions)

    @property
    def leg_frames(self) -> int:
        return int(sum(self.emu_frames))

    def as_lists(self) -> tuple[list[int], list[int]]:
        return list(self.actions), list(self.emu_frames)


def new_leg_replay_buffer() -> LegReplayBuffer:
    return LegReplayBuffer()


def _git_commit(project_root: Path | str | None) -> str:
    cwd = Path(project_root) if project_root is not None else Path.cwd()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip()


def _meta_field(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    try:
        meta = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(meta, dict):
        return ""
    val = meta.get(key)
    return str(val) if val is not None else ""


def predecessor_meta_path(env: Any, from_index: int) -> Path | None:
    pb = None
    reset_opts = getattr(env, "_reset_options", None)
    if isinstance(reset_opts, dict):
        pb = (reset_opts.get("pb_bundle") or {}) if reset_opts else None
    if not isinstance(pb, dict):
        pb = None
    state_path = None
    if pb:
        raw = pb.get("state_path")
        if raw:
            state_path = Path(str(raw))
            if not state_path.is_absolute():
                root = Path(getattr(env, "project_root", ".") or ".")
                state_path = root / state_path
    if state_path is not None:
        return state_path.parent / CELL_META_NAME
    root = Path(getattr(env, "project_root", ".") or ".")
    from re1_rl.yawn_rails_sync import cell_slot_dir, yawn_rails_root

    return cell_slot_dir(yawn_rails_root(root), from_index) / CELL_META_NAME


def should_write_leg_replay(env: Any, completed_index: int) -> bool:
    """Single-leg only: episode loaded the adjacent predecessor."""
    try:
        start = int(getattr(env, "_route_start_index", -1))
        completed = int(completed_index)
    except (TypeError, ValueError):
        return False
    if completed < 1:
        return False
    if start != completed:
        return False
    buf = getattr(env, "_leg_replay", None)
    return isinstance(buf, LegReplayBuffer) and len(buf) > 0


def build_leg_replay_payload(
    env: Any,
    *,
    completed_index: int,
    completed_id: str,
    settled: bool,
    live_state: dict[str, Any],
    quality: list[int] | tuple[int, ...],
    to_state_sha256: str,
) -> dict[str, Any] | None:
    if not should_write_leg_replay(env, completed_index):
        return None
    buf: LegReplayBuffer = env._leg_replay
    actions, emu_frames = buf.as_lists()
    from_index = int(completed_index) - 1
    meta_p = predecessor_meta_path(env, from_index)
    from_id = _meta_field(meta_p, "checkpoint_id") if meta_p else ""
    from_sha = _meta_field(meta_p, "state_sha256") if meta_p else ""
    progress = getattr(env, "_progress", None)
    kills: dict[str, int] = {}
    if progress is not None and hasattr(progress, "leg_kills_for_capture"):
        try:
            raw_kills = progress.leg_kills_for_capture() or {}
            kills = {str(k): int(v) for k, v in raw_kills.items()}
        except (TypeError, ValueError, AttributeError):
            kills = {}
    inv_slots = live_state.get("inventory_slots")
    inv: list[Any]
    if isinstance(inv_slots, list):
        inv = []
        for row in inv_slots:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                inv.append([str(row[0]), int(row[1])])
            elif row:
                inv.append([str(row), 1])
    else:
        inv = [
            [str(n), 1]
            for n in (live_state.get("inventory") or [])
            if str(n).strip()
        ]
    root = getattr(env, "project_root", None)
    n_actions = int(getattr(getattr(env, "action_space", None), "n", 45) or 45)
    joypad_bits = _joypad_bits_from_env(env)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "from_checkpoint_index": from_index,
        "from_checkpoint_id": from_id,
        "from_state_sha256": from_sha,
        "to_checkpoint_index": int(completed_index),
        "to_checkpoint_id": str(completed_id or ""),
        "to_state_sha256": str(to_state_sha256 or ""),
        "contract": {
            "n_actions": n_actions,
            "frame_skip": int(getattr(env, "frame_skip", 8) or 8),
            "async_cutscene_skip": bool(getattr(env, "_async_cutscene_skip", False)),
            "code_commit": _git_commit(root),
            "action_map_version": ACTION_MAP_VERSION,
            "joypad_tape": bool(joypad_bits),
        },
        "actions": actions,
        "emu_frames_per_step": emu_frames,
        "leg_steps": len(actions),
        "leg_frames": int(sum(emu_frames)),
        "settled": bool(settled),
        "end": {
            "room_id": str(live_state.get("room_id", "") or ""),
            "x": int(live_state.get("x", 0) or 0),
            "z": int(live_state.get("z", 0) or 0),
            "facing": int(live_state.get("facing", 0) or 0),
            "hp": int(live_state.get("hp", 0) or 0),
            "in_control": bool(live_state.get("in_control", True)),
            "quality": [int(x) for x in quality],
            "inventory_slots": inv,
            "leg_kills_by_room": kills,
        },
    }
    if joypad_bits:
        payload["joypad_bits"] = joypad_bits
        payload["joypad_frames"] = len(joypad_bits)
    return payload


def _joypad_bits_from_env(env: Any) -> list[int] | None:
    bridge = getattr(env, "bridge", None)
    dump = getattr(bridge, "tape_dump", None)
    if not callable(dump):
        return None
    try:
        frames = dump()
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return None
    if not isinstance(frames, list) or not frames:
        return None
    out: list[int] = []
    for item in frames:
        try:
            out.append(max(0, int(item)) & 0xFFFF)
        except (TypeError, ValueError):
            return None
    return out


def write_leg_replay_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def maybe_write_capture_tape(
    env: Any,
    staging: Path,
    *,
    completed_index: int,
    completed_id: str,
    settled: bool,
    live_state: dict[str, Any],
    quality: list[int] | tuple[int, ...],
    to_state_sha256: str,
) -> int | None:
    """Write ``leg_replay.json`` into staging. Returns ``leg_frames`` or None."""
    payload = build_leg_replay_payload(
        env,
        completed_index=completed_index,
        completed_id=completed_id,
        settled=settled,
        live_state=live_state,
        quality=quality,
        to_state_sha256=to_state_sha256,
    )
    if payload is None:
        return None
    write_leg_replay_json(staging / CELL_REPLAY_NAME, payload)
    return int(payload["leg_frames"])
