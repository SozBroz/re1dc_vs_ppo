"""Gold-path probe: QuickSave1 at Main Hall typewriter, tap cross save sequence."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.ingame_save import tap
from re1_rl.memory_map import GAME_MODE, GAME_STATE
from re1_rl.typewriter_save import count_ink_ribbons
from scripts.probe_typewriter_save_slot1 import _load_savestate_file

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = (
    ROOT
    / "tools/BizHawk-2.11.1/PSX/State/Resident Evil - Director's Cut (USA).Nymashock.QuickSave1.State"
)
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"


def _mode_gs(bridge: BizHawkClient) -> tuple[int, int]:
    ram = bridge.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
        ]
    )
    return int(ram["game_mode"]), int(ram["game_state"])


def _snap(env: RE1Env, label: str) -> dict:
    st = env._read_state(track_items=True)
    mode, gs = _mode_gs(env.bridge)
    print(
        f"{label}: room={st.get('room_id')} pos=({st.get('x')},{st.get('z')}) "
        f"ctrl={st.get('in_control')} rib={count_ink_ribbons(st)} "
        f"mode=0x{mode:02X} gs=0x{gs:08X}",
        flush=True,
    )
    return st


def main() -> int:
    port = 7799
    bridge = BizHawkClient(port=port, timeout=180.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            frame_skip=1,
            project_root=ROOT,
        )
        _load_savestate_file(
            env,
            STATE,
            meta_path=None,
            cutscene_speed=6400,
            skip_uncontrolled=False,
        )
        prev = _snap(env, "load")

        interact = ACTION_NAMES.index("interact")
        turn_r = ACTION_NAMES.index("turn_right")
        turn_l = ACTION_NAMES.index("turn_left")
        forward = ACTION_NAMES.index("forward")

        # Face typewriter: small turn sweep then one forward tap.
        for action in [turn_r] * 4 + [turn_l] * 8 + [forward]:
            env.step(action)
            _snap(env, f"nav {ACTION_NAMES[action]}")

        # Env interact steps (short hold at frame_skip=1).
        for i in range(12):
            _obs, reward, _t, _tr, info = env.step(interact)
            st = env._read_state(track_items=True)
            tw = float((info.get("reward_breakdown") or {}).get("typewriter_save", 0))
            det = env._typewriter_save_detector
            print(
                f"interact[{i}] r={reward:+.4f} tw={tw:.3f} rib={count_ink_ribbons(st)} "
                f"pend={getattr(det, '_pending', False)} ctrl={st.get('in_control')}",
                flush=True,
            )
            if tw > 0 or count_ink_ribbons(st) < count_ink_ribbons(prev):
                break

        # Raw cross tap sequence (Save -> slot -> Yes).
        prev = env._read_state(track_items=True)
        cur = prev
        for i in range(8):
            tap(bridge, "cross", hold=2, release=40)
            cur = env._read_state(track_items=True)
            complete = env._typewriter_save_detector.update(prev, cur)
            mode, gs = _mode_gs(bridge)
            print(
                f"tap[{i}] rib={count_ink_ribbons(prev)}->{count_ink_ribbons(cur)} "
                f"ctrl={cur.get('in_control')} complete={complete} "
                f"mode=0x{mode:02X} gs=0x{gs:08X}",
                flush=True,
            )
            if complete:
                print("DETECTOR COMPLETE", flush=True)
                break
            prev = cur

        # Mash cross through save cinema.
        for i in range(200):
            tap(bridge, "cross", hold=1, release=4)
            cur = env._read_state(track_items=True)
            complete = env._typewriter_save_detector.update(prev, cur)
            if (
                i < 15
                or complete
                or count_ink_ribbons(cur) < count_ink_ribbons(prev)
                or not cur.get("in_control")
            ):
                print(
                    f"mash[{i}] rib={count_ink_ribbons(cur)} ctrl={cur.get('in_control')} "
                    f"pend={env._typewriter_save_detector._pending} complete={complete}",
                    flush=True,
                )
            if complete:
                break
            prev = cur

        out = {
            "final_ribbons": count_ink_ribbons(cur),
            "detector_completed": env._typewriter_save_detector.completed_room,
        }
        path = ROOT / "data" / "_probe_tw_goldpath.json"
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"wrote {path}", flush=True)
        return 0 if out["detector_completed"] else 1
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        bridge.close()
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
