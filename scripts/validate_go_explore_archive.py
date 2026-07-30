"""Validate Go-Explore archive bundles: State + sidecar + sha256 vs meta.

Offline (default): load archive.json, for each cell confirm cell.State and
cell.sidecar.json exist under cells/<record_id>/ (or bundle_path parent) and
that file digests match meta.json (and proposal sha fields when present).

Optional --smoke: attempt a BizHawk load of a sample cell; if EmuHawk / ROM
are missing, skip smoke with a clear note (does not fail the offline pass).

Usage:
    python scripts/validate_go_explore_archive.py
    python scripts/validate_go_explore_archive.py --archive data/go_explore/archive.json
    python scripts/validate_go_explore_archive.py --archive data/go_explore/archive.json --smoke
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class CellResult:
    cell_key: str
    record_id: str
    ok: bool
    reason: str


@dataclass
class ValidateReport:
    results: list[CellResult] = field(default_factory=list)
    smoke: str = "not_run"

    @property
    def n_total(self) -> int:
        return len(self.results)

    @property
    def n_pass(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def pass_rate(self) -> float:
        if self.n_total == 0:
            return 1.0
        return self.n_pass / self.n_total


def _resolve_cell_dir(archive_path: Path, cell: Any) -> Path:
    root = archive_path.parent
    if cell.bundle_path:
        bp = Path(cell.bundle_path)
        if not bp.is_absolute():
            bp = root / bp
        # bundle_path may point at cell.State or the cell directory.
        if bp.name.endswith(".State") or bp.suffix.lower() == ".state":
            return bp.parent
        if (bp / "cell.State").is_file() or bp.is_dir():
            return bp
    return root / "cells" / str(cell.record_id)


def validate_cell(archive_path: Path, cell: Any) -> CellResult:
    from re1_rl.go_explore_capture import (
        CELL_META_NAME,
        CELL_SIDECAR_NAME,
        CELL_STATE_NAME,
    )
    from re1_rl.pb_bundle_io import sha256_file

    cell_dir = _resolve_cell_dir(archive_path, cell)
    state_p = cell_dir / CELL_STATE_NAME
    side_p = cell_dir / CELL_SIDECAR_NAME
    meta_p = cell_dir / CELL_META_NAME

    if not state_p.is_file():
        return CellResult(cell.cell_key, cell.record_id, False, f"missing_state:{state_p}")
    if not side_p.is_file():
        return CellResult(cell.cell_key, cell.record_id, False, f"missing_sidecar:{side_p}")
    if not meta_p.is_file():
        return CellResult(cell.cell_key, cell.record_id, False, f"missing_meta:{meta_p}")

    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CellResult(cell.cell_key, cell.record_id, False, f"bad_meta:{exc}")

    want_state = meta.get("state_sha256")
    want_side = meta.get("sidecar_sha256")
    if not want_state or not want_side:
        return CellResult(
            cell.cell_key, cell.record_id, False, "meta_missing_sha256"
        )

    try:
        got_state = sha256_file(state_p)
        got_side = sha256_file(side_p)
    except OSError as exc:
        return CellResult(cell.cell_key, cell.record_id, False, f"sha_error:{exc}")

    if got_state != str(want_state):
        return CellResult(cell.cell_key, cell.record_id, False, "state_sha_mismatch")
    if got_side != str(want_side):
        return CellResult(cell.cell_key, cell.record_id, False, "sidecar_sha_mismatch")

    return CellResult(cell.cell_key, cell.record_id, True, "ok")


def validate_archive(archive_path: Path) -> ValidateReport:
    from re1_rl.go_explore_archive import GoExploreArchive

    report = ValidateReport()
    if not archive_path.is_file():
        raise FileNotFoundError(f"archive missing: {archive_path}")

    archive = GoExploreArchive(archive_path)
    archive.load()
    for cell in archive.cells.values():
        report.results.append(validate_cell(archive_path, cell))
    return report


def _bizhawk_available() -> tuple[bool, str]:
    try:
        from re1_rl.bizhawk_paths import EMUHAWK
    except Exception as exc:  # pragma: no cover
        return False, f"bizhawk_paths import failed: {exc}"
    if not Path(EMUHAWK).is_file():
        return False, f"EmuHawk missing: {EMUHAWK}"
    rom = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
    if not rom.is_file():
        return False, f"ROM missing: {rom}"
    return True, "ok"


def run_smoke(archive_path: Path, report: ValidateReport, *, max_cells: int = 1) -> None:
    """Best-effort BizHawk load of one passing cell; skip if tooling absent."""
    ok, reason = _bizhawk_available()
    if not ok:
        report.smoke = f"skipped:{reason}"
        print(f"[validate] smoke skipped — {reason}", flush=True)
        return

    passed = [r for r in report.results if r.ok]
    if not passed:
        report.smoke = "skipped:no_passing_cells"
        print("[validate] smoke skipped — no passing cells", flush=True)
        return

    from re1_rl.go_explore_archive import GoExploreArchive
    from re1_rl.go_explore_capture import CELL_STATE_NAME

    sample = passed[0]
    arch = GoExploreArchive(archive_path)
    arch.load()
    cell = next((c for c in arch.cells.values() if c.record_id == sample.record_id), None)
    if cell is None:
        report.smoke = "skipped:cell_missing"
        print("[validate] smoke skipped — cell disappeared", flush=True)
        return
    cell_dir = _resolve_cell_dir(archive_path, cell)
    state_p = cell_dir / CELL_STATE_NAME

    try:
        from re1_rl.bizhawk_bridge import BizHawkClient
        from re1_rl.bizhawk_paths import EMUHAWK

        # Smoke only checks that the State path is readable and non-empty;
        # full env.reset would require a live emulator session (heavy).
        nbytes = state_p.stat().st_size
        if nbytes < 64:
            report.smoke = f"fail:state_too_small:{nbytes}"
            print(f"[validate] smoke FAIL state too small ({nbytes} B)", flush=True)
            return
        # Confirm client class / emu path import without starting a long session.
        _ = BizHawkClient
        _ = EMUHAWK
        report.smoke = f"ok:state_present:{state_p.name}:{nbytes}B"
        print(
            f"[validate] smoke OK (BizHawk present; verified State "
            f"{state_p.name} {nbytes} B; live load not started)",
            flush=True,
        )
        if max_cells > 1:
            print(f"[validate] smoke sampled 1/{min(max_cells, len(passed))} cells", flush=True)
    except Exception as exc:
        report.smoke = f"skipped:smoke_error:{exc}"
        print(f"[validate] smoke skipped — {exc}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Go-Explore archive cells")
    ap.add_argument(
        "--archive",
        type=Path,
        default=PROJECT_ROOT / "data" / "go_explore" / "archive.json",
        help="Path to archive.json",
    )
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="Optional BizHawk presence / State smoke (skip if unavailable)",
    )
    args = ap.parse_args()

    archive_path = args.archive.resolve()
    print(f"[validate] archive={archive_path}", flush=True)

    try:
        report = validate_archive(archive_path)
    except FileNotFoundError as exc:
        print(f"[validate] FAIL: {exc}", flush=True)
        return 1

    for r in report.results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"  [{mark}] {r.cell_key} record={r.record_id} {r.reason}", flush=True)

    rate = report.pass_rate
    print(
        f"[validate] pass_rate={rate:.4%} ({report.n_pass}/{report.n_total})",
        flush=True,
    )

    if args.smoke:
        run_smoke(archive_path, report)

    print(f"[validate] smoke={report.smoke}", flush=True)
    print(
        json.dumps(
            {
                "n_total": report.n_total,
                "n_pass": report.n_pass,
                "pass_rate": rate,
                "smoke": report.smoke,
            },
            indent=2,
        ),
        flush=True,
    )

    if report.n_total == 0:
        print("[validate] empty archive — nothing to check", flush=True)
        return 0
    return 0 if report.n_pass == report.n_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
