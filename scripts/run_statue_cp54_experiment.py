"""Auto experiment: load BizHawk QuickSave at statue, hold forward, check cp54 leg.

Uses cp52 sidecar + user QuickSave in one bundle dir (same parent as cell.State).
Default QuickSave0 (tools/BizHawk PSX/State) — updated when user saves at statue.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_paths import BIZHAWK_STATE_DIR, EMUHAWK, assert_rom_present, emuhawk_argv
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env
from re1_rl.memory_map import DEFAULT_RAM_FIELDS
from re1_rl.yawn_rails import capture_successor_cell
from scripts.play_human import configure_ram_skip, human_advance, wait_for_emuhawk

PORT = 5820
CURRICULUM = ROOT / "curriculum" / "yawn_rails_one_leg.json"
CP52_SIDE = ROOT / "states" / "yawn_rails" / "cells" / "cp52" / "cell.sidecar.json"
BUNDLE_DIR = ROOT / "data" / "logs" / "statue_cp54_experiment_bundle"
# route_steps[53] == seq 54 == statue_202 (cp52 checkpoint_index + 1).
ROUTE_START = 53
LEG_SPAN = 1
LOG_PATH = ROOT / "data" / "logs" / "statue_cp54_experiment.jsonl"
DEFAULT_QS = (
    BIZHAWK_STATE_DIR
    / "Resident Evil - Director's Cut (USA).Nymashock.QuickSave0.State"
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prepare_bundle(quicksave: Path) -> Path:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    dst_state = BUNDLE_DIR / "cell.State"
    dst_side = BUNDLE_DIR / "cell.sidecar.json"
    shutil.copy2(quicksave, dst_state)
    shutil.copy2(CP52_SIDE, dst_side)
    return BUNDLE_DIR


def _log(row: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _snap(env: RE1Env, tag: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    ram = env.bridge.read_ram(list(DEFAULT_RAM_FIELDS))
    flag = int(ram.get("dining_statue_flag", 0) or 0)
    row: dict[str, Any] = {
        "ts": _utc(),
        "tag": tag,
        "room": str(env._prev_state.get("room_id", "")),
        "dining_statue_flag": flag,
        "dining_statue_knocked": bool(flag & 0x10),
        "pos": [int(ram.get("player_x", 0)), int(ram.get("player_z", 0))],
        "hp": int(ram.get("player_hp", 0) or 0),
        "objective": str(
            (env._planner.current_objective() or {}).get("checkpoint_id") or "?"
        ),
        "checkpoint_success": bool(env._progress.checkpoint_success),
        "legs_completed": int(env._progress.legs_completed),
    }
    if extra:
        row.update(extra)
    print(
        f"[exp] {tag} room={row['room']} flag={flag} knocked={row['dining_statue_knocked']} "
        f"pos={row['pos']} cp_ok={row['checkpoint_success']} obj={row['objective']}"
        + (
            f" prog={extra.get('bd_dining_statue_progress', 0):+.2f}"
            if extra and "bd_dining_statue_progress" in extra
            else ""
        ),
        flush=True,
    )
    _log(row)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="statue_202 -> cp54 checkpoint experiment")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument(
        "--quicksave",
        type=Path,
        default=DEFAULT_QS,
        help="BizHawk savestate to load (default QuickSave0)",
    )
    ap.add_argument("--max-push-chunks", type=int, default=50)
    ap.add_argument("--speed", type=int, default=200)
    args = ap.parse_args()

    qs = args.quicksave.resolve()
    if not qs.is_file():
        print(f"ERROR: quicksave missing: {qs}", file=sys.stderr)
        return 1
    if not CP52_SIDE.is_file():
        print(f"ERROR: cp52 sidecar missing: {CP52_SIDE}", file=sys.stderr)
        return 1

    bundle = _prepare_bundle(qs)
    print(f"[exp] bundle={bundle} quicksave={qs.name} mtime={qs.stat().st_mtime}", flush=True)

    port = int(args.port)
    log_path = ROOT / "data" / "logs" / f"emuhawk_statue_exp_{port}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lf = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        emuhawk_argv(port=port),
        cwd=str(EMUHAWK.parent),
        stdout=lf,
        stderr=subprocess.STDOUT,
    )

    bridge = BizHawkClient(port=port, timeout=120.0)
    bridge.start_server()
    try:
        wait_for_emuhawk(bridge, proc, port=port, timeout=120.0, log_path=log_path)
        bridge.set_speed(int(args.speed))

        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            frame_skip=4,
            project_root=ROOT,
            async_cutscene_skip=True,
            camera_whiten=False,
        )
        configure_ram_skip(
            env,
            int(args.speed),
            cutscene_speed=6400,
            turbo_patches=True,
            invisible_cutscenes=False,
            skip_chunk=600,
        )

        reset_options: dict[str, Any] = {
            "pb_bundle": {
                "state_path": str((bundle / "cell.State").resolve()),
                "sidecar_path": str((bundle / "cell.sidecar.json").resolve()),
                "source": "yawn_rails",
            },
            "route_start_index": ROUTE_START,
            "leg_span": LEG_SPAN,
        }
        env.reset(options=reset_options)
        stage = json.loads(CURRICULUM.read_text(encoding="utf-8"))
        _snap(env, "init")

        knocked_before = bool(env._prev_state.get("dining_statue_knocked"))
        if knocked_before:
            print("[exp] WARN: savestate already has dining_statue_knocked", flush=True)

        buttons = {"up": True}
        knocked_at: int | None = None
        cp_at: int | None = None
        for i in range(int(args.max_push_chunks)):
            state, reward, breakdown, _goal, info = human_advance(
                env, buttons, stage=stage
            )
            row = _snap(
                env,
                "push",
                extra={
                    "chunk": i,
                    "reward": float(reward),
                    "gs": int(state.get("game_state", 0) or 0),
                    "anim": int(state.get("player_anim", 0) or 0),
                    "pushing": bool(
                        int(state.get("game_state", 0) or 0) == 0x80800040
                        and int(state.get("player_anim", 0) or 0) == 0x10
                    ),
                    "statue_xz": [
                        int(state.get("dining_statue_x", 0) or 0),
                        int(state.get("dining_statue_z", 0) or 0),
                    ],
                    "bd_dining_statue": float(breakdown.get("dining_statue", 0) or 0),
                    "bd_dining_statue_progress": float(
                        breakdown.get("dining_statue_progress", 0) or 0
                    ),
                    "bd_checkpoint_success": float(
                        breakdown.get("checkpoint_success", 0) or 0
                    ),
                },
            )
            if row["dining_statue_knocked"] and knocked_at is None:
                knocked_at = i
                _snap(env, "statue_knocked", extra={"chunk": i})
            if env._progress.checkpoint_success and cp_at is None:
                cp_at = i
                _snap(env, "checkpoint_success", extra={"chunk": i})
                break
            terminated, truncated, episode_failure = env._termination_flags(state)
            if terminated or truncated:
                _snap(
                    env,
                    "episode_end",
                    extra={"reason": episode_failure or "truncated"},
                )
                break

        proposal = None
        if env._progress.checkpoint_success:
            proposal = capture_successor_cell(
                env,
                env._prev_state,
                {"checkpoint_success": 1.0},
            )

        cp54_exists = (ROOT / "states" / "yawn_rails" / "cells" / "cp54").is_dir()
        lines = [
            "",
            "[statue_cp54 verdict]",
            f"  quicksave: {qs}",
            f"  statue knocked @ chunk: {knocked_at if knocked_at is not None else 'NO'}",
            f"  checkpoint_success @ chunk: {cp_at if cp_at is not None else 'NO'}",
            f"  capture_proposal: {'yes' if proposal else 'no'}",
            f"  cp54 cell on disk: {cp54_exists}",
        ]
        if env._progress.checkpoint_success and knocked_at is not None:
            lines.append("  OVERALL: PASS (leg satisfied)")
        elif knocked_at is not None:
            lines.append("  OVERALL: PARTIAL (knocked but no checkpoint_success)")
        else:
            lines.append("  OVERALL: FAIL (statue not knocked)")
        print("\n".join(lines), flush=True)
        _log(
            {
                "ts": _utc(),
                "tag": "verdict",
                "knocked_at": knocked_at,
                "checkpoint_at": cp_at,
                "proposal": bool(proposal),
                "cp54_exists": cp54_exists,
            }
        )
        return 0 if env._progress.checkpoint_success and knocked_at is not None else 1
    finally:
        bridge.close()
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
