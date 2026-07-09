"""Machine-tagged logging with human-readable EST timestamps."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_EST = ZoneInfo("America/New_York")


def est_now() -> str:
    return datetime.now(_EST).strftime("%Y-%m-%d %H:%M:%S EST")


def log(machine: str, msg: str) -> None:
    print(f"[{est_now()}] [{machine}] {msg}", flush=True)
