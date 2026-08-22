"""cp119 -> yawn_cutscene_210 harness: walk/run forward to trigger Yawn cinema.

Starts from live ``cp119`` (already in attic 210). Auto-holds run-forward until
the intro cinema skip settles and mints ``210:yawn``. Capture stays in 210 so
the successor cell (cp120) spawns in the fight.

Isolated BizHawk port (default 7798); only frees that port, not fleet workers.

Keys override auto-run: WASD move, Shift+W run, Esc/Q quit.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.bizhawk_paths import (
    EMUHAWK,
    assert_rom_present,
    emuhawk_argv,
    newest_quicksave,
)
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.memory_map import DEFAULT_RAM_FIELDS, decode_inventory
from re1_rl.ram_skip import needs_skip_from_ram, SKIP_POLL_RAM_FIELDS
from re1_rl.sticky_input import human_step_gate
from re1_rl.yawn_cutscene_checkpoint import YAWN_CUTSCENE_KEY
from scripts.play_human import (
    _import_keyboard,
    _kill_stale_listener,
    _poll_play_buttons,
    configure_ram_skip,
    wait_for_emuhawk,
)
from scripts.play_ppo_harness import (
    MOVEMENT_KEYS,
    _resolve_movement_actions,
)

PORT = 7798
WINDOW_TITLE = "CP119-yawn-cutscene"
CP119_DIR = ROOT / "states" / "yawn_rails" / "cells" / "cp119"
CURRICULUM = ROOT / "curriculum" / "yawn_rails_one_leg.json"
# cp119 checkpoint_index=119 -> hunted leg is route index 120 (yawn_cutscene_210).
ROUTE_START = 120
LEG_SPAN = 1
OBJECTIVE_ID = "yawn_cutscene_210"
WALK_FORWARD = ACTION_NAMES.index("forward")
NOOP = ACTION_NAMES.index("noop")
HOLD_CHUNK = 20
HOLD_MAX_FRAMES = 2400
WALK_STATE_COPY = ROOT / "states" / "yawn_210_walk_forward.State"
LOG_PATH = ROOT / "data" / "logs" / "cp119_yawn_cutscene_harness.jsonl"


def _launch_emuhawk(port: int, log_path: Path) -> subprocess.Popen[Any]:
    rom = assert_rom_present()
    if not EMUHAWK.is_file():
        raise FileNotFoundError(f"EmuHawk not found at {EMUHAWK}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = log_path.open("w", encoding="utf-8")
    print(f"[cp119-yawn] launching EmuHawk rom={rom} log={log_path}", flush=True)
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


def _objective(env: RE1Env) -> str:
    return str((env._planner.current_objective() or {}).get("checkpoint_id") or "?")


def _hits(breakdown: dict[str, float]) -> dict[str, float]:
    return {k: float(v) for k, v in breakdown.items() if abs(float(v)) > 1e-9}


def _read_pose_ram(bridge: BizHawkClient) -> dict[str, Any]:
    ram = bridge.read_ram(list(DEFAULT_RAM_FIELDS))
    inv = [name for name, _qty in decode_inventory(ram) if name]
    return {
        "room": _room_code(ram),
        "player_hp": int(ram.get("player_hp", 0) or 0),
        "player_x": int(ram.get("player_x", 0) or 0),
        "player_z": int(ram.get("player_z", 0) or 0),
        "facing": int(ram.get("player_facing", ram.get("facing", 0)) or 0),
        "cam_id": int(ram.get("cam_id", 0) or 0),
        "in_control": bool(int(ram.get("game_mode", 0) or 0) & 0x80),
        "inventory": inv,
    }


def _append_log(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _needs_skip(bridge: BizHawkClient) -> bool:
    try:
        ram = bridge.read_ram(list(SKIP_POLL_RAM_FIELDS))
        return bool(needs_skip_from_ram(ram))
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return False


def _settle_cinema(env: RE1Env, bridge: BizHawkClient) -> None:
    """Turbo-skip the intro cinema through env so ``210:yawn`` can mint."""
    env._ram_skip.install_engine_patches()
    bridge.clear_latched_input()
    skipped, died = env._skip_uncontrolled()
    print(
        f"[cp119-yawn] skip_uncontrolled frames={skipped} died={died}",
        flush=True,
    )
    if died:
        return
    for _ in range(8):
        env.step(NOOP)


def _hold_forward(bridge: BizHawkClient, frames: int) -> None:
    """Keep D-pad Up down across a native frame batch (user 'hold forward')."""
    bridge.step(
        n=max(1, int(frames)),
        sticky={
            "up": True,
            "down": False,
            "left": False,
            "right": False,
            "square": False,
        },
        pulse=None,
        pulse_hold=None,
    )


def _banner(title: str, *lines: str) -> None:
    bar = "=" * 64
    print("\n".join((bar, f"  {title}", *lines, bar)), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="cp119 -> yawn_cutscene_210 walk-forward capture"
    )
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--speed", type=int, default=200, help="in-control BizHawk speed %%")
    ap.add_argument("--cutscene-speed", type=int, default=6400)
    ap.add_argument("--cutscene-chunk", type=int, default=600)
    ap.add_argument("--frame-skip", type=int, default=4)
    ap.add_argument("--connect-timeout", type=float, default=90.0)
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument(
        "--state",
        type=Path,
        default=None,
        help="BizHawk .State to walk from (default: newest QuickSave)",
    )
    ap.add_argument(
        "--no-auto-walk",
        action="store_true",
        help="disable auto walk-forward (keyboard/gamepad only)",
    )
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
    auto_walk = not args.no_auto_walk
    live_state = Path(args.state).resolve() if args.state else newest_quicksave()
    if not live_state.is_file():
        print(f"ERROR: missing walk-start savestate {live_state}", file=sys.stderr)
        return 1

    port = int(args.port)
    if args.kill_stale:
        _kill_stale_listener(port)

    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    state_path = CP119_DIR / "cell.State"
    sidecar_path = CP119_DIR / "cell.sidecar.json"
    if not state_path.is_file() or not sidecar_path.is_file():
        print(f"ERROR: missing cp119 bundle under {CP119_DIR}", file=sys.stderr)
        return 1

    # Propose to the learner so the fight-start cell reaches the fleet.
    os.environ.setdefault("RE1_YAWN_RAILS_SYNC", "1")

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
    emu_log = ROOT / "data" / "logs" / f"emuhawk_cp119_yawn_{port}.log"
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
    # No engine turbo during the walk — patches/skip were eating the cinema
    # trigger when we env.step()'d from this same QuickSave.
    configure_ram_skip(
        env,
        int(args.speed),
        cutscene_speed=int(args.cutscene_speed),
        turbo_patches=False,
        invisible_cutscenes=False,
        skip_chunk=int(args.cutscene_chunk),
    )

    log_path = args.log.resolve()
    if log_path.is_file():
        log_path.unlink()

    _obs, _info = env.reset(options=reset_options)
    WALK_STATE_COPY.parent.mkdir(parents=True, exist_ok=True)
    if live_state.resolve() != WALK_STATE_COPY.resolve():
        shutil.copy2(live_state, WALK_STATE_COPY)
    print(f"[cp119-yawn] overlay walk-start state={live_state}", flush=True)
    bridge.load_savestate(str(live_state))
    bridge.clear_latched_input()
    bridge.frameadvance(1)
    try:
        env._prev_state = env._read_state(track_items=False)
        env._cutscene_skip_entry_prev = dict(env._prev_state)
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        pass
    init = _read_pose_ram(bridge)
    yawn_seen = YAWN_CUTSCENE_KEY in _ledgers(env)
    _append_log(
        log_path,
        {
            "ts": _utc_iso(),
            "tag": "init",
            "step": 0,
            "objective": _objective(env),
            "yawn_key": yawn_seen,
            **init,
        },
    )
    print(
        f"\n[cp119-yawn] {WINDOW_TITLE} port={port} speed={args.speed}% "
        f"auto_walk={auto_walk} log={log_path}\n"
        "  Auto: hold walk-forward until the Yawn intro cinema.\n"
        "  Override: WASD move | Shift+W run | Esc/Q quit\n"
        f"  spawn room={init['room']} x={init['player_x']} z={init['player_z']} "
        f"facing={init['facing']} obj={_objective(env)}\n",
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
    success = False

    try:
        while True:
            if kb is not None and (kb.is_pressed("esc") or kb.is_pressed("q")):
                print("[cp119-yawn] quit", flush=True)
                break

            buttons = _poll_play_buttons(
                kb=kb if use_keyboard else None,
                bridge=bridge,
                use_keyboard=use_keyboard,
                use_emuhawk_joypad=use_gamepad,
            )
            gate_buttons = {k: v for k, v in buttons.items() if k in MOVEMENT_KEYS}
            human_move = any(gate_buttons.values())
            actions: list[int] = []
            if human_move:
                should_step, step_armed = human_step_gate(gate_buttons, armed=step_armed)
                if not should_step:
                    time.sleep(0.033)
                    continue
                actions = _resolve_movement_actions(buttons)
            elif auto_walk:
                mem = _read_pose_ram(bridge)
                if mem.get("in_control"):
                    _hold_forward(bridge, HOLD_CHUNK)
                    step_idx += 1
                    mem = _read_pose_ram(bridge)
                    if step_idx <= 3 or step_idx % 15 == 0 or not mem.get("in_control"):
                        print(
                            f"[cp119-yawn] hold_up frames={step_idx * HOLD_CHUNK} "
                            f"room={mem.get('room')} cam={mem.get('cam_id')} "
                            f"x={mem.get('player_x')} z={mem.get('player_z')} "
                            f"ctrl={int(bool(mem.get('in_control')))}",
                            flush=True,
                        )
                    if step_idx * HOLD_CHUNK >= HOLD_MAX_FRAMES:
                        print("[cp119-yawn] hold_up budget exhausted", flush=True)
                        break
                    if mem.get("in_control") and not _needs_skip(bridge):
                        continue
                    _banner(
                        "CINEMA START",
                        f"  room={mem.get('room')} x={mem.get('player_x')} "
                        f"z={mem.get('player_z')} ctrl={int(bool(mem.get('in_control')))}",
                    )
                    _settle_cinema(env, bridge)
                    mem = _read_pose_ram(bridge)
                    yawn_seen = YAWN_CUTSCENE_KEY in _ledgers(env)
                    actions = [NOOP]
                elif _needs_skip(bridge):
                    _settle_cinema(env, bridge)
                    actions = [NOOP]
                else:
                    actions = [NOOP]
            else:
                time.sleep(0.033)
                continue
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
                hits = _hits(breakdown)
                prev_yawn = yawn_seen
                yawn_seen = YAWN_CUTSCENE_KEY in _ledgers(env)
                line = (
                    f"[cp119-yawn] step={step_idx} {ACTION_NAMES[int(action_id)]} "
                    f"room={mem.get('room')} cam={mem.get('cam_id')} "
                    f"x={mem.get('player_x')} z={mem.get('player_z')} "
                    f"ctrl={int(bool(mem.get('in_control')))} "
                    f"yawn={'YES' if yawn_seen else 'NO'} "
                    f"obj={_objective(env)} r={reward:+.4f}"
                )
                if hits:
                    line += "  hits=" + " ".join(
                        f"{k}={v:+.4f}" for k, v in sorted(hits.items())
                    )
                if (
                    step_idx <= 3
                    or step_idx % 25 == 0
                    or hits
                    or yawn_seen != prev_yawn
                    or terminated
                    or truncated
                ):
                    print(line, flush=True)

                row = {
                    "ts": _utc_iso(),
                    "tag": "step",
                    "step": step_idx,
                    "action": ACTION_NAMES[int(action_id)],
                    "reward": float(reward),
                    "hits": hits,
                    "objective": _objective(env),
                    "yawn_key": yawn_seen,
                    "episode_failure": info.get("episode_failure"),
                    "checkpoint_success_py": bool(env._progress.checkpoint_success),
                    **mem,
                }
                if hits or yawn_seen != prev_yawn or terminated or truncated:
                    _append_log(log_path, row)
                elif step_idx == 1 or step_idx % 25 == 0:
                    _append_log(log_path, {**row, "tag": "heartbeat"})

                if yawn_seen and not prev_yawn:
                    _banner(
                        "YAWN CINEMA LEDGER  210:yawn",
                        f"  room={mem.get('room')} x={mem.get('player_x')} "
                        f"z={mem.get('player_z')}",
                    )
                if abs(float(hits.get("checkpoint_success", 0.0))) > 1e-9:
                    success = True
                    _banner(
                        "CP120 CELL TRIGGER  checkpoint_success",
                        f"  room={mem.get('room')} yawn={yawn_seen}",
                        "  watch for [yawn_capture] quality/installed",
                    )
                if terminated or truncated:
                    break

            if terminated or truncated:
                reason = info.get("episode_failure") or (
                    "checkpoint_success"
                    if env._progress.checkpoint_success
                    else "truncated"
                )
                success = success or bool(env._progress.checkpoint_success)
                _append_log(
                    log_path,
                    {
                        "ts": _utc_iso(),
                        "tag": "episode_end",
                        "step": step_idx,
                        "reason": reason,
                        "yawn_key": yawn_seen,
                        "checkpoint_success_py": bool(env._progress.checkpoint_success),
                        "objective": _objective(env),
                    },
                )
                _banner(
                    f"EPISODE END  {reason}",
                    f"  yawn_key={yawn_seen}",
                    f"  checkpoint_success={bool(env._progress.checkpoint_success)}",
                )
                break
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        bridge.close()

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
