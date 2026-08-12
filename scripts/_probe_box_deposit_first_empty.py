"""Live QA gates for box deposit integrity (pollution / allowlist).

Full deposit dest-slot matrix needs a warm non-empty-box QS; current QuickSave0
empty-box deposit is flaky even on the pre-change macro (2026-08-11). This probe
still enforces the hard contracts we can prove without a successful deposit.
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
    BOX_SLOTS_LIVE,
    _encode_slot,
    box_pollution_reason,
    can_deposit,
    read_box_live,
)
from re1_rl.item_box_ui_macro import (  # noqa: E402
    BOX_LIST_HOME_UPS,
    close_box_ui,
    execute_box_deposit_ui,
    probe_box_ui_open,
    _wait,
)
from re1_rl.memory_map import INVENTORY_BASE, ITEM_BOX_BASE, PLAYER_HP  # noqa: E402

KNIFE = 0x01
CLIP = 0x0B
CHEMICAL = 0x26


def _seed(
    c: BizHawkClient,
    *,
    inv: list[tuple[int, int]],
    box_live: list[tuple[int, int]],
) -> None:
    fields = [(f"b{i}", ITEM_BOX_BASE + i * 2, "u16", 0) for i in range(BOX_SLOTS_LIVE)]
    fields += [(f"i{i}", INVENTORY_BASE + i * 2, "u16", 0) for i in range(8)]
    for i, (iid, q) in enumerate(box_live[:BOX_SLOTS_LIVE]):
        fields.append((f"b{i}", ITEM_BOX_BASE + i * 2, "u16", _encode_slot(iid, q)))
    for i, (iid, q) in enumerate(inv[:8]):
        fields.append((f"i{i}", INVENTORY_BASE + i * 2, "u16", _encode_slot(iid, q)))
    c.write_ram(fields)
    c.frameadvance(4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5655)
    args = ap.parse_args()

    state = next(
        (ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State").glob(
            "*QuickSave0.State"
        )
    )
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
    try:
        c.wait_for_client()
        c.set_speed(300)
        c.load_savestate(str(state.resolve()))
        c.frameadvance(4)
        hp = int(c.read_ram([("hp", PLAYER_HP, "u16")])["hp"])

        assert BOX_LIST_HOME_UPS == 15

        # Deep pollution preflight must block deposit.
        deep = [(0, 0)] * 48
        deep[30] = (CLIP, 15)
        _seed(c, inv=[(KNIFE, 0)] + [(0, 0)] * 7, box_live=deep)
        if probe_box_ui_open(c):
            close_box_ui(c, prev_hp=hp, episode_start_hp=hp)
            _wait(c, frames=30, prev_hp=hp, episode_start_hp=hp)
        c.step(buttons={"cross": True}, n=12, abort_on_zero_hp=False)
        _wait(c, frames=90, prev_hp=hp, episode_start_hp=hp)
        assert probe_box_ui_open(c)
        before = read_box_live(c)
        assert box_pollution_reason(before) == "deep_box_item:handgun_bullets@30"
        _d, _f, rep = execute_box_deposit_ui(
            c, 0, prev_hp=hp, episode_start_hp=hp, room_id="100"
        )
        assert not rep.get("ok"), rep
        assert "deep_box_item" in str(rep.get("reason", "")), rep
        print("PASS deep_preflight_blocked", rep.get("reason"))

        # Chemical never allowlisted (policy).
        ok, reason = can_deposit(
            [(CHEMICAL, 1)] + [(0, 0)] * 7,
            [(0, 0)] * 16,
            0,
            room_id="118",
            enforce_allowlist=True,
        )
        assert not ok and reason == "not_allowlisted"
        print("PASS chemical_blocked")

        # Chemical in box is pollution.
        chem_box = [(0, 0)] * 48
        chem_box[0] = (CHEMICAL, 1)
        assert box_pollution_reason(chem_box) == "key_item_in_box:chemical@0"
        chem_deep = [(0, 0)] * 48
        chem_deep[40] = (CHEMICAL, 1)
        assert box_pollution_reason(chem_deep) == "key_item_in_box:chemical@40"
        print("PASS chemical_pollution")

        print("ALL_PASS")
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
