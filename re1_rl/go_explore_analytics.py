"""Go-Explore manifest analytics: semantic buckets, pose redundancy, coverage."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import urlopen

from re1_rl.milestone_digest import YAWN_PATH_ROOMS, parse_cell_key_v2

DEFAULT_PROBE_RECORD_IDS: frozenset[str] = frozenset({"biglive", "probe_live_001"})
DEFAULT_POSE_WARN_THRESHOLD = 8


@dataclass(frozen=True)
class ManifestCellRow:
    record_id: str
    cell_key: str
    room_id: str
    tile_x: int
    tile_z: int
    milestone_digest: str
    quality: tuple[int, ...]
    bytes: int

    @property
    def tile(self) -> tuple[int, int]:
        return (self.tile_x, self.tile_z)

    @property
    def digest_short(self) -> str:
        """Compact digest label for tables (first/last tokens)."""
        d = self.milestone_digest
        if len(d) <= 48:
            return d
        return d[:22] + "..." + d[-18:]


@dataclass
class SemanticBucket:
    room_id: str
    milestone_digest: str
    cells: list[ManifestCellRow] = field(default_factory=list)

    @property
    def pose_count(self) -> int:
        return len(self.cells)

    @property
    def unique_tiles(self) -> set[tuple[int, int]]:
        return {c.tile for c in self.cells}

    @property
    def bytes_total(self) -> int:
        return sum(c.bytes for c in self.cells)

    @property
    def quality_best(self) -> tuple[int, ...] | None:
        if not self.cells:
            return None
        return max(c.quality for c in self.cells)


@dataclass
class MultiDigestTile:
    room_id: str
    tile_x: int
    tile_z: int
    digests: list[str]
    record_ids: list[str]


@dataclass
class ManifestAnalyticsReport:
    archive_version: int | None
    source: str
    cells_total: int
    cells_real: int
    bytes_total: int
    rooms_seen: list[str]
    yawn_rooms_seen: list[str]
    yawn_rooms_missing: list[str]
    unique_cell_keys: int
    unique_tiles: int
    buckets: list[SemanticBucket]
    multi_digest_tiles: list[MultiDigestTile]
    pose_warn_threshold: int
    excluded_record_ids: list[str]

    @property
    def buckets_over_pose_threshold(self) -> list[SemanticBucket]:
        t = self.pose_warn_threshold
        return sorted(
            [b for b in self.buckets if b.pose_count > t],
            key=lambda b: (-b.pose_count, b.room_id, b.milestone_digest),
        )

    @property
    def avg_bytes_per_cell(self) -> float:
        if self.cells_real <= 0:
            return 0.0
        return self.bytes_total / self.cells_real


def _as_quality(raw: Any) -> tuple[int, ...]:
    from re1_rl.go_explore_archive import normalize_quality

    if not isinstance(raw, (list, tuple)) or len(raw) < 5:
        return normalize_quality(None)
    return normalize_quality(raw)


def manifest_row_to_cell(row: dict[str, Any]) -> ManifestCellRow | None:
    """Parse one HTTP/local manifest row into a normalized cell."""
    if not isinstance(row, dict):
        return None
    cell_key = str(row.get("cell_key") or "").strip()
    if not cell_key.startswith("v2|"):
        return None
    try:
        parsed = parse_cell_key_v2(cell_key)
    except ValueError:
        return None
    record_id = str(row.get("record_id") or "").strip()
    if not record_id:
        return None
    room_id = str(row.get("room_id") or parsed["room_id"]).strip().upper()
    return ManifestCellRow(
        record_id=record_id,
        cell_key=cell_key,
        room_id=room_id,
        tile_x=int(parsed["tile_x"]),
        tile_z=int(parsed["tile_z"]),
        milestone_digest=str(parsed["milestone_digest"]),
        quality=_as_quality(row.get("quality")),
        bytes=max(0, int(row.get("bytes") or 0)),
    )


def load_manifest_dict(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"manifest root must be object: {p}")
    return data


def load_manifest_from_archive(archive_path: Path | str) -> dict[str, Any]:
    """Build manifest-shaped dict from canonical ``archive.json``."""
    from re1_rl.go_explore_archive import GoExploreArchive
    from re1_rl.go_explore_merge import GoExploreMerge

    ap = Path(archive_path)
    merge = GoExploreMerge(ap)
    return merge.build_manifest(since_version=0)


def load_manifest_from_http(base_url: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/go_explore/manifest"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except URLError as exc:
        raise ConnectionError(f"GET {url} failed: {exc}") from exc
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"manifest root must be object from {url}")
    return data


def normalize_manifest_cells(
    manifest: dict[str, Any],
    *,
    exclude_record_ids: Iterable[str] | None = None,
) -> tuple[list[ManifestCellRow], list[ManifestCellRow], int | None]:
    """Return (all_cells, real_cells, archive_version)."""
    excluded = {str(x) for x in (exclude_record_ids or DEFAULT_PROBE_RECORD_IDS)}
    version_raw = manifest.get("archive_version")
    version = int(version_raw) if version_raw is not None else None
    all_cells: list[ManifestCellRow] = []
    for row in manifest.get("cells") or []:
        cell = manifest_row_to_cell(row)
        if cell is None:
            continue
        all_cells.append(cell)
    real = [c for c in all_cells if c.record_id not in excluded]
    return all_cells, real, version


def analyze_manifest(
    manifest: dict[str, Any],
    *,
    source: str = "manifest",
    exclude_record_ids: Iterable[str] | None = None,
    pose_warn_threshold: int = DEFAULT_POSE_WARN_THRESHOLD,
    yawn_rooms: frozenset[str] | None = None,
) -> ManifestAnalyticsReport:
    """Group cells by ``(room_id, milestone_digest)`` and flag pose redundancy."""
    excluded = {str(x) for x in (exclude_record_ids or DEFAULT_PROBE_RECORD_IDS)}
    yawn = yawn_rooms if yawn_rooms is not None else YAWN_PATH_ROOMS
    all_cells, real_cells, version = normalize_manifest_cells(
        manifest, exclude_record_ids=excluded
    )

    bucket_map: dict[tuple[str, str], SemanticBucket] = {}
    for cell in real_cells:
        key = (cell.room_id, cell.milestone_digest)
        bucket = bucket_map.get(key)
        if bucket is None:
            bucket = SemanticBucket(room_id=cell.room_id, milestone_digest=cell.milestone_digest)
            bucket_map[key] = bucket
        bucket.cells.append(cell)

    buckets = sorted(
        bucket_map.values(),
        key=lambda b: (-b.pose_count, b.room_id, b.milestone_digest),
    )

    tile_digest: dict[tuple[str, int, int], dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for cell in real_cells:
        tile_digest[(cell.room_id, cell.tile_x, cell.tile_z)][cell.milestone_digest].append(
            cell.record_id
        )

    multi: list[MultiDigestTile] = []
    for (room_id, tx, tz), digest_map in sorted(tile_digest.items()):
        if len(digest_map) <= 1:
            continue
        digests = sorted(digest_map.keys())
        record_ids = [rid for d in digests for rid in digest_map[d]]
        multi.append(
            MultiDigestTile(
                room_id=room_id,
                tile_x=tx,
                tile_z=tz,
                digests=digests,
                record_ids=record_ids,
            )
        )

    rooms_seen = sorted({c.room_id for c in real_cells})
    yawn_seen = sorted(set(rooms_seen) & {r.upper() for r in yawn})
    yawn_missing = sorted({r.upper() for r in yawn} - set(yawn_seen))

    return ManifestAnalyticsReport(
        archive_version=version,
        source=source,
        cells_total=len(all_cells),
        cells_real=len(real_cells),
        bytes_total=sum(c.bytes for c in real_cells),
        rooms_seen=rooms_seen,
        yawn_rooms_seen=yawn_seen,
        yawn_rooms_missing=yawn_missing,
        unique_cell_keys=len({c.cell_key for c in real_cells}),
        unique_tiles=len({(c.room_id, c.tile_x, c.tile_z) for c in real_cells}),
        buckets=buckets,
        multi_digest_tiles=multi,
        pose_warn_threshold=int(pose_warn_threshold),
        excluded_record_ids=sorted(excluded),
    )


def report_to_dict(report: ManifestAnalyticsReport) -> dict[str, Any]:
    """JSON-serializable summary for scripts and dashboards."""
    by_room = Counter(c.room_id for b in report.buckets for c in b.cells)
    return {
        "source": report.source,
        "archive_version": report.archive_version,
        "cells_total": report.cells_total,
        "cells_real": report.cells_real,
        "excluded_record_ids": report.excluded_record_ids,
        "bytes_total": report.bytes_total,
        "avg_bytes_per_cell": round(report.avg_bytes_per_cell, 1),
        "unique_cell_keys": report.unique_cell_keys,
        "unique_tiles": report.unique_tiles,
        "rooms_seen": report.rooms_seen,
        "yawn_rooms_seen": report.yawn_rooms_seen,
        "yawn_rooms_missing": report.yawn_rooms_missing,
        "yawn_coverage_pct": round(
            100.0 * len(report.yawn_rooms_seen) / max(1, len(YAWN_PATH_ROOMS)),
            1,
        ),
        "pose_warn_threshold": report.pose_warn_threshold,
        "buckets_over_threshold": [
            {
                "room_id": b.room_id,
                "milestone_digest": b.milestone_digest,
                "pose_count": b.pose_count,
                "unique_tiles": len(b.unique_tiles),
                "bytes_total": b.bytes_total,
                "quality_best": list(b.quality_best or ()),
                "tiles": sorted(b.unique_tiles),
            }
            for b in report.buckets_over_pose_threshold
        ],
        "by_room": dict(sorted(by_room.items())),
        "semantic_buckets": [
            {
                "room_id": b.room_id,
                "milestone_digest": b.milestone_digest,
                "pose_count": b.pose_count,
                "unique_tiles": len(b.unique_tiles),
                "bytes_total": b.bytes_total,
                "quality_best": list(b.quality_best or ()),
            }
            for b in report.buckets
        ],
        "multi_digest_tiles": [
            {
                "room_id": m.room_id,
                "tile": [m.tile_x, m.tile_z],
                "digests": m.digests,
                "n_digests": len(m.digests),
            }
            for m in report.multi_digest_tiles
        ],
    }


def format_report_text(report: ManifestAnalyticsReport) -> str:
    """Human-readable report for terminal / logs."""
    lines: list[str] = []
    mb = report.bytes_total / 1_000_000.0
    avg_kb = report.avg_bytes_per_cell / 1024.0
    yawn_n = len(YAWN_PATH_ROOMS)
    yawn_hit = len(report.yawn_rooms_seen)

    lines.append("Go-Explore manifest analytics")
    lines.append(f"  source:           {report.source}")
    if report.archive_version is not None:
        lines.append(f"  archive_version:  {report.archive_version}")
    lines.append(
        f"  cells:            {report.cells_real} real "
        f"({report.cells_total} total incl. probes)"
    )
    if report.excluded_record_ids:
        lines.append(f"  excluded probes:  {', '.join(report.excluded_record_ids)}")
    lines.append(f"  storage:          {mb:.2f} MB total ({avg_kb:.0f} KB/cell avg)")
    lines.append(
        f"  keys/tiles:       {report.unique_cell_keys} unique cell_keys, "
        f"{report.unique_tiles} unique (room,tile) pairs"
    )
    lines.append(
        f"  Yawn coverage:    {yawn_hit}/{yawn_n} rooms "
        f"({100.0 * yawn_hit / max(1, yawn_n):.0f}%)"
    )
    if report.yawn_rooms_missing:
        lines.append(f"  Yawn missing:     {', '.join(report.yawn_rooms_missing)}")

    by_room = Counter(c.room_id for b in report.buckets for c in b.cells)
    lines.append("")
    lines.append("Cells by room:")
    for room, count in by_room.most_common():
        lines.append(f"  {room}: {count}")

    over = report.buckets_over_pose_threshold
    lines.append("")
    lines.append(
        f"Semantic buckets with >{report.pose_warn_threshold} poses "
        f"({len(over)} flagged):"
    )
    if not over:
        lines.append("  (none)")
    else:
        for b in over:
            digest = b.milestone_digest
            if len(digest) > 56:
                digest = digest[:53] + "..."
            tiles = sorted(b.unique_tiles)
            tile_preview = ", ".join(f"({x},{z})" for x, z in tiles[:6])
            if len(tiles) > 6:
                tile_preview += f", +{len(tiles) - 6} more"
            lines.append(
                f"  {b.room_id} | {digest}\n"
                f"    poses={b.pose_count} tiles={len(tiles)} "
                f"bytes={b.bytes_total / 1_000_000:.2f}MB "
                f"best_q={b.quality_best}\n"
                f"    tiles: {tile_preview}"
            )

    lines.append("")
    lines.append("Top semantic buckets (by pose count):")
    for b in report.buckets[:12]:
        digest = b.cells[0].digest_short if b.cells else b.milestone_digest
        lines.append(
            f"  {b.pose_count:3d}  {b.room_id}  {digest}  "
            f"({len(b.unique_tiles)} tiles, {b.bytes_total // 1024} KB)"
        )

    if report.multi_digest_tiles:
        lines.append("")
        lines.append(
            f"Same-tile / different-digest ({len(report.multi_digest_tiles)} tiles — "
            "likely useful, not redundant):"
        )
        for m in report.multi_digest_tiles[:10]:
            ds = "; ".join(
                (d[:40] + "...") if len(d) > 40 else d for d in m.digests
            )
            lines.append(
                f"  {m.room_id} tile=({m.tile_x},{m.tile_z}) "
                f"n_digests={len(m.digests)}: {ds}"
            )
        if len(report.multi_digest_tiles) > 10:
            lines.append(f"  … +{len(report.multi_digest_tiles) - 10} more")

    return "\n".join(lines)
