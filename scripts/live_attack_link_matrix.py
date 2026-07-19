"""Live attack-link matrix on QuickSave3: 3 macros × 3 successors × 3 weapons."""

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
from re1_rl.knife_macro import (  # noqa: E402
    execute_knife_macro,
    knife_action_ready,
    read_knife_hooks,
)
from re1_rl.memory_map import PLAYER_HP  # noqa: E402
from re1_rl.weapon_equip import read_inventory_ids  # noqa: E402

PORT = 7793
STATE = newest_quicksave()
OUT = ROOT / "data" / "live_attack_link_matrix.json"

WEAPONS = (("knife", 0x01), ("beretta", 0x02), ("shotgun", 0x03))
MACROS: tuple[tuple[str, Callable[..., tuple[bool, int, dict[str, Any]]]], ...] = (
    ("attack", execute_attack_macro),
    ("attack_up", execute_attack_up_macro),
    ("attack_down", execute_attack_down_macro),
)
EMPTY_STICKY = {
    key: False for key in ("up", "down", "left", "right", "square", "cross", "r1")
}


def execute_legacy_knife_swing(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, dict[str, Any]]:
    died, frames = execute_knife_macro(
        bridge,
        empty_sticky=empty_sticky,
        use_ram_gates=True,
        link_aim=True,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    raw = getattr(bridge, "last_knife_anim_report", None) or {}
    report = dict(raw)
    report.setdefault("outcome", "ok")
    report["weapon"] = "knife"
    report["saw_fire_anim"] = report.get("outcome") == "ok"
    report["frames"] = frames
    return died, frames, report


KNIFE_MACROS = MACROS + (("knife_swing", execute_legacy_knife_swing),)


def _hp(bridge: BizHawkClient) -> int:
    return int(bridge.read_ram([("hp", PLAYER_HP, "u16")])["hp"])


def _attack_ok(weapon_id: int, died: bool, report: dict[str, Any]) -> bool:
    if died or report.get("outcome") != "ok":
        return False
    if not report.get("link_aim_held"):
        return False
    if weapon_id == 0x01:
        return bool(report.get("saw_fire_anim"))
    return int(report.get("ammo_spent", 0) or 0) >= 1


def run_pair(
    bridge: BizHawkClient,
    *,
    weapon_name: str,
    weapon_id: int,
    first_name: str,
    first_fn: Callable[..., tuple[bool, int, dict[str, Any]]],
    second_name: str,
    second_fn: Callable[..., tuple[bool, int, dict[str, Any]]],
) -> dict[str, Any]:
    bridge.load_savestate(str(STATE))
    bridge.frameadvance(6)
    inventory_ids = read_inventory_ids(bridge)
    slot = inventory_ids.index(weapon_id)
    died_equip, _equip_frames, equip = execute_equip_macro(
        bridge,
        slot,
        prev_hp=_hp(bridge),
        episode_start_hp=_hp(bridge),
    )
    bridge.frameadvance(4)
    hp = _hp(bridge)

    died1, frames1, report1 = first_fn(
        bridge,
        empty_sticky=EMPTY_STICKY,
        prev_hp=hp,
        episode_start_hp=hp,
    )
    hooks_between = read_knife_hooks(bridge)
    died2, frames2, report2 = second_fn(
        bridge,
        empty_sticky=EMPTY_STICKY,
        prev_hp=hp,
        episode_start_hp=hp,
    )
    hooks_after = read_knife_hooks(bridge)

    first_ok = _attack_ok(weapon_id, died1, report1)
    second_ok = _attack_ok(weapon_id, died2, report2)
    link_boundary_ok = int(hooks_between[2]) == 0 and knife_action_ready(
        *hooks_between
    )
    return {
        "weapon": weapon_name,
        "weapon_id": weapon_id,
        "first": first_name,
        "second": second_name,
        "equip_ok": not died_equip and bool(equip.get("ok")),
        "first_ok": first_ok,
        "second_ok": second_ok,
        "link_boundary_ok": link_boundary_ok,
        "ok": (
            not died_equip
            and bool(equip.get("ok"))
            and first_ok
            and second_ok
            and link_boundary_ok
        ),
        "frames_first": frames1,
        "frames_second": frames2,
        "hooks_between": list(hooks_between),
        "hooks_after": list(hooks_after),
        "first_report": report1,
        "second_report": report2,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-only", action="store_true")
    parser.add_argument("--weapon", choices=[name for name, _ in WEAPONS])
    args = parser.parse_args()
    if not STATE.is_file():
        raise FileNotFoundError(STATE)
    bridge = BizHawkClient(port=PORT, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
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
    rows: list[dict[str, Any]] = []
    try:
        bridge.wait_for_client()
        bridge.set_speed(40)
        for weapon_name, weapon_id in WEAPONS:
            if args.weapon and weapon_name != args.weapon:
                continue
            macros = KNIFE_MACROS if weapon_id == 0x01 else MACROS
            for first_name, first_fn in macros:
                for second_name, second_fn in macros:
                    if args.legacy_only and "knife_swing" not in (
                        first_name,
                        second_name,
                    ):
                        continue
                    if args.legacy_only and weapon_id != 0x01:
                        continue
                    row = run_pair(
                        bridge,
                        weapon_name=weapon_name,
                        weapon_id=weapon_id,
                        first_name=first_name,
                        first_fn=first_fn,
                        second_name=second_name,
                        second_fn=second_fn,
                    )
                    rows.append(row)
                    flag = "PASS" if row["ok"] else "FAIL"
                    print(
                        f"[{flag}] {weapon_name:8s} "
                        f"{first_name:11s}->{second_name:11s} "
                        f"frames={row['frames_first']}+{row['frames_second']} "
                        f"between={row['hooks_between']}",
                        flush=True,
                    )
        payload = {
            "state": str(STATE),
            "port": PORT,
            "results": rows,
            "all_ok": all(row["ok"] for row in rows),
        }
        out = (
            OUT.with_name("live_attack_legacy_link_matrix.json")
            if args.legacy_only
            else (
                OUT.with_name(f"live_attack_{args.weapon}_link_matrix.json")
                if args.weapon
                else OUT
            )
        )
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {out} all_ok={payload['all_ok']}", flush=True)
        return 0 if payload["all_ok"] else 1
    finally:
        try:
            bridge.quit()
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
