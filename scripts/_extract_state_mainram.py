"""Score candidate MainRAM bases in Core.bin using multi-field anchors."""

from __future__ import annotations

import zipfile
from pathlib import Path

import zstandard as zstd

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"

OFF = {
    "hp": 0xC51AC,
    "stage": 0xC8660,
    "room": 0xC8661,
    "char": 0xC8669,
    "mode": 0xC3003,
    "item_box": 0xC8724,
    "inventory": 0xC8784,
}
ITEM_IDS = {0x01: "knife", 0x02: "beretta/clip", 0x41: "spray"}


def load_core(path: Path) -> bytes:
    with zipfile.ZipFile(path, "r") as zf:
        return zstd.ZstdDecompressor().decompress(zf.read("Core.bin.zst"), max_output_size=64 * 1024 * 1024)


def score_base(core: bytes, base: int) -> int | None:
    if base + 0xC8800 > len(core):
        return None
    hp = core[base + OFF["hp"]] | (core[base + OFF["hp"] + 1] << 8)
    if hp < 1 or hp > 200:
        return None
    stage = core[base + OFF["stage"]]
    room = core[base + OFF["room"]]
    char_id = core[base + OFF["char"]]
    mode = core[base + OFF["mode"]]
    s = 0
    if stage == 0:
        s += 2
    if room in (0x00, 0x1B):  # save room or store
        s += 3
    if char_id in (0, 1):
        s += 1
    if mode & 0x80:
        s += 2
    return s


def best_base(core: bytes) -> int | None:
    best = (-1, -1)
    for base in range(0, len(core) - 0xD0000):
        sc = score_base(core, base)
        if sc is None:
            continue
        if sc > best[0]:
            best = (sc, base)
    return best[1] if best[0] >= 0 else None


def show_slots(block: bytes, n: int) -> list[str]:
    lines = []
    for i in range(n):
        iid, qty = block[i * 2], block[i * 2 + 1]
        if iid or qty:
            name = ITEM_IDS.get(iid, f"0x{iid:02X}")
            lines.append(f"    [{i}] {name} x{qty}")
    return lines


def analyze(path: Path) -> tuple[int, bytes]:
    core = load_core(path)
    base = best_base(core)
    print(f"\n{path.name}  base={base}")
    if base is None:
        return -1, core
    hp = core[base + OFF["hp"]] | (core[base + OFF["hp"] + 1] << 8)
    room = core[base + OFF["room"]]
    stage = core[base + OFF["stage"]]
    print(f"  hp={hp} room={stage+1}{room:02X} char={core[base+OFF['char']]} mode=0x{core[base+OFF['mode']]:02X}")
    inv = core[base + OFF["inventory"] : base + OFF["inventory"] + 16]
    box = core[base + OFF["item_box"] : base + OFF["item_box"] + 32]
    print(f"  inv: {inv.hex(' ')}")
    for line in show_slots(inv, 8) or ["    (empty)"]:
        print(line)
    print(f"  box: {box.hex(' ')}")
    for line in show_slots(box, 16) or ["    (empty)"]:
        print(line)
    return base, core


def main() -> None:
    states = sorted(STATE_DIR.glob("*.QuickSave*.State"), key=lambda p: p.stat().st_mtime)
    b5, c5 = analyze(states[-2])
    b6, c6 = analyze(states[-1])
    if b5 < 0 or b6 < 0:
        return
    print("\n=== DIFFS 0xC8700-0xC8800 ===")
    for off in range(0xC8700, 0xC8800):
        if c5[b5 + off] != c6[b6 + off]:
            print(f"  0x{off:06X}: {c5[b5+off]:02X} -> {c6[b6+off]:02X}")


if __name__ == "__main__":
    main()
