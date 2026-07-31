"""After every movement/attack PPO action, switch weapons via equip path.

Uses the fleet curriculum init state (jill_control_fresh.State).
For each start weapon and each precursor action:
  reset -> magic-equip start -> env.step(precursor) -> equip -> select other weapon
  Verify equipped RAM id changed to the target.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import (  # noqa: E402
    ATTACK_ACTION,
    ATTACK_DOWN_ACTION,
    ATTACK_UP_ACTION,
    EQUIP_ACTION,
    SELECT_SLOT_BASE,
)
from re1_rl.attack_macro import read_equipped_weapon  # noqa: E402
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import EMUHAWK, LUA, ROM  # noqa: E402
from re1_rl.env import ACTION_NAMES, RE1Env  # noqa: E402
from re1_rl.item_box import read_inventory  # noqa: E402
from re1_rl.knife_macro import knife_action_ready, read_knife_hooks  # noqa: E402
from re1_rl.memory_map import ITEM_IDS  # noqa: E402
from re1_rl.weapon_equip import (  # noqa: E402
    EQUIPPABLE_WEAPON_IDS,
    magic_equip_slot,
    policy_inventory,
)

PORT = 7794
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
OUT = ROOT / "data" / "live_equip_after_actions_sweep.json"

# Movement + interact + all attack macros the agent can pick.
PRECURSORS = [
    "noop",
    "forward",
    "back",
    "turn_left",
    "turn_right",
    "run_forward",
    "interact",
    "attack",
    "attack_up",
    "attack_down",
]


def _name_to_id(name: str) -> int:
    return ACTION_NAMES.index(name)


def _weapon_slots(inv: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for slot, (item_id, _qty) in enumerate(inv):
        if int(item_id) in EQUIPPABLE_WEAPON_IDS:
            out.append((slot, int(item_id), ITEM_IDS.get(int(item_id), f"id_{item_id}")))
    return out


def main() -> int:
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
    results: dict[str, Any] = {
        "curriculum": str(CURRICULUM),
        "precursors": PRECURSORS,
        "trials": [],
        "summary": {},
    }
    try:
        bridge.wait_for_client()
        bridge.set_speed(400)
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        env.reset()
        agent_state = ROOT / env._stage["init_savestate"]
        results["state"] = str(agent_state)
        weapons = _weapon_slots(policy_inventory(read_inventory(bridge)))
        results["weapons"] = [
            {"slot": s, "id": i, "name": n} for s, i, n in weapons
        ]
        print(
            f"[READY] state={agent_state} weapons={results['weapons']}",
            flush=True,
        )
        if len(weapons) < 2:
            results["summary"] = {"ok": False, "reason": "need_two_weapons"}
            OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
            return 1

        passes = 0
        fails = 0
        for start_slot, start_id, start_name in weapons:
            others = [w for w in weapons if w[0] != start_slot]
            for target_slot, target_id, target_name in others:
                for prec_name in PRECURSORS:
                    prec = _name_to_id(prec_name)
                    env.reset()
                    magic_equip_slot(bridge, start_slot)
                    bridge.frameadvance(4)

                    mask0 = env.action_masks()
                    prec_legal = bool(mask0[prec])
                    eq0 = int(read_equipped_weapon(bridge) or 0)
                    hooks0 = read_knife_hooks(bridge)

                    # Force the precursor the same way env.step would for an agent pick.
                    _obs, _r, term1, trunc1, info1 = env.step(prec)
                    if term1 or trunc1:
                        row = {
                            "start": start_name,
                            "target": target_name,
                            "precursor": prec_name,
                            "precursor_legal": prec_legal,
                            "ok": False,
                            "stuck": "episode_ended_on_precursor",
                            "info1": {
                                "action_name": info1.get("action_name"),
                                "attack_report": info1.get("attack_report"),
                                "knife_anim_report": info1.get("knife_anim_report"),
                            },
                        }
                        fails += 1
                        results["trials"].append(row)
                        print(
                            f"[FAIL] {start_name}|{prec_name}->{target_name}: "
                            f"episode ended on precursor",
                            flush=True,
                        )
                        continue

                    hooks1 = read_knife_hooks(bridge)
                    mask1 = env.action_masks()
                    equip_legal = bool(mask1[EQUIP_ACTION])
                    eq1 = int(read_equipped_weapon(bridge) or 0)
                    ready1 = knife_action_ready(*hooks1)

                    if not equip_legal:
                        # One settle noop if anim gate is the only blocker.
                        settled = False
                        if not ready1:
                            for _ in range(8):
                                if term1 or trunc1:
                                    break
                                env.step(0)
                                hooks1 = read_knife_hooks(bridge)
                                mask1 = env.action_masks()
                                equip_legal = bool(mask1[EQUIP_ACTION])
                                ready1 = knife_action_ready(*hooks1)
                                if equip_legal:
                                    settled = True
                                    break
                        row = {
                            "start": start_name,
                            "target": target_name,
                            "precursor": prec_name,
                            "precursor_legal": prec_legal,
                            "equipped_after_precursor": ITEM_IDS.get(eq1, f"id_{eq1}"),
                            "hooks_after_precursor": {
                                "anim": hooks1[0],
                                "aux": hooks1[1],
                                "recovery": hooks1[2],
                            },
                            "anim_ready": ready1,
                            "equip_legal": equip_legal,
                            "settled_with_noop": settled,
                            "ok": False,
                            "stuck": "equip_masked_after_precursor",
                            "legal_after_precursor": [
                                ACTION_NAMES[i] for i, ok in enumerate(mask1) if ok
                            ],
                        }
                        if not equip_legal:
                            fails += 1
                            results["trials"].append(row)
                            print(
                                f"[FAIL] {start_name}|{prec_name}->{target_name}: "
                                f"equip masked after precursor "
                                f"hooks={hooks1} ready={ready1}",
                                flush=True,
                            )
                            continue

                    # PPO equip open
                    _obs, _r, term2, trunc2, info2 = env.step(EQUIP_ACTION)
                    report2 = info2.get("magic_report") or {}
                    phase = int(getattr(env, "_equip_phase", 0))
                    if phase != 1 or report2.get("reason") != "equip_open":
                        row = {
                            "start": start_name,
                            "target": target_name,
                            "precursor": prec_name,
                            "precursor_legal": prec_legal,
                            "ok": False,
                            "stuck": "equip_open_failed",
                            "magic_report": report2,
                            "equip_phase": phase,
                        }
                        fails += 1
                        results["trials"].append(row)
                        print(
                            f"[FAIL] {start_name}|{prec_name}->{target_name}: "
                            f"equip open failed {report2}",
                            flush=True,
                        )
                        continue

                    mask2 = env.action_masks()
                    select = SELECT_SLOT_BASE + target_slot
                    select_legal = bool(mask2[select])
                    if not select_legal:
                        row = {
                            "start": start_name,
                            "target": target_name,
                            "precursor": prec_name,
                            "precursor_legal": prec_legal,
                            "ok": False,
                            "stuck": "target_slot_masked",
                            "phase1_legal": [
                                ACTION_NAMES[i] for i, ok in enumerate(mask2) if ok
                            ],
                        }
                        fails += 1
                        results["trials"].append(row)
                        print(
                            f"[FAIL] {start_name}|{prec_name}->{target_name}: "
                            f"target slot masked; legal="
                            f"{[ACTION_NAMES[i] for i, ok in enumerate(mask2) if ok]}",
                            flush=True,
                        )
                        continue

                    _obs, _r, term3, trunc3, info3 = env.step(select)
                    report3 = info3.get("magic_report") or {}
                    eq_final = int(read_equipped_weapon(bridge) or 0)
                    ok = (
                        bool(report3.get("ok"))
                        and eq_final == target_id
                        and int(getattr(env, "_equip_phase", 0)) == 0
                    )
                    row = {
                        "start": start_name,
                        "target": target_name,
                        "precursor": prec_name,
                        "precursor_legal": prec_legal,
                        "hooks_before_precursor": {
                            "anim": hooks0[0],
                            "aux": hooks0[1],
                            "recovery": hooks0[2],
                        },
                        "hooks_after_precursor": {
                            "anim": hooks1[0],
                            "aux": hooks1[1],
                            "recovery": hooks1[2],
                        },
                        "equipped_before": ITEM_IDS.get(eq0, f"id_{eq0}"),
                        "equipped_after_precursor": ITEM_IDS.get(eq1, f"id_{eq1}"),
                        "equipped_final": ITEM_IDS.get(eq_final, f"id_{eq_final}"),
                        "equipped_final_id": eq_final,
                        "target_id": target_id,
                        "equip_open": report2,
                        "equip_select": report3,
                        "attack_report": info1.get("attack_report"),
                        "knife_anim_report": info1.get("knife_anim_report"),
                        "ok": ok,
                    }
                    if ok:
                        passes += 1
                        print(
                            f"[OK] {start_name}|{prec_name}->{target_name}: "
                            f"{ITEM_IDS.get(eq0, '?')} => {ITEM_IDS.get(eq_final, '?')} "
                            f"(prec_legal={prec_legal})",
                            flush=True,
                        )
                    else:
                        fails += 1
                        row["stuck"] = "switch_failed"
                        print(
                            f"[FAIL] {start_name}|{prec_name}->{target_name}: "
                            f"final={ITEM_IDS.get(eq_final, '?')} "
                            f"report={report3}",
                            flush=True,
                        )
                    results["trials"].append(row)

        results["summary"] = {
            "ok": fails == 0,
            "passes": passes,
            "fails": fails,
            "n_trials": passes + fails,
        }
        OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(
            f"[SUMMARY] passes={passes} fails={fails} wrote={OUT}",
            flush=True,
        )
        return 0 if fails == 0 else 2
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
