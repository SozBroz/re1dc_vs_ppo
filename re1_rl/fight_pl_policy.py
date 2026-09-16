"""Fighting-PL duration / march policy (generous walls until kit end-state).

A hop ``tip -> tip+1`` is fighting when docs kit deltas show kills up, HP down,
or any on-person ammo column down. Fighting hops keep the full planner wall
under adaptive cap until the live target cell meets champion HP/kills/ammo;
only then stage-tighten (5x / 4x / 3x with high floors). March frames factor
defaults to 1.50 for fighting hops (nav hops keep the pin-file factor).
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

_RESOURCES_REL = Path("docs/planner_loyal_resources.md")
_CHAMPIONS_REL = Path("data/planner_march_champions.json")
_STATE_REL = Path("data/planner_fight_cap_state.json")
_KIT_CACHE: dict[str, Any] = {}
_CHAMP_CACHE: dict[str, Any] = {}

# March frames budget vs resources.md Frames for fighting hops.
FIGHT_MARCH_FRAMES_FACTOR = 1.50

# After kit-qualified live cell: staged tighten (emulated-frame multiples).
FIGHT_STAGE_FACTORS = (5.0, 4.0, 3.0)
FIGHT_STAGE_FLOORS_F = (14_400, 12_000, 9_600)
FIGHT_STAGE_MIN_QUALIFIED = (3, 10, 25)  # sticky counts to enter stage 0/1/2

_AMMO_KEYS = ("pistol", "shotgun", "bazooka", "magnum")


def _project_root(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = (os.environ.get("RE1_RL_ROOT") or "").strip()
    return Path(env) if env else Path.cwd()


def parse_resources_kit(
    project_root: Path | str | None = None,
) -> dict[int, dict[str, int]]:
    """``{slot: {hp,kills,pistol,shotgun,bazooka,magnum,frames}}`` from resources.md."""
    root = _project_root(project_root)
    path = root / _RESOURCES_REL
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _KIT_CACHE.get(str(path))
    if isinstance(cached, dict) and cached.get("mtime") == mtime:
        return cached["kit"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    kit: dict[int, dict[str, int]] = {}
    in_kit = False
    for ln in text.splitlines():
        s = ln.strip()
        if s == "## Kit by PL":
            in_kit = True
            continue
        if in_kit and s.startswith("## "):
            break
        if not in_kit or not s.startswith("|") or s.startswith("| :") or s.startswith("| PL"):
            continue
        parts = [p.strip() for p in s.strip("|").split("|")]
        if len(parts) < 8:
            continue
        name = parts[0].strip("`")
        if not name.startswith("pl") or not name[2:].isdigit():
            continue
        try:
            slot = int(name[2:])
            row = {
                "hp": int(parts[1]) if parts[1] else 0,
                "kills": int(parts[2]) if parts[2] else 0,
                "pistol": int(parts[3]) if parts[3] else 0,
                "shotgun": int(parts[4]) if parts[4] else 0,
                "bazooka": int(parts[5]) if parts[5] else 0,
                "magnum": int(parts[6]) if parts[6] else 0,
                "frames": int(parts[7]) if parts[7] else 0,
            }
        except ValueError:
            continue
        kit[slot] = row
    _KIT_CACHE[str(path)] = {"mtime": mtime, "kit": kit}
    return kit


def is_fighting_hop(
    tip_slot: int, target_slot: int, project_root: Path | str | None = None
) -> bool:
    """True when champion kit implies combat / resource spend on this hop.

    Fail-closed to fighting when either row is missing (safer: keep full wall).
    """
    tip = int(tip_slot)
    target = int(target_slot)
    if target != tip + 1:
        # Non-linear seeks: treat as fighting to avoid accidental strangle.
        return True
    kit = parse_resources_kit(project_root)
    a = kit.get(tip)
    b = kit.get(target)
    if a is None or b is None:
        return True
    if int(b.get("kills", 0)) > int(a.get("kills", 0)):
        return True
    if int(b.get("hp", 0)) < int(a.get("hp", 0)):
        return True
    for key in _AMMO_KEYS:
        if int(b.get(key, 0)) < int(a.get(key, 0)):
            return True
    return False


def march_requires_kit_bar(
    tip_slot: int, target_slot: int, project_root: Path | str | None = None
) -> bool:
    """True when march advance must enforce ammo + frames (combat outcome).

    Kills-up or HP-down hops keep the full kit bar. Ammo-only drips (e.g.
    ``pl52→pl53`` pistol −1 on a traverse) use nav qualify so a soft tip kit
    cannot strand the tape before the next real fight. Fail-closed to True
    when either resources row is missing.
    """
    tip = int(tip_slot)
    target = int(target_slot)
    if target != tip + 1:
        return True
    kit = parse_resources_kit(project_root)
    a = kit.get(tip)
    b = kit.get(target)
    if a is None or b is None:
        return True
    if int(b.get("kills", 0)) > int(a.get("kills", 0)):
        return True
    if int(b.get("hp", 0)) < int(a.get("hp", 0)):
        return True
    return False


def _champions(project_root: Path | str | None = None) -> dict[int, list[int]]:
    root = _project_root(project_root)
    path = root / _CHAMPIONS_REL
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _CHAMP_CACHE.get(str(path))
    if isinstance(cached, dict) and cached.get("mtime") == mtime:
        return cached["cells"]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cells: dict[int, list[int]] = {}
    raw = doc.get("cells") if isinstance(doc, dict) else None
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, list) and len(v) >= 3:
                cells[idx] = [int(x) for x in v[:11]]
    _CHAMP_CACHE[str(path)] = {"mtime": mtime, "cells": cells}
    return cells


def live_quality_for_pl(
    target_slot: int, cells_root: Path | str
) -> list[int] | None:
    try:
        meta = Path(cells_root) / "cells" / f"pl{int(target_slot):02d}" / "meta.json"
        if not meta.is_file():
            return None
        q = (json.loads(meta.read_text(encoding="utf-8")) or {}).get("quality")
        if not isinstance(q, list) or len(q) < 3:
            return None
        return [int(x) for x in q]
    except (OSError, ValueError, TypeError):
        return None


def live_kit_meets_champion(
    target_slot: int,
    cells_root: Path | str,
    project_root: Path | str | None = None,
) -> bool:
    """True when live cell HP/kills/ammo (dims 0/1/2) meet champion bars.

    Ammo may trail champion by ``MARCH_AMMO_SLACK`` (same as march advance).
    """
    from re1_rl.planner_march import MARCH_AMMO_SLACK

    live = live_quality_for_pl(target_slot, cells_root)
    champ = _champions(project_root).get(int(target_slot))
    if live is None or champ is None or len(champ) < 3:
        return False
    return (
        int(live[0]) >= int(champ[0])
        and int(live[1]) >= int(champ[1])
        and int(live[2]) >= int(champ[2]) - int(MARCH_AMMO_SLACK)
    )


def _state_path(project_root: Path | str | None = None) -> Path:
    return _project_root(project_root) / _STATE_REL


def _read_state(project_root: Path | str | None = None) -> dict[str, Any]:
    path = _state_path(project_root)
    if not path.is_file():
        return {"hops": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"hops": {}}
    if not isinstance(doc, dict):
        return {"hops": {}}
    hops = doc.get("hops")
    if not isinstance(hops, dict):
        doc["hops"] = {}
    return doc


def _write_state(project_root: Path | str | None, doc: dict[str, Any]) -> None:
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _hop_key(tip_slot: int, target_slot: int) -> str:
    return f"{int(tip_slot):02d}->{int(target_slot):02d}"


def note_kit_qualified_success(
    tip_slot: int,
    target_slot: int,
    *,
    project_root: Path | str | None = None,
    emulated_frames: int = 0,
    worker_id: str = "",
) -> None:
    """Record one kit-qualified completion for sticky stage progression."""
    if not is_fighting_hop(tip_slot, target_slot, project_root):
        return
    doc = _read_state(project_root)
    hops = doc.setdefault("hops", {})
    key = _hop_key(tip_slot, target_slot)
    block = hops.get(key)
    if not isinstance(block, dict):
        block = {"qualified": [], "stage": -1}
    quals = block.get("qualified")
    if not isinstance(quals, list):
        quals = []
    quals.append(
        {
            "unix": time.time(),
            "emulated_frames": int(emulated_frames),
            "worker_id": str(worker_id or ""),
        }
    )
    # Cap history so the file stays small.
    block["qualified"] = quals[-64:]
    block["stage"] = int(_stage_from_count(len(block["qualified"])))
    hops[key] = block
    try:
        _write_state(project_root, doc)
    except OSError:
        pass


def _stage_from_count(n: int) -> int:
    """-1 = full wall; 0/1/2 = 5x/4x/3x stages."""
    if n < int(FIGHT_STAGE_MIN_QUALIFIED[0]):
        return -1
    if n < int(FIGHT_STAGE_MIN_QUALIFIED[1]):
        return 0
    if n < int(FIGHT_STAGE_MIN_QUALIFIED[2]):
        return 1
    return 2


def fight_cap_stage(
    tip_slot: int,
    target_slot: int,
    cells_root: Path | str,
    project_root: Path | str | None = None,
) -> int:
    """Sticky stage for a fighting hop (-1 = keep full wall)."""
    if not live_kit_meets_champion(target_slot, cells_root, project_root):
        return -1
    doc = _read_state(project_root)
    block = (doc.get("hops") or {}).get(_hop_key(tip_slot, target_slot))
    n = 0
    if isinstance(block, dict):
        quals = block.get("qualified")
        if isinstance(quals, list):
            n = len(quals)
    return int(_stage_from_count(n))


def fight_adaptive_cap_frames(
    *,
    tip_slot: int,
    target_slot: int,
    best_emulated_frames: int,
    cells_root: Path | str,
    project_root: Path | str | None = None,
    boss: bool = False,
) -> int:
    """Wall for a fighting hop after a mint was observed."""
    from re1_rl.planner_hop_score import planner_timeout_frames

    wall = int(planner_timeout_frames(boss=boss))
    stage = fight_cap_stage(tip_slot, target_slot, cells_root, project_root)
    if stage < 0:
        return wall
    best = max(0, int(best_emulated_frames))
    if best <= 0:
        return wall
    factor = float(FIGHT_STAGE_FACTORS[min(stage, 2)])
    floor_f = int(FIGHT_STAGE_FLOORS_F[min(stage, 2)])
    tight_steps = int(math.ceil((float(best) * factor) / 8.0))
    tight = int(tight_steps * 8)
    return int(min(max(int(tight), int(floor_f)), wall))


def march_frames_factor_for_target(
    target_slot: int,
    project_root: Path | str | None = None,
    *,
    base_factor: float = 1.11,
) -> float:
    """Nav keeps ``base_factor``; fighting hops raise the floor to 1.50."""
    tip = int(target_slot) - 1
    if tip < 0:
        return float(base_factor)
    if is_fighting_hop(tip, int(target_slot), project_root):
        return float(max(float(base_factor), float(FIGHT_MARCH_FRAMES_FACTOR)))
    return float(base_factor)
