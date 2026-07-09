"""Batch knife swing QA: RAM anim monitors + frame-count consistency report.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_anim_consistency_qa.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_anim_consistency_qa.py --swings 25 --port 5790
"""

from __future__ import annotations

import argparse
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
DEFAULT_STATE = ROOT / "states" / "jill_control_fresh.State"


def _fmt_report(rep: dict) -> str:
    return (
        f"ok={int(rep.get('ok', False))} outcome={rep.get('outcome')} "
        f"macro_f={rep.get('macro_frames')} "
        f"swing={rep.get('swing_frames')}/{rep.get('expect_swing')} "
        f"rec={rep.get('recovery_frames')}/{rep.get('expect_recovery')} "
        f"issues={len(rep.get('issues') or [])}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Knife anim consistency QA")
    ap.add_argument("--port", type=int, default=5790)
    ap.add_argument("--speed", type=int, default=200)
    ap.add_argument("--swings", type=int, default=20)
    ap.add_argument("--noops", type=int, default=4)
    ap.add_argument(
        "--settle-noops",
        type=int,
        default=12,
        help="extra noops after each knife swing to let anim return idle",
    )
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env

    port = int(args.port)
    bridge = BizHawkClient(
        port=port,
        timeout=300.0,
        screenshot_path=str(ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    print(f"[knife_anim_qa] launching EmuHawk port={port}", flush=True)
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    env: RE1Env | None = None

    def shutdown(code: int = 0) -> None:
        try:
            if env is not None:
                env.close()
        except Exception:
            pass
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        raise SystemExit(code)

    signal.signal(signal.SIGINT, lambda *_: shutdown(130))
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: shutdown(143))

    env = RE1Env(
        curriculum_path=ROOT / "curriculum" / "m0_dining_to_main_hall.json",
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=False,
    )
    env._ram_skip.use_engine_patches = False
    env.knife_use_ram_gates = True

    bridge.wait_for_client()
    bridge.set_speed(int(args.speed))
    env.reset()

    noop = ACTION_NAMES.index("noop")
    knife = ACTION_NAMES.index("knife_swing")
    reports: list[dict] = []
    t0 = time.perf_counter()

    print(
        f"[knife_anim_qa] {args.swings} swings, {args.noops} noops between, "
        f"RAM-gated macro + [knife_anim] logs on mismatch",
        flush=True,
    )

    for swing in range(1, int(args.swings) + 1):
        for _ in range(int(args.noops)):
            env.step(noop)
        _, _, term, trunc, info = env.step(knife)
        rep = info.get("knife_anim_report") or {}
        reports.append(rep)
        line = _fmt_report(rep)
        if rep.get("issues"):
            for issue in rep["issues"]:
                print(f"  issue: {issue}", flush=True)
        print(f"swing {swing:02d}: {line}", flush=True)
        for _ in range(int(args.settle_noops)):
            env.step(noop)
        if term or trunc:
            env.reset()

    elapsed = time.perf_counter() - t0
    ok_reports = [r for r in reports if r.get("ok")]
    bad_reports = [r for r in reports if r and not r.get("ok")]
    swing_counts = [int(r["swing_frames"]) for r in ok_reports if "swing_frames" in r]
    rec_counts = [int(r["recovery_frames"]) for r in ok_reports if "recovery_frames" in r]
    macro_counts = [int(r["macro_frames"]) for r in reports if r.get("macro_frames")]

    expect_swing = reports[0].get("expect_swing") if reports else None
    expect_rec = reports[0].get("expect_recovery") if reports else None

    print("\n[knife_anim_qa] === SUMMARY ===", flush=True)
    print(
        f"  swings={len(reports)} clean={len(ok_reports)} "
        f"flagged={len(bad_reports)} elapsed={elapsed:.1f}s",
        flush=True,
    )
    if expect_swing is not None:
        print(f"  expect swing/rec emu frames: {expect_swing}/{expect_rec}", flush=True)
    if swing_counts:
        print(
            f"  swing_frames ok-only: min={min(swing_counts)} "
            f"median={statistics.median(swing_counts):.0f} "
            f"max={max(swing_counts)} stdev={statistics.pstdev(swing_counts):.2f}",
            flush=True,
        )
    if rec_counts:
        print(
            f"  recovery_frames ok-only: min={min(rec_counts)} "
            f"median={statistics.median(rec_counts):.0f} "
            f"max={max(rec_counts)} stdev={statistics.pstdev(rec_counts):.2f}",
            flush=True,
        )
    if macro_counts:
        print(
            f"  macro_frames all: min={min(macro_counts)} "
            f"median={statistics.median(macro_counts):.0f} max={max(macro_counts)}",
            flush=True,
        )

    consistent = (
        len(ok_reports) == len(reports)
        and len(reports) > 0
        and swing_counts
        and statistics.pstdev(swing_counts) == 0
        and (not rec_counts or statistics.pstdev(rec_counts) == 0)
    )
    if consistent:
        print("[knife_anim_qa] PASS — consistent swing behavior", flush=True)
        shutdown(0)
    print("[knife_anim_qa] FAIL — see flagged swings / frame spread above", flush=True)
    shutdown(1)


if __name__ == "__main__":
    raise SystemExit(main())
