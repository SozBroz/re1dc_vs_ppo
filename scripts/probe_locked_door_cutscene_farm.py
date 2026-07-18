"""Live check: spam interact at 107->108 lockpick door — no cutscene farm.

  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_locked_door_cutscene_farm.py

Uses port 7792 (off fleet). Creates savestate if missing.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env
from re1_rl.memory_map import (
    CAM_ID,
    GAME_MODE,
    GAME_STATE,
    IN_CONTROL_MASK,
    MESSAGE_FLAG,
    PLAYER_FACING,
    PLAYER_HP,
    PLAYER_X,
    PLAYER_Y,
    PLAYER_Z,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
)
from re1_rl.reward import NEW_CUTSCENE_BONUS
from re1_rl.sticky_input import INTERACT_ACTION

FORWARD_ACTION = 1

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
FRESH = ROOT / "states" / "jill_control_fresh.State"
DOOR_STATE = ROOT / "states" / "jill_locked_door_107_108.State"
_IN_CONTROL_GAME_STATE = 0x80800004
_DOOR_POSE = {
    "stage_id": 0,
    "room_byte": 0x07,
    "x": 15089,
    "z": 2647,
    "facing": 3472,
    "cam_id": 2,
    "y": 0,
}


def _resync_env(env: RE1Env) -> dict:
    env._sticky_input.reset()
    env._frame_stack = []
    rgb = env.bridge.screenshot()
    frame_obs = env._push_frame(rgb)
    state = env._read_state()
    env._seed_episode_progress(state)
    env._episode_history.reset(str(state.get("room_id", "")), step=0)
    env._visited.reset()
    env._visited.update(state["room_id"], state["x"], state["z"])
    env._prev_state = state
    env._prev_hp = int(state["hp"])
    env._episode_start_hp = int(state["hp"])
    env._episode_min_hp = int(state["hp"])
    env._step_count = 0
    env._build_obs(frame_obs, state)
    return state


def _warp_to_door(bridge: BizHawkClient) -> None:
    pose = _DOOR_POSE
    bridge.write_ram(
        [
            ("stage_id", STAGE_ID, "u8", int(pose["stage_id"])),
            ("room_id", ROOM_ID, "u8", int(pose["room_byte"])),
            ("cam_id", CAM_ID, "u8", int(pose["cam_id"])),
            ("player_x", PLAYER_X, "s16", int(pose["x"])),
            ("player_y", PLAYER_Y, "s16", int(pose["y"])),
            ("player_z", PLAYER_Z, "s16", int(pose["z"])),
            ("player_facing", PLAYER_FACING, "u16", int(pose["facing"])),
            ("player_hp", PLAYER_HP, "u16", 96),
            ("game_mode", GAME_MODE, "u8", IN_CONTROL_MASK),
            ("game_state", GAME_STATE, "u32", _IN_CONTROL_GAME_STATE),
            ("scene_flag", SCENE_FLAG, "u8", 0x80),
            ("msg_flag", MESSAGE_FLAG, "u8", 0),
        ]
    )
    bridge.frameadvance(45)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7792)
    ap.add_argument("--spams", type=int, default=8)
    ap.add_argument("--approach-forward", action="store_true", help="tap forward before each interact")
    ap.add_argument("--speed", type=int, default=200)
    ap.add_argument(
        "--state",
        type=Path,
        default=DOOR_STATE,
        help="door savestate (warped from fresh if missing)",
    )
    args = ap.parse_args()

    bridge = BizHawkClient(
        port=args.port,
        timeout=180.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / f"_locked_door_probe_{args.port}.png"),
        screenshot_mmf=True,
    )
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={args.port}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    total_cutscene = 0.0
    try:
        bridge.wait_for_client()
        bridge.set_speed(args.speed)
        env = RE1Env(
            curriculum_path=str(CURRICULUM),
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=True,
        )
        env.reset()

        state_path = args.state
        if state_path.is_file():
            print(f"[door-farm] load savestate {state_path}", flush=True)
            bridge.load_savestate(str(state_path))
            bridge.frameadvance(8)
        else:
            if not FRESH.is_file():
                print(f"ERROR: missing {FRESH}", file=sys.stderr)
                return 1
            print(f"[door-farm] no door savestate — warp from {FRESH.name}", flush=True)
            bridge.load_savestate(str(FRESH))
            bridge.frameadvance(30)
            _warp_to_door(bridge)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            bridge.save_savestate(str(state_path))
            print(f"[door-farm] saved {state_path}", flush=True)

        state0 = _resync_env(env)
        print(
            f"[door-farm] ready room={state0.get('room_id')} "
            f"cam={state0.get('cam_id')} hp={state0.get('hp')} "
            f"scene=0x{int(state0.get('scene_flag', 0)):02X} "
            f"msg=0x{int(state0.get('msg_flag', 0)):02X}",
            flush=True,
        )

        saw_message_skip = False
        for i in range(int(args.spams)):
            if args.approach_forward:
                env.step(FORWARD_ACTION)
            obs, rew, term, trunc, info = env.step(INTERACT_ACTION)
            state = info.get("state") or {}
            bd = info.get("reward_breakdown") or {}
            cut = float(bd.get("new_cutscene", 0.0))
            total_cutscene += cut
            key = state.get("cutscene_key")
            frames = int(state.get("step_emulated_frames", 0) or 0)
            if frames >= 20:
                saw_message_skip = True
            print(
                f"[door-farm] spam={i} rew={rew:+.4f} "
                f"new_cutscene={cut:+.4f} cutscene_key={key!r} "
                f"room={state.get('room_id')} scene=0x{int(state.get('scene_flag', 0)):02X} "
                f"msg=0x{int(state.get('msg_flag', 0)):02X} frames={frames}",
                flush=True,
            )
            if term or trunc:
                print(f"[door-farm] episode ended term={term} trunc={trunc}", flush=True)
                break

        print(
            f"[door-farm] total new_cutscene={total_cutscene:+.4f} "
            f"(expect 0, bonus={NEW_CUTSCENE_BONUS}) "
            f"message_skip_seen={saw_message_skip}",
            flush=True,
        )
        if total_cutscene != 0.0:
            print("[door-farm] FAIL: cutscene farm still paying", flush=True)
            return 2
        print("[door-farm] PASS: no cutscene reward on door spam", flush=True)
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        bridge.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
