"""cp41 harness: East Stairway 1F (10B) -> Mansion Storeroom (118).

Starts from ``cp40`` (``east_stairs_101``), ``leg_span=1``:
  leg: ``storeroom_enter_118`` - entering room ``118`` must end with ``checkpoint_success``.

Keys: WASD move, Shift+W run, Z/E interact, Esc/Q quit.
Gamepad: focus EmuHawk - stick move, Cross interact, Square run (``--input both``).

Uses an isolated BizHawk port (default 7796); only frees that port, not fleet workers.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import INTERACT_ACTION
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.memory_map import DEFAULT_RAM_FIELDS, decode_inventory
from re1_rl.sticky_input import human_step_gate
from re1_rl.bizhawk_paths import EMUHAWK, assert_rom_present, emuhawk_argv
from scripts.play_human import (
    _import_keyboard,
    _kill_stale_listener,
    _poll_play_buttons,
    _read_emuhawk_joypad,
    configure_ram_skip,
    wait_for_emuhawk,
)
from scripts.play_ppo_harness import (
    MOVEMENT_KEYS,
    _combat_key_edge,
    _format_combat_mask,
    _pressed_combat_keys,
    _resolve_movement_actions,
)

PORT = 7796
WINDOW_TITLE = "CP41-10B-118"
CP40_DIR = ROOT / "states" / "yawn_rails" / "cells" / "cp40"
CURRICULUM = ROOT / "curriculum" / "yawn_rails_one_leg.json"
# cp40 checkpoint_index=40 -> first leg is route index 41 (storeroom_enter_118).
ROUTE_START = 41
LEG_SPAN = 1
TARGET_ROOM = "118"
OBJECTIVE_ID = "storeroom_enter_118"
LOG_PATH = ROOT / "data" / "logs" / "cp41_harness.jsonl"


def _launch_emuhawk(port: int, log_path: Path) -> subprocess.Popen[Any]:
    rom = assert_rom_present()
    if not EMUHAWK.is_file():
        raise FileNotFoundError(f"EmuHawk not found at {EMUHAWK}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = log_path.open("w", encoding="utf-8")
    print(f"[cp41] launching EmuHawk rom={rom} log={log_path}", flush=True)
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


def _read_pose_ram(bridge: BizHawkClient) -> dict[str, Any]:
    ram = bridge.read_ram(list(DEFAULT_RAM_FIELDS))
    inv = [name for name, _qty in decode_inventory(ram) if name]
    return {
        "room": _room_code(ram),
        "player_hp": int(ram.get("player_hp", 0) or 0),
        "player_x": int(ram.get("player_x", 0) or 0),
        "player_z": int(ram.get("player_z", 0) or 0),
        "cam_id": int(ram.get("cam_id", 0) or 0),
        "in_control": bool(int(ram.get("game_mode", 0) or 0) & 0x80),
        "inventory": inv,
        "chemical": "chemical" in inv,
    }


def _objective(env: RE1Env) -> str:
    return str((env._planner.current_objective() or {}).get("checkpoint_id") or "?")


@dataclass
class Cp41Verdict:
    init_room: str | None = None
    init_objective: str | None = None
    room_target_step: int | None = None
    episode_end_reason: str | None = None
    checkpoint_success: bool = False
    failures: list[str] = field(default_factory=list)

    def note_init(self, *, room: str, objective: str, chemical: bool) -> None:
        self.init_room = room
        self.init_objective = objective
        if objective != OBJECTIVE_ID:
            self.failures.append(f"init objective {objective!r} != {OBJECTIVE_ID}")
        if room != "10B":
            self.failures.append(f"spawn room {room!r} != 10B")
        if chemical:
            self.failures.append("spawn already has chemical (expected pre-storeroom)")

    def note_room(self, *, step: int, room: str) -> None:
        if room == TARGET_ROOM and self.room_target_step is None:
            self.room_target_step = step

    def note_episode_end(
        self,
        *,
        step: int,
        reason: str,
        checkpoint_success: bool,
        room: str,
    ) -> None:
        self.episode_end_reason = reason
        self.checkpoint_success = checkpoint_success
        if room != TARGET_ROOM:
            self.failures.append(f"episode ended in room {room!r}, expected {TARGET_ROOM}")
        if not checkpoint_success:
            self.failures.append(f"episode ended without checkpoint_success ({reason})")
        elif reason != "checkpoint_success":
            self.failures.append(f"checkpoint_success but reason={reason!r}")

    def summary(self) -> str:
        lines = [
            "[cp41 verdict]",
            f"  spawn: room={self.init_room} objective={self.init_objective}",
            f"  entered {TARGET_ROOM} @ step: "
            f"{self.room_target_step if self.room_target_step is not None else 'NOT OBSERVED'}",
            f"  episode end: {self.episode_end_reason!r} checkpoint_success={self.checkpoint_success}",
        ]
        if self.failures:
            lines.append("  failures:")
            for f in self.failures:
                lines.append(f"    - {f}")
            lines.append("  OVERALL: FAIL")
        elif self.room_target_step is not None and self.checkpoint_success:
            lines.append("  OVERALL: PASS")
        else:
            lines.append("  OVERALL: INCOMPLETE")
        return "\n".join(lines)


class MemoryMonitor:
    def __init__(self, bridge: BizHawkClient, log_path: Path) -> None:
        self._bridge = bridge
        self._log_path = log_path
        self._last_room: str | None = None
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def snapshot(
        self,
        *,
        step: int | None,
        tag: str,
        env: RE1Env | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snap = _read_pose_ram(self._bridge)
        row: dict[str, Any] = {"ts": _utc_iso(), "tag": tag, "step": step, **snap}
        if env is not None:
            prog = env._progress
            row["objective"] = _objective(env)
            row["legs_completed"] = int(prog.legs_completed)
            row["leg_span"] = int(prog.leg_span)
            row["checkpoint_success_py"] = bool(prog.checkpoint_success)
        if extra:
            row.update(extra)
        room_changed = snap["room"] != self._last_room
        verbose = tag in ("init", "room_target", "episode_end")
        if verbose or room_changed:
            line = (
                f"[mem] {tag} step={step} obj={row.get('objective', '?')} "
                f"room={snap['room']} pos=({snap['player_x']},{snap['player_z']}) "
                f"cam={snap['cam_id']} chemical={snap['chemical']} "
                f"legs={row.get('legs_completed')}/{row.get('leg_span')}"
            )
            if room_changed and self._last_room is not None:
                line += f" droom={self._last_room}->{snap['room']}"
            print(line, flush=True)
        if verbose or room_changed or tag == "step":
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        self._last_room = snap["room"]
        return snap


def _print_step_panel(
    *,
    step_idx: int,
    action: int,
    reward: float,
    breakdown: dict[str, float],
    mem: dict[str, Any],
    env: RE1Env,
    info: dict[str, Any],
) -> None:
    lines = [
        f"--- step {step_idx} {ACTION_NAMES[int(action)]} reward={reward:+.3f} ---",
        f"  objective={_objective(env)} room={mem.get('room')} "
        f"pos=({mem.get('player_x')},{mem.get('player_z')}) hp={mem.get('player_hp')}",
        f"  inv={mem.get('inventory')}",
    ]
    for key in ("checkpoint_success",):
        val = float(breakdown.get(key, 0.0) or 0.0)
        if abs(val) > 1e-9:
            lines.append(f"  {key}: {val:+.3f}")
    if info.get("episode_failure"):
        lines.append(f"  ** episode_failure={info['episode_failure']} **")
    print("\n".join(lines), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="cp41 -> 118 storeroom enter harness")
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
    ap.add_argument("--show-mask", action="store_true")
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

    state_path = CP40_DIR / "cell.State"
    sidecar_path = CP40_DIR / "cell.sidecar.json"
    if not state_path.is_file() or not sidecar_path.is_file():
        print(f"ERROR: missing cp40 bundle under {CP40_DIR}", file=sys.stderr)
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
    log_path = ROOT / "data" / "logs" / f"emuhawk_cp41_{port}.log"
    proc = None if args.no_launch else _launch_emuhawk(port, log_path)
    wait_for_emuhawk(
        bridge,
        proc,
        port=port,
        timeout=float(args.connect_timeout),
        log_path=log_path,
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

    monitor = MemoryMonitor(bridge, args.log.resolve())
    verdict = Cp41Verdict()

    _obs, _info = env.reset(options=reset_options)
    init_snap = monitor.snapshot(step=0, tag="init", env=env)
    verdict.note_init(
        room=str(init_snap["room"]),
        objective=_objective(env),
        chemical=bool(init_snap["chemical"]),
    )

    print(
        f"\n[cp41] {WINDOW_TITLE} port={port} speed={args.speed}% "
        f"input={args.input} log={args.log}\n"
        "  Keyboard: WASD move | Shift+W run | Z/E interact | Esc/Q quit\n"
        "  Gamepad: focus EmuHawk - stick move | Cross interact | Square run\n"
        f"  CHECK: navigate 10B -> 10A -> 11A -> {TARGET_ROOM} (Storeroom) -> checkpoint_success\n",
        flush=True,
    )
    if use_gamepad:
        sample = _read_emuhawk_joypad(bridge, debug=True)
        pressed = [k for k, v in sample.items() if v]
        print(
            "[cp41] EmuHawk gamepad active - click the game window, then move stick.",
            flush=True,
        )
        if pressed:
            print(f"[cp41] joypad sample: {pressed}", flush=True)
    if args.show_mask:
        print(_format_combat_mask(env, env._prev_state), flush=True)

    kb = _import_keyboard()
    step_idx = 0
    step_armed = True
    combat_keys_prev: set[str] = set()

    try:
        while True:
            if kb is not None and (kb.is_pressed("esc") or kb.is_pressed("q")):
                verdict.note_episode_end(
                    step=step_idx,
                    reason="user_quit",
                    checkpoint_success=False,
                    room=str((_read_pose_ram(bridge)).get("room", "?")),
                )
                print("[cp41] quit", flush=True)
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
                obs, reward, terminated, truncated, info = env.step(int(action_id))
                step_idx += 1
                cur_ram = monitor.snapshot(step=step_idx, tag="step", env=env)
                breakdown = dict(info.get("reward_breakdown") or {})
                _print_step_panel(
                    step_idx=step_idx,
                    action=int(action_id),
                    reward=float(reward),
                    breakdown=breakdown,
                    mem=cur_ram,
                    env=env,
                    info=info,
                )
                verdict.note_room(step=step_idx, room=str(cur_ram["room"]))
                if cur_ram["room"] == TARGET_ROOM and verdict.room_target_step == step_idx:
                    monitor.snapshot(step=step_idx, tag="room_target", env=env)

                if args.show_mask:
                    print(_format_combat_mask(env, env._prev_state), flush=True)
                if terminated or truncated:
                    break

            if terminated or truncated:
                reason = info.get("episode_failure") or (
                    "checkpoint_success"
                    if env._progress.checkpoint_success
                    else "truncated"
                )
                cur = _read_pose_ram(bridge)
                verdict.note_episode_end(
                    step=step_idx,
                    reason=str(reason),
                    checkpoint_success=bool(env._progress.checkpoint_success),
                    room=str(cur["room"]),
                )
                monitor.snapshot(
                    step=step_idx,
                    tag="episode_end",
                    env=env,
                    extra={"reason": reason},
                )
                print(f"\n{verdict.summary()}\n", flush=True)
                print(
                    "[cp41] halted - Esc/Q to exit (cutscene skip still runs in background).",
                    flush=True,
                )
                while kb is not None and True:
                    if kb.is_pressed("esc") or kb.is_pressed("q"):
                        break
                    time.sleep(0.1)
                break
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        bridge.close()

    return 0 if not verdict.failures and verdict.checkpoint_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
