"""Shared typewriter-save champion: score, atomic replace, load."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from re1_rl.item_todo import canonical_item
from re1_rl.typewriter_save import TYPEWRITER_SAVE_MILESTONE

CHAMPION_MILESTONE = TYPEWRITER_SAVE_MILESTONE
CHAMPION_SUBDIR = "champions/mainhall_typewriter"
CHAMPION_JSON = "champion.json"

_PB_ROOT_ENV = "RE1_PB_ROOT"


def pb_root(project_root: Path | str) -> Path:
    override = os.environ.get(_PB_ROOT_ENV, "").strip()
    if override:
        return Path(override)
    return Path(project_root) / "states" / "pb"


def champion_dir(project_root: Path | str) -> Path:
    return pb_root(project_root) / CHAMPION_SUBDIR


def _slots(state: dict[str, Any] | None) -> list[tuple[str, int]]:
    if not state:
        return []
    raw = state.get("inventory_slots")
    if raw is None:
        return [(canonical_item(str(n)), 1) for n in (state.get("inventory") or []) if n]
    out: list[tuple[str, int]] = []
    for entry in raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            out.append((canonical_item(str(entry[0])), int(entry[1])))
        elif isinstance(entry, dict):
            out.append(
                (
                    canonical_item(str(entry.get("name") or entry.get("item") or "")),
                    int(entry.get("qty", 1) or 0),
                )
            )
    return out


def champion_score(state: dict[str, Any] | None) -> tuple[int, int, int, int]:
    """Lexicographic score (higher better).

    (valuable_slots, hp, handgun_bullets, -ink_ribbons)
    """
    valuable = 0
    ribbons = 0
    bullets = 0
    for name, qty in _slots(state):
        q = max(0, int(qty))
        if not name or q <= 0:
            continue
        if name == "ink_ribbon":
            ribbons += q
        elif name in ("beretta", "handgun_bullets"):
            valuable += 1
            bullets += q
        else:
            valuable += 1
    hp = int((state or {}).get("hp", 0) or 0)
    return (valuable, hp, bullets, -ribbons)


def score_beats(candidate: tuple[int, ...], incumbent: tuple[int, ...] | None) -> bool:
    if incumbent is None:
        return True
    return tuple(candidate) > tuple(incumbent)


def load_champion_record(project_root: Path | str) -> dict[str, Any] | None:
    path = champion_dir(project_root) / CHAMPION_JSON
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("state_path") or not data.get("sidecar_path"):
        return None
    return data


def champion_bundle_for_reset(project_root: Path | str) -> dict[str, str] | None:
    """Paths relative to project_root for ``env.reset(options=pb_bundle=...)``."""
    rec = load_champion_record(project_root)
    if not rec:
        return None
    return {
        "state_path": str(rec["state_path"]),
        "sidecar_path": str(rec["sidecar_path"]),
        "milestone_id": str(rec.get("milestone_id") or CHAMPION_MILESTONE),
    }


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="champion_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def try_replace_champion(
    project_root: Path | str,
    *,
    state_path: Path,
    sidecar_path: Path,
    state: dict[str, Any],
    score: tuple[int, ...] | None = None,
) -> bool:
    """Copy candidate into champion dir and replace if score wins.

    Returns True when champion was created or replaced.
    """
    project_root = Path(project_root)
    score_t = tuple(score) if score is not None else champion_score(state)
    cdir = champion_dir(project_root)
    cdir.mkdir(parents=True, exist_ok=True)

    incumbent = load_champion_record(project_root)
    inc_score = None
    if incumbent and "score" in incumbent:
        try:
            inc_score = tuple(int(x) for x in incumbent["score"])
        except (TypeError, ValueError):
            inc_score = None
    if not score_beats(score_t, inc_score):
        return False

    dest_state = cdir / "champion.State"
    dest_sidecar = cdir / "champion.sidecar.json"
    shutil.copy2(state_path, dest_state)
    shutil.copy2(sidecar_path, dest_sidecar)

    try:
        rel_state = dest_state.relative_to(project_root).as_posix()
        rel_sidecar = dest_sidecar.relative_to(project_root).as_posix()
    except ValueError:
        rel_state = dest_state.as_posix()
        rel_sidecar = dest_sidecar.as_posix()

    record = {
        "milestone_id": CHAMPION_MILESTONE,
        "state_path": rel_state,
        "sidecar_path": rel_sidecar,
        "score": list(score_t),
        "room_id": str(state.get("room_id", "") or ""),
        "hp": int(state.get("hp", 0) or 0),
    }
    _atomic_write_json(cdir / CHAMPION_JSON, record)
    return True
