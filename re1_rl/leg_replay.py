"""Minimal per-leg input tapes for yawn cell replay (YouTube footage gate).

Stored as ``states/yawn_rails/cells/cpNN/leg_replay.json`` — sibling to
``cell.State``, not inside the sidecar (sidecar is parsed on every reset).
"""

from __future__ import annotations

import array
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from re1_rl.go_explore_merge import CELL_META_NAME, CELL_REPLAY_NAME, CELL_STATE_NAME

ACTION_MAP_VERSION = 1
SCHEMA_VERSION = 3
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
JOYPAD_TURBO_BIT = 1 << len(JOYPAD_BUTTON_ORDER)
JOYPAD_BUTTON_MASK = JOYPAD_TURBO_BIT - 1
JOYPAD_PACKED_ENCODING = "b64x3_buttons14_turbo14"
_JOYPAD_PACKED_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_JOYPAD_PACKED_DECODE = {
    char: index for index, char in enumerate(_JOYPAD_PACKED_ALPHABET)
}


def _json_float(value: float) -> float:
    return round(float(value), 6)


def _clamp_u16(value: int) -> int:
    return max(0, min(_UINT16_MAX, int(value)))


def sparse_reward_events(breakdown: dict[str, Any] | None) -> dict[str, float]:
    """Keep nonzero channels so a later audit can replay the payout mix."""
    if not isinstance(breakdown, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw in breakdown.items():
        try:
            number = _json_float(raw)
        except (TypeError, ValueError):
            continue
        if number != 0.0:
            out[str(key)] = number
    return out


class LegReplayBuffer:
    """In-memory action + per-channel frame + stepwise-reward tape.

    Frame channels:
    - ``policy_frames``: agent-controlled hold / macro time (cell speed)
    - ``skip_frames``: automatic async cutscene/door turbo burn
    - ``reward_only_frames``: synthetic living-cost min bills (not emu time)
    """

    __slots__ = (
        "actions",
        "policy_frames",
        "skip_frames",
        "reward_only_frames",
        "rewards",
        "reward_events",
    )

    def __init__(self) -> None:
        self.actions = bytearray()
        self.policy_frames = array.array("H")
        self.skip_frames = array.array("H")
        self.reward_only_frames = array.array("H")
        self.rewards = array.array("d")
        self.reward_events: list[dict[str, float]] = []

    def append(
        self,
        action: int,
        emu_frames: int | None = None,
        *,
        policy_frames: int | None = None,
        skip_frames: int = 0,
        reward_only_frames: int = 0,
    ) -> None:
        """Record one policy decision.

        Backward-compatible call ``append(action, emu_frames)`` treats the
        second positional arg as policy-controlled frames.
        """
        a = max(0, min(255, int(action)))
        if policy_frames is None:
            policy = _clamp_u16(0 if emu_frames is None else emu_frames)
        else:
            policy = _clamp_u16(policy_frames)
        skip = _clamp_u16(skip_frames)
        reward_only = _clamp_u16(reward_only_frames)
        self.actions.append(a)
        self.policy_frames.append(policy)
        self.skip_frames.append(skip)
        self.reward_only_frames.append(reward_only)

    def append_reward(
        self, reward: float, breakdown: dict[str, Any] | None = None
    ) -> None:
        """Attach the just-computed payout to the latest unmatched action."""
        if len(self.rewards) >= len(self.actions):
            return
        while len(self.rewards) < len(self.actions) - 1:
            self.rewards.append(0.0)
            self.reward_events.append({})
        self.rewards.append(float(reward))
        self.reward_events.append(sparse_reward_events(breakdown))

    def __len__(self) -> int:
        return len(self.actions)

    @property
    def policy_leg_frames(self) -> int:
        """Agent-controlled frames only — used for cell speed quality."""
        return int(sum(self.policy_frames))

    @property
    def skip_leg_frames(self) -> int:
        return int(sum(self.skip_frames))

    @property
    def reward_only_leg_frames(self) -> int:
        return int(sum(self.reward_only_frames))

    @property
    def leg_frames(self) -> int:
        """Real emulated frames (policy + automatic skip)."""
        return self.policy_leg_frames + self.skip_leg_frames

    @property
    def emu_frames(self) -> array.array:
        """Compat view: policy + skip per step (excludes reward-only)."""
        out = array.array("H")
        for policy, skip in zip(self.policy_frames, self.skip_frames):
            out.append(_clamp_u16(int(policy) + int(skip)))
        return out

    def as_lists(self) -> tuple[list[int], list[int]]:
        return list(self.actions), [int(p) + int(s) for p, s in zip(self.policy_frames, self.skip_frames)]

    def as_channel_lists(
        self,
    ) -> tuple[list[int], list[int], list[int], list[int]]:
        return (
            list(self.actions),
            list(self.policy_frames),
            list(self.skip_frames),
            list(self.reward_only_frames),
        )

    def aligned_rewards(self) -> tuple[list[float], list[dict[str, float]]]:
        n = len(self.actions)
        rewards = [_json_float(x) for x in self.rewards]
        events = [dict(item) for item in self.reward_events]
        while len(rewards) < n:
            rewards.append(0.0)
            events.append({})
        return rewards[:n], events[:n]


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


ATTACK_ACTION_IDS = frozenset({6, 7, 8})
HEADING_RESTORE_VERSION = 1


_SKIP_MASH_MIN_LEN = 24
_SKIP_MASH_RATIO_MIN = 0.22
_SKIP_MASH_RATIO_MAX = 0.45
_SKIP_MASH_CROSS_BITS = 16  # TAPE_BUTTON_ORDER index 4 = cross


def _merge_joypad_spans(
    spans: list[tuple[list[int], str]],
) -> list[tuple[list[int], str]]:
    merged: list[tuple[list[int], str]] = []
    for bits_span, mode in spans:
        if not bits_span:
            continue
        if merged and merged[-1][1] == mode:
            merged[-1][0].extend(bits_span)
        else:
            merged.append((list(bits_span), mode))
    return merged


def _is_skip_mash_run(frames: list[int]) -> bool:
    """True for async fast_forward's 4-on/8-off Cross mash (ratio ~1/3)."""
    n = len(frames)
    if n < _SKIP_MASH_MIN_LEN:
        return False
    cross = 0
    for raw in frames:
        bits = int(raw)
        if bits == _SKIP_MASH_CROSS_BITS:
            cross += 1
        elif bits != 0:
            return False
    ratio = cross / n
    return _SKIP_MASH_RATIO_MIN <= ratio <= _SKIP_MASH_RATIO_MAX


def decode_packed_joypad(
    packed: str, *, expected_frames: int | None = None
) -> tuple[list[int], list[int]]:
    """Decode fixed-width frames into legacy-compatible buttons/turbo lists."""
    if not isinstance(packed, str) or len(packed) % 3:
        return [], []
    n_frames = len(packed) // 3
    if expected_frames is not None and n_frames != int(expected_frames):
        return [], []
    bits: list[int] = []
    turbo: list[int] = []
    for i in range(0, len(packed), 3):
        try:
            word = (
                _JOYPAD_PACKED_DECODE[packed[i]] * 4096
                + _JOYPAD_PACKED_DECODE[packed[i + 1]] * 64
                + _JOYPAD_PACKED_DECODE[packed[i + 2]]
            )
        except KeyError:
            return [], []
        bits.append(word & JOYPAD_BUTTON_MASK)
        turbo.append(1 if word & JOYPAD_TURBO_BIT else 0)
    return bits, turbo


def _joypad_lists_from_tape(tape: dict[str, Any]) -> tuple[list[int], list[int]]:
    packed = tape.get("joypad_packed")
    encoding = str(tape.get("joypad_encoding") or "")
    if isinstance(packed, str) and encoding == JOYPAD_PACKED_ENCODING:
        expected = tape.get("joypad_frames")
        try:
            expected_frames = None if expected is None else int(expected)
        except (TypeError, ValueError):
            return [], []
        return decode_packed_joypad(packed, expected_frames=expected_frames)
    bits = [int(b) for b in (tape.get("joypad_bits") or [])]
    turbo_raw = tape.get("joypad_turbo")
    turbo = (
        [1 if int(flag or 0) else 0 for flag in turbo_raw]
        if isinstance(turbo_raw, list) and len(turbo_raw) == len(bits)
        else []
    )
    return bits, turbo


def tape_has_joypad(tape: dict[str, Any]) -> bool:
    """True for either preserved legacy arrays or the compact packed stream."""
    if tape.get("joypad_bits"):
        return True
    return bool(
        tape.get("joypad_packed")
        and str(tape.get("joypad_encoding") or "") == JOYPAD_PACKED_ENCODING
        and int(tape.get("joypad_frames") or 0) > 0
    )


def tape_has_joypad_turbo(tape: dict[str, Any]) -> bool:
    """True when replay has an explicit turbo channel, including all-zero data."""
    if (
        tape.get("joypad_packed")
        and str(tape.get("joypad_encoding") or "") == JOYPAD_PACKED_ENCODING
    ):
        return True
    bits = tape.get("joypad_bits")
    turbo = tape.get("joypad_turbo")
    return isinstance(bits, list) and isinstance(turbo, list) and len(turbo) == len(bits)


def joypad_replay_spans(tape: dict[str, Any]) -> list[tuple[list[int], str]]:
    """Split joypad bits into ``(frames, patch_mode)`` TAS spans.

    New tapes stamp ``joypad_turbo`` (1 = cutscene turbo poke was on). Replay
    those bits with ``force`` / ``off`` so grab skip and cinema match capture.
    Old tapes without the channel fall back to Cross-mash detection.
    """
    bits, turbo = _joypad_lists_from_tape(tape)
    if not bits:
        return []
    if len(turbo) == len(bits):
        spans: list[tuple[list[int], str]] = []
        i = 0
        n = len(bits)
        while i < n:
            on = bool(turbo[i])
            j = i + 1
            while j < n and bool(turbo[j]) == on:
                j += 1
            spans.append((bits[i:j], "force" if on else "off"))
            i = j
        return _merge_joypad_spans(spans)
    spans = []
    i = 0
    n = len(bits)
    while i < n:
        if bits[i] in (0, _SKIP_MASH_CROSS_BITS):
            j = i + 1
            while j < n and bits[j] in (0, _SKIP_MASH_CROSS_BITS):
                j += 1
            run = bits[i:j]
            mode = "force" if _is_skip_mash_run(run) else "step"
            spans.append((run, mode))
            i = j
        else:
            j = i + 1
            while j < n and bits[j] not in (0, _SKIP_MASH_CROSS_BITS):
                j += 1
            spans.append((bits[i:j], "step"))
            i = j
    return _merge_joypad_spans(spans)


def tape_is_combat(tape: dict[str, Any]) -> bool:
    """True when the tape recorded attacks or room kills."""
    if bool(tape.get("combat_leg")):
        return True
    contract = tape.get("contract") or {}
    if bool(contract.get("combat_leg")):
        return True
    kills = (tape.get("end") or {}).get("leg_kills_by_room") or {}
    try:
        if any(int(v or 0) > 0 for v in kills.values()):
            return True
    except (TypeError, ValueError):
        pass
    try:
        return any(int(a) in ATTACK_ACTION_IDS for a in tape.get("actions") or [])
    except (TypeError, ValueError):
        return False


def _file_sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def successor_cell_state_path(
    project_root: Path | str, tape: dict[str, Any]
) -> Path | None:
    """``cell.State`` for the tape's destination checkpoint, if present."""
    try:
        to_index = int(tape.get("to_checkpoint_index"))
    except (TypeError, ValueError):
        return None
    from re1_rl.yawn_rails_sync import cell_slot_dir, yawn_rails_root

    path = cell_slot_dir(yawn_rails_root(project_root), to_index) / CELL_STATE_NAME
    return path if path.is_file() else None


def successor_state_sha_ok(path: Path | None, tape: dict[str, Any]) -> bool:
    """True when disk State is the exact file the tape hashed at capture."""
    want = str(tape.get("to_state_sha256") or "")
    if not want or path is None or not path.is_file():
        return False
    return _file_sha256(path) == want


def init_savestate_path(env: Any) -> Path | None:
    """Dining-room fresh start State (predecessor of cp00)."""
    stage = getattr(env, "_stage", None)
    raw = ""
    if isinstance(stage, dict):
        raw = str(stage.get("init_savestate") or "")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        root = Path(getattr(env, "project_root", ".") or ".")
        path = root / path
    return path


def predecessor_state_path(env: Any, from_index: int) -> Path | None:
    if int(from_index) < 0:
        return init_savestate_path(env)
    meta = predecessor_meta_path(env, from_index)
    if meta is None:
        return None
    return meta.parent / CELL_STATE_NAME


def predecessor_meta_path(env: Any, from_index: int) -> Path | None:
    if int(from_index) < 0:
        return None
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
    """Single-leg only: fresh→cp00 or episode loaded the adjacent predecessor."""
    try:
        start = int(getattr(env, "_route_start_index", -1))
        completed = int(completed_index)
    except (TypeError, ValueError):
        return False
    if completed < 0:
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
    actions, policy, skip, reward_only = buf.as_channel_lists()
    emu_frames = [int(p) + int(s) for p, s in zip(policy, skip)]
    from_index = int(completed_index) - 1
    if from_index < 0:
        from_id = "route_initial"
        from_sha = _file_sha256(init_savestate_path(env))
    else:
        meta_p = predecessor_meta_path(env, from_index)
        from_id = _meta_field(meta_p, "checkpoint_id") if meta_p else ""
        from_sha = _file_sha256(predecessor_state_path(env, from_index))
        if not from_sha:
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
    joypad_payload = _joypad_payload_from_env(env)
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
            "joypad_tape": bool(joypad_payload),
            "joypad_turbo": bool(
                joypad_payload.get("joypad_turbo")
                or joypad_payload.get("joypad_packed")
            ),
            "heading_restore": HEADING_RESTORE_VERSION,
            "combat_leg": any(int(v or 0) > 0 for v in kills.values())
            or any(int(a) in ATTACK_ACTION_IDS for a in actions),
            "frame_channels": True,
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
    payload.update(joypad_payload)
    if len(buf.rewards) > 0:
        rewards, events = buf.aligned_rewards()
        by_channel: dict[str, float] = {}
        compact: list[list[Any]] = []
        for i, step_events in enumerate(events):
            if step_events:
                compact.append([i, step_events])
                for key, value in step_events.items():
                    by_channel[key] = _json_float(by_channel.get(key, 0.0) + value)
        payload["rewards"] = rewards
        payload["reward_events"] = compact
        payload["reward_total"] = _json_float(sum(rewards))
        payload["reward_by_channel"] = by_channel
    return payload


def _joypad_tape_from_env(env: Any) -> tuple[list[int], list[int]]:
    bridge = getattr(env, "bridge", None)
    dump_full = getattr(bridge, "tape_dump_full", None)
    dump = getattr(bridge, "tape_dump", None)
    frames: list[int] = []
    turbo: list[int] = []
    try:
        if callable(dump_full):
            raw_frames, raw_turbo = dump_full()
            frames = list(raw_frames or [])
            turbo = list(raw_turbo or [])
        elif callable(dump):
            raw = dump()
            if isinstance(raw, list):
                frames = list(raw)
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return [], []
    out: list[int] = []
    for item in frames:
        try:
            out.append(max(0, int(item)) & 0xFFFF)
        except (TypeError, ValueError):
            return [], []
    if not out:
        return [], []
    flags: list[int] = []
    if len(turbo) == len(out):
        for item in turbo:
            try:
                flags.append(1 if int(item) else 0)
            except (TypeError, ValueError):
                flags.append(0)
    return out, flags


def _joypad_payload_from_env(env: Any) -> dict[str, Any]:
    """Prefer the bridge's compact stream; retain old-bridge array compatibility."""
    bridge = getattr(env, "bridge", None)
    dump_packed = getattr(bridge, "tape_dump_packed", None)
    if callable(dump_packed):
        try:
            packed = dump_packed()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
            packed = None
        if isinstance(packed, dict):
            encoded = str(packed.get("packed") or "")
            encoding = str(packed.get("encoding") or "")
            try:
                n_frames = int(packed.get("n") or 0)
            except (TypeError, ValueError):
                n_frames = 0
            if encoded and encoding == JOYPAD_PACKED_ENCODING and n_frames > 0:
                return {
                    "joypad_encoding": encoding,
                    "joypad_packed": encoded,
                    "joypad_frames": n_frames,
                }
    bits, turbo = _joypad_tape_from_env(env)
    if not bits:
        return {}
    out: dict[str, Any] = {
        "joypad_bits": bits,
        "joypad_frames": len(bits),
    }
    if turbo:
        out["joypad_turbo"] = turbo
    return out


def _joypad_bits_from_env(env: Any) -> list[int] | None:
    bits, _turbo = _joypad_tape_from_env(env)
    return bits or None


def write_leg_replay_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def policy_leg_frames_from_tape(tape: dict[str, Any] | None) -> int | None:
    """Prefer explicit policy channel; fall back to total emu for old tapes."""
    if not isinstance(tape, dict):
        return None
    if "policy_leg_frames" in tape:
        try:
            return max(0, int(tape["policy_leg_frames"]))
        except (TypeError, ValueError):
            return None
    policy_steps = tape.get("policy_frames_per_step")
    if isinstance(policy_steps, list) and policy_steps:
        try:
            return max(0, int(sum(int(x) for x in policy_steps)))
        except (TypeError, ValueError):
            return None
    if "leg_frames" in tape:
        try:
            return max(0, int(tape["leg_frames"]))
        except (TypeError, ValueError):
            return None
    return None


def reclassify_contaminated_async_skip_tape(
    tape: dict[str, Any],
    *,
    frame_skip: int = 8,
    skip_chunk: int = 600,
) -> dict[str, Any]:
    """Split legacy async-skip contamination out of ``emu_frames_per_step``.

    Pre-channel tapes billed whole bg skip chunks (often 600/1200) onto a
    single noop/interact policy row, and charged synthetic ``frame_skip``
    mins while waiting for the next chunk. Those inflate quality speed.
    """
    out = dict(tape)
    actions = [int(a) for a in (out.get("actions") or [])]
    emu = [int(f) for f in (out.get("emu_frames_per_step") or [])]
    if len(emu) != len(actions):
        raise ValueError("actions / emu_frames_per_step length mismatch")
    if isinstance(out.get("policy_frames_per_step"), list) and out.get(
        "contract", {}
    ).get("frame_channels"):
        return out

    policy: list[int] = []
    skip: list[int] = []
    reward_only: list[int] = []
    fs = max(1, int(frame_skip))
    chunk = max(fs, int(skip_chunk))
    i = 0
    n = len(actions)
    while i < n:
        action = int(actions[i])
        f = max(0, int(emu[i]))
        # Full skip-chunk multiples on a cutscene decision are automatic burn.
        if f >= chunk and f % chunk == 0:
            policy.append(0)
            skip.append(f)
            reward_only.append(0)
            i += 1
            # Trailing noops after a chunk are still skip-session accounting:
            # frame_skip mins are synthetic living-cost; any other size is the
            # real leftover burn from the bg worker.
            while i < n and int(actions[i]) == 0:
                trail = max(0, int(emu[i]))
                if trail == fs:
                    policy.append(0)
                    skip.append(0)
                    reward_only.append(fs)
                else:
                    policy.append(0)
                    skip.append(trail)
                    reward_only.append(0)
                i += 1
            continue
        policy.append(f)
        skip.append(0)
        reward_only.append(0)
        i += 1

    out["schema_version"] = SCHEMA_VERSION
    out["policy_frames_per_step"] = policy
    out["skip_frames_per_step"] = skip
    out["reward_only_frames_per_step"] = reward_only
    out["emu_frames_per_step"] = [int(p) + int(s) for p, s in zip(policy, skip)]
    out["policy_leg_frames"] = int(sum(policy))
    out["skip_leg_frames"] = int(sum(skip))
    out["reward_only_leg_frames"] = int(sum(reward_only))
    out["leg_frames"] = int(sum(out["emu_frames_per_step"]))
    contract = dict(out.get("contract") or {})
    contract["frame_channels"] = True
    out["contract"] = contract
    end = dict(out.get("end") or {})
    quality = list(end.get("quality") or [])
    if len(quality) >= 8:
        from re1_rl.go_explore_archive import attach_leg_frames

        end["quality"] = list(attach_leg_frames(quality[:7], out["policy_leg_frames"]))
        out["end"] = end
    return out


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
    """Write ``leg_replay.json`` into staging. Returns ``policy_leg_frames`` or None."""
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
    return int(payload["policy_leg_frames"])
