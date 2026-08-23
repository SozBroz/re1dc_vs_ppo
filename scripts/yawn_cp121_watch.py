"""Poll live cp121 captures and run replay validation when the tape changes.

  venv\\Scripts\\python.exe scripts\\yawn_cp121_watch.py
  venv\\Scripts\\python.exe scripts\\yawn_cp121_watch.py --interval-s 120
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAPE = ROOT / "states" / "yawn_rails" / "cells" / "cp121" / "leg_replay.json"
DEFAULT_LOG = ROOT / "data" / "logs" / "yawn_cp121_watch.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _tape_mtime(path: Path) -> float:
    return path.stat().st_mtime if path.is_file() else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", type=Path, default=DEFAULT_TAPE)
    ap.add_argument("--interval-s", type=float, default=90.0)
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--once", action="store_true", help="Validate once if tape exists, then exit")
    args = ap.parse_args()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    seen_mtime = 0.0
    print(f"[yawn_watch] tape={args.tape} log={args.log}", flush=True)

    while True:
        mtime = _tape_mtime(args.tape)
        if mtime > 0 and mtime != seen_mtime:
            seen_mtime = mtime
            print(f"[yawn_watch] new tape mtime={mtime:.0f}; validating...", flush=True)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "yawn_replay_validate.py"),
                    "--to",
                    "121",
                    "--json-out",
                    str(ROOT / "data" / "logs" / "yawn_cp121_validate.json"),
                ],
                cwd=str(ROOT),
            )
            row = {
                "ts": _utc_now(),
                "tape": str(args.tape),
                "mtime": mtime,
                "passed": proc.returncode == 0,
            }
            with args.log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            print(
                f"[yawn_watch] validate {'PASS' if row['passed'] else 'FAIL'}",
                flush=True,
            )
            if args.once:
                return 0 if row["passed"] else 1
        elif mtime <= 0:
            print("[yawn_watch] waiting for cp121 leg_replay.json...", flush=True)
            if args.once:
                return 2
        if args.once:
            return 0
        time.sleep(max(5.0, float(args.interval_s)))


if __name__ == "__main__":
    raise SystemExit(main())
