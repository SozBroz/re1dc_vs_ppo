"""Filter modal-flag candidates against an IDLE-in-control baseline.

The flag must satisfy: idle-control values == moving-control values behavior
(or at least DISJOINT from modal), i.e. discriminate modal vs *standing
still*, not modal vs walking. Otherwise auto-mash would fire while the
player idles.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag7.py --port 5721
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
    (0x800C2000, 0x8000),
    (0x800CF000, 0x1000),
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
    ap.add_argument("--port", type=int, default=5721)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)

    idle_vals: dict[int, set[int]] = {}
    move_vals: dict[int, set[int]] = {}
    modal_vals: dict[int, set[int]] = {}

    def absorb(dst: dict[int, set[int]], s: dict[int, int]) -> None:
        for a, v in s.items():
            dst.setdefault(a, set()).add(v)

    # IDLE baseline in dining room: stand still, sample over ~2s of frames
    print("sampling idle-control baseline...", flush=True)
    for _ in range(8):
        b.frameadvance(15)
        absorb(idle_vals, snap(b))

    # a couple of moving samples too (for reference filtering)
    for mv in ("up", "left", "right"):
        b.step({mv: True}, 16)
        absorb(move_vals, snap(b))

    # trigger the Kenneth-blood modal in tea room via same wander
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
            print(f"[{i}] burned uncontrolled {r['burned']}", flush=True)
            continue
        dead, last = all_dirs_dead(b)
        if dead:
            print(f"[{i}] TRUE MODAL: {fmt(last)}", flush=True)
            # also collect a mid-modal idle window (text scrolling)
            for _ in range(8):
                absorb(modal_vals, snap(b))
                b.frameadvance(8)
            modal_found = True
            break

    if not modal_found:
        print("NO MODAL FOUND", flush=True)
        b.set_speed(100)
        b.quit()
        b.close()
        print("DIAG_DONE", flush=True)
        return 1

    hits = []
    for a, mset in modal_vals.items():
        if len(mset) != 1:
            continue
        m = next(iter(mset))
        iv = idle_vals.get(a, set())
        mvv = move_vals.get(a, set())
        if iv and mvv and m not in iv and m not in mvv and len(iv) <= 3:
            hits.append((a, sorted(iv), sorted(mvv), m))
    hits.sort()
    print(f"\n=== {len(hits)} idle-safe discriminators ===", flush=True)
    for a, iv, mvv, m in hits[:80]:
        iv_txt = ",".join(f"0x{x:02X}" for x in iv[:5])
        mv_txt = ",".join(f"0x{x:02X}" for x in mvv[:5])
        print(f"  0x{a:08X}: idle={{{iv_txt}}} move={{{mv_txt}}} modal=0x{m:02X}",
              flush=True)

    # dismiss and print post-dismiss values of top hits
    watch = [a for a, _, _, _ in hits[:16]]
    for j in range(25):
        b.step({"cross": True}, 8)
        b.frameadvance(20)
        dead, last = all_dirs_dead(b)
        if not dead:
            print(f"\ndismissed after {j + 1} cycles: {fmt(last)}", flush=True)
            # idle for a beat, then snapshot (post-dismiss idle values)
            b.frameadvance(20)
            s = snap(b)
            for a in watch:
                print(f"  0x{a:08X} post_dismiss_idle=0x{s[a]:02X}", flush=True)
            break
    else:
        print("modal NOT dismissed", flush=True)

    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
