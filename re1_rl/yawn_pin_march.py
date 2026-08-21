"""Pking speed-march: pin one cell, then advance when the hunt is 4x faster.

Loads ``cpN`` and hunts ``cp{N+1}``. After ≥15 min wall-clock on that pin
*and* a captured hunt ≤ 1/4 of the human 1x time, write the next pin.
After max dwell (default 2h) advance anyway if the hunted cell exists, even
when it is slower than 4x. Stops at pin 18 (L Passage enter) so pking keeps
fighting the hallway dogs for ``ammo_108`` (cp19) and never marches past
that fight.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from re1_rl.go_explore_archive import LEG_FRAMES_QUALITY_INDEX, LEG_FRAMES_SENTINEL
from re1_rl.go_explore_merge import CELL_META_NAME, CELL_REPLAY_NAME, CELL_STATE_NAME
from re1_rl.leg_replay import policy_leg_frames_from_tape
from re1_rl.yawn_cell_timeout import load_timeout_table, parse_mmss
from re1_rl.yawn_rails_sync import cell_slot_dir, yawn_rails_root

_MARCH_ENV = "RE1_YAWN_PIN_MARCH"
_MARCH_START_ENV = "RE1_YAWN_PIN_MARCH_START"
_MARCH_STOP_ENV = "RE1_YAWN_PIN_MARCH_STOP"
_MARCH_RATIO_ENV = "RE1_YAWN_PIN_MARCH_RATIO"
_MARCH_DWELL_ENV = "RE1_YAWN_PIN_MARCH_MIN_DWELL_S"
_MARCH_MAX_DWELL_ENV = "RE1_YAWN_PIN_MARCH_MAX_DWELL_S"
_STATE_ENV = "RE1_YAWN_PIN_MARCH_STATE"

DEFAULT_START_PIN = 4
DEFAULT_STOP_PIN = 18  # load cp18, hunt cp19 ammo_108 (both hallway dogs)
DEFAULT_SPEED_RATIO = 0.25  # 4x faster than human 1x
DEFAULT_MIN_DWELL_S = 15 * 60
DEFAULT_MAX_DWELL_S = 2 * 60 * 60
DEFAULT_FPS = 60
_DEFAULT_STATE_REL = Path("data/yawn_pin_march_state.json")
_PIN_INDEX_KEY = "RE1_YAWN_RESET_PIN_INDEX"


@dataclass(frozen=True)
class MarchTick:
    pin_index: int
    hunted_index: int
    human_s: float | None
    agent_s: float | None
    speed_ratio: float
    dwell_s: float
    min_dwell_s: float
    max_dwell_s: float
    speed_ok: bool
    dwell_ok: bool
    at_stop: bool
    advanced: bool
    reason: str


def pin_march_enabled(project_root: Path | str | None = None) -> bool:
    """``RE1_YAWN_PIN_MARCH=1`` in the pin file (or launcher env)."""
    from re1_rl.yawn_rails import _pin_env_raw

    raw = _pin_env_raw(_MARCH_ENV, project_root)
    if not raw:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float_pin_env(
    key: str,
    default: float,
    project_root: Path | str | None,
    *,
    lo: float,
    hi: float,
) -> float:
    from re1_rl.yawn_rails import _pin_env_raw

    raw = _pin_env_raw(key, project_root)
    if not raw:
        return default
    try:
        return max(lo, min(hi, float(raw)))
    except ValueError:
        return default


def _int_pin_env(
    key: str,
    default: int,
    project_root: Path | str | None,
    *,
    lo: int,
    hi: int,
) -> int:
    from re1_rl.yawn_rails import _pin_env_raw

    raw = _pin_env_raw(key, project_root)
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw, 10)))
    except ValueError:
        return default


def march_start_pin(project_root: Path | str | None = None) -> int:
    return _int_pin_env(
        _MARCH_START_ENV, DEFAULT_START_PIN, project_root, lo=0, hi=101
    )


def march_stop_pin(project_root: Path | str | None = None) -> int:
    start = march_start_pin(project_root)
    return _int_pin_env(
        _MARCH_STOP_ENV, DEFAULT_STOP_PIN, project_root, lo=start, hi=101
    )


def march_speed_ratio(project_root: Path | str | None = None) -> float:
    return _float_pin_env(
        _MARCH_RATIO_ENV, DEFAULT_SPEED_RATIO, project_root, lo=0.01, hi=1.0
    )


def march_min_dwell_s(project_root: Path | str | None = None) -> float:
    return _float_pin_env(
        _MARCH_DWELL_ENV, float(DEFAULT_MIN_DWELL_S), project_root, lo=0.0, hi=86400.0
    )


def march_max_dwell_s(project_root: Path | str | None = None) -> float:
    min_dwell = march_min_dwell_s(project_root)
    raw = _float_pin_env(
        _MARCH_MAX_DWELL_ENV,
        float(DEFAULT_MAX_DWELL_S),
        project_root,
        lo=0.0,
        hi=86400.0,
    )
    return max(min_dwell, raw)


def human_seconds_for_cell(
    checkpoint_index: int,
    project_root: Path | str | None = None,
) -> float | None:
    """1x human time for the leg that creates ``cpNN``, or None if missing."""
    table = load_timeout_table(project_root)
    row = (table.get("cells") or {}).get(str(int(checkpoint_index)))
    if not isinstance(row, dict):
        return None
    raw = row.get("time")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(parse_mmss(str(raw)))
    except (TypeError, ValueError):
        return None


def agent_policy_frames(
    checkpoint_index: int,
    project_root: Path | str | None = None,
) -> int | None:
    """Policy-controlled frames on the captured ``cpNN`` cell, or None."""
    slot = cell_slot_dir(yawn_rails_root(project_root), checkpoint_index)
    tape_path = slot / CELL_REPLAY_NAME
    if tape_path.is_file():
        try:
            tape = json.loads(tape_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            tape = None
        frames = policy_leg_frames_from_tape(tape if isinstance(tape, dict) else None)
        if frames is not None:
            return int(frames)
    meta_path = slot / CELL_META_NAME
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    quality = meta.get("quality") if isinstance(meta, dict) else None
    if not isinstance(quality, list) or len(quality) <= LEG_FRAMES_QUALITY_INDEX:
        return None
    try:
        billed = -int(quality[LEG_FRAMES_QUALITY_INDEX])
    except (TypeError, ValueError):
        return None
    if billed <= 0 or billed >= LEG_FRAMES_SENTINEL:
        return None
    return billed


def hunted_cell_ready(
    checkpoint_index: int,
    project_root: Path | str | None = None,
) -> bool:
    """True when the hunted cell has a loadable state on disk."""
    slot = cell_slot_dir(yawn_rails_root(project_root), checkpoint_index)
    return (slot / CELL_STATE_NAME).is_file()


def speed_gate_ok(
    agent_s: float | None,
    human_s: float | None,
    *,
    ratio: float = DEFAULT_SPEED_RATIO,
) -> bool:
    """True when agent time is at most ``ratio`` of the human 1x time (4x faster)."""
    if agent_s is None or human_s is None:
        return False
    if human_s <= 0.0 or agent_s < 0.0:
        return False
    return float(agent_s) <= float(human_s) * float(ratio)


def _state_path(project_root: Path | str | None = None) -> Path:
    raw = os.environ.get(_STATE_ENV, "").strip()
    if raw:
        path = Path(raw)
        if path.is_absolute():
            return path
        base = Path(project_root) if project_root is not None else Path.cwd()
        return base / path
    base = Path(project_root) if project_root is not None else Path.cwd()
    return base / _DEFAULT_STATE_REL


def load_march_state(project_root: Path | str | None = None) -> dict[str, Any]:
    path = _state_path(project_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_march_state(
    state: dict[str, Any],
    project_root: Path | str | None = None,
) -> None:
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="yawn_pin_march_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_pin_index(pin_file: Path, pin_index: int) -> None:
    """Set ``RE1_YAWN_RESET_PIN_INDEX`` in an existing pin file; keep other lines."""
    idx = int(pin_index)
    line = f"{_PIN_INDEX_KEY}={idx}\n"
    if pin_file.is_file():
        text = pin_file.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        replaced = False
        out: list[str] = []
        for raw in lines:
            stripped = raw.lstrip()
            if stripped.startswith(f"{_PIN_INDEX_KEY}="):
                out.append(line)
                replaced = True
            else:
                out.append(raw)
        if not replaced:
            if out and not out[-1].endswith("\n"):
                out[-1] = out[-1] + "\n"
            out.append(line)
        payload = "".join(out)
        if not payload.endswith("\n"):
            payload += "\n"
    else:
        payload = line
    pin_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="yawn_reset_pin_", suffix=".env", dir=str(pin_file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, pin_file)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _current_pin(project_root: Path | str | None) -> int | None:
    from re1_rl.yawn_rails import reset_pin_index_from_env

    return reset_pin_index_from_env(project_root)


def maybe_advance_pin(
    project_root: Path | str | None = None,
    *,
    now: float | None = None,
) -> MarchTick | None:
    """Hot-reload tick. No-op unless ``RE1_YAWN_PIN_MARCH=1``. Returns the tick."""
    if not pin_march_enabled(project_root):
        return None
    from re1_rl.yawn_rails import _pin_file_path

    pin_file = _pin_file_path(project_root)
    if pin_file is None:
        return None
    start = march_start_pin(project_root)
    stop = march_stop_pin(project_root)
    ratio = march_speed_ratio(project_root)
    min_dwell = march_min_dwell_s(project_root)
    max_dwell = march_max_dwell_s(project_root)
    clock = time.time() if now is None else float(now)

    pin = _current_pin(project_root)
    if pin is None or pin < start:
        write_pin_index(pin_file, start)
        save_march_state(
            {"pin_index": start, "pin_started_unix": clock},
            project_root,
        )
        hunted = start + 1
        human_s = human_seconds_for_cell(hunted, project_root)
        frames = agent_policy_frames(hunted, project_root)
        agent_s = None if frames is None else frames / float(DEFAULT_FPS)
        return MarchTick(
            pin_index=start,
            hunted_index=hunted,
            human_s=human_s,
            agent_s=agent_s,
            speed_ratio=ratio,
            dwell_s=0.0,
            min_dwell_s=min_dwell,
            max_dwell_s=max_dwell,
            speed_ok=speed_gate_ok(agent_s, human_s, ratio=ratio),
            dwell_ok=False,
            at_stop=start >= stop,
            advanced=pin != start,
            reason="clamped_to_start",
        )
    if pin > stop:
        write_pin_index(pin_file, stop)
        save_march_state(
            {"pin_index": stop, "pin_started_unix": clock},
            project_root,
        )
        hunted = stop + 1
        human_s = human_seconds_for_cell(hunted, project_root)
        frames = agent_policy_frames(hunted, project_root)
        agent_s = None if frames is None else frames / float(DEFAULT_FPS)
        return MarchTick(
            pin_index=stop,
            hunted_index=hunted,
            human_s=human_s,
            agent_s=agent_s,
            speed_ratio=ratio,
            dwell_s=0.0,
            min_dwell_s=min_dwell,
            max_dwell_s=max_dwell,
            speed_ok=speed_gate_ok(agent_s, human_s, ratio=ratio),
            dwell_ok=False,
            at_stop=True,
            advanced=True,
            reason="clamped_to_stop",
        )

    state = load_march_state(project_root)
    started = state.get("pin_started_unix")
    state_pin = state.get("pin_index")
    if state_pin != pin or not isinstance(started, (int, float)):
        started = clock
        save_march_state(
            {"pin_index": pin, "pin_started_unix": started},
            project_root,
        )
    dwell_s = max(0.0, clock - float(started))
    hunted = pin + 1
    human_s = human_seconds_for_cell(hunted, project_root)
    frames = agent_policy_frames(hunted, project_root)
    agent_s = None if frames is None else frames / float(DEFAULT_FPS)
    cell_ok = hunted_cell_ready(hunted, project_root)
    speed_ok = speed_gate_ok(agent_s, human_s, ratio=ratio) and cell_ok
    dwell_ok = dwell_s + 1e-9 >= float(min_dwell)
    max_dwell_ok = dwell_s + 1e-9 >= float(max_dwell)
    at_stop = pin >= stop

    if at_stop:
        return MarchTick(
            pin_index=pin,
            hunted_index=hunted,
            human_s=human_s,
            agent_s=agent_s,
            speed_ratio=ratio,
            dwell_s=dwell_s,
            min_dwell_s=min_dwell,
            max_dwell_s=max_dwell,
            speed_ok=speed_ok,
            dwell_ok=dwell_ok,
            at_stop=True,
            advanced=False,
            reason="hold_dog_fight",
        )
    if not dwell_ok:
        return MarchTick(
            pin_index=pin,
            hunted_index=hunted,
            human_s=human_s,
            agent_s=agent_s,
            speed_ratio=ratio,
            dwell_s=dwell_s,
            min_dwell_s=min_dwell,
            max_dwell_s=max_dwell,
            speed_ok=speed_ok,
            dwell_ok=False,
            at_stop=False,
            advanced=False,
            reason="dwell",
        )
    if not speed_ok and not (max_dwell_ok and cell_ok):
        return MarchTick(
            pin_index=pin,
            hunted_index=hunted,
            human_s=human_s,
            agent_s=agent_s,
            speed_ratio=ratio,
            dwell_s=dwell_s,
            min_dwell_s=min_dwell,
            max_dwell_s=max_dwell,
            speed_ok=False,
            dwell_ok=True,
            at_stop=False,
            advanced=False,
            reason="speed",
        )

    nxt = pin + 1
    write_pin_index(pin_file, nxt)
    save_march_state(
        {"pin_index": nxt, "pin_started_unix": clock},
        project_root,
    )
    next_hunt = nxt + 1
    next_human = human_seconds_for_cell(next_hunt, project_root)
    next_frames = agent_policy_frames(next_hunt, project_root)
    next_agent = None if next_frames is None else next_frames / float(DEFAULT_FPS)
    return MarchTick(
        pin_index=nxt,
        hunted_index=next_hunt,
        human_s=next_human,
        agent_s=next_agent,
        speed_ratio=ratio,
        dwell_s=0.0,
        min_dwell_s=min_dwell,
        max_dwell_s=max_dwell,
        speed_ok=speed_gate_ok(next_agent, next_human, ratio=ratio),
        dwell_ok=False,
        at_stop=nxt >= stop,
        advanced=True,
        reason="advanced" if speed_ok else "max_dwell",
    )


def format_tick(tick: MarchTick) -> str:
    def _fmt_s(value: float | None) -> str:
        if value is None:
            return "-"
        if value >= 60.0:
            mins = int(value // 60)
            secs = value - mins * 60
            return f"{mins}:{secs:05.2f}"
        return f"{value:.2f}s"

    hunt = f"cp{tick.hunted_index:02d}"
    pin = f"cp{tick.pin_index:02d}"
    need = tick.human_s * tick.speed_ratio if tick.human_s is not None else None
    return (
        f"pin {pin} hunt {hunt} agent={_fmt_s(tick.agent_s)} "
        f"human={_fmt_s(tick.human_s)} need<={_fmt_s(need)} "
        f"dwell={tick.dwell_s / 60.0:.1f}/{tick.min_dwell_s / 60.0:.0f}-"
        f"{tick.max_dwell_s / 60.0:.0f}m "
        f"{tick.reason}"
    )
