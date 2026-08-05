"""Go-Explore semantic bucket admission: pose caps and eviction scoring."""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from re1_rl.go_explore_archive import Quality, normalize_quality, quality_beats
from re1_rl.milestone_digest import parse_cell_key_v2

_POSE_CAP_ENV = "RE1_GO_MAX_POSES_PER_BUCKET"
_MAX_ARCHIVE_CELLS_ENV = "RE1_GO_MAX_ARCHIVE_CELLS"
_POSE_EVICT_ENV = "RE1_GO_POSE_EVICT"
_DEFAULT_POSE_CAP = 1
_DEFAULT_MAX_ARCHIVE_CELLS = 8000

SemanticKey = tuple[str, str]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def pose_cap() -> int:
    """Max poses (cell keys) per ``(room_id, milestone_digest)`` bucket."""
    return max(1, _env_int(_POSE_CAP_ENV, _DEFAULT_POSE_CAP))


def max_poses_per_bucket() -> int:
    """Alias for :func:`pose_cap`."""
    return pose_cap()


def max_archive_cells() -> int:
    """Hard cap on total archive cells (fleet safety ceiling)."""
    return max(1, _env_int(_MAX_ARCHIVE_CELLS_ENV, _DEFAULT_MAX_ARCHIVE_CELLS))


def pose_evict_enabled() -> bool:
    """When true, full buckets may admit if quality beats the weakest incumbent."""
    raw = os.environ.get(_POSE_EVICT_ENV, "1").strip()
    if not raw:
        return True
    return raw in {"1", "true", "TRUE", "yes", "YES"}


def semantic_bucket_key(room_id: str | int, milestone_digest: str) -> SemanticKey:
    room = str(room_id).strip()
    if room.lower().startswith("0x"):
        room = room[2:]
    return (room.upper(), str(milestone_digest))


def semantic_bucket_key_from_cell_key(cell_key: str) -> SemanticKey:
    parsed = parse_cell_key_v2(cell_key)
    return semantic_bucket_key(parsed["room_id"], parsed["milestone_digest"])


def _as_quality(raw: Any) -> Quality:
    if isinstance(raw, dict):
        raw = raw.get("quality")
    elif hasattr(raw, "quality"):
        raw = raw.quality
    if not isinstance(raw, (list, tuple)) or len(raw) < 5:
        return normalize_quality(None)
    return normalize_quality(raw)


def _row_as_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "to_json"):
        return row.to_json()
    return {
        "record_id": getattr(row, "record_id", ""),
        "cell_key": getattr(row, "cell_key", ""),
        "room_id": getattr(row, "room_id", ""),
        "quality": list(getattr(row, "quality", (0, 0, 0, 0, 0, 0))),
        "visit_count": int(getattr(row, "visit_count", 0) or 0),
        "tile_bin": list(getattr(row, "tile_bin", (0, 0))),
        "meta": dict(getattr(row, "meta", {}) or {}),
    }


def _row_tile(row: Any) -> tuple[int, int]:
    data = _row_as_dict(row)
    tb = data.get("tile_bin")
    if isinstance(tb, (list, tuple)) and len(tb) >= 2:
        return (int(tb[0]), int(tb[1]))
    key = str(data.get("cell_key") or "")
    if key.startswith("v2|"):
        try:
            parsed = parse_cell_key_v2(key)
            return (int(parsed["tile_x"]), int(parsed["tile_z"]))
        except ValueError:
            pass
    return (0, 0)


def _row_visit_count(row: Any) -> int:
    data = _row_as_dict(row)
    return int(data.get("visit_count", 0) or 0)


def _row_captured_at(row: Any) -> str:
    data = _row_as_dict(row)
    raw = data.get("captured_at_iso")
    if raw:
        return str(raw)
    meta = data.get("meta") or {}
    if isinstance(meta, dict) and meta.get("captured_at_iso"):
        return str(meta["captured_at_iso"])
    return ""


def _tile_centroid(rows: Sequence[Any]) -> tuple[float, float]:
    if not rows:
        return (0.0, 0.0)
    tiles = [_row_tile(r) for r in rows]
    cx = sum(t[0] for t in tiles) / len(tiles)
    cz = sum(t[1] for t in tiles) / len(tiles)
    return (cx, cz)


def eviction_sort_key(row: Any, centroid: tuple[float, float] | None = None) -> tuple[Any, ...]:
    """Lower key = evict first (weak quality, central tile, low visits, older)."""
    q = _as_quality(row)
    tile = _row_tile(row)
    if centroid is None:
        centroid = (float(tile[0]), float(tile[1]))
    dx = float(tile[0]) - float(centroid[0])
    dz = float(tile[1]) - float(centroid[1])
    dist_sq = dx * dx + dz * dz
    return (q, dist_sq, _row_visit_count(row), _row_captured_at(row))


def weakest_incumbent(rows: Sequence[Any]) -> Any | None:
    """Row to evict first from a semantic bucket (or global pool)."""
    if not rows:
        return None
    centroid = _tile_centroid(rows)
    return min(rows, key=lambda r: eviction_sort_key(r, centroid))


def bucket_champion(rows: Sequence[Any]) -> Any | None:
    """Strongest row in a semantic bucket (inverse of :func:`weakest_incumbent`)."""
    if not rows:
        return None
    centroid = _tile_centroid(rows)
    return max(rows, key=lambda r: eviction_sort_key(r, centroid))


def semantic_replace_allowed(
    new_q: Quality | Sequence[int],
    old_q: Quality | Sequence[int],
) -> bool:
    """True when ``new_q`` may replace a different-tile incumbent in the same bucket."""
    from re1_rl.go_explore_capture import quality_replace_significant

    o = _as_quality(old_q)
    n = _as_quality(new_q)
    if not quality_beats(n, o):
        return False
    return quality_replace_significant(n, o)


def keep_best_rows(rows: Sequence[Any], n: int) -> list[Any]:
    """Keep the ``n`` strongest rows by inverse eviction score."""
    if n <= 0:
        return []
    if len(rows) <= n:
        return list(rows)
    centroid = _tile_centroid(rows)
    ranked = sorted(rows, key=lambda r: eviction_sort_key(r, centroid), reverse=True)
    return ranked[:n]


def bucket_pose_count(
    index: Mapping[SemanticKey, Sequence[Any]],
    semantic_key: SemanticKey,
) -> int:
    rows = index.get(semantic_key) or ()
    return len(rows)


def _iter_manifest_rows(
    manifest_or_rows: Mapping[str, Any] | Iterable[Any] | None,
) -> list[Any]:
    if manifest_or_rows is None:
        return []
    if isinstance(manifest_or_rows, Mapping):
        if "cells" in manifest_or_rows:
            return [r for r in (manifest_or_rows.get("cells") or []) if r is not None]
        # cell_key → row index (worker manifest_index)
        out: list[Any] = []
        for key, val in manifest_or_rows.items():
            if key in {"archive_version", "version", "cells"}:
                continue
            if isinstance(val, dict) and (val.get("cell_key") or val.get("record_id")):
                out.append(val)
        return out
    return [r for r in manifest_or_rows if r is not None]


def manifest_index_by_semantic_bucket(
    manifest_or_rows: Mapping[str, Any] | Iterable[Any] | None,
) -> dict[SemanticKey, list[dict[str, Any]]]:
    """Group manifest / archive rows by ``(room_id, milestone_digest)``."""
    index: dict[SemanticKey, list[dict[str, Any]]] = defaultdict(list)
    for row in _iter_manifest_rows(manifest_or_rows):
        data = _row_as_dict(row)
        key_s = str(data.get("cell_key") or "").strip()
        if key_s.startswith("v2|"):
            try:
                sk = semantic_bucket_key_from_cell_key(key_s)
            except ValueError:
                continue
        else:
            room = str(data.get("room_id") or "").strip()
            digest = str(data.get("milestone_digest") or "")
            if not room:
                continue
            sk = semantic_bucket_key(room, digest)
        index[sk].append(data)
    return dict(index)


def room_digest_count(
    room: str | int,
    *,
    manifest_index: Mapping[str, dict[str, Any]] | None = None,
    archive_cells: Iterable[Any] | None = None,
) -> int:
    """Distinct ``milestone_digest`` values already stored for ``room``."""
    room_u = str(room).strip()
    if room_u.lower().startswith("0x"):
        room_u = room_u[2:]
    room_u = room_u.upper()
    digests: set[str] = set()
    if manifest_index is not None:
        for row in manifest_index.values():
            if not isinstance(row, dict):
                continue
            rid = str(row.get("room_id") or "").strip().upper()
            if rid != room_u:
                continue
            digest = str(row.get("milestone_digest") or "").strip()
            if not digest:
                key_s = str(row.get("cell_key") or "")
                if key_s.startswith("v2|"):
                    try:
                        digest = str(parse_cell_key_v2(key_s)["milestone_digest"])
                    except ValueError:
                        continue
            if digest:
                digests.add(digest)
    if archive_cells is not None:
        for cell in archive_cells:
            data = _row_as_dict(cell)
            rid = str(data.get("room_id") or "").strip().upper()
            if rid != room_u:
                continue
            digest = str(data.get("milestone_digest") or "").strip()
            if not digest:
                key_s = str(data.get("cell_key") or "")
                if key_s.startswith("v2|"):
                    try:
                        digest = str(parse_cell_key_v2(key_s)["milestone_digest"])
                    except ValueError:
                        continue
            if digest:
                digests.add(digest)
    return len(digests)


def semantic_admission_allowed(
    room: str | int,
    digest: str,
    cell_key: str,
    quality: Sequence[int] | Quality,
    *,
    manifest_index: Mapping[str, dict[str, Any]] | None,
    semantic_index: Mapping[SemanticKey, Sequence[Any]] | None = None,
) -> bool:
    """Worker pre-filter: True if proposal may proceed to ``save_state``.

    Same ``cell_key`` delegates to existing quality-replace rules. New keys
    admit under pose cap, or when eviction is enabled and quality beats the
    weakest incumbent in the semantic bucket.
    """
    from re1_rl.go_explore_capture import quality_replace_significant

    q = _as_quality(quality)
    key = str(cell_key).strip()
    room_u = str(room).strip()
    if room_u.lower().startswith("0x"):
        room_u = room_u[2:]
    room_u = room_u.upper()

    if manifest_index is not None and key in manifest_index:
        existing = manifest_index[key]
        old_q = _as_quality(existing.get("quality") if isinstance(existing, dict) else existing)
        if not quality_beats(q, old_q):
            return False
        if not quality_replace_significant(q, old_q):
            return False
        return True

    if semantic_index is None:
        semantic_index = manifest_index_by_semantic_bucket(manifest_index)

    bucket = semantic_bucket_key(room_u, digest)
    rows = list(semantic_index.get(bucket) or ())
    # Ignore a stale same-key row if present under a different index path.
    rows = [r for r in rows if str(_row_as_dict(r).get("cell_key") or "") != key]

    if len(rows) < pose_cap():
        return True
    if not pose_evict_enabled():
        return False
    weak = weakest_incumbent(rows)
    if weak is None:
        return True
    return semantic_replace_allowed(q, _as_quality(weak))
