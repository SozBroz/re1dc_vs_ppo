"""Offline tests for env-style box cursor tracking helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CursorTracker:
    inv: int = 0
    box: int = 0

    def apply(self, report: dict[str, Any]) -> None:
        if not report.get("ok"):
            return
        if report.get("inv_cursor") is not None:
            self.inv = int(report["inv_cursor"])
        if report.get("box_cursor") is not None:
            self.box = int(report["box_cursor"])


def test_cursor_tracker_only_advances_on_success() -> None:
    cur = CursorTracker(inv=0, box=0)
    cur.apply({"ok": False, "inv_cursor": 5, "box_cursor": 3})
    assert cur.inv == 0 and cur.box == 0

    cur.apply({"ok": True, "inv_cursor": 7, "box_cursor": 0})
    assert cur.inv == 7 and cur.box == 0

    cur.apply({"ok": True, "inv_cursor": 0, "box_cursor": 2})
    assert cur.inv == 0 and cur.box == 2


def test_cursor_tracker_ignores_missing_out_fields() -> None:
    cur = CursorTracker(inv=3, box=1)
    cur.apply({"ok": True})
    assert cur.inv == 3 and cur.box == 1
