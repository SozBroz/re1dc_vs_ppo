"""Replay a yawn cell tape: load predecessor, step recorded actions, compare end probe.

cp19 example (L hallway ammo pickup, captured from cp18):

  venv\\Scripts\\python.exe scripts\\replay_leg.py --to 19
  venv\\Scripts\\python.exe scripts\\replay_leg.py --to 12 13
  venv\\Scripts\\python.exe scripts\\replay_leg.py --to 0 --watch
  venv\\Scripts\\python.exe scripts\\replay_leg.py --crystals --to 0 1 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.bizhawk_paths import EMUHAWK, assert_rom_present, emuhawk_argv
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.go_explore_merge import CELL_REPLAY_NAME, CELL_SIDECAR_NAME, CELL_STATE_NAME
from re1_rl.attack_macro import FACING_RESTORE_TOL, facing_signed_delta
from re1_rl.leg_replay import (
    chain_forgive_stale_tape_miss,
    joypad_replay_spans,
    successor_cell_state_path,
    successor_state_sha_ok,
    tape_has_joypad,
    tape_has_joypad_turbo,
    tape_is_combat,
)
from re1_rl.yawn_rails import _settle_state_for_capture
from re1_rl.yawn_rails_sync import resolve_cell_dir, yawn_rails_root
from scripts.play_human import configure_ram_skip, wait_for_emuhawk

CURRICULUM = ROOT / "curriculum" / "yawn_rails_one_leg.json"
DEFAULT_PORT = 7798
CRYSTALS_ROOT = ROOT / "backups" / "Crystals_in_time"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rails_root_from_args(args: argparse.Namespace) -> Path:
    if bool(args.crystals) and args.rails_root is not None:
        raise SystemExit("ERROR: use --crystals or --rails-root, not both")
    if args.crystals:
        return CRYSTALS_ROOT.resolve()
    if args.rails_root is not None:
        return Path(args.rails_root).resolve()
    return yawn_rails_root(ROOT)


def _cell_dir(rails_root: Path, idx: int) -> Path:
    return resolve_cell_dir(rails_root, idx)


def _load_tape(
    to_index: int, tape_path: Path | None, rails_root: Path
) -> tuple[dict[str, Any], Path]:
    if tape_path is None:
        tape_path = _cell_dir(rails_root, to_index) / CELL_REPLAY_NAME
    if not tape_path.is_file():
        raise FileNotFoundError(f"no tape: {tape_path}")
    tape = json.loads(tape_path.read_text(encoding="utf-8"))
    if not isinstance(tape, dict):
        raise ValueError(f"invalid tape JSON: {tape_path}")
    return tape, tape_path


def _launch_emuhawk(port: int, log_path: Path) -> subprocess.Popen[Any]:
    assert_rom_present()
    if not EMUHAWK.is_file():
        raise FileNotFoundError(f"EmuHawk not found at {EMUHAWK}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        emuhawk_argv(port=port),
        cwd=str(EMUHAWK.parent),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )


def _mismatch_frames(got: list[int], want: list[int]) -> int | None:
    n = min(len(got), len(want))
    for i in range(n):
        if int(got[i]) != int(want[i]):
            return i
    if len(got) != len(want):
        return n
    return None


def _play_joypad(
    env: RE1Env,
    spans: list[tuple[list[int], str]],
    *,
    label: str,
    want_end: dict[str, Any] | None = None,
    stop_at_end_pose: bool = False,
    billed_frames: int | None = None,
    tape_frames: int | None = None,
    watch_long_skips: bool = False,
) -> dict[str, Any]:
    """TAS replay: apply recorded pad bits, no env.step / capture / skip."""
    chunk = 120
    last_state: dict[str, Any] = dict(getattr(env, "_prev_state") or {})
    total = sum(len(bits) for bits, _mode in spans)
    if billed_frames is not None and tape_frames is not None and tape_frames > billed_frames:
        print(
            f"[replay] {label} drop unbilled joypad suffix "
            f"{tape_frames - billed_frames} (billed={billed_frames} tape={tape_frames})",
            flush=True,
        )
    played = 0
    hit_end_at: int | None = None
    want = want_end or {}
    want_room = str(want.get("room_id", "") or "")
    want_x = int(want.get("x", 0) or 0)
    want_z = int(want.get("z", 0) or 0)
    n = total
    for span_bits, patch_mode in spans:
        print(
            f"[replay] {label} span {len(span_bits)} frames patch_mode={patch_mode} "
            f"at {played}/{n}",
            flush=True,
        )
        if (
            watch_long_skips
            and patch_mode == "force"
            and len(span_bits) >= 480
        ):
            print(
                f"[replay] {label} play {len(span_bits)} skip frames at 1x "
                f"(pickup cinema)",
                flush=True,
            )
            for start in range(0, len(span_bits), chunk):
                sl = span_bits[start : start + chunk]
                got = env.bridge.tape_play(sl, patch_mode="off")
                played += int(got)
                try:
                    last_state = dict(env._read_state(track_items=True))
                except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                    last_state = dict(getattr(env, "_prev_state") or {})
                if played % 360 == 0 or start == 0:
                    print(
                        f"[replay] {label} cinema {played}/{n} "
                        f"room={last_state.get('room_id')} "
                        f"pos=({last_state.get('x')},{last_state.get('z')}) "
                        f"inv={sorted(_inventory_names(last_state))} "
                        f"in_control={last_state.get('in_control')}",
                        flush=True,
                    )
            if not bool(last_state.get("in_control", True)):
                last_state = _watch_cinema_1x(env, label=label)
            continue
        for start in range(0, len(span_bits), chunk):
            sl = span_bits[start : start + chunk]
            got = env.bridge.tape_play(sl, patch_mode=patch_mode)
            played += int(got)
            try:
                last_state = dict(env._read_state(track_items=True))
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                last_state = dict(getattr(env, "_prev_state") or {})
            want_hp = int(want.get("hp", 0) or 0)
            if (
                hit_end_at is None
                and want_room
                and str(last_state.get("room_id", "") or "").upper() == want_room.upper()
                and abs(int(last_state.get("x", 0) or 0) - want_x) <= 256
                and abs(int(last_state.get("z", 0) or 0) - want_z) <= 256
                and (not want_hp or abs(int(last_state.get("hp", 0) or 0) - want_hp) <= 12)
            ):
                hit_end_at = played
                print(
                    f"[replay] {label} first end-pose hit at joypad {played}/{n} "
                    f"hp={last_state.get('hp')} pos=({last_state.get('x')},"
                    f"{last_state.get('z')}) facing={last_state.get('facing')}",
                    flush=True,
                )
                if stop_at_end_pose:
                    print(
                        f"[replay] {label} stop at end pose; leftover={n - played}",
                        flush=True,
                    )
                    return last_state
            if played == n or played % 360 == 0 or start == 0:
                print(
                    f"[replay] {label} joypad {played}/{n} "
                    f"room={last_state.get('room_id')} hp={last_state.get('hp')} "
                    f"pos=({last_state.get('x')},{last_state.get('z')})",
                    flush=True,
                )
    return last_state


def _inventory_names(state: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    slots = state.get("inventory_slots") or []
    if isinstance(slots, list):
        for row in slots:
            if isinstance(row, (list, tuple)) and row and str(row[0]).strip():
                names.add(str(row[0]))
            elif isinstance(row, str) and row.strip():
                names.add(row)
    for n in state.get("inventory") or []:
        if str(n).strip():
            names.add(str(n))
    return names


def _watch_until_inventory(
    env: RE1Env,
    want: dict[str, Any],
    *,
    label: str,
    max_frames: int = 5400,
) -> dict[str, Any]:
    """1x Cross mash after the tape so pickup cinema / Yes-No can finish on-screen."""
    want_names = _inventory_names(want)
    last: dict[str, Any] = dict(getattr(env, "_prev_state") or {})
    got = _inventory_names(last)
    if want_names and want_names <= got and bool(last.get("in_control", True)):
        return last
    print(
        f"[replay] {label} watch pickup until inventory {sorted(want_names)}",
        flush=True,
    )
    played = 0
    mash = ([16] * 8) + ([0] * 8)
    while played < max_frames:
        env.bridge.tape_play(mash, patch_mode="off")
        played += len(mash)
        try:
            last = dict(env._read_state(track_items=True))
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            last = dict(getattr(env, "_prev_state") or {})
        got = _inventory_names(last)
        if want_names and want_names <= got and bool(last.get("in_control", True)):
            print(
                f"[replay] {label} inventory complete at +{played}f {sorted(got)}",
                flush=True,
            )
            return last
        if played % 480 == 0:
            print(
                f"[replay] {label} watch +{played}f room={last.get('room_id')} "
                f"inv={sorted(got)} in_control={last.get('in_control')}",
                flush=True,
            )
    print(
        f"[replay] {label} watch timeout +{played}f inv={sorted(got)}",
        flush=True,
    )
    return last


def _watch_cinema_1x(
    env: RE1Env,
    *,
    label: str,
    max_frames: int = 7200,
    min_frames: int = 180,
) -> dict[str, Any]:
    """Play a recorded skip cinema at 1x until control returns."""
    last: dict[str, Any] = dict(getattr(env, "_prev_state") or {})
    start_inv = _inventory_names(last)
    print(
        f"[replay] {label} 1x cinema mash in_control={last.get('in_control')} "
        f"inv={sorted(start_inv)}",
        flush=True,
    )
    played = 0
    mash = ([16] * 8) + ([0] * 8)
    announced: set[str] = set()
    while played < max_frames:
        env.bridge.tape_play(mash, patch_mode="off")
        played += len(mash)
        try:
            last = dict(env._read_state(track_items=True))
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            last = dict(getattr(env, "_prev_state") or {})
        names = _inventory_names(last)
        new_items = names - start_inv - announced
        if new_items:
            announced |= new_items
            print(
                f"[replay] {label} cinema got {sorted(new_items)} at +{played}f",
                flush=True,
            )
        ic = bool(last.get("in_control", True))
        if played % 480 == 0 or new_items:
            print(
                f"[replay] {label} cinema +{played}f room={last.get('room_id')} "
                f"pos=({last.get('x')},{last.get('z')}) inv={sorted(names)} "
                f"in_control={ic}",
                flush=True,
            )
        if announced and played >= 60:
            return last
    print(f"[replay] {label} cinema timeout +{played}f", flush=True)
    return last


def _play_actions(
    env: RE1Env,
    actions: list[int],
    *,
    label: str,
    want_frames: list[int] | None = None,
) -> tuple[dict[str, Any], list[int], bool, bool]:
    got_frames: list[int] = []
    last_state: dict[str, Any] = dict(getattr(env, "_prev_state") or {})
    term = trunc = False
    for i, action in enumerate(actions):
        if want_frames is not None and i < len(want_frames) and int(want_frames[i]) == 0:
            got_frames.append(0)
            continue
        _obs, _rew, term, trunc, info = env.step(int(action))
        st = info.get("state") if isinstance(info, dict) else None
        if not isinstance(st, dict):
            st = dict(getattr(env, "_prev_state") or {})
        last_state = st
        got_frames.append(int(st.get("step_emulated_frames") or 0))
        if (i + 1) % 50 == 0 or i == 0 or i + 1 == len(actions):
            name = ACTION_NAMES[action] if 0 <= action < len(ACTION_NAMES) else str(action)
            print(
                f"[replay] {label} {i + 1}/{len(actions)} {name} "
                f"room={st.get('room_id')} hp={st.get('hp')} "
                f"pos=({st.get('x')},{st.get('z')})",
                flush=True,
            )
        if term or trunc:
            print(
                f"[replay] {label} episode ended at step {i + 1} "
                f"term={term} trunc={trunc}",
                flush=True,
            )
            break
    return last_state, got_frames, term, trunc


def _end_failures(
    last_state: dict[str, Any], tape: dict[str, Any], got_frames: list[int]
) -> list[str]:
    fail: list[str] = []
    end = tape.get("end") or {}
    want_frames = [int(f) for f in tape.get("emu_frames_per_step") or []]
    got_room = str(last_state.get("room_id", "") or "")
    want_room = str(end.get("room_id", "") or "")
    if want_room and got_room.upper() != want_room.upper():
        fail.append(f"room {got_room!r} != {want_room!r}")
    got_hp = int(last_state.get("hp", 0) or 0)
    want_hp = int(end.get("hp", 0) or 0)
    if want_hp and abs(got_hp - want_hp) > 12:
        fail.append(f"hp {got_hp} != {want_hp} (±12)")
    for axis in ("x", "z"):
        g = int(last_state.get(axis, 0) or 0)
        w = int(end.get(axis, 0) or 0)
        if abs(g - w) > 256:
            fail.append(f"{axis} {g} != {w} (±256)")
    want_facing = int(end.get("facing", 0) or 0)
    if want_facing:
        got_facing = int(last_state.get("facing", 0) or 0)
        if abs(facing_signed_delta(got_facing, want_facing)) > FACING_RESTORE_TOL:
            fail.append(
                f"facing {got_facing} != {want_facing} (±{FACING_RESTORE_TOL})"
            )
    frame_i = _mismatch_frames(got_frames, want_frames)
    if frame_i is not None:
        g = got_frames[frame_i] if frame_i < len(got_frames) else None
        w = want_frames[frame_i] if frame_i < len(want_frames) else None
        fail.append(f"emu_frames first mismatch @ {frame_i}: got={g} want={w}")
    return fail


def _resync_to_successor_state(
    env: RE1Env,
    tape: dict[str, Any],
    last_state: dict[str, Any],
) -> dict[str, Any]:
    """Load the captured dest State so the next tape starts where it was recorded.

    Each tape is recorded after ``reset(load pred State)``, not from the previous
    tape-end RAM. Capture can save a different pose than ``tape['end']`` (settle
    / bg-skip after ``tape_enable(False)``). Chain replay must reload.
    """
    path = successor_cell_state_path(ROOT, tape)
    try:
        to_idx = int(tape.get("to_checkpoint_index"))
    except (TypeError, ValueError):
        to_idx = -1
    label = f"cp{to_idx:02d}"
    if path is None:
        print(f"[replay] {label} no successor State; continue from tape end", flush=True)
        return last_state
    if not successor_state_sha_ok(path, tape):
        print(
            f"[replay] {label} WARN successor State sha != tape to_state_sha256; "
            "skip resync",
            flush=True,
        )
        return last_state
    before = (
        str(last_state.get("room_id", "") or ""),
        int(last_state.get("x", 0) or 0),
        int(last_state.get("z", 0) or 0),
        int(last_state.get("facing", 0) or 0),
    )
    env.bridge.load_savestate(str(path))
    env.bridge.clear_latched_input()
    env.bridge.frameadvance(1)
    env._skip_uncontrolled()
    try:
        env._auto_accept_pause_pickup_modal()
        env._dismiss_non_box_pause_menu_if_safe()
    except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
        pass
    env._skip_uncontrolled()
    env.bridge.clear_latched_input()
    try:
        last_state = dict(env._read_state(track_items=True))
    except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
        pass
    after = (
        str(last_state.get("room_id", "") or ""),
        int(last_state.get("x", 0) or 0),
        int(last_state.get("z", 0) or 0),
        int(last_state.get("facing", 0) or 0),
    )
    drifted = before[0] != after[0] or abs(before[1] - after[1]) > 256 or abs(
        before[2] - after[2]
    ) > 256
    print(
        f"[replay] {label} resync load {path.name} sha={_sha256(path)[:12]} "
        f"tape_end={before[0]} ({before[1]},{before[2]}) "
        f"state={after[0]} ({after[1]},{after[2]})"
        f"{' (pose drift)' if drifted else ''}",
        flush=True,
    )
    return last_state


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--to",
        type=int,
        nargs="+",
        default=[19],
        help="Destination cell index(es); multiple reload each captured successor State",
    )
    ap.add_argument("--tape", type=Path, default=None, help="Override leg_replay.json path")
    ap.add_argument(
        "--crystals",
        action="store_true",
        help=f"Read cells from {CRYSTALS_ROOT} (flat cpNN dirs)",
    )
    ap.add_argument(
        "--rails-root",
        type=Path,
        default=None,
        help="Override yawn rails root (cells/cpNN or flat cpNN)",
    )
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--speed", type=int, default=100)
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--connect-timeout", type=float, default=120.0)
    ap.add_argument(
        "--mode",
        choices=("auto", "joypad", "actions"),
        default="auto",
        help="auto uses joypad_bits when every tape has them",
    )
    ap.add_argument(
        "--force-stale",
        action="store_true",
        help="play even when predecessor State SHA != tape from_state_sha256",
    )
    ap.add_argument(
        "--joypad-patch",
        choices=("auto", "step", "force", "off", "skip"),
        default="auto",
        help="auto classifies Cross-mash as force turbo; others apply one patch mode",
    )
    ap.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave EmuHawk running after the tape so you can watch the end pose.",
    )
    ap.add_argument(
        "--watch",
        action="store_true",
        help="Play the full tape at 1x with no cutscene turbo and leave EmuHawk open "
        "so pickup cinemas / inventory are visible.",
    )
    ap.add_argument(
        "--stop-at-end-pose",
        action="store_true",
        help="Stop each tape at the first pose match (skips leftover cinema/inventory).",
    )
    ap.add_argument(
        "--no-resync",
        action="store_true",
        help="Do not reload captured successor State between chain legs "
        "(single continuous TAS; fails when save pose != tape end).",
    )
    args = ap.parse_args()
    if args.watch:
        args.keep_open = True
    rails_root = _rails_root_from_args(args)
    os.environ["RE1_YAWN_RAILS_ROOT"] = str(rails_root)
    print(f"[replay] rails_root={rails_root}", flush=True)

    dests = [int(x) for x in args.to]
    if args.tape is not None and len(dests) != 1:
        print("ERROR: --tape only works with a single --to", file=sys.stderr)
        return 1

    loaded: list[tuple[dict[str, Any], Path]] = []
    for dest in dests:
        tape, tape_path = _load_tape(dest, args.tape, rails_root)
        actions = [int(a) for a in tape.get("actions") or []]
        if not actions:
            print(f"ERROR: empty actions in {tape_path}", file=sys.stderr)
            return 1
        loaded.append((tape, tape_path))

    for i in range(1, len(loaded)):
        prev_to = int(loaded[i - 1][0]["to_checkpoint_index"])
        cur_from = int(loaded[i][0]["from_checkpoint_index"])
        if prev_to != cur_from:
            print(
                f"ERROR: tapes do not chain: cp{prev_to:02d} -> "
                f"cp{cur_from:02d} (expected adjacent)",
                file=sys.stderr,
            )
            return 1

    first = loaded[0][0]
    from_idx = int(first["from_checkpoint_index"])
    contract = first.get("contract") or {}
    fresh_start = from_idx < 0
    sidecar_path: Path | None = None

    if fresh_start:
        stage = json.loads(CURRICULUM.read_text(encoding="utf-8"))
        init_rel = str(stage.get("init_savestate") or "states/jill_control_fresh.State")
        state_path = (ROOT / init_rel).resolve()
        if not state_path.is_file():
            print(f"ERROR: missing init savestate {state_path}", file=sys.stderr)
            return 1
        pred_sha = _sha256(state_path)
        want_sha = str(first.get("from_state_sha256") or "")
        if want_sha and pred_sha != want_sha:
            if args.force_stale:
                print(
                    f"WARN: init State sha {pred_sha[:12]} != tape "
                    f"{want_sha[:12]} (--force-stale)",
                    flush=True,
                )
            else:
                print(
                    f"ERROR: fresh-start tape requires exact init State "
                    f"(disk {pred_sha[:12]} != tape {want_sha[:12]}). "
                    f"Recapture from the current init, or pass --force-stale.",
                    file=sys.stderr,
                )
                return 1
        print(
            f"[replay] fresh start init={state_path.name} sha={pred_sha[:12]}",
            flush=True,
        )
    else:
        pred = _cell_dir(rails_root, from_idx)
        state_path = pred / CELL_STATE_NAME
        sidecar_path = pred / CELL_SIDECAR_NAME
        if not state_path.is_file() or not sidecar_path.is_file():
            print(f"ERROR: missing predecessor bundle {pred}", file=sys.stderr)
            return 1
        pred_sha = _sha256(state_path)
        want_sha = str(first.get("from_state_sha256") or "")
    if not fresh_start and want_sha and pred_sha != want_sha:
        dest_cell = _cell_dir(rails_root, int(first["to_checkpoint_index"]))
        dest_state = dest_cell / CELL_STATE_NAME
        dest_side = dest_cell / CELL_SIDECAR_NAME
        dest_sha = _sha256(dest_state) if dest_state.is_file() else ""
        want_to = str(first.get("to_state_sha256") or "")
        skip_stale_combat = (
            tape_is_combat(first)
            and dest_sha
            and want_to
            and dest_sha == want_to
            and len(loaded) > 1
        )
        if skip_stale_combat:
            print(
                f"[replay] skip stale combat tape cp"
                f"{int(first['from_checkpoint_index']):02d}->"
                f"{int(first['to_checkpoint_index']):02d} "
                f"(pred sha {pred_sha[:12]} != tape {want_sha[:12]}); "
                f"load captured fight State and play the next tape",
                flush=True,
            )
            loaded = loaded[1:]
            first = loaded[0][0]
            from_idx = int(first["from_checkpoint_index"])
            pred = dest_cell
            state_path = dest_state
            sidecar_path = dest_side
            pred_sha = dest_sha
            want_sha = str(first.get("from_state_sha256") or "")
            contract = first.get("contract") or {}
        elif args.force_stale:
            print(
                f"WARN: predecessor State sha {pred_sha[:12]} != tape "
                f"{want_sha[:12]} (--force-stale)",
                flush=True,
            )
        else:
            kind = "combat" if tape_is_combat(first) else "nav"
            print(
                f"ERROR: {kind} tape requires exact predecessor State "
                f"(disk {pred_sha[:12]} != tape {want_sha[:12]}). "
                f"Recapture from the current pred, or pass --force-stale.",
                file=sys.stderr,
            )
            return 1
        if want_sha and pred_sha != want_sha and not args.force_stale:
            print(
                f"ERROR: next tape also stale "
                f"(disk {pred_sha[:12]} != tape {want_sha[:12]}). "
                f"Recapture the post-fight cell, or pass --force-stale.",
                file=sys.stderr,
            )
            return 1

    frame_skip = int(contract.get("frame_skip") or 8)
    async_skip = bool(contract.get("async_cutscene_skip", True))
    reset_options: dict[str, Any] = {
        "route_start_index": int(first["to_checkpoint_index"]),
        "leg_span": len(loaded),
    }
    if sidecar_path is not None:
        reset_options["pb_bundle"] = {
            "state_path": str(state_path.resolve()),
            "sidecar_path": str(sidecar_path.resolve()),
            "source": "yawn_rails",
        }

    port = int(args.port)
    bridge = BizHawkClient(port=port, timeout=300.0)
    bridge.start_server()
    log_path = ROOT / "data" / "logs" / f"emuhawk_replay_{port}.log"
    proc = None if args.no_launch else _launch_emuhawk(port, log_path)
    wait_for_emuhawk(
        bridge,
        proc,
        port=port,
        timeout=float(args.connect_timeout),
        log_path=log_path,
    )
    bridge.set_speed(int(args.speed))

    use_joypad = args.mode == "joypad" or (
        args.mode == "auto"
        and all(tape_has_joypad(t) for t, _ in loaded)
    )
    env = RE1Env(
        curriculum_path=CURRICULUM,
        bridge=bridge,
        frame_skip=frame_skip,
        project_root=ROOT,
        async_cutscene_skip=False if use_joypad else async_skip,
        camera_whiten=False,
    )
    env.knife_echo_joypad = False
    configure_ram_skip(
        env,
        int(args.speed),
        cutscene_speed=int(args.speed),
        # Joypad tapes include fast_forward mash frames. Those spans were
        # recorded with door-skip + forced cutscene turbo; tape_play now
        # reapplies the same writes. Disabling patches here is what kept
        # cp13 stuck in the Bar (in-control scene 0x91 at 1x).
        turbo_patches=True,
        invisible_cutscenes=False,
        skip_chunk=600,
    )

    chain = " then ".join(
        f"cp{int(t['from_checkpoint_index']):02d}->{int(t['to_checkpoint_index']):02d}"
        for t, _ in loaded
    )
    print(
        f"[replay] chain={chain} tapes={len(loaded)} "
        f"async_skip={async_skip} skip={frame_skip} "
        f"watch={bool(args.watch)} keep_open={bool(args.keep_open)} "
        f"joypad_patch={args.joypad_patch}",
        flush=True,
    )
    for tape, tape_path in loaded:
        print(
            f"[replay] tape={tape_path} "
            f"cp{int(tape['from_checkpoint_index']):02d}"
            f"->{int(tape['to_checkpoint_index']):02d} "
            f"steps={len(tape.get('actions') or [])} "
            f"frames={tape.get('leg_frames')} "
            f"joypad={tape.get('joypad_frames')} "
            f"policy={tape.get('policy_leg_frames')} "
            f"skip={tape.get('skip_leg_frames')}",
            flush=True,
        )
        joy_n = int(tape.get("joypad_frames") or 0)
        billed = int(tape.get("policy_leg_frames") or 0) + int(
            tape.get("skip_leg_frames") or 0
        )
        if joy_n > billed + 8:
            print(
                f"[replay] WARN unbilled joypad extra={joy_n - billed} "
                f"(bg skip interleaved; skip_frames_per_step is not a TAS map)",
                flush=True,
            )
        if joy_n and not tape_has_joypad_turbo(tape):
            print(
                "[replay] WARN tape has no joypad_turbo; recapture after Lua "
                "reload — this pad stream cannot TAS-replay combat skip",
                flush=True,
            )
        by_channel = tape.get("reward_by_channel")
        if isinstance(by_channel, dict) and by_channel:
            parts = " ".join(
                f"{k}={by_channel[k]}"
                for k in sorted(by_channel, key=lambda k: -abs(float(by_channel[k] or 0)))
            )
            print(
                f"[replay] rewards total={tape.get('reward_total')} {parts}",
                flush=True,
            )
    # Action-mode tapes can store cinema as 0-frame rows. Burning that same
    # cinema on reset would double-skip. Joypad tapes already contain those
    # frames as pad bits, but capture itself *did* skip on reset before the
    # recorder started — replay must match that start state.
    if not use_joypad:
        _skip = env._skip_uncontrolled
        env._skip_uncontrolled = lambda *a, **k: (0, False)  # type: ignore[method-assign]
        try:
            env.reset(options=reset_options)
        finally:
            env._skip_uncontrolled = _skip
    else:
        env.reset(options=reset_options)
    st = getattr(env, "_prev_state", {}) or {}
    print(
        f"[replay] after reset skip_frames={getattr(env, '_last_skip_frames', 0)} "
        f"room={st.get('room_id')} "
        f"pos=({st.get('x')},{st.get('z')}) "
        f"in_control={st.get('in_control')} "
        f"hp={st.get('hp')} equipped=0x{int(st.get('equipped_weapon_id') or 0):02X}",
        flush=True,
    )
    # Capture freeze would hitch (and rewrite cells) at each CP success.
    env._arm_checkpoint_freeze = lambda: None  # type: ignore[method-assign]
    if use_joypad:
        print("[replay] mode=joypad (raw pad bits, no env.step)", flush=True)
        try:
            env.bridge.tape_enable(False)
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass

    last_state: dict[str, Any] = dict(getattr(env, "_prev_state") or {})
    all_fail: list[str] = []
    try:
        for tape_i, (tape, tape_path) in enumerate(loaded):
            to_idx = int(tape["to_checkpoint_index"])
            label = f"cp{to_idx:02d}"
            actions = [int(a) for a in tape.get("actions") or []]
            term = trunc = False
            got_frames: list[int] = []
            if use_joypad:
                bits = [int(b) for b in (tape.get("joypad_bits") or [])]
                if args.joypad_patch == "auto":
                    spans = joypad_replay_spans(tape)
                else:
                    spans = [(bits, str(args.joypad_patch))]
                billed = sum(len(s[0]) for s in spans)
                last_state = _play_joypad(
                    env,
                    spans,
                    label=label,
                    want_end=tape.get("end") or {},
                    stop_at_end_pose=bool(args.stop_at_end_pose),
                    billed_frames=billed,
                    tape_frames=len(bits),
                    watch_long_skips=bool(args.watch),
                )
                if args.watch:
                    if "emblem" not in _inventory_names(last_state) or not bool(
                        last_state.get("in_control", True)
                    ):
                        last_state = _watch_until_inventory(
                            env, tape.get("end") or {}, label=label
                        )
            else:
                last_state, got_frames, term, trunc = _play_actions(
                    env,
                    actions,
                    label=label,
                    want_frames=[int(f) for f in tape.get("emu_frames_per_step") or []],
                )
            if (
                not args.watch
                and (
                    bool(tape.get("settled"))
                    or not bool(last_state.get("in_control", True))
                )
            ):
                settled = _settle_state_for_capture(env, last_state)
                if settled is not None:
                    last_state = settled
            try:
                last_state = dict(env._read_state(track_items=True))
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                pass
            end = tape.get("end") or {}
            inv = last_state.get("inventory_slots") or last_state.get("inventory")
            print("[replay] end", json.dumps({
                "leg": label,
                "mode": "joypad" if use_joypad else "actions",
                "room": str(last_state.get("room_id", "") or ""),
                "hp": int(last_state.get("hp", 0) or 0),
                "x": int(last_state.get("x", 0) or 0),
                "z": int(last_state.get("z", 0) or 0),
                "inventory": inv,
                "want": {k: end.get(k) for k in ("room_id", "hp", "x", "z")},
                "want_inventory": end.get("inventory_slots") or end.get("inventory"),
                "steps_played": len(got_frames) if not use_joypad else None,
                "joypad_frames": int(tape.get("joypad_frames") or 0) if use_joypad else None,
                "steps_tape": len(actions),
            }, separators=(",", ":")))
            fails = _end_failures(last_state, tape, got_frames)
            if use_joypad:
                fails = [row for row in fails if not row.startswith("emu_frames")]
            has_next = (not args.no_resync) and tape_i + 1 < len(loaded)
            dest_path = successor_cell_state_path(ROOT, tape) if has_next else None
            if fails and chain_forgive_stale_tape_miss(
                dest_path, tape, has_next=has_next
            ):
                print(
                    f"[replay] {label} tape miss; keep captured dest State "
                    f"({'; '.join(fails)})",
                    flush=True,
                )
                fails = []
            for row in fails:
                all_fail.append(f"{label}: {row}")
            if term or trunc:
                all_fail.append(f"{label}: episode ended early")
                break
            if has_next:
                last_state = _resync_to_successor_state(env, tape, last_state)
    finally:
        if args.keep_open:
            try:
                env.bridge.close()
            except (OSError, RuntimeError, AttributeError):
                pass
            print(
                "[replay] EmuHawk left open — close the window when you are done",
                flush=True,
            )
        else:
            try:
                env.close()
            except (OSError, RuntimeError):
                pass
            if proc is not None:
                try:
                    proc.terminate()
                except OSError:
                    pass

    if all_fail:
        print("[replay] FAIL")
        for row in all_fail:
            print(f"  - {row}")
        return 1
    print("[replay] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
