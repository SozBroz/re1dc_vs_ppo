"""Retry transient Windows file sharing / permission races.

Multi-process yawn rails writers and readers contend on ``manifest.json``;
``PermissionError`` / WinError 32 on open or ``os.replace`` must not kill actors.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def _transient_fs_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror in (5, 32):  # access denied / sharing violation
            return True
        if getattr(exc, "errno", None) in (13, 11):  # EACCES / EAGAIN
            return True
    return False


def read_text_retry(
    path: Path | str,
    *,
    encoding: str = "utf-8-sig",
    attempts: int = 5,
    delay_s: float = 1.0,
) -> str:
    """``Path.read_text`` with sleeps between transient permission failures."""
    target = Path(path)
    last: OSError | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            return target.read_text(encoding=encoding)
        except OSError as exc:
            if not _transient_fs_error(exc):
                raise
            last = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(float(delay_s))
    assert last is not None
    raise last


def replace_retry(
    src: Path | str,
    dst: Path | str,
    *,
    attempts: int = 5,
    delay_s: float = 1.0,
) -> None:
    """``os.replace`` with sleeps between transient permission failures."""
    last: OSError | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            os.replace(str(src), str(dst))
            return
        except OSError as exc:
            if not _transient_fs_error(exc):
                raise
            last = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(float(delay_s))
    assert last is not None
    raise last
