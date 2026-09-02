"""Record human demonstrations for a planner-loyal leg (default pl79 -> pl80).

You play through the *training* env: every ``frame_skip`` batch your held
buttons are mapped to one discrete PPO action, sent through ``RE1Env.step``
and stored with the exact observation Dict + legal-action mask the policy
would have seen. Successful episodes (``checkpoint_success``) are written to
``data/demos/planner_loyal/plNN_<stamp>_ok.npz`` for ``combat_ppo.DemoBCAux``.

  python scripts/record_planner_demo.py                # pl79, keyboard + pad
  python scripts/record_planner_demo.py --speed 150    # faster emu
  python scripts/record_planner_demo.py --keep-failures

Keyboard: WASD move | Shift+W run | Z/E interact | R aim | F fire | Esc quit
Gamepad (focus EmuHawk): stick / d-pad | Square run | Cross interact | R1 aim | R2 fire

Isolated BizHawk on port 5801 (not a fleet worker port). The episode ends on
the same terminals as training (pl80 mint, divert, gas, timeout); it then
auto-resets to the pinned cell after a short pause.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_START_INDEX = 79
DEFAULT_PORT = 5801
DEFAULT_OUT = ROOT / "data" / "demos" / "planner_loyal"
CURRICULUM = ROOT / "curriculum" / "planner_loyal_one_leg.json"
DEFAULT_CHUNK = "data/planner_chunks/cp05_shield_key.json"


def _configure_planner_loyal_env(start_index: int, port: int) -> Path:
    """Mirror fleet/local/planner_loyal.env.cmd, minus learner sync/captures."""
    os.environ.setdefault("RE1_CAMERA_WHITEN", "0")
    os.environ.setdefault("RE1_LAYERED_GEOMETRY", "0")
    os.environ["RE1_PLANNER_LOYAL"] = "1"
    os.environ.setdefault("RE1_PLANNER_CHUNK", DEFAULT_CHUNK)
    os.environ.setdefault("RE1_PLANNER_LOYAL_CELLS_ROOT", "states/planner_loyal")
    os.environ["RE1_CELL_TIMEOUT_FLAT_12M"] = "1"
    os.environ["RE1_YAWN_LEG_REPLAY"] = "0"
    os.environ["RE1_YAWN_PAYFORWARD_RIPPLE"] = "0"
    os.environ["RE1_YAWN_EXTEND_EPISODE_ON_CELL"] = "0"
    # A human recorder must never mint cells or talk to the learner.
    os.environ["RE1_YAWN_RAILS_SYNC"] = "0"
    os.environ["RE1_GO_EXPLORE_CAPTURE"] = "0"
    os.environ["RE1_GO_EXPLORE_SYNC"] = "0"
    os.environ["RE1_PB_CAPTURE"] = "0"
    os.environ["RE1_PB_V1_TYPEWRITER_ONLY"] = "0"
    os.environ["RE1_PB_DANGER_ROOMS"] = "0"
    pin = ROOT / "data" / "logs" / f"_demo_reset_pin_{port}.env"
    pin.parent.mkdir(parents=True, exist_ok=True)
    pin.write_text(
        f"RE1_PLANNER_RESET_PIN_INDEX={int(start_index)}\n"
        "RE1_PLANNER_RESET_PIN_RANGE=\n"
        "RE1_PLANNER_RESET_PIN_WEIGHTS=\n"
        "RE1_PLANNER_RESET_PIN_SET=\n"
        "RE1_PLANNER_RESET_PIN_SET_WEIGHT=\n",
        encoding="utf-8",
    )
    os.environ["RE1_PLANNER_RESET_PIN_FILE"] = str(pin)
    return pin


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT), text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _launch_emuhawk(port: int, log_path: Path) -> subprocess.Popen[Any]:
    from re1_rl.bizhawk_paths import EMUHAWK, emuhawk_argv

    if not EMUHAWK.is_file():
        raise FileNotFoundError(f"EmuHawk not found at {EMUHAWK}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = log_path.open("w", encoding="utf-8")
    argv = emuhawk_argv(port=port)
    print(f"[demo] launching EmuHawk port={port} log={log_path}", flush=True)
    return subprocess.Popen(argv, cwd=str(EMUHAWK.parent), stdout=log_f, stderr=subprocess.STDOUT)


def _objective_id(env: Any) -> str:
    queue = getattr(env, "_planner_loyal_queue", None)
    if queue is not None:
        step = queue.current() or {}
        return str(step.get("beat_id") or step.get("site_id") or step.get("id") or f"step{queue.index}")
    try:
        return str((env._planner.current_objective() or {}).get("checkpoint_id") or "?")
    except AttributeError:
        return "?"


def _fmt_events(bd: dict[str, float]) -> str:
    keep = []
    for key, val in sorted(bd.items(), key=lambda kv: -abs(float(kv[1]))):
        if key == "step" or abs(float(val)) < 0.005:
            continue
        keep.append(f"{key}={float(val):+.3f}")
    return " ".join(keep[:5])


def main() -> int:
    ap = argparse.ArgumentParser(description="record human demos through the planner-loyal env")
    ap.add_argument("--start-index", type=int, default=DEFAULT_START_INDEX, help="plNN cell to pin")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--speed", type=int, default=100, help="in-control BizHawk speed %%")
    ap.add_argument("--cutscene-speed", type=int, default=6400)
    ap.add_argument("--skip-chunk", type=int, default=600)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--keep-failures", action="store_true", help="also save failed episodes")
    ap.add_argument("--input", choices=("both", "keyboard", "gamepad"), default="both")
    ap.add_argument("--connect-timeout", type=float, default=120.0)
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--reset-pause", type=float, default=2.0)
    ap.add_argument("--quiet", action="store_true", help="only print episode summaries")
    args = ap.parse_args()

    port = int(args.port)
    pin_path = _configure_planner_loyal_env(int(args.start_index), port)
    start_cell = f"pl{int(args.start_index):02d}"

    import numpy as np

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.demo_record import (
        DEMO_SCHEMA_VERSION,
        DemoEpisode,
        buttons_to_action,
        demo_filename,
        write_demo,
    )
    from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION
    from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES, RE1Env
    from scripts.play_human import (
        _import_keyboard,
        _kill_stale_listener,
        _poll_play_buttons,
        _read_emuhawk_joypad,
        wait_for_emuhawk,
    )

    use_keyboard = args.input in ("keyboard", "both")
    use_gamepad = args.input in ("gamepad", "both")
    _kill_stale_listener(port)
    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    bridge = BizHawkClient(port=port, timeout=120.0)
    bridge.start_server()
    emu_log = ROOT / "data" / "logs" / f"emuhawk_demo_{port}.log"
    proc = None if args.no_launch else _launch_emuhawk(port, emu_log)
    wait_for_emuhawk(bridge, proc, port=port, timeout=float(args.connect_timeout), log_path=emu_log)
    bridge.set_speed(int(args.speed))

    env = RE1Env(
        curriculum_path=CURRICULUM,
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=True,
        camera_whiten=False,
    )
    env._ram_skip.training_speed = int(args.speed)
    env._ram_skip.cutscene_speed = int(args.cutscene_speed)
    env._ram_skip.skip_chunk = int(args.skip_chunk)
    env._ram_skip.invisible_during_skip = False
    env.knife_echo_joypad = False

    out_dir = args.out if args.out.is_absolute() else ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = _git_commit()
    kb = _import_keyboard() if use_keyboard else None

    print(
        f"\n[demo] start={start_cell} pin={pin_path.name} port={port} speed={args.speed}% "
        f"input={args.input} out={out_dir}\n"
        "  Keyboard: WASD move | Shift+W run | Z/E interact | R aim | F fire | Esc quit\n"
        "  Gamepad: focus EmuHawk - stick/d-pad | Square run | Cross interact | R1 aim | R2 fire\n"
        "  Episode auto-resets on any terminal; successes are saved, failures "
        f"{'saved' if args.keep_failures else 'discarded'}.\n",
        flush=True,
    )
    if use_gamepad:
        sample = _read_emuhawk_joypad(bridge, debug=True)
        pressed = [k for k, v in sample.items() if v]
        if pressed:
            print(f"[demo] joypad sample: {pressed}", flush=True)

    n_episodes = 0
    n_success = 0
    quit_requested = False
    try:
        while not quit_requested:
            obs, _info = env.reset()
            episode = DemoEpisode()
            n_episodes += 1
            step_idx = 0
            ep_reward = 0.0
            last_action = -1
            objective = _objective_id(env)
            print(f"\n=== episode {n_episodes} start={start_cell} objective={objective} ===", flush=True)
            while True:
                if kb is not None and (kb.is_pressed("esc") or kb.is_pressed("q")):
                    quit_requested = True
                    print("[demo] quit requested; discarding current episode", flush=True)
                    break
                mask = np.asarray(env.action_masks(), dtype=bool)
                decision = int(mask.sum()) > 1
                if decision:
                    buttons = _poll_play_buttons(
                        kb=kb, bridge=bridge, use_keyboard=use_keyboard, use_emuhawk_joypad=use_gamepad
                    )
                    action = buttons_to_action(
                        buttons, env._sticky_input.as_dict(), button_map=ACTION_BUTTON_MAP
                    )
                    if not mask[action]:
                        action = 0
                    episode.add(obs, action, mask)
                else:
                    action = 0
                    time.sleep(0.02)
                obs, reward, terminated, truncated, info = env.step(action)
                step_idx += 1
                ep_reward += float(reward)
                if decision:
                    episode.note_reward(float(reward))
                bd = dict(info.get("reward_breakdown") or {})
                events = _fmt_events(bd)
                if not args.quiet and (events or action != last_action):
                    print(
                        f"  s{step_idx:04d} {ACTION_NAMES[action]:<12} r={float(reward):+.3f} "
                        f"ep={ep_reward:+.2f} {events}",
                        flush=True,
                    )
                last_action = action
                if terminated or truncated:
                    success = bool(env._progress.checkpoint_success)
                    reason = info.get("episode_failure") or (
                        "checkpoint_success" if success else "truncated"
                    )
                    n_success += int(success)
                    print(
                        f"=== episode {n_episodes} end: {'SUCCESS' if success else 'fail'} "
                        f"reason={reason} steps={step_idx} decisions={len(episode)} "
                        f"return={ep_reward:+.2f} | tally {n_success}/{n_episodes} ===",
                        flush=True,
                    )
                    if len(episode) > 0 and (success or args.keep_failures):
                        meta = {
                            "schema": DEMO_SCHEMA_VERSION,
                            "obs_schema_version": int(OBS_SCHEMA_VERSION),
                            "n_actions": len(ACTION_NAMES),
                            "start_cell": start_cell,
                            "start_index": int(args.start_index),
                            "objective": objective,
                            "success": success,
                            "reason": str(reason),
                            "frame_skip": int(env.frame_skip),
                            "curriculum": str(CURRICULUM.relative_to(ROOT)),
                            "speed": int(args.speed),
                            "commit": commit,
                            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "recorded_by": "human",
                            "env_steps": int(step_idx),
                        }
                        path = write_demo(
                            out_dir / demo_filename(start_cell=start_cell, success=success), episode, meta
                        )
                        print(f"[demo] saved {path.relative_to(ROOT)} ({len(episode)} decisions)", flush=True)
                    time.sleep(float(args.reset_pause))
                    break
    except KeyboardInterrupt:
        print("\n[demo] interrupted", flush=True)
    finally:
        try:
            env.close()
        except Exception:  # noqa: BLE001
            pass
        if proc is not None and proc.poll() is None:
            proc.terminate()
        bridge.close()
        try:
            pin_path.unlink()
        except OSError:
            pass
    print(json.dumps({"episodes": n_episodes, "successes": n_success, "out": str(out_dir)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
