"""Probe BizHawk .State file layout for offline MainRAM extraction."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"


def try_extract_mainram(data: bytes) -> bytes | None:
  # BizHawk 2.x text header then binary chunks; search for 2 MiB MainRAM blob.
  # Heuristic: decompress all zlib blocks, pick 2097152-byte result.
  for i in range(len(data) - 2):
    if data[i] != 0x78 or data[i + 1] not in (0x01, 0x5E, 0x9C, 0xDA):
      continue
    for size in (len(data) - i, 500000, 200000, 100000):
      try:
        dec = zlib.decompress(data[i : i + size])
      except zlib.error:
        continue
      if len(dec) == 2_097_152:
        return dec
      if len(dec) > 1_900_000 and len(dec) < 2_200_000:
        return dec
  return None


def main() -> None:
  states = sorted(STATE_DIR.glob("*.QuickSave*.State"), key=lambda p: p.stat().st_mtime)
  for p in states[-2:]:
    data = p.read_bytes()
    print(f"\n{p.name} size={len(data)}")
  for marker in (b"MainRAM", b"SAVESTATE", b"Nyma", b"Nymashock"):
    print(f"  {marker!r} @ {data.find(marker)}")
  ram = try_extract_mainram(data)
  print(f"  extracted ram: {len(ram) if ram else None}")
  if ram:
    off_box = 0xC8724
    off_inv = 0xC8784
    print(f"  box @0x{off_box:06X}: {ram[off_box:off_box+30].hex(' ')}")
    print(f"  inv @0x{off_inv:06X}: {ram[off_inv:off_inv+16].hex(' ')}")


if __name__ == "__main__":
  main()
