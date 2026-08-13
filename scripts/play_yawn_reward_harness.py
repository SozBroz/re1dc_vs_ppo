"""Human Yawn pay-watch: shoot body segments, print training combat rewards.

Loads the attic Yawn savestate, hands you control, and scores every HP drop
the same way training does (``decode_enemy_table`` + pending-credit combat
fields + ``enemy_combat_rewards``). Yawn has one combat HP bar (slot 0);
body segments are extra collision poses. This harness shows both so you can
confirm head/mid/tail shots still chip slot 0 and pay **4×**.

Expected attic shotgun chip: ``hp_delta * 0.014 * 4``. A 45 HP hit is +2.52.
Kill (library) is +8.0. Fodder tax must stay 0.

Usage
-----
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\play_yawn_reward_harness.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\play_yawn_reward_harness.py --newest-quicksave
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\play_yawn_reward_harness.py --state path\\to.State

Controls
--------
  WASD / pad — play (focus window YAWN-PAY)
  Esc / Q    — quit (writes JSON summary)
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.play_human import (  # noqa: E402
    _import_keyboard,
    _poll_play_buttons,
)
from scripts.play_whiten_coverage_harness import _rename_emuhawk_window  # noqa: E402
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import (  # noqa: E402
    EMUHAWK,
    assert_rom_present,
    emuhawk_argv,
    newest_quicksave,
)
from re1_rl.enemy_combat import apply_combat_step_fields  # noqa: E402
from re1_rl.memory_map import (  # noqa: E402
    CAM_ID,
    ENEMY_FIELD_OFFSETS,
    ENEMY_SLOT_STRIDE,
    ENEMY_TABLE_BASE,
    ENEMY_TABLE_SLOTS,
    EQUIPPED_WEAPON_ID,
    GAME_MODE,
    GAME_STATE,
    IN_CONTROL_MASK,
    ITEM_IDS,
    MESSAGE_FLAG,
    PLAYER_HP,
    PLAYER_X,
    PLAYER_Z,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
    decode_enemy_table,
    enemy_coords_in_room_band,
    enemy_table_fields,
)
from re1_rl.ram_skip import (  # noqa: E402
    CUTSCENE_TURBO_ADDR,
    CUTSCENE_TURBO_RESTORE,
    CUTSCENE_TURBO_VALUE,
    RamSkipper,
    in_control_from_ram,
    needs_skip_from_ram,
)
from re1_rl.reward import (  # noqa: E402
    BOSS_COMBAT_REWARD_SCALE,
    ENEMY_DAMAGE_REWARD,
    ENEMY_KILL_REWARD,
    enemy_combat_rewards,
    heavy_weapon_fodder_hit_penalty,
)
from re1_rl.sticky_input import human_step_gate  # noqa: E402
from re1_rl.yawn_hp import YAWN_LOGICAL_MAX_ATTIC, yawn_logical_hp  # noqa: E402

DEFAULT_STATE = ROOT / "states" / "yawn_210_spawn_cinema.State"
WINDOW_TITLE = "YAWN-PAY"
IDLE_POLL_S = 0.02
SCAN_SLOTS = 16
HUD_S = 3.0
EPS = 1e-6

RAM_FIELDS = [
    ("stage_id", STAGE_ID, "u8"),
    ("room_id", ROOM_ID, "u8"),
    ("cam_id", CAM_ID, "u8"),
    ("player_x", PLAYER_X, "s16"),
    ("player_z", PLAYER_Z, "s16"),
    ("player_hp", PLAYER_HP, "u16"),
    ("game_mode", GAME_MODE, "u8"),
    ("game_state", GAME_STATE, "u32"),
    ("scene_flag", SCENE_FLAG, "u8"),
    ("msg_flag", MESSAGE_FLAG, "u8"),
    ("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8"),
] + enemy_table_fields()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def _room_code(ram: dict[str, int]) -> str:
    return f"{int(ram['stage_id']) + 1}{int(ram['room_id']):02X}"


def _read_extra_table(bridge: BizHawkClient) -> bytes:
    assert ENEMY_TABLE_BASE is not None
    n = SCAN_SLOTS * ENEMY_SLOT_STRIDE
    return bytes(bridge.read_block(int(ENEMY_TABLE_BASE), n))


def _raw_slots(raw: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    n = min(SCAN_SLOTS, len(raw) // ENEMY_SLOT_STRIDE)
    for i in range(n):
        off = i * ENEMY_SLOT_STRIDE
        chunk = raw[off : off + ENEMY_SLOT_STRIDE]
        hp0 = struct.unpack_from("<H", chunk, 0)[0]
        kind = chunk[int(ENEMY_FIELD_OFFSETS["type_id"][0])]
        x = struct.unpack_from("<h", chunk, int(ENEMY_FIELD_OFFSETS["x"][0]))[0]
        z = struct.unpack_from("<h", chunk, int(ENEMY_FIELD_OFFSETS["z"][0]))[0]
        act = chunk[int(ENEMY_FIELD_OFFSETS["active_byte"][0])]
        out.append(
            {
                "slot": i,
                "hp0": hp0,
                "type_id": kind,
                "x": x,
                "z": z,
                "act": act,
                "in_band": enemy_coords_in_room_band(x, z),
            }
        )
    return out


def _interesting_raw(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        s
        for s in slots
        if s["slot"] == 0
        or s["in_band"]
        or (0 < int(s["hp0"]) <= 4000)
    ]


def _raw_moves(
    prev: list[dict[str, Any]],
    curr: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_prev = {int(s["slot"]): s for s in prev}
    moves: list[dict[str, Any]] = []
    for s in curr:
        p = by_prev.get(int(s["slot"]))
        if p is None:
            continue
        hp_delta = int(p["hp0"]) - int(s["hp0"])
        xz_moved = int(p["x"]) != int(s["x"]) or int(p["z"]) != int(s["z"])
        if hp_delta == 0 and not xz_moved:
            continue
        if int(p["hp0"]) in (0, 65535) and int(s["hp0"]) in (0, 65535) and not s["in_band"]:
            continue
        moves.append(
            {
                "slot": int(s["slot"]),
                "hp0": int(s["hp0"]),
                "hp_delta": hp_delta,
                "x": int(s["x"]),
                "z": int(s["z"]),
                "type_id": int(s["type_id"]),
                "in_band": bool(s["in_band"]),
                "decoded_slot": int(s["slot"]) < ENEMY_TABLE_SLOTS,
            }
        )
    return moves


def _state_from_ram(ram: dict[str, Any]) -> dict[str, Any]:
    room = _room_code(ram)
    return {
        "room_id": room,
        "hp": int(ram["player_hp"]),
        "x": int(ram["player_x"]),
        "z": int(ram["player_z"]),
        "enemies": decode_enemy_table(ram),
        "equipped_weapon_id": int(ram.get("equipped_weapon_id", 0) or 0),
        "in_control": bool(in_control_from_ram(ram)),
    }


def _verdict(
    *,
    hp_delta: int,
    damage_pay: float,
    kill_pay: float,
    fodder: float,
    killed: bool,
) -> str:
    if hp_delta <= 0:
        return "NO_PAY"
    expected_4x = hp_delta * ENEMY_DAMAGE_REWARD * BOSS_COMBAT_REWARD_SCALE
    expected_1x = hp_delta * ENEMY_DAMAGE_REWARD
    expected_kill = ENEMY_KILL_REWARD * BOSS_COMBAT_REWARD_SCALE if killed else 0.0
    if fodder < -EPS:
        return "FAIL_FODDER"
    if abs(damage_pay) < EPS:
        return "FAIL_ZERO"
    if abs(damage_pay - expected_1x) < EPS:
        return "FAIL_1X"
    pay_ok = abs(damage_pay - expected_4x) < EPS
    kill_ok = abs(kill_pay - expected_kill) < EPS
    if pay_ok and kill_ok:
        return "PASS_4X"
    return f"FAIL_ODD pay={damage_pay:.4f} want={expected_4x:.4f}"


def _fmt_enemies(enemies: list[dict[str, Any]]) -> str:
    if not enemies:
        return "-"
    parts = []
    for e in enemies:
        raw = e.get("hp_raw")
        raw_s = f" raw={raw}" if raw is not None else ""
        parts.append(
            f"s{int(e.get('slot', -1))}:hp{int(e.get('hp', 0))}{raw_s}"
            f"@{int(e.get('x', 0))},{int(e.get('z', 0))}"
            f",t{int(e.get('type_id', 0))}"
        )
    return " ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=5826)
    ap.add_argument("--state", type=Path, default=None)
    ap.add_argument(
        "--newest-quicksave",
        action="store_true",
        help="load newest BizHawk QuickSave instead of default spawn cinema",
    )
    ap.add_argument("--speed", type=int, default=100)
    ap.add_argument("--no-skip-to-control", action="store_true")
    ap.add_argument(
        "--input",
        choices=("both", "keyboard", "gamepad"),
        default="both",
    )
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    assert_rom_present()
    if args.newest_quicksave:
        state = newest_quicksave()
    elif args.state is not None:
        state = args.state
    else:
        state = DEFAULT_STATE if DEFAULT_STATE.is_file() else newest_quicksave()
    if not state.is_file():
        print(f"missing state {state}", file=sys.stderr)
        return 2
    if _port_in_use(int(args.port)):
        print(f"[yawn-pay] REFUSE: port {args.port} in use", flush=True)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = args.out or (ROOT / "data" / f"yawn_reward_harness_{stamp}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    bridge = BizHawkClient(port=int(args.port), timeout=300.0)
    bridge.start_server()
    argv = emuhawk_argv(port=int(args.port)) + ["--gdi"]
    proc = subprocess.Popen(
        argv,
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    hits: list[dict[str, Any]] = []
    n_pass = 0
    n_fail = 0
    total_pay = 0.0
    total_hp = 0

    try:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))
        bridge.set_patches(
            [],
            {
                "addr": CUTSCENE_TURBO_ADDR,
                "on_value": CUTSCENE_TURBO_VALUE,
                "off_value": CUTSCENE_TURBO_RESTORE,
                "mode_addr": GAME_MODE,
                "mask": IN_CONTROL_MASK,
            },
        )
        print(f"[yawn-pay] load {state}", flush=True)
        bridge.load_savestate(str(state))
        bridge.frameadvance(8)

        skipper = RamSkipper(
            bridge,
            training_speed=int(args.speed),
            cutscene_speed=6400,
            skip_chunk=400,
            invisible_during_skip=False,
        )
        if not args.no_skip_to_control:
            ram = bridge.read_ram(RAM_FIELDS)
            if needs_skip_from_ram(ram) or not in_control_from_ram(ram):
                burned, _ = skipper.skip_uncontrolled(max_frames=12000)
                print(f"[yawn-pay] skip-to-control burned={burned}", flush=True)
            bridge.send_buttons({})
            bridge.frameadvance(20)

        if _rename_emuhawk_window(proc.pid, WINDOW_TITLE):
            print(f"[yawn-pay] window {WINDOW_TITLE!r}", flush=True)
        else:
            print(f"[yawn-pay] WARN: title stamp failed — port {args.port}", flush=True)

        kb = _import_keyboard() if args.input in ("keyboard", "both") else None
        use_kb = args.input in ("keyboard", "both")
        use_pad = args.input in ("gamepad", "both")
        step_armed = True
        last_hud = 0.0

        ram0 = bridge.read_ram(RAM_FIELDS)
        prev_state = _state_from_ram(ram0)
        prev_raw = _raw_slots(_read_extra_table(bridge))
        wid = int(ram0.get("equipped_weapon_id", 0) or 0)
        print(
            f"[yawn-pay] room={prev_state['room_id']} jill={prev_state['hp']} "
            f"weapon={ITEM_IDS.get(wid, wid)!r} (id=0x{wid:02X})",
            flush=True,
        )
        print(f"[yawn-pay] decoded: {_fmt_enemies(prev_state['enemies'])}", flush=True)
        print("[yawn-pay] extra slots (segment poses, not extra HP bars):", flush=True)
        for s in _interesting_raw(prev_raw):
            logi = (
                yawn_logical_hp(int(s["hp0"]))
                if 500 <= int(s["hp0"]) <= 4000
                else None
            )
            logi_s = f" logi={logi}" if logi is not None else ""
            print(
                f"  s{s['slot']}: hp0={s['hp0']}{logi_s} t=0x{s['type_id']:02X} "
                f"xz=({s['x']},{s['z']}) in_band={s['in_band']}",
                flush=True,
            )
        print(
            "\n"
            f"[yawn-pay] PLAY {WINDOW_TITLE!r}. Shoot head / mid / tail.\n"
            f"  4× chip = hp_delta * {ENEMY_DAMAGE_REWARD} * {BOSS_COMBAT_REWARD_SCALE:g}\n"
            f"  attic logical max = {YAWN_LOGICAL_MAX_ATTIC}\n"
            "  Esc/Q quit\n",
            flush=True,
        )

        with out_path.open("w", encoding="utf-8") as log_f:
            log_f.write(
                json.dumps(
                    {
                        "type": "meta",
                        "state": str(state),
                        "created_utc": stamp,
                        "boss_scale": BOSS_COMBAT_REWARD_SCALE,
                        "damage_unit": ENEMY_DAMAGE_REWARD,
                    }
                )
                + "\n"
            )
            log_f.flush()

            while True:
                if kb is not None:
                    if kb.is_pressed("esc") or kb.is_pressed("q"):
                        print("[yawn-pay] quit", flush=True)
                        break

                ram = bridge.read_ram(RAM_FIELDS)
                buttons = _poll_play_buttons(
                    kb=kb,
                    bridge=bridge,
                    use_keyboard=use_kb,
                    use_emuhawk_joypad=use_pad,
                )
                should_step, step_armed = human_step_gate(buttons, armed=step_armed)
                if should_step:
                    bridge.step(n=1, sticky=buttons, abort_on_zero_hp=False)
                else:
                    if needs_skip_from_ram(ram) or not in_control_from_ram(ram):
                        bridge.frameadvance(1)
                    else:
                        time.sleep(IDLE_POLL_S)
                        continue

                ram_now = bridge.read_ram(RAM_FIELDS)
                curr_state = _state_from_ram(ram_now)
                curr_raw = _raw_slots(_read_extra_table(bridge))
                attacking = bool(
                    buttons.get("square")
                    or buttons.get("attack")
                    or buttons.get("r1")
                )
                credit = curr_state["room_id"] == "210" or bool(
                    curr_state["in_control"] or attacking
                )
                scored = apply_combat_step_fields(
                    prev_state,
                    curr_state,
                    credit_damage=credit,
                )
                events = list(scored.get("combat_events") or [])
                raw_moves = _raw_moves(prev_raw, curr_raw)
                hp_delta = int(scored.get("enemy_damage", 0) or 0)
                killed = int(scored.get("enemy_kills", 0) or 0) > 0

                if events or hp_delta > 0:
                    damage_pay, kill_pay = enemy_combat_rewards(scored)
                    fodder = heavy_weapon_fodder_hit_penalty(scored)
                    verdict = _verdict(
                        hp_delta=hp_delta,
                        damage_pay=damage_pay,
                        kill_pay=kill_pay,
                        fodder=fodder,
                        killed=killed,
                    )
                    want = hp_delta * ENEMY_DAMAGE_REWARD * BOSS_COMBAT_REWARD_SCALE
                    ok = verdict.startswith("PASS")
                    n_pass += int(ok)
                    n_fail += int(not ok)
                    total_pay += damage_pay + kill_pay
                    total_hp += hp_delta
                    wid = int(scored.get("equipped_weapon_id", 0) or 0)
                    row = {
                        "type": "hit",
                        "t": time.time(),
                        "room": curr_state["room_id"],
                        "player_xz": [curr_state["x"], curr_state["z"]],
                        "weapon": ITEM_IDS.get(wid, wid),
                        "hp_delta": hp_delta,
                        "killed": killed,
                        "damage_pay": round(damage_pay, 6),
                        "kill_pay": round(kill_pay, 6),
                        "want_4x": round(want, 6),
                        "fodder": fodder,
                        "verdict": verdict,
                        "combat_events": events,
                        "decoded": curr_state["enemies"],
                        "raw_moves": raw_moves,
                    }
                    hits.append(row)
                    log_f.write(json.dumps(row) + "\n")
                    log_f.flush()
                    tag = "PASS" if ok else "FAIL"
                    print(
                        f"[{tag} {verdict}] hp_delta={hp_delta} "
                        f"pay={damage_pay:.4f} want_4x={want:.4f} "
                        f"kill={kill_pay:.4f} fodder={fodder:.1f} "
                        f"weapon={ITEM_IDS.get(wid, wid)} "
                        f"jill@({curr_state['x']},{curr_state['z']})",
                        flush=True,
                    )
                    for ev in events:
                        print(
                            f"  ev s{ev.get('slot')}: "
                            f"{ev.get('hp_before')}->{ev.get('hp_after')} "
                            f"(-{ev.get('damage')}) "
                            f"t={ev.get('type_id')} "
                            f"yawn={ev.get('is_yawn')} boss={ev.get('is_boss')} "
                            f"part={bool(ev.get('yawn_part'))} "
                            f"zombie={ev.get('is_zombie')} killed={ev.get('killed')}",
                            flush=True,
                        )
                    extra_hp = [m for m in raw_moves if m["hp_delta"] > 0]
                    if extra_hp:
                        print("  raw HP drops (incl. filtered slots):", flush=True)
                        for m in extra_hp:
                            print(
                                f"    s{m['slot']}: hp0 -{m['hp_delta']} -> {m['hp0']} "
                                f"xz=({m['x']},{m['z']}) decoded={m['decoded_slot']}",
                                flush=True,
                            )
                    segs = [
                        s
                        for s in _interesting_raw(curr_raw)
                        if s["in_band"] or s["slot"] == 0
                    ]
                    if segs:
                        print(
                            "  segs "
                            + " ".join(
                                f"s{s['slot']}@({s['x']},{s['z']})" for s in segs[:10]
                            ),
                            flush=True,
                        )
                    print(
                        f"  tally hits={len(hits)} pass={n_pass} fail={n_fail} "
                        f"sum_hp={total_hp} sum_pay={total_pay:.4f}",
                        flush=True,
                    )

                prev_state = curr_state
                prev_raw = curr_raw

                now = time.time()
                if now - last_hud >= HUD_S:
                    print(
                        f"[hud] room={curr_state['room_id']} "
                        f"jill@({curr_state['x']},{curr_state['z']}) "
                        f"decoded={_fmt_enemies(curr_state['enemies'])}",
                        flush=True,
                    )
                    segs = [
                        s
                        for s in _interesting_raw(curr_raw)
                        if s["in_band"] or s["slot"] == 0
                    ]
                    if segs:
                        print(
                            "  segs "
                            + " ".join(
                                f"s{s['slot']}@({s['x']},{s['z']})" for s in segs[:10]
                            ),
                            flush=True,
                        )
                    last_hud = now

        summary = {
            "type": "summary",
            "state": str(state),
            "n_hits": len(hits),
            "n_pass": n_pass,
            "n_fail": n_fail,
            "total_hp": total_hp,
            "total_pay": total_pay,
            "want_4x": total_hp * ENEMY_DAMAGE_REWARD * BOSS_COMBAT_REWARD_SCALE,
            "log": str(out_path),
            "verdicts": [h["verdict"] for h in hits],
        }
        sum_path = out_path.with_suffix(".summary.json")
        sum_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[yawn-pay] summary -> {sum_path}", flush=True)
        print(
            f"[yawn-pay] hits={len(hits)} pass={n_pass} fail={n_fail} "
            f"sum_hp={total_hp} sum_pay={total_pay:.4f} "
            f"want_4x={summary['want_4x']:.4f}",
            flush=True,
        )
        return 0 if n_fail == 0 else 1
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(0.4)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            bridge.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
