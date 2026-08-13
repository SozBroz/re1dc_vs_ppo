"""Replay a yawn cell tape: load predecessor, step recorded actions, compare end probe.

cp19 example (L hallway ammo pickup, captured from cp18):

  venv\\Scripts\\python.exe scripts\\replay_leg.py --to 19
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", type=int, default=19, help="Destination cell index (cpNN)")
    ap.add_argument("--tape", type=Path, default=None, help="Override leg_replay.json path")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--speed", type=int, default=100)
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--connect-timeout", type=float, default=120.0)
    args = ap.parse_args()

    tape, tape_path = _load_tape(int(args.to), args.tape)
    from_idx = int(tape["from_checkpoint_index"])
    to_idx = int(tape["to_checkpoint_index"])
    contract = tape.get("contract") or {}
    actions = [int(a) for a in tape.get("actions") or []]
    want_frames = [int(f) for f in tape.get("emu_frames_per_step") or []]
    end = tape.get("end") or {}
    if not actions:
        print(f"ERROR: empty actions in {tape_path}", file=sys.stderr)
        return 1

    pred = yawn_rails_root(ROOT) / "cells" / cell_dir_name(from_idx)
    state_path = pred / CELL_STATE_NAME
    sidecar_path = pred / CELL_SIDECAR_NAME
    if not state_path.is_file() or not sidecar_path.is_file():
        print(f"ERROR: missing predecessor bundle {pred}", file=sys.stderr)
        return 1
    pred_sha = _sha256(state_path)
    want_sha = str(tape.get("from_state_sha256") or "")
    if want_sha and pred_sha != want_sha:
        print(
            f"WARN: predecessor State sha {pred_sha[:12]} != tape {want_sha[:12]}",
            flush=True,
        )

    frame_skip = int(contract.get("frame_skip") or 8)
    async_skip = bool(contract.get("async_cutscene_skip", True))
    reset_options: dict[str, Any] = {
        "pb_bundle": {
            "state_path": str(state_path.resolve()),
            "sidecar_path": str(sidecar_path.resolve()),
            "source": "yawn_rails",
        },
        "route_start_index": to_idx,
        "leg_span": 1,
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

    env = RE1Env(
        curriculum_path=CURRICULUM,
        bridge=bridge,
        frame_skip=frame_skip,
        project_root=ROOT,
        async_cutscene_skip=async_skip,
        camera_whiten=False,
    )
    env.knife_echo_joypad = False
    configure_ram_skip(
        env,
        int(args.speed),
        cutscene_speed=int(args.speed),
        turbo_patches=True,
        invisible_cutscenes=False,
        skip_chunk=600,
    )

    print(
        f"[replay] tape={tape_path} cp{from_idx:02d}->{to_idx:02d} "
        f"steps={len(actions)} frames={tape.get('leg_frames')} "
        f"async_skip={async_skip} skip={frame_skip}",
        flush=True,
    )
    env.reset(options=reset_options)

    got_frames: list[int] = []
    last_state: dict[str, Any] = dict(getattr(env, "_prev_state") or {})
    term = trunc = False
    try:
        for i, action in enumerate(actions):
            _obs, _rew, term, trunc, info = env.step(int(action))
            st = info.get("state") if isinstance(info, dict) else None
            if not isinstance(st, dict):
                st = dict(getattr(env, "_prev_state") or {})
            last_state = st
            got_frames.append(int(st.get("step_emulated_frames") or 0))
            if (i + 1) % 50 == 0 or i == 0:
                name = ACTION_NAMES[action] if 0 <= action < len(ACTION_NAMES) else str(action)
                print(
                    f"[replay] {i + 1}/{len(actions)} {name} "
                    f"room={st.get('room_id')} hp={st.get('hp')} "
                    f"pos=({st.get('x')},{st.get('z')})",
                    flush=True,
                )
            if term or trunc:
                print(f"[replay] episode ended at step {i + 1} term={term} trunc={trunc}", flush=True)
                break
        if bool(tape.get("settled")):
            settled = _settle_state_for_capture(env, last_state)
            if settled is not None:
                last_state = settled
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

    fail: list[str] = []
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

    print("[replay] end", json.dumps({
        "room": got_room,
        "hp": got_hp,
        "x": int(last_state.get("x", 0) or 0),
        "z": int(last_state.get("z", 0) or 0),
        "want": {k: end.get(k) for k in ("room_id", "hp", "x", "z")},
        "steps_played": len(got_frames),
        "steps_tape": len(actions),
    }, separators=(",", ":")))
    if fail:
        print("[replay] FAIL")
        for row in fail:
            print(f"  - {row}")
        return 1
    print("[replay] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
