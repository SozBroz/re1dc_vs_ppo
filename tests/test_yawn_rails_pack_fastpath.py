"""Yawn rails bundle-pack fast path + cells-lock stale recovery.

Regression cover for the fleet sync wedge (2026-09-12): every worker poll
fetches every catalogued index, and most catalogued rows have no on-disk
bundle. ``pack_bundle_zip`` used to take ``cells.sync.lock`` before checking
the dir, so hundreds of doomed ghost packs serialized behind real ingest /
mint traffic and wedged every worker's manifest poll behind lock waits.
Leaked (empty) lock files then browned out the plane because the waiter
timeout (90s) was shorter than the stale horizon (180s).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from re1_rl.yawn_rails_sync import (
    YawnRailsCellStore,
    _LOCK_NAME,
    acquire_yawn_cells_lock,
    yawn_cells_locked,
)


def _pl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RE1_YAWN_CELL_PREFIX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RAILS_ROOT", raising=False)
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")


def test_pack_missing_returns_none_without_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pl_env(monkeypatch)
    store = YawnRailsCellStore(tmp_path)
    assert store.pack_bundle_zip("pl04") is None
    assert not (tmp_path / _LOCK_NAME).exists()


def test_pack_present_still_zips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pl_env(monkeypatch)
    cell = tmp_path / "cells" / "pl04"
    cell.mkdir(parents=True)
    (cell / "cell.pst").write_bytes(b"STATE_04")
    (cell / "cell.sidecar.json").write_text(
        json.dumps({"checkpoint_index": 4}) + "\n", encoding="utf-8"
    )
    store = YawnRailsCellStore(tmp_path)
    blob = store.pack_bundle_zip("pl04")
    assert blob is not None and len(blob) > 0
    assert not (tmp_path / _LOCK_NAME).exists()


def test_stale_empty_lock_cleared_within_waiter_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pl_env(monkeypatch)
    lp = tmp_path / _LOCK_NAME
    lp.write_bytes(b"")  # leaked empty lock: crashed between create and payload
    old = time.time() - 3600.0
    os.utime(lp, (old, old))
    with yawn_cells_locked(tmp_path, holder="test-recovery"):
        pass
    assert not lp.exists()


def test_live_lock_not_stolen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pl_env(monkeypatch)
    assert acquire_yawn_cells_lock(tmp_path, holder="test-holder") is True
    # Second acquire must fail while the first holder is live.
    assert acquire_yawn_cells_lock(tmp_path, holder="test-other") is False
