"""Benchmark 15 manual knife swings against the production attack macros."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.attack_macro import (  # noqa: E402
    execute_attack_down_macro,
    execute_attack_macro,
    execute_attack_up_macro,
)
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import EMUHAWK, LUA, ROM, newest_quicksave  # noqa: E402
from re1_rl.inventory_menu_macro import execute_equip_macro  # noqa: E402
from re1_rl.knife_macro import is_knife_slash_anim, read_knife_hooks  # noqa: E402
from re1_rl.memory_map import EQUIPPED_WEAPON_ID, PLAYER_HP  # noqa: E402
from re1_rl.weapon_equip import read_inventory_ids  # noqa: E402

PORT = 7794
KNIFE_ID = 0x01
SHOT_COUNT = 15
STATE = newest_quicksave()
OUT = ROOT / "data" / "live_knife_human_vs_macro.json"
EMPTY_STICKY = {
    key: False for key in ("up", "down", "left", "right", "square", "cross", "r1")
}


def _equipped(client: BizHawkClient) -> int:
    return int(
        client.read_ram([("equipped", EQUIPPED_WEAPON_ID, "u8")])["equipped"]
    )


def _hp(client: BizHawkClient) -> int:
    return int(client.read_ram([("hp", PLAYER_HP, "u16")])["hp"])


def _direction(buttons: dict[str, bool]) -> str:
    if buttons.get("up"):
        return "up"
    if buttons.get("down"):
        return "down"
    return "neutral"


def _summary(
    *,
    swing_frames: list[int],
    directions: list[str],
    start_frame: int,
    end_frame: int,
) -> dict[str, Any]:
    intervals = [
        current - previous
        for previous, current in zip(swing_frames, swing_frames[1:])
    ]
    frames = end_frame - start_frame + 1
    return {
        "start_frame": start_frame,
        "end_frame": end_frame,
        "frames": frames,
        "seconds_at_60fps": frames / 60.0,
        "swing_frames": swing_frames,
        "swing_intervals": intervals,
        "directions": directions,
    }


def _human_run(
    client: BizHawkClient,
    *,
    swing_count: int = SHOT_COUNT,
    capture_trace: bool = False,
) -> dict[str, Any]:
    expected = (["up", "neutral", "down"] * swing_count)[:swing_count]
    start_frame: int | None = None
    swing_frames: list[int] = []
    directions: list[str] = []
    last_attack_direction = "neutral"
    was_slash = False
    settled_run = 0
    frame_trace: list[dict[str, Any]] = []

    print(
        "[HUMAN_READY] Equip the knife, then swing "
        f"UP -> NEUTRAL -> DOWN until {swing_count} swings. "
        f"Hold R1 after swing {swing_count}.",
        flush=True,
    )
    while True:
        frame = client.frameadvance(1)
        buttons = client.read_joypad()
        assert isinstance(buttons, dict)
        hooks = read_knife_hooks(client)
        slash = is_knife_slash_anim(*hooks)
        if _equipped(client) != KNIFE_ID:
            was_slash = slash
            continue
        if capture_trace:
            frame_trace.append(
                {
                    "frame": frame,
                    "buttons": sorted(key for key, value in buttons.items() if value),
                    "hooks": list(hooks),
                    "slash": slash,
                }
            )

        attacking = bool(buttons.get("r1") and buttons.get("cross"))
        if attacking:
            last_attack_direction = _direction(buttons)
            if start_frame is None:
                start_frame = frame

        if slash and not was_slash:
            swing_frames.append(frame)
            directions.append(last_attack_direction)
            print(
                f"[human] swing={len(swing_frames):02d} frame={frame} "
                f"direction={last_attack_direction} hooks={list(hooks)}",
                flush=True,
            )
        was_slash = slash

        if len(swing_frames) >= swing_count:
            settled = hooks[2] == 0 and not slash
            settled_run = settled_run + 1 if settled else 0
            if settled_run >= 2:
                if start_frame is None:
                    start_frame = swing_frames[0]
                result = _summary(
                    swing_frames=swing_frames[:swing_count],
                    directions=directions[:swing_count],
                    start_frame=start_frame,
                    end_frame=frame,
                )
                result["sequence_ok"] = directions[:swing_count] == expected
                if capture_trace:
                    result["frame_trace"] = frame_trace
                print(
                    f"[HUMAN_DONE] frames={result['frames']} "
                    f"seconds={result['seconds_at_60fps']:.3f} "
                    f"sequence_ok={result['sequence_ok']}",
                    flush=True,
                )
                return result


def _macro_run(client: BizHawkClient, slot: int) -> dict[str, Any]:
    client.load_savestate(str(STATE))
    client.frameadvance(6)
    hp = _hp(client)
    died, _frames, equip = execute_equip_macro(
        client, slot, prev_hp=hp, episode_start_hp=hp
    )
    if died or not equip.get("ok"):
        raise RuntimeError(f"knife menu equip failed: {equip}")

    sequence: list[
        tuple[str, Callable[..., tuple[bool, int, dict[str, Any]]]]
    ] = [
        ("up", execute_attack_up_macro),
        ("neutral", execute_attack_macro),
        ("down", execute_attack_down_macro),
    ] * 5
    swing_frames: list[int] = []
    directions: list[str] = []
    was_slash = is_knife_slash_anim(*read_knife_hooks(client))
    original_step = client.step

    def tracked_step(*args: Any, **kwargs: Any) -> tuple[int, bool]:
        nonlocal was_slash
        result = original_step(*args, **kwargs)
        slash = is_knife_slash_anim(*read_knife_hooks(client))
        if slash and not was_slash:
            swing_frames.append(result[0])
        was_slash = slash
        return result

    client.step = tracked_step  # type: ignore[method-assign]
    start_frame = int(
        client._request({"cmd": "framecount"}).get("frame", client.emulated_frame)
    ) + 1
    try:
        for swing, (direction, macro) in enumerate(sequence, 1):
            before = len(swing_frames)
            died, _frames, report = macro(
                client,
                empty_sticky=EMPTY_STICKY,
                prev_hp=hp,
                episode_start_hp=hp,
            )
            if died or report.get("outcome") != "ok" or len(swing_frames) != before + 1:
                raise RuntimeError(f"macro swing {swing} failed: {report}")
            directions.append(direction)
            print(
                f"[macro] swing={swing:02d} frame={swing_frames[-1]} "
                f"direction={direction}",
                flush=True,
            )
    finally:
        client.step = original_step  # type: ignore[method-assign]

    result = _summary(
        swing_frames=swing_frames,
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--macro-only", action="store_true")
    parser.add_argument("--human-trace", action="store_true")
    args = parser.parse_args()
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
        slot = read_inventory_ids(client).index(KNIFE_ID)
        if args.human_trace:
            human = _human_run(client, swing_count=3, capture_trace=True)
            trace_out = OUT.with_name("live_knife_human_input_trace.json")
            trace_out.write_text(json.dumps(human, indent=2), encoding="utf-8")
            print(f"[TRACE_DONE] wrote={trace_out}", flush=True)
            return 0
        if args.macro_only:
            prior = json.loads(OUT.read_text(encoding="utf-8"))
            human = prior["human"]
        else:
            human = _human_run(client)
        print("[COMPARE] Running the production knife sequence now.", flush=True)
        client.set_speed(40)
        macro = _macro_run(client, slot)
        payload = {
            "state": str(STATE),
            "human": human,
            "macro": macro,
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
