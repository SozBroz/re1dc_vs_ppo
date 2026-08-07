"""Gallery cp33 harness with live RAM monitoring and episode-end assertions.

Starts from cp33 (6 portraits done), leg_span=2:
  leg 1: gallery_complete_117 — final switch must NOT terminate episode
  leg 2: star_crest_117         — crest pickup must terminate checkpoint_success

Keys: WASD move, Shift+W run, Z/E interact, Esc/Q quit.
Gamepad: focus EmuHawk — stick move, Cross interact, Square run (default --input both).
"""

from __future__ import annotations

import argparse
import json
import signal
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
from re1_rl.gallery_puzzle import GALLERY_COMPLETE_PREV_RAW, completed_steps
from re1_rl.memory_map import DEFAULT_RAM_FIELDS, decode_inventory
from re1_rl.sticky_input import human_step_gate
from scripts.play_human import (
    _import_keyboard,
    _kill_stale_listener,
    _poll_play_buttons,
    _read_emuhawk_joypad,
    configure_ram_skip,
    launch_emuhawk,
    wait_for_emuhawk,
)
from scripts.play_ppo_harness import (
    MOVEMENT_KEYS,
    _combat_key_edge,
    _format_combat_mask,
    _pressed_combat_keys,
    _resolve_movement_actions,
)

PORT = 7792
WINDOW_TITLE = "GALLERY-CP33-MEM"
CP33_DIR = ROOT / "states" / "yawn_rails" / "cells" / "cp33"
CURRICULUM = ROOT / "curriculum" / "yawn_rails_one_leg.json"
ROUTE_START = 34
LEG_SPAN = 2
LOG_PATH = ROOT / "data" / "logs" / "gallery_cp33_harness.jsonl"

RAM_EXTRA = [
    ("gallery_flag_3010", 0x800C3010, "u8"),
    ("gallery_flag_3002", 0x800C3002, "u8"),
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _room_code(ram: dict[str, Any]) -> str:
    return f"{int(ram.get('stage_id', 0)) + 1}{int(ram.get('room_id', 0)):02X}"


def _read_gallery_ram(bridge: BizHawkClient) -> dict[str, Any]:
    ram = bridge.read_ram(list(DEFAULT_RAM_FIELDS) + RAM_EXTRA)
    inv = [name for name, _qty in decode_inventory(ram) if name]
    raw = int(ram.get("gallery_progress", 0) or 0)
    return {
        "room": _room_code(ram),
        "gallery_progress_raw": raw,
        "gallery_progress_steps": completed_steps(raw),
        "gallery_confirm": int(ram.get("gallery_confirm", 0) or 0),
        "gallery_flag_3010": int(ram.get("gallery_flag_3010", 0) or 0),
        "gallery_flag_3002": int(ram.get("gallery_flag_3002", 0) or 0),
        "player_hp": int(ram.get("player_hp", 0) or 0),
        "in_control": bool(int(ram.get("game_mode", 0) or 0) & 0x80),
        "inventory": inv,
        "star_crest": "star_crest" in inv,
    }


@dataclass
class GalleryVerdict:
    puzzle_complete_step: int | None = None
    puzzle_complete_terminated: bool = False
    puzzle_complete_ok: bool | None = None
    crest_pickup_step: int | None = None
    crest_episode_end_ok: bool | None = None
    episode_end_reason: str | None = None
    failures: list[str] = field(default_factory=list)

    def note_puzzle_complete(self, *, step: int, terminated: bool) -> None:
        if self.puzzle_complete_step is not None:
            return
        self.puzzle_complete_step = step
        self.puzzle_complete_terminated = terminated
        self.puzzle_complete_ok = not terminated
        if terminated:
            self.failures.append(
                f"puzzle complete at step {step} TERMINATED episode (expected continue)"
            )

    def note_episode_end(
        self,
        *,
        step: int,
        reason: str,
        checkpoint_success: bool,
        has_crest: bool,
        gallery_wrong: bool,
    ) -> None:
        self.episode_end_reason = reason
        if has_crest or reason == "checkpoint_success":
            self.crest_pickup_step = step
            ok = checkpoint_success and reason == "checkpoint_success" and not gallery_wrong
            self.crest_episode_end_ok = ok
            if not ok:
                self.failures.append(
                    f"crest/end step {step}: reason={reason!r} "
                    f"checkpoint_success={checkpoint_success} gallery_wrong={gallery_wrong}"
                )
        elif gallery_wrong:
            self.failures.append(f"episode ended gallery_wrong at step {step}")
        elif reason not in ("user_quit",):
            self.failures.append(f"episode ended early at step {step}: {reason}")

    def summary(self) -> str:
        lines = ["=== GALLERY HARNESS VERDICT ==="]
        if self.puzzle_complete_step is None:
            lines.append("  puzzle complete: NOT OBSERVED")
            self.failures.append("never completed gallery puzzle (final switch)")
        else:
            tag = "PASS" if self.puzzle_complete_ok else "FAIL"
            lines.append(
                f"  puzzle complete @ step {self.puzzle_complete_step}: {tag} "
                f"(terminated={self.puzzle_complete_terminated})"
            )
        if self.crest_pickup_step is None:
            lines.append("  crest pickup end: NOT OBSERVED")
            if self.episode_end_reason not in ("user_quit",):
                self.failures.append("never picked up star crest / checkpoint_success end")
        else:
            tag = "PASS" if self.crest_episode_end_ok else "FAIL"
            lines.append(f"  crest pickup @ step {self.crest_pickup_step}: {tag}")
        if self.failures:
            lines.append("  failures:")
            for f in self.failures:
                lines.append(f"    - {f}")
            lines.append("  OVERALL: FAIL")
        elif self.puzzle_complete_ok and self.crest_episode_end_ok:
            lines.append("  OVERALL: PASS")
        else:
            lines.append("  OVERALL: INCOMPLETE")
        return "\n".join(lines)


class MemoryMonitor:
    """Log live RAM deltas to console + JSONL."""

    def __init__(self, bridge: BizHawkClient, log_path: Path) -> None:
        self._bridge = bridge
        self._log_path = log_path
        self._last: dict[str, Any] | None = None
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def snapshot(
        self,
        *,
        step: int | None,
        tag: str,
        env: RE1Env | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snap = _read_gallery_ram(self._bridge)
        row: dict[str, Any] = {
            "ts": _utc_iso(),
            "tag": tag,
            "step": step,
            **snap,
        }
        if env is not None:
            prog = env._progress
            row["objective"] = str((env._planner.current_objective() or {}).get("checkpoint_id"))
            row["gallery_puzzle_solved_py"] = bool(prog.gallery_puzzle_solved)
            row["gallery_wrong_py"] = bool(prog.gallery_wrong_breached)
            row["legs_completed"] = int(prog.legs_completed)
            row["leg_span"] = int(prog.leg_span)
            row["checkpoint_success_py"] = bool(prog.checkpoint_success)
        if extra:
            row.update(extra)
        changed = self._delta_keys(snap)
        verbose = tag in ("init", "puzzle_complete", "crest_acquired", "episode_end")
        if verbose or changed:
            line = (
                f"[mem] {tag} step={step} obj={row.get('objective', '?')} "
                f"raw={snap['gallery_progress_raw']} steps={snap['gallery_progress_steps']} "
                f"3010={snap['gallery_flag_3010']} crest={snap['star_crest']} "
                f"py_solved={row.get('gallery_puzzle_solved_py')} "
                f"legs={row.get('legs_completed')}/{row.get('leg_span')}"
            )
            if changed:
                line += f" Δ={','.join(changed)}"
            print(line, flush=True)
        if verbose or changed or tag == "step":
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        self._last = snap
        return row

    def _delta_keys(self, snap: dict[str, Any]) -> list[str]:
        if self._last is None:
            return []
        keys = (
            "gallery_progress_raw",
            "gallery_confirm",
            "gallery_flag_3010",
            "star_crest",
        )
        out: list[str] = []
        for k in keys:
            if snap.get(k) != self._last.get(k):
                out.append(f"{k}:{self._last.get(k)}->{snap.get(k)}")
        return out


def _objective(env: RE1Env) -> str:
    return str((env._planner.current_objective() or {}).get("checkpoint_id") or "?")


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
        f"  objective={_objective(env)} room={mem.get('room')} hp={mem.get('player_hp')}",
        f"  RAM raw={mem['gallery_progress_raw']} steps={mem['gallery_progress_steps']} "
        f"confirm={mem['gallery_confirm']} flag3010={mem['gallery_flag_3010']}",
        f"  py puzzle_solved={mem.get('gallery_puzzle_solved_py')} "
        f"wrong={mem.get('gallery_wrong_py')} legs={mem.get('legs_completed')}/{mem.get('leg_span')}",
        f"  inv={mem.get('inventory')}",
    ]
    for key in ("gallery", "gallery_wrong", "checkpoint_success"):
        val = float(breakdown.get(key, 0.0) or 0.0)
        if abs(val) > 1e-9:
            lines.append(f"  {key}: {val:+.3f}")
    if info.get("episode_failure"):
        lines.append(f"  ** episode_failure={info['episode_failure']} **")
    print("\n".join(lines), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Gallery cp33 RAM-monitored harness")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--speed", type=int, default=200, help="in-control BizHawk speed %%")
    ap.add_argument("--cutscene-speed", type=int, default=6400)
    ap.add_argument("--cutscene-chunk", type=int, default=600)
    ap.add_argument("--frame-skip", type=int, default=4)
    ap.add_argument("--connect-timeout", type=float, default=90.0)
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--kill-stale", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--show-mask", action="store_true")
    ap.add_argument(
        "--input",
        choices=("both", "keyboard", "gamepad"),
        default="both",
        help="both = keyboard + EmuHawk gamepad (focus game window for pad)",
    )
    ap.add_argument("--log", type=Path, default=LOG_PATH)
    args = ap.parse_args()

    use_keyboard = args.input in ("keyboard", "both")
    use_gamepad = args.input in ("gamepad", "both")

    port = int(args.port)
    if args.kill_stale:
        _kill_stale_listener(port)

    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    state_path = CP33_DIR / "cell.State"
    sidecar_path = CP33_DIR / "cell.sidecar.json"
    if not state_path.is_file() or not sidecar_path.is_file():
        print(f"ERROR: missing cp33 bundle under {CP33_DIR}", file=sys.stderr)
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
    log_path = ROOT / "data" / "logs" / f"emuhawk_gallery_cp33_{port}.log"
    proc = None if args.no_launch else launch_emuhawk(port, log_path)
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
    verdict = GalleryVerdict()

    obs, info = env.reset(options=reset_options)
    monitor.snapshot(step=0, tag="init", env=env)

    print(
        f"\n[gallery] {WINDOW_TITLE} port={port} speed={args.speed}% "
        f"input={args.input} log={args.log}\n"
        "  Keyboard: WASD move | Shift+W run | Z/E interact | Esc/Q quit\n"
        "  Gamepad: focus EmuHawk window — stick move | Cross interact | Square run\n"
        "  CHECK 1: final switch completes puzzle -> episode continues\n"
        "  CHECK 2: star crest pickup -> checkpoint_success episode end\n",
        flush=True,
    )
    if use_gamepad:
        sample = _read_emuhawk_joypad(bridge, debug=True)
        pressed = [k for k, v in sample.items() if v]
        print(
            "[gallery] EmuHawk gamepad active — click the game window, then move stick.",
            flush=True,
        )
        if pressed:
            print(f"[gallery] joypad sample: {pressed}", flush=True)
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
                    has_crest=_read_gallery_ram(bridge)["star_crest"],
                    gallery_wrong=env._progress.gallery_wrong_breached,
                )
                print("[gallery] quit", flush=True)
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
            info = {}
            for action_id in actions:
                pre_obj = _objective(env)
                pre_raw = (monitor._last or {}).get("gallery_progress_raw")
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

                puzzle_done = (
                    verdict.puzzle_complete_step is None
                    and (
                        env._progress.gallery_puzzle_solved
                        or (
                            pre_raw == GALLERY_COMPLETE_PREV_RAW
                            and cur_ram["gallery_progress_raw"] == 0
                        )
                        or (
                            pre_obj == "gallery_complete_117"
                            and _objective(env) == "star_crest_117"
                        )
                    )
                )
                if puzzle_done:
                    verdict.note_puzzle_complete(step=step_idx, terminated=terminated)
                    monitor.snapshot(
                        step=step_idx,
                        tag="puzzle_complete",
                        env=env,
                        extra={"terminated_on_puzzle": terminated},
                    )
                    if verdict.puzzle_complete_ok:
                        print(
                            f"[check] PASS puzzle complete @ step {step_idx} — episode still running",
                            flush=True,
                        )
                    else:
                        print(
                            f"[check] FAIL puzzle complete @ step {step_idx} — episode terminated!",
                            flush=True,
                        )

                if cur_ram["star_crest"] and verdict.crest_pickup_step is None:
                    monitor.snapshot(step=step_idx, tag="crest_acquired", env=env)

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
                has_crest = _read_gallery_ram(bridge)["star_crest"]
                verdict.note_episode_end(
                    step=step_idx,
                    reason=str(reason),
                    checkpoint_success=bool(env._progress.checkpoint_success),
                    has_crest=has_crest,
                    gallery_wrong=bool(env._progress.gallery_wrong_breached),
                )
                monitor.snapshot(
                    step=step_idx,
                    tag="episode_end",
                    env=env,
                    extra={"reason": reason},
                )
                print(f"\n{verdict.summary()}\n", flush=True)
                if str(reason) == "gallery_wrong_portrait":
                    print(
                        "[gallery] episode killed by gallery_wrong_portrait — "
                        "often a false positive during the crest-reveal cutscene "
                        "(confirm byte bumps while raw=0). Re-run with latest fix.",
                        flush=True,
                    )
                print(
                    "[gallery] halted — Esc/Q to exit (cutscene skip still runs in background).",
                    flush=True,
                )
                while True:
                    if kb.is_pressed("esc") or kb.is_pressed("q"):
                        break
                    time.sleep(0.1)
                break
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        bridge.close()

    return 1 if verdict.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
