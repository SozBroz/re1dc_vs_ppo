"""Regenerate static world-almanac JSON consumed by WorldCatalog.

Writes:
  data/room_areas.json
  data/item_categories.json
  data/er_files.json
  data/combine_recipes.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from re1_rl.herb_combine import (  # noqa: E402
    BLUE,
    GREEN,
    MIXED_GB,
    MIXED_GG,
    MIXED_GGB,
    MIXED_GGG,
    MIXED_GR,
    MIXED_GRB,
    RED,
    _COMBINE_TABLE,
    _DECOMPOSE,
)
from re1_rl.item_todo import canonical_item  # noqa: E402
from re1_rl.key_items import KEY_ITEM_NAMES  # noqa: E402
from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS  # noqa: E402

DATA = _ROOT / "data"

AREA_ENUM: dict[str, int] = {
    "mansion_1f": 0,
    "mansion_2f": 1,
    "mansion_b1": 2,
    "courtyard": 3,
    "underground": 4,
    "guardhouse_1f": 5,
    "guardhouse_b1": 6,
    "lab_b1": 7,
    "lab_b2": 8,
    "lab_b3": 9,
    "lab_b4": 10,
}

COURTYARD_ROOMS = frozenset({"300", "301", "302", "303", "304"})
GUARDHOUSE_B1 = frozenset({"40D", "40E", "40F", "410", "411"})
MANSION_B1 = frozenset({"21A", "21B", "21C"})


def _name_to_id(name: str) -> int:
    for iid, iname in ITEM_IDS.items():
        if iname == name:
            return int(iid)
    return 0


def area_for_room(code: str) -> tuple[int, str]:
    if code in MANSION_B1:
        return AREA_ENUM["mansion_b1"], "mansion_b1"
    lead = code[0]
    if lead == "1":
        return AREA_ENUM["mansion_1f"], "mansion_1f"
    if lead == "2":
        return AREA_ENUM["mansion_2f"], "mansion_2f"
    if lead == "3":
        if code in COURTYARD_ROOMS:
            return AREA_ENUM["courtyard"], "courtyard"
        return AREA_ENUM["underground"], "underground"
    if lead == "4":
        if code in GUARDHOUSE_B1:
            return AREA_ENUM["guardhouse_b1"], "guardhouse_b1"
        return AREA_ENUM["guardhouse_1f"], "guardhouse_1f"
    if lead == "5":
        val = int(code, 16)
        if val <= 0x503:
            return AREA_ENUM["lab_b1"], "lab_b1"
        if val <= 0x50A:
            return AREA_ENUM["lab_b2"], "lab_b2"
        if val <= 0x50F:
            return AREA_ENUM["lab_b3"], "lab_b3"
        return AREA_ENUM["lab_b4"], "lab_b4"
    return AREA_ENUM["mansion_1f"], "mansion_1f"


def build_room_areas() -> dict[str, dict[str, object]]:
    rooms = json.loads((DATA / "rooms.json").read_text(encoding="utf-8"))
    out: dict[str, dict[str, object]] = {}
    for code in sorted(rooms):
        area_id, area_name = area_for_room(code)
        out[code] = {"area_id": area_id, "area_name": area_name}
    return out


def _category_for(name: str, *, key_item: bool) -> str:
    if key_item or name in KEY_ITEM_NAMES:
        return "key"
    if name in {
        "red_herb",
        "green_herb",
        "blue_herb",
        "mixed_herbs_gr",
        "mixed_herbs_gg",
        "mixed_herbs_gb",
        "mixed_herbs_grb",
        "mixed_herbs_ggg",
        "mixed_herbs_ggb",
        "first_aid_spray_alt",
        "serum",
    }:
        return "recovery"
    iid = _name_to_id(name)
    if iid in WEAPON_ITEM_IDS:
        return "weapon"
    if name.endswith("_rounds") or name.endswith("_shells") or name.endswith("_bullets") or name in {
        "handgun_bullets",
        "flamethrower_fuel",
        "dumdum_rounds",
        "magnum_rounds",
        "explosive_rounds",
        "acid_rounds",
        "flame_rounds",
    }:
        return "ammo"
    if name.startswith("map_") or name.endswith("_book") or name.endswith("_diary") or name in {
        "orders",
        "scrapbook",
        "researchers_will",
        "slides",
        "pass_number",
        "passcode_a",
        "passcode_b",
        "ink_ribbon",
        "plant_42_report",
        "v_jolt_report",
        "botany_book",
        "keepers_diary",
        "red_book",
        "doom_book_1",
        "doom_book_2",
    }:
        return "file"
    return "misc"


def build_item_categories() -> dict[str, str]:
    room_items = json.loads((DATA / "room_items.json").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for room_id, block in room_items.items():
        if room_id.startswith("_"):
            continue
        for row in block.get("items", []):
            name = canonical_item(str(row.get("name", "")))
            if not name:
                continue
            out[name] = _category_for(name, key_item=bool(row.get("key_item")))
    return dict(sorted(out.items()))


def build_er_files() -> list[dict[str, object]]:
    """Numeric codes for files the policy may need to enter (best-effort)."""
    return [
        {
            "name": "pass_number",
            "room": "404",
            "codes": [3.0, 4.0, 5.0],
            "notes": "Jill rec-room pool table (Chris gets this from Barry)",
        },
        {
            "name": "pass_number",
            "room": "304",
            "codes": [3.0, 4.0, 5.0],
            "notes": "Guardhouse gate keypad uses same pool pass number",
        },
        {
            "name": "researchers_will",
            "room": "20A",
            "codes": [74.0, 79.0, 72.0, 78.0],
            "notes": "ASCII ordinals for login JOHN (lab computer username)",
        },
        {
            "name": "researchers_will",
            "room": "20A",
            "codes": [65.0, 68.0, 65.0],
            "notes": "ASCII ordinals for password ADA (visual data room)",
        },
        {
            "name": "slides",
            "room": "510",
            "codes": [77.0, 79.0, 76.0, 69.0],
            "notes": "ASCII ordinals for MOLE (B3 electronic lock password)",
        },
        {
            "name": "passcode_a",
            "room": "508",
            "codes": [],
            "notes": "MO disc reader output — three digits vary by disc slot; not a fixed pickup code",
        },
        {
            "name": "passcode_b",
            "room": "508",
            "codes": [],
            "notes": "Second MO disc reader output — dynamic three-digit code",
        },
    ]


def _counts_to_item_id(counts: tuple[int, int, int]) -> int:
    for iid, deco in _DECOMPOSE.items():
        if deco == counts:
            return int(iid)
    raise KeyError(counts)


def build_combine_recipes() -> list[dict[str, object]]:
    recipes: list[dict[str, object]] = []
    for counts, dst in _COMBINE_TABLE.items():
        src_a = _counts_to_item_id(counts)
        # Represent pair as two base herb ids when possible (order-independent).
        herbs = []
        r, g, b = counts
        herbs.extend([RED] * r)
        herbs.extend([GREEN] * g)
        herbs.extend([BLUE] * b)
        if len(herbs) != 2:
            # Triple mixes: document as chained pairs elsewhere; skip direct pair.
            continue
        a, b = herbs[0], herbs[1]
        recipes.append({"a": a, "b": b, "dst": int(dst), "kind": "herb"})

    # Triple herb mixes (two-step in menu; static graph lists final totals).
    for counts, dst in _COMBINE_TABLE.items():
        if sum(counts) != 3:
            continue
        mid = None
        if counts == (0, 3, 0):
            mid = MIXED_GG
            recipes.append({"a": GREEN, "b": MIXED_GG, "dst": int(dst), "kind": "herb"})
        elif counts == (0, 2, 1):
            mid = MIXED_GB
            recipes.append({"a": GREEN, "b": MIXED_GB, "dst": int(dst), "kind": "herb"})
        elif counts == (1, 1, 1):
            mid = MIXED_GR
            recipes.append({"a": BLUE, "b": MIXED_GR, "dst": int(dst), "kind": "herb"})
        _ = mid

    chem = [
        ("yellow_6", "umb_no2", "n_p003"),
        ("umb_no4", "water", "umb_no4"),
        ("umb_no7", "umb_no4", "umb_no7"),
        ("umb_no13", "umb_no7", "v_jolt"),
    ]
    for a_name, b_name, dst_name in chem:
        recipes.append(
            {
                "a": _name_to_id(a_name),
                "b": _name_to_id(b_name),
                "dst": _name_to_id(dst_name),
                "kind": "chem",
            }
        )
    return recipes


def main() -> None:
    outputs = {
        "room_areas.json": build_room_areas(),
        "item_categories.json": build_item_categories(),
        "er_files.json": build_er_files(),
        "combine_recipes.json": build_combine_recipes(),
    }
    for fname, payload in outputs.items():
        path = DATA / fname
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
