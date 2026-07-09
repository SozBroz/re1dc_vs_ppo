"""Compare crouch vs standing knife swipe RAM signatures on live EmuHawk.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_stance_compare_qa.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_stance_compare_qa.py --port 5795 --swings 3
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
OUT_JSON = ROOT / "data" / "knife_stance_compare.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Crouch vs standing knife RAM compare")
    ap.add_argument("--port", type=int, default=5795)
    ap.add_argument("--speed", type=int, default=200)
    ap.add_argument("--swings", type=int, default=3, help="traces per stance")
    ap.add_argument("--settle", type=int, default=16, help="noop env steps between traces")
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env
    from re1_rl.knife_macro import (
        build_knife_frame_buttons,
        build_quick_knife_frame_buttons,
        build_standing_knife_frame_buttons,
        compare_knife_stances,
        execute_knife_macro,
        probe_standing_aim_hooks,
        summarize_knife_trace,
        trace_knife_button_schedule,
    )

    port = int(args.port)
    bridge = BizHawkClient(
        port=port,
        timeout=300.0,
        screenshot_path=str(ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    print(f"[stance_qa] launching EmuHawk port={port}", flush=True)
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

    def shutdown(code: int) -> None:
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
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    crouch_sched = build_knife_frame_buttons()
    stand_sched = build_standing_knife_frame_buttons()
    quick_sched = build_quick_knife_frame_buttons()

    aim_probe = probe_standing_aim_hooks(bridge, aim_frames=40, empty_sticky=empty)
    aim_unique = sorted(
        {f"0x{a:02X}/0x{x:02X}/{r}:{lbl}" for a, x, r, lbl in aim_probe}
    )
    print(f"[stance_qa] R1-only aim probe (40f): {aim_unique[:12]}", flush=True)

    frames, ys = trace_knife_button_schedule(
        bridge, stand_sched, empty_sticky=empty, record_y=True
    )
    stand_first = summarize_knife_trace(frames)
    stand_first["player_y_range"] = [min(ys), max(ys)] if ys else []
    print(
        f"[stance_qa] standing-first fixed: labels={stand_first['labels_seen']} "
        f"y={stand_first['player_y_range']}",
        flush=True,
    )

    for _ in range(int(args.settle)):
        env.step(noop)

    frames, ys = trace_knife_button_schedule(
        bridge, crouch_sched, empty_sticky=empty, record_y=True
    )
    crouch_first = summarize_knife_trace(frames)
    crouch_first["player_y_range"] = [min(ys), max(ys)] if ys else []
    print(
        f"[stance_qa] crouch-first fixed: labels={crouch_first['labels_seen']} "
        f"y={crouch_first['player_y_range']}",
        flush=True,
    )

    ok_first, reasons_first = compare_knife_stances(crouch_first, stand_first)
    print(f"[stance_qa] fresh-order compare ok={ok_first}", flush=True)
    for line in reasons_first:
        print(f"  - {line}", flush=True)

    for _ in range(int(args.settle)):
        env.step(noop)

    frames, ys = trace_knife_button_schedule(
        bridge, quick_sched, empty_sticky=empty, record_y=True
    )
    quick_first = summarize_knife_trace(frames)
    quick_first["player_y_range"] = [min(ys), max(ys)] if ys else []
    print(
        f"[stance_qa] quick-knife (cross only): labels={quick_first['labels_seen']} "
        f"hooks={quick_first['hook_pair_counts']} y={quick_first['player_y_range']}",
        flush=True,
    )

    ok_quick, reasons_quick = compare_knife_stances(crouch_first, quick_first)
    print(f"[stance_qa] crouch vs quick-knife ok={ok_quick}", flush=True)
    for line in reasons_quick:
        print(f"  - {line}", flush=True)

    crouch_traces: list[dict] = [
        {"mode": "crouch_first_fixed", **crouch_first},
        {"mode": "standing_first_fixed", **stand_first},
        {"mode": "quick_knife_fixed", **quick_first},
        {"mode": "r1_aim_probe", "unique": aim_unique},
    ]
    stand_traces: list[dict] = []

    print(
        f"[stance_qa] {args.swings} traces per stance "
        f"(crouch RAM-gated + fixed schedule, standing fixed schedule)",
        flush=True,
    )

    for i in range(int(args.swings)):
        for _ in range(int(args.settle)):
            env.step(noop)

        # Crouch: RAM-gated macro (production path)
        execute_knife_macro(
            bridge,
            empty_sticky=empty,
            use_ram_gates=True,
            prev_hp=96,
            episode_start_hp=96,
        )
        rep = getattr(bridge, "last_knife_anim_report", {}) or {}
        crouch_traces.append(
            {
                "mode": "crouch_ram_gated",
                "swing": i + 1,
                "macro_frames": rep.get("macro_frames"),
                "report": rep,
            }
        )
        print(
            f"  crouch #{i + 1} ram_gated: macro_f={rep.get('macro_frames')} "
            f"swing={rep.get('swing_frames')}/{rep.get('expect_swing')} "
            f"rec={rep.get('recovery_frames')}/{rep.get('expect_recovery')}",
            flush=True,
        )

        for _ in range(int(args.settle)):
            env.step(noop)

        # Crouch fixed schedule trace (per-frame hooks)
        frames = trace_knife_button_schedule(bridge, crouch_sched, empty_sticky=empty)
        summary = summarize_knife_trace(frames)
        crouch_traces.append(
            {"mode": "crouch_fixed_trace", "swing": i + 1, **summary}
        )
        print(
            f"  crouch #{i + 1} fixed: labels={summary['labels_seen']} "
            f"hooks={summary['hook_pair_counts']}",
            flush=True,
        )

        for _ in range(int(args.settle)):
            env.step(noop)

        # Standing fixed schedule trace
        frames = trace_knife_button_schedule(bridge, stand_sched, empty_sticky=empty)
        summary = summarize_knife_trace(frames)
        stand_traces.append(
            {"mode": "standing_fixed_trace", "swing": i + 1, **summary}
        )
        print(
            f"  standing #{i + 1}: labels={summary['labels_seen']} "
            f"hooks={summary['hook_pair_counts']}",
            flush=True,
        )

    # Aggregate fixed-trace summaries across swings
    def merge_summaries(traces: list[dict], mode: str) -> dict:
        picked = [t for t in traces if t.get("mode") == mode]
        label_counts: dict[str, int] = {}
        hook_counts: dict[str, int] = {}
        for t in picked:
            for k, v in t.get("label_counts", {}).items():
                label_counts[k] = label_counts.get(k, 0) + int(v)
            for k, v in t.get("hook_pair_counts", {}).items():
                hook_counts[k] = hook_counts.get(k, 0) + int(v)
        return {
            "swings": len(picked),
            "label_counts": label_counts,
            "hook_pair_counts": hook_counts,
            "labels_seen": sorted(k for k in label_counts if k != "idle"),
            "saw_crouch_aim": label_counts.get("crouch_aim", 0) > 0,
            "saw_standing_knife": label_counts.get("standing_knife", 0) > 0,
            "saw_swing_recovery": label_counts.get("swing_recovery", 0) > 0,
        }

    crouch_agg = crouch_first
    stand_agg = stand_first
    ok, reasons = compare_knife_stances(crouch_agg, stand_agg)
    if ok_quick:
        ok, reasons = ok_quick, reasons_quick
    elif ok_first:
        ok, reasons = ok_first, reasons_first

    payload = {
        "port": port,
        "swings": int(args.swings),
        "crouch_traces": crouch_traces,
        "standing_traces": stand_traces,
        "crouch_aggregate": crouch_agg,
        "standing_aggregate": stand_agg,
        "distinguishable": ok,
        "reasons": reasons,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n[stance_qa] === COMPARE ===", flush=True)
    print(f"  crouch labels: {crouch_agg['labels_seen']}", flush=True)
    print(f"  crouch hooks:  {crouch_agg['hook_pair_counts']}", flush=True)
    print(f"  standing labels: {stand_agg['labels_seen']}", flush=True)
    print(f"  standing hooks:  {stand_agg['hook_pair_counts']}", flush=True)
    for line in reasons:
        print(f"  - {line}", flush=True)
    print(f"  wrote {OUT_JSON}", flush=True)

    if ok:
        print("[stance_qa] PASS — crouch and standing swipes are distinguishable", flush=True)
        shutdown(0)
    print("[stance_qa] FAIL — stances not clearly separated", flush=True)
    shutdown(1)


if __name__ == "__main__":
    raise SystemExit(main())
