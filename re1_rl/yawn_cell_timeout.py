"""Per-checkpoint emulated-frame fail budgets for Yawn one-leg rails.

Human 1x playthrough times (MM:SS.SS) are converted to NTSC frames (60 fps).
The budget applies to the leg that *creates* that CP: fresh start uses CP00,
loading CP16 uses CP17, and so on. Missing CPs have no per-cell wall (the
existing 12 min idle / max_steps cap still applies).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULT_REL = Path("data/yawn_cell_timeouts.json")
_FPS = 60
_CAP_FRAMES = 12 * 60 * 60
_DEFAULT_MULT = 2.0
_HARD_MULT = 4.0
_CUTSCENE_MULT = 1.5
# Flat 12-minute wall (no per-CP human-time multipliers).
FLAT_CELL_TIMEOUT_FRAMES = _CAP_FRAMES
_FLAT_12M_ENV = "RE1_CELL_TIMEOUT_FLAT_12M"

_cache: tuple[float, dict[str, Any]] | None = None


def flat_cell_timeout_enabled() -> bool:
    """When set, every cell uses a plain 12-minute wall (ignores custom table)."""
    raw = (os.environ.get(_FLAT_12M_ENV) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _seconds_to_hundredths(raw: str) -> int:
    token = str(raw or "").strip()
    if not token:
        raise ValueError("empty seconds")
    if "." in token:
        whole, frac = token.split(".", 1)
        frac = (frac + "00")[:2]
        return int(whole or "0") * 100 + int(frac)
    return int(token) * 100


def parse_mmss_hundredths(raw: str) -> int:
    """Parse ``MM:SS.SS`` or ``SS.SS`` into integer hundredths of a second."""
    token = str(raw or "").strip()
    if not token:
        raise ValueError("empty time")
    if ":" in token:
        minutes_s, seconds_s = token.split(":", 1)
        return int(minutes_s) * 6000 + _seconds_to_hundredths(seconds_s)
    return _seconds_to_hundredths(token)


def parse_mmss(raw: str) -> float:
    """Parse ``MM:SS.SS`` or ``SS.SS`` into seconds."""
    return parse_mmss_hundredths(raw) / 100.0


def frames_from_human_time(
    raw: str,
    *,
    multiplier: float,
    fps: int = _FPS,
    cap_frames: int = _CAP_FRAMES,
) -> int:
    """1x seconds → emulated frames after multiplier, clamped to ``cap_frames``."""
    hundredths = parse_mmss_hundredths(raw)
    frames = int(round(hundredths * int(fps) * float(multiplier) / 100.0))
    cap = max(0, int(cap_frames))
    if frames > cap:
        return cap
    return max(0, frames)


def _table_path(project_root: Path | str | None = None) -> Path:
    if project_root is None:
        return Path.cwd() / _DEFAULT_REL
    return Path(project_root) / _DEFAULT_REL


def load_timeout_table(
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    """Read ``data/yawn_cell_timeouts.json`` (mtime-cached)."""
    global _cache
    path = _table_path(project_root)
    if not path.is_file():
        return {}
    mtime = path.stat().st_mtime
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
    _cache = (mtime, data)
    return data


def created_checkpoint_index(planner: Any) -> int | None:
    """CP index being created (seq 1 → 0). None if the planner has no hunt."""
    seq = getattr(planner, "current_route_seq", lambda: None)()
    if seq is not None:
        return max(0, int(seq) - 1)
    if planner is None:
        return None
    try:
        return max(0, int(planner.waypoint_index))
    except (AttributeError, TypeError, ValueError):
        return None


def cell_timeout_frames(
    created_index: int,
    project_root: Path | str | None = None,
) -> int:
    """Emulated-frame budget for creating ``cp{created_index:02d}``, or 0 if none.

    With ``RE1_CELL_TIMEOUT_FLAT_12M=1``, always returns the 12-minute cap
    (custom ``yawn_cell_timeouts.json`` rows are ignored).
    """
    if flat_cell_timeout_enabled():
        return int(FLAT_CELL_TIMEOUT_FRAMES)
    table = load_timeout_table(project_root)
    cells = table.get("cells") or {}
    row = cells.get(str(int(created_index)))
    if not isinstance(row, dict):
        return 0
    fps = int(table.get("fps") or _FPS)
    cap = int(table.get("cap_frames") or _CAP_FRAMES)
    if row.get("frames") is not None:
        return min(cap, max(0, int(row["frames"])))
    raw = row.get("time")
    if raw is None or str(raw).strip() == "":
        return 0
    if row.get("multiplier") is not None:
        mult = float(row["multiplier"])
    elif bool(row.get("hard")):
        mult = float(table.get("hard_multiplier") or _HARD_MULT)
    elif bool(row.get("cutscene")):
        mult = float(table.get("cutscene_multiplier") or _CUTSCENE_MULT)
    else:
        mult = float(table.get("default_multiplier") or _DEFAULT_MULT)
    return frames_from_human_time(str(raw), multiplier=mult, fps=fps, cap_frames=cap)


def cell_timeout_frames_for_planner(
    planner: Any,
    project_root: Path | str | None = None,
) -> int:
    if flat_cell_timeout_enabled():
        return int(FLAT_CELL_TIMEOUT_FRAMES)
    idx = created_checkpoint_index(planner)
    if idx is None:
        return 0
    return cell_timeout_frames(idx, project_root)
