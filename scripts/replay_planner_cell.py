#!/usr/bin/env python3
"""Replay the recorded inputs of a planner-loyal cell on the recomp guest.

Loads the predecessor ``cell.pst`` (``from_pl_slot``), plays the tape's
``leg_replay.json`` joypad spans through ``bridge.tape_play`` (same TAS path
capture used), then compares the end pose against ``tape["end"]``.

  venv\\Scripts\\python.exe -u scripts\\replay_planner_cell.py --slot 2
  venv\\Scripts\\python.exe -u scripts\\replay_planner_cell.py --slot 2 --watch
  venv\\Scripts\\python.exe -u scripts\\replay_planner_cell.py --slot 2 --record-mp4
  venv\\Scripts\\python.exe -u scripts\\replay_planner_cell.py --tape states\\planner_loyal\\cells\\pl02\\leg_replay.json

Notes:
- Recomp-only (planner cells are ``cell.pst`` grafts; BizHawk cannot load them).
- Never mints cells or talks to the learner (capture/sync env vars forced off,
  checkpoint freeze disarmed, isolated reset pin + non-fleet SHM tag).
- Omit ``--slot`` to replay the newest slot dir that contains a tape.

Modes (``--mode``):
- ``actions`` (default): steps ``tape['actions']`` through ``env.step`` —
  the exact capture path including feedback-driven item-menu dismissal.
  This is the verifier: PASS means tape + cells are deterministic.
- ``joypad``: open-loop TAS playback of raw pad bits. Best-effort around
  frame-precise item menus (a 1-frame turbo-poke phase difference vs capture
  can break a menu flow); good for footage, not for verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ECO_PY = Path(r"D:\re1_recomp\ecosystem\py")
for _p in (ECO_PY, ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

sys.path.insert(0, str(ROOT))

from re1_rl.attack_macro import FACING_RESTORE_TOL
from re1_rl.go_explore_merge import CELL_REPLAY_NAME, CELL_SIDECAR_NAME
from re1_rl.leg_replay import joypad_replay_spans, tape_has_joypad
from re1_rl.planner_loyal_cells import cell_state_filename

CURRICULUM = ROOT / "curriculum" / "planner_loyal_one_leg.json"
RECOMP_STATE_NAME = "cell.pst"
DEFAULT_TAG = "plreplay"
DEFAULT_WORK_SLOT = 9


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cell_dirs(cells_root: Path) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    try:
        names = sorted(p.name for p in cells_root.iterdir() if p.is_dir())
    except OSError:
        return out
    for name in names:
        if len(name) == 4 and name[:2] == "pl" and name[2:].isdigit():
            out.append((int(name[2:]), cells_root / name))
    return out


def _load_tape(slot: int | None, tape_arg: Path | None, cells_root: Path) -> tuple[dict[str, Any], Path, int]:
    if tape_arg is not None:
        tape_path = tape_arg.resolve()
        if not tape_path.is_file():
            raise SystemExit(f"ERROR: no tape: {tape_path}")
        tape = json.loads(tape_path.read_text(encoding="utf-8"))
        raw_to = tape.get("to_pl_slot", tape.get("to_checkpoint_index", -1))
        try:
            slot_n = int(raw_to if raw_to is not None else -1)
        except (TypeError, ValueError):
            slot_n = -1
        return tape, tape_path, slot_n
    if slot is None:
        for slot_n, cell_dir in reversed(_cell_dirs(cells_root)):
            cand = cell_dir / CELL_REPLAY_NAME
            if cand.is_file():
                tape = json.loads(cand.read_text(encoding="utf-8"))
                return tape, cand, slot_n
        raise SystemExit(f"ERROR: no cell with {CELL_REPLAY_NAME} under {cells_root}")
    cell_dir = cells_root / f"pl{int(slot):02d}"
    tape_path = cell_dir / CELL_REPLAY_NAME
    if not tape_path.is_file():
        raise SystemExit(f"ERROR: no tape: {tape_path}")
    tape = json.loads(tape_path.read_text(encoding="utf-8"))
    if not isinstance(tape, dict):
        raise SystemExit(f"ERROR: invalid tape JSON: {tape_path}")
    return tape, tape_path, int(slot)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slot", type=int, default=None, help="plNN slot (default: newest taped slot)")
    ap.add_argument("--tape", type=Path, default=None, help="Override leg_replay.json path")
    ap.add_argument("--cells-root", type=Path, default=None, help="Default: states/planner_loyal (--cells-root points at the planner root, 'cells' is appended)")
    ap.add_argument("--init-state", type=Path, default=None, help="Required for route_initial tapes (from_pl_slot < 0)")
    ap.add_argument("--tag", default=DEFAULT_TAG, help="Non-fleet recomp SHM tag")
    ap.add_argument("--work-slot", type=int, default=DEFAULT_WORK_SLOT)
    ap.add_argument("--chunk", default=None, help="Planner chunk (default: opening_to_lockpick)")
    ap.add_argument("--visible", action="store_true", help="Show the guest window (implied by --watch)")
    ap.add_argument("--watch", action="store_true", help="Leave the guest running at the end pose")
    ap.add_argument("--record-mp4", action="store_true", help="Timelapse the replay under data/footage/planner_replay/")
    ap.add_argument("--record-every", type=int, default=30, help="Screenshot cadence in played frames")
    ap.add_argument(
        "--mode",
        choices=("joypad", "actions"),
        default="actions",
        help="actions steps tape['actions'] through env.step (exact: same "
        "feedback-driven menu/cutscene handling as capture). joypad plays raw "
        "pad bits via bridge.tape_play (open-loop; best-effort around "
        "frame-precise item menus, good for footage).",
    )
    ap.add_argument("--settle", action="store_true", help="Run capture settle before the end-pose compare")
    ap.add_argument("--force-stale", action="store_true", help="Play even when pred State SHA != tape from_state_sha256")
    args = ap.parse_args()

    cells_root = ((Path(args.cells_root) if args.cells_root else ROOT / "states" / "planner_loyal") / "cells").resolve()
    tape, tape_path, slot_n = _load_tape(args.slot, args.tape, cells_root)
    if not bool(tape.get("planner_loyal", False)):
        print("[replay] WARN tape is not planner_loyal; continuing anyway", flush=True)
    if not tape_has_joypad(tape):
        raise SystemExit(
            f"ERROR: {tape_path} has no joypad stream — recapture with "
            "RE1_PLANNER_LEG_REPLAY=1 (action-only replay is not supported)."
        )
    actions = [int(a) for a in tape.get("actions") or []]
    if not actions:
        raise SystemExit(f"ERROR: empty actions in {tape_path}")

    raw_from = tape.get("from_pl_slot", tape.get("from_checkpoint_index", -1))
    try:
        from_slot = int(raw_from if raw_from is not None else -1)
    except (TypeError, ValueError):
        from_slot = -1
    if from_slot < 0:
        if args.init_state is None:
            raise SystemExit("ERROR: route_initial tape needs --init-state <fresh.State/.pst>")
        state_path = Path(args.init_state).resolve()
        sidecar_path = None
        pred_dir = state_path.parent
        want_sha = str(tape.get("from_state_sha256") or "")
    else:
        pred_dir = cells_root / f"pl{from_slot:02d}"
        state_path = pred_dir / RECOMP_STATE_NAME
        if not state_path.is_file():
            legacy = pred_dir / cell_state_filename()
            raise SystemExit(
                f"ERROR: predecessor {state_path} missing "
                f"(found={legacy.name if legacy.is_file() else 'none'}; "
                "recomp replay needs cell.pst — BizHawk cell.State cannot load here)."
            )
        sidecar_path = pred_dir / CELL_SIDECAR_NAME
        if not sidecar_path.is_file():
            raise SystemExit(f"ERROR: missing predecessor sidecar {sidecar_path}")
        want_sha = str(tape.get("from_state_sha256") or "")
        if not want_sha:
            try:
                meta = json.loads((pred_dir / "meta.json").read_text(encoding="utf-8-sig"))
                want_sha = str(meta.get("state_sha256") or "")
            except (OSError, json.JSONDecodeError):
                want_sha = ""
    if want_sha:
        got_sha = _sha256(state_path)
        if got_sha != want_sha and not args.force_stale:
            raise SystemExit(
                f"ERROR: predecessor State sha mismatch (disk {got_sha[:12]} != "
                f"tape {want_sha[:12]}). Pass --force-stale to play anyway."
            )
        if got_sha != want_sha:
            print(f"[replay] WARN stale predecessor (--force-stale) disk={got_sha[:12]}", flush=True)

    spans = joypad_replay_spans(tape)
    if not spans:
        raise SystemExit(f"ERROR: no joypad spans decoded from {tape_path}")
    total = sum(len(bits) for bits, _ in spans)

    # Recorder-grade env: planner queue on, captures/learner off, isolated pin.
    # Pin the predecessor so any tip-fallback path matches the tape chain
    # (env.reset must also honor options.pb_bundle — see caller_pb guard).
    from scripts.record_planner_demo import DEFAULT_CHUNK, _configure_planner_loyal_env

    _configure_planner_loyal_env(max(int(from_slot), 0), 7699)
    os.environ["RE1_PLANNER_LEG_REPLAY"] = "0"
    os.environ["RE1_PLANNER_LEG_REPLAY"] = "0"
    if args.chunk:
        os.environ["RE1_PLANNER_CHUNK"] = str(args.chunk)
    else:
        os.environ.setdefault("RE1_PLANNER_CHUNK", DEFAULT_CHUNK)
    os.environ["RE1_ECOSYSTEM_BRIDGE"] = "recomp"
    os.environ["RE1_ECOSYSTEM_TAG"] = str(args.tag)
    os.environ["RE1_RL_TAG"] = str(args.tag)
    os.environ["RE1_RECOMP_CELLS"] = "1"
    os.environ["RE1_RECOMP_ROOT"] = r"D:\re1_recomp"
    for key in ("RE1_LEARNER_HOST", "RE1_LEARNER_PORT", "FLEET_LEARNER_HOST",
                "FLEET_LEARNER_PORT", "RE1_YAWN_RAILS_ROOT"):
        os.environ.pop(key, None)

    from bridge_factory import assert_tag_not_fleet, make_recomp_bridge

    from re1_rl.env import RE1Env

    assert_tag_not_fleet(str(args.tag))
    if not CURRICULUM.is_file():
        raise SystemExit(f"ERROR: missing curriculum {CURRICULUM}")

    pred_meta: dict[str, Any] = {}
    try:
        pred_meta = json.loads((pred_dir / "meta.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        pred_meta = {}
    if not args.chunk:
        # Replay under the chunk that minted the tape: queue seek + settle
        # must match capture, otherwise the start state diverges.
        from re1_rl.planner_loyal import chunk_path_for_id

        meta_chunk = chunk_path_for_id(str(pred_meta.get("chunk_id") or ""), ROOT)
        if meta_chunk is not None:
            os.environ["RE1_PLANNER_CHUNK"] = str(meta_chunk)
            print(f"[replay] chunk from pred meta: {meta_chunk.name}", flush=True)
    pb_bundle: dict[str, Any] = {
        "source": "planner_loyal",
        "state_path": str(state_path),
        "checkpoint_id": pred_meta.get("checkpoint_id") or f"pl{max(from_slot, 0):02d}",
        "checkpoint_index": int(pred_meta.get("checkpoint_index", from_slot) or from_slot or 0),
        "room_id": pred_meta.get("room_id") or "",
        "state_sha256": pred_meta.get("state_sha256") or want_sha,
        "sidecar_sha256": pred_meta.get("sidecar_sha256"),
        "planner_step_index": pred_meta.get("planner_step_index"),
        "chunk_id": pred_meta.get("chunk_id"),
    }
    if sidecar_path is not None:
        pb_bundle["sidecar_path"] = str(sidecar_path)

    chain = f"pl{max(from_slot, 0):02d}->pl{slot_n:02d}"
    seg = tape.get("room_segment_id")
    print(
        f"[replay] tape={tape_path} chain={chain} segment={seg} "
        f"steps={len(actions)} joypad_frames={total} spans={len(spans)}",
        flush=True,
    )

    bridge = make_recomp_bridge(tag=str(args.tag), work_slot=int(args.work_slot), timeout=180.0)
    visible = bool(args.visible or args.watch)
    if visible:
        os.environ["PSX_HEADLESS"] = "0"
    bridge.launch(headless=not visible, renderer="software",
                  stdout_path=str(ROOT / "data" / "logs" / f"recomp_replay_{args.tag}.log"),
                  stderr_path=str(ROOT / "data" / "logs" / f"recomp_replay_{args.tag}.err.log"))
    try:
        contract = tape.get("contract") or {}
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            frame_skip=int(contract.get("frame_skip") or 8),
            project_root=ROOT,
            async_cutscene_skip=False,
            camera_whiten=False,
        )
        env.reset(options={"pb_bundle": pb_bundle, "allow_capture": False})
        # Never mint / freeze while replaying someone else's inputs.
        env._arm_checkpoint_freeze = lambda: None  # type: ignore[method-assign]
        maybe_capture = getattr(env, "_maybe_capture_planner_loyal_cell", None)
        if callable(maybe_capture):
            env._maybe_capture_planner_loyal_cell = lambda *a, **k: None  # type: ignore[method-assign]
        try:
            bridge.tape_enable(False)
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass
        # Combat determinism: re-arm the LCG recorded at tape capture (08c).
        # Must run after tip load/settle and before actions/joypad play.
        try:
            from re1_rl.rng_seed import apply_tape_rng_seed

            rng_status = apply_tape_rng_seed(bridge, tape)
            if rng_status.get("applied"):
                print(
                    f"[replay] rng_seed poked "
                    f"0x{int(rng_status['requested']):08X} "
                    f"(before=0x{int(rng_status.get('before') or 0):08X})",
                    flush=True,
                )
            elif str(rng_status.get("reason") or "") == "no_rng_seed_in_tape":
                print(
                    "[replay] WARN tape has no rng_seed — relying on .pst LCG only "
                    "(remint after this build for frame-perfect combat)",
                    flush=True,
                )
            else:
                print(f"[replay] WARN rng_seed poke failed: {rng_status}", flush=True)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"[replay] WARN rng_seed poke skipped: {exc}", flush=True)
        st = getattr(env, "_prev_state", {}) or {}
        print(
            f"[replay] start room={st.get('room_id')} pos=({st.get('x')},{st.get('z')}) "
            f"hp={st.get('hp')} in_control={st.get('in_control')}",
            flush=True,
        )

        writer = None
        if args.record_mp4:
            from re1_rl.obs_capture import RawVideoFfmpegWriter

            ts = time.strftime("%Y%m%d_%H%M%S")
            out = ROOT / "data" / "footage" / "planner_replay" / f"pl{slot_n:02d}_{ts}"
            out.mkdir(parents=True, exist_ok=True)
            writer = RawVideoFfmpegWriter(out, width=320, height=240, fps=30, scale=3)
            print(f"[replay] recording timelapse -> {out}", flush=True)

        import numpy as np

        last_state: dict[str, Any] = dict(st)
        label = f"pl{slot_n:02d}"
        if str(args.mode) == "actions":
            from re1_rl.env import ACTION_NAMES

            print(f"[replay] mode=actions ({len(actions)} policy steps, same path as capture)", flush=True)
            for i, a in enumerate(actions):
                _obs, _rew, term, trunc, info = env.step(int(a))
                _st = (info or {}).get("state") if isinstance(info, dict) else None
                if isinstance(_st, dict):
                    last_state = dict(_st)
                else:
                    last_state = dict(getattr(env, "_prev_state", {}) or {})
                if (i + 1) % 10 == 0 or i == 0 or i + 1 == len(actions):
                    name = ACTION_NAMES[a] if 0 <= int(a) < len(ACTION_NAMES) else str(a)
                    inv = last_state.get("inventory_slots") or last_state.get("inventory")
                    print(
                        f"[replay] {label} step {i + 1}/{len(actions)} {name} "
                        f"room={last_state.get('room_id')} hp={last_state.get('hp')} "
                        f"pos=({last_state.get('x')},{last_state.get('z')}) "
                        f"ctrl={last_state.get('in_control')} inv={inv}",
                        flush=True,
                    )
                if term or trunc:
                    print(f"[replay] {label} episode ended at step {i + 1} term={term} trunc={trunc}", flush=True)
                    break
            played = total = len(actions)
        else:
            played = 0
            since_shot = 0
            chunk = 120
            for span_bits, patch_mode in spans:
                print(f"[replay] {label} span {len(span_bits)} frames patch_mode={patch_mode} at {played}/{total}", flush=True)
                for start in range(0, len(span_bits), chunk):
                    sl = span_bits[start:start + chunk]
                    got = bridge.tape_play(sl, patch_mode=patch_mode)
                    played += int(got)
                    since_shot += int(got)
                    try:
                        last_state = dict(env._read_state(track_items=True))  # noqa: SLF001
                    except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                        last_state = dict(getattr(env, "_prev_state", {}) or {})
                    if writer is not None and since_shot >= int(args.record_every):
                        since_shot = 0
                        try:
                            rgb = np.ascontiguousarray(bridge.screenshot())
                            writer.append_rgb(rgb)
                        except (OSError, RuntimeError, ValueError):
                            pass
                    if played == total or played % 360 == 0 or start == 0:
                        print(
                            f"[replay] {label} joypad {played}/{total} "
                            f"room={last_state.get('room_id')} hp={last_state.get('hp')} "
                            f"pos=({last_state.get('x')},{last_state.get('z')})",
                            flush=True,
                        )
        if args.settle:
            from re1_rl.yawn_rails import _settle_state_for_capture

            settled = _settle_state_for_capture(env, last_state)
            if settled is not None:
                last_state = settled
        try:
            last_state = dict(env._read_state(track_items=True))  # noqa: SLF001
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass

        mp4 = None
        if writer is not None:
            try:
                mp4 = writer.close()
            except (OSError, RuntimeError, ValueError):
                mp4 = None
            print(f"[replay] timelapse mp4={mp4}", flush=True)

        end = tape.get("end") or {}
        inv = last_state.get("inventory_slots") or last_state.get("inventory")
        print("[replay] end " + json.dumps({
            "leg": label, "segment": seg, "mode": str(args.mode),
            "room": str(last_state.get("room_id", "") or ""),
            "hp": int(last_state.get("hp", 0) or 0),
            "x": int(last_state.get("x", 0) or 0),
            "z": int(last_state.get("z", 0) or 0),
            "facing": int(last_state.get("facing", 0) or 0),
            "inventory": inv,
            "want": {k: end.get(k) for k in ("room_id", "hp", "x", "z", "facing")},
            "want_inventory": end.get("inventory_slots") or end.get("inventory"),
            "joypad_frames": int(tape.get("joypad_frames") or 0), "steps_tape": len(actions),
            "mp4": str(mp4) if mp4 else None,
        }, separators=(",", ":")))

        fail: list[str] = []
        want_room = str(end.get("room_id", "") or "")
        if want_room and str(last_state.get("room_id", "") or "").upper() != want_room.upper():
            fail.append(f"room {last_state.get('room_id')!r} != {want_room!r}")
        want_hp = int(end.get("hp", 0) or 0)
        if want_hp and abs(int(last_state.get("hp", 0) or 0) - want_hp) > 12:
            fail.append(f"hp {last_state.get('hp')} != {want_hp} (±12)")
        for axis in ("x", "z"):
            g = int(last_state.get(axis, 0) or 0)
            w = int(end.get(axis, 0) or 0)
            if abs(g - w) > 256:
                fail.append(f"{axis} {g} != {w} (±256)")
        want_facing = int(end.get("facing", 0) or 0)
        if want_facing:
            from re1_rl.attack_macro import facing_signed_delta

            got_facing = int(last_state.get("facing", 0) or 0)
            if abs(facing_signed_delta(got_facing, want_facing)) > FACING_RESTORE_TOL:
                fail.append(f"facing {got_facing} != {want_facing} (±{FACING_RESTORE_TOL})")
        if fail:
            print("[replay] FAIL")
            for row in fail:
                print(f"  - {row}")
            return 1
        print("[replay] PASS")
        return 0
    finally:
        if args.watch:
            print("[replay] guest left running — close the window when done", flush=True)
        else:
            try:
                env.close()  # type: ignore[possibly-undefined]
            except (OSError, RuntimeError, NameError):
                pass
            try:
                bridge.close()
            except (OSError, RuntimeError):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
