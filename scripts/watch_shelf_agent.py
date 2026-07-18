"""Watch trained policy from newest QuickSave (shelf room) — visible EmuHawk.

Logs when forward/run extends to 20f (pushable contact) and when push GS fires.
Port 7788 — does not touch the training fleet.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.async_fleet import load_async_learner
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.pushable import (
    FORWARD_ACTIONS,
    PUSH_GAME_STATE,
    PUSHABLE_HOLD_FRAMES,
    touching_pushable,
)

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
POLICY = ROOT / "data" / "ppo_re1_final_reward_tune_1040k.zip"
PORT = 7788
STEPS = 800


def newest_state() -> Path:
    return sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[0]


def main() -> int:
    state_path = newest_state()
    print(f"[watch-shelf] savestate={state_path.name}", flush=True)
    print(f"[watch-shelf] policy={POLICY.name}", flush=True)
    print(f"[watch-shelf] port={PORT}  (watch the EmuHawk window)", flush=True)

    if not POLICY.is_file():
        print(f"[watch-shelf] missing policy {POLICY}", flush=True)
        return 1

    bridge = BizHawkClient(port=PORT, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
    )
    try:
        print("[watch-shelf] waiting for EmuHawk...", flush=True)
        bridge.wait_for_client()
        bridge.set_speed(100)

        print("[watch-shelf] loading policy (may transplant)...", flush=True)
        model = load_async_learner(device="cpu", resume=POLICY, tb_log=None)

        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
            frame_skip=8,
        )
        obs, info = env.reset()
        # Drop into the shelf save (curriculum reset loads dining).
        bridge.load_savestate(str(state_path.resolve()))
        bridge.frameadvance(4)
        env.unwrapped._sticky_input.reset()
        env.unwrapped._forward_collision_stall = False
        state = env.unwrapped._read_state()
        env.unwrapped._prev_state = state
        env.unwrapped._prev_hp = int(state.get("hp", 0))
        env.unwrapped._step_count = 0
        # Rebuild obs from loaded pose
        rgb = bridge.screenshot()
        frame_obs = env.unwrapped._push_frame(rgb)
        obs = env.unwrapped._build_obs(frame_obs, state)
        print(
            f"[watch-shelf] loaded room={state.get('room_id')} "
            f"pos=({state.get('x')},{state.get('z')}) "
            f"facing={state.get('facing')} "
            f"gs=0x{int(state.get('game_state', 0)):08X} "
            f"anim=0x{int(state.get('player_anim', 0)):02X}",
            flush=True,
        )
        print(
            "[watch-shelf] running policy — look for PUSHABLE_HOLD / PUSH_START lines",
            flush=True,
        )

        for step in range(STEPS):
            masks = env.unwrapped.action_masks()
            try:
                action, _ = model.predict(
                    obs, action_masks=masks, deterministic=False
                )
            except TypeError:
                action, _ = model.predict(obs, deterministic=False)
            action = int(action)
            pre = env.unwrapped._prev_state or {}
            will_extend = (
                action in FORWARD_ACTIONS
                and touching_pushable(
                    pre,
                    forward_collision_stall=bool(
                        getattr(env.unwrapped, "_forward_collision_stall", False)
                    ),
                )
            )
            if will_extend:
                print(
                    f"[watch-shelf] step {step}: PUSHABLE_HOLD "
                    f"{ACTION_NAMES[action]} -> {PUSHABLE_HOLD_FRAMES}f "
                    f"(anim=0x{int(pre.get('player_anim', 0)):02X} "
                    f"stall={getattr(env.unwrapped, '_forward_collision_stall', False)} "
                    f"gs=0x{int(pre.get('game_state', 0)):08X})",
                    flush=True,
                )

            obs, rew, term, trunc, info = env.step(action)
            st = info.get("state") or {}
            gs = int(st.get("game_state", 0))
            frames = int(st.get("step_emulated_frames", 0))
            if gs == PUSH_GAME_STATE:
                print(
                    f"[watch-shelf] step {step}: PUSH_ACTIVE "
                    f"action={ACTION_NAMES[action]} frames={frames} "
                    f"pos=({st.get('x')},{st.get('z')}) "
                    f"anim=0x{int(st.get('player_anim', 0)):02X} "
                    f"rew={rew:+.3f}",
                    flush=True,
                )
            elif step % 25 == 0:
                print(
                    f"[watch-shelf] step {step}: {ACTION_NAMES[action]} "
                    f"frames={frames} room={st.get('room_id')} "
                    f"pos=({st.get('x')},{st.get('z')}) "
                    f"gs=0x{gs:08X} anim=0x{int(st.get('player_anim', 0)):02X} "
                    f"rew={rew:+.3f}",
                    flush=True,
                )

            if term or trunc:
                print(f"[watch-shelf] episode end at step {step}; stopping", flush=True)
                break

        print("[watch-shelf] done", flush=True)
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
