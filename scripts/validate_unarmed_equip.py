"""Validate legal unarmed equip still works after guards."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import EQUIP_ACTION, SELECT_SLOT_BASE
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env
from re1_rl.memory_map import EQUIPPED_WEAPON_ID, GAME_MODE

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools/BizHawk-2.11.1/EmuHawk.exe"
ROM = ROOT / "roms/Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua/re1_client.lua"
CURRICULUM = ROOT / "curriculum/m0_dining_to_main_hall.json"


def main() -> int:
    port = 7777
    bridge = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={port}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        env.reset()
        # Unequip via RAM (training may need this path after combine etc.)
        bridge.write_ram([("eq", EQUIPPED_WEAPON_ID, "u8", 0)])
        bridge.frameadvance(2)
        m = env.unwrapped.action_masks()
        assert m[EQUIP_ACTION], "equip should be legal when unarmed"
        assert not m[SELECT_SLOT_BASE + 1], "select slots only in equip phase 1"
        _, _, _, _, info1 = env.step(EQUIP_ACTION)
        assert info1.get("magic_report", {}).get("reason") == "equip_open"
        m2 = env.unwrapped.action_masks()
        assert m2[SELECT_SLOT_BASE + 1], "beretta slot pickable after equip_open"
        _, _, _, _, info2 = env.step(SELECT_SLOT_BASE + 1)
        report = info2.get("magic_report", {})
        ram = bridge.read_ram([("game_mode", GAME_MODE, "u8"), ("eq", EQUIPPED_WEAPON_ID, "u8")])
        mode = int(ram["game_mode"])
        eq = int(ram["eq"])
        print(
            f"equip_ok={report.get('ok')} reason={report.get('reason')} "
            f"frames={info2.get('state', {}).get('step_emulated_frames')} "
            f"eq=0x{eq:02X} gm=0x{mode:02X} in_control={bool(mode & 0x80)}",
            flush=True,
        )
        assert report.get("ok") is True
        assert eq == 0x02
        assert mode & 0x80
        print("PASS: unarmed -> beretta equip", flush=True)
    finally:
        try:
            bridge.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
