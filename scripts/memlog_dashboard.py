#!/usr/bin/env python
"""Launch the local RE1 memlog browser dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from re1_rl.memlog_dashboard.server import main


if __name__ == "__main__":
    raise SystemExit(main())
