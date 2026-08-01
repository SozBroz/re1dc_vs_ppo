"""Learner-side Go-Explore proposal ingest (fleet HTTP merge)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from re1_rl.go_explore_archive import (
    ARCHIVE_VERSION,
    ArchiveCell,
    GoExploreArchive,
    archive_locked,
    new_record_id,
    quality_beats,
)
from re1_rl.go_explore_semantic import (
    max_archive_cells,
    pose_cap,
    pose_evict_enabled,
    semantic_bucket_key,
    weakest_incumbent,
)
from re1_rl.milestone_digest import parse_cell_key_v2

CELL_STATE_NAME = "cell.State"
CELL_SIDECAR_NAME = "cell.sidecar.json"
CELL_META_NAME = "meta.json"


def default_archive_path() -> Path:
    from re1_rl.go_explore_capture import resolve_archive_path

    return resolve_archive_path()


def go_explore_root(archive_path: Path | str) -> Path:
    """Directory holding archive.json + cells/."""
    p = Path(archive_path)
    return p.parent if p.suffix else p


def cells_dir(archive_path: Path | str) -> Path:
    return go_explore_root(archive_path) / "cells"


def _as_quality(raw: Any) -> tuple[int, int, int, int, int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 5:
        return None
    try:
        q = tuple(int(x) for x in raw[:5])
    except (TypeError, ValueError):
        return None
    return (int(q[0]), int(q[1]), int(q[2]), int(q[3]), int(q[4]))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_proposals_from_infos(infos: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Pull ``go_explore_capture`` lists/dicts out of rollout episode infos."""
    out: list[dict[str, Any]] = []
    for info in infos or []:
        if not isinstance(info, dict):
            continue
        caps = info.get("go_explore_capture")
        if caps is None:
            continue
        if isinstance(caps, dict):
            out.append(caps)
        elif isinstance(caps, list):
            for row in caps:
                if isinstance(row, dict):
                    out.append(row)
    return out


class GoExploreMerge:
    """Canonical archive writer: admit/replace cells from worker proposals."""

    def __init__(self, archive_path: Path | str | None = None) -> None:
        self.archive_path = Path(archive_path) if archive_path is not None else default_archive_path()
        self.archive_version = 0
        self.archive = GoExploreArchive(self.archive_path)
        self.rejected_semantic = 0
        self.evicted = 0
        self._load()

    def _load(self) -> None:
        if not self.archive_path.is_file():
            self.archive.cells = {}
            self.archive_version = 0
            return
        raw = json.loads(self.archive_path.read_text(encoding="utf-8"))
        self.archive_version = int(raw.get("archive_version", 0) or 0)
        self.archive.load()

    def _persist_unlocked(self) -> None:
        payload = {
            "version": ARCHIVE_VERSION,
            "archive_version": int(self.archive_version),
            "cells": {k: c.to_json() for k, c in sorted(self.archive.cells.items())},
        }
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix="archive_",
            suffix=".json",
            dir=str(self.archive_path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_name, self.archive_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def cell_dir(self, record_id: str) -> Path:
        return cells_dir(self.archive_path) / str(record_id)

    def ingest_proposals(self, proposals: list[dict[str, Any]]) -> list[str]:
        """Validate, admit/replace, write bundles. Returns accepted record_ids."""
        accepted: list[str] = []
        if not proposals:
            return accepted
        with archive_locked(self.archive_path, holder="go_explore_merge"):
            # Reload under lock so concurrent writers don't clobber.
            if self.archive_path.is_file():
                raw = json.loads(self.archive_path.read_text(encoding="utf-8"))
                self.archive_version = int(raw.get("archive_version", 0) or 0)
                self.archive.load()
            for prop in proposals:
                rid = self._ingest_one_unlocked(prop)
                if rid is not None:
                    accepted.append(rid)
            if accepted:
                self.archive_version += 1
                self._persist_unlocked()
        return accepted

    def _evict_cell_unlocked(self, cell: ArchiveCell, *, for_record_id: str) -> None:
        """Remove incumbent from archive + delete its bundle directory."""
        removed = self.archive.remove_cell(cell.record_id)
        if removed is None:
            # Fallback: drop by cell_key if record_id mismatch.
            self.archive.cells.pop(cell.cell_key, None)
            removed = cell
        old_dir = self.cell_dir(removed.record_id)
        if old_dir.is_dir():
            shutil.rmtree(old_dir, ignore_errors=True)
        self.evicted += 1
        print(
            f"go_explore evicted {removed.record_id} "
            f"bucket={removed.room_id}/{removed.milestone_digest} "
            f"for {for_record_id}",
            flush=True,
        )

    def _plan_new_key_evictions(
        self,
        *,
        room: str,
        digest: str,
        quality: tuple[int, int, int, int, int],
    ) -> list[ArchiveCell] | None:
        """Return cells to evict before admitting a new key, or None to reject."""
        bucket = semantic_bucket_key(room, digest)
        bucket_cells = list(self.archive.cells_by_semantic_bucket().get(bucket) or ())
        to_evict: list[ArchiveCell] = []

        if len(bucket_cells) >= pose_cap():
            if not pose_evict_enabled():
                self.rejected_semantic += 1
                return None
            weak = weakest_incumbent(bucket_cells)
            if weak is None or not quality_beats(quality, weak.quality):
                self.rejected_semantic += 1
                return None
            to_evict.append(weak)

        evict_ids = {c.record_id for c in to_evict}
        projected = len(self.archive.cells) - len(to_evict) + 1
        if projected > max_archive_cells():
            remaining = [
                c for c in self.archive.cells.values() if c.record_id not in evict_ids
            ]
            # Never delete the last cell of a room (coverage preserve).
            room_counts: dict[str, int] = {}
            for c in remaining:
                room_counts[c.room_id] = room_counts.get(c.room_id, 0) + 1
            evictable = [c for c in remaining if room_counts.get(c.room_id, 0) > 1]
            room_present = any(c.room_id == room for c in self.archive.cells.values())
            is_new_room = not room_present
            global_weak = weakest_incumbent(evictable) if evictable else None
            if global_weak is None:
                self.rejected_semantic += 1
                return None
            if not is_new_room and not quality_beats(quality, global_weak.quality):
                self.rejected_semantic += 1
                return None
            if global_weak.record_id not in evict_ids:
                to_evict.append(global_weak)

        room_cells = [
            c
            for c in self.archive.cells.values()
            if c.room_id == room and c.record_id not in evict_ids
        ]
        if len(room_cells) >= self.archive.max_cells_per_room:
            # Room full: replace weakest room cell when quality is meaningfully better
            # (same spirit as PB champion upgrade — avoid resource-starved traps).
            from re1_rl.go_explore_capture import quality_replace_significant

            weak_room = weakest_incumbent(room_cells)
            if (
                weak_room is None
                or not quality_beats(quality, weak_room.quality)
                or not quality_replace_significant(quality, weak_room.quality)
            ):
                self.rejected_semantic += 1
                return None
            if weak_room.record_id not in evict_ids:
                to_evict.append(weak_room)
        return to_evict

    def _ingest_one_unlocked(self, prop: dict[str, Any]) -> str | None:
        from re1_rl.go_explore_capture import (
            _disk_free_bytes,
            max_capture_bundle_bytes,
            min_free_bytes,
        )

        cell_key = str(prop.get("cell_key") or "").strip()
        if not cell_key.startswith("v2|"):
            return None
        try:
            parsed = parse_cell_key_v2(cell_key)
        except ValueError:
            return None
        quality = _as_quality(prop.get("quality"))
        if quality is None:
            return None
        record_id = str(prop.get("record_id") or "").strip() or new_record_id()

        from re1_rl.go_explore_capture import quality_replace_significant

        existing = self.archive.cells.get(cell_key)
        if existing is not None:
            if not quality_beats(quality, existing.quality) or not quality_replace_significant(
                quality, existing.quality
            ):
                existing.visit_count += 1
                return None

        room = str(parsed["room_id"])
        tb = tuple(parsed["tile_bin"])
        digest = str(parsed["milestone_digest"])

        to_evict: list[ArchiveCell] = []
        if existing is None:
            planned = self._plan_new_key_evictions(room=room, digest=digest, quality=quality)
            if planned is None:
                return None
            to_evict = planned

        bundle_bytes = self._decode_bundle(prop)
        if bundle_bytes is not None:
            if len(bundle_bytes) > max_capture_bundle_bytes():
                return None
            if _disk_free_bytes(go_explore_root(self.archive_path)) < min_free_bytes():
                return None
            ok, reason = self._validate_bundle_bytes(bundle_bytes, prop)
            if not ok:
                return None
            for victim in to_evict:
                self._evict_cell_unlocked(victim, for_record_id=record_id)
            bundle_sha = _sha256_bytes(bundle_bytes)
            rel_path = self._write_bundle_unlocked(record_id, bundle_bytes, prop, bundle_sha)
        else:
            # Metadata-only admit (shadow / tests); no on-disk State yet.
            for victim in to_evict:
                self._evict_cell_unlocked(victim, for_record_id=record_id)
            bundle_sha = str(prop.get("bundle_sha256") or "")
            rel_path = None

        meta = dict(prop.get("meta") or {})
        for k in ("worker_id", "captured_at_step", "state_sha256", "sidecar_sha256"):
            if k in prop and prop[k] is not None:
                meta[k] = prop[k]
        if prop.get("capture_reasons"):
            meta["capture_reasons"] = list(prop["capture_reasons"])
        if bundle_sha:
            meta["bundle_sha256"] = bundle_sha

        if existing is None:
            cell = ArchiveCell(
                record_id=record_id,
                cell_key=cell_key,
                room_id=room,
                tile_bin=(int(tb[0]), int(tb[1])),
                milestone_digest=digest,
                quality=quality,
                visit_count=1,
                bundle_path=rel_path,
                meta=meta,
            )
            self.archive.cells[cell_key] = cell
        else:
            # Drop previous bundle dir if record_id changes.
            old_id = existing.record_id
            existing.visit_count += 1
            existing.quality = quality
            existing.record_id = record_id
            existing.bundle_path = rel_path if rel_path is not None else existing.bundle_path
            existing.meta.update(meta)
            if old_id and old_id != record_id:
                old_dir = self.cell_dir(old_id)
                if old_dir.is_dir():
                    shutil.rmtree(old_dir, ignore_errors=True)

        return record_id

    def _decode_bundle(self, prop: dict[str, Any]) -> bytes | None:
        b64 = prop.get("bundle_b64")
        if not b64:
            return None
        try:
            return base64.b64decode(str(b64), validate=False)
        except (ValueError, TypeError):
            return None

    def _validate_bundle_bytes(
        self, data: bytes, prop: dict[str, Any]
    ) -> tuple[bool, str]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return False, "bad_zip"
        names = set(zf.namelist())
        if CELL_STATE_NAME not in names:
            return False, "missing_state"
        if CELL_SIDECAR_NAME not in names:
            return False, "missing_sidecar"
        state_bytes = zf.read(CELL_STATE_NAME)
        side_bytes = zf.read(CELL_SIDECAR_NAME)
        want_state = prop.get("state_sha256")
        if want_state and _sha256_bytes(state_bytes) != str(want_state):
            return False, "state_sha_mismatch"
        want_side = prop.get("sidecar_sha256")
        if want_side and _sha256_bytes(side_bytes) != str(want_side):
            return False, "sidecar_sha_mismatch"
        return True, "ok"

    def _write_bundle_unlocked(
        self,
        record_id: str,
        bundle_bytes: bytes,
        prop: dict[str, Any],
        bundle_sha: str,
    ) -> str:
        dest = self.cell_dir(record_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        incoming = dest.parent / f".incoming_{record_id}"
        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)
        incoming.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as zf:
                zf.extractall(incoming)
            meta = {
                "record_id": record_id,
                "cell_key": prop.get("cell_key"),
                "quality": list(prop.get("quality") or []),
                "bundle_sha256": bundle_sha,
                "state_sha256": prop.get("state_sha256"),
                "sidecar_sha256": prop.get("sidecar_sha256"),
                "bytes": len(bundle_bytes),
            }
            (incoming / CELL_META_NAME).write_text(
                json.dumps(meta, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            os.replace(str(incoming), str(dest))
        except Exception:
            shutil.rmtree(incoming, ignore_errors=True)
            raise
        # Relative path from archive root for portability.
        return f"cells/{record_id}"

    def build_manifest(self, *, since_version: int = 0) -> dict[str, Any]:
        """Catalog for workers. Empty ``cells`` when client is already current."""
        with archive_locked(self.archive_path, holder="go_explore_manifest"):
            if self.archive_path.is_file():
                raw = json.loads(self.archive_path.read_text(encoding="utf-8"))
                self.archive_version = int(raw.get("archive_version", 0) or 0)
                self.archive.load()
            ver = int(self.archive_version)
            cell_count = len(self.archive.cells)
            if int(since_version) >= ver:
                return {
                    "archive_version": ver,
                    "cells": [],
                    "cell_count": cell_count,
                }
            cells_out: list[dict[str, Any]] = []
            for cell in self.archive.cells.values():
                meta = cell.meta or {}
                bundle_sha = str(meta.get("bundle_sha256") or "")
                nbytes = meta.get("bytes")
                if nbytes is None and cell.bundle_path:
                    zpath = self.cell_dir(cell.record_id)
                    state_p = zpath / CELL_STATE_NAME
                    if state_p.is_file():
                        nbytes = state_p.stat().st_size
                cells_out.append(
                    {
                        "record_id": cell.record_id,
                        "cell_key": cell.cell_key,
                        "room_id": cell.room_id,
                        "quality": list(cell.quality),
                        "bundle_sha256": bundle_sha,
                        "bytes": int(nbytes or 0),
                    }
                )
            return {
                "archive_version": ver,
                "cells": cells_out,
                "cell_count": len(cells_out),
            }

    def pack_bundle_zip(self, record_id: str) -> bytes | None:
        """Zip bytes for ``GET /go_explore/bundle/<record_id>``."""
        d = self.cell_dir(record_id)
        state_p = d / CELL_STATE_NAME
        side_p = d / CELL_SIDECAR_NAME
        if not state_p.is_file() or not side_p.is_file():
            return None
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(state_p, CELL_STATE_NAME)
            zf.write(side_p, CELL_SIDECAR_NAME)
            meta_p = d / CELL_META_NAME
            if meta_p.is_file():
                zf.write(meta_p, CELL_META_NAME)
        return buf.getvalue()


def make_cell_bundle_zip(
    *,
    state_bytes: bytes,
    sidecar: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> bytes:
    """Helper for tests / capture: build a cell zip in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(CELL_STATE_NAME, state_bytes)
        zf.writestr(
            CELL_SIDECAR_NAME,
            json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        )
        if meta is not None:
            zf.writestr(
                CELL_META_NAME,
                json.dumps(meta, indent=2, sort_keys=True) + "\n",
            )
    return buf.getvalue()
