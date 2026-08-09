"""Tests for Windows FS retry helpers used by yawn rails manifest IO."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.win_fs_retry import read_text_retry, replace_retry
from re1_rl.yawn_rails import load_manifest


def test_read_text_retry_succeeds_after_permission_errors(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"ok": true}\n', encoding="utf-8")
    calls = {"n": 0}
    real = Path.read_text

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)

    with patch.object(Path, "read_text", flaky):
        with patch("re1_rl.win_fs_retry.time.sleep") as sleep:
            text = read_text_retry(path, attempts=5, delay_s=1.0)
    assert '"ok"' in text
    assert calls["n"] == 3
    assert sleep.call_count == 2
    sleep.assert_called_with(1.0)


def test_read_text_retry_exhausted(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("x", encoding="utf-8")

    def always_denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    with patch.object(Path, "read_text", always_denied):
        with patch("re1_rl.win_fs_retry.time.sleep"):
            with pytest.raises(PermissionError):
                read_text_retry(path, attempts=3, delay_s=0.01)


def test_replace_retry_succeeds_after_permission_errors(tmp_path: Path) -> None:
    src = tmp_path / "a.tmp"
    dst = tmp_path / "a.json"
    src.write_text("hi", encoding="utf-8")
    calls = {"n": 0}
    real = __import__("os").replace

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 2:
            raise PermissionError(13, "Permission denied")
        return real(a, b)

    with patch("re1_rl.win_fs_retry.os.replace", flaky):
        with patch("re1_rl.win_fs_retry.time.sleep"):
            replace_retry(src, dst, attempts=5, delay_s=1.0)
    assert dst.read_text(encoding="utf-8") == "hi"


def test_load_manifest_retries_permission_error(tmp_path: Path) -> None:
    man = tmp_path / "manifest.json"
    man.write_text(
        '{"schema_version": 1, "route_id": "r", "cells": []}\n',
        encoding="utf-8",
    )
    stage = {"route_id": "r", "cells_manifest": "manifest.json"}
    calls = {"n": 0}
    real = Path.read_text

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)

    with patch.object(Path, "read_text", flaky):
        with patch("re1_rl.win_fs_retry.time.sleep"):
            data = load_manifest(tmp_path, stage)
    assert data["route_id"] == "r"
    assert calls["n"] == 2
