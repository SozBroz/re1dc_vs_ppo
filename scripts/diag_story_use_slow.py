"""Slow RAM-monitored story USE at save load pose (no turns, no warp).

  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\diag_story_use_slow.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\diag_story_use_slow.py --savestate path\\to.State
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import SELECT_SLOT_BASE, USE_ACTION
from re1_rl.bizhawk_paths import EMUHAWK, assert_rom_present, emuhawk_argv, newest_quicksave
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env
from re1_rl.inventory_menu_macro import execute_use_macro
from re1_rl.item_box import read_inventory
from re1_rl.memory_map import ITEM_IDS
from re1_rl.story_item_use import (
    legal_story_use_slots,
    matching_story_sites,
    read_story_use_probe,
    story_site_for_slot,
)

CUR = ROOT / "curriculum" / "m0_dining_to_main_hall.json"


def _fmt_probe(p: dict) -> str:
    return (
        f"scene=0x{int(p.get('scene_flag', 0)):02x} "
        f"msg=0x{int(p.get('msg_flag', 0)):02x} "
        f"gs=0x{int(p.get('game_state', 0)):08x} "
        f"ctrl={bool(p.get('in_control'))} "
        f"item_menu={bool(p.get('in_item_menu'))} "
        f"scene_active={bool(p.get('scene_active'))}"
    )


def _music_slot(
    inv: list[tuple[int, int]], *, room: str, x: int, z: int
) -> int | None:
    for slot in legal_story_use_slots(
        inv, room=room, x=x, z=z, rewarded_site_ids=set()
    ):
        if ITEM_IDS.get(inv[slot][0]) == "music_notes":
            return slot
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--savestate", default="", help="default: newest QuickSave")
    ap.add_argument("--port", type=int, default=5833)
    args = ap.parse_args()

    save = Path(args.savestate) if args.savestate else newest_quicksave()
    assert_rom_present()

    bridge = BizHawkClient(port=args.port, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        emuhawk_argv(port=args.port),
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        env = RE1Env(
            curriculum_path=CUR,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        env.reset()
        bridge.load_savestate(str(save))
        bridge.frameadvance(8)

        st = env._read_state()
        env._prev_state = st
        env._prev_hp = int(st.get("hp", 0))
        env._episode_start_hp = int(st.get("hp", 0))
        env._use_phase = 0
        inv = read_inventory(bridge)

        sites = [s["id"] for s in matching_story_sites(
            room=str(st["room_id"]),
            x=st["x"],
            z=st["z"],
            inventory=inv,
            rewarded_site_ids=set(),
        )]
        slot = _music_slot(
            inv, room=str(st["room_id"]), x=int(st["x"]), z=int(st["z"])
        )
        site = (
            story_site_for_slot(
                inv,
                int(slot),
                room=str(st["room_id"]),
                x=st["x"],
                z=st["z"],
                rewarded_site_ids=set(),
            )
            if slot is not None
            else None
        )

        print(f"ROM: {assert_rom_present()}")
        print(f"savestate: {save.name}")
        print(
            f"room={st['room_id']} pos=({st['x']},{st['z']}) facing={st['facing']} "
            f"(start pose — no turns)"
        )
        for i, (iid, qty) in enumerate(inv):
            if iid or qty:
                print(f"  inv[{i}] {ITEM_IDS.get(iid, iid)} x{qty}")
        print(f"story_sites={sites} music_notes_slot={slot}")
        print(f"baseline RAM: {_fmt_probe(read_story_use_probe(bridge))}")

        if slot is None or site is None:
            print("\nABORT: no music_notes story site at this load pose.")
            print("Stand at piano with notes x1, F1 QuickSave, re-run.")
            return 1

        print(f"\n--- slow macro: slot {slot} site {site['id']} ---")
        probe0 = read_story_use_probe(bridge)
        died, frames, report = execute_use_macro(
            bridge,
            int(slot),
            prev_hp=int(st.get("hp", 0)),
            episode_start_hp=int(st.get("hp", 0)),
            story_site=site,
        )
        print(f"macro frames={frames} died={died} report={report}")
        print(f"after RAM: {_fmt_probe(read_story_use_probe(bridge))}")
        inv2 = read_inventory(bridge)
        print(
            "notes after:",
            [(i, ITEM_IDS.get(iid, "?"), q) for i, (iid, q) in enumerate(inv2) if ITEM_IDS.get(iid) == "music_notes"],
        )

        if report.get("ok"):
            print("\nSUCCESS")
            return 0

        print("\n--- PPO path (use -> select_slot) for comparison ---")
        bridge.load_savestate(str(save))
        bridge.frameadvance(8)
        st = env._read_state()
        env._prev_state = st
        env._prev_hp = int(st.get("hp", 0))
        env._episode_start_hp = int(st.get("hp", 0))
        env._use_phase = 0
        print(f"baseline: {_fmt_probe(read_story_use_probe(bridge))}")
        _obs, _rew, _term, _trunc, info = env.step(USE_ACTION)
        print(f"after use: {info.get('magic_report')}")
        _obs, rew, _term, _trunc, info = env.step(SELECT_SLOT_BASE + int(slot))
        print(f"after slot: reward={rew:+.4f} macro={info.get('magic_report')}")
        print(f"final RAM: {_fmt_probe(read_story_use_probe(bridge))}")
        return 2 if not report.get("ok") else 0
    finally:
        try:
            bridge.quit()
        except (OSError, ConnectionError, RuntimeError):
            pass
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
