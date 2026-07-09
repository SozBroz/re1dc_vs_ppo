"""Synthesize keyboard input to drive play_human.py hands-free (diagnostic).

Fresh dining spawn sits ON the west double door (entered through it), facing
into the room. Turn ~180 degrees, press action to open the door back to the
main hall 106 -- triggers the Barry scene on a fresh save.

Run AFTER play_human is up (banner printed):
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_drive_keys.py
"""

from __future__ import annotations

import sys
import time

import keyboard  # same package play_human uses to read keys


def hold(key: str, seconds: float) -> None:
    print(f"[drive] hold {key} {seconds:.1f}s", flush=True)
    keyboard.press(key)
    time.sleep(seconds)
    keyboard.release(key)
    time.sleep(0.15)


def tap(key: str, times: int = 1, gap: float = 0.35) -> None:
    for _ in range(times):
        print(f"[drive] tap {key}", flush=True)
        keyboard.press(key)
        time.sleep(0.08)
        keyboard.release(key)
        time.sleep(gap)


def main() -> int:
    delay = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    print(f"[drive] starting in {delay:.0f}s...", flush=True)
    time.sleep(delay)

    # turn ~180 (tank turn ~16 facing-units/frame -> ~2.1s for 2048)
    hold("d", 2.2)
    # open the door (west double door directly behind spawn)
    tap("z", times=3, gap=0.5)
    # give the auto-skip 20s to chew through door + Barry scene
    print("[drive] waiting 20s for door + cutscene skip...", flush=True)
    time.sleep(20.0)
    # walk forward a bit in the new room to prove control returned
    hold("w", 1.5)
    print("[drive] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
