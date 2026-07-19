"""Benchmark 15 manual Beretta shots against the production attack macros."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.attack_macro import (  # noqa: E402
    execute_attack_down_macro,
    execute_attack_macro,
    execute_attack_up_macro,
    is_gun_aim_stable,
)
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import EMUHAWK, LUA, ROM, newest_quicksave  # noqa: E402
from re1_rl.inventory_menu_macro import execute_equip_macro  # noqa: E402
from re1_rl.memory_map import (  # noqa: E402
    EQUIPPED_WEAPON_ID,
    INVENTORY_BASE,
    PLAYER_ACTION_AUX,
    PLAYER_ANIM_STATE,
    PLAYER_HP,
    PLAYER_RECOVERY_TIMER,
)
from re1_rl.weapon_equip import read_inventory_ids  # noqa: E402

PORT = 7793
WEAPON_ID = 0x02
WEAPON_NAME = "beretta"
SHOT_COUNT = 15
SET_AMMO = 0
STATE = newest_quicksave()
OUT = ROOT / "data" / "live_gun_human_vs_macro.json"
EMPTY_STICKY = {
    key: False for key in ("up", "down", "left", "right", "square", "cross", "r1")
}


def _read(client: BizHawkClient, ammo_addr: int) -> dict[str, int]:
    fields = [
        ("equipped", EQUIPPED_WEAPON_ID, "u8"),
        ("ammo", ammo_addr, "u16"),
        ("hp", PLAYER_HP, "u16"),
        ("anim", PLAYER_ANIM_STATE, "u8"),
        ("aux", PLAYER_ACTION_AUX, "u8"),
        ("recovery", PLAYER_RECOVERY_TIMER, "u8"),
    ]
    row = {key: int(value) for key, value in client.read_ram(fields).items()}
    row["ammo"] >>= 8
    return row


def _set_loaded_ammo(client: BizHawkClient, ammo_addr: int) -> None:
    if SET_AMMO > 0:
        packed = (int(SET_AMMO) << 8) | int(WEAPON_ID)
        client.write_ram([("loaded_ammo", ammo_addr, "u16", packed)])


def _direction(buttons: dict[str, bool]) -> str:
    if buttons.get("up"):
        return "up"
    if buttons.get("down"):
        return "down"
    return "neutral"


def _settled(row: dict[str, int]) -> bool:
    hooks = (row["anim"], row["aux"], row["recovery"])
    return is_gun_aim_stable(*hooks) or hooks == (0, 0, 0)


def _summary(
    *,
    drop_frames: list[int],
    directions: list[str],
    start_frame: int,
    end_frame: int,
) -> dict[str, Any]:
    intervals = [
        current - previous for previous, current in zip(drop_frames, drop_frames[1:])
    ]
    frames = end_frame - start_frame + 1
    return {
        "start_frame": start_frame,
        "end_frame": end_frame,
        "frames": frames,
        "seconds_at_60fps": frames / 60.0,
        "drop_frames": drop_frames,
        "drop_intervals": intervals,
        "directions": directions,
    }


def _human_run(client: BizHawkClient, ammo_addr: int) -> dict[str, Any]:
    expected = (["up", "neutral", "down"] * SHOT_COUNT)[:SHOT_COUNT]
    previous_ammo = _read(client, ammo_addr)["ammo"]
    start_frame: int | None = None
    drop_frames: list[int] = []
    directions: list[str] = []
    last_fire_direction = "neutral"
    settled_run = 0

    print(
        f"[HUMAN_READY] Equip {WEAPON_NAME} (0x{WEAPON_ID:02X}), then fire "
        f"UP -> NEUTRAL -> DOWN until {SHOT_COUNT} shots. "
        "Hold R1 after the last shot.",
        flush=True,
    )
    while True:
        frame = client.frameadvance(1)
        buttons = client.read_joypad()
        assert isinstance(buttons, dict)
        row = _read(client, ammo_addr)
        if row["equipped"] != WEAPON_ID:
            previous_ammo = row["ammo"]
            continue

        firing = bool(buttons.get("r1") and buttons.get("cross"))
        if firing:
            last_fire_direction = _direction(buttons)
            if start_frame is None:
                start_frame = frame

        if row["ammo"] < previous_ammo:
            for _ in range(previous_ammo - row["ammo"]):
                drop_frames.append(frame)
                directions.append(last_fire_direction)
                print(
                    f"[human] shot={len(drop_frames):02d} frame={frame} "
                    f"direction={last_fire_direction} ammo={row['ammo']}",
                    flush=True,
                )
            previous_ammo = row["ammo"]

        if len(drop_frames) >= SHOT_COUNT:
            settled_run = settled_run + 1 if _settled(row) else 0
            if settled_run >= 2:
                if start_frame is None:
                    start_frame = drop_frames[0]
                result = _summary(
                    drop_frames=drop_frames[:SHOT_COUNT],
                    directions=directions[:SHOT_COUNT],
                    start_frame=start_frame,
                    end_frame=frame,
                )
                result["sequence_ok"] = directions[:SHOT_COUNT] == expected
                print(
                    f"[HUMAN_DONE] frames={result['frames']} "
                    f"seconds={result['seconds_at_60fps']:.3f} "
                    f"sequence_ok={result['sequence_ok']}",
                    flush=True,
                )
                return result


def _macro_run(client: BizHawkClient, ammo_addr: int, slot: int) -> dict[str, Any]:
    client.load_savestate(str(STATE))
    client.frameadvance(6)
    _set_loaded_ammo(client, ammo_addr)
    hp = _read(client, ammo_addr)["hp"]
    died, _frames, equip = execute_equip_macro(
        client, slot, prev_hp=hp, episode_start_hp=hp
    )
    if died or not equip.get("ok"):
        raise RuntimeError(f"{WEAPON_NAME} menu equip failed: {equip}")

    cycle: list[
        tuple[str, Callable[..., tuple[bool, int, dict[str, Any]]]]
    ] = [
        ("up", execute_attack_up_macro),
        ("neutral", execute_attack_macro),
        ("down", execute_attack_down_macro),
    ]
    sequence = (cycle * SHOT_COUNT)[:SHOT_COUNT]
    drop_frames: list[int] = []
    directions: list[str] = []
    previous_ammo = _read(client, ammo_addr)["ammo"]
    original_step = client.step

    def tracked_step(*args: Any, **kwargs: Any) -> tuple[int, bool]:
        nonlocal previous_ammo
        result = original_step(*args, **kwargs)
        ammo = _read(client, ammo_addr)["ammo"]
        if ammo < previous_ammo:
            drop_frames.extend([result[0]] * (previous_ammo - ammo))
        previous_ammo = ammo
        return result

    client.step = tracked_step  # type: ignore[method-assign]
    start_frame = int(
        client._request({"cmd": "framecount"}).get("frame", client.emulated_frame)
    ) + 1
    try:
        for shot, (direction, macro) in enumerate(sequence, 1):
            before = len(drop_frames)
            died, _frames, report = macro(
                client,
                empty_sticky=EMPTY_STICKY,
                prev_hp=hp,
                episode_start_hp=hp,
            )
            if died or report.get("outcome") != "ok" or len(drop_frames) != before + 1:
                raise RuntimeError(f"macro shot {shot} failed: {report}")
            directions.append(direction)
            print(
                f"[macro] shot={shot:02d} frame={drop_frames[-1]} "
                f"direction={direction} ammo={previous_ammo}",
                flush=True,
            )
    finally:
        client.step = original_step  # type: ignore[method-assign]

    result = _summary(
        drop_frames=drop_frames,
        directions=directions,
        start_frame=start_frame,
        end_frame=client.emulated_frame,
    )
    print(
        f"[MACRO_DONE] frames={result['frames']} "
        f"seconds={result['seconds_at_60fps']:.3f}",
        flush=True,
    )
    return result


def main() -> int:
    global OUT, SET_AMMO, SHOT_COUNT, WEAPON_ID, WEAPON_NAME
    parser = argparse.ArgumentParser()
    parser.add_argument("--macro-only", action="store_true")
    parser.add_argument("--weapon-id", type=lambda value: int(value, 0), default=0x02)
    parser.add_argument("--weapon-name", default="beretta")
    parser.add_argument("--shots", type=int, default=0, help="0 = all loaded rounds")
    parser.add_argument("--set-ammo", type=int, default=0)
    args = parser.parse_args()
    WEAPON_ID = int(args.weapon_id)
    WEAPON_NAME = str(args.weapon_name)
    SET_AMMO = max(0, int(args.set_ammo))
    OUT = ROOT / "data" / f"live_{WEAPON_NAME}_human_vs_macro.json"
    if not STATE.is_file():
        raise FileNotFoundError(STATE)
    client = BizHawkClient(port=PORT, timeout=300.0, connect_timeout=120.0)
    client.start_server()
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
            "--gdi",
        ],
        cwd=str(EMUHAWK.parent),
    )
    try:
        client.wait_for_client()
        client.set_speed(100)
        client.load_savestate(str(STATE))
        client.frameadvance(6)
        inventory_ids = read_inventory_ids(client)
        slot = inventory_ids.index(WEAPON_ID)
        ammo_addr = INVENTORY_BASE + 2 * slot
        _set_loaded_ammo(client, ammo_addr)
        if int(args.shots) > 0:
            SHOT_COUNT = int(args.shots)
        else:
            SHOT_COUNT = _read(client, ammo_addr)["ammo"]
        if args.macro_only:
            prior = json.loads(OUT.read_text(encoding="utf-8"))
            human = prior["human"]
        else:
            human = _human_run(client, ammo_addr)
        print("[COMPARE] Running the production macro sequence now.", flush=True)
        client.set_speed(40)
        started = time.perf_counter()
        macro = _macro_run(client, ammo_addr, slot)
        payload = {
            "state": str(STATE),
            "human": human,
            "macro": macro,
            "macro_wall_seconds_at_40pct": time.perf_counter() - started,
            "macro_minus_human_frames": macro["frames"] - human["frames"],
            "macro_speedup": human["frames"] / macro["frames"],
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(
            f"[RESULT] human={human['frames']}f macro={macro['frames']}f "
            f"speedup={payload['macro_speedup']:.3f}x wrote={OUT}",
            flush=True,
        )
        return 0
    finally:
        try:
            client.quit()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
