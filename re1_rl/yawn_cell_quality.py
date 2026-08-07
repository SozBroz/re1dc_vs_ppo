"""Recompute yawn cell quality from cell.State + sidecar (inventory + box)."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import zstandard as zstd

from re1_rl.go_explore_capture import compute_quality
from re1_rl.memory_map import (
    INVENTORY_BASE,
    INVENTORY_SLOTS,
    ITEM_IDS,
    PLAYER_HP,
    ROOM_ID,
    STAGE_ID,
    ps1_to_mainram_offset,
)

HP_OFF = ps1_to_mainram_offset(PLAYER_HP)
INV_OFF = ps1_to_mainram_offset(INVENTORY_BASE)
STAGE_OFF = ps1_to_mainram_offset(STAGE_ID)
ROOM_OFF = ps1_to_mainram_offset(ROOM_ID)


def load_core(state_path: Path) -> bytes:
    with zipfile.ZipFile(state_path, "r") as zf:
        return zstd.ZstdDecompressor().decompress(
            zf.read("Core.bin.zst"), max_output_size=64 * 1024 * 1024
        )


def _slots_at(core: bytes, base: int) -> list[tuple[str, int]]:
    inv = core[base + INV_OFF : base + INV_OFF + INVENTORY_SLOTS * 2]
    out: list[tuple[str, int]] = []
    for i in range(INVENTORY_SLOTS):
        item_id, qty = inv[i * 2], inv[i * 2 + 1]
        if not item_id:
            out.append(("", 0))
            continue
        name = ITEM_IDS.get(item_id, f"unknown_0x{item_id:02X}")
        out.append((name, int(qty)))
    return out


def find_mainram_base(core: bytes, *, expect_room: str | None = None) -> int | None:
    best: tuple[int, int] | None = None
    limit = len(core) - 0xC8800
    for base in range(0, max(0, limit), 4):
        hp = core[base + HP_OFF] | (core[base + HP_OFF + 1] << 8)
        if not (1 <= hp <= 140):
            continue
        slots = _slots_at(core, base)
        occupied = [(n, q) for n, q in slots if n]
        if not occupied:
            continue
        if any(q > 99 for _n, q in occupied):
            continue
        if any(n.startswith("unknown_") for n, _q in occupied):
            continue
        names = [n for n, _q in occupied]
        if len(names) != len(set(names)) and not (
            names.count("handgun_bullets") <= 2
        ):
            if any(names.count(n) > 1 for n in names if n not in ("handgun_bullets",)):
                continue
        score = 0
        if hp == 96:
            score += 6
        elif hp >= 70:
            score += 2
        if "knife" in names:
            score += 3
        if "beretta" in names:
            score += 3
        if any(n in names for n in ("handgun_bullets", "shotgun_shells", "shield_key")):
            score += 1
        stage = core[base + STAGE_OFF]
        room = core[base + ROOM_OFF]
        if stage > 5:
            continue
        rid = f"{stage + 1}{room:02X}"
        if expect_room and rid.upper() == expect_room.upper():
            score += 8
        if best is None or score > best[0]:
            best = (score, base)
    return None if best is None or best[0] < 6 else best[1]


def compute_quality_from_cell(
    state_path: Path | str,
    sidecar: dict[str, Any],
) -> dict[str, Any]:
    """Return ``{ok, quality, hp, room_id, ...}`` from live State + sidecar box."""
    path = Path(state_path)
    core = load_core(path)
    expect_room = str(
        sidecar.get("captured_room_id") or sidecar.get("room_id") or ""
    ) or None
    base = find_mainram_base(core, expect_room=expect_room)
    if base is None:
        return {"ok": False, "error": "mainram_base_not_found"}
    hp = core[base + HP_OFF] | (core[base + HP_OFF + 1] << 8)
    stage = core[base + STAGE_OFF]
    room = core[base + ROOM_OFF]
    slots = _slots_at(core, base)
    ever_held = None
    if isinstance(sidecar.get("ever_held"), list):
        ever_held = [str(x) for x in sidecar["ever_held"]]
    state = {
        "hp": hp,
        "inventory_slots": [(n, q) for n, q in slots if n],
        "room_id": f"{stage + 1}{room:02X}",
        "box_cache": sidecar.get("box_cache"),
    }
    q = list(compute_quality(state, ever_held=ever_held))
    return {
        "ok": True,
        "hp": hp,
        "room_id": state["room_id"],
        "quality": q,
        "state_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def refresh_yawn_quality_metadata(project_root: Path | str) -> list[dict[str, Any]]:
    """Update manifest, store.json, and meta.json quality fields in place."""
    from re1_rl.go_explore_archive import quality_beats
    from re1_rl.yawn_rails_sync import MANIFEST_FILENAME, cell_dir_name, yawn_rails_root

    root = Path(project_root)
    yroot = yawn_rails_root(root)
    man_path = yroot / MANIFEST_FILENAME
    store_path = yroot / "store.json"
    man = json.loads(man_path.read_text(encoding="utf-8-sig"))
    rows = list(man.get("cells") or [])
    changes: list[dict[str, Any]] = []

    for row in sorted(rows, key=lambda r: int(r["checkpoint_index"])):
        idx = int(row["checkpoint_index"])
        cell_dir = yroot / "cells" / cell_dir_name(idx)
        state_p = cell_dir / "cell.State"
        side_p = cell_dir / "cell.sidecar.json"
        meta_p = cell_dir / "meta.json"
        if not state_p.is_file() or not side_p.is_file():
            changes.append({"idx": idx, "error": "missing bundle"})
            continue
        sidecar = json.loads(side_p.read_text(encoding="utf-8-sig"))
        info = compute_quality_from_cell(state_p, sidecar)
        if not info.get("ok"):
            changes.append({"idx": idx, "error": info.get("error")})
            continue
        old_q = list(row.get("quality") or [])
        new_q = list(info["quality"])
        row["quality"] = new_q
        if meta_p.is_file():
            meta = json.loads(meta_p.read_text(encoding="utf-8-sig"))
            meta["quality"] = new_q
            meta_p.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        changes.append(
            {
                "idx": idx,
                "checkpoint_id": row.get("checkpoint_id"),
                "old_ammo": old_q[1] if len(old_q) > 1 else None,
                "new_ammo": new_q[1],
                "old_q": old_q,
                "new_q": new_q,
                "beats_self": quality_beats(new_q, old_q),
                "old_beats_new": quality_beats(old_q, new_q),
            }
        )

    man_path.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")

    if store_path.is_file():
        store = json.loads(store_path.read_text(encoding="utf-8-sig"))
        by_idx = {int(r["checkpoint_index"]): r for r in rows}
        for key, cell in (store.get("cells") or {}).items():
            try:
                idx = int(key)
            except ValueError:
                continue
            row = by_idx.get(idx)
            if row and row.get("quality"):
                cell["quality"] = list(row["quality"])
        store_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")

    return changes
