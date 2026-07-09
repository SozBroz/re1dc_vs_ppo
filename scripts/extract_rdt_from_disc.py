"""Extract ROOM*.RDT from PS1 BIN (2352-byte sectors) via pycdlib."""
from __future__ import annotations

import io
from pathlib import Path

import pycdlib

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "roms" / "Resident Evil - Director's Cut.bin"
OUT = ROOT / "data" / "rdt_raw"
SECTOR = 2352
DATA_OFF = 24
DATA_LEN = 2048


def bin_to_iso2048(path: Path) -> bytes:
    """Strip sync/header/EDC from raw PS1 BIN -> contiguous 2048-byte sectors."""
    raw = path.read_bytes()
    out = bytearray()
    for off in range(0, len(raw) - SECTOR + 1, SECTOR):
        out.extend(raw[off + DATA_OFF : off + DATA_OFF + DATA_LEN])
    return bytes(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[rdt] stripping BIN -> ISO2048 ({BIN.stat().st_size // SECTOR} sectors)...")
    iso_bytes = bin_to_iso2048(BIN)
    iso = pycdlib.PyCdlib()
    iso.open_fp(io.BytesIO(iso_bytes))
    n = 0
    for dirpath, _dirs, files in iso.walk(iso_path="/"):
        for fname in files:
            base = fname.split(";")[0].upper()
            if not (base.startswith("ROOM") and base.endswith(".RDT")):
                continue
            iso_path = f"{dirpath.rstrip('/')}/{fname}"
            out = OUT / base
            with out.open("wb") as fp:
                iso.get_file_from_iso_fp(fp, iso_path=iso_path)
            n += 1
            if n <= 5 or n % 50 == 0:
                print(f"  {base}  {out.stat().st_size}")
    iso.close()
    print(f"[rdt] extracted {n} files -> {OUT}")


if __name__ == "__main__":
    main()
