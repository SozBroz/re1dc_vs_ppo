"""Verify execute_box_deposit_ui: withdraw ammo, bank knife + green herb."""
from __future__ import annotations

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
    close_box_ui,
    execute_box_deposit_ui,
    execute_box_withdraw_ui,
    probe_box_ui_open,
    _wait,
)
from re1_rl.memory_map import (  # noqa: E402
    INVENTORY_BASE,
    ITEM_BOX_BASE,
    ITEM_IDS,
    PLAYER_HP,
)

GREEN_HERB = 0x44
KNIFE = 0x01
CLIP = 0x0B
PORT = 5645


def _fmt(slots: list[tuple[int, int]]) -> str:
    return "[" + ", ".join(
        f"{i}:{ITEM_IDS.get(iid, hex(iid))}x{q}"
        for i, (iid, q) in enumerate(slots)
        if iid
    ) + "]"


def main() -> int:
    state = next(
        (ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State").glob(
            "*QuickSave0.State"
        )
    )
    rom = assert_rom_present()
    c = BizHawkClient(port=PORT, timeout=180.0, connect_timeout=90.0)
    c.start_server()
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(rom),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        c.wait_for_client()
        c.set_speed(200)
        c.load_savestate(str(state.resolve()))
        c.frameadvance(4)
        hp = int(c.read_ram([("hp", PLAYER_HP, "u16")])["hp"])
        if probe_box_ui_open(c):
            close_box_ui(c, prev_hp=hp, episode_start_hp=hp)
            _wait(c, frames=30, prev_hp=hp, episode_start_hp=hp)
        fields = [(f"b{i}", ITEM_BOX_BASE + i * 2, "u16", 0) for i in range(48)]
        fields += [(f"i{i}", INVENTORY_BASE + i * 2, "u16", 0) for i in range(8)]
        fields += [
            ("b0", ITEM_BOX_BASE, "u16", _encode_slot(CLIP, 15)),
            ("i0", INVENTORY_BASE, "u16", _encode_slot(KNIFE, 0)),
            ("i1", INVENTORY_BASE + 2, "u16", _encode_slot(GREEN_HERB, 1)),
        ]
        c.write_ram(fields)
        c.frameadvance(4)
        c.step(buttons={"cross": True}, n=12, abort_on_zero_hp=False)
        _wait(c, frames=90, prev_hp=hp, episode_start_hp=hp)
        assert probe_box_ui_open(c), "box UI not open"

        inv_c, box_c = 0, 0
        _d, _f, wd = execute_box_withdraw_ui(
            c, 0, prev_hp=hp, episode_start_hp=hp, inv_cursor=inv_c, box_cursor=box_c
        )
        print("withdraw", wd)
        assert wd.get("ok"), wd
        inv_c = int(wd["inv_cursor"])
        box_c = int(wd["box_cursor"])
        print("after wd", _fmt(read_inventory(c)), _fmt(read_box_live(c)))

        _d, _f, dep_k = execute_box_deposit_ui(
            c, 0, prev_hp=hp, episode_start_hp=hp, inv_cursor=inv_c, box_cursor=box_c
        )
        print("deposit knife", dep_k)
        assert dep_k.get("ok"), dep_k
        inv_c = int(dep_k["inv_cursor"])
        box_c = int(dep_k["box_cursor"])
        print("after knife", _fmt(read_inventory(c)), _fmt(read_box_live(c)))

        inv = read_inventory(c)
        herb_slot = next(i for i, (iid, _) in enumerate(inv) if iid == GREEN_HERB)
        _d, _f, dep_h = execute_box_deposit_ui(
            c,
            herb_slot,
            prev_hp=hp,
            episode_start_hp=hp,
            inv_cursor=inv_c,
            box_cursor=box_c,
        )
        print("deposit herb", dep_h)
        assert dep_h.get("ok"), dep_h
        inv = read_inventory(c)
        box = read_box_live(c)
        print("final", _fmt(inv), _fmt(box))
        assert any(iid == KNIFE for iid, _ in box), "knife not in box"
        assert any(iid == GREEN_HERB for iid, _ in box), "herb not in box"
        assert not any(iid == KNIFE for iid, _ in inv), "knife still in inv"
        assert not any(iid == GREEN_HERB for iid, _ in inv), "herb still in inv"
        print("PASS")
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
