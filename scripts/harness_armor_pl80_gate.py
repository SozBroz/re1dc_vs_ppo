"""Offline harness: prove pl79/pl80 mint gates against real savestates.

Uses the same production detector as the fleet
(``armor_vent_step_complete`` / three OM-object mirrors).

  python scripts/harness_armor_pl80_gate.py
  python scripts/harness_armor_pl80_gate.py --strict   # exit 1 on any FAIL

What "PASS" means for each case is printed in the EXPECT column.
pl80 must be True only when BOTH statues sit on their vents.
"""
from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.armor_room_puzzle import (  # noqa: E402
    ARMOR_EAST_SCRIPT_TARGET,
    ARMOR_SCRIPT_TARGET_TOLERANCE,
    ARMOR_WEST_SCRIPT_TARGET,
    armor_stable_statues_seated,
    armor_vent_step_complete,
)
from re1_rl.bizhawk_paths import BIZHAWK_STATE_DIR  # noqa: E402
from re1_rl.memory_map import (  # noqa: E402
    ARMOR_EAST_STATUE_X,
    ARMOR_EAST_STATUE_X_B,
    ARMOR_EAST_STATUE_X_C,
    ARMOR_EAST_STATUE_Z,
    ARMOR_EAST_STATUE_Z_B,
    ARMOR_EAST_STATUE_Z_C,
    ARMOR_WEST_STATUE_X,
    ARMOR_WEST_STATUE_X_B,
    ARMOR_WEST_STATUE_X_C,
    ARMOR_WEST_STATUE_Z,
    ARMOR_WEST_STATUE_Z_B,
    ARMOR_WEST_STATUE_Z_C,
    PLAYER_X,
    PLAYER_Z,
    ROOM_ID,
    ps1_to_mainram_offset,
)
from re1_rl.yawn_cell_quality import find_mainram_base, load_core  # noqa: E402

PREFIX = "Resident Evil - Director's Cut (USA).Nymashock."
PL79_STEP = {"beat_id": "armor_vent_door", "site_id": "armor_vent_door"}
PL80_STEP = {"beat_id": "armor_vent_far", "site_id": "armor_vent_far"}

FIELDS: dict[str, int] = {
    "armor_east_statue_x": ARMOR_EAST_STATUE_X,
    "armor_east_statue_z": ARMOR_EAST_STATUE_Z,
    "armor_east_statue_x_b": ARMOR_EAST_STATUE_X_B,
    "armor_east_statue_z_b": ARMOR_EAST_STATUE_Z_B,
    "armor_east_statue_x_c": ARMOR_EAST_STATUE_X_C,
    "armor_east_statue_z_c": ARMOR_EAST_STATUE_Z_C,
    "armor_west_statue_x": ARMOR_WEST_STATUE_X,
    "armor_west_statue_z": ARMOR_WEST_STATUE_Z,
    "armor_west_statue_x_b": ARMOR_WEST_STATUE_X_B,
    "armor_west_statue_z_b": ARMOR_WEST_STATUE_Z_B,
    "armor_west_statue_x_c": ARMOR_WEST_STATUE_X_C,
    "armor_west_statue_z_c": ARMOR_WEST_STATUE_Z_C,
    "player_x": PLAYER_X,
    "player_z": PLAYER_Z,
}


@dataclass(frozen=True)
class Case:
    name: str
    path: Path
    # Expected (pl79_gate, pl80_gate). None = skip assert (informational).
    expect_pl79: bool | None
    expect_pl80: bool | None
    note: str = ""


def _s16(core: bytes, base: int, address: int) -> int:
    return struct.unpack_from("<h", core, base + ps1_to_mainram_offset(address))[0]


def _u8(core: bytes, base: int, address: int) -> int:
    return core[base + ps1_to_mainram_offset(address)]


def read_state(path: Path) -> dict[str, object]:
    core = load_core(path)
    base = find_mainram_base(core, expect_room="205") or find_mainram_base(core)
    if base is None:
        raise RuntimeError(f"no MainRAM in {path}")
    room = _u8(core, base, ROOM_ID)
    state: dict[str, object] = {"room_id": f"{room:03d}" if room < 1000 else str(room)}
    # ROOM_ID in RE1 is often stored as a packed id; prefer 205 when MainRAM
    # hunt already keyed on it.
    if find_mainram_base(core, expect_room="205") is not None:
        state["room_id"] = "205"
    for name, address in FIELDS.items():
        state[name] = _s16(core, base, address)
    state["x"] = state["player_x"]
    state["z"] = state["player_z"]
    return state


def _delta(xz: tuple[int, int], target: tuple[int, int]) -> tuple[int, int]:
    return xz[0] - target[0], xz[1] - target[1]


def _mirrors_ok(state: dict[str, object], prefix: str, target: tuple[int, int]) -> bool:
    for suffix in ("", "_b", "_c"):
        x = int(state.get(f"armor_{prefix}_statue_x{suffix}", 0) or 0)
        z = int(state.get(f"armor_{prefix}_statue_z{suffix}", 0) or 0)
        if abs(x - target[0]) > ARMOR_SCRIPT_TARGET_TOLERANCE:
            return False
        if abs(z - target[1]) > ARMOR_SCRIPT_TARGET_TOLERANCE:
            return False
    return True


def evaluate(path: Path) -> dict[str, object]:
    state = read_state(path)
    east = (
        int(state["armor_east_statue_x"]),
        int(state["armor_east_statue_z"]),
    )
    west = (
        int(state["armor_west_statue_x"]),
        int(state["armor_west_statue_z"]),
    )
    seated = armor_stable_statues_seated(state)
    pl79 = armor_vent_step_complete(PL79_STEP, state)
    pl80 = armor_vent_step_complete(PL80_STEP, state)
    return {
        "room": state["room_id"],
        "jill": (int(state["x"]), int(state["z"])),
        "east": east,
        "west": west,
        "east_d": _delta(east, ARMOR_EAST_SCRIPT_TARGET),
        "west_d": _delta(west, ARMOR_WEST_SCRIPT_TARGET),
        "east_mirrors": _mirrors_ok(state, "east", ARMOR_EAST_SCRIPT_TARGET),
        "west_mirrors": _mirrors_ok(state, "west", ARMOR_WEST_SCRIPT_TARGET),
        "seated": seated,
        "pl79": pl79,
        "pl80": pl80,
    }


def default_cases() -> list[Case]:
    qs = BIZHAWK_STATE_DIR
    tmp = ROOT / "_tmp"
    cells = ROOT / "states" / "planner_loyal" / "cells"
    return [
        Case(
            "qs0_both_human",
            qs / f"{PREFIX}QuickSave0.State",
            True,
            True,
            "human both-on-vents; MUST mint pl80",
        ),
        Case(
            "qs9_west_only",
            qs / f"{PREFIX}QuickSave9.State",
            False,
            False,
            "west seated, east still at rest; must NOT mint pl80",
        ),
        Case(
            "qs2_east_approach",
            qs / f"{PREFIX}QuickSave2.State",
            False,
            False,
            "neither seated",
        ),
        Case(
            "qs5_west_approach",
            qs / f"{PREFIX}QuickSave5.State",
            False,
            False,
            "neither seated",
        ),
        Case(
            "east_exact",
            tmp / "armor_corrected_field_probe" / "east_exact.State",
            True,
            False,
            "east only; pl79 yes, pl80 no",
        ),
        Case(
            "west_exact",
            tmp / "armor_west_object_probe" / "west_exact.State",
            False,
            False,
            "west only; pl80 must stay False",
        ),
        Case(
            "both_pre_button",
            tmp / "armor_both_vents_probe" / "both_pre_button.State",
            False,
            False,
            "probe overshot east by 400; correctly rejects pl80",
        ),
        Case(
            "false_pl79",
            tmp / "pl79_false_mint.State",
            False,
            False,
            "known bad mint; reject both gates",
        ),
        Case(
            "false_pl80",
            tmp / "pl80_false_mint.State",
            False,
            False,
            "known bad mint; reject both gates",
        ),
        Case(
            "live_pl78",
            cells / "pl78" / "cell.State",
            False,
            False,
            "armor room entry; neither seated",
        ),
        Case(
            "live_pl79",
            cells / "pl79" / "cell.State",
            True,
            False,
            "fleet-minted pl79; east must seat, pl80 must stay False",
        ),
        Case(
            "live_pl80",
            cells / "pl80" / "cell.State",
            True,
            True,
            "fleet-minted pl80 if present; both must seat",
        ),
    ]


def _fmt_bool(v: bool) -> str:
    return "YES" if v else "no "


def run(cases: list[Case], strict: bool) -> int:
    print(
        f"targets  east={ARMOR_EAST_SCRIPT_TARGET}  "
        f"west={ARMOR_WEST_SCRIPT_TARGET}  tol=+/-{ARMOR_SCRIPT_TARGET_TOLERANCE}"
    )
    print(
        f"{'case':<22} {'pl79':>4} {'pl80':>4}  "
        f"{'east xz':>16} {'dE':>10}  "
        f"{'west xz':>16} {'dW':>10}  result  note"
    )
    fails = 0
    skipped = 0
    for case in cases:
        if not case.path.is_file():
            print(f"{case.name:<22}  -- missing: {case.path}")
            skipped += 1
            continue
        try:
            row = evaluate(case.path)
        except Exception as exc:  # noqa: BLE001 — harness must keep going
            print(f"{case.name:<22}  ERROR {exc}")
            fails += 1
            continue

        ok_parts: list[str] = []
        bad = False
        if case.expect_pl79 is not None:
            if bool(row["pl79"]) == case.expect_pl79:
                ok_parts.append("pl79 ok")
            else:
                ok_parts.append(f"pl79 want={case.expect_pl79}")
                bad = True
        if case.expect_pl80 is not None:
            if bool(row["pl80"]) == case.expect_pl80:
                ok_parts.append("pl80 ok")
            else:
                ok_parts.append(f"pl80 want={case.expect_pl80}")
                bad = True
        result = "FAIL" if bad else "PASS"
        if bad:
            fails += 1
        east = row["east"]
        west = row["west"]
        print(
            f"{case.name:<22} {_fmt_bool(bool(row['pl79'])):>4} "
            f"{_fmt_bool(bool(row['pl80'])):>4}  "
            f"{str(east):>16} {str(row['east_d']):>10}  "
            f"{str(west):>16} {str(row['west_d']):>10}  "
            f"{result:<5}  {case.note or '; '.join(ok_parts)}"
        )
        if bad or case.name in {"live_pl79", "qs0_both_human", "both_pre_button"}:
            print(
                f"{'':22} mirrors east={row['east_mirrors']} "
                f"west={row['west_mirrors']}  "
                f"seated={row['seated']}  jill={row['jill']}  room={row['room']}"
            )

    print()
    print(
        f"done: fails={fails} skipped={skipped} "
        f"(skipped usually means pl80 not minted yet)"
    )
    if strict and fails:
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any expected gate result mismatches",
    )
    ap.add_argument(
        "extra",
        nargs="*",
        help="optional extra .State paths (informational, no expected)",
    )
    args = ap.parse_args()
    cases = default_cases()
    for raw in args.extra:
        path = Path(raw)
        cases.append(Case(path.stem, path, None, None, "extra"))
    return run(cases, strict=bool(args.strict))


if __name__ == "__main__":
    raise SystemExit(main())
