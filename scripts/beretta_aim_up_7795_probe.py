"""Live per-frame Beretta aim-up probe on port 7795.

Loads the real QuickSave1 inventory/equip state. Never writes game RAM.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.attack_macro import execute_attack_macro, is_gun_aim_stable
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.bizhawk_paths import BIZHAWK_STATE_DIR, EMUHAWK, emuhawk_argv
from re1_rl.item_box import read_inventory
from re1_rl.knife_macro import read_knife_hooks
from re1_rl.memory_map import (
    EQUIPPED_SLOT_INDEX,
    EQUIPPED_SLOT_INDEX_1BASED,
    EQUIPPED_WEAPON_ID,
    GAME_MODE,
    GAME_STATE,
    IN_CONTROL_MASK,
    PLAYER_FACING,
    PLAYER_X,
    PLAYER_Z,
)

PORT = 7795
BERETTA_ID = 0x02
STATE = BIZHAWK_STATE_DIR / (
    "Resident Evil - Director's Cut (USA).Nymashock.QuickSave1.State"
)
OUT = ROOT / "data" / "beretta_aim_up_7795_evidence.json"
FRAME_DIR = ROOT / "data" / "beretta_aim_up_7795_frames"


def ammo(client: BizHawkClient) -> int:
    return sum(qty for item_id, qty in read_inventory(client) if item_id == BERETTA_ID)


def state(client: BizHawkClient) -> dict[str, int]:
    fields = [
        ("equipped", EQUIPPED_WEAPON_ID, "u8"),
        ("slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8"),
        ("slot_0based", EQUIPPED_SLOT_INDEX, "u8"),
        ("game_mode", GAME_MODE, "u8"),
        ("game_state", GAME_STATE, "u32"),
        ("x", PLAYER_X, "s16"),
        ("z", PLAYER_Z, "s16"),
        ("facing", PLAYER_FACING, "u16"),
    ]
    return {k: int(v) for k, v in client.read_ram(fields).items()}


def save_frame(client: BizHawkClient, name: str) -> str:
    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    path = FRAME_DIR / f"{name}.png"
    rgb = client.screenshot(allow_file_fallback=True)
    cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return str(path.relative_to(ROOT))


def run_aim_up(client: BizHawkClient) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    captures: dict[str, str] = {}
    initial_ammo = ammo(client)
    saw_fire = False

    def step(phase: str, buttons: dict[str, bool]) -> dict[str, Any]:
        nonlocal saw_fire
        client.step(buttons=buttons, n=1, echo_joypad=True)
        anim, aux, recovery = read_knife_hooks(client)
        row = {
            "frame": len(rows) + 1,
            "emu_frame": client.emulated_frame,
            "phase": phase,
            "buttons_commanded": sorted(k for k, v in buttons.items() if v),
            "buttons_echo": list(client.last_step_echo or []),
            "anim": int(anim),
            "aux": int(aux),
            "recovery": int(recovery),
            "ammo": ammo(client),
            **state(client),
        }
        saw_fire = saw_fire or (anim == 0x14 and aux == 0x03)
        rows.append(row)
        return row

    captures["initial"] = save_frame(client, "aim_up_initial")

    idle_run = 0
    for _ in range(20):
        row = step("settle_neutral", {})
        idle_run = idle_run + 1 if (row["anim"], row["aux"], row["recovery"]) == (0, 0, 0) else 0
        if idle_run >= 2:
            break
    if idle_run < 2:
        raise RuntimeError("neutral settle did not reach two idle frames")

    aim_start = len(rows) + 1
    stable_run = 0
    for _ in range(120):
        row = step("aim_r1_up", {"r1": True, "up": True})
        stable = is_gun_aim_stable(row["anim"], row["aux"], row["recovery"])
        stable_run = stable_run + 1 if stable else 0
        if stable_run >= 2:
            break
    if stable_run < 2:
        raise RuntimeError("R1+Up never reached stable gun aim")
    aim_stable = len(rows)
    captures["aim_up_stable"] = save_frame(client, f"f{aim_stable:03d}_aim_up_stable")

    fire_start = len(rows) + 1
    ammo_drop_frame: int | None = None
    for _ in range(30):
        row = step("fire_r1_up_cross", {"r1": True, "up": True, "cross": True})
        if row["frame"] == fire_start:
            captures["cross_press"] = save_frame(client, f"f{row['frame']:03d}_cross_press")
        if row["ammo"] == initial_ammo - 1:
            ammo_drop_frame = row["frame"]
            captures["ammo_drop"] = save_frame(client, f"f{row['frame']:03d}_ammo_drop")
            break
        if row["ammo"] < initial_ammo - 1:
            raise RuntimeError("more than one round consumed before Cross release")
    if ammo_drop_frame is None:
        raise RuntimeError("R1+Up+Cross did not consume a round")

    cross_release = len(rows) + 1
    row = step("recover_r1_up_cross_released", {"r1": True, "up": True})
    captures["cross_release"] = save_frame(client, f"f{row['frame']:03d}_cross_release")

    stable_run = 0
    for _ in range(240):
        row = step("recover_r1_up_cross_released", {"r1": True, "up": True})
        stable = saw_fire and is_gun_aim_stable(
            row["anim"], row["aux"], row["recovery"]
        )
        stable_run = stable_run + 1 if stable else 0
        if stable_run >= 2:
            break
    if stable_run < 2:
        raise RuntimeError("fire recovery did not return to stable aim-up")
    recovery_stable = len(rows)
    captures["recovery_stable"] = save_frame(
        client, f"f{recovery_stable:03d}_recovery_stable"
    )

    up_release = len(rows) + 1
    standing_stable_run = 0
    saw_up_release_transition = False
    for i in range(120):
        row = step("up_released_r1_held", {"r1": True})
        if i == 0:
            captures["up_release"] = save_frame(client, f"f{row['frame']:03d}_up_release")
        stable = is_gun_aim_stable(row["anim"], row["aux"], row["recovery"])
        saw_up_release_transition = saw_up_release_transition or not stable
        standing_stable_run = (
            standing_stable_run + 1
            if saw_up_release_transition and stable
            else 0
        )
        if standing_stable_run >= 2:
            break
    if standing_stable_run < 2:
        raise RuntimeError("Up release did not return to stable standing aim")
    standing_stable_after_up_release = len(rows)
    captures["standing_stable_after_up_release"] = save_frame(
        client, f"f{standing_stable_after_up_release:03d}_standing_stable"
    )

    r1_release = len(rows) + 1
    row = step("r1_released_neutral", {})
    captures["r1_release"] = save_frame(client, f"f{row['frame']:03d}_r1_release")

    final_idle_run = 0
    for _ in range(120):
        row = step("neutral_tail", {})
        final_idle_run = (
            final_idle_run + 1
            if (row["anim"], row["aux"], row["recovery"]) == (0, 0, 0)
            else 0
        )
        if final_idle_run >= 2:
            break
    if final_idle_run < 2:
        raise RuntimeError("R1 release did not return to idle")
    final_idle = len(rows)
    captures["final_idle"] = save_frame(client, f"f{final_idle:03d}_final_idle")

    before_control = state(client)
    control_start = len(rows) + 1
    for _ in range(6):
        step("control_probe_up", {"up": True})
    for _ in range(2):
        step("control_probe_release", {})
    after_control = state(client)
    control_changed = (
        before_control["x"],
        before_control["z"],
        before_control["facing"],
    ) != (
        after_control["x"],
        after_control["z"],
        after_control["facing"],
    )
    final_ammo = ammo(client)
    return {
        "kind": "aim_up_candidate",
        "initial_ammo": initial_ammo,
        "final_ammo": final_ammo,
        "ammo_spent": initial_ammo - final_ammo,
        "aim_start_frame": aim_start,
        "aim_stable_frame": aim_stable,
        "fire_start_frame": fire_start,
        "ammo_drop_frame": ammo_drop_frame,
        "cross_release_frame": cross_release,
        "recovery_stable_frame": recovery_stable,
        "up_release_frame": up_release,
        "standing_stable_after_up_release_frame": standing_stable_after_up_release,
        "r1_release_frame": r1_release,
        "final_idle_frame": final_idle,
        "control_probe_start_frame": control_start,
        "in_control_at_final_idle": bool(rows[final_idle - 1]["game_mode"] & IN_CONTROL_MASK),
        "control_probe_changed_pose": control_changed,
        "control_before": before_control,
        "control_after": after_control,
        "saw_fire_anim": saw_fire,
        "captures": captures,
        "trace": rows,
    }


def run_production(client: BizHawkClient) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    captures: dict[str, str] = {}
    initial_ammo = ammo(client)
    original_step = client.step

    def traced_step(*args: Any, **kwargs: Any) -> Any:
        buttons: dict[str, bool] = {}
        buttons.update(
            {k: bool(v) for k, v in (kwargs.get("sticky") or {}).items() if v}
        )
        for frame_buttons in kwargs.get("frame_buttons") or []:
            buttons.update({k: bool(v) for k, v in frame_buttons.items() if v})
        buttons.update(
            {k: bool(v) for k, v in (kwargs.get("buttons") or {}).items() if v}
        )
        kwargs["echo_joypad"] = True
        result = original_step(*args, **kwargs)
        anim, aux, recovery = read_knife_hooks(client)
        row = {
            "frame": len(rows) + 1,
            "emu_frame": client.emulated_frame,
            "buttons_commanded": sorted(k for k, v in buttons.items() if v),
            "buttons_echo": list(client.last_step_echo or []),
            "anim": int(anim),
            "aux": int(aux),
            "recovery": int(recovery),
            "ammo": ammo(client),
            **state(client),
        }
        rows.append(row)
        if row["frame"] == 32:
            captures["standing_aim_stable"] = save_frame(
                client, "production_f032_standing_aim_stable"
            )
        if row["ammo"] < initial_ammo and "ammo_drop" not in captures:
            captures["ammo_drop"] = save_frame(
                client, f"production_f{row['frame']:03d}_ammo_drop"
            )
        if (
            row["ammo"] < initial_ammo
            and is_gun_aim_stable(row["anim"], row["aux"], row["recovery"])
            and "recovery_stable" not in captures
        ):
            captures["recovery_stable"] = save_frame(
                client, f"production_f{row['frame']:03d}_recovery_stable"
            )
        return result

    client.step = traced_step  # type: ignore[method-assign]
    try:
        died, frames, report = execute_attack_macro(
            client,
            empty_sticky={
                "up": False,
                "down": False,
                "left": False,
                "right": False,
                "square": False,
            },
            prev_hp=140,
            episode_start_hp=140,
        )
    finally:
        client.step = original_step  # type: ignore[method-assign]
    captures["final"] = save_frame(client, "production_final")

    cross_frames = [
        row["frame"] for row in rows if "cross" in row["buttons_commanded"]
    ]
    fire_frames = [
        row["frame"]
        for row in rows
        if row["anim"] == 0x14 and row["aux"] == 0x03
    ]
    ammo_drop = next(
        (row["frame"] for row in rows if row["ammo"] < initial_ammo), None
    )
    return {
        "kind": "production_standing",
        "died": bool(died),
        "macro_frames": int(frames),
        "report": report,
        "initial_ammo": initial_ammo,
        "final_ammo": ammo(client),
        "first_cross_frame": min(cross_frames) if cross_frames else None,
        "last_cross_frame": max(cross_frames) if cross_frames else None,
        "ammo_drop_frame": ammo_drop,
        "first_fire_anim_frame": min(fire_frames) if fire_frames else None,
        "last_fire_anim_frame": max(fire_frames) if fire_frames else None,
        "captures": captures,
        "trace": rows,
    }


def main() -> int:
    if not STATE.is_file():
        raise FileNotFoundError(STATE)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    client = BizHawkClient(
        port=PORT,
        timeout=300.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / "beretta_aim_up_7795_frame.png"),
    )
    client.start_server()
    proc = subprocess.Popen(
        emuhawk_argv(port=PORT) + ["--gdi"],
        cwd=str(EMUHAWK.parent),
    )
    try:
        client.wait_for_client()
        client.set_speed(50)
        client.load_savestate(str(STATE.resolve()))
        client.frameadvance(8)
        initial_state = state(client)
        inventory = [
            {"slot": i, "item_id": item_id, "qty": qty}
            for i, (item_id, qty) in enumerate(read_inventory(client))
        ]
        if initial_state["equipped"] != BERETTA_ID:
            raise RuntimeError(
                "QuickSave1 is not Beretta-equipped: "
                f"equipped=0x{initial_state['equipped']:02X}; refusing RAM/menu equip"
            )
        aim_up = run_aim_up(client)

        client.load_savestate(str(STATE.resolve()))
        client.frameadvance(8)
        if state(client)["equipped"] != BERETTA_ID:
            raise RuntimeError("QuickSave1 equip changed before production comparison")
        production = run_production(client)

        evidence = {
            "port": PORT,
            "savestate": str(STATE),
            "savestate_mtime": STATE.stat().st_mtime,
            "no_ram_writes": True,
            "initial_state": initial_state,
            "inventory": inventory,
            "aim_up": aim_up,
            "production_standing": production,
        }
        OUT.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({**evidence, "aim_up": {k: v for k, v in aim_up.items() if k != "trace"}, "production_standing": {k: v for k, v in production.items() if k != "trace"}}, indent=2))
        print(f"EVIDENCE={OUT}")
        return 0
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError, RuntimeError):
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
