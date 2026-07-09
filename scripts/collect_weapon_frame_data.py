"""Collect per-frame anim hooks for standing aim+fire across all weapons.

RAM-equips each weapon from jill_control_fresh.State, records a full
standing attack cycle (neutral settle, R1 aim, cross fire x2, holster tail),
and writes data/weapon_frame_data.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.knife_macro import read_knife_hooks
from re1_rl.memory_map import (
    EQUIPPED_WEAPON_ID,
    EQUIPPED_SLOT_INDEX_1BASED,
    INVENTORY_BASE,
)

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"
OUT_JSON = ROOT / "data" / "weapon_frame_data.json"
OUT_FRAMES = ROOT / "data" / "weapon_frames"

EQUIPPED_SLOT = 0x800C50BE

WEAPONS: list[tuple[int, int, str]] = [
    (0x01, 0, "knife"),
    (0x02, 30, "beretta"),
    (0x03, 30, "shotgun"),
    (0x04, 30, "colt_python_dumdum"),
    (0x05, 30, "colt_python"),
    (0x06, 100, "flamethrower"),
    (0x07, 30, "bazooka_acid"),
    (0x08, 30, "bazooka_explosive"),
    (0x09, 30, "bazooka_flame"),
    (0x0A, 30, "rocket_launcher"),
]

# RE1 samples the pad every 2 emulated frames; every button state is held
# for one 2-frame step. All *_FRAMES budgets below are emulated frames.
FRAMES_PER_STEP = 2
AIM_MAX = 120
FIRE_CROSS_FRAMES = 6  # 3 steps of r1+cross
FIRE_R1_MAX = 180
SETTLE_NEUTRAL = 20
SETTLE_TAIL = 30
STABLE_RUN_STEPS = 2  # 2 consecutive identical step-reads = 4 emulated frames


def ram_equip(client: BizHawkClient, weapon_id: int, qty: int) -> None:
    # 0x800C8689 is NOT a weapon-id mirror: it is the 1-BASED equipped
    # inventory slot index the engine consumes ammo from (verified
    # scripts/_tmp_slot_hypothesis.py 2026-07-07: writing weapon_id there
    # drained slot weapon_id-1; writing 1 drains slot 0 correctly).
    client.write_ram([
        ("inv0", INVENTORY_BASE, "u16", (qty << 8) | weapon_id),
        ("eq_p", EQUIPPED_WEAPON_ID, "u8", weapon_id),
        ("eq_s", EQUIPPED_SLOT_INDEX_1BASED, "u8", 1),
        ("eq_slot", EQUIPPED_SLOT, "u8", 0),
    ])
    client.frameadvance(5)


def slot0_qty(client: BizHawkClient) -> int:
    raw = int(client.read_ram([("inv0", INVENTORY_BASE, "u16")])["inv0"])
    return raw >> 8


def read_inventory(client: BizHawkClient) -> list[int]:
    return client.read_block(INVENTORY_BASE, 16)


def read_equip_verify(client: BizHawkClient) -> dict[str, int]:
    vals = client.read_ram([
        ("eq_p", EQUIPPED_WEAPON_ID, "u8"),
        ("eq_s", EQUIPPED_SLOT_INDEX_1BASED, "u8"),
        ("eq_slot", EQUIPPED_SLOT, "u8"),
    ])
    return {k: int(v) for k, v in vals.items()}


def is_idle(anim: int, aux: int, rec: int) -> bool:
    return anim == 0 and aux == 0 and rec == 0


def is_at_signature(
    anim: int, aux: int, rec: int, sig: tuple[int, int] | None
) -> bool:
    if sig is None:
        return is_idle(anim, aux, rec)
    return (anim, aux) == sig and rec == 0


def step_frame(
    client: BizHawkClient,
    buttons: dict[str, bool],
    phase: str,
    step_n: int,
    trail: list[str],
) -> tuple[int, int, int]:
    """Hold ``buttons`` for one 2-emulated-frame step, then read hooks."""
    client.step(buttons=buttons, n=FRAMES_PER_STEP)
    anim, aux, rec = read_knife_hooks(client)
    trail.append(
        f"{phase}f{step_n * FRAMES_PER_STEP}:"
        f"anim=0x{anim:02X}:aux=0x{aux:02X}:rec={rec}"
    )
    return anim, aux, rec


def detect_stable_aim(
    history: list[tuple[int, int, int]],
) -> tuple[int | None, dict[str, int] | None]:
    """Emulated frame where the FINAL aim signature first holds stable for
    STABLE_RUN_STEPS consecutive 2-frame step-reads (= 4 emu frames) with
    recovery==0.

    Guns raise through a draw pose (anim 0x12) that also briefly stabilizes
    before settling into the true aim hold (anim 0x13); keying on the first
    stable window would lock onto the draw pose and the fire phase would
    never "return" to it. So the signature is taken from the LAST stable
    window in the aim phase, and we report the first stable window with that
    signature. (Knife never re-stabilizes in its late 0x13 cycle, so its last
    stable window is the 0x12/0x04 crouch aim — as desired.)

    ``history`` has one entry per step; returned count is emulated frames
    (step index * FRAMES_PER_STEP)."""
    if len(history) < STABLE_RUN_STEPS:
        return None, None

    def window_sig(end: int) -> tuple[int, int] | None:
        window = history[end - STABLE_RUN_STEPS + 1 : end + 1]
        sig = (window[0][0], window[0][1])
        if all((w[0], w[1]) == sig and w[2] == 0 for w in window):
            return sig
        return None

    final_sig: tuple[int, int] | None = None
    for end in range(len(history) - 1, STABLE_RUN_STEPS - 2, -1):
        final_sig = window_sig(end)
        if final_sig is not None:
            break
    if final_sig is None:
        return None, None
    for end in range(STABLE_RUN_STEPS - 1, len(history)):
        if window_sig(end) == final_sig:
            step_idx = end - STABLE_RUN_STEPS + 2  # 1-based start of stable run
            return (
                step_idx * FRAMES_PER_STEP,
                {"anim": final_sig[0], "aux": final_sig[1]},
            )
    return None, None


def distinct_pairs(history: list[tuple[int, int, int]]) -> list[list[int]]:
    out: list[list[int]] = []
    last: tuple[int, int] | None = None
    for anim, aux, _ in history:
        pair = (anim, aux)
        if pair != last:
            out.append([anim, aux])
            last = pair
    return out


def fire_recovery_frames(
    history: list[tuple[int, int, int]],
    aim_sig: dict[str, int] | None,
    *,
    start_after: int = 0,
) -> int | None:
    """Emulated frames from ``start_after`` (step index) until aim signature
    or idle. ``history`` has one entry per 2-frame step."""
    sig = None
    if aim_sig is not None:
        sig = (aim_sig["anim"], aim_sig["aux"])
    for i in range(start_after, len(history)):
        anim, aux, rec = history[i]
        if is_at_signature(anim, aux, rec, sig):
            return (i - start_after + 1) * FRAMES_PER_STEP
    return None


def run_fire_phase(
    client: BizHawkClient,
    trail: list[str],
    phase: str,
    aim_sig: dict[str, int] | None,
    *,
    screenshot_path: Path | None = None,
) -> tuple[list[tuple[int, int, int]], int | None, list[list[int]], int | None]:
    """R1+cross for FIRE_CROSS_FRAMES (3 steps), R1-only up to FIRE_R1_MAX;
    return history, recovery."""
    history: list[tuple[int, int, int]] = []
    step_n = 0
    fire_start_idx = 0

    for _ in range(FIRE_CROSS_FRAMES // FRAMES_PER_STEP):
        step_n += 1
        anim, aux, rec = step_frame(
            client, {"r1": True, "cross": True}, phase, step_n, trail
        )
        history.append((anim, aux, rec))
        if step_n == 1 and screenshot_path is not None:
            # 2 emulated frames after the cross press
            import cv2

            OUT_FRAMES.mkdir(parents=True, exist_ok=True)
            rgb = client.screenshot()
            cv2.imwrite(
                str(screenshot_path),
                cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
            )

    fire_start_idx = 0  # recovery from first cross step
    for _ in range(FIRE_R1_MAX // FRAMES_PER_STEP):
        step_n += 1
        anim, aux, rec = step_frame(client, {"r1": True}, phase, step_n, trail)
        history.append((anim, aux, rec))
        sig = None
        if aim_sig is not None:
            sig = (aim_sig["anim"], aim_sig["aux"])
        if is_at_signature(anim, aux, rec, sig):
            break

    pairs = distinct_pairs(history)
    rec_frames = fire_recovery_frames(history, aim_sig, start_after=fire_start_idx)
    return history, rec_frames, pairs, rec_frames


def collect_weapon(
    client: BizHawkClient,
    weapon_id: int,
    qty: int,
    name: str,
) -> dict:
    trail: list[str] = []
    result: dict = {
        "item_id": weapon_id,
        "frames_to_stable_aim": None,
        "aim_signature": None,
        "fire_anim_pairs": [],
        "fire_recovery_frames": None,
        "ammo_before": 0,
        "ammo_after_shot1": 0,
        "ammo_after_shot2": 0,
        "inventory_before": [],
        "inventory_after": [],
        "shot2": {"fire_anim_pairs": [], "fire_recovery_frames": None},
        "settle_tail_frames": SETTLE_TAIL,
        "raw_trail": trail,
        "equip_verify": {},
        "anomalies": [],
    }

    client.load_savestate(str(STATE.resolve()))
    client.frameadvance(10)
    ram_equip(client, weapon_id, qty)
    equip = read_equip_verify(client)
    result["equip_verify"] = equip
    if equip.get("eq_p") != weapon_id or equip.get("eq_s") != 1:
        result["anomalies"].append(
            f"equip mismatch: expected id=0x{weapon_id:02X} slot_1based=1, got "
            f"player=0x{equip.get('eq_p', 0):02X} slot=0x{equip.get('eq_s', 0):02X}"
        )

    # Neutral settle
    for i in range(SETTLE_NEUTRAL // FRAMES_PER_STEP):
        step_frame(client, {}, "neutral", i + 1, trail)

    # AIM phase
    aim_history: list[tuple[int, int, int]] = []
    for i in range(AIM_MAX // FRAMES_PER_STEP):
        anim, aux, rec = step_frame(client, {"r1": True}, "aim", i + 1, trail)
        aim_history.append((anim, aux, rec))

    ft_stable, aim_sig = detect_stable_aim(aim_history)
    result["frames_to_stable_aim"] = ft_stable
    result["aim_signature"] = aim_sig
    if ft_stable is None:
        result["anomalies"].append("no stable aim within 120 frames")

    result["inventory_before"] = read_inventory(client)
    result["ammo_before"] = slot0_qty(client)

    shot_png = OUT_FRAMES / f"{name}_fire.png"
    _, rec1, pairs1, _ = run_fire_phase(
        client, trail, "fire", aim_sig, screenshot_path=shot_png
    )
    result["fire_anim_pairs"] = pairs1
    result["fire_recovery_frames"] = rec1
    result["ammo_after_shot1"] = slot0_qty(client)

    if len(pairs1) <= 1 and aim_sig and pairs1 == [[aim_sig["anim"], aim_sig["aux"]]]:
        result["anomalies"].append("no anim change on fire shot1")

    _, rec2, pairs2, _ = run_fire_phase(client, trail, "fire2", aim_sig)
    result["shot2"]["fire_anim_pairs"] = pairs2
    result["shot2"]["fire_recovery_frames"] = rec2
    result["ammo_after_shot2"] = slot0_qty(client)
    result["inventory_after"] = read_inventory(client)

    ammo1_consumed = result["ammo_before"] - result["ammo_after_shot1"]
    ammo2_consumed = result["ammo_after_shot1"] - result["ammo_after_shot2"]
    if qty > 0 and ammo1_consumed == 0:
        result["anomalies"].append("ammo did not decrement on shot1")
    if qty > 0 and ammo2_consumed == 0 and rec2 is not None:
        result["anomalies"].append("ammo did not decrement on shot2")

    # Holster tail
    tail_idle: int | None = None
    for i in range(SETTLE_TAIL // FRAMES_PER_STEP):
        anim, aux, rec = step_frame(client, {}, "tail", i + 1, trail)
        if tail_idle is None and is_idle(anim, aux, rec):
            tail_idle = (i + 1) * FRAMES_PER_STEP
    result["settle_tail_frames"] = tail_idle if tail_idle is not None else SETTLE_TAIL
    if tail_idle is None:
        result["anomalies"].append("did not reach idle within 30 tail frames")

    print(
        f"[{name}] aim_stable={ft_stable} sig={aim_sig} "
        f"fire_pairs={pairs1} rec1={rec1} rec2={rec2} "
        f"ammo {result['ammo_before']}->{result['ammo_after_shot1']}"
        f"->{result['ammo_after_shot2']}"
        + (f" ANOMALIES={result['anomalies']}" if result["anomalies"] else ""),
        flush=True,
    )
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7778)
    args = ap.parse_args()
    port = int(args.port)

    client = BizHawkClient(port=port, timeout=600.0, connect_timeout=120.0)
    client.start_server()
    print(f"[{port}] listening — launching EmuHawk...", flush=True)
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    output: dict = {
        "_meta": {
            "date": datetime.now(timezone.utc).isoformat(),
            "state": "jill_control_fresh.State",
            "method": (
                "ram_equip standing aim (r1, no down); 2 emu frames per "
                "button step; 0x800C8689 = 1-based equipped slot index"
            ),
            "equipped_slot_addr": f"0x{EQUIPPED_SLOT:08X}",
            "equipped_slot_written": 0,
        },
    }

    try:
        client.wait_for_client()
        print(f"[{port}] connected", flush=True)

        for weapon_id, qty, name in WEAPONS:
            print(f"\n=== {name} (0x{weapon_id:02X}) qty={qty} ===", flush=True)
            weapon_data = collect_weapon(client, weapon_id, qty, name)
            weapon_data.pop("anomalies", None)
            weapon_data.pop("equip_verify", None)
            output[name] = weapon_data

        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        print(f"\nWrote {OUT_JSON}", flush=True)
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
