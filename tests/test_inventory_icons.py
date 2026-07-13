"""Unit tests for GPURAM inventory icon patch loader (no emulator)."""

from __future__ import annotations

from pathlib import Path

import pytest

from re1_rl.inventory_icons import (
    PATCH_CLIP_INTO_SLOT0_FROM_KNIFE_QS5,
    apply_gpuram_icon_patch,
    load_gpuram_patch,
    patch_paths,
)


def test_clip_slot0_patch_files_exist_and_parse():
    idx, blob = patch_paths(PATCH_CLIP_INTO_SLOT0_FROM_KNIFE_QS5)
    assert idx.is_file(), idx
    assert blob.is_file(), blob
    runs = load_gpuram_patch(PATCH_CLIP_INTO_SLOT0_FROM_KNIFE_QS5)
    assert len(runs) > 10
    total = sum(len(d) for _, d in runs)
    assert total == blob.stat().st_size
    assert total == 5036


def test_apply_gpuram_icon_patch_writes_domain():
    writes: list[tuple[str, int, int]] = []

    class _Bridge:
        def write_domain(self, domain: str, address: int, data):
            writes.append((domain, address, len(data)))

    n = apply_gpuram_icon_patch(_Bridge())
    assert n == 5036
    assert writes
    assert all(d == "GPURAM" for d, _, _ in writes)
