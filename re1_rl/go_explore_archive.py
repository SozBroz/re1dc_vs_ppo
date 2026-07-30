"""Go-Explore lite archive v2 — digest-aware cells with path-filtered frontier.

JSON schema (v2)::

  {
    "version": 2,
    "cells": {
      "v2|r=105|x=3|z=1|m=gallery:idle": {
        "record_id": "...",
        "cell_key": "v2|r=105|x=3|z=1|m=gallery:idle",
        "room_id": "105",
        "tile_bin": [3, 1],
        "milestone_digest": "gallery:idle",
        "quality": [hp, ammo, healing, slots, poison],
        "visit_count": 2,
        "bundle_path": null,
        "meta": {}
      }
    }
  }

File lock mirrors ``pb_bundle_io`` (exclusive create of a lockfile beside the
archive JSON). Windows-safe via ``O_CREAT|O_EXCL``.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from re1_rl.milestone_digest import (
    DEFAULT_TILE_SPAN,
    YAWN_PATH_ROOMS,
    cell_key_v2,
    parse_cell_key_v2,
)

ARCHIVE_VERSION = 2
LEGACY_ARCHIVE_VERSION = 1

_LOCK_NAME = "archive.sync.lock"
_STALE_LOCK_S = 180.0
_MAX_CELLS_PER_ROOM_ENV = "RE1_GO_MAX_CELLS_PER_ROOM"
_DEFAULT_MAX_CELLS_PER_ROOM = 40

Quality = tuple[int, int, int, int, int]


def max_cells_per_room_default() -> int:
    raw = os.environ.get(_MAX_CELLS_PER_ROOM_ENV, "").strip()
    if not raw:
        return _DEFAULT_MAX_CELLS_PER_ROOM
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_CELLS_PER_ROOM


def tile_bin(x: int, z: int, *, tile_span: int = DEFAULT_TILE_SPAN) -> tuple[int, int]:
    """Coarse allocentric tile indices inside a room."""
    span = max(1, int(tile_span))
    return (int(x) // span, int(z) // span)


def quality_beats(a: Quality | list[int] | tuple[int, ...], b: Quality | list[int] | tuple[int, ...] | None) -> bool:
    """True if *a* should replace *b* (lexicographic, higher better)."""
    if b is None:
        return True
    return tuple(int(x) for x in a) > tuple(int(x) for x in b)


def new_record_id() -> str:
    return uuid.uuid4().hex


def _normalize_room_id(room_id: str | int) -> str:
    s = str(room_id).strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    return s.upper()


def _lock_path(archive_path: Path) -> Path:
    return archive_path.parent / _LOCK_NAME


def _clear_stale_lock(archive_path: Path, *, stale_s: float = _STALE_LOCK_S) -> bool:
    lp = _lock_path(archive_path)
    if not lp.is_file():
        return False
    try:
        age = time.time() - lp.stat().st_mtime
    except OSError:
        return False
    if age < float(stale_s):
        return False
    try:
        lp.unlink()
        return True
    except OSError:
        return False


def acquire_archive_lock(
    archive_path: Path | str,
    *,
    holder: str = "go_explore",
    stale_s: float = _STALE_LOCK_S,
) -> bool:
    """Exclusive lockfile beside archive.json. False if another holder is live."""
    path = Path(archive_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _clear_stale_lock(path, stale_s=stale_s)
    lp = _lock_path(path)
    if lp.is_file():
        return False
    payload = {
        "holder": str(holder),
        "created_unix": time.time(),
        "pid": os.getpid(),
    }
    tmp = path.parent / f".{_LOCK_NAME}.{os.getpid()}.tmp"
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(tmp.read_text(encoding="utf-8"))
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        return True
    except FileExistsError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def release_archive_lock(archive_path: Path | str) -> None:
    try:
        _lock_path(Path(archive_path)).unlink()
    except OSError:
        pass


def wait_for_archive_unlock(
    archive_path: Path | str,
    *,
    timeout_s: float = 90.0,
    poll_s: float = 0.25,
    stale_s: float = _STALE_LOCK_S,
) -> bool:
    path = Path(archive_path)
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        _clear_stale_lock(path, stale_s=stale_s)
        if not _lock_path(path).is_file():
            return True
        if time.monotonic() >= deadline:
            _clear_stale_lock(path, stale_s=stale_s)
            return not _lock_path(path).is_file()
        time.sleep(max(0.05, float(poll_s)))


@contextmanager
def archive_locked(
    archive_path: Path | str,
    *,
    holder: str = "go_explore",
    timeout_s: float = 90.0,
) -> Iterator[None]:
    path = Path(archive_path)
    if not wait_for_archive_unlock(path, timeout_s=timeout_s):
        raise TimeoutError(f"archive lock timeout: {path}")
    if not acquire_archive_lock(path, holder=holder):
        # Lost race after wait — one more short wait.
        if not wait_for_archive_unlock(path, timeout_s=min(5.0, timeout_s)):
            raise TimeoutError(f"archive lock busy: {path}")
        if not acquire_archive_lock(path, holder=holder):
            raise TimeoutError(f"archive lock acquire failed: {path}")
    try:
        yield
    finally:
        release_archive_lock(path)


@dataclass
class ArchiveCell:
    record_id: str
    cell_key: str
    room_id: str
    tile_bin: tuple[int, int]
    milestone_digest: str
    quality: Quality = (0, 0, 0, 0, 0)
    visit_count: int = 0
    bundle_path: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.cell_key

    def to_json(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "cell_key": self.cell_key,
            "room_id": self.room_id,
            "tile_bin": list(self.tile_bin),
            "milestone_digest": self.milestone_digest,
            "quality": list(self.quality),
            "visit_count": self.visit_count,
            "bundle_path": self.bundle_path,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], *, fallback_key: str | None = None) -> ArchiveCell:
        key = str(data.get("cell_key") or fallback_key or "")
        if key.startswith("v2|"):
            parsed = parse_cell_key_v2(key)
            room = str(data.get("room_id") or parsed["room_id"])
            tb_raw = data.get("tile_bin")
            if tb_raw is not None:
                tb = (int(tb_raw[0]), int(tb_raw[1]))
            else:
                tb = tuple(parsed["tile_bin"])  # type: ignore[assignment]
            digest = str(data.get("milestone_digest", parsed["milestone_digest"]))
        else:
            room = _normalize_room_id(data.get("room_id", "000"))
            tb_raw = data.get("tile_bin", [0, 0])
            tb = (int(tb_raw[0]), int(tb_raw[1]))
            digest = str(data.get("milestone_digest", ""))
            if not key:
                key = cell_key_v2(room, tb[0] * DEFAULT_TILE_SPAN, tb[1] * DEFAULT_TILE_SPAN, digest)

        q_raw = data.get("quality") or [0, 0, 0, 0, 0]
        q = tuple(int(x) for x in list(q_raw)[:5])
        while len(q) < 5:
            q = q + (0,)
        return cls(
            record_id=str(data.get("record_id") or new_record_id()),
            cell_key=key,
            room_id=_normalize_room_id(room),
            tile_bin=(int(tb[0]), int(tb[1])),
            milestone_digest=digest,
            quality=(int(q[0]), int(q[1]), int(q[2]), int(q[3]), int(q[4])),
            visit_count=int(data.get("visit_count", 0) or 0),
            bundle_path=data.get("bundle_path") or data.get("state_path"),
            meta=dict(data.get("meta") or {}),
        )


def _migrate_v1_cells(raw_cells: dict[str, Any]) -> dict[str, ArchiveCell]:
    """Minimal v1 → v2: keep room/tile, empty digest, drop score → quality zeros."""
    out: dict[str, ArchiveCell] = {}
    for key, val in (raw_cells or {}).items():
        if not isinstance(val, dict):
            continue
        room = _normalize_room_id(val.get("room_id", "000"))
        tb_raw = val.get("tile_bin", [0, 0])
        tb = (int(tb_raw[0]), int(tb_raw[1]))
        # v1 keys were "ROOM:tx,tz" — synthesize empty-digest v2 key.
        digest = ""
        v2_key = cell_key_v2(room, tb[0] * DEFAULT_TILE_SPAN, tb[1] * DEFAULT_TILE_SPAN, digest)
        cell = ArchiveCell(
            record_id=str(val.get("record_id") or new_record_id()),
            cell_key=v2_key,
            room_id=room,
            tile_bin=tb,
            milestone_digest=digest,
            quality=(0, 0, 0, 0, 0),
            visit_count=int(val.get("visit_count", 0) or 0),
            bundle_path=val.get("state_path") or val.get("bundle_path"),
            meta=dict(val.get("meta") or {}),
        )
        # Preserve legacy score in meta for forensics.
        if "score" in val and "legacy_score" not in cell.meta:
            cell.meta["legacy_score"] = val["score"]
        out[v2_key] = cell
    return out


class GoExploreArchive:
    """In-memory Go-Explore cell store with locked JSON persistence (v2)."""

    def __init__(
        self,
        path: Path | str,
        *,
        tile_span: int = DEFAULT_TILE_SPAN,
        max_cells_per_room: int | None = None,
        migrate_v1: bool = True,
    ) -> None:
        self.path = Path(path).resolve()
        self.tile_span = int(tile_span)
        self.max_cells_per_room = (
            int(max_cells_per_room)
            if max_cells_per_room is not None
            else max_cells_per_room_default()
        )
        self.migrate_v1 = bool(migrate_v1)
        self.cells: dict[str, ArchiveCell] = {}

    def load(self) -> None:
        if not self.path.is_file():
            self.cells = {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        version = int(raw.get("version", 0) or 0)
        if version == ARCHIVE_VERSION:
            self.cells = {
                key: ArchiveCell.from_json(val, fallback_key=key)
                for key, val in (raw.get("cells") or {}).items()
            }
            return
        if version == LEGACY_ARCHIVE_VERSION:
            if not self.migrate_v1:
                raise ValueError(
                    f"unsupported archive version {version}; expected {ARCHIVE_VERSION}. "
                    "Pass migrate_v1=True or delete the v1 archive."
                )
            self.cells = _migrate_v1_cells(raw.get("cells") or {})
            return
        raise ValueError(
            f"unsupported archive version {version!r}; expected {ARCHIVE_VERSION}"
            + (f" (or {LEGACY_ARCHIVE_VERSION} with migrate_v1)" if self.migrate_v1 else "")
        )

    def save(self) -> None:
        payload = {
            "version": ARCHIVE_VERSION,
            "cells": {k: c.to_json() for k, c in sorted(self.cells.items())},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix="archive_",
            suffix=".json",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def load_locked(self, *, holder: str = "go_explore") -> None:
        with archive_locked(self.path, holder=holder):
            self.load()

    def save_locked(self, *, holder: str = "go_explore") -> None:
        with archive_locked(self.path, holder=holder):
            self.save()

    def _room_cell_count(self, room_id: str) -> int:
        room = _normalize_room_id(room_id)
        return sum(1 for c in self.cells.values() if c.room_id == room)

    def cell_from_pose(
        self,
        room_id: str,
        x: int,
        z: int,
        *,
        digest: str,
        record_id: str | None = None,
        quality: Quality | list[int] | tuple[int, ...] | None = None,
    ) -> ArchiveCell:
        room = _normalize_room_id(room_id)
        tb = tile_bin(x, z, tile_span=self.tile_span)
        key = cell_key_v2(room, x, z, digest, tile_span=self.tile_span)
        cell = self.cells.get(key)
        if cell is None:
            q = tuple(int(v) for v in (quality or (0, 0, 0, 0, 0)))
            while len(q) < 5:
                q = q + (0,)
            cell = ArchiveCell(
                record_id=str(record_id or new_record_id()),
                cell_key=key,
                room_id=room,
                tile_bin=tb,
                milestone_digest=str(digest),
                quality=(int(q[0]), int(q[1]), int(q[2]), int(q[3]), int(q[4])),
            )
            self.cells[key] = cell
        return cell

    def upsert(
        self,
        *,
        room_id: str,
        x: int,
        z: int,
        digest: str,
        quality: Quality | list[int] | tuple[int, ...],
        bundle_path: str | None = None,
        meta: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> ArchiveCell | None:
        """Admit or update a cell. Enforces per-room cap for *new* keys.

        Returns the cell when admitted/updated, or None when rejected by cap.
        Existing keys always accept visit bumps and quality replaces.
        """
        room = _normalize_room_id(room_id)
        key = cell_key_v2(room, x, z, digest, tile_span=self.tile_span)
        q = tuple(int(v) for v in quality)
        while len(q) < 5:
            q = q + (0,)
        q5: Quality = (int(q[0]), int(q[1]), int(q[2]), int(q[3]), int(q[4]))

        existing = self.cells.get(key)
        if existing is not None:
            existing.visit_count += 1
            if quality_beats(q5, existing.quality):
                existing.quality = q5
                if record_id:
                    existing.record_id = str(record_id)
                if bundle_path is not None:
                    existing.bundle_path = bundle_path
            if meta:
                existing.meta.update(meta)
            return existing

        if self._room_cell_count(room) >= self.max_cells_per_room:
            return None

        cell = ArchiveCell(
            record_id=str(record_id or new_record_id()),
            cell_key=key,
            room_id=room,
            tile_bin=tile_bin(x, z, tile_span=self.tile_span),
            milestone_digest=str(digest),
            quality=q5,
            visit_count=1,
            bundle_path=bundle_path,
            meta=dict(meta or {}),
        )
        self.cells[key] = cell
        return cell

    def select_frontier(
        self,
        room_ids: frozenset[str] | set[str] | list[str] | None = None,
        k: int = 1,
        *,
        rng: random.Random | None = None,
    ) -> list[ArchiveCell]:
        """Pick under-visited cells (lowest visit_count; tie-break quality).

        Default ``room_ids`` is ``YAWN_PATH_ROOMS``.
        """
        rng = rng or random.Random()
        allowed_src = YAWN_PATH_ROOMS if room_ids is None else room_ids
        allowed = {_normalize_room_id(r) for r in allowed_src}
        pool = [c for c in self.cells.values() if c.room_id in allowed]
        if not pool:
            return []

        def sort_key(c: ArchiveCell) -> tuple[Any, ...]:
            # Prefer under-visited; among ties prefer *worse* quality (frontier).
            q = c.quality
            return (c.visit_count, tuple(-int(x) for x in q), c.cell_key)

        pool.sort(key=sort_key)
        if k >= len(pool):
            rng.shuffle(pool)
            return pool
        head = pool[: max(k * 4, k)]
        rng.shuffle(head)
        return head[:k]

    def stats(self) -> dict[str, Any]:
        rooms = {c.room_id for c in self.cells.values()}
        return {
            "version": ARCHIVE_VERSION,
            "cell_count": len(self.cells),
            "room_count": len(rooms),
            "rooms": sorted(rooms),
            "with_bundle": sum(1 for c in self.cells.values() if c.bundle_path),
            "max_cells_per_room": self.max_cells_per_room,
        }
