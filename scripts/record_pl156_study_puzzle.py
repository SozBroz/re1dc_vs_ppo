"""Human harness: pl156 study room (20A) puzzle demos on visible C-RE1.

Play from live tip ``pl156`` (already in ``20A`` after ``208->20A``) through
the three puzzle beats that should become separate planner PLs, then take
``20A:explosive_rounds:1`` (today's single chunk step / future ``pl160``).

Four capture steps (mark 1/2/3 live; acquire auto-closes on env success):

  1  study_insect_switch   — wall bug specimen switch / button (drains tank)
  2  study_push_fishtank   — push aquarium into drained space
  3  study_push_cupboard   — push bookcase/cupboard to reveal ammo
  (auto) acquire           — take explosive_rounds (current pl157 pay)

Each decision is logged with pose / gs / anim / inventory so demos can be
re-cut offline even if a marker was late. Success writes:

  data/demos/planner_loyal/pl156_<stamp>_ok.npz
  data/demos/planner_loyal/pl156_<stamp>_ok_actions.json
  data/demos/planner_loyal/pl156_<stamp>_ok_timeline.json

Split later::

  venv\\Scripts\\python.exe -u scripts\\split_pl148_demo_segments.py path\\to\\*_ok.npz

Controls: WASD move | Shift+W run | Z/E interact | Space stand still | Esc quit
  1 / 2 / 3  — mark segment complete (edge-triggered)
  Pad: stick | Square run | Cross interact | Circle stand still
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

TAG = "pl156h"
# In-game card slots are 00–11 only; slot 12 → plugin error_code=4 on loadstate.
SLOT = 11
DEFAULT_START = 156
DEFAULT_OUT = ROOT / "data" / "demos" / "planner_loyal"
CURRICULUM = ROOT / "curriculum" / "planner_loyal_one_leg.json"

# Future PL beat ids (chunk still has a single acquire — recording-only contract).
PLANNED_SEGMENTS = (
    {
        "key": "1",
        "id": "study_insect_switch",
        "label": "button / insect switch (drain tank)",
        "future_pl_note": "pl157 do_puzzle after pl156 enter",
    },
    {
        "key": "2",
        "id": "study_push_fishtank",
        "label": "push fish tank / aquarium",
        "future_pl_note": "pl158 do_puzzle after switch",
    },
    {
        "key": "3",
        "id": "study_push_cupboard",
        "label": "push cupboard / bookcase (reveal ammo)",
        "future_pl_note": "pl159 do_puzzle before acquire",
    },
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

from re1_rl.demo_record import (  # noqa: E402
    DEMO_SCHEMA_VERSION,
    DemoEpisode,
    buttons_to_action,
    demo_filename,
    write_demo,
)
from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION  # noqa: E402
from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES, RE1Env  # noqa: E402
from re1_rl.pushable import PUSH_GAME_STATE, touching_pushable  # noqa: E402
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


def _edge_markers(kb: Any, held: dict[str, bool]) -> list[str]:
    """Return segment ids newly pressed this frame (1/2/3)."""
    fired: list[str] = []
    if kb is None:
        return fired
    for seg in PLANNED_SEGMENTS:
        key = seg["key"]
        try:
            down = bool(kb.is_pressed(key))
        except Exception:
            down = False
        was = bool(held.get(key))
        held[key] = down
        if down and not was:
            fired.append(seg["id"])
    return fired


def _inv_names(state: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for slot in state.get("inventory_slots") or state.get("inventory") or []:
        if isinstance(slot, dict):
            n = slot.get("name") or slot.get("item") or slot.get("id")
            if n:
                names.append(str(n))
        elif slot:
            names.append(str(slot))
    # also flat list of strings
    items = state.get("items") or state.get("item_names")
    if isinstance(items, (list, tuple)):
        for n in items:
            if n and str(n) not in names:
                names.append(str(n))
    return names


def _snap(env: RE1Env, *, decision_i: int, action: int, reward: float) -> dict[str, Any]:
    state = env._read_state(track_items=True)
    q = env._planner_loyal_queue
    beat = None
    qidx = -1
    if q is not None:
        cur = q.current if isinstance(q.current, dict) else {}
        beat = cur.get("beat_id") or cur.get("pickup_id") or cur.get("edge_id")
        qidx = int(getattr(q, "index", -1) or -1)
    gs = int(state.get("game_state") or 0)
    return {
        "i": int(decision_i),
        "action": int(action),
        "action_name": ACTION_NAMES[int(action)] if 0 <= action < len(ACTION_NAMES) else "?",
        "reward": float(reward),
        "room": state.get("room_id"),
        "jill": [int(state.get("x") or 0), int(state.get("z") or 0)],
        "facing": int(state.get("facing") or 0),
        "gs": hex(gs),
        "push_gs": gs == PUSH_GAME_STATE,
        "pushing": bool(touching_pushable(state)),
        "anim": int(state.get("player_anim") or 0),
        "hp": int(state.get("hp") or 0),
        "in_control": bool(state.get("in_control")),
        "inv": _inv_names(state),
        "beat": beat,
        "qidx": qidx,
        "has_explosive": any("explosive" in n.lower() for n in _inv_names(state)),
    }


def _write_sidecars(
    npz_path: Path,
    *,
    actions: list[int],
    meta: dict[str, Any],
    timeline: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> tuple[Path, Path]:
    actions_path = npz_path.with_name(npz_path.stem + "_actions.json")
    timeline_path = npz_path.with_name(npz_path.stem + "_timeline.json")
    actions_path.write_text(
        json.dumps(
            {
                "source": str(npz_path.relative_to(ROOT)).replace("\\", "/"),
                "start_cell_recorded": meta.get("start_cell"),
                "objective": meta.get("objective"),
                "replay_start_cell": "pl156",
                "runtime": "recomp",
                "room": "20A",
                "puzzle": "study_insect_tank_cupboard",
                "planned_segments": list(PLANNED_SEGMENTS),
                "segments": segments,
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
    timeline_path.write_text(
        json.dumps(
            {
                "source": str(npz_path.relative_to(ROOT)).replace("\\", "/"),
                "segments": segments,
                "planned_segments": list(PLANNED_SEGMENTS),
                "timeline": timeline,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return actions_path, timeline_path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Record pl156 study-room puzzle demos with segment markers for future PLs"
    )
    ap.add_argument("--start-index", type=int, default=DEFAULT_START)
    ap.add_argument("--target", type=int, default=8, help="stop after this many successes")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--keep-failures", action="store_true")
    ap.add_argument("--reset-pause", type=float, default=1.5)
    ap.add_argument(
        "--speed",
        type=int,
        default=100,
        help="wall-clock pace vs 60fps (100 ≈ one 8-frame step / 133ms)",
    )
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="headless reset to pl156 and exit")
    args = ap.parse_args()

    if args.smoke:
        os.environ["RE1_RECOMP_VISIBLE"] = "0"

    assert_tag_not_fleet(TAG)
    pin_path = _configure_planner_loyal_env(int(args.start_index), 7632)
    start_cell = f"pl{int(args.start_index):02d}"
    out_dir = args.out if args.out.is_absolute() else ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    kb = None
    try:
        kb = _import_keyboard()
    except SystemExit:
        kb = None
        print("[pl156] keyboard package missing — pad only (segment keys need keyboard)", flush=True)
    pad = None
    try:
        pad = _open_playstation_pad()
    except Exception as exc:
        print(f"[pl156] pad open failed ({exc})", flush=True)

    bridge = make_recomp_bridge(tag=TAG, work_slot=SLOT, timeout=180.0)
    bridge.start_server()
    print(
        f"[pl156] launching C-RE1 tag={TAG} pin={start_cell} "
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
        beat = None
        if q is not None and isinstance(q.current, dict):
            beat = q.current.get("beat_id") or q.current.get("pickup_id")
        snap = {
            "room": state.get("room_id"),
            "jill": [int(state.get("x") or 0), int(state.get("z") or 0)],
            "facing": int(state.get("facing") or 0),
            "beat": beat,
            "qidx": int(getattr(q, "index", -1) or -1),
        }
        print(json.dumps({"smoke": True, **snap}), flush=True)
        ok = str(snap["room"]) == "20A"
        try:
            env.close()
            bridge.close()
            pin_path.unlink(missing_ok=True)
        except Exception:
            pass
        return 0 if ok else 1

    commit = _git_commit()
    pace_s = (float(env.frame_skip) / 60.0) * (100.0 / max(1, int(args.speed)))
    print(
        f"\n[pl156] {start_cell} (20A STUDY) → explosive_rounds\n"
        f"  Today: one env step pay on acquire (pl157).\n"
        f"  Mark future PLs as you finish each beat:\n"
        f"    1  {PLANNED_SEGMENTS[0]['label']}  → future pl157\n"
        f"    2  {PLANNED_SEGMENTS[1]['label']}  → future pl158\n"
        f"    3  {PLANNED_SEGMENTS[2]['label']}  → future pl159\n"
        f"  Then take the ammo (future pl160 / current pl157 pay).\n"
        f"  Timeline always saved for offline re-cuts.\n"
        f"  paced {int(args.speed)}% ≈ {pace_s * 1000:.0f}ms/decision  out={out_dir}\n"
        f"  WASD | Shift+W run | Z/E interact | Space stand | Esc quit\n",
        flush=True,
    )

    import numpy as np

    n_episodes = 0
    n_success = 0
    quit_requested = False
    marker_held: dict[str, bool] = {}
    try:
        while not quit_requested and n_success < int(args.target):
            obs, _info = env.reset()
            episode = DemoEpisode()
            timeline: list[dict[str, Any]] = []
            segments: list[dict[str, Any]] = []
            marked: set[str] = set()
            n_episodes += 1
            step_idx = 0
            decision_i = 0
            ep_reward = 0.0
            last_action = -1
            objective = _objective_id(env)
            print(
                f"\n=== episode {n_episodes} start={start_cell} objective={objective} "
                f"tally {n_success}/{args.target} ===",
                flush=True,
            )
            while True:
                buttons, force_noop, quit_requested = _poll_buttons(kb, pad)
                for seg_id in _edge_markers(kb, marker_held):
                    if seg_id in marked:
                        print(f"  [mark] {seg_id} already set @ decision {decision_i}", flush=True)
                        continue
                    marked.add(seg_id)
                    label = next(s["label"] for s in PLANNED_SEGMENTS if s["id"] == seg_id)
                    rec = {
                        "id": seg_id,
                        "label": label,
                        "end_decision": int(decision_i),
                        "end_env_step": int(step_idx),
                        "marked_at": time.strftime("%H:%M:%S"),
                    }
                    segments.append(rec)
                    print(f"  [mark] {seg_id}  ({label}) @ decision={decision_i}", flush=True)

                if quit_requested:
                    print("[pl156] quit — discarding current episode", flush=True)
                    break

                mask = np.asarray(env.action_masks(), dtype=bool)
                decision = int(mask.sum()) > 1
                if decision:
                    action = (
                        0
                        if force_noop
                        else buttons_to_action(
                            buttons, env._sticky_input.as_dict(), button_map=ACTION_BUTTON_MAP
                        )
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
                    timeline.append(
                        _snap(env, decision_i=decision_i, action=int(action), reward=float(reward))
                    )
                    decision_i += 1

                bd = dict(info.get("reward_breakdown") or {})
                events = _fmt_events(bd)
                if not args.quiet and (events or action != last_action):
                    print(
                        f"  s{step_idx:04d} d{decision_i:04d} {ACTION_NAMES[action]:<12} "
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
                        "planner_step_success"
                        if step_ok
                        else ("checkpoint_success" if success else "truncated")
                    )
                    # Auto-close acquire segment on success.
                    if success and "acquire_explosive_rounds" not in marked:
                        segments.append(
                            {
                                "id": "acquire_explosive_rounds",
                                "label": "take 20A:explosive_rounds:1 (current pl157 / future pl160)",
                                "end_decision": int(decision_i),
                                "end_env_step": int(step_idx),
                                "marked_at": time.strftime("%H:%M:%S"),
                                "auto": True,
                            }
                        )
                    n_success += int(success)
                    missing = [s["id"] for s in PLANNED_SEGMENTS if s["id"] not in marked]
                    print(
                        f"=== episode {n_episodes} end: {'SUCCESS' if success else 'fail'} "
                        f"reason={reason} decisions={len(episode)} return={ep_reward:+.2f} "
                        f"marks={len(marked)}/3 missing={missing} ===",
                        flush=True,
                    )
                    if len(episode) > 0 and (success or args.keep_failures):
                        # Fill start_decision for each segment from previous end.
                        segs_out: list[dict[str, Any]] = []
                        prev_end = 0
                        for seg in segments:
                            row = dict(seg)
                            row["start_decision"] = int(prev_end)
                            row["n_decisions"] = max(0, int(seg["end_decision"]) - int(prev_end))
                            segs_out.append(row)
                            prev_end = int(seg["end_decision"])
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
                            "room": "20A",
                            "puzzle": "study_insect_tank_cupboard",
                            "planned_segments": list(PLANNED_SEGMENTS),
                            "segments": segs_out,
                            "segment_marks_complete": len(missing) == 0,
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
                        apath, tpath = _write_sidecars(
                            path,
                            actions=actions,
                            meta=meta,
                            timeline=timeline,
                            segments=segs_out,
                        )
                        print(
                            f"[pl156] saved {path.relative_to(ROOT)}\n"
                            f"        + {apath.name}\n"
                            f"        + {tpath.name}",
                            flush=True,
                        )
                        if success and missing:
                            print(
                                f"[pl156] WARN: success without all marks {missing} — "
                                "timeline still usable for offline split",
                                flush=True,
                            )
                    if not quit_requested:
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
            pin_path.unlink(missing_ok=True)
        except Exception:
            pass
    print(f"[pl156] done episodes={n_episodes} success={n_success}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
