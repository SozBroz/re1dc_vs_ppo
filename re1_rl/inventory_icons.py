"""GPURAM inventory-grid icon patches (RE1 PS1 / BizHawk Nymashock).

Magic ``INVENTORY_BASE`` writes update item id/qty text but leave the ITEM-screen
grid icons stale. Those icons live in emulator ``GPURAM`` (not MainRAM). A
same-layout GPURAM delta taken from an authentic box-UI transfer fixes the
knife→CLIP slot-0 case used by the save-room box swap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATCH_DIR = _ROOT / "data" / "inventory_icon_patches"

# Knife-in-slot0 → CLIP-in-slot0 (+ knife in slot3) after QS5 magic box swap.
PATCH_CLIP_INTO_SLOT0_FROM_KNIFE_QS5 = "clip_into_slot0_from_knife_qs5"


class _BridgeGpu(Protocol):
    def write_domain(self, domain: str, address: int, data: bytes | list[int]) -> None: ...


def patch_paths(patch_id: str, patch_dir: Path | None = None) -> tuple[Path, Path]:
    base = Path(patch_dir) if patch_dir is not None else DEFAULT_PATCH_DIR
    return base / f"{patch_id}.idx", base / f"{patch_id}.bin"


def load_gpuram_patch(
    patch_id: str,
    *,
    patch_dir: Path | None = None,
) -> list[tuple[int, bytes]]:
    """Load ``(gpuram_offset, bytes)`` runs for ``patch_id``."""
    idx_path, bin_path = patch_paths(patch_id, patch_dir)
    if not idx_path.is_file() or not bin_path.is_file():
        raise FileNotFoundError(f"missing icon patch files for {patch_id!r} under {idx_path.parent}")
    blob = bin_path.read_bytes()
    runs: list[tuple[int, bytes]] = []
    off = 0
    for line in idx_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        addr_s, ln_s = line.split(",")
        addr = int(addr_s, 16)
        ln = int(ln_s)
        runs.append((addr, blob[off : off + ln]))
        off += ln
    if off != len(blob):
        raise ValueError(f"patch {patch_id!r}: idx consumed {off} bytes but bin has {len(blob)}")
    return runs


def apply_gpuram_icon_patch(
    bridge: _BridgeGpu,
    patch_id: str = PATCH_CLIP_INTO_SLOT0_FROM_KNIFE_QS5,
    *,
    patch_dir: Path | None = None,
) -> int:
    """Write a GPURAM icon patch. Returns number of bytes written."""
    if not hasattr(bridge, "write_domain"):
        raise TypeError("bridge does not support write_domain (GPURAM)")
    total = 0
    for addr, data in load_gpuram_patch(patch_id, patch_dir=patch_dir):
        bridge.write_domain("GPURAM", addr, data)
        total += len(data)
    return total
