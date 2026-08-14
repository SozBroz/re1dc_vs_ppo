"""Replay a yawn cell tape: load predecessor, step recorded actions, compare end probe.

cp19 example (L hallway ammo pickup, captured from cp18):

  venv\\Scripts\\python.exe scripts\\replay_leg.py --to 19
  venv\\Scripts\\python.exe scripts\\replay_leg.py --to 12 13
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
from re1_rl.yawn_rails import _settle_state_for_capture
from re1_rl.yawn_rails_sync import cell_dir_name, yawn_rails_root
from scripts.play_human import configure_ram_skip, wait_for_emuhawk

CURRICULUM = ROOT / "curriculum" / "yawn_rails_one_leg.json"
DEFAULT_PORT = 7798


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_tape(to_index: int, tape_path: Path | None) -> tuple[dict[str, Any], Path]:
    if tape_path is None:
        tape_path = (
            yawn_rails_root(ROOT) / "cells" / cell_dir_name(to_index) / CELL_REPLAY_NAME
        )
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
    env: RE1Env, bits: list[int], *, label: str
) -> dict[str, Any]:
    """TAS replay: apply recorded pad bits, no env.step / capture / skip."""
    chunk = 120
    last_state: dict[str, Any] = dict(getattr(env, "_prev_state") or {})
    n = len(bits)
    played = 0
    for start in range(0, n, chunk):
        sl = bits[start : start + chunk]
        got = env.bridge.tape_play(sl)
        played += int(got)
        try:
            last_state = dict(env._read_state(track_items=False))
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            last_state = dict(getattr(env, "_prev_state") or {})
        done = min(start + chunk, n)
        if done == n or start == 0 or done % 360 == 0:
            print(
                f"[replay] {label} joypad {done}/{n} played={played} "
                f"room={last_state.get('room_id')} hp={last_state.get('hp')} "
                f"pos=({last_state.get('x')},{last_state.get('z')})",
                flush=True,
            )
    return last_state


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
    frame_i = _mismatch_frames(got_frames, want_frames)
    if frame_i is not None:
        g = got_frames[frame_i] if frame_i < len(got_frames) else None
        w = want_frames[frame_i] if frame_i < len(want_frames) else None
        fail.append(f"emu_frames first mismatch @ {frame_i}: got={g} want={w}")
    return fail


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--to",
        type=int,
        nargs="+",
        default=[19],
        help="Destination cell index(es); multiple plays back-to-back with no reload",
    )
    ap.add_argument("--tape", type=Path, default=None, help="Override leg_replay.json path")
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
    args = ap.parse_args()

    dests = [int(x) for x in args.to]
    if args.tape is not None and len(dests) != 1:
        print("ERROR: --tape only works with a single --to", file=sys.stderr)
        return 1

    loaded: list[tuple[dict[str, Any], Path]] = []
    for dest in dests:
        tape, tape_path = _load_tape(dest, args.tape)
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

    pred = yawn_rails_root(ROOT) / "cells" / cell_dir_name(from_idx)
    state_path = pred / CELL_STATE_NAME
    sidecar_path = pred / CELL_SIDECAR_NAME
    if not state_path.is_file() or not sidecar_path.is_file():
        print(f"ERROR: missing predecessor bundle {pred}", file=sys.stderr)
        return 1
    pred_sha = _sha256(state_path)
    want_sha = str(first.get("from_state_sha256") or "")
    if want_sha and pred_sha != want_sha:
        print(
            f"WARN: predecessor State sha {pred_sha[:12]} != tape {want_sha[:12]}",
            flush=True,
        )
        dest_cell = yawn_rails_root(ROOT) / "cells" / cell_dir_name(
            int(first["to_checkpoint_index"])
        )
        dest_state = dest_cell / CELL_STATE_NAME
        dest_side = dest_cell / CELL_SIDECAR_NAME
        dest_sha = _sha256(dest_state) if dest_state.is_file() else ""
        want_to = str(first.get("to_state_sha256") or "")
        if dest_sha and want_to and dest_sha == want_to and len(loaded) > 1:
            print(
                f"[replay] cp{int(first['to_checkpoint_index']):02d} matches "
                f"tape end — start there and skip the stale first tape",
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

    frame_skip = int(contract.get("frame_skip") or 8)
    async_skip = bool(contract.get("async_cutscene_skip", True))
    reset_options: dict[str, Any] = {
        "pb_bundle": {
            "state_path": str(state_path.resolve()),
            "sidecar_path": str(sidecar_path.resolve()),
            "source": "yawn_rails",
        },
        "route_start_index": int(first["to_checkpoint_index"]),
        "leg_span": len(loaded),
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
        and all(bool((t.get("joypad_bits") or [])) for t, _ in loaded)
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
        f"async_skip={async_skip} skip={frame_skip}",
        flush=True,
    )
    for tape, tape_path in loaded:
        print(
            f"[replay] tape={tape_path} "
            f"cp{int(tape['from_checkpoint_index']):02d}"
            f"->{int(tape['to_checkpoint_index']):02d} "
            f"steps={len(tape.get('actions') or [])} "
            f"frames={tape.get('leg_frames')}",
            flush=True,
        )
    # Reset's _skip_uncontrolled would burn the same cinema the tape
    # recorded as 0-frame steps and desync playback.
    _skip = env._skip_uncontrolled
    env._skip_uncontrolled = lambda *a, **k: (0, False)  # type: ignore[method-assign]
    try:
        env.reset(options=reset_options)
    finally:
        env._skip_uncontrolled = _skip
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
        for tape, tape_path in loaded:
            to_idx = int(tape["to_checkpoint_index"])
            label = f"cp{to_idx:02d}"
            actions = [int(a) for a in tape.get("actions") or []]
            term = trunc = False
            got_frames: list[int] = []
            if use_joypad:
                bits = [int(b) for b in (tape.get("joypad_bits") or [])]
                last_state = _play_joypad(env, bits, label=label)
            else:
                last_state, got_frames, term, trunc = _play_actions(
                    env,
                    actions,
                    label=label,
                    want_frames=[int(f) for f in tape.get("emu_frames_per_step") or []],
                )
            if bool(tape.get("settled")):
                settled = _settle_state_for_capture(env, last_state)
                if settled is not None:
                    last_state = settled
            end = tape.get("end") or {}
            print("[replay] end", json.dumps({
                "leg": label,
                "mode": "joypad" if use_joypad else "actions",
                "room": str(last_state.get("room_id", "") or ""),
                "hp": int(last_state.get("hp", 0) or 0),
                "x": int(last_state.get("x", 0) or 0),
                "z": int(last_state.get("z", 0) or 0),
                "want": {k: end.get(k) for k in ("room_id", "hp", "x", "z")},
                "steps_played": len(got_frames) if not use_joypad else None,
                "joypad_frames": int(tape.get("joypad_frames") or 0) if use_joypad else None,
                "steps_tape": len(actions),
            }, separators=(",", ":")))
            fails = _end_failures(last_state, tape, got_frames)
            if use_joypad:
                fails = [row for row in fails if not row.startswith("emu_frames")]
            for row in fails:
                all_fail.append(f"{label}: {row}")
            if term or trunc:
                all_fail.append(f"{label}: episode ended early")
                break
    finally:
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
