"""Hunt authentic RE1 DC box-UI deposit sequences on QuickSave0.

Withdraw scaffolding (empty inv → Cross → box item → Cross) is known-good.
Deposit should be the inverse: occupied inv → Cross → empty box (-Nothing-) → Cross,
or Cross on occupied may open exchange onto a box slot.

Does not touch checkpoint cells. Seeds inv/box via RAM before open only.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import EMUHAWK, LUA, assert_rom_present  # noqa: E402
from re1_rl.item_box import (  # noqa: E402
    _encode_slot,
    read_box_live,
    read_inventory,
)
from re1_rl.item_box_ui_macro import (  # noqa: E402
    _confirm_cross,
    _move,
    _navigate_inventory,
    _wait,
    execute_box_withdraw_ui,
    probe_box_ui_open,
)
from re1_rl.memory_map import (  # noqa: E402
    GAME_MODE,
    GAME_STATE,
    INVENTORY_BASE,
    ITEM_BOX_BASE,
    ITEM_IDS,
    PLAYER_HP,
)

GREEN_HERB = next(k for k, v in ITEM_IDS.items() if v == "green_herb")
CLIP = 0x0B


def _fmt(slots: list[tuple[int, int]], *, live: bool = False) -> str:
    out = []
    for i, (iid, q) in enumerate(slots):
        if iid:
            out.append(f"{i}:{ITEM_IDS.get(iid, hex(iid))}x{q}")
    return "[" + ", ".join(out) + "]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5640)
    ap.add_argument("--speed", type=int, default=200)
    ap.add_argument(
        "--state",
        type=Path,
        default=None,
        help="QS0-like state facing the box (default: BizHawk QuickSave0)",
    )
    args = ap.parse_args()
    state = args.state
    if state is None:
        state = next(
            (ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State").glob(
                "*QuickSave0.State"
            )
        )
    out = ROOT / "data" / "box_deposit_hunt"
    out.mkdir(parents=True, exist_ok=True)

    rom = assert_rom_present()
    c = BizHawkClient(port=args.port, timeout=180.0, connect_timeout=90.0)
    c.start_server()
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(rom),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={args.port}",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shot_n = 0

    def shot(tag: str) -> None:
        nonlocal shot_n
        shot_n += 1
        path = out / f"{shot_n:02d}_{tag}.png"
        c.frameadvance(2)
        c._request({"cmd": "screenshot", "path": str(path.resolve())})
        ram = c.read_ram(
            [
                ("gm", GAME_MODE, "u8"),
                ("gs", GAME_STATE, "u32"),
            ]
        )
        print(
            f"SHOT {path.name} open={probe_box_ui_open(c)} "
            f"gm={int(ram['gm']):02x} gs={int(ram['gs']):08x} "
            f"inv={_fmt(read_inventory(c))} box={_fmt(read_box_live(c))}",
            flush=True,
        )

    def clear_all() -> None:
        fields = [(f"b{i}", ITEM_BOX_BASE + i * 2, "u16", 0) for i in range(48)]
        fields += [(f"i{i}", INVENTORY_BASE + i * 2, "u16", 0) for i in range(8)]
        c.write_ram(fields)
        c.frameadvance(2)

    def open_box(hp: int) -> None:
        c.step(buttons={"cross": True}, n=12, abort_on_zero_hp=False)
        c.step(buttons={}, n=100, abort_on_zero_hp=False)
        _wait(c, frames=90, prev_hp=hp, episode_start_hp=hp)

    def press(btn: str, hold: int = 8, settle: int = 55) -> None:
        c.step(buttons={btn: True}, n=hold, abort_on_zero_hp=False)
        c.step(buttons={}, n=settle, abort_on_zero_hp=False)

    def run_case(name: str, steps: list[str], *, warm_withdraw: bool) -> None:
        nonlocal shot_n
        shot_n = 0
        case_dir_tag = name
        print(f"\n=== {name} warm_withdraw={warm_withdraw} ===", flush=True)
        c.load_savestate(str(state.resolve()))
        c.frameadvance(6)
        clear_all()
        # Empty inv slot0 for withdraw dest; herb at slot1; optional clip in box.
        fields = [("i1", INVENTORY_BASE + 2, "u16", _encode_slot(GREEN_HERB, 1))]
        if warm_withdraw:
            fields.append(("b0", ITEM_BOX_BASE, "u16", _encode_slot(CLIP, 15)))
        c.write_ram(fields)
        c.frameadvance(4)
        hp = int(c.read_ram([("hp", PLAYER_HP, "u16")])["hp"])
        open_box(hp)
        shot(f"{case_dir_tag}_open")
        inv_cursor = 0
        if warm_withdraw:
            died, _f, report = execute_box_withdraw_ui(
                c, 0, prev_hp=hp, episode_start_hp=hp, inv_cursor=0
            )
            print("withdraw", report, flush=True)
            if not report.get("ok"):
                shot(f"{case_dir_tag}_wd_fail")
                return
            inv_cursor = int(report.get("inv_cursor") or 0)
            shot(f"{case_dir_tag}_after_wd")
        # Cursor onto herb @1
        _navigate_inventory(
            c, inv_cursor, 1, prev_hp=hp, episode_start_hp=hp
        )
        shot(f"{case_dir_tag}_on_herb")
        inv_b = read_inventory(c)
        box_b = read_box_live(c)
        for i, btn in enumerate(steps):
            if btn in ("up", "down", "left", "right"):
                _move(c, btn, prev_hp=hp, episode_start_hp=hp)
            elif btn == "cross":
                _confirm_cross(c, prev_hp=hp, episode_start_hp=hp)
            else:
                press(btn)
            shot(f"{case_dir_tag}_s{i}_{btn}")
        inv_a = read_inventory(c)
        box_a = read_box_live(c)
        moved = inv_a != inv_b or box_a != box_b
        herb_in_box = any(iid == GREEN_HERB for iid, _ in box_a)
        print(
            f"RESULT {name}: moved={moved} herb_in_box={herb_in_box} "
            f"inv={_fmt(inv_a)} box={_fmt(box_a)}",
            flush=True,
        )

    try:
        c.wait_for_client()
        c.set_speed(int(args.speed))

        # A) Empty box — deposit should land herb into -Nothing-
        for name, steps in [
            ("empty_XX", ["cross", "cross"]),
            ("empty_X_down_X", ["cross", "down", "cross"]),
            ("empty_X_up_X", ["cross", "up", "cross"]),
            ("empty_square", ["square"]),
            ("empty_X_square", ["cross", "square"]),
            ("empty_circle", ["circle"]),
            ("empty_right_X", ["right", "cross"]),
            ("empty_X_right_X", ["cross", "right", "cross"]),
        ]:
            run_case(name, steps, warm_withdraw=False)

        # B) After withdraw (empty box, warm UI) — same sequences
        for name, steps in [
            ("warm_XX", ["cross", "cross"]),
            ("warm_X_down_X", ["cross", "down", "cross"]),
            ("warm_square", ["square"]),
            ("warm_X_square", ["cross", "square"]),
        ]:
            run_case(name, steps, warm_withdraw=True)

        # C) Exchange onto occupied box: seed clip AFTER withdrawing? 
        #    Seed clip in box, herb in inv, no withdraw — Cross on herb then
        #    Cross on clip should swap (known exchange behavior).
        print("\n=== exchange_herb_onto_clip ===", flush=True)
        c.load_savestate(str(state.resolve()))
        c.frameadvance(6)
        clear_all()
        c.write_ram(
            [
                ("b0", ITEM_BOX_BASE, "u16", _encode_slot(CLIP, 15)),
                ("i0", INVENTORY_BASE, "u16", _encode_slot(GREEN_HERB, 1)),
            ]
        )
        c.frameadvance(4)
        hp = int(c.read_ram([("hp", PLAYER_HP, "u16")])["hp"])
        open_box(hp)
        shot("ex_open")
        # On herb @0 already
        _confirm_cross(c, prev_hp=hp, episode_start_hp=hp)
        shot("ex_after_X1")
        _confirm_cross(c, prev_hp=hp, episode_start_hp=hp)
        shot("ex_after_X2")
        print(
            f"RESULT exchange: inv={_fmt(read_inventory(c))} "
            f"box={_fmt(read_box_live(c))}",
            flush=True,
        )

        print(f"\nShots under {out}", flush=True)
        return 0
    finally:
        try:
            c.quit()
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
