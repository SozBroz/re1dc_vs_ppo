"""Live harness: pl94 tip -> push dining 2F statue (pl95 mint gate).

Loads fleet cell ``pl94`` (``dining_2f_enter`` done), seeks ``push_statue_2f``,
then human-plays through the **training** planner-loyal env while printing reward
breakdown and mint signals.

Mint rule (production, same as fleet)
-------------------------------------
``pl95`` mints when step 91 ``push_statue_2f`` completes:
  - live step is ``do_puzzle`` / ``dining_statue_knocked`` in room ``202``
  - ``dining_statue_flag`` bit ``0x10`` set (``dining_statue_knocked``)

This harness disables cell capture (``RE1_PB_CAPTURE=0``) but prints
``*** WOULD MINT pl95 ***`` when the gate would fire.

Usage
-----
  python scripts/harness_pl94_dining_statue.py
  python scripts/harness_pl94_dining_statue.py --verbose
  python scripts/harness_pl94_dining_statue.py --offline   # gate check on pl94 save only
  python scripts/harness_pl94_dining_statue.py --no-launch --port 5802

Keys: WASD move | Shift+W run | Z/E interact | R aim | F fire | Space noop
      R reload pl94 | M mirror dump | Esc/Q quit
Gamepad: focus EmuHawk window.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_PORT = 5802
DEFAULT_PIN_INDEX = 94
CURRICULUM = ROOT / "curriculum" / "planner_loyal_one_leg.json"
CHUNK = "data/planner_chunks/cp05_shield_key.json"
PL94_DIR = ROOT / "states" / "planner_loyal" / "cells" / "pl94"
PL94_STATE = PL94_DIR / "cell.State"
LOG_PATH = ROOT / "data" / "logs" / "pl94_statue_harness.jsonl"

PL95_STEP = {
    "n": 91,
    "op": "do_puzzle",
    "site_id": "dining_statue_knocked",
    "room_id": "202",
    "beat_id": "push_statue_2f",
}

REWARD_KEYS = (
    "step",
    "dining_statue_progress",
    "dining_statue",
    "planner_step_success",
    "checkpoint_success",
    "planner_divert",
    "wrong_room",
    "enemy_damage",
    "enemy_kill",
    "hp",
    "weapon_reload",
    "armor_statue_progress",
    "armor_inplace_statue_push",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _configure_env(pin_index: int, port: int) -> Path:
    os.environ.setdefault("RE1_CAMERA_WHITEN", "0")
    os.environ.setdefault("RE1_LAYERED_GEOMETRY", "0")
    os.environ["RE1_PLANNER_LOYAL"] = "1"
    os.environ.setdefault("RE1_PLANNER_CHUNK", CHUNK)
    os.environ.setdefault("RE1_PLANNER_LOYAL_CELLS_ROOT", "states/planner_loyal")
    os.environ["RE1_CELL_TIMEOUT_FLAT_12M"] = "1"
    os.environ["RE1_YAWN_LEG_REPLAY"] = "0"
    os.environ["RE1_YAWN_PAYFORWARD_RIPPLE"] = "0"
    os.environ["RE1_YAWN_EXTEND_EPISODE_ON_CELL"] = "0"
    os.environ["RE1_YAWN_RAILS_SYNC"] = "0"
    os.environ["RE1_GO_EXPLORE_CAPTURE"] = "0"
    os.environ["RE1_GO_EXPLORE_SYNC"] = "0"
    os.environ["RE1_PB_CAPTURE"] = "0"
    pin = ROOT / "data" / "logs" / f"_pl94_harness_pin_{port}.env"
    pin.parent.mkdir(parents=True, exist_ok=True)
    pin.write_text(
        f"RE1_PLANNER_RESET_PIN_INDEX={int(pin_index)}\n"
        "RE1_PLANNER_RESET_PIN_RANGE=\n"
        "RE1_PLANNER_RESET_PIN_WEIGHTS=\n"
        "RE1_PLANNER_RESET_PIN_SET=\n"
        "RE1_PLANNER_RESET_PIN_SET_WEIGHT=\n",
        encoding="utf-8",
    )
    os.environ["RE1_PLANNER_RESET_PIN_FILE"] = str(pin)
    return pin


def _poll_key() -> str | None:
    if sys.platform == "win32":
        import msvcrt

        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            return ch.lower() if ch else None
        return None
    import select

    if select.select([sys.stdin], [], [], 0.0)[0]:
        return sys.stdin.read(1).lower()
    return None


def _fmt_events(bd: dict[str, float]) -> str:
    parts: list[str] = []
    for key in REWARD_KEYS:
        val = float(bd.get(key, 0.0) or 0.0)
        if key == "step" or abs(val) < 0.004:
            continue
        parts.append(f"{key}={val:+.3f}")
    return " ".join(parts)


@dataclass
class MintTracker:
    knocked_step: int | None = None
    mint_step: int | None = None
    mint_latched: bool = False
    prev_knocked: bool = False
    events: list[str] = field(default_factory=list)

    def note(
        self,
        *,
        step: int,
        knocked: bool,
        gate: bool,
        step_success: bool,
        pay: float,
        beat: str | None,
        completed_index: int | None,
    ) -> str | None:
        banner = None
        if knocked and not self.prev_knocked and self.knocked_step is None:
            self.knocked_step = step
            self.events.append(f"step={step} statue_knocked_rising_edge")
            if not gate:
                banner = (
                    f"*** FLAG 0x10 SET but gate_complete=False @ step {step} "
                    f"(check queue index / RAM read) ***"
                )
                self.events.append(banner)
        if (
            knocked
            and gate
            and not step_success
            and pay <= 0.0
            and beat == "push_statue_2f"
            and not self.mint_latched
        ):
            miss = (
                f"*** KNOCK DETECTED but NO step_success @ step {step} "
                f"(was divert bug — re-run with fixed planner_loyal) ***"
            )
            self.events.append(miss)
            banner = miss
        if (
            step_success
            and gate
            and pay > 0.0
            and beat == "push_statue_2f"
            and not self.mint_latched
        ):
            self.mint_step = step
            self.mint_latched = True
            slot = (int(completed_index) + 6) if completed_index is not None else 95
            banner = (
                f"*** WOULD MINT pl{slot:02d} @ step {step} "
                f"(completed_index={completed_index} beat={beat} pay={pay:+.3f}) ***"
            )
            self.events.append(banner)
        self.prev_knocked = knocked
        return banner


def _seek_push_statue(queue: Any) -> int:
    idx = next(
        i
        for i, s in enumerate(queue._steps)
        if s.get("beat_id") == "push_statue_2f"
    )
    queue.seek(idx)
    return idx


def _dining_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    from re1_rl.dining_statue_puzzle import (
        dining_statue_knocked_from_state,
        dining_statue_nav_target,
        dining_statue_pushing,
        _live_statue_xz,
    )
    from re1_rl.planner_loyal import _dining_statue_step_complete

    knocked = dining_statue_knocked_from_state(state)
    live = _live_statue_xz(state)
    nav = dining_statue_nav_target(state) if state else (0.0, 0.0)
    dist = None
    if live is not None:
        dist = math.hypot(live[0] - nav[0], live[1] - nav[1])
    flag = int(state.get("dining_statue_flag", state.get("dining_statue_flag_raw", 0)) or 0)
    return {
        "room": state.get("room_id"),
        "knocked": knocked,
        "flag": flag,
        "flag_hex": hex(flag),
        "pushing": dining_statue_pushing(state),
        "statue_xz": live,
        "nav_xz": (round(nav[0], 1), round(nav[1], 1)),
        "nav_dist": None if dist is None else round(dist, 1),
        "gate_complete": _dining_statue_step_complete(PL95_STEP, state),
        "jill": (
            int(state.get("x") or 0),
            int(state.get("z") or 0),
            int(state.get("facing") or 0),
        ),
        "in_control": bool(state.get("in_control")),
    }


def _read_offline_state(path: Path) -> dict[str, Any]:
    from re1_rl.memory_map import (
        DINING_STATUE_FLAG,
        DINING_STATUE_X,
        DINING_STATUE_Z,
        PLAYER_X,
        PLAYER_Z,
        ROOM_ID,
        ps1_to_mainram_offset,
    )
    from re1_rl.yawn_cell_quality import find_mainram_base, load_core

    core = load_core(path)
    base = find_mainram_base(core, expect_room="202") or find_mainram_base(core)
    if base is None:
        raise RuntimeError(f"no MainRAM in {path}")

    def _s16(addr: int) -> int:
        return struct.unpack_from(
            "<h", core, base + ps1_to_mainram_offset(addr)
        )[0]

    def _u8(addr: int) -> int:
        return core[base + ps1_to_mainram_offset(addr)]

    room_u8 = _u8(ROOM_ID)
    return {
        "room_id": "202" if find_mainram_base(core, expect_room="202") else f"{room_u8:03d}",
        "x": _s16(PLAYER_X),
        "z": _s16(PLAYER_Z),
        "facing": 0,
        "in_control": True,
        "dining_statue_flag": _u8(DINING_STATUE_FLAG),
        "dining_statue_x": _s16(DINING_STATUE_X),
        "dining_statue_z": _s16(DINING_STATUE_Z),
    }


def offline_gate_check() -> int:
    from re1_rl.planner_loyal import PlannerLoyalQueue, _dining_statue_step_complete

    if not PL94_STATE.is_file():
        print(f"ERROR: missing {PL94_STATE}", file=sys.stderr)
        return 1
    state = _read_offline_state(PL94_STATE)
    snap = _dining_snapshot(state)
    queue = PlannerLoyalQueue()
    idx = _seek_push_statue(queue)
    gate = _dining_statue_step_complete(PL95_STEP, state)
    print("[offline] pl94 cell.State dining statue gate")
    print(f"  path={PL94_STATE}")
    print(f"  room={snap['room']} knocked={snap['knocked']} flag={snap['flag_hex']}")
    print(f"  statue_xz={snap['statue_xz']} nav={snap['nav_xz']} dist={snap['nav_dist']}")
    print(f"  push_statue queue_index={idx} beat={queue.current.get('beat_id')}")
    print(f"  gate_complete={gate}  -> would_mint_pl95={gate and snap['knocked']}")
    if snap["knocked"]:
        print("  WARNING: pl94 tip already has statue knocked — not a clean pl94 tip")
    else:
        print("  OK: pl94 tip statue still up (expected)")
    return 0


def _log_json(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _print_step_panel(
    *,
    step_idx: int,
    action: int,
    reward: float,
    ep_reward: float,
    bd: dict[str, float],
    snap: dict[str, Any],
    beat: str | None,
    queue_index: int,
    loyal: dict[str, Any],
    banner: str | None,
    verbose: bool,
) -> None:
    events = _fmt_events(bd)
    interesting = (
        verbose
        or banner
        or events
        or snap["pushing"]
        or snap["knocked"]
        or loyal.get("divert")
        or loyal.get("step_success")
    )
    if not interesting:
        return
    from re1_rl.env import ACTION_NAMES

    act = ACTION_NAMES[int(action)] if 0 <= int(action) < len(ACTION_NAMES) else str(action)
    lines = [
        f"--- s{step_idx:04d} {act:<12} r={reward:+.3f} ep={ep_reward:+.2f} ---",
        (
            f"  beat={beat} q_idx={queue_index} room={snap['room']} "
            f"knocked={snap['knocked']} flag={snap['flag_hex']} push={snap['pushing']}"
        ),
        (
            f"  statue={snap['statue_xz']} nav={snap['nav_xz']} "
            f"dist={snap['nav_dist']} jill={snap['jill']}"
        ),
        (
            f"  gate={snap['gate_complete']} loyal_step_success={loyal.get('step_success')} "
            f"divert={loyal.get('divert')} reason={loyal.get('divert_reason')!r}"
        ),
    ]
    if events:
        lines.append(f"  rewards: {events}")
    if banner:
        lines.append(f"  {banner}")
    print("\n".join(lines), flush=True)


def live_harness(args: argparse.Namespace) -> int:
    import numpy as np

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES, RE1Env
    from re1_rl.demo_record import buttons_to_action
    from re1_rl.planner_loyal import _dining_statue_step_complete
    from scripts.play_human import (
        _import_keyboard,
        _kill_stale_listener,
        _poll_play_buttons,
        _read_emuhawk_joypad,
        configure_ram_skip,
        launch_emuhawk,
        wait_for_emuhawk,
    )

    port = int(args.port)
    pin = _configure_env(int(args.pin_index), port)
    if not PL94_STATE.is_file():
        print(f"ERROR: missing {PL94_STATE}", file=sys.stderr)
        return 1

    use_keyboard = args.input in ("keyboard", "both")
    use_gamepad = args.input in ("gamepad", "both")
    _kill_stale_listener(port)
    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    bridge = BizHawkClient(port=port, timeout=120.0)
    bridge.start_server()
    emu_log = ROOT / "data" / "logs" / f"emuhawk_pl94_harness_{port}.log"
    proc = None if args.no_launch else launch_emuhawk(port, emu_log)
    wait_for_emuhawk(
        bridge, proc, port=port, timeout=float(args.connect_timeout), log_path=emu_log
    )
    bridge.set_speed(int(args.speed))

    env = RE1Env(
        curriculum_path=CURRICULUM,
        bridge=bridge,
        project_root=ROOT,
        frame_skip=max(1, int(args.frame_skip)),
        async_cutscene_skip=True,
        camera_whiten=False,
    )
    env.knife_echo_joypad = False
    configure_ram_skip(
        env,
        int(args.speed),
        cutscene_speed=int(args.cutscene_speed),
        turbo_patches=True,
        invisible_cutscenes=False,
        skip_chunk=int(args.skip_chunk),
    )
    env._ram_skip.install_engine_patches()

    kb = _import_keyboard() if use_keyboard else None
    mint = MintTracker()
    log_path = args.log.resolve()

    def _reload() -> dict[str, Any]:
        env.bridge.load_savestate(str(PL94_STATE))
        env.bridge.step(n=1, sticky=env._sticky_input.as_dict())
        q = env._planner_loyal_queue
        if q is None:
            raise RuntimeError("planner loyal queue missing")
        idx = _seek_push_statue(q)
        state = env._read_state(track_items=False)
        snap = _dining_snapshot(state)
        print(
            f"[reload] pl94 -> push_statue_2f q_idx={idx} beat={q.current.get('beat_id')} "
            f"room={snap['room']} knocked={snap['knocked']} flag={snap['flag_hex']}",
            flush=True,
        )
        _log_json(
            log_path,
            {"ts": _utc_iso(), "tag": "reload", "queue_index": idx, **snap},
        )
        return snap

    try:
        env.reset()
        init_snap = _reload()
        if init_snap["knocked"]:
            print(
                "WARNING: pl94 cell already has dining_statue_knocked — "
                "mint gate may fire immediately on false pre-state",
                flush=True,
            )
        if init_snap["room"] != "202":
            print(
                f"WARNING: pl94 cell room={init_snap['room']!r} expected '202'",
                flush=True,
            )

        print(
            f"\n[pl94 harness] pin={pin.name} port={port} speed={args.speed}% "
            f"log={log_path}\n"
            "  GOAL: knock dining 2F statue -> WOULD MINT pl95\n"
            "  Console: R reload | M dump | Esc/Q quit\n"
            "  Play in EmuHawk (keyboard and/or gamepad)\n",
            flush=True,
        )
        if use_gamepad:
            sample = _read_emuhawk_joypad(bridge, debug=True)
            pressed = [k for k, v in sample.items() if v]
            if pressed:
                print(f"[pl94] joypad sample: {pressed}", flush=True)

        step_idx = 0
        ep_reward = 0.0
        last_action = -1
        q = env._planner_loyal_queue

        while True:
            key = _poll_key()
            if key in ("q",) or (kb is not None and (kb.is_pressed("esc") or kb.is_pressed("q"))):
                print("[pl94] quit", flush=True)
                break
            if key == "r":
                _reload()
                mint = MintTracker()
                ep_reward = 0.0
                step_idx = 0
                continue
            if key == "m":
                snap = _dining_snapshot(env._read_state(track_items=False))
                print(json.dumps(snap, indent=2), flush=True)

            mask = np.asarray(env.action_masks(), dtype=bool)
            decision = int(mask.sum()) > 1
            if not decision:
                time.sleep(0.02)
                continue

            buttons = _poll_play_buttons(
                kb=kb,
                bridge=bridge,
                use_keyboard=use_keyboard,
                use_emuhawk_joypad=use_gamepad,
            )
            force_noop = bool(buttons.pop("circle", False))
            if kb is not None and kb.is_pressed("space"):
                force_noop = True
            if force_noop:
                action = 0
            else:
                action = buttons_to_action(
                    buttons, env._sticky_input.as_dict(), button_map=ACTION_BUTTON_MAP
                )
            if not mask[action]:
                action = 0

            prev_state = dict(env._prev_state or {})
            prev_q_idx = int(q.index) if q is not None else -1
            beat_before = (q.current or {}).get("beat_id") if q is not None else None

            obs, reward, terminated, truncated, info = env.step(action)
            step_idx += 1
            ep_reward += float(reward)
            bd = dict(info.get("reward_breakdown") or {})
            state = env._read_state(track_items=False)
            snap = _dining_snapshot(state)

            loyal: dict[str, Any] = {}
            if q is not None:
                loyal = q.evaluate_transition(
                    prev_state=prev_state,
                    state=state,
                    box_opened=bool(state.get("box_open")),
                    box_closed=False,
                    typewriter_save_complete=bool(state.get("typewriter_save_complete")),
                    progress=env._progress,
                )
            pay = float(bd.get("planner_step_success", 0.0) or 0.0)
            completed = prev_q_idx if loyal.get("step_success") else None
            banner = mint.note(
                step=step_idx,
                knocked=bool(snap["knocked"]),
                gate=_dining_statue_step_complete(PL95_STEP, state),
                step_success=bool(loyal.get("step_success") or pay > 0.0),
                pay=pay,
                beat=str(beat_before or ""),
                completed_index=completed,
            )

            _print_step_panel(
                step_idx=step_idx,
                action=action,
                reward=float(reward),
                ep_reward=ep_reward,
                bd=bd,
                snap=snap,
                beat=beat_before,
                queue_index=int(q.index) if q is not None else -1,
                loyal=loyal,
                banner=banner,
                verbose=bool(args.verbose),
            )
            _log_json(
                log_path,
                {
                    "ts": _utc_iso(),
                    "tag": "step",
                    "step": step_idx,
                    "action": ACTION_NAMES[action],
                    "reward": float(reward),
                    "ep_reward": ep_reward,
                    "beat_before": beat_before,
                    "queue_index": int(q.index) if q is not None else None,
                    **snap,
                    "loyal_step_success": loyal.get("step_success"),
                    "loyal_divert": loyal.get("divert"),
                    "reward_breakdown": {k: bd[k] for k in REWARD_KEYS if k in bd},
                    "would_mint": banner is not None,
                },
            )

            if banner:
                print(
                    "\n[pl94] Statue step paid. Episode keeps running (mid-chunk). "
                    "Esc/Q to exit or R to reload pl94.\n",
                    flush=True,
                )

            last_action = action
            step_ok = pay > 0.0 or bool(loyal.get("step_success"))
            if step_ok and mint.mint_latched:
                # Do not auto-exit; user may want to watch post-mint state.
                pass
            if terminated or truncated:
                reason = info.get("episode_failure") or "terminated"
                print(
                    f"[pl94] episode end reason={reason} steps={step_idx} "
                    f"return={ep_reward:+.2f} mint_events={mint.events}",
                    flush=True,
                )
                break

        print(
            f"\n[pl94 summary] knocked_step={mint.knocked_step} "
            f"mint_step={mint.mint_step} events={mint.events}\n",
            flush=True,
        )
        return 0 if mint.mint_latched or mint.knocked_step else 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        bridge.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="pl94 dining statue harness (pl95 mint gate)")
    ap.add_argument("--offline", action="store_true", help="offline gate check on pl94 save only")
    ap.add_argument("--pin-index", type=int, default=DEFAULT_PIN_INDEX)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--speed", type=int, default=100)
    ap.add_argument("--cutscene-speed", type=int, default=6400)
    ap.add_argument("--skip-chunk", type=int, default=600)
    ap.add_argument("--frame-skip", type=int, default=8)
    ap.add_argument("--connect-timeout", type=float, default=120.0)
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="print every step, not just signal steps")
    ap.add_argument("--input", choices=("both", "keyboard", "gamepad"), default="both")
    ap.add_argument("--log", type=Path, default=LOG_PATH)
    args = ap.parse_args()
    if args.offline:
        return offline_gate_check()
    return live_harness(args)


if __name__ == "__main__":
    raise SystemExit(main())
