"""Joypad + PPO tape capture for planner-loyal room segments.

Planner-loyal mints thin ``plNN`` cells (``cell.pst`` + sidecar + ``meta.json``).
When ``RE1_PLANNER_LEG_REPLAY=1``, the env also arms the joypad tape and the
PPO ``FootageTraceBuffer`` on reset, and this module dumps one fat tape per
capture unit into the minted cell dir:

- room segment → dump on the segment **exit** hop (continuous tape across hops)
- solo / gallery / armor PL → dump on that PL's hop

Mid-segment hops mint thin only (no tape write, tape keeps recording).

Payload reuses the yawn ``leg_replay.json`` schema (actions + frame channels +
packed joypad) plus planner fields (``planner_loyal``, ``room_segment_id``,
``from_pl_slot`` / ``to_pl_slot``) so ``scripts/replay_leg.py`` can play it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from re1_rl.go_explore_merge import CELL_POLICY_NAME, CELL_REPLAY_NAME
from re1_rl.leg_replay import (
    ACTION_MAP_VERSION,
    HEADING_RESTORE_VERSION,
    JOYPAD_PACKED_ENCODING,
    SCHEMA_VERSION,
    LegReplayBuffer,
    _joypad_payload_from_env,
    sparse_reward_events,
)

_PLANNER_LEG_REPLAY_ENV = "RE1_PLANNER_LEG_REPLAY"


def planner_leg_replay_enabled_from_env() -> bool:
    """``RE1_PLANNER_LEG_REPLAY=1`` arms joypad/PPO tape on planner-loyal (off default)."""
    raw = str(os.environ.get(_PLANNER_LEG_REPLAY_ENV, "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def should_write_planner_leg_replay(env: Any) -> bool:
    """True when a fat planner tape may be dumped (enabled + non-empty buffer)."""
    if not planner_leg_replay_enabled_from_env():
        return False
    buf = getattr(env, "_leg_replay", None)
    return isinstance(buf, LegReplayBuffer) and len(buf) > 0


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


def _file_sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def arm_planner_leg_replay(env: Any) -> bool:
    """Arm joypad tape + buffers on a planner-loyal reset. Returns armed or not."""
    if not planner_leg_replay_enabled_from_env():
        return False
    if getattr(env, "_planner_loyal_queue", None) is None:
        return False
    from re1_rl.leg_replay import new_leg_replay_buffer

    env._leg_replay = new_leg_replay_buffer()
    try:
        env.bridge.tape_clear()
        env.bridge.tape_enable(True)
    except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
        pass
    try:
        from re1_rl.footage_trace import new_footage_trace_buffer

        env._footage_trace = new_footage_trace_buffer()
    except (ImportError, AttributeError, TypeError):
        pass
    return True


def stop_planner_tape(env: Any) -> None:
    """Stop the joypad recorder before settle so post-success frames don't pollute."""
    try:
        env.bridge.tape_enable(False)
    except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
        pass


def build_planner_leg_replay_payload(
    env: Any,
    *,
    completed_index: int,
    checkpoint_id: str,
    slot: int,
    from_slot: int | None,
    live_state: dict[str, Any],
    quality: list[int] | tuple[int, ...],
    to_state_sha256: str,
    segment_id: str | None = None,
) -> dict[str, Any] | None:
    """Build a yawn-compatible tape payload for one planner capture unit."""
    if not should_write_planner_leg_replay(env):
        return None
    buf: LegReplayBuffer = env._leg_replay
    actions, policy, skip, reward_only = buf.as_channel_lists()
    emu_frames = [int(p) + int(s) for p, s in zip(policy, skip)]
    root = getattr(env, "project_root", None)
    from re1_rl.planner_loyal_cells import (
        cell_slot_dir,
        cell_state_filename,
        planner_loyal_root,
    )

    proj_root = Path(root) if root is not None else Path.cwd()
    pl_root = planner_loyal_root(proj_root)
    if from_slot is not None and int(from_slot) >= 0:
        from_dir = cell_slot_dir(pl_root, int(from_slot))
        from_sha = _file_sha256(from_dir / cell_state_filename())
        if not from_sha:
            try:
                meta = json.loads((from_dir / "meta.json").read_text(encoding="utf-8-sig"))
                from_sha = str(meta.get("state_sha256") or "")
            except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                from_sha = ""
        from_id = f"pl{int(from_slot):02d}"
    else:
        from_sha = ""
        from_id = "route_initial"
    kills: dict[str, int] = {}
    progress = getattr(env, "_progress", None)
    if progress is not None and hasattr(progress, "leg_kills_for_capture"):
        try:
            raw_kills = progress.leg_kills_for_capture() or {}
            kills = {str(k): int(v) for k, v in raw_kills.items()}
        except (TypeError, ValueError, AttributeError):
            kills = {}
    inv_slots = live_state.get("inventory_slots")
    if isinstance(inv_slots, list):
        inv: list[Any] = []
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
    n_actions = int(getattr(getattr(env, "action_space", None), "n", 45) or 45)
    joypad_payload = _joypad_payload_from_env(env)
    from re1_rl.leg_replay import ATTACK_ACTION_IDS

    payload = {
        "schema_version": SCHEMA_VERSION,
        "from_checkpoint_index": int(from_slot) if from_slot is not None else -1,
        "from_checkpoint_id": from_id,
        "from_state_sha256": from_sha,
        "to_checkpoint_index": int(slot),
        "to_checkpoint_id": str(checkpoint_id or ""),
        "to_state_sha256": str(to_state_sha256 or ""),
        "from_pl_slot": int(from_slot) if from_slot is not None else -1,
        "to_pl_slot": int(slot),
        "planner_loyal": True,
        "room_segment_id": segment_id,
        "contract": {
            "n_actions": n_actions,
            "frame_skip": int(getattr(env, "frame_skip", 8) or 8),
            "async_cutscene_skip": bool(getattr(env, "_async_cutscene_skip", False)),
            "code_commit": _git_commit(root),
            "action_map_version": ACTION_MAP_VERSION,
            "joypad_tape": bool(joypad_payload),
            "joypad_turbo": bool(
                joypad_payload.get("joypad_turbo")
                or joypad_payload.get("joypad_packed")
            ),
            "joypad_encoding": str(
                joypad_payload.get("joypad_encoding") or ""
            ),
            "heading_restore": HEADING_RESTORE_VERSION,
            "combat_leg": any(int(v or 0) > 0 for v in kills.values())
            or any(int(a) in ATTACK_ACTION_IDS for a in actions),
            "frame_channels": True,
            "planner_loyal": True,
        },
        "actions": actions,
        "emu_frames_per_step": emu_frames,
        "policy_frames_per_step": policy,
        "skip_frames_per_step": skip,
        "reward_only_frames_per_step": reward_only,
        "leg_steps": len(actions),
        "leg_frames": int(sum(emu_frames)),
        "policy_leg_frames": int(sum(policy)),
        "skip_leg_frames": int(sum(skip)),
        "reward_only_leg_frames": int(sum(reward_only)),
        "settled": True,
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
    payload.update(joypad_payload)
    if len(buf.rewards) > 0:
        rewards, events = buf.aligned_rewards()
        by_channel: dict[str, float] = {}
        compact: list[list[Any]] = []
        for i, step_events in enumerate(events):
            if step_events:
                compact.append([i, step_events])
                for key, value in step_events.items():
                    by_channel[key] = round(by_channel.get(key, 0.0) + value, 6)
        payload["rewards"] = rewards
        payload["reward_events"] = compact
        payload["reward_total"] = round(sum(rewards), 6)
        payload["reward_by_channel"] = by_channel
    # Keep linters calm about the packed-encoding import (asserted in contract).
    _ = (JOYPAD_PACKED_ENCODING, sparse_reward_events)
    return payload


def maybe_write_planner_capture_tape(
    env: Any,
    dest_dir: Path,
    *,
    completed_index: int,
    checkpoint_id: str,
    slot: int,
    from_slot: int | None,
    live_state: dict[str, Any],
    quality: list[int] | tuple[int, ...],
    to_state_sha256: str,
    segment_id: str | None = None,
) -> Path | None:
    """Write ``leg_replay.json`` into an already-minted ``plNN`` dir."""
    payload = build_planner_leg_replay_payload(
        env,
        completed_index=completed_index,
        checkpoint_id=checkpoint_id,
        slot=slot,
        from_slot=from_slot,
        live_state=live_state,
        quality=quality,
        to_state_sha256=to_state_sha256,
        segment_id=segment_id,
    )
    if payload is None:
        return None
    dest = Path(dest_dir) / CELL_REPLAY_NAME
    dest.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    return dest


def maybe_write_planner_footage_trace(env: Any, dest_dir: Path) -> Path | None:
    """Write ``leg_policy.npz`` into an already-minted ``plNN`` dir."""
    if not should_write_planner_leg_replay(env):
        return None
    buf = getattr(env, "_footage_trace", None)
    if buf is None or len(buf) == 0:
        return None
    return buf.write(Path(dest_dir) / CELL_POLICY_NAME)
