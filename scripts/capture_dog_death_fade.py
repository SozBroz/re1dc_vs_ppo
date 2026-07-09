"""Load a savestate and auto-advance until dog-death white fade; log RAM + cutscene gating."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
DEFAULT_STATE = (
    ROOT
    / "tools"
    / "BizHawk-2.11.1"
    / "PSX"
    / "State"
    / "Resident Evil - Director's Cut (USA).Nymashock.QuickSave4.State"
)
OUT = ROOT / "data" / "dog_death_fade_ram_trace.jsonl"

from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.cutscene_reward import (  # noqa: E402
    cutscene_disqualify_reason,
    qualify_cutscene_reward,
)
from re1_rl.game_session import episode_failure_reason, outside_gameplay_reason
from re1_rl.memory_map import (  # noqa: E402
    CAM_ID,
    CHARACTER_ID,
    GAME_MODE,
    GAME_STATE,
    MESSAGE_FLAG,
    PLAYER_HP,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
)
from re1_rl.ram_skip import needs_skip_from_ram, scene_active_from_ram


def _poll(bridge: BizHawkClient) -> dict[str, int]:
    raw = bridge.read_ram(
        [
            ("game_state", GAME_STATE, "u32"),
            ("game_mode", GAME_MODE, "u8"),
            ("scene_flag", SCENE_FLAG, "u8"),
            ("msg_flag", MESSAGE_FLAG, "u8"),
            ("stage_id", STAGE_ID, "u8"),
            ("room_id", ROOM_ID, "u8"),
            ("character_id", CHARACTER_ID, "u8"),
            ("player_hp", PLAYER_HP, "u16"),
            ("cam_id", CAM_ID, "u8"),
        ]
    )
    return {k: int(raw[k]) for k in raw}


def _state_dict(ram: dict[str, int]) -> dict:
    room_code = f"{ram['stage_id'] + 1}{ram['room_id']:02X}"
    return {
        "hp": ram["player_hp"],
        "room_id": room_code,
        "room_byte": ram["room_id"],
        "stage_id": ram["stage_id"],
        "cam_id": ram["cam_id"],
        "character_id": ram["character_id"],
        "game_mode": ram["game_mode"],
        "game_state": ram["game_state"],
        "scene_flag": ram["scene_flag"],
        "msg_flag": ram["msg_flag"],
    }


def _row(ram: dict[str, int], *, frame: int, episode_start_hp: int, prev: dict | None) -> dict:
    probe = {
        "player_hp": ram["player_hp"],
        "stage_id": ram["stage_id"],
        "room_id": ram["room_id"],
        "character_id": ram["character_id"],
        "game_mode": ram["game_mode"],
        "game_state": ram["game_state"],
        "msg_flag": ram["msg_flag"],
        "scene_flag": ram["scene_flag"],
    }
    cur = _state_dict(ram)
    skip_frames = 0
    cutscene_key = None
    cutscene_disqual = None
    if prev is not None:
        skip_frames = 30  # simulate post-skip sync threshold met
        cutscene_key = qualify_cutscene_reward(
            skip_frames=skip_frames,
            prev_state=prev,
            new_state=cur,
            episode_start_hp=episode_start_hp,
        )
        cutscene_disqual = cutscene_disqualify_reason(
            skip_frames=skip_frames,
            prev_state=prev,
            new_state=cur,
            episode_start_hp=episode_start_hp,
        )
    return {
        "frame": frame,
        "hp": ram["player_hp"],
        "room": f"{ram['stage_id'] + 1}{ram['room_id']:02X}",
        "cam": ram["cam_id"],
        "game_state": f"0x{ram['game_state']:08X}",
        "game_mode": f"0x{ram['game_mode']:02X}",
        "scene_flag": f"0x{ram['scene_flag']:02X}",
        "msg_flag": f"0x{ram['msg_flag']:02X}",
        "needs_skip": needs_skip_from_ram(probe),
        "scene_active": scene_active_from_ram(probe),
        "outside": outside_gameplay_reason(probe, episode_start_hp=episode_start_hp),
        "episode_failure": episode_failure_reason(
            probe, episode_start_hp=episode_start_hp, prev_hp=prev["hp"] if prev else ram["player_hp"]
        ),
        "cutscene_key_would_pay": cutscene_key,
        "cutscene_disqualify": cutscene_disqual,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--savestate", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--max-frames", type=int, default=3600)
    ap.add_argument("--post-death-frames", type=int, default=600)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    state_path = args.savestate if args.savestate.is_absolute() else ROOT / args.savestate
    if not state_path.is_file():
        print(f"missing savestate: {state_path}", flush=True)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    bridge = BizHawkClient(port=args.port, timeout=120.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={args.port}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"[dog-fade] savestate={state_path.name} port={args.port}", flush=True)
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        bridge.load_savestate(str(state_path))
        bridge.frameadvance(2)

        prev_ram: dict[str, int] | None = None
        prev_state: dict | None = None
        episode_start_hp = 96
        death_frame: int | None = None
        trace: list[dict] = []

        for frame in range(int(args.max_frames)):
            ram = _poll(bridge)
            if ram["player_hp"] > 0 and prev_ram is None:
                episode_start_hp = ram["player_hp"]
            row = _row(ram, frame=frame, episode_start_hp=episode_start_hp, prev=prev_state)
            changed = prev_ram is None or ram != prev_ram
            if changed:
                trace.append(row)
                with args.out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                print(
                    f"f={frame:4d} hp={row['hp']:3d} room={row['room']} cam={row['cam']} "
                    f"gs={row['game_state']} mode={row['game_mode']} scene={row['scene_flag']} "
                    f"skip={row['needs_skip']} cutscene={row['cutscene_key_would_pay']!r} "
                    f"disq={row['cutscene_disqualify']!r}",
                    flush=True,
                )
            if death_frame is None and episode_start_hp > 0 and (
                ram["player_hp"] <= 0 or ram["player_hp"] > 140
            ):
                death_frame = frame
                bridge.screenshot(str(ROOT / "data" / "dog_death_fade_death.png"))
                print(f"[dog-fade] *** hp<=0 at frame {frame}", flush=True)
            if death_frame is not None and frame - death_frame >= int(args.post_death_frames):
                break
            bridge.frameadvance(1)
            prev_ram = dict(ram)
            prev_state = _state_dict(ram)

        summary = {
            "savestate": str(state_path),
            "frames": len(trace),
            "death_frame": death_frame,
            "episode_start_hp": episode_start_hp,
            "paid_cutscene_rows": [
                r for r in trace if r.get("cutscene_key_would_pay")
            ],
        }
        summary_path = args.out.with_suffix(".summary.json")
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[dog-fade] trace -> {args.out}", flush=True)
        print(f"[dog-fade] summary -> {summary_path}", flush=True)
        print(f"[dog-fade] cutscene_would_pay={len(summary['paid_cutscene_rows'])}", flush=True)
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        bridge.close()
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
