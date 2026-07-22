"""Shared typewriter-save champion: score v2, atomic replace, load."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from re1_rl.item_todo import canonical_item
from re1_rl.key_items import KEY_ITEM_NAMES
from re1_rl.memory_map import ITEM_IDS
from re1_rl.typewriter_save import TYPEWRITER_SAVE_MILESTONE

CHAMPION_MILESTONE = TYPEWRITER_SAVE_MILESTONE
CHAMPION_SUBDIR = "champions/mainhall_typewriter"
CHAMPION_JSON = "champion.json"
SCORE_VERSION = 2

_PB_ROOT_ENV = "RE1_PB_ROOT"
_TYPEWRITER_MILESTONE_PREFIX = "typewriter_save:"
_KEY_ITEM_NAME_SET: frozenset[str] = frozenset(KEY_ITEM_NAMES)

# Herb atom values; mixes are always the sum of component atoms.
_HERB_UNIT_V: dict[str, float] = {
    "green_herb": 1.0 / 3.0,
    "red_herb": 2.0 / 3.0,
    "blue_herb": 1.0 / 6.0,
    "mixed_herbs_gr": 1.0 / 3.0 + 2.0 / 3.0,
    "mixed_herbs_gg": 1.0 / 3.0 + 1.0 / 3.0,
    "mixed_herbs_gb": 1.0 / 3.0 + 1.0 / 6.0,
    "mixed_herbs_ggg": 1.0 / 3.0 + 1.0 / 3.0 + 1.0 / 3.0,
    "mixed_herbs_ggb": 1.0 / 3.0 + 1.0 / 3.0 + 1.0 / 6.0,
    "mixed_herbs_grb": 1.0 / 3.0 + 2.0 / 3.0 + 1.0 / 6.0,
}


def pb_root(project_root: Path | str) -> Path:
    override = os.environ.get(_PB_ROOT_ENV, "").strip()
    if override:
        return Path(override)
    return Path(project_root) / "states" / "pb"


def _normalize_room_id(room_id: int | str) -> str:
    s = str(room_id).strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    return s.upper()


def typewriter_champion_subdir(room_id: int | str) -> str:
    room = _normalize_room_id(room_id)
    if room == "106":
        return CHAMPION_SUBDIR
    return f"champions/typewriter_{room}"


def typewriter_champion_dir(project_root: Path | str, room_id: int | str) -> Path:
    return pb_root(project_root) / typewriter_champion_subdir(room_id)


def champion_dir(project_root: Path | str) -> Path:
    """Compat: Main Hall (106) champion slot."""
    return typewriter_champion_dir(project_root, "106")


def typewriter_milestone_id(room_id: int | str) -> str:
    return f"{_TYPEWRITER_MILESTONE_PREFIX}{_normalize_room_id(room_id)}"


def is_typewriter_milestone(trigger_id: str | None) -> bool:
    if not trigger_id:
        return False
    return str(trigger_id).startswith(_TYPEWRITER_MILESTONE_PREFIX)


def parse_typewriter_room(trigger_id: str | None) -> str | None:
    if not is_typewriter_milestone(trigger_id):
        return None
    room = str(trigger_id)[len(_TYPEWRITER_MILESTONE_PREFIX) :].strip()
    if not room:
        return None
    return _normalize_room_id(room)


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


def _normalize_inv_slots(
    inventory_slots: Iterable[Any] | None,
) -> list[tuple[str, int]]:
    if not inventory_slots:
        return []
    out: list[tuple[str, int]] = []
    for entry in inventory_slots:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            name, qty = entry[0], entry[1]
            if isinstance(name, int):
                decoded = ITEM_IDS.get(int(name))
                if not decoded:
                    continue
                name = decoded
            out.append((canonical_item(str(name)), int(qty)))
        elif isinstance(entry, dict):
            raw_name = entry.get("name") or entry.get("item") or ""
            out.append((canonical_item(str(raw_name)), int(entry.get("qty", 1) or 0)))
    return out


def _box_slots(box_cache: Iterable[Any] | None) -> list[tuple[str, int]]:
    """Decode box_cache list of (item_id, qty) via ITEM_IDS."""
    if not box_cache:
        return []
    out: list[tuple[str, int]] = []
    for entry in box_cache:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        item_id = int(entry[0])
        qty = int(entry[1])
        if item_id <= 0 or qty <= 0:
            continue
        name = ITEM_IDS.get(item_id)
        if not name:
            continue
        out.append((canonical_item(name), qty))
    return out


def champion_score(state: dict[str, Any] | None) -> tuple[int, int, int, int]:
    """Legacy v1 lexicographic score (higher better).

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


def champion_score_v2(
    *,
    inventory_slots: Iterable[Any] | None,
    box_cache: Iterable[Any] | None,
    ever_held: Iterable[str] | None,
    visited_rooms: Iterable[str] | None,
    hp: int,
) -> tuple[int, int, int, int, int]:
    """Unified V score: (v_milli, hp, handgun_bullets, -ink_ribbons, n_visited)."""
    inv = _normalize_inv_slots(inventory_slots)
    box = _box_slots(box_cache)
    physical = inv + box

    v_physical = 0.0
    ribbons = 0
    bullets = 0
    present_keys: set[str] = set()

    for name, qty in physical:
        q = max(0, int(qty))
        if not name or q <= 0:
            continue
        if name == "ink_ribbon":
            ribbons += q
            continue
        if name in ("beretta", "handgun_bullets"):
            bullets += q
        herb_u = _HERB_UNIT_V.get(name)
        if herb_u is not None:
            v_physical += herb_u * q
        else:
            v_physical += 1.0
            if name in _KEY_ITEM_NAME_SET:
                present_keys.add(name)

    held = {canonical_item(str(n)) for n in (ever_held or ()) if n}
    key_credit = held & _KEY_ITEM_NAME_SET
    key_credit -= present_keys
    v_keys = float(len(key_credit))

    v = v_physical + v_keys
    v_milli = int(round(1000.0 * v))
    n_visited = len({_normalize_room_id(r) for r in (visited_rooms or ()) if r})
    return (v_milli, int(hp), int(bullets), -int(ribbons), int(n_visited))


def score_beats(
    candidate: tuple[int, ...],
    incumbent: tuple[int, ...] | None,
    *,
    candidate_version: int = 2,
    incumbent_version: int | None = None,
) -> bool:
    """Return True if *candidate* should replace *incumbent*.

    Prefer same-version lexicographic compare. If the incumbent has no version
    (``None``) or an older ``score_version`` than the candidate, the candidate
    wins without lex-comparing incompatible tuples — so the first v2 record
    can replace a legacy v1 / unversioned incumbent. A newer incumbent schema
    is never beaten by an older candidate schema.

    When ``incumbent_version`` is omitted but both tuples share the same rank
    length, fall back to lex compare (legacy bare callers / sync before
    version wiring). Differing lengths with a missing incumbent version are
    treated as a schema upgrade and the candidate wins.
    """
    if incumbent is None:
        return True
    if incumbent_version == candidate_version:
        return tuple(candidate) > tuple(incumbent)
    if incumbent_version is None:
        if len(candidate) == len(incumbent):
            return tuple(candidate) > tuple(incumbent)
        return True
    if incumbent_version < candidate_version:
        return True
    return False


def _read_champion_json(path: Path) -> dict[str, Any] | None:
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


def load_champion_record(
    project_root_or_path: Path | str,
    room_id: int | str | None = None,
) -> dict[str, Any] | None:
    """Load champion.json by project root (+ optional room) or by path.

    *project_root_or_path* may be:
    - a project root (uses ``room_id`` or defaults to ``106``)
    - a champion directory containing ``champion.json``
    - a path to ``champion.json`` itself
    """
    p = Path(project_root_or_path)
    if p.is_file():
        return _read_champion_json(p)
    looks_like_slot = (
        room_id is None
        and (
            p.name == "mainhall_typewriter"
            or p.name.startswith("typewriter_")
            or (p / CHAMPION_JSON).is_file()
            or (p / "champion.State").is_file()
        )
    )
    if looks_like_slot:
        return _read_champion_json(p / CHAMPION_JSON)
    room = room_id if room_id is not None else "106"
    return _read_champion_json(typewriter_champion_dir(p, room) / CHAMPION_JSON)


def champion_bundle_for_reset(
    project_root: Path | str,
    room_id: int | str | None = None,
) -> dict[str, str] | None:
    """Paths relative to project_root for ``env.reset(options=pb_bundle=...)``."""
    room = room_id if room_id is not None else "106"
    rec = load_champion_record(project_root, room)
    if not rec:
        return None
    return {
        "state_path": str(rec["state_path"]),
        "sidecar_path": str(rec["sidecar_path"]),
        "milestone_id": str(
            rec.get("milestone_id") or typewriter_milestone_id(room)
        ),
    }


def list_filled_champions(project_root: Path | str) -> list[dict[str, Any]]:
    """Scan mainhall_typewriter + typewriter_* dirs with a coherent unlocked bundle.

    Skips slots mid-sync (lockfile) or with State/sidecar/json split in half.
    """
    from re1_rl.pb_bundle_io import verify_champion_bundle

    champs_root = pb_root(project_root) / "champions"
    if not champs_root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(champs_root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        name = child.name
        if name != "mainhall_typewriter" and not name.startswith("typewriter_"):
            continue
        ok, _reason = verify_champion_bundle(child, require_unlocked=True)
        if not ok:
            continue
        rec = _read_champion_json(child / CHAMPION_JSON)
        if rec is None:
            continue
        if not rec.get("state_path") or not rec.get("sidecar_path"):
            continue
        enriched = dict(rec)
        enriched.setdefault("champion_dir", child.as_posix())
        if name == "mainhall_typewriter":
            enriched.setdefault("room_id", str(rec.get("room_id") or "106"))
        elif name.startswith("typewriter_"):
            enriched.setdefault("room_id", name[len("typewriter_") :])
        out.append(enriched)
    return out


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
    room_id: int | str | None = None,
    ever_held: Iterable[str] | None = None,
    box_cache: Iterable[Any] | None = None,
    visited_rooms: Iterable[str] | None = None,
) -> bool:
    """Copy candidate into the per-room champion dir and replace if score wins.

    Returns True when champion was created or replaced. Writes ``score_version`` 2.
    """
    project_root = Path(project_root)
    room = _normalize_room_id(
        room_id if room_id is not None else (state.get("room_id") or "106")
    )

    if score is not None:
        score_t = tuple(int(x) for x in score)
    else:
        score_t = champion_score_v2(
            inventory_slots=_slots(state),
            box_cache=box_cache,
            ever_held=ever_held,
            visited_rooms=visited_rooms,
            hp=int(state.get("hp", 0) or 0),
        )

    cdir = typewriter_champion_dir(project_root, room)
    cdir.mkdir(parents=True, exist_ok=True)

    # Fast path: if a stronger incumbent is already visible, keep going.
    incumbent = load_champion_record(cdir)
    inc_score = None
    inc_version: int | None = None
    if incumbent:
        if "score_version" in incumbent:
            try:
                inc_version = int(incumbent["score_version"])
            except (TypeError, ValueError):
                inc_version = None
        if "score" in incumbent:
            try:
                inc_score = tuple(int(x) for x in incumbent["score"])
            except (TypeError, ValueError):
                inc_score = None
    if not score_beats(
        score_t,
        inc_score,
        candidate_version=SCORE_VERSION,
        incumbent_version=inc_version,
    ):
        return False

    dest_state = cdir / "champion.State"
    dest_sidecar = cdir / "champion.sidecar.json"
    try:
        rel_state = dest_state.relative_to(project_root).as_posix()
        rel_sidecar = dest_sidecar.relative_to(project_root).as_posix()
    except ValueError:
        rel_state = dest_state.as_posix()
        rel_sidecar = dest_sidecar.as_posix()

    record = {
        "milestone_id": typewriter_milestone_id(room),
        "state_path": rel_state,
        "sidecar_path": rel_sidecar,
        "score": list(score_t),
        "score_version": SCORE_VERSION,
        "room_id": room,
        "hp": int(state.get("hp", 0) or 0),
    }
    from re1_rl.pb_bundle_io import install_champion_bundle, wait_for_slot_unlock

    # Lock held → wait, then re-check; install does a second CAS under the lock.
    if not wait_for_slot_unlock(cdir, timeout_s=90.0):
        return False
    incumbent = load_champion_record(cdir)
    inc_score = None
    inc_version = None
    if incumbent:
        if "score_version" in incumbent:
            try:
                inc_version = int(incumbent["score_version"])
            except (TypeError, ValueError):
                inc_version = None
        if "score" in incumbent:
            try:
                inc_score = tuple(int(x) for x in incumbent["score"])
            except (TypeError, ValueError):
                inc_score = None
    if not score_beats(
        score_t,
        inc_score,
        candidate_version=SCORE_VERSION,
        incumbent_version=inc_version,
    ):
        return False

    try:
        bid = install_champion_bundle(
            cdir,
            state_src=state_path,
            sidecar_src=sidecar_path,
            record=record,
            holder=f"capture:{os.environ.get('COMPUTERNAME', 'local')}",
            candidate_score=score_t,
            candidate_version=SCORE_VERSION,
            wait_timeout_s=90.0,
        )
    except RuntimeError:
        return False
    return bid is not None
