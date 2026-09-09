"""Human pay-watch: pl98 dining 2F statue push on visible C-RE1.

Loads the live planner-loyal tip (default ``pl98`` / ``dining_2f_enter``),
hands you the PPO action map, and prints every step's reward breakdown —
especially ``dining_statue_progress``, pad/nav crumbs, and (on terminal)
``hop_score`` plus the failed-hop push override learning targets.

On a failed cell, steps with ``dining_statue_progress > 0`` still compose to
``Y = L`` (same rule as armor shove), so you can see positive push pay even
when the hop settles negative.

Does not mint cells, does not talk to the learner, does not touch the fleet pin.

Usage
-----
  venv\\Scripts\\python.exe -u scripts\\play_pl98_statue_reward_harness.py
  venv\\Scripts\\python.exe -u scripts\\play_pl98_statue_reward_harness.py --start-index 98
  venv\\Scripts\\python.exe -u scripts\\play_pl98_statue_reward_harness.py --smoke

Keyboard: WASD move | Shift+W run | Z/E interact | R aim | F fire | Space stand still | Esc quit
Pad: stick/d-pad | Square run | Cross interact | R1 aim | R2 fire | Circle stand still
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ECO_PY = Path(r"D:\re1_recomp\ecosystem\py")
RECOMP_PY = Path(r"D:\re1_recomp\py")
for p in (ECO_PY, RECOMP_PY, ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

TAG = "pl98h"
SLOT = 11
DEFAULT_START = 98
CURRICULUM = ROOT / "curriculum" / "planner_loyal_one_leg.json"

# Channels worth always surfacing while shoving / approaching the balcony statue.
STATUE_KEYS = (
    "dining_statue_progress",
    "dining_statue",
    "hop_score",
    "planner_step_success",
    "planner_divert",
    "planner_timeout",
    "softlock",
    "step",
    "death",
)

os.environ.setdefault("RE1_CAMERA_WHITEN", "0")
os.environ.setdefault("RE1_LAYERED_GEOMETRY", "0")
os.environ["RE1_PLANNER_LOYAL"] = "1"
os.environ.setdefault("RE1_PLANNER_CHUNK", "data/planner_chunks/cp05_shield_key.json")
os.environ.setdefault("RE1_PLANNER_LOYAL_CELLS_ROOT", "states/planner_loyal")
os.environ["RE1_CELL_TIMEOUT_FLAT_12M"] = "1"
os.environ["RE1_YAWN_LEG_REPLAY"] = "0"
os.environ["RE1_YAWN_PAYFORWARD_RIPPLE"] = "0"
os.environ["RE1_YAWN_EXTEND_EPISODE_ON_CELL"] = "0"
os.environ["RE1_YAWN_RAILS_SYNC"] = "0"
os.environ["RE1_GO_EXPLORE_CAPTURE"] = "0"
os.environ["RE1_GO_EXPLORE_SYNC"] = "0"
os.environ["RE1_PB_CAPTURE"] = "0"
os.environ["RE1_PB_V1_TYPEWRITER_ONLY"] = "0"
os.environ["RE1_PB_DANGER_ROOMS"] = "0"
os.environ["RE1_ECOSYSTEM_BRIDGE"] = "recomp"
os.environ["RE1_ECOSYSTEM_TAG"] = TAG
os.environ["RE1_RL_TAG"] = TAG
os.environ["RE1_RECOMP_CELLS"] = "1"
os.environ["RE1_RECOMP_ROOT"] = r"D:\re1_recomp"
os.environ["RE1_RECOMP_VISIBLE"] = "1"
for key in (
    "RE1_LEARNER_HOST",
    "RE1_LEARNER_PORT",
    "FLEET_LEARNER_HOST",
    "FLEET_LEARNER_PORT",
    "RE1_YAWN_RAILS_ROOT",
):
    os.environ.pop(key, None)

from bridge_factory import assert_tag_not_fleet, make_recomp_bridge  # noqa: E402
from play_cell_recomp import _open_playstation_pad  # noqa: E402

from re1_rl.demo_record import buttons_to_action  # noqa: E402
from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES, RE1Env  # noqa: E402
from re1_rl.planner_hop_score import (  # noqa: E402
    compose_hop_learning_target,
    hop_local_reward_from_bd,
)
from scripts.play_human import _import_keyboard, _keys_to_buttons  # noqa: E402
from scripts.record_planner_demo import (  # noqa: E402
    _configure_planner_loyal_env,
    _objective_id,
)


def _poll_buttons(kb: Any, pad: Any) -> tuple[dict[str, bool], bool, bool]:
    buttons: dict[str, bool] = {}
    quit_req = False
    force_noop = False
    if kb is not None:
        try:
            if kb.is_pressed("esc") or kb.is_pressed("q"):
                quit_req = True
            if kb.is_pressed("space"):
                force_noop = True
            buttons.update(_keys_to_buttons(kb))
        except Exception:
            pass
    if pad is not None:
        try:
            pad_btns = pad.poll()
            if pad_btns.pop("circle", False):
                force_noop = True
            buttons.update(pad_btns)
        except Exception:
            pass
    buttons.pop("circle", None)
    return buttons, force_noop, quit_req


def _fmt_statue_bd(bd: dict[str, float]) -> str:
    parts: list[str] = []
    for key in STATUE_KEYS:
        val = float(bd.get(key, 0.0) or 0.0)
        if abs(val) < 1e-6:
            continue
        parts.append(f"{key}={val:+.3f}")
    # Any other non-tiny channel not already listed.
    for key, raw in sorted(bd.items(), key=lambda kv: -abs(float(kv[1] or 0.0))):
        if key in STATUE_KEYS or key == "step":
            continue
        val = float(raw or 0.0)
        if abs(val) < 0.005:
            continue
        parts.append(f"{key}={val:+.3f}")
        if len(parts) >= 10:
            break
    return " ".join(parts)


def _statue_pose(state: dict[str, Any]) -> str:
    return (
        f"room={state.get('room_id')} "
        f"jill=({int(state.get('x') or 0)},{int(state.get('z') or 0)}) "
        f"face={int(state.get('facing') or 0)} "
        f"statue=({int(state.get('dining_statue_x') or 0)},"
        f"{int(state.get('dining_statue_z') or 0)}) "
        f"knocked={int(bool(state.get('dining_statue_knocked')))} "
        f"gs={hex(int(state.get('game_state') or 0))} "
        f"anim={hex(int(state.get('player_anim') or 0))}"
    )


def _print_episode_learning(
    step_locals: list[tuple[int, float, bool, dict[str, float]]],
    *,
    S: float,
    success: bool,
) -> None:
    """Replay fleet compose: failed hop + dining push => Y=L on those steps."""
    if not step_locals:
        return
    print(
        f"  --- learning targets (S={S:+.3f} success={success}) ---",
        flush=True,
    )
    push_pay = 0.0
    overridden = 0
    for step_idx, loc, push, bd in step_locals:
        y = compose_hop_learning_target(
            S, loc, positive_push_override=bool(push)
        )
        mark = ""
        if push and loc > 0.0 and S < 0.0 and abs(y - loc) < 1e-9:
            mark = " PUSH_OVERRIDE"
            overridden += 1
            push_pay += loc
        dsp = float(bd.get("dining_statue_progress") or 0.0)
        extra = f" dsp={dsp:+.3f}" if abs(dsp) > 1e-9 else ""
        if abs(loc) > 1e-6 or push or mark:
            print(
                f"  Y s{step_idx:04d} L={loc:+.3f} -> Y={y:+.3f}{extra}{mark}",
                flush=True,
            )
    if S < 0.0:
        print(
            f"  push_override_steps={overridden} push_L_sum={push_pay:+.3f} "
            f"(failed hop still keeps these as +Y)",
            flush=True,
        )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="pl98 dining statue push — print reward signals on C-RE1"
    )
    ap.add_argument("--start-index", type=int, default=DEFAULT_START)
    ap.add_argument("--reset-pause", type=float, default=1.5)
    ap.add_argument(
        "--speed",
        type=int,
        default=100,
        help="wall-clock pace vs 60fps (100 ≈ one 8-frame PPO step per 133ms)",
    )
    ap.add_argument(
        "--every",
        action="store_true",
        help="print every decision step (default: print when signals move)",
    )
    ap.add_argument("--smoke", action="store_true", help="headless reset and exit")
    args = ap.parse_args()

    if args.smoke:
        os.environ["RE1_RECOMP_VISIBLE"] = "0"

    assert_tag_not_fleet(TAG)
    pin_path = _configure_planner_loyal_env(int(args.start_index), 7624)
    start_cell = f"pl{int(args.start_index):02d}"

    kb = None
    try:
        kb = _import_keyboard()
    except SystemExit:
        kb = None
        print("[pl98] keyboard package missing — pad only", flush=True)
    pad = None
    try:
        pad = _open_playstation_pad()
    except Exception as exc:
        print(f"[pl98] pad open failed ({exc})", flush=True)

    bridge = make_recomp_bridge(tag=TAG, work_slot=SLOT, timeout=180.0)
    bridge.start_server()
    print(
        f"[pl98] launching C-RE1 tag={TAG} pin={start_cell} "
        f"visible={os.environ.get('RE1_RECOMP_VISIBLE')}",
        flush=True,
    )
    bridge.wait_for_client(headless=bool(args.smoke))
    env = RE1Env(
        curriculum_path=CURRICULUM,
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=False,
        camera_whiten=False,
    )
    env.knife_echo_joypad = False

    if args.smoke:
        env.reset()
        state = env._read_state(track_items=False)
        q = env._planner_loyal_queue
        beat = q.current.get("beat_id") if q is not None and isinstance(q.current, dict) else None
        snap = {
            "room": state.get("room_id"),
            "jill": [int(state.get("x") or 0), int(state.get("z") or 0)],
            "statue": [
                int(state.get("dining_statue_x") or 0),
                int(state.get("dining_statue_z") or 0),
            ],
            "knocked": bool(state.get("dining_statue_knocked")),
            "beat": beat,
            "qidx": int(getattr(q, "index", -1) or -1),
        }
        print(json.dumps({"smoke": True, **snap}), flush=True)
        ok = str(snap["room"]) == "202"
        try:
            env.close()
            bridge.close()
            pin_path.unlink()
        except Exception:
            pass
        return 0 if ok else 1

    import numpy as np

    pace_s = (float(env.frame_skip) / 60.0) * (100.0 / max(1, int(args.speed)))
    print(
        f"\n[pl98] {start_cell} dining statue pay-watch\n"
        f"  paced {int(args.speed)}% ≈ {pace_s * 1000:.0f}ms/decision\n"
        "  WASD move | Shift+W run | Z/E interact | Space stand still | Esc quit\n"
        "  Pad: stick | Square run | Cross interact | Circle stand still\n"
        "  Prints dining_statue_progress / hop_score / compose Y on episode end.\n"
        "  Esc after a fail to see PUSH_OVERRIDE lines for shove steps.\n",
        flush=True,
    )

    n_episodes = 0
    quit_requested = False
    try:
        while not quit_requested:
            obs, _info = env.reset()
            n_episodes += 1
            step_idx = 0
            ep_reward = 0.0
            last_action = -1
            objective = _objective_id(env)
            step_locals: list[tuple[int, float, bool, dict[str, float]]] = []
            print(
                f"\n=== episode {n_episodes} start={start_cell} "
                f"objective={objective} ===",
                flush=True,
            )
            state0 = env._read_state(track_items=False)
            print(f"  {_statue_pose(state0)}", flush=True)

            while True:
                buttons, force_noop, quit_requested = _poll_buttons(kb, pad)
                if quit_requested:
                    print("[pl98] quit — discarding current episode", flush=True)
                    break
                mask = np.asarray(env.action_masks(), dtype=bool)
                decision = int(mask.sum()) > 1
                if decision:
                    action = (
                        0
                        if force_noop
                        else buttons_to_action(
                            buttons,
                            env._sticky_input.as_dict(),
                            button_map=ACTION_BUTTON_MAP,
                        )
                    )
                    if not mask[action]:
                        action = 0
                else:
                    action = 0

                t0 = time.perf_counter()
                obs, reward, terminated, truncated, info = env.step(action)
                leftover = pace_s - (time.perf_counter() - t0)
                if leftover > 0:
                    time.sleep(leftover)

                step_idx += 1
                ep_reward += float(reward)
                bd = dict(info.get("reward_breakdown") or {})
                # Match async_fleet: buffer stores env scalar; peel S on terminal.
                scalar = float(reward)
                S_here = float(bd.get("hop_score") or 0.0)
                loc_preview = hop_local_reward_from_bd(bd)
                push = float(bd.get("dining_statue_progress") or 0.0) > 0.0
                step_locals.append((step_idx, scalar, push, bd))

                events = _fmt_statue_bd(bd)
                moved = (
                    push
                    or abs(float(bd.get("dining_statue") or 0.0)) > 1e-6
                    or abs(S_here) > 1e-6
                    or abs(scalar) > 0.02
                    or action != last_action
                )
                if args.every or moved:
                    state = env._read_state(track_items=False)
                    print(
                        f"  s{step_idx:04d} {ACTION_NAMES[action]:<12} "
                        f"r={scalar:+.3f} L_hop={loc_preview:+.3f} "
                        f"ep={ep_reward:+.2f} {events}",
                        flush=True,
                    )
                    if push or args.every:
                        print(f"         {_statue_pose(state)}", flush=True)
                last_action = action

                step_ok = (
                    float(bd.get("planner_step_success", 0.0) or 0.0) > 0.0
                    or float(bd.get("checkpoint_success", 0.0) or 0.0) > 0.0
                )
                if terminated or truncated or step_ok:
                    success = bool(step_ok) or bool(env._progress.checkpoint_success)
                    reason = info.get("episode_failure") or (
                        "planner_step_success"
                        if step_ok
                        else ("checkpoint_success" if success else "truncated")
                    )
                    S = float(bd.get("hop_score") or 0.0)
                    # Peel S from the last buffered scalar before compose.
                    if abs(S) > 1e-9 and step_locals:
                        si, sc, pu, bdi = step_locals[-1]
                        step_locals[-1] = (si, float(sc) - S, pu, bdi)
                    print(
                        f"=== episode {n_episodes} end: "
                        f"{'SUCCESS' if success else 'fail'} reason={reason} "
                        f"steps={step_idx} return={ep_reward:+.2f} "
                        f"S={S:+.3f} ===",
                        flush=True,
                    )
                    _print_episode_learning(
                        step_locals,
                        S=S if abs(S) > 1e-9 else (0.96 if success else -4.0),
                        success=success,
                    )
                    time.sleep(float(args.reset_pause))
                    break
    finally:
        try:
            env.close()
        except Exception:
            pass
        try:
            bridge.close()
        except Exception:
            pass
        try:
            pin_path.unlink()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
