"""Learner-authoritative Yawn rails cell sync (fleet HTTP).

Bounded checkpoint table (``cp00``..``cpNN``), one row per ``checkpoint_index``.
Transport mirrors Go-Explore (rollout proposals → learner merge → worker poll)
but does not share ``GoExploreMerge`` semantics.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any

from re1_rl.go_explore_archive import quality_beats
from re1_rl.go_explore_merge import (
    CELL_META_NAME,
    CELL_SIDECAR_NAME,
    CELL_STATE_NAME,
    make_cell_bundle_zip,
)

DEFAULT_YAWN_RAILS_REL = "states/yawn_rails"
STORE_FILENAME = "store.json"
MANIFEST_FILENAME = "manifest.json"
_ROOT_ENV = "RE1_YAWN_RAILS_ROOT"
_SYNC_ENV = "RE1_YAWN_RAILS_SYNC"


def yawn_rails_sync_enabled() -> bool:
    """Cross-machine yawn mirror + local capture; ``RE1_YAWN_RAILS_SYNC=0`` freezes."""
    raw = os.environ.get(_SYNC_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def yawn_rails_root(project_root: Path | str | None = None) -> Path:
    """Absolute path to the Yawn rails cell store root."""
    override = os.environ.get(_ROOT_ENV, "").strip()
    if override:
        p = Path(override)
        if not p.is_absolute():
            base = Path(project_root) if project_root is not None else Path.cwd()
            p = base / p
        return p.resolve()
    base = Path(project_root) if project_root is not None else Path.cwd()
    return (base / DEFAULT_YAWN_RAILS_REL).resolve()


def cell_dir_name(checkpoint_index: int) -> str:
    return f"cp{int(checkpoint_index):02d}"


def cell_slot_dir(root: Path | str, checkpoint_index: int) -> Path:
    return Path(root) / "cells" / cell_dir_name(checkpoint_index)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _as_quality(raw: Any) -> tuple[int, int, int, int, int, int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 5:
        return None
    try:
        from re1_rl.go_explore_archive import normalize_quality

        return normalize_quality(raw)
    except (TypeError, ValueError):
        return None


def extract_yawn_rails_proposals(
    infos: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Pull ``yawn_rails_capture`` lists/dicts out of rollout episode infos."""
    out: list[dict[str, Any]] = []
    for info in infos or []:
        if not isinstance(info, dict):
            continue
        caps = info.get("yawn_rails_capture")
        if caps is None:
            continue
        if isinstance(caps, dict):
            out.append(caps)
        elif isinstance(caps, list):
            for row in caps:
                if isinstance(row, dict):
                    out.append(row)
    return out


def pack_cell_bundle(
    *,
    state_bytes: bytes,
    sidecar: dict[str, Any] | bytes | str,
    meta: dict[str, Any] | None = None,
) -> bytes:
    """Zip ``cell.State`` + ``cell.sidecar.json`` (+ optional meta)."""
    if isinstance(sidecar, (bytes, bytearray)):
        side_obj = json.loads(bytes(sidecar).decode("utf-8"))
    elif isinstance(sidecar, str):
        side_obj = json.loads(sidecar)
    else:
        side_obj = dict(sidecar)
    return make_cell_bundle_zip(
        state_bytes=state_bytes,
        sidecar=side_obj,
        meta=meta,
    )


def build_capture_proposal(
    *,
    route_id: str,
    checkpoint_index: int,
    checkpoint_id: str,
    room_id: str,
    quality: tuple[int, ...] | list[int],
    state_path: Path,
    sidecar_path: Path,
    worker_id: str | None = None,
    capacity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pack a local cell capture into a rollout proposal dict."""
    state_bytes = Path(state_path).read_bytes()
    side_text = Path(sidecar_path).read_text(encoding="utf-8")
    sidecar = json.loads(side_text)
    from re1_rl.go_explore_archive import normalize_quality

    q = list(normalize_quality(quality))
    while len(q) < 5:
        q.append(0)
    meta = {
        "route_id": str(route_id),
        "checkpoint_index": int(checkpoint_index),
        "checkpoint_id": str(checkpoint_id),
        "room_id": str(room_id),
        "quality": q,
    }
    if worker_id:
        meta["worker_id"] = str(worker_id)
    capacity_meta = {
        key: (capacity or {}).get(key)
        for key in (
            "inventory_free_slots",
            "next_checkpoint_id",
            "next_slots_needed",
            "inventory_feasible",
            "captured_in_box_room",
        )
        if key in (capacity or {})
    }
    meta.update(capacity_meta)
    blob = pack_cell_bundle(state_bytes=state_bytes, sidecar=sidecar, meta=meta)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        state_sha = _sha256_bytes(zf.read(CELL_STATE_NAME))
        side_sha = _sha256_bytes(zf.read(CELL_SIDECAR_NAME))
    return {
        "route_id": str(route_id),
        "checkpoint_index": int(checkpoint_index),
        "checkpoint_id": str(checkpoint_id),
        "room_id": str(room_id),
        "quality": q,
        "bundle_b64": base64.b64encode(blob).decode("ascii"),
        "bundle_sha256": _sha256_bytes(blob),
        "state_sha256": state_sha,
        "sidecar_sha256": side_sha,
        "bytes": len(blob),
        "worker_id": worker_id,
        "meta": meta,
        **capacity_meta,
    }


class YawnRailsCellStore:
    """Canonical learner store: one bundle per checkpoint index."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else yawn_rails_root()
        self.archive_version = 0
        self.route_id: str | None = None
        self.cells: dict[int, dict[str, Any]] = {}
        self.accepted = 0
        self.rejected = 0
        self._lock = threading.Lock()
        self._load()

    @property
    def store_path(self) -> Path:
        return self.root / STORE_FILENAME

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    def _load(self) -> None:
        path = self.store_path
        if not path.is_file():
            # Bootstrap from curriculum-style manifest if present.
            self._load_manifest_fallback()
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._load_manifest_fallback()
            return
        self.archive_version = int(raw.get("archive_version", 0) or 0)
        self.route_id = str(raw.get("route_id") or "") or None
        cells: dict[int, dict[str, Any]] = {}
        for key, row in (raw.get("cells") or {}).items():
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row.get("checkpoint_index", key))
            except (TypeError, ValueError):
                continue
            cells[idx] = dict(row)
            cells[idx]["checkpoint_index"] = idx
        self.cells = cells

    def _load_manifest_fallback(self) -> None:
        path = self.manifest_path
        if not path.is_file():
            self.archive_version = 0
            self.cells = {}
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.archive_version = 0
            self.cells = {}
            return
        self.archive_version = int(raw.get("archive_version", 0) or 0)
        self.route_id = str(raw.get("route_id") or "") or None
        cells: dict[int, dict[str, Any]] = {}
        for row in raw.get("cells") or []:
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row["checkpoint_index"])
            except (KeyError, TypeError, ValueError):
                continue
            cells[idx] = dict(row)
        self.cells = cells

    def _persist_unlocked(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "archive_version": int(self.archive_version),
            "route_id": self.route_id,
            "cells": {
                str(idx): self.cells[idx]
                for idx in sorted(self.cells)
            },
        }
        fd, tmp_name = tempfile.mkstemp(
            prefix="yawn_rails_store_",
            suffix=".json",
            dir=str(self.root),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_name, self.store_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self._write_sampling_manifest_unlocked()

    def _write_sampling_manifest_unlocked(self) -> None:
        """Curriculum-facing manifest consumed by ``sample_one_leg_options``."""
        cells = []
        for idx in sorted(self.cells):
            row = dict(self.cells[idx])
            row["state_path"] = (
                f"{DEFAULT_YAWN_RAILS_REL}/cells/{cell_dir_name(idx)}/{CELL_STATE_NAME}"
            )
            row["sidecar_path"] = (
                f"{DEFAULT_YAWN_RAILS_REL}/cells/{cell_dir_name(idx)}/{CELL_SIDECAR_NAME}"
            )
            cells.append(row)
        manifest = {
            "schema_version": 1,
            "route_id": self.route_id,
            "archive_version": int(self.archive_version),
            "cells": cells,
        }
        tmp = self.manifest_path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.manifest_path)

    def ingest_proposals(self, proposals: list[dict[str, Any]]) -> list[str]:
        """Admit/replace cells. Returns accepted ``cpNN`` ids."""
        accepted: list[str] = []
        if not proposals:
            return accepted
        with self._lock:
            self._load()
            for prop in proposals:
                cid = self._ingest_one_unlocked(prop)
                if cid is not None:
                    accepted.append(cid)
            if accepted:
                self.archive_version += 1
                self._persist_unlocked()
        return accepted

    def _ingest_one_unlocked(self, prop: dict[str, Any]) -> str | None:
        try:
            idx = int(prop["checkpoint_index"])
        except (KeyError, TypeError, ValueError):
            self.rejected += 1
            return None
        if idx < 0:
            self.rejected += 1
            return None
        quality = _as_quality(prop.get("quality"))
        if quality is None:
            self.rejected += 1
            return None
        route_id = str(prop.get("route_id") or "") or None
        if self.route_id and route_id and route_id != self.route_id:
            self.rejected += 1
            return None
        if route_id and not self.route_id:
            self.route_id = route_id
        if prop.get("inventory_feasible") is False:
            self.rejected += 1
            return None

        existing = self.cells.get(idx)
        if existing is not None:
            old_q = _as_quality(existing.get("quality"))
            capacity_upgrade = (
                "inventory_feasible" not in existing
                and prop.get("inventory_feasible") is True
            )
            if (
                not capacity_upgrade
                and old_q is not None
                and not quality_beats(quality, old_q)
            ):
                self.rejected += 1
                return None
            from re1_rl.go_explore_capture import quality_replace_significant

            if (
                not capacity_upgrade
                and old_q is not None
                and not quality_replace_significant(quality, old_q)
            ):
                self.rejected += 1
                return None

        bundle_bytes = self._decode_bundle(prop)
        if bundle_bytes is None:
            self.rejected += 1
            return None
        ok, _reason = self._validate_bundle_bytes(bundle_bytes, prop)
        if not ok:
            self.rejected += 1
            return None

        bundle_sha = _sha256_bytes(bundle_bytes)
        self._write_bundle_unlocked(idx, bundle_bytes, prop, bundle_sha)
        row = {
            "checkpoint_index": idx,
            "checkpoint_id": str(prop.get("checkpoint_id") or ""),
            "room_id": str(prop.get("room_id") or ""),
            "quality": list(quality),
            "bundle_sha256": bundle_sha,
            "bytes": len(bundle_bytes),
            "state_path": (
                f"{DEFAULT_YAWN_RAILS_REL}/cells/{cell_dir_name(idx)}/{CELL_STATE_NAME}"
            ),
            "sidecar_path": (
                f"{DEFAULT_YAWN_RAILS_REL}/cells/{cell_dir_name(idx)}/{CELL_SIDECAR_NAME}"
            ),
        }
        for key in (
            "inventory_free_slots",
            "next_checkpoint_id",
            "next_slots_needed",
            "inventory_feasible",
            "captured_in_box_room",
        ):
            if key in prop:
                row[key] = prop[key]
        if prop.get("worker_id"):
            row["worker_id"] = str(prop["worker_id"])
        self.cells[idx] = row
        self.accepted += 1
        return cell_dir_name(idx)

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
        checkpoint_index: int,
        bundle_bytes: bytes,
        prop: dict[str, Any],
        bundle_sha: str,
    ) -> None:
        dest = cell_slot_dir(self.root, checkpoint_index)
        dest.parent.mkdir(parents=True, exist_ok=True)
        incoming = dest.parent / f".incoming_{cell_dir_name(checkpoint_index)}"
        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)
        incoming.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as zf:
                zf.extractall(incoming)
            meta = {
                "checkpoint_index": int(checkpoint_index),
                "checkpoint_id": prop.get("checkpoint_id"),
                "room_id": prop.get("room_id"),
                "quality": list(prop.get("quality") or []),
                "bundle_sha256": bundle_sha,
                "state_sha256": prop.get("state_sha256"),
                "sidecar_sha256": prop.get("sidecar_sha256"),
                "bytes": len(bundle_bytes),
                "route_id": prop.get("route_id") or self.route_id,
            }
            for key in (
                "inventory_free_slots",
                "next_checkpoint_id",
                "next_slots_needed",
                "inventory_feasible",
                "captured_in_box_room",
            ):
                if key in prop:
                    meta[key] = prop[key]
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

    def build_manifest(self, *, since_version: int = 0) -> dict[str, Any]:
        with self._lock:
            self._load()
            ver = int(self.archive_version)
            cell_count = len(self.cells)
            if int(since_version) >= ver:
                return {
                    "archive_version": ver,
                    "route_id": self.route_id,
                    "cells": [],
                    "cell_count": cell_count,
                }
            cells_out = []
            for idx in sorted(self.cells):
                row = self.cells[idx]
                cells_out.append(
                    {
                        "checkpoint_index": idx,
                        "checkpoint_id": row.get("checkpoint_id", ""),
                        "room_id": row.get("room_id", ""),
                        "quality": list(row.get("quality") or []),
                        "bundle_sha256": str(row.get("bundle_sha256") or ""),
                        "bytes": int(row.get("bytes") or 0),
                        "cell_id": cell_dir_name(idx),
                        "state_path": row.get("state_path"),
                        "sidecar_path": row.get("sidecar_path"),
                        **{
                            key: row[key]
                            for key in (
                                "inventory_free_slots",
                                "next_checkpoint_id",
                                "next_slots_needed",
                                "inventory_feasible",
                                "captured_in_box_room",
                            )
                            if key in row
                        },
                    }
                )
            return {
                "archive_version": ver,
                "route_id": self.route_id,
                "cells": cells_out,
                "cell_count": len(cells_out),
            }

    def pack_bundle_zip(self, cell_id: str) -> bytes | None:
        """Zip bytes for ``GET /yawn_rails/bundle/<cpNN>``."""
        cid = str(cell_id).strip()
        if not cid.startswith("cp") or "/" in cid or "\\" in cid or ".." in cid:
            return None
        try:
            idx = int(cid[2:], 10)
        except ValueError:
            return None
        d = cell_slot_dir(self.root, idx)
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


def yawn_rails_store_from_env(
    project_root: Path | str | None = None,
) -> YawnRailsCellStore:
    """Always-on store under ``states/yawn_rails`` (or ``RE1_YAWN_RAILS_ROOT``)."""
    return YawnRailsCellStore(yawn_rails_root(project_root))
