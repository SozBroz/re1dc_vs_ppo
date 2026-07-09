"""Wide-RAM scan for the dialogue/modal flag (bit80 stays SET in dialogue).

Strict modal test: movement dead in ALL FOUR directions (wall-stuck would
still allow at least one). Samples 36KB of the globals bank repeatedly in
control and in the modal, then reports addresses whose value sets are
disjoint (control vs modal) and constant while modal.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag6.py --port 5720
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import GAME_STATE, PLAYER_X, PLAYER_Z, ROOM_ID

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "states" / "jill_control_fresh.State"

REGIONS = [
    (0x800C2000, 0x8000),   # game-state globals
    (0x800CF000, 0x1000),   # text/message vars (MK map)
]

POLL = [
    ("state", GAME_STATE, "u32"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def fmt(v: dict) -> str:
    hi = (v["state"] >> 24) & 0xFF
    return (f"bit80={'1' if hi & 0x80 else '0'} room={v['room']} "
            f"pos=({v['x']},{v['z']})")


def snap(b: BizHawkClient) -> dict[int, int]:
    out: dict[int, int] = {}
    for base, size in REGIONS:
        for off in range(0, size, 0x1000):
            blk = b.read_block(base + off, min(0x1000, size - off))
            for i, byte in enumerate(blk):
                out[base + off + i] = byte
    return out


def all_dirs_dead(b: BizHawkClient) -> tuple[bool, dict]:
    last = read(b)
    for d in ("up", "down", "left", "right"):
        before = read(b)
        b.step({d: True}, 12)
        after = read(b)
        last = after
        if abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) >= 8:
            return False, last
    return True, last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5720)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)
    print(f"start: {fmt(read(b))}", flush=True)

    # phase A: control samples while wandering
    control_vals: dict[int, set[int]] = {}
    modal_vals: dict[int, set[int]] = {}

    def absorb(dst: dict[int, set[int]], s: dict[int, int]) -> None:
        for a, v in s.items():
            dst.setdefault(a, set()).add(v)

    moves = ["up", "up", "left", "up", "right", "up", "up", "down", "left", "up"] * 3
    modal_found = False
    for i, mv in enumerate(moves):
        b.step({mv: True}, 24)
        b.step({"cross": True}, 2)
        b.frameadvance(20)
        v = read(b)
        hi = (v["state"] >> 24) & 0xFF
        if not hi & 0x80:
            r = b.fast_forward(6000, mode_addr=0x800C3003, mask=0x80,
                               speed=6400, restore_speed=6400, invisible=False)
            print(f"[{i}] burned uncontrolled span {r['burned']}", flush=True)
            continue
        dead, last = all_dirs_dead(b)
        if dead:
            print(f"[{i}] TRUE MODAL (all dirs dead): {fmt(last)}", flush=True)
            for _ in range(6):
                absorb(modal_vals, snap(b))
                b.frameadvance(6)
            modal_found = True
            break
        absorb(control_vals, snap(b))
        print(f"[{i}] control sample: {fmt(last)}", flush=True)

    if not modal_found:
        print("NO TRUE MODAL FOUND on wander path", flush=True)
        b.set_speed(100)
        b.quit()
        b.close()
        print("DIAG_DONE", flush=True)
        return 1

    # report discriminators: modal constant, disjoint from control set
    hits = []
    for a, mv_set in modal_vals.items():
        if len(mv_set) != 1:
            continue
        cv = control_vals.get(a)
        if cv and not (mv_set & cv):
            hits.append((a, sorted(cv), next(iter(mv_set))))
    hits.sort()
    print(f"\n=== {len(hits)} discriminator candidates ===", flush=True)
    for a, cv, m in hits[:120]:
        cv_txt = ",".join(f"0x{x:02X}" for x in cv[:6])
        print(f"  0x{a:08X}: control={{{cv_txt}}} modal=0x{m:02X}", flush=True)

    # try to dismiss with longer press cycles and watch the top candidates
    watch = [a for a, _, _ in hits[:12]]
    print("\ndismissing with 8f press / 20f release cycles...", flush=True)
    for j in range(25):
        b.step({"cross": True}, 8)
        b.frameadvance(20)
        dead, last = all_dirs_dead(b)
        if not dead:
            print(f"dismissed after {j + 1} cycles: {fmt(last)}", flush=True)
            s = snap(b)
            for a in watch:
                print(f"  0x{a:08X} now=0x{s[a]:02X}", flush=True)
            break
    else:
        print("modal NOT dismissed after 25 cycles", flush=True)

    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
