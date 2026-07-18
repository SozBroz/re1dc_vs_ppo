"""Load a savestate in BizHawk and log legal action mask every N seconds.

Example:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\monitor_action_mask.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\monitor_action_mask.py --savestate tools/BizHawk-2.11.1/PSX/State/...QuickSave1.State
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import SELECT_SLOT_BASE, USE_ACTION
from re1_rl.memory_map import ITEM_IDS
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.story_item_use import legal_story_use_slots, matching_story_sites

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"


def newest_quicksave() -> Path | None:
    states = sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in states:
        if not p.name.endswith(".bak"):
            return p
    return None


def _sync_env_state(env: RE1Env) -> dict:
    state = env._read_state()
    env._prev_state = state
    env._prev_hp = int(state.get("hp", 0))
    if int(state.get("hp", 0)) > 0:
        env._episode_start_hp = int(state.get("hp", 0))
    return state


def _format_mask(mask: np.ndarray) -> str:
    legal = [ACTION_NAMES[i] for i in range(len(mask)) if bool(mask[i])]
    return ", ".join(legal) if legal else "(none)"


_NAME_TO_ID = {name: iid for iid, name in ITEM_IDS.items()}


def _inventory_ids(state: dict) -> list[tuple[int, int]]:
    inv = state.get("inventory_slots") or []
    out: list[tuple[int, int]] = []
    for row in inv:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            a, b = row[0], row[1]
            if isinstance(a, str):
                iid = _NAME_TO_ID.get(a, 0)
            else:
                iid = int(a) & 0xFF
            out.append((iid, int(b)))
    while len(out) < 8:
        out.append((0, 0))
    return out


def _story_summary(state: dict, *, rewarded: set[str]) -> str:
    inv_ids = _inventory_ids(state)
    sites = matching_story_sites(
        room=str(state.get("room_id", "")),
        x=state.get("x"),
        z=state.get("z"),
        inventory=inv_ids,
        rewarded_site_ids=rewarded,
    )
    slots = legal_story_use_slots(
        inv_ids,
        room=str(state.get("room_id", "")),
        x=state.get("x"),
        z=state.get("z"),
        rewarded_site_ids=rewarded,
    )
    if not sites:
        return "story_sites=none"
    ids = [str(s["id"]) for s in sites]
    return f"story_sites={ids} story_slots={slots}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5820)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument(
        "--savestate",
        type=str,
        default="",
        help="Path to .State (default: newest QuickSave in BizHawk State dir)",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Seconds to run (0 = until Ctrl+C)",
    )
    args = ap.parse_args()

    save_path = Path(args.savestate) if args.savestate else newest_quicksave()
    if save_path is None or not save_path.is_file():
        print(f"savestate not found: {save_path}", file=sys.stderr)
        return 1
    if not save_path.is_absolute():
        save_path = (ROOT / save_path).resolve()

    bridge = BizHawkClient(port=int(args.port), timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    print(f"[mask-monitor] savestate={save_path.name} port={args.port}", flush=True)
    print(f"[mask-monitor] logging every {args.interval}s (Ctrl+C to stop)", flush=True)

    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={args.port}",
        ],
        cwd=str(EMU.parent),
    )
    t_end = time.monotonic() + float(args.duration) if args.duration > 0 else None
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        env.reset()
        bridge.load_savestate(str(save_path))
        bridge.frameadvance(4)
        state = _sync_env_state(env)
        print(
            f"[mask-monitor] loaded room={state.get('room_id')} "
            f"pos=({state.get('x')},{state.get('z')}) "
            f"hp={state.get('hp')} inv={state.get('inventory')}",
            flush=True,
        )

        tick = 0
        while True:
            state = _sync_env_state(env)
            mask = env.action_masks()
            legal_n = int(mask.sum())
            use_ph = int(getattr(env, "_use_phase", 0))
            rewarded = set(env._progress.rewarded_story_uses)
            story = _story_summary(state, rewarded=rewarded)
            ts = datetime.now().strftime("%H:%M:%S")
            print(
                f"[{ts}] tick={tick} room={state.get('room_id')} "
                f"pos=({state.get('x')},{state.get('z')}) "
                f"facing={state.get('facing')} in_control={state.get('in_control')} "
                f"use_phase={use_ph} use_legal={bool(mask[USE_ACTION])} "
                f"legal_n={legal_n} {story}",
                flush=True,
            )
            print(f"  actions: {_format_mask(mask)}", flush=True)
            if use_ph == 1:
                slot_legal = [
                    i
                    for i in range(8)
                    if bool(mask[SELECT_SLOT_BASE + i])
                ]
                print(f"  use_phase=1 select_slots={slot_legal}", flush=True)
            print(flush=True)

            tick += 1
            if t_end is not None and time.monotonic() >= t_end:
                break
            time.sleep(float(args.interval))
        return 0
    except KeyboardInterrupt:
        print("\n[mask-monitor] stopped", flush=True)
        return 0
    finally:
        try:
            bridge.quit()
        except (OSError, ConnectionError, RuntimeError):
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
