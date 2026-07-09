"""Compare two BizHawk savestates (RAM summary)."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.fresh_spawn import format_spawn_summary, validate_fresh_dining_spawn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"


def summarize(client: BizHawkClient, path: Path) -> dict:
    client.load_savestate(str(path))
    client.frameadvance(3)
    return client.read_ram()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--port", type=int, default=5566)
    ap.add_argument("--launch-emu", action="store_true")
    args = ap.parse_args()

    proc = None
    if args.launch_emu:
        proc = subprocess.Popen(
            [str(EMUHAWK), str(ROM), f"--lua={LUA}",
             "--socket_ip=127.0.0.1", f"--socket_port={args.port}"],
            cwd=str(EMUHAWK.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(8.0)

    client = BizHawkClient(port=args.port, timeout=120.0, connect_timeout=120.0)
    client.start_server()
    print(f"listening on {args.port}", flush=True)
    client.wait_for_client()
    print("connected", flush=True)

    for label, path in [("A", args.a), ("B", args.b)]:
        ram = summarize(client, path)
        ok, errs = validate_fresh_dining_spawn(ram, require_jill=False)
        print(f"\n{label} {path.name}: {format_spawn_summary(ram)}")
        print(f"  fresh_ok={ok}" + (f" ({errs})" if errs else ""))

    client.quit()
    client.close()
    if proc is not None:
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
