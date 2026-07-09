"""Diff two BizHawk Core.bin blobs; highlight small structured changes."""

from __future__ import annotations

import zipfile
from pathlib import Path

import zstandard as zstd

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
ITEM_IDS = {0x01: "knife", 0x02: "beretta/clip", 0x41: "spray"}


def load_core(path: Path) -> bytes:
    with zipfile.ZipFile(path, "r") as zf:
        raw = zf.read("Core.bin.zst")
    return zstd.ZstdDecompressor().decompress(raw, max_output_size=64 * 1024 * 1024)


def diff_regions(a: bytes, b: bytes, *, max_len: int = 32, max_show: int = 80) -> None:
    runs: list[tuple[int, int]] = []
    i = 0
    n = min(len(a), len(b))
    while i < n:
        if a[i] == b[i]:
            i += 1
            continue
        start = i
        while i < n and a[i] != b[i]:
            i += 1
        if i - start <= max_len:
            runs.append((start, i))
    print(f"diff runs (len<={max_len}): {len(runs)}")
    for start, end in runs[:max_show]:
        span = end - start
        chunk_a = a[start:end]
        chunk_b = b[start:end]
        note = ""
        if span <= 8:
            pairs_a = [f"{chunk_a[j]:02X}/{chunk_a[j+1]:02X}" for j in range(0, len(chunk_a)-1, 2)]
            pairs_b = [f"{chunk_b[j]:02X}/{chunk_b[j+1]:02X}" for j in range(0, len(chunk_b)-1, 2)]
            note = f" pairsA={pairs_a} pairsB={pairs_b}"
        print(f"  @{start:7d} len={span:4d}  A={chunk_a[:16].hex()}  B={chunk_b[:16].hex()}{note}")


def scan_for_clip_pattern(core: bytes) -> None:
    """Find 02 XX 02 YY patterns (two beretta/clip slots)."""
    hits = []
    for i in range(len(core) - 4):
        if core[i] == 0x02 and core[i + 2] == 0x02:
            hits.append((i, core[i : i + 4]))
    print(f"beretta/clip pair patterns: {len(hits)}")
    for off, chunk in hits[:20]:
        print(f"  @{off}: {chunk.hex()}")


def main() -> None:
    states = sorted(STATE_DIR.glob("*.QuickSave*.State"), key=lambda p: p.stat().st_mtime)
    s5, s6 = states[-2], states[-1]
    c5, c6 = load_core(s5), load_core(s6)
    print(f"A={s5.name}\nB={s6.name}\n")
    diff_regions(c5, c6, max_len=32, max_show=80)
    print("\n-- scan A for twin clip slots --")
    scan_for_clip_pattern(c5)


if __name__ == "__main__":
    main()
