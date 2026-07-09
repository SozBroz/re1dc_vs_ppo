"""Single probe agent: beretta attack after movement + RAM/hooks monitor.

Does NOT touch fleet ports (5555-5574). Default probe port 7788.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
OUT = ROOT / "data" / "beretta_attack_settle_probe.jsonl"

from re1_rl.action_mask import ATTACK_ACTION  # noqa: E402
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.env import ACTION_NAMES, RE1Env  # noqa: E402
from re1_rl.knife_macro import read_knife_hooks  # noqa: E402
from re1_rl.memory_map import EQUIPPED_WEAPON_ID, PLAYER_HP  # noqa: E402
from re1_rl.weapon_equip import magic_equip  # noqa: E402


def _hooks(bridge: BizHawkClient) -> str:
    anim, aux, rec = read_knife_hooks(bridge)
    return f"anim=0x{anim:02X} aux=0x{aux:02X} rec={rec}"


def _ammo(bridge: BizHawkClient, weapon_id: int = 0x02) -> int:
    from re1_rl.memory_map import INVENTORY_BASE, INVENTORY_SLOTS

    fields = [(f"inv_slot_{i}", INVENTORY_BASE + 2 * i, "u16") for i in range(INVENTORY_SLOTS)]
    ram = bridge.read_ram(fields)
    total = 0
    for i in range(INVENTORY_SLOTS):
        raw = int(ram.get(f"inv_slot_{i}", 0))
        if raw & 0xFF == weapon_id:
            total += raw >> 8
    return total


def _row(*, step: int, label: str, bridge: BizHawkClient, extra: dict | None = None) -> dict:
    raw = bridge.read_ram(
        [
            ("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8"),
            ("player_hp", PLAYER_HP, "u16"),
        ]
    )
    r = {
        "step": step,
        "label": label,
        "hooks": _hooks(bridge),
        "equipped": f"0x{int(raw['equipped_weapon_id']):02X}",
        "hp": int(raw["player_hp"]),
        "beretta_ammo": _ammo(bridge),
    }
    if extra:
        r.update(extra)
    return r


def _log(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(
        f"[beretta-probe] {row['label']:<22} {row['hooks']} "
        f"eq={row['equipped']} ammo={row['beretta_ammo']} "
        f"{''.join(f' {k}={v}' for k, v in row.items() if k in ('outcome', 'ammo_spent', 'frames', 'action'))}",
        flush=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7788)
    ap.add_argument("--cycles", type=int, default=3)
    args = ap.parse_args()
    port = int(args.port)

    if OUT.exists():
        OUT.unlink()

    bridge = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    print(f"[beretta-probe] port {port} (fleet untouched) log -> {OUT}", flush=True)
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={port}"],
        cwd=str(EMU.parent),
    )
    step_i = 0
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
        _log(_row(step=step_i, label="after_reset", bridge=bridge))

        eq = magic_equip(bridge, 0x02)
        bridge.frameadvance(4)
        _log(_row(step=step_i, label="beretta_equipped", bridge=bridge, extra={"equip": eq}))

        run_fwd = ACTION_NAMES.index("run_forward")
        for cycle in range(int(args.cycles)):
            for _ in range(4):
                step_i += 1
                _log(_row(step=step_i, label=f"c{cycle}_pre_run", bridge=bridge))
                env.step(run_fwd)
                _log(_row(step=step_i, label=f"c{cycle}_post_run", bridge=bridge))

            step_i += 1
            pre = _row(step=step_i, label=f"c{cycle}_pre_attack", bridge=bridge)
            _log(pre)
            _, rew, term, trunc, info = env.step(ATTACK_ACTION)
            report = info.get("attack_report") or {}
            post = _row(
                step=step_i,
                label=f"c{cycle}_post_attack",
                bridge=bridge,
                extra={
                    "action": "attack",
                    "outcome": report.get("outcome"),
                    "ammo_spent": report.get("ammo_spent"),
                    "frames": report.get("frames"),
                    "saw_fire_anim": report.get("saw_fire_anim"),
                    "trail": report.get("trail"),
                    "reward": float(rew),
                    "terminated": term,
                },
            )
            _log(post)
            if report.get("outcome") == "settle_interrupt":
                print(
                    f"[beretta-probe] *** FAIL cycle {cycle}: settle_interrupt "
                    f"trail={report.get('trail')}",
                    flush=True,
                )
            elif int(report.get("ammo_spent") or 0) > 0 or report.get("saw_fire_anim"):
                print(f"[beretta-probe] *** OK cycle {cycle}: fired", flush=True)
            else:
                print(
                    f"[beretta-probe] *** WARN cycle {cycle}: outcome={report.get('outcome')}",
                    flush=True,
                )
            if term or trunc:
                print("[beretta-probe] episode ended — stopping", flush=True)
                break

        print("[beretta-probe] done — EmuHawk left open; Ctrl+C to quit", flush=True)
        while True:
            import time

            time.sleep(3600)
    except KeyboardInterrupt:
        print("[beretta-probe] stopped", flush=True)
    finally:
        try:
            bridge.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
