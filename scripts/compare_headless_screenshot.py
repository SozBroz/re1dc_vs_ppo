"""Audit headless vs windowed: full policy obs + multi-step action sync.

Launches two sequential RE1Env sessions on a free probe port (does not touch
the training fleet). Both load the same curriculum savestate, then run the
same scripted actions:

  hold back -> knife_swing -> turn (same direction)

At reset and every RL step, compares everything the policy is fed:
  frame, proprio, goal, spatial, visited, rooms_visited, box, inventory,
  history, acquisitions, room_enemies, keys_held, affordances,
  cutscene_ledger, milestones, maps_files
plus action_masks, reward, terminated/truncated, and a few RAM fields.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\compare_headless_screenshot.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\compare_headless_screenshot.py --port 7789
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.env import ACTION_NAMES, RE1Env  # noqa: E402
from re1_rl.memory_map import PLAYER_HP, PLAYER_X, PLAYER_Z, ROOM_ID  # noqa: E402

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
DEFAULT_CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"

RAM_FIELDS: list[tuple[str, int, str]] = [
    ("room", ROOM_ID, "u8"),
    ("hp", PLAYER_HP, "u16"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]

# Default script: hold back, swing knife, turn right (see --script).


def _kill_proc(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _parse_script(spec: str) -> list[tuple[str, int]]:
    """Parse 'back:10,knife_swing:1,turn_right:10' into [(name, n), ...]."""
    out: list[tuple[str, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"bad script token {part!r}; want name:count")
        name, count_s = part.split(":", 1)
        name = name.strip()
        if name not in ACTION_NAMES:
            raise ValueError(f"unknown action {name!r}; known={ACTION_NAMES[:12]}...")
        out.append((name, int(count_s)))
    return out


def _expand_actions(script: list[tuple[str, int]]) -> list[int]:
    actions: list[int] = []
    for name, n in script:
        idx = ACTION_NAMES.index(name)
        actions.extend([idx] * int(n))
    return actions


def _arr_diff(a: np.ndarray, b: np.ndarray, name: str) -> dict[str, Any]:
    if a.shape != b.shape:
        return {
            "name": name,
            "identical": False,
            "error": f"shape {a.shape} vs {b.shape}",
        }
    if a.dtype != b.dtype:
        # Compare numerically anyway.
        pass
    if np.issubdtype(a.dtype, np.floating) or np.issubdtype(b.dtype, np.floating):
        af = a.astype(np.float64)
        bf = b.astype(np.float64)
        absdiff = np.abs(af - bf)
    else:
        absdiff = np.abs(a.astype(np.int64) - b.astype(np.int64)).astype(np.float64)
    n = int(absdiff.size)
    n_diff = int(np.count_nonzero(absdiff))
    return {
        "name": name,
        "identical": n_diff == 0,
        "max_abs": float(absdiff.max()) if n else 0.0,
        "mean_abs": float(absdiff.mean()) if n else 0.0,
        "pct_differ": 100.0 * n_diff / n if n else 0.0,
        "n_differ": n_diff,
        "dtype": str(a.dtype),
        "shape": list(a.shape),
    }


def _snapshot(
    env: RE1Env,
    *,
    step_i: int,
    action_name: str | None,
    action: int | None,
    obs: dict[str, np.ndarray],
    reward: float | None,
    terminated: bool,
    truncated: bool,
) -> dict[str, Any]:
    ram = env.bridge.read_ram(RAM_FIELDS)
    frame_resp = env.bridge._request({"cmd": "framecount"})
    masks = env.action_masks()
    return {
        "step": step_i,
        "action": action,
        "action_name": action_name,
        "reward": None if reward is None else float(reward),
        "terminated": bool(terminated),
        "truncated": bool(truncated),
        "emu_frame": int(frame_resp.get("frame", -1)),
        "ram": {name: int(ram[name]) for name, _, _ in RAM_FIELDS},
        "action_masks": np.asarray(masks, dtype=bool).copy(),
        "obs": {k: np.asarray(v).copy() for k, v in obs.items()},
    }


def _run_session(
    *,
    port: int,
    curriculum: Path,
    headless: bool,
    actions: list[int],
    speed: int,
) -> list[dict[str, Any]]:
    label = "headless" if headless else "windowed"
    shot_path = str(ROOT / "data" / f"_cmp_{label}_{port}.png")
    bridge = BizHawkClient(
        port=port,
        timeout=180.0,
        connect_timeout=180.0,
        screenshot_path=shot_path,
        screenshot_mmf=True,
    )
    bridge.start_server()

    cmd = [
        str(EMUHAWK),
        str(ROM),
        f"--lua={LUA}",
        "--socket_ip=127.0.0.1",
        f"--socket_port={port}",
    ]
    if headless:
        cmd.extend(["--gdi", "--chromeless"])

    print(f"[{label}] launching EmuHawk port={port}", flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env: RE1Env | None = None
    try:
        bridge.wait_for_client()
        bridge.set_speed(speed)
        env = RE1Env(
            curriculum_path=curriculum,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        env._ram_skip.training_speed = speed
        env._ram_skip.cutscene_speed = speed
        env._ram_skip.invisible_during_skip = headless
        env.knife_echo_joypad = False

        obs, _info = env.reset()
        snaps: list[dict[str, Any]] = [
            _snapshot(
                env,
                step_i=0,
                action_name=None,
                action=None,
                obs=obs,
                reward=None,
                terminated=False,
                truncated=False,
            )
        ]
        print(
            f"[{label}] reset emu_frame={snaps[0]['emu_frame']} ram={snaps[0]['ram']}",
            flush=True,
        )

        for i, act in enumerate(actions, start=1):
            name = ACTION_NAMES[act]
            obs, reward, terminated, truncated, _info = env.step(act)
            snap = _snapshot(
                env,
                step_i=i,
                action_name=name,
                action=act,
                obs=obs,
                reward=float(reward),
                terminated=bool(terminated),
                truncated=bool(truncated),
            )
            snaps.append(snap)
            print(
                f"[{label}] step={i}/{len(actions)} act={name} "
                f"rew={reward:.4f} emu_frame={snap['emu_frame']} "
                f"ram={snap['ram']} done={terminated or truncated}",
                flush=True,
            )
            if terminated or truncated:
                break
        return snaps
    finally:
        if env is not None:
            try:
                env.close()
            except Exception:
                pass
        try:
            bridge._request({"cmd": "quit"})
        except Exception:
            pass
        bridge.close()
        _kill_proc(proc)
        time.sleep(1.5)


def _compare_snaps(
    h: list[dict[str, Any]],
    w: list[dict[str, Any]],
) -> dict[str, Any]:
    n = min(len(h), len(w))
    steps: list[dict[str, Any]] = []
    all_ok = len(h) == len(w)
    if not all_ok:
        # still compare overlapping prefix
        pass

    obs_keys = sorted(h[0]["obs"].keys()) if h else []

    for i in range(n):
        hs, ws = h[i], w[i]
        key_diffs: list[dict[str, Any]] = []
        step_ok = True

        if hs["action"] != ws["action"] or hs["action_name"] != ws["action_name"]:
            step_ok = False
            key_diffs.append(
                {
                    "name": "action",
                    "identical": False,
                    "headless": hs["action_name"],
                    "windowed": ws["action_name"],
                }
            )

        for meta in ("emu_frame", "reward", "terminated", "truncated"):
            hv, wv = hs[meta], ws[meta]
            if meta == "reward":
                same = (hv is None and wv is None) or (
                    hv is not None
                    and wv is not None
                    and abs(float(hv) - float(wv)) < 1e-9
                )
            else:
                same = hv == wv
            if not same:
                step_ok = False
                key_diffs.append(
                    {
                        "name": meta,
                        "identical": False,
                        "headless": hv,
                        "windowed": wv,
                    }
                )

        if hs["ram"] != ws["ram"]:
            step_ok = False
            key_diffs.append(
                {
                    "name": "ram",
                    "identical": False,
                    "headless": hs["ram"],
                    "windowed": ws["ram"],
                }
            )

        mask_diff = _arr_diff(hs["action_masks"], ws["action_masks"], "action_masks")
        if not mask_diff["identical"]:
            step_ok = False
            key_diffs.append(mask_diff)

        obs_diffs: list[dict[str, Any]] = []
        for k in obs_keys:
            d = _arr_diff(hs["obs"][k], ws["obs"][k], k)
            if not d["identical"]:
                step_ok = False
                obs_diffs.append(d)

        all_ok = all_ok and step_ok
        steps.append(
            {
                "step": i,
                "action_name": hs["action_name"],
                "ok": step_ok,
                "meta_diffs": key_diffs,
                "obs_diffs": obs_diffs,
                "emu_frame": {"headless": hs["emu_frame"], "windowed": ws["emu_frame"]},
                "ram": {"headless": hs["ram"], "windowed": ws["ram"]},
            }
        )

    return {
        "n_headless": len(h),
        "n_windowed": len(w),
        "length_match": len(h) == len(w),
        "all_ok": all_ok and len(h) == len(w),
        "steps": steps,
        "first_fail_step": next((s["step"] for s in steps if not s["ok"]), None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Full-obs headless vs windowed sync audit")
    ap.add_argument("--port", type=int, default=7789)
    ap.add_argument("--curriculum", type=Path, default=DEFAULT_CURRICULUM)
    ap.add_argument("--speed", type=int, default=400)
    ap.add_argument(
        "--script",
        type=str,
        default="back:10,knife_swing:1,turn_right:10",
        help="comma list of action:count (names from ACTION_NAMES)",
    )
    ap.add_argument(
        "--order",
        choices=("headless-first", "windowed-first"),
        default="headless-first",
    )
    args = ap.parse_args()

    if not EMUHAWK.is_file():
        print(f"missing EmuHawk: {EMUHAWK}", file=sys.stderr)
        return 2
    if not args.curriculum.is_file():
        print(f"missing curriculum: {args.curriculum}", file=sys.stderr)
        return 2

    script = _parse_script(args.script)
    actions = _expand_actions(script)
    print(f"script={script}  n_actions={len(actions)}", flush=True)

    first_headless = args.order == "headless-first"
    a = _run_session(
        port=args.port,
        curriculum=args.curriculum,
        headless=first_headless,
        actions=actions,
        speed=args.speed,
    )
    b = _run_session(
        port=args.port,
        curriculum=args.curriculum,
        headless=not first_headless,
        actions=actions,
        speed=args.speed,
    )
    if first_headless:
        h_snaps, w_snaps = a, b
    else:
        w_snaps, h_snaps = a, b

    cmp = _compare_snaps(h_snaps, w_snaps)
    verdict = "PASS" if cmp["all_ok"] else "FAIL"

    # Compact per-step summary for stdout / JSON (drop huge arrays).
    compact_steps = []
    for s in cmp["steps"]:
        compact_steps.append(
            {
                "step": s["step"],
                "action_name": s["action_name"],
                "ok": s["ok"],
                "emu_frame": s["emu_frame"],
                "ram": s["ram"],
                "meta_diffs": s["meta_diffs"],
                "obs_diffs": [
                    {
                        "name": d["name"],
                        "max_abs": d.get("max_abs"),
                        "pct_differ": d.get("pct_differ"),
                        "n_differ": d.get("n_differ"),
                        "error": d.get("error"),
                    }
                    for d in s["obs_diffs"]
                ],
            }
        )

    report = {
        "curriculum": str(args.curriculum),
        "script": [{"action": n, "count": c} for n, c in script],
        "speed": args.speed,
        "verdict": verdict,
        "length_match": cmp["length_match"],
        "n_headless": cmp["n_headless"],
        "n_windowed": cmp["n_windowed"],
        "first_fail_step": cmp["first_fail_step"],
        "steps": compact_steps,
        "obs_keys_checked": sorted(h_snaps[0]["obs"].keys()) if h_snaps else [],
    }
    out_json = ROOT / "data" / f"_cmp_full_obs_sync_{args.port}.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print()
    print("=== full-obs headless vs windowed ===")
    print(f"steps headless={cmp['n_headless']} windowed={cmp['n_windowed']} "
          f"length_match={cmp['length_match']}")
    for s in compact_steps:
        status = "OK" if s["ok"] else "DIFF"
        extra = ""
        if not s["ok"]:
            names = [d["name"] for d in s["meta_diffs"]] + [
                d["name"] for d in s["obs_diffs"]
            ]
            extra = f"  diffs={names}"
        print(
            f"  step {s['step']:02d} act={s['action_name'] or 'reset':12s} "
            f"{status}  emu={s['emu_frame']['headless']}/{s['emu_frame']['windowed']}"
            f"{extra}"
        )
    print(f"VERDICT: {verdict}")
    if cmp["first_fail_step"] is not None:
        print(f"first_fail_step={cmp['first_fail_step']}")
    print(f"wrote {out_json}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
