#!/usr/bin/env python3
"""Advance (or restore) the planner-loyal reset pin during the tape march.

March mode pins one start cell exclusively (hot-reloaded by workers, no
restart). Restoring brings back the pre-march weights saved in
``data/planner_loyal_reset_pin.pre_march.env``.

Usage:
  venv\\Scripts\\python.exe scripts/advance_march_pin.py --index 0
  venv\\Scripts\\python.exe scripts/advance_march_pin.py --index 2
  venv\\Scripts\\python.exe scripts/advance_march_pin.py --restore-pre-march
  venv\\Scripts\\python.exe scripts/advance_march_pin.py --sync-only
  venv\\Scripts\\python.exe scripts/advance_march_pin.py --show

Every mutation (and --sync-only) commits the pin on pking, pushes, and pulls
all WHs, so moving the pin on pking pins every WH. Use --no-sync for local only.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = ROOT / "data" / "planner_loyal_reset_pin.env"
PRE_MARCH_FILE = ROOT / "data" / "planner_loyal_reset_pin.pre_march.env"

# (label, ssh host, repo path on that box)
FLEET = (
    ("WH1", "sshuser@192.168.0.203", "D:/re1_rl"),
    ("WH2", "sshuser@192.168.0.116", "C:/Users/sshuser/re1_rl"),
    ("WH3", "sshuser@192.168.0.229", "C:/Users/sshuser/re1_rl"),
)

HEADER = """# Planner-loyal reset pin. Hot-reload (no worker restart after this code is loaded).
# Precedence: INDEX > RANGE > WEIGHTS > SET. Blank knobs = uniform over every loadable start.
# Exclusive pins that match no minted start fall back to the full pool.
"""


def _read_weights(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("RE1_PLANNER_RESET_PIN_WEIGHTS="):
            return line.split("=", 1)[1].strip()
    return ""


def show() -> int:
    print(PIN_FILE.read_text(encoding="utf-8"), flush=True)
    return 0


def set_index(slot: int) -> int:
    if slot < 0:
        print("ERROR: slot must be >= 0", flush=True)
        return 2
    PIN_FILE.write_text(
        HEADER
        + "\n"
        + f"# March mode: 100% starts on pl{slot:02d} (tape capture).\n"
        + f"RE1_PLANNER_RESET_PIN_INDEX={slot}\n"
        + "\n"
        + "RE1_PLANNER_RESET_PIN_RANGE=\n"
        + "\n"
        + "RE1_PLANNER_RESET_PIN_WEIGHTS=\n"
        + "\n"
        + "RE1_PLANNER_RESET_PIN_SET=\n"
        + "\n"
        + "RE1_PLANNER_RESET_PIN_SET_WEIGHT=\n",
        encoding="utf-8",
    )
    print(f"[pin] march INDEX={slot} (pl{slot:02d}); weights parked in {PRE_MARCH_FILE.name}",
          flush=True)
    return 0


def restore_pre_march() -> int:
    if not PRE_MARCH_FILE.is_file():
        print(f"ERROR: no restore point at {PRE_MARCH_FILE}", flush=True)
        return 2
    shutil.copy2(PRE_MARCH_FILE, PIN_FILE)
    print(f"[pin] restored pre-march weights: WEIGHTS={_read_weights(PIN_FILE) or '(blank)'}",
          flush=True)
    return 0


def fleet_sync(message: str) -> int:
    """Commit pin files on pking, push, pull on all WHs, verify same SHA.

    Moving the pin on pking pins every WH — no manual per-box steps.
    """
    import subprocess

    def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              timeout=120, **kw)

    rel_pin = str(PIN_FILE.relative_to(ROOT)).replace("\\", "/")
    rel_pre = str(PRE_MARCH_FILE.relative_to(ROOT)).replace("\\", "/")
    rel_self = "scripts/advance_march_pin.py"
    status = run(["git", "status", "--short", "--", rel_pin, rel_pre, rel_self]).stdout
    if status.strip():
        run(["git", "add", "--", rel_pin, rel_pre, rel_self])
        commit = run(["git", "commit", "-m", message])
        if commit.returncode != 0:
            print(f"[pin] commit failed:\n{commit.stderr}", flush=True)
            return 2
        print(f"[pin] committed: {message}", flush=True)
    push = run(["git", "push"])
    if push.returncode != 0:
        print(f"[pin] push failed:\n{push.stderr}", flush=True)
        return 2
    want = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    ok = True
    for label, host, repo in FLEET:
        pull = run(["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes",
                    host, f"cd /d {repo} && git pull --ff-only && git rev-parse --short HEAD"])
        got = (pull.stdout or "").strip().split()[-1] if pull.stdout else ""
        match = pull.returncode == 0 and got == want
        ok = ok and match
        print(f"[pin] {label}: {'OK ' + got if match else 'MISMATCH out=' + (pull.stdout or '')[-200:] + ' err=' + (pull.stderr or '')[-200:]}",
              flush=True)
    if not ok:
        print("[pin] fleet SHAs differ — resolve before relying on the pin", flush=True)
        return 2
    print(f"[pin] fleet in sync at {want}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--index", type=int, default=None, help="march pin slot (e.g. 0, 2, 6)")
    grp.add_argument("--restore-pre-march", action="store_true",
                     help="restore weights saved before the march")
    grp.add_argument("--show", action="store_true")
    grp.add_argument("--sync-only", action="store_true",
                     help="push current pin file to all WHs (use after hand-edits)")
    ap.add_argument("--no-sync", action="store_true",
                    help="skip fleet commit/push/pull (local file only)")
    args = ap.parse_args()
    if args.show:
        return show()
    if args.sync_only:
        return fleet_sync("pin: sync hand-edited reset pin to fleet")
    if args.restore_pre_march:
        rc = restore_pre_march()
    else:
        rc = set_index(int(args.index))
    if rc != 0:
        return rc
    if args.no_sync:
        print("[pin] local only (--no-sync); WHs NOT updated", flush=True)
        return 0
    what = ("restore pre-march reset pin"
            if args.restore_pre_march else f"march pin INDEX={int(args.index)}")
    return fleet_sync(f"pin: {what}")


if __name__ == "__main__":
    raise SystemExit(main())
