"""Verify player X/Y/Z/facing addresses with a live walk trace."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient

IN_STATE = "D:/re1_rl/states/jill_control.State"

FIELDS = [
    ("x", 0x800C5158, "s16"),
    ("y", 0x800C515C, "s16"),
    ("z", 0x800C5160, "s16"),
    ("facing", 0x800C5198, "u16"),
]


def trace(client: BizHawkClient, label: str, buttons: dict, steps: int) -> None:
    print(f"--- {label} ---", flush=True)
    for _ in range(steps):
        client.send_buttons(buttons)
        client.frameadvance(15)
        vals = client.read_ram(FIELDS)
        print(f"  x={vals['x']:6d} y={vals['y']:6d} z={vals['z']:6d}"
              f" facing={vals['facing']:5d}", flush=True)
    client.send_buttons({})


def main() -> int:
    client = BizHawkClient(timeout=300.0)
    client.start_server()
    print("listening; launch EmuHawk now", flush=True)
    client.wait_for_client()
    client.set_speed(6400)
    client.load_savestate(IN_STATE)
    client.frameadvance(5)

    vals = client.read_ram(FIELDS)
    print(f"start: {vals}", flush=True)

    trace(client, "turn left 1s", {"left": True}, 4)
    trace(client, "walk fwd 2s", {"up": True}, 8)
    trace(client, "turn right 0.5s", {"right": True}, 2)
    trace(client, "walk fwd 2s", {"up": True}, 8)

    client.set_speed(100)
    client.quit()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
