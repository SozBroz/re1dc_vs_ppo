"""Human harness: pl82 -> pl83 (armor_vent_far) on visible C-RE1.

Play through the training env (discrete PPO actions + obs + mask). Each
success writes ``data/demos/planner_loyal/pl82_*_ok.npz`` for DemoBCAux and
an action-id JSON the same ``env.step`` path can replay. After every save
the harness resets to live pl82 and open-loop replays that take.

Does not mint cells, does not talk to the learner, does not touch the fleet pin.

  venv\\Scripts\\python.exe -u scripts\\record_pl82_armor_far.py
  venv\\Scripts\\python.exe -u scripts\\record_pl82_armor_far.py --target 12

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

TAG = "pl83h"
SLOT = 10
DEFAULT_START = 82
DEFAULT_OUT = ROOT / "data" / "demos" / "planner_loyal"
CURRICULUM = ROOT / "curriculum" / "planner_loyal_one_leg.json"

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

from re1_rl.armor_room_puzzle import armor_vent_step_complete  # noqa: E402
from re1_rl.demo_record import (  # noqa: E402
    DEMO_SCHEMA_VERSION,
    DemoEpisode,
    buttons_to_action,
    demo_filename,
    write_demo,
)
from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION  # noqa: E402
from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES, RE1Env  # noqa: E402
from scripts.play_human import _import_keyboard, _keys_to_buttons  # noqa: E402
from scripts.record_planner_demo import (  # noqa: E402
    _configure_planner_loyal_env,
    _fmt_events,
    _git_commit,
    _objective_id,
)


def _poll_buttons(kb: Any, pad: Any) -> tuple[dict[str, bool], bool, bool]:
    """Return (buttons, force_noop, quit). Space / Circle clear sticky walk."""
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


def _write_action_json(npz_path: Path, actions: list[int], meta: dict[str, Any]) -> Path:
    dest = npz_path.with_name(npz_path.stem + "_actions.json")
    dest.write_text(
        json.dumps(
            {
                "source": str(npz_path.relative_to(ROOT)).replace("\\", "/"),
                "start_cell_recorded": meta.get("start_cell"),
                "objective": meta.get("objective"),
                "replay_start_cell": f"pl{int(meta.get('start_index') or DEFAULT_START):02d}",
                "runtime": "recomp",
                "frame_skip": int(meta.get("frame_skip") or 8),
                "n_actions_space": int(meta.get("n_actions") or len(ACTION_NAMES)),
                "steps": len(actions),
                "actions": actions,
                "action_names": [ACTION_NAMES[a] for a in actions],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return dest


def replay_recorded_actions(env: RE1Env, actions: list[int]) -> dict[str, Any]:
    """Open-loop ``env.step`` of a just-recorded take from the pinned cell."""
    import numpy as np

    env.reset()
    i = 0
    last: dict[str, Any] = {}
    for _ in range(len(actions) + 80):
        mask = np.asarray(env.action_masks(), dtype=bool)
        if int(mask.sum()) > 1:
            if i >= len(actions):
                break
            action = int(actions[i])
            i += 1
            if action < 0 or action >= len(mask) or not mask[action]:
                action = 0
        else:
            action = 0
        _obs, _reward, terminated, truncated, info = env.step(action)
        last = dict(info or {})
        bd = dict(last.get("reward_breakdown") or {})
        step_ok = (
            float(bd.get("planner_step_success") or 0) > 0
            or float(bd.get("checkpoint_success") or 0) > 0
        )
        if step_ok or terminated or truncated:
            state = env._read_state(track_items=False)
            return {
                "ok": bool(step_ok),
                "consumed": i,
                "far": bool(armor_vent_step_complete({"beat_id": "armor_vent_far"}, state)),
                "reason": last.get("episode_failure") or ("planner_step_success" if step_ok else None),
            }
    state = env._read_state(track_items=False)
    return {
        "ok": False,
        "consumed": i,
        "far": bool(armor_vent_step_complete({"beat_id": "armor_vent_far"}, state)),
        "reason": "exhausted",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Record pl82->pl83 armor_vent_far demos on C-RE1")
    ap.add_argument("--start-index", type=int, default=DEFAULT_START)
    ap.add_argument("--target", type=int, default=12, help="stop after this many verified successes")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--keep-failures", action="store_true")
    ap.add_argument("--no-verify", action="store_true", help="skip open-loop replay after each save")
    ap.add_argument("--reset-pause", type=float, default=1.5)
    ap.add_argument(
        "--speed",
        type=int,
        default=100,
        help="wall-clock pace vs 60fps (100 = one 8-frame PPO step per 133ms, like BizHawk 100%%). "
        "C-RE1 set_speed is a no-op; this sleep is what makes it playable.",
    )
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="headless reset to pl82 and exit")
    args = ap.parse_args()

    if args.smoke:
        os.environ["RE1_RECOMP_VISIBLE"] = "0"

    assert_tag_not_fleet(TAG)
    pin_path = _configure_planner_loyal_env(int(args.start_index), 7623)
    start_cell = f"pl{int(args.start_index):02d}"
    out_dir = args.out if args.out.is_absolute() else ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    kb = None
    try:
        kb = _import_keyboard()
    except SystemExit:
        kb = None
        print("[pl82] keyboard package missing — pad only", flush=True)
    pad = None
    try:
        pad = _open_playstation_pad()
    except Exception as exc:
        print(f"[pl82] pad open failed ({exc})", flush=True)

    bridge = make_recomp_bridge(tag=TAG, work_slot=SLOT, timeout=180.0)
    bridge.start_server()
    print(
        f"[pl82] launching C-RE1 tag={TAG} pin={start_cell} "
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
            "facing": int(state.get("facing") or 0),
            "beat": beat,
            "qidx": int(getattr(q, "index", -1) or -1),
        }
        print(json.dumps({"smoke": True, **snap}), flush=True)
        ok = str(snap["room"]) == "205" and snap["beat"] == "armor_vent_far"
        try:
            env.close()
            bridge.close()
            pin_path.unlink()
        except Exception:
            pass
        return 0 if ok else 1
    commit = _git_commit()

    pace_s = (float(env.frame_skip) / 60.0) * (100.0 / max(1, int(args.speed)))
    print(
        f"\n[pl82] {start_cell} -> armor_vent_far  target={int(args.target)}  out={out_dir}\n"
        f"  PPO map = BizHawk record_planner_demo (buttons_to_action -> env.step, "
        f"schema v{int(OBS_SCHEMA_VERSION)}, {len(ACTION_NAMES)} actions)\n"
        f"  paced {int(args.speed)}% ≈ {pace_s * 1000:.0f}ms/decision "
        f"(C-RE1 itself is unlocked; restart this script to pick up the cap)\n"
        "  WASD move | Shift+W run | Z/E interact | Space stand still | Esc quit\n"
        "  Pad: stick | Square run | Cross interact | Circle stand still\n"
        "  Each SUCCESS saves an NN demo npz, then replays it from pl82.\n",
        flush=True,
    )

    import numpy as np

    n_episodes = 0
    n_success = 0
    n_verified = 0
    quit_requested = False
    try:
        while not quit_requested and n_verified < int(args.target):
            obs, _info = env.reset()
            episode = DemoEpisode()
            n_episodes += 1
            step_idx = 0
            ep_reward = 0.0
            last_action = -1
            objective = _objective_id(env)
            print(
                f"\n=== episode {n_episodes} start={start_cell} objective={objective} "
                f"tally {n_verified}/{args.target} verified ===",
                flush=True,
            )
            while True:
                buttons, force_noop, quit_requested = _poll_buttons(kb, pad)
                if quit_requested:
                    print("[pl82] quit — discarding current episode", flush=True)
                    break
                mask = np.asarray(env.action_masks(), dtype=bool)
                decision = int(mask.sum()) > 1
                if decision:
                    action = 0 if force_noop else buttons_to_action(
                        buttons, env._sticky_input.as_dict(), button_map=ACTION_BUTTON_MAP
                    )
                    if not mask[action]:
                        action = 0
                    episode.add(obs, action, mask)
                else:
                    action = 0
                t0 = time.perf_counter()
                obs, reward, terminated, truncated, info = env.step(action)
                leftover = pace_s - (time.perf_counter() - t0)
                if leftover > 0:
                    time.sleep(leftover)
                step_idx += 1
                ep_reward += float(reward)
                if decision:
                    episode.note_reward(float(reward))
                bd = dict(info.get("reward_breakdown") or {})
                events = _fmt_events(bd)
                if not args.quiet and (events or action != last_action):
                    print(
                        f"  s{step_idx:04d} {ACTION_NAMES[action]:<12} "
                        f"r={float(reward):+.3f} ep={ep_reward:+.2f} {events}",
                        flush=True,
                    )
                last_action = action
                step_ok = (
                    float(bd.get("planner_step_success", 0.0) or 0.0) > 0.0
                    or float(bd.get("checkpoint_success", 0.0) or 0.0) > 0.0
                )
                if terminated or truncated or step_ok:
                    success = bool(step_ok) or bool(env._progress.checkpoint_success)
                    reason = info.get("episode_failure") or (
                        "planner_step_success" if step_ok else (
                            "checkpoint_success" if success else "truncated"
                        )
                    )
                    n_success += int(success)
                    print(
                        f"=== episode {n_episodes} end: {'SUCCESS' if success else 'fail'} "
                        f"reason={reason} steps={step_idx} decisions={len(episode)} "
                        f"return={ep_reward:+.2f} | raw {n_success}/{n_episodes} ===",
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
                            "runtime": "recomp",
                            "commit": commit,
                            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "recorded_by": "human",
                            "env_steps": int(step_idx),
                        }
                        path = write_demo(
                            out_dir / demo_filename(start_cell=start_cell, success=success),
                            episode,
                            meta,
                        )
                        actions = [int(a) for a in episode.actions]
                        json_path = _write_action_json(path, actions, meta)
                        print(
                            f"[pl82] saved {path.relative_to(ROOT)} + {json_path.name} "
                            f"({len(episode)} decisions)",
                            flush=True,
                        )
                        if success and not args.no_verify:
                            print("[pl82] verifying open-loop replay from pl82…", flush=True)
                            check = replay_recorded_actions(env, actions)
                            meta["open_loop_replay"] = bool(check.get("ok") or check.get("far"))
                            write_demo(path, episode, meta)
                            if meta["open_loop_replay"]:
                                n_verified += 1
                                print(
                                    f"[pl82] replay PASS  verified {n_verified}/{args.target}",
                                    flush=True,
                                )
                            else:
                                print(
                                    f"[pl82] replay FAIL consumed={check.get('consumed')} "
                                    f"reason={check.get('reason')} — npz kept for BC, "
                                    "not counted as verified",
                                    flush=True,
                                )
                    time.sleep(float(args.reset_pause))
                    break
    except KeyboardInterrupt:
        print("\n[pl82] interrupted", flush=True)
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
        except OSError:
            pass
    print(
        json.dumps(
            {
                "episodes": n_episodes,
                "successes": n_success,
                "verified": n_verified,
                "target": int(args.target),
                "out": str(out_dir),
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
