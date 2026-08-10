"""PPO-identical human harness — every commit calls ``RE1Env.step(discrete_action)``.

Movement uses the same press-to-chunk gate as ``play_human`` (one ``frame_skip``
batch per press). Combat uses dedicated keys that run the full knife / gun
macros inside a single env step (not raw ``bridge.step``).

Keys (keyboard)
---------------
  WASD / arrows     movement (forward / back / turn)
  Shift + W         run forward
  Shift + W + A/D   run forward + turn (two composed PPO steps)
  1                 attack       (action 7, neutral aim+fire)
  2                 attack_up    (action 6)
  3                 attack_down  (action 8)
  Z / E             interact     (action 9)
  Esc / Q           quit

Usage
-----
  python scripts/play_ppo_harness.py --pb-bundle states/pb/champions/room_108/champion.json --speed 100
  python scripts/play_ppo_harness.py --start-savestate states/jill_control_fresh.State --no-training-parity
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import (  # noqa: E402
    ATTACK_ACTION,
    ATTACK_DOWN_ACTION,
    ATTACK_UP_ACTION,
    INTERACT_ACTION,
)
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.pushable import (
    FORWARD_ACTION,
    RUN_FORWARD_ACTION,
    TURN_LEFT_ACTION,
    TURN_RIGHT_ACTION,
)
from re1_rl.sticky_input import human_step_gate
from scripts.play_human import (  # noqa: E402
    DEFAULT_CURRICULUM,
    _import_keyboard,
    _kill_stale_listener,
    _poll_play_buttons,
    bootstrap_env,
    bootstrap_fleet_reset,
    configure_ram_skip,
    launch_emuhawk,
    wait_for_emuhawk,
)
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402

HARNESS_PORT = 7791
WINDOW_TITLE = "PPO_HARNESS"
IDLE_POLL_S = 0.033
COMBAT_KEY_ACTIONS: dict[str, int] = {
    "1": ATTACK_ACTION,
    "2": ATTACK_UP_ACTION,
    "3": ATTACK_DOWN_ACTION,
}
COMBAT_ACTIONS = frozenset(COMBAT_KEY_ACTIONS.values())
MOVEMENT_KEYS = frozenset({"up", "down", "left", "right", "square"})


def _resolve_movement_actions(buttons: dict[str, bool]) -> list[int]:
    """Map latched movement buttons to one or more discrete env actions.

    Curved run uses sticky composition (run_forward then turn_*), matching PPO.
    """
    up = bool(buttons.get("up"))
    down = bool(buttons.get("down"))
    left = bool(buttons.get("left"))
    right = bool(buttons.get("right"))
    run = bool(buttons.get("square"))
    if up and run and left and not down and not right:
        return [RUN_FORWARD_ACTION, TURN_LEFT_ACTION]
    if up and run and right and not down and not left:
        return [RUN_FORWARD_ACTION, TURN_RIGHT_ACTION]
    if up and not down and not left and not right:
        return [RUN_FORWARD_ACTION if run else FORWARD_ACTION]
    if down and not up and not left and not right:
        return [2]
    if left and not up and not down and not right:
        return [3]
    if right and not up and not down and not left:
        return [4]
    return []


def _resolve_movement_action(buttons: dict[str, bool]) -> int | None:
    actions = _resolve_movement_actions(buttons)
    return actions[0] if actions else None


def _combat_key_edge(kb, prev: set[str]) -> int | None:
    if kb is None:
        return None
    for key, action in COMBAT_KEY_ACTIONS.items():
        if kb.is_pressed(key) and key not in prev:
            return action
    return None


def _pressed_combat_keys(kb) -> set[str]:
    if kb is None:
        return set()
    return {k for k in COMBAT_KEY_ACTIONS if kb.is_pressed(k)}


def _format_combat_panel(
    *,
    step_idx: int,
    action: int,
    reward: float,
    breakdown: dict[str, float],
    info: dict[str, Any],
) -> str:
    name = ACTION_NAMES[int(action)]
    state = info.get("state") or {}
    frames = state.get("step_emulated_frames", "?")
    lines = [
        f"--- ppo step {step_idx} action={action} ({name}) frames={frames} ---",
        (
            f"room={info.get('room_id')} hp={info.get('hp')} "
            f"pos={info.get('pos')} reward={reward:+.4f}"
        ),
    ]
    for key in (
        "enemy_damage",
        "enemy_kill",
        "attack_miss",
        "ammo_spend",
        "ammo_waste",
        "hp",
        "death",
    ):
        val = float(breakdown.get(key, 0.0) or 0.0)
        if abs(val) > 1e-9:
            lines.append(f"  {key}: {val:+.4f}")
    attack_report = info.get("attack_report")
    if attack_report:
        lines.append(f"  attack_report: {json.dumps(attack_report, sort_keys=True)}")
    knife_report = info.get("knife_anim_report")
    if knife_report:
        lines.append(f"  knife_anim_report: {json.dumps(knife_report, sort_keys=True)}")
    dmg = int(state.get("enemy_damage", 0) or 0)
    kills = int(state.get("enemy_kills", 0) or 0)
    if dmg or kills:
        lines.append(f"  combat: dmg={dmg} kills={kills}")
    return "\n".join(lines)


def _format_combat_mask(env: RE1Env, state: dict[str, Any]) -> str:
    mask = env.action_masks(state)
    parts: list[str] = []
    for action_id in sorted(COMBAT_ACTIONS):
        legal = bool(mask[action_id]) if action_id < len(mask) else False
        tag = "OK" if legal else "MASKED"
        parts.append(f"{ACTION_NAMES[action_id]}={tag}")
    enemies = state.get("enemies") or []
    alive = sum(1 for e in enemies if int(e.get("hp", 0) or 0) > 0)
    return f"[mask] {' | '.join(parts)} | enemies_alive={alive}"


def _load_pb_bundle(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent.resolve()
    bundle = dict(data)
    bundle["state_path"] = str((root / Path(data["state_path"]).name).resolve())
    bundle["sidecar_path"] = str((root / Path(data["sidecar_path"]).name).resolve())
    return bundle


def main() -> int:
    ap = argparse.ArgumentParser(description="PPO-identical RE1 harness (env.step)")
    ap.add_argument("--port", type=int, default=HARNESS_PORT)
    ap.add_argument("--speed", type=int, default=100, help="in-control BizHawk speed %%")
    ap.add_argument("--cutscene-speed", type=int, default=6400)
    ap.add_argument("--cutscene-chunk", type=int, default=600)
    ap.add_argument("--frame-skip", type=int, default=8)
    ap.add_argument(
        "--curriculum",
        default=str(DEFAULT_CURRICULUM.relative_to(ROOT)),
    )
    ap.add_argument(
        "--pb-bundle",
        type=Path,
        default=None,
        help="champion.json for PB sidecar reset (room 108 etc.)",
    )
    ap.add_argument("--start-savestate", type=Path, default=None)
    ap.add_argument(
        "--training-parity",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="fleet dining bootstrap (default off; use --pb-bundle or --start-savestate)",
    )
    ap.add_argument(
        "--async-cutscene-skip",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="match fleet env (async skip inside env.step)",
    )
    ap.add_argument(
        "--input",
        choices=("keyboard", "both"),
        default="keyboard",
    )
    ap.add_argument("--connect-timeout", type=float, default=90.0)
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument(
        "--kill-stale",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    ap.add_argument(
        "--show-mask",
        action="store_true",
        help="print combat action mask after every step",
    )
    args = ap.parse_args()

    os.environ.setdefault("ATTACK_LOG", "1")

    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    port = int(args.port)
    if args.kill_stale:
        _kill_stale_listener(port)

    bridge = BizHawkClient(port=port, timeout=120.0)
    bridge.start_server()
    log_path = ROOT / "data" / "logs" / f"emuhawk_ppo_harness_{port}.log"
    proc = None if args.no_launch else launch_emuhawk(port, log_path)
    wait_for_emuhawk(
        bridge,
        proc,
        port=port,
        timeout=float(args.connect_timeout),
        log_path=log_path,
    )
    bridge.set_speed(int(args.speed))

    curriculum_path = ROOT / args.curriculum
    env = RE1Env(
        curriculum_path=curriculum_path,
        bridge=bridge,
        frame_skip=max(1, int(args.frame_skip)),
        project_root=ROOT,
        async_cutscene_skip=bool(args.async_cutscene_skip),
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

    reset_options: dict[str, Any] | None = None
    if args.pb_bundle is not None:
        reset_options = {"pb_bundle": _load_pb_bundle(args.pb_bundle.resolve())}
        obs, info = env.reset(options=reset_options)
        state = info.get("state") or env._prev_state
        print(
            f"[ppo] PB reset room={state.get('room_id')} hp={state.get('hp')} "
            f"bundle={args.pb_bundle.name}",
            flush=True,
        )
    elif args.start_savestate is not None:
        state, _goal = bootstrap_env(
            env,
            savestate=args.start_savestate.resolve(),
            checkpoint_meta=None,
            play_speed=int(args.speed),
            cutscene_speed=int(args.cutscene_speed),
            turbo_patches=True,
            invisible_cutscenes=False,
            warp_room=None,
            skip_to_control=True,
        )
        obs = env._build_obs(env.bridge.build_frame_stack(), state)
        info = {"room_id": state.get("room_id"), "hp": state.get("hp"), "state": state}
    elif args.training_parity:
        state, _goal = bootstrap_fleet_reset(
            env,
            play_speed=int(args.speed),
            cutscene_speed=int(args.cutscene_speed),
            skip_chunk=int(args.cutscene_chunk),
            turbo_patches=True,
            invisible_cutscenes=False,
        )
        obs = env._build_obs(env.bridge.build_frame_stack(), state)
        info = {"room_id": state.get("room_id"), "hp": state.get("hp"), "state": state}
    else:
        obs, info = env.reset()
        state = info.get("state") or env._prev_state

    use_keyboard = args.input in ("keyboard", "both")
    use_pad = args.input == "both"
    kb = _import_keyboard() if use_keyboard else None

    step_idx = 0
    step_armed = True
    combat_keys_prev: set[str] = set()

    print(
        "\n"
        f"[ppo] {WINDOW_TITLE} on port {port}. Focus EmuHawk.\n"
        "  WASD move (one chunk per press) | Shift+W run\n"
        "  1=knife  2=attack  3=attack_up  4=attack_down  |  Z/E=interact\n"
        "  Each combat key runs the full PPO macro via env.step().\n"
        "  Esc/Q quit.\n",
        flush=True,
    )
    if args.show_mask:
        print(_format_combat_mask(env, state), flush=True)

    try:
        while True:
            if kb is not None and (kb.is_pressed("esc") or kb.is_pressed("q")):
                print("[ppo] quit", flush=True)
                break

            buttons = _poll_play_buttons(
                kb=kb,
                bridge=bridge,
                use_keyboard=use_keyboard,
                use_emuhawk_joypad=use_pad,
            )

            combat_action = _combat_key_edge(kb, combat_keys_prev)
            combat_keys_prev = _pressed_combat_keys(kb)

            actions_to_run: list[int] = []
            if combat_action is not None:
                actions_to_run = [int(combat_action)]
            else:
                gate_buttons = {k: v for k, v in buttons.items() if k in MOVEMENT_KEYS}
                if buttons.get("cross") and not gate_buttons:
                    gate_buttons = {"cross": True}
                should_step, step_armed = human_step_gate(gate_buttons, armed=step_armed)
                if not should_step:
                    time.sleep(IDLE_POLL_S)
                    continue
                if buttons.get("cross") and not any(
                    buttons.get(k) for k in MOVEMENT_KEYS
                ):
                    actions_to_run = [INTERACT_ACTION]
                else:
                    actions_to_run = _resolve_movement_actions(buttons)
                if not actions_to_run:
                    time.sleep(IDLE_POLL_S)
                    continue

            terminated = truncated = False
            info: dict[str, Any] = {}
            for action_id in actions_to_run:
                obs, reward, terminated, truncated, info = env.step(int(action_id))
                step_idx += 1
                breakdown = dict(info.get("reward_breakdown") or {})
                info["state"] = env._prev_state
                print(
                    _format_combat_panel(
                        step_idx=step_idx,
                        action=int(action_id),
                        reward=float(reward),
                        breakdown=breakdown,
                        info=info,
                    ),
                    flush=True,
                )
                if args.show_mask:
                    print(_format_combat_mask(env, env._prev_state), flush=True)
                if terminated or truncated:
                    break
            if terminated or truncated:
                reason = info.get("episode_failure") or "done"
                print(f"[ppo] episode ended: {reason}", flush=True)
                obs, info = env.reset(options=reset_options)
                step_armed = True
                if args.show_mask:
                    print(_format_combat_mask(env, env._prev_state), flush=True)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        bridge.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
