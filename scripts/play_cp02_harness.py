"""cp02 harness: Kenneth ``104:*:sN`` flag, then return to dining.

Starts from ``cp01`` (tea room 104), ``leg_span=1``:
  leg: ``barry_return_105`` — Kenneth flag + 104→105 must pay
  ``checkpoint_success`` and print ``[yawn_capture]``.

Dining without the flag, or timeout, is a punishment.

Keys: WASD move, Shift+W run, Z/E interact, Esc/Q quit.
Gamepad: focus EmuHawk — stick move, Cross interact, Square run.

Isolated BizHawk port (default 7792); only frees that port, not fleet workers.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import INTERACT_ACTION
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.bizhawk_paths import EMUHAWK, assert_rom_present, emuhawk_argv
from re1_rl.cutscene_reward import kenneth_cutscene_seen
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.memory_map import DEFAULT_RAM_FIELDS, SCENE_FLAG, decode_inventory
from re1_rl.sticky_input import human_step_gate
from scripts.play_human import (
    _import_keyboard,
    _kill_stale_listener,
    _poll_play_buttons,
    configure_ram_skip,
    wait_for_emuhawk,
)
from scripts.play_ppo_harness import (
    MOVEMENT_KEYS,
    _combat_key_edge,
    _pressed_combat_keys,
    _resolve_movement_actions,
)

PORT = 7792
WINDOW_TITLE = "CP02-kenneth-dining"
CP01_DIR = ROOT / "states" / "yawn_rails" / "cells" / "cp01"
CURRICULUM = ROOT / "curriculum" / "yawn_rails_one_leg.json"
# cp01 checkpoint_index=1 -> first hunted leg is route index 2 (barry_return_105).
ROUTE_START = 2
LEG_SPAN = 1
OBJECTIVE_ID = "barry_return_105"
LOG_PATH = ROOT / "data" / "logs" / "cp02_harness.jsonl"


def _launch_emuhawk(port: int, log_path: Path) -> subprocess.Popen[Any]:
    rom = assert_rom_present()
    if not EMUHAWK.is_file():
        raise FileNotFoundError(f"EmuHawk not found at {EMUHAWK}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = log_path.open("w", encoding="utf-8")
    print(f"[cp02] launching EmuHawk rom={rom} log={log_path}", flush=True)
    return subprocess.Popen(
        emuhawk_argv(port=port),
        cwd=str(EMUHAWK.parent),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _room_code(ram: dict[str, Any]) -> str:
    return f"{int(ram.get('stage_id', 0)) + 1}{int(ram.get('room_id', 0)):02X}"


def _ledgers(env: RE1Env) -> set[str]:
    prog = env._progress
    return set(prog.observed_cutscenes or ()) | set(prog.rewarded_cutscenes or ())


def _kenneth_keys(env: RE1Env) -> list[str]:
    return sorted(
        k for k in _ledgers(env) if str(k).startswith("104:") and ":s" in str(k)
    )


def _objective(env: RE1Env) -> str:
    return str((env._planner.current_objective() or {}).get("checkpoint_id") or "?")


def _hits(breakdown: dict[str, float]) -> dict[str, float]:
    return {k: float(v) for k, v in breakdown.items() if abs(float(v)) > 1e-9}


def _read_pose_ram(bridge: BizHawkClient) -> dict[str, Any]:
    fields = list(DEFAULT_RAM_FIELDS)
    if not any(name == "scene_flag" for name, _addr, _kind in fields):
        fields.append(("scene_flag", SCENE_FLAG, "u8"))
    ram = bridge.read_ram(fields)
    inv = [name for name, _qty in decode_inventory(ram) if name]
    return {
        "room": _room_code(ram),
        "player_hp": int(ram.get("player_hp", 0) or 0),
        "player_x": int(ram.get("player_x", 0) or 0),
        "player_z": int(ram.get("player_z", 0) or 0),
        "cam_id": int(ram.get("cam_id", 0) or 0),
        "scene_flag": int(ram.get("scene_flag", 0) or 0),
        "in_control": bool(int(ram.get("game_mode", 0) or 0) & 0x80),
        "inventory": inv,
    }


def _append_log(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _banner(title: str, *lines: str) -> None:
    bar = "=" * 64
    print("\n".join((bar, f"  {title}", *lines, bar)), flush=True)


def _print_panel(
    *,
    step_idx: int,
    action: int,
    reward: float,
    breakdown: dict[str, float],
    mem: dict[str, Any],
    env: RE1Env,
    info: dict[str, Any],
    prev_ken: bool,
) -> bool:
    ken_keys = _kenneth_keys(env)
    ken = bool(ken_keys) or kenneth_cutscene_seen(_ledgers(env))
    hits = _hits(breakdown)
    room = str(mem.get("room") or "?")
    line = (
        f"[cp02] step={step_idx} {ACTION_NAMES[int(action)]} "
        f"room={room} cam={mem.get('cam_id')} hp={mem.get('player_hp')} "
        f"ken={'YES ' + ','.join(ken_keys) if ken else 'NO'} "
        f"obj={_objective(env)} r={reward:+.4f}"
    )
    if hits:
        line += "  hits=" + " ".join(f"{k}={v:+.4f}" for k, v in sorted(hits.items()))
    print(line, flush=True)

    if ken and not prev_ken:
        _banner(
            "KENNETH FLAG LANDED",
            f"  keys={ken_keys}",
            "  now walk back into dining (105) to trigger CP02",
        )
    if abs(float(hits.get("checkpoint_success", 0.0))) > 1e-9:
        _banner(
            "CP02 CELL TRIGGER  checkpoint_success",
            f"  room={room} ken={ken_keys}",
            "  watch for [yawn_capture] quality/installed on this console",
        )
    if abs(float(hits.get("checkpoint_capture_ineligible", 0.0))) > 1e-9:
        _banner(
            "PUNISH  dining without Kenneth flag",
            f"  room={room} ken={ken_keys or 'NONE'}",
            f"  penalty={hits['checkpoint_capture_ineligible']:+.4f}",
        )
    if abs(float(hits.get("checkpoint_timeout", 0.0))) > 1e-9:
        _banner(
            "PUNISH  checkpoint timeout",
            f"  room={room} ken={ken_keys or 'NONE'}",
            f"  penalty={hits['checkpoint_timeout']:+.4f}",
        )
    if info.get("episode_failure"):
        print(f"  ** episode_failure={info['episode_failure']} **", flush=True)
    return ken


def main() -> int:
    ap = argparse.ArgumentParser(description="cp01 -> barry_return_105 Kenneth harness")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--speed", type=int, default=200, help="in-control BizHawk speed %%")
    ap.add_argument("--cutscene-speed", type=int, default=6400)
    ap.add_argument("--cutscene-chunk", type=int, default=600)
    ap.add_argument("--frame-skip", type=int, default=4)
    ap.add_argument("--connect-timeout", type=float, default=90.0)
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument(
        "--kill-stale",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="free only this harness port (default on); does not touch fleet workers",
    )
    ap.add_argument(
        "--input",
        choices=("both", "keyboard", "gamepad"),
        default="both",
    )
    ap.add_argument("--log", type=Path, default=LOG_PATH)
    args = ap.parse_args()

    use_keyboard = args.input in ("keyboard", "both")
    use_gamepad = args.input in ("gamepad", "both")

    port = int(args.port)
    if args.kill_stale:
        _kill_stale_listener(port)

    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    state_path = CP01_DIR / "cell.State"
    sidecar_path = CP01_DIR / "cell.sidecar.json"
    if not state_path.is_file() or not sidecar_path.is_file():
        print(f"ERROR: missing cp01 bundle under {CP01_DIR}", file=sys.stderr)
        return 1

    reset_options: dict[str, Any] = {
        "pb_bundle": {
            "state_path": str(state_path.resolve()),
            "sidecar_path": str(sidecar_path.resolve()),
            "source": "yawn_rails",
        },
        "route_start_index": ROUTE_START,
        "leg_span": LEG_SPAN,
    }

    bridge = BizHawkClient(port=port, timeout=120.0)
    bridge.start_server()
    emu_log = ROOT / "data" / "logs" / f"emuhawk_cp02_{port}.log"
    proc = None if args.no_launch else _launch_emuhawk(port, emu_log)
    wait_for_emuhawk(
        bridge,
        proc,
        port=port,
        timeout=float(args.connect_timeout),
        log_path=emu_log,
    )
    bridge.set_speed(int(args.speed))

    env = RE1Env(
        curriculum_path=CURRICULUM,
        bridge=bridge,
        frame_skip=max(1, int(args.frame_skip)),
        project_root=ROOT,
        async_cutscene_skip=True,
        camera_whiten=False,
    )
    env.knife_echo_joypad = False
    configure_ram_skip(
        env,
        int(args.speed),
        cutscene_speed=int(args.cutscene_speed),
        turbo_patches=True,
        invisible_cutscenes=False,
        skip_chunk=int(args.cutscene_chunk),
    )
    env._ram_skip.install_engine_patches()

    log_path = args.log.resolve()
    if log_path.is_file():
        log_path.unlink()

    _obs, _info = env.reset(options=reset_options)
    init = _read_pose_ram(bridge)
    ken = bool(_kenneth_keys(env))
    _append_log(
        log_path,
        {
            "ts": _utc_iso(),
            "tag": "init",
            "step": 0,
            "objective": _objective(env),
            "kenneth": ken,
            "kenneth_keys": _kenneth_keys(env),
            **init,
        },
    )
    print(
        f"\n[cp02] {WINDOW_TITLE} port={port} speed={args.speed}% "
        f"input={args.input} log={log_path}\n"
        "  Keyboard: WASD move | Shift+W run | Z/E interact | Esc/Q quit\n"
        "  Gamepad: focus EmuHawk — stick move | Cross interact | Square run\n"
        "  PASS: Kenneth cinema (104:*:sN) then walk back into dining 105\n"
        "  FAIL: dining without that flag, or timeout\n"
        f"  spawn room={init['room']} obj={_objective(env)} ken={ken}\n",
        flush=True,
    )
    if _objective(env) != OBJECTIVE_ID:
        print(
            f"WARNING: objective is {_objective(env)!r}, expected {OBJECTIVE_ID}",
            flush=True,
        )

    kb = _import_keyboard()
    step_idx = 0
    step_armed = True
    combat_keys_prev: set[str] = set()

    try:
        while True:
            if kb is not None and (kb.is_pressed("esc") or kb.is_pressed("q")):
                print("[cp02] quit", flush=True)
                break

            buttons = _poll_play_buttons(
                kb=kb if use_keyboard else None,
                bridge=bridge,
                use_keyboard=use_keyboard,
                use_emuhawk_joypad=use_gamepad,
            )
            combat_action = (
                _combat_key_edge(kb, combat_keys_prev) if use_keyboard and kb else None
            )
            combat_keys_prev = _pressed_combat_keys(kb) if use_keyboard and kb else set()

            actions: list[int] = []
            if combat_action is not None:
                actions = [int(combat_action)]
            else:
                gate_buttons = {k: v for k, v in buttons.items() if k in MOVEMENT_KEYS}
                if buttons.get("cross") and not gate_buttons:
                    gate_buttons = {"cross": True}
                should_step, step_armed = human_step_gate(gate_buttons, armed=step_armed)
                if not should_step:
                    time.sleep(0.033)
                    continue
                if buttons.get("cross") and not any(
                    buttons.get(k) for k in MOVEMENT_KEYS
                ):
                    actions = [INTERACT_ACTION]
                else:
                    actions = _resolve_movement_actions(buttons)
                if not actions:
                    time.sleep(0.033)
                    continue

            terminated = truncated = False
            info: dict[str, Any] = {}
            for action_id in actions:
                _obs, reward, terminated, truncated, info = env.step(int(action_id))
                step_idx += 1
                mem = _read_pose_ram(bridge)
                breakdown = dict(info.get("reward_breakdown") or {})
                prev_ken = ken
                ken = _print_panel(
                    step_idx=step_idx,
                    action=int(action_id),
                    reward=float(reward),
                    breakdown=breakdown,
                    mem=mem,
                    env=env,
                    info=info,
                    prev_ken=prev_ken,
                )
                hits = _hits(breakdown)
                row = {
                    "ts": _utc_iso(),
                    "tag": "step",
                    "step": step_idx,
                    "action": ACTION_NAMES[int(action_id)],
                    "reward": float(reward),
                    "hits": hits,
                    "objective": _objective(env),
                    "kenneth": ken,
                    "kenneth_keys": _kenneth_keys(env),
                    "episode_failure": info.get("episode_failure"),
                    "checkpoint_success_py": bool(env._progress.checkpoint_success),
                    **mem,
                }
                if hits or ken != prev_ken or terminated or truncated:
                    _append_log(log_path, row)
                elif step_idx == 1 or step_idx % 25 == 0:
                    _append_log(log_path, {**row, "tag": "heartbeat"})

                if terminated or truncated:
                    break

            if terminated or truncated:
                reason = info.get("episode_failure") or (
                    "checkpoint_success"
                    if env._progress.checkpoint_success
                    else "truncated"
                )
                _append_log(
                    log_path,
                    {
                        "ts": _utc_iso(),
                        "tag": "episode_end",
                        "step": step_idx,
                        "reason": reason,
                        "kenneth": ken,
                        "kenneth_keys": _kenneth_keys(env),
                        "checkpoint_success_py": bool(env._progress.checkpoint_success),
                        "objective": _objective(env),
                    },
                )
                _banner(
                    f"EPISODE END  {reason}",
                    f"  kenneth={_kenneth_keys(env) or 'NONE'}",
                    f"  checkpoint_success={bool(env._progress.checkpoint_success)}",
                )
                print("[cp02] Esc/Q to close.", flush=True)
                while kb is not None:
                    if kb.is_pressed("esc") or kb.is_pressed("q"):
                        break
                    time.sleep(0.1)
                break
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        bridge.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
