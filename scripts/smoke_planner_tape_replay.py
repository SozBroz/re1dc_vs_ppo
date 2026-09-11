#!/usr/bin/env python3
"""Offline replay-compat smoke for planner-loyal tapes (no emulator).

Builds a synthetic fat ``plNN`` cell via ``re1_rl.planner_leg_replay`` and
runs the exact replay consumers against it:

- ``tape_has_joypad`` / ``joypad_replay_spans`` (replay_leg joypad mode)
- ``policy_leg_frames_from_tape`` (quality/speed channel split)
- ``load_policy_for_cell`` (lockstep odds, real npz + synthetic fallback)

Usage:
  venv\\Scripts\\python.exe -u scripts/smoke_planner_tape_replay.py
  venv\\Scripts\\python.exe -u scripts/smoke_planner_tape_replay.py --packed-frames 5 --policy-steps 4
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fake_env(project_root: Path, *, packed_frames: int, policy_steps: int):
    import numpy as np

    from re1_rl.footage_trace import FootageTraceBuffer
    from re1_rl.leg_replay import JOYPAD_PACKED_ENCODING, new_leg_replay_buffer

    buf = new_leg_replay_buffer()
    for i in range(policy_steps):
        buf.append(2 + (i % 3), policy_frames=8)
        buf.append_reward(0.1, {"step": 0.1})

    class _Bridge:
        def tape_dump_packed(self):
            return {
                "packed": "A" * (3 * packed_frames),
                "encoding": JOYPAD_PACKED_ENCODING,
                "n": packed_frames,
            }

    trace = FootageTraceBuffer()
    for i in range(policy_steps):
        trace.append(
            action=2,
            action_mask=np.ones(45, dtype=bool),
            masked_probs=np.full(45, 1.0 / 45, dtype=np.float32),
        )
    return types.SimpleNamespace(
        _planner_loyal_queue=object(),
        _leg_replay=buf,
        _footage_trace=trace,
        bridge=_Bridge(),
        action_space=types.SimpleNamespace(n=45),
        frame_skip=8,
        _async_cutscene_skip=False,
        project_root=project_root,
        _progress=None,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--packed-frames", type=int, default=5)
    ap.add_argument("--policy-steps", type=int, default=4)
    ap.add_argument("--slot", type=int, default=24)
    args = ap.parse_args()

    import os

    os.environ["RE1_PLANNER_LEG_REPLAY"] = "1"

    from re1_rl.leg_replay import (
        joypad_replay_spans,
        policy_leg_frames_from_tape,
        tape_has_joypad,
    )
    from re1_rl.obs_capture import load_policy_for_cell
    from re1_rl.planner_leg_replay import (
        maybe_write_planner_capture_tape,
        maybe_write_planner_footage_trace,
    )

    with tempfile.TemporaryDirectory(prefix="pl_tape_smoke_") as tmp:
        dest = Path(tmp) / f"pl{args.slot:02d}"
        dest.mkdir()
        env = _fake_env(
            Path(tmp),
            packed_frames=args.packed_frames,
            policy_steps=args.policy_steps,
        )
        tape_path = maybe_write_planner_capture_tape(
            env,
            dest,
            completed_index=2,
            checkpoint_id="smoke",
            slot=args.slot,
            from_slot=args.slot - 1,
            live_state={"room_id": "109"},
            quality=[0] * 8,
            to_state_sha256="smoke",
            segment_id="l_hallway_bullets",
        )
        policy_path = maybe_write_planner_footage_trace(env, dest)
        assert tape_path is not None and tape_path.is_file(), "tape write failed"
        assert policy_path is not None and policy_path.is_file(), "policy write failed"

        tape = json.loads(tape_path.read_text(encoding="utf-8"))
        assert tape_has_joypad(tape), "replay would not take joypad mode"
        spans = joypad_replay_spans(tape)
        billed = sum(len(frames) for frames, _ in spans)
        assert billed == args.packed_frames, f"span frames {billed} != {args.packed_frames}"
        assert policy_leg_frames_from_tape(tape) == 8 * args.policy_steps
        policy = load_policy_for_cell(dest, tape)
        assert policy["synthetic"] is False
        assert len(policy["action"]) == args.policy_steps
        # Synthetic fallback (no npz) must also work.
        policy_path.unlink()
        fallback = load_policy_for_cell(dest, tape)
        assert fallback["synthetic"] is True
        assert len(fallback["action"]) == args.policy_steps

    print(
        f"[smoke] planner tape replay-compat OK "
        f"packed_frames={args.packed_frames} policy_steps={args.policy_steps}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
