"""Validate a Yawn fight cell tape replays from its predecessor.

Example (live cp121 after grind capture):

  venv\\Scripts\\python.exe scripts\\yawn_replay_validate.py --to 121
  venv\\Scripts\\python.exe scripts\\yawn_replay_validate.py --to 121 --crystals
  venv\\Scripts\\python.exe scripts\\yawn_replay_validate.py --to 121 --json-out data\\logs\\yawn_cp121_validate.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_replay(
    *,
    to_idx: int,
    mode: str,
    crystals: bool,
    rails_root: Path | None,
    port: int,
    force_stale: bool,
) -> dict[str, object]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "replay_leg.py"),
        "--to",
        str(to_idx),
        "--mode",
        mode,
        "--port",
        str(port),
    ]
    if crystals:
        cmd.append("--crystals")
    elif rails_root is not None:
        cmd.extend(["--rails-root", str(rails_root)])
    if force_stale:
        cmd.append("--force-stale")
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    passed = proc.returncode == 0
    tail = "\n".join(combined.splitlines()[-24:])
    return {
        "mode": mode,
        "exit_code": int(proc.returncode),
        "passed": bool(passed),
        "tail": tail,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", type=int, default=121, help="Destination checkpoint index")
    ap.add_argument("--crystals", action="store_true", help="Use backups/Crystals_in_time")
    ap.add_argument("--rails-root", type=Path, default=None, help="Override yawn rails root")
    ap.add_argument("--port", type=int, default=7798)
    ap.add_argument(
        "--no-force-stale",
        action="store_true",
        help="Require exact predecessor State sha (strict)",
    )
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--actions-only", action="store_true")
    ap.add_argument("--joypad-only", action="store_true")
    args = ap.parse_args()

    modes = ["actions", "joypad"]
    if args.actions_only:
        modes = ["actions"]
    elif args.joypad_only:
        modes = ["joypad"]

    report: dict[str, object] = {
        "ts": _utc_now(),
        "to_checkpoint_index": int(args.to),
        "crystals": bool(args.crystals),
        "rails_root": str(args.rails_root) if args.rails_root else None,
        "modes": {},
    }
    all_ok = True
    for mode in modes:
        row = _run_replay(
            to_idx=int(args.to),
            mode=mode,
            crystals=bool(args.crystals),
            rails_root=args.rails_root,
            port=int(args.port),
            force_stale=not bool(args.no_force_stale),
        )
        report["modes"][mode] = row
        all_ok = all_ok and bool(row["passed"])
        status = "PASS" if row["passed"] else "FAIL"
        print(f"[yawn_validate] cp{int(args.to):02d} {mode}: {status}", flush=True)
        print(row["tail"], flush=True)

    report["passed"] = all_ok
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"[yawn_validate] wrote {args.json_out}", flush=True)

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
