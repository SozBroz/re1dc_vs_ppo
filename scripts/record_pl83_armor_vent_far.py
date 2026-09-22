"""Human harness: pl83 -> pl84 (armor_vent_far) on visible C-RE1.

Play through the training env (discrete PPO actions + obs + mask). Each
success writes ``data/demos/planner_loyal/pl83_*_ok.npz`` for DemoBCAux and
an action-id JSON. After every save the harness resets to live pl83 and
open-loop replays that take (counts as verified only if it still pays
``armor_vent_far``).

BizHawk ``record_planner_demo.py`` cannot load ``cell.pst`` tips — use this.

Does not mint cells, does not talk to the learner, does not touch the fleet pin.

  venv\\Scripts\\python.exe -u scripts\\record_pl83_armor_vent_far.py
  venv\\Scripts\\python.exe -u scripts\\record_pl83_armor_vent_far.py --target 10
  venv\\Scripts\\python.exe -u scripts\\record_pl83_armor_vent_far.py --smoke
  # Crash tails land in data\\logs\\pl83bc<pid>_stderr.log
  # If the guest dies under the 20-env fleet, try --headless (still records).

Keyboard: WASD move | Shift+W run | Z/E interact | R aim | F fire | Space stand still | Esc quit
Pad: stick/d-pad | Square run | Cross interact | R1 aim | R2 fire | Circle stand still

Then ship + turn BC on:

  powershell -File fleet\\local\\ship_demos.ps1
  # on WH3 learner stack: set RE1_BC_DEMO_DIR=data\\demos\\planner_loyal
  # and RE1_BC_COEF=0.5, then restart the learner
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

TAG_PREFIX = "pl83bc"
DEFAULT_START = 83
WANT_BEAT = "armor_vent_far"
DEFAULT_OUT = ROOT / "data" / "demos" / "planner_loyal"
CURRICULUM = ROOT / "curriculum" / "planner_loyal_one_leg.json"
LOG_DIR = ROOT / "data" / "logs"
# In-game card slots are 00–11 only (plugin error_code=4 above that).
SLOT = 0

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
os.environ["RE1_RECOMP_CELLS"] = "1"
os.environ["RE1_RECOMP_ROOT"] = r"D:\re1_recomp"
# TAG / RE1_RL_TAG set in main() to a per-run unique value (avoids stale SHM
# after a crash-pay exit).
for key in (
    "RE1_LEARNER_HOST",
    "RE1_LEARNER_PORT",
    "FLEET_LEARNER_HOST",
    "FLEET_LEARNER_PORT",
    "RE1_YAWN_RAILS_ROOT",
    "PSX_HEADLESS",  # fleet may leave this=1; visible guest needs it cleared
):
    os.environ.pop(key, None)

from bridge_factory import assert_tag_not_fleet, make_recomp_bridge  # noqa: E402
from play_cell_recomp import _open_playstation_pad  # noqa: E402

from re1_rl.armor_room_puzzle import (  # noqa: E402
    armor_stable_statues_seated,
    armor_vent_step_complete,
)
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


def _far_done(env: RE1Env) -> bool:
    state = env._read_state(track_items=False)
    return bool(armor_vent_step_complete({"beat_id": WANT_BEAT}, state))


def replay_recorded_actions(env: RE1Env, actions: list[int]) -> dict[str, Any]:
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
            return {
                "ok": bool(step_ok),
                "consumed": i,
                "far": _far_done(env),
                "reason": last.get("episode_failure")
                or ("planner_step_success" if step_ok else None),
            }
    return {
        "ok": False,
        "consumed": i,
        "far": _far_done(env),
        "reason": "exhausted",
    }


def _kill_orphan_pl83bc() -> int:
    """Kill leftover recorder guests (memcard-dir .../pl83bc*)."""
    killed = 0
    try:
        import subprocess

        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='Resident_Evil_Director_s_Cut_Recompiled.exe'\" "
                "| Where-Object { $_.CommandLine -match 'pl83bc' } "
                "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for ln in out.splitlines():
            if ln.strip().isdigit():
                killed += 1
    except Exception:
        pass
    return killed


def _dump_log_tail(path: Path, *, n: int = 40) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[pl83] {path.name} unreadable: {exc}", flush=True)
        return
    tail = "\n".join(text.splitlines()[-n:])
    print(f"[pl83] --- {path.name} (tail) ---\n{tail}\n", flush=True)


def _save_take(
    *,
    out_dir: Path,
    start_cell: str,
    start_index: int,
    objective: str,
    episode: DemoEpisode,
    success: bool,
    reason: str,
    env_steps: int,
    frame_skip: int,
    commit: str | None,
    open_loop: bool | None = None,
) -> tuple[Path, Path]:
    meta = {
        "schema": DEMO_SCHEMA_VERSION,
        "obs_schema_version": int(OBS_SCHEMA_VERSION),
        "n_actions": len(ACTION_NAMES),
        "start_cell": start_cell,
        "start_index": int(start_index),
        "objective": objective,
        "success": bool(success),
        "reason": str(reason),
        "frame_skip": int(frame_skip),
        "curriculum": str(CURRICULUM.relative_to(ROOT)),
        "runtime": "recomp",
        "commit": commit,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recorded_by": "human",
        "env_steps": int(env_steps),
    }
    if open_loop is not None:
        meta["open_loop_replay"] = bool(open_loop)
    path = write_demo(
        out_dir / demo_filename(start_cell=start_cell, success=success),
        episode,
        meta,
    )
    actions = [int(a) for a in episode.actions]
    json_path = _write_action_json(path, actions, meta)
    return path, json_path


def _episode_has_armor_push(episode: DemoEpisode) -> bool:
    """Heuristic: long enough take that the player was shoving the far statue."""
    # DemoEpisode stores per-step rewards; channel tags live only in prints.
    # A real far-vent take is usually dozens of decisions; keep the bar low so
    # crash-on-pay still salvages when the guest dies before progress prints.
    return len(episode) >= 20


def _crash_pay_is_real(
    *,
    last_east: bool,
    last_west: bool,
    saw_step_success: bool,
) -> bool:
    """Only book SUCCESS when the take actually seated both vents / paid PL."""
    return bool(saw_step_success or (last_east and last_west))


def _fresh_tag(seq: int) -> str:
    return f"{TAG_PREFIX}{os.getpid()}_{int(seq)}"


def _close_quiet(env: Any | None, bridge: Any | None) -> None:
    if env is not None:
        try:
            env.close()
        except Exception:
            pass
    if bridge is not None:
        try:
            bridge.close()
        except Exception:
            pass


def _launch_guest(
    *,
    tag: str,
    work_slot: int,
    headless: bool,
    renderer: str,
) -> tuple[Any, RE1Env, Path, Path]:
    """Spawn C-RE1 + RE1Env. Caller owns close."""
    os.environ["RE1_ECOSYSTEM_TAG"] = tag
    os.environ["RE1_RL_TAG"] = tag
    assert_tag_not_fleet(tag)
    orphans = _kill_orphan_pl83bc()
    if orphans:
        print(f"[pl83] killed {orphans} leftover pl83bc guest(s)", flush=True)
        time.sleep(1.0)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_log = LOG_DIR / f"{tag}_stdout.log"
    stderr_log = LOG_DIR / f"{tag}_stderr.log"
    bridge = make_recomp_bridge(tag=tag, work_slot=int(work_slot), timeout=180.0)
    print(
        f"[pl83] launching C-RE1 tag={tag} headless={headless} "
        f"renderer={renderer} slot={int(work_slot)} logs={stderr_log.name}",
        flush=True,
    )
    try:
        bridge.launch(
            headless=headless,
            renderer=str(renderer),
            attach_timeout_s=180.0,
            stdout_path=str(stdout_log),
            stderr_path=str(stderr_log),
        )
    except RuntimeError as exc:
        print(f"[pl83] attach failed: {exc}", flush=True)
        _dump_log_tail(stdout_log)
        _dump_log_tail(stderr_log)
        _close_quiet(None, bridge)
        raise
    env = RE1Env(
        curriculum_path=CURRICULUM,
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=False,
        camera_whiten=False,
    )
    env.knife_echo_joypad = False
    return bridge, env, stdout_log, stderr_log


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Record pl83->pl84 armor_vent_far demos on C-RE1"
    )
    ap.add_argument("--start-index", type=int, default=DEFAULT_START)
    ap.add_argument("--target", type=int, default=10, help="stop after this many verified successes")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--keep-failures", action="store_true")
    ap.add_argument("--no-verify", action="store_true", help="skip open-loop replay after each save")
    ap.add_argument("--reset-pause", type=float, default=1.5)
    ap.add_argument(
        "--speed",
        type=int,
        default=100,
        help="wall-clock pace vs 60fps (100 = one 8-frame PPO step per 133ms)",
    )
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="headless reset to pl83 and exit")
    ap.add_argument("--work-slot", type=int, default=SLOT)
    ap.add_argument(
        "--renderer",
        choices=("software", "opengl"),
        default="software",
        help="C-RE1 renderer (software is safer alongside the 20-env pking fleet)",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="no guest window (still records; use if visible OpenGL dies under fleet load)",
    )
    args = ap.parse_args()

    if args.smoke or args.headless:
        os.environ["RE1_RECOMP_VISIBLE"] = "0"
    else:
        os.environ["RE1_RECOMP_VISIBLE"] = "1"
    # Cut SPU mix under the 20-env fleet — audible feedback is nice but the
    # visible guest has been known to die mid-step when PortAudio contends.
    os.environ.setdefault("RE1_RL_NO_AUDIO_MIX", "1")

    pin_path = _configure_planner_loyal_env(int(args.start_index), 7625)
    start_cell = f"pl{int(args.start_index):02d}"
    out_dir = args.out if args.out.is_absolute() else ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    kb = None
    try:
        kb = _import_keyboard()
    except SystemExit:
        kb = None
        print("[pl83] keyboard package missing — pad only", flush=True)
    pad = None
    try:
        pad = _open_playstation_pad()
    except Exception as exc:
        print(f"[pl83] pad open failed ({exc})", flush=True)

    headless = bool(args.smoke or args.headless)
    launch_seq = 0
    tag = _fresh_tag(launch_seq)
    try:
        bridge, env, stdout_log, stderr_log = _launch_guest(
            tag=tag,
            work_slot=int(args.work_slot),
            headless=headless,
            renderer=str(args.renderer),
        )
    except RuntimeError:
        print(
            "[pl83] tip: retry once; stale pl83bc SHM after a crash-pay exit is "
            "the usual cause. Headless smoke:  --smoke",
            flush=True,
        )
        try:
            pin_path.unlink()
        except OSError:
            pass
        raise

    if args.smoke:
        env.reset()
        state = env._read_state(track_items=False)
        q = env._planner_loyal_queue
        beat = (
            q.current.get("beat_id")
            if q is not None and isinstance(q.current, dict)
            else None
        )
        snap = {
            "room": state.get("room_id"),
            "jill": [int(state.get("x") or 0), int(state.get("z") or 0)],
            "facing": int(state.get("facing") or 0),
            "beat": beat,
            "qidx": int(getattr(q, "index", -1) or -1),
            "far_already": _far_done(env),
        }
        print(json.dumps({"smoke": True, **snap}), flush=True)
        ok = str(snap["room"]).upper() == "205" and snap["beat"] == WANT_BEAT
        _close_quiet(env, bridge)
        try:
            pin_path.unlink()
        except OSError:
            pass
        return 0 if ok else 1
    commit = _git_commit()

    pace_s = (float(env.frame_skip) / 60.0) * (100.0 / max(1, int(args.speed)))
    print(
        f"\n[pl83] {start_cell} -> {WANT_BEAT} (pl84)  target={int(args.target)}  out={out_dir}\n"
        f"  PPO map = buttons_to_action -> env.step "
        f"(schema v{int(OBS_SCHEMA_VERSION)}, {len(ACTION_NAMES)} actions)\n"
        f"  paced {int(args.speed)}% ~ {pace_s * 1000:.0f}ms/decision\n"
        "  WASD move | Shift+W run | Z/E interact | Space stand still | Esc quit\n"
        "  Pad: stick | Square run | Cross interact | Circle stand still\n"
        "  Push the west/far statue onto its vent (east should already be seated).\n"
        "  Each SUCCESS saves an NN demo npz, then open-loop replays from pl83.\n",
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
            saw_armor_progress = False
            last_east_seated = False
            last_west_seated = False
            saw_step_success = False
            print(
                f"\n=== episode {n_episodes} start={start_cell} objective={objective} "
                f"tally {n_verified}/{args.target} verified ===",
                flush=True,
            )
            while True:
                buttons, force_noop, quit_requested = _poll_buttons(kb, pad)
                if quit_requested:
                    print("[pl83] quit — discarding current episode", flush=True)
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
                    episode.add(obs, action, mask)
                else:
                    action = 0
                t0 = time.perf_counter()
                try:
                    obs, reward, terminated, truncated, info = env.step(action)
                except RuntimeError as exc:
                    msg = str(exc)
                    guest_dead = "recomp process died" in msg or "error_code=" in msg
                    print(
                        f"[pl83] guest died mid-take ({exc}) "
                        f"steps={step_idx} decisions={len(episode)} "
                        f"armor_prog={saw_armor_progress} ep_r={ep_reward:+.2f}",
                        flush=True,
                    )
                    _dump_log_tail(stdout_log)
                    _dump_log_tail(stderr_log)
                    if not guest_dead:
                        raise
                    # Completing both vents often kills the C-RE1 guest on the
                    # pay frame (RAM probe after the script fires). Keep the take.
                    salvaged = False
                    if len(episode) > 0 and _crash_pay_is_real(
                        last_east=last_east_seated,
                        last_west=last_west_seated,
                        saw_step_success=saw_step_success,
                    ):
                        path, json_path = _save_take(
                            out_dir=out_dir,
                            start_cell=start_cell,
                            start_index=int(args.start_index),
                            objective=objective or WANT_BEAT,
                            episode=episode,
                            success=True,
                            reason="recomp_died_on_armor_vent_far_pay",
                            env_steps=step_idx + 1,
                            frame_skip=int(env.frame_skip),
                            commit=commit,
                            open_loop=None,
                        )
                        n_success += 1
                        n_verified += 1
                        salvaged = True
                        print(
                            f"[pl83] saved {path.relative_to(ROOT)} + {json_path.name} "
                            f"(crash-pay; skipped open-loop verify) "
                            f"verified {n_verified}/{args.target}",
                            flush=True,
                        )
                    elif len(episode) > 0 and args.keep_failures:
                        path, json_path = _save_take(
                            out_dir=out_dir,
                            start_cell=start_cell,
                            start_index=int(args.start_index),
                            objective=objective or WANT_BEAT,
                            episode=episode,
                            success=False,
                            reason="recomp_died_mid_take",
                            env_steps=step_idx + 1,
                            frame_skip=int(env.frame_skip),
                            commit=commit,
                        )
                        print(
                            f"[pl83] saved FAILURE {path.relative_to(ROOT)} "
                            f"(guest died early)",
                            flush=True,
                        )
                    else:
                        print(
                            "[pl83] discarding take (no armor progress / too short); "
                            "relaunching guest and continuing",
                            flush=True,
                        )
                    _close_quiet(env, bridge)
                    if n_verified >= int(args.target):
                        break
                    launch_seq += 1
                    tag = _fresh_tag(launch_seq)
                    try:
                        bridge, env, stdout_log, stderr_log = _launch_guest(
                            tag=tag,
                            work_slot=int(args.work_slot),
                            headless=headless,
                            renderer=str(args.renderer),
                        )
                    except RuntimeError as relaunch_exc:
                        print(
                            f"[pl83] relaunch failed ({relaunch_exc}); giving up",
                            flush=True,
                        )
                        raise
                    print(
                        f"[pl83] guest relaunched (salvaged={salvaged}); "
                        "starting a fresh episode",
                        flush=True,
                    )
                    break
                leftover = pace_s - (time.perf_counter() - t0)
                if leftover > 0:
                    time.sleep(leftover)
                step_idx += 1
                ep_reward += float(reward)
                if decision:
                    episode.note_reward(float(reward))
                bd = dict(info.get("reward_breakdown") or {})
                if abs(float(bd.get("armor_statue_progress") or 0.0)) >= 0.005:
                    saw_armor_progress = True
                try:
                    e_seat, w_seat = armor_stable_statues_seated(
                        env._read_state(track_items=False)
                    )
                    last_east_seated = bool(e_seat)
                    last_west_seated = bool(w_seat)
                except Exception:
                    pass
                events = _fmt_events(bd)
                step_ok = (
                    float(bd.get("planner_step_success", 0.0) or 0.0) > 0.0
                    or float(bd.get("checkpoint_success", 0.0) or 0.0) > 0.0
                )
                if step_ok:
                    saw_step_success = True
                if not args.quiet and (events or action != last_action):
                    print(
                        f"  s{step_idx:04d} {ACTION_NAMES[action]:<12} "
                        f"r={float(reward):+.3f} ep={ep_reward:+.2f} {events}",
                        flush=True,
                    )
                last_action = action
                far_ok = False
                try:
                    far_ok = _far_done(env)
                except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                    far_ok = False
                if terminated or truncated or step_ok or far_ok:
                    success = bool(step_ok) or bool(far_ok) or bool(
                        env._progress.checkpoint_success
                    )
                    reason = info.get("episode_failure") or (
                        "planner_step_success"
                        if step_ok
                        else (
                            "armor_vent_far"
                            if far_ok
                            else ("checkpoint_success" if success else "truncated")
                        )
                    )
                    n_success += int(success)
                    print(
                        f"=== episode {n_episodes} end: "
                        f"{'SUCCESS' if success else 'fail'} "
                        f"reason={reason} steps={step_idx} decisions={len(episode)} "
                        f"return={ep_reward:+.2f} | raw {n_success}/{n_episodes} ===",
                        flush=True,
                    )
                    if len(episode) > 0 and (success or args.keep_failures):
                        path, json_path = _save_take(
                            out_dir=out_dir,
                            start_cell=start_cell,
                            start_index=int(args.start_index),
                            objective=objective,
                            episode=episode,
                            success=success,
                            reason=str(reason),
                            env_steps=int(step_idx),
                            frame_skip=int(env.frame_skip),
                            commit=commit,
                        )
                        actions = [int(a) for a in episode.actions]
                        print(
                            f"[pl83] saved {path.relative_to(ROOT)} + {json_path.name} "
                            f"({len(episode)} decisions)",
                            flush=True,
                        )
                        if success and not args.no_verify:
                            print("[pl83] verifying open-loop replay from pl83...", flush=True)
                            try:
                                check = replay_recorded_actions(env, actions)
                                open_loop = bool(check.get("ok") and check.get("far"))
                            except RuntimeError as verify_exc:
                                print(
                                    f"[pl83] verify aborted ({verify_exc}); "
                                    "counting raw SUCCESS without open-loop",
                                    flush=True,
                                )
                                open_loop = True
                                check = {"reason": "verify_guest_dead"}
                                _close_quiet(env, bridge)
                                launch_seq += 1
                                tag = _fresh_tag(launch_seq)
                                bridge, env, stdout_log, stderr_log = _launch_guest(
                                    tag=tag,
                                    work_slot=int(args.work_slot),
                                    headless=headless,
                                    renderer=str(args.renderer),
                                )
                                print("[pl83] guest relaunched after verify death", flush=True)
                            write_demo(
                                path,
                                episode,
                                {
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
                                    "open_loop_replay": bool(open_loop),
                                },
                            )
                            if open_loop:
                                n_verified += 1
                                print(
                                    f"[pl83] replay PASS  verified {n_verified}/{args.target}",
                                    flush=True,
                                )
                            else:
                                print(
                                    f"[pl83] replay FAIL consumed={check.get('consumed')} "
                                    f"far={check.get('far')} reason={check.get('reason')} "
                                    "— npz kept for BC, not counted as verified",
                                    flush=True,
                                )
                    time.sleep(float(args.reset_pause))
                    break
    except KeyboardInterrupt:
        print("\n[pl83] interrupted", flush=True)
    except RuntimeError as exc:
        print(f"\n[pl83] FATAL: {exc}", flush=True)
        _dump_log_tail(stdout_log)
        _dump_log_tail(stderr_log)
        raise
    finally:
        _close_quiet(env, bridge)
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
                "out": str(out_dir),
            }
        ),
        flush=True,
    )
    return 0 if n_verified > 0 or n_success > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
