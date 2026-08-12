"""Dump 48-slot box + inv from recent QuickSaves (no UI macros)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import EMUHAWK, LUA, assert_rom_present  # noqa: E402
from re1_rl.item_box import box_pollution_reason, read_box_live, read_inventory  # noqa: E402
from re1_rl.memory_map import ITEM_IDS, PLAYER_HP  # noqa: E402

PORT = 5654


def _fmt(slots):
    return "[" + ", ".join(
        f"{i}:{ITEM_IDS.get(iid, hex(iid))}x{q}"
        for i, (iid, q) in enumerate(slots)
        if iid
    ) + "]"


def main() -> int:
    states = sorted(
        (ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State").glob(
            "*QuickSave*.State"
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:4]
    rom = assert_rom_present()
    c = BizHawkClient(port=PORT, timeout=180.0, connect_timeout=90.0)
    c.start_server()
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(rom),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        c.wait_for_client()
        c.set_speed(400)
        for st in states:
            c.load_savestate(str(st.resolve()))
            c.frameadvance(4)
            hp = int(c.read_ram([("hp", PLAYER_HP, "u16")])["hp"])
            inv = read_inventory(c)
            box = read_box_live(c)
            pol = box_pollution_reason(box)
            print(f"=== {st.name} hp={hp} ===")
            print(f"inv {_fmt(inv)}")
            print(f"box {_fmt(box)}")
            print(f"pollution={pol}")
        return 0
    finally:
        try:
            c.quit()
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
