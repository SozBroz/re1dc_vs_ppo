"""Live production item-box round-trip on the exact QuickSave2."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(r"D:\re1_rl")
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.bizhawk_paths import EMUHAWK, emuhawk_argv
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.item_box import (
    BOX_ROOMS,
    can_deposit,
    can_withdraw,
    is_box_room,
    read_box,
    read_inventory,
)
from re1_rl.memory_map import ITEM_IDS

PORT = 7798
STATE = Path(
    r"D:\re1_rl\tools\BizHawk-2.11.1\PSX\State"
    r"\Resident Evil - Director's Cut (USA).Nymashock.QuickSave2.State"
)
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
LOG = ROOT / "box_roundtrip_7798_diagnostic.json"


def named_slots(slots: list[tuple[int, int]]) -> list[dict[str, Any]]:
    return [
        {
            "slot": i,
            "item_id": item_id,
            "item": ITEM_IDS.get(item_id, f"0x{item_id:02X}") if item_id else "empty",
            "qty": qty,
        }
        for i, (item_id, qty) in enumerate(slots)
    ]


def inspect_loaded(env: RE1Env, bridge: BizHawkClient, save: Path) -> dict[str, Any]:
    bridge.load_savestate(str(save))
    bridge.frameadvance(6)
    state = env._read_state(track_items=False)
    env._prev_state = dict(state)
    env._prev_hp = int(state["hp"])
    env._episode_start_hp = max(1, int(state["hp"]))
    inventory = read_inventory(bridge)
    box = read_box(bridge)
    mask = env.action_masks(state)
    deposit_reasons = {}
    withdraw_reasons = {}
    for slot in range(8):
        ok, reason = can_deposit(inventory, box, slot)
        deposit_reasons[f"deposit_slot_{slot}"] = {"can_transfer": ok, "reason": reason}
    for slot in range(16):
        ok, reason = can_withdraw(inventory, box, slot)
        withdraw_reasons[f"withdraw_box_{slot}"] = {"can_transfer": ok, "reason": reason}
    legal_deposits = [
        {"index": i, "name": ACTION_NAMES[i]}
        for i in range(12, 20)
        if bool(mask[i])
    ]
    legal_withdrawals = [
        {"index": i, "name": ACTION_NAMES[i]}
        for i in range(20, 36)
        if bool(mask[i])
    ]
    return {
        "save": str(save),
        "save_name": save.name,
        "pose": {
            key: state[key]
            for key in (
                "room_id",
                "x",
                "y",
                "z",
                "facing",
                "cam_id",
                "hp",
                "in_control",
                "game_state",
                "game_mode",
                "scene_flag",
                "msg_flag",
                "interaction_prompt",
            )
        },
        "box_room": is_box_room(state["room_id"]),
        "box_room_rule": sorted(BOX_ROOMS),
        "inventory": named_slots(inventory),
        "box_0_15": named_slots(box),
        "mask": [
            {"index": i, "name": ACTION_NAMES[i], "legal": bool(mask[i])}
            for i in range(len(mask))
        ],
        "legal_deposits": legal_deposits,
        "legal_withdrawals": legal_withdrawals,
        "deposit_transfer_checks": deposit_reasons,
        "withdraw_transfer_checks": withdraw_reasons,
    }


def main() -> int:
    if not STATE.is_file():
        raise FileNotFoundError(STATE)
    resolved_state = STATE.resolve()
    mtime = datetime.fromtimestamp(resolved_state.stat().st_mtime)
    print(f"EXACT_RESOLVED_PATH={resolved_state}", flush=True)
    print(f"EXACT_MTIME={mtime:%Y-%m-%d %H:%M:%S}", flush=True)
    if mtime.strftime("%Y-%m-%d %H:%M:%S") != "2026-07-17 21:06:05":
        raise RuntimeError(f"unexpected exact-save mtime: {mtime!s}")
    bridge = BizHawkClient(port=PORT, timeout=180.0, connect_timeout=90.0)
    bridge.start_server()
    print(f"LAUNCHING_PORT={PORT}", flush=True)
    process = subprocess.Popen(
        emuhawk_argv(port=PORT),
        cwd=str(EMUHAWK.parent),
    )
    result: dict[str, Any] = {
        "port": PORT,
        "exact_resolved_path_printed_before_launch": str(resolved_state),
        "exact_mtime": mtime.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        bridge.set_invisible(False)
        print(f"LOADING_EXACT_PATH={resolved_state}", flush=True)
        env = RE1Env(
            CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        env._load_stage()
        result["before"] = inspect_loaded(env, bridge, resolved_state)

        # Initialize the episode bookkeeping around the already-loaded exact save
        # without env.reset(), which would load the curriculum's different save.
        env._sticky_input.reset()
        env._prev_action = None
        env._forward_collision_stall = False
        env._use_phase = 0
        env._equip_phase = 0
        env._combine_phase = 0
        env._combine_slot_a = None
        env._init_anim_history()
        env._step_count = 0
        env.bridge.frame_ring.clear()
        env.bridge.attack_pins.clear()
        env._visited.reset()
        env._box_cache = None
        rgb = bridge.screenshot()
        if bridge.emulated_frame >= 0:
            bridge.frame_ring.store_rgb(bridge.emulated_frame, rgb)
        state_before = env._read_state()
        env._seed_episode_progress(state_before)
        env._episode_history.reset(str(state_before["room_id"]), step=0)
        env._visited.update(
            state_before["room_id"], state_before["x"], state_before["z"]
        )
        env._prev_state = state_before
        env._prev_hp = int(state_before["hp"])

        deposit_action = 15  # deposit_slot_3: green herb, safe/non-equipped
        corresponding_withdraw_action = 23  # first empty box slot is box slot 3
        before_inventory = read_inventory(bridge)
        before_box = read_box(bridge)
        before_mask = env.action_masks(state_before)
        if not bool(before_mask[deposit_action]):
            raise RuntimeError("chosen production deposit action is unexpectedly masked")

        _obs_d, reward_d, terminated_d, truncated_d, deposit_info = env.step(
            deposit_action
        )
        state_after_deposit = dict(deposit_info["state"])
        inventory_after_deposit = read_inventory(bridge)
        box_after_deposit = read_box(bridge)
        mask_after_deposit = env.action_masks(state_after_deposit)
        if not bool(mask_after_deposit[corresponding_withdraw_action]):
            raise RuntimeError("corresponding production withdraw action stayed masked")

        _obs_w, reward_w, terminated_w, truncated_w, withdraw_info = env.step(
            corresponding_withdraw_action
        )
        state_after_withdraw = dict(withdraw_info["state"])
        final_inventory = read_inventory(bridge)
        final_box = read_box(bridge)
        final_mask = env.action_masks(state_after_withdraw)

        expected_deposit_inventory = list(before_inventory)
        expected_deposit_inventory[3] = (0, 0)
        expected_deposit_box = list(before_box)
        expected_deposit_box[3] = before_inventory[3]
        deposit_exact = (
            inventory_after_deposit == expected_deposit_inventory
            and box_after_deposit == expected_deposit_box
        )
        final_equal = final_inventory == before_inventory and final_box == before_box
        masks_transitioned = (
            bool(before_mask[deposit_action])
            and not bool(before_mask[corresponding_withdraw_action])
            and not bool(mask_after_deposit[deposit_action])
            and bool(mask_after_deposit[corresponding_withdraw_action])
            and bool(final_mask[deposit_action])
            and not bool(final_mask[corresponding_withdraw_action])
        )
        reports_ok = bool((deposit_info.get("magic_report") or {}).get("ok")) and bool(
            (withdraw_info.get("magic_report") or {}).get("ok")
        )
        control_ok = bool(state_after_deposit["in_control"]) and bool(
            state_after_withdraw["in_control"]
        )
        no_timeout = not any(
            (terminated_d, truncated_d, terminated_w, truncated_w)
        )
        passed = (
            deposit_exact
            and final_equal
            and masks_transitioned
            and reports_ok
            and control_ok
            and no_timeout
        )
        result["production_roundtrip"] = {
            "deposit_action": {
                "index": deposit_action,
                "name": ACTION_NAMES[deposit_action],
                "legal_before": bool(before_mask[deposit_action]),
                "report": deposit_info.get("magic_report"),
                "reward": reward_d,
                "terminated": terminated_d,
                "truncated": truncated_d,
            },
            "corresponding_withdraw_action": {
                "index": corresponding_withdraw_action,
                "name": ACTION_NAMES[corresponding_withdraw_action],
                "legal_after_deposit": bool(
                    mask_after_deposit[corresponding_withdraw_action]
                ),
                "report": withdraw_info.get("magic_report"),
                "reward": reward_w,
                "terminated": terminated_w,
                "truncated": truncated_w,
            },
            "mask_transition": {
                "before": {
                    ACTION_NAMES[deposit_action]: bool(before_mask[deposit_action]),
                    ACTION_NAMES[corresponding_withdraw_action]: bool(
                        before_mask[corresponding_withdraw_action]
                    ),
                },
                "after_deposit": {
                    ACTION_NAMES[deposit_action]: bool(
                        mask_after_deposit[deposit_action]
                    ),
                    ACTION_NAMES[corresponding_withdraw_action]: bool(
                        mask_after_deposit[corresponding_withdraw_action]
                    ),
                },
                "after_withdraw": {
                    ACTION_NAMES[deposit_action]: bool(final_mask[deposit_action]),
                    ACTION_NAMES[corresponding_withdraw_action]: bool(
                        final_mask[corresponding_withdraw_action]
                    ),
                },
            },
            "before_snapshot": {
                "inventory": named_slots(before_inventory),
                "box_0_15": named_slots(before_box),
                "in_control": bool(state_before["in_control"]),
                "game_mode": int(state_before["game_mode"]),
            },
            "after_deposit_snapshot": {
                "inventory": named_slots(inventory_after_deposit),
                "box_0_15": named_slots(box_after_deposit),
                "in_control": bool(state_after_deposit["in_control"]),
                "game_mode": int(state_after_deposit["game_mode"]),
            },
            "after_withdraw_snapshot": {
                "inventory": named_slots(final_inventory),
                "box_0_15": named_slots(final_box),
                "in_control": bool(state_after_withdraw["in_control"]),
                "game_mode": int(state_after_withdraw["game_mode"]),
            },
            "checks": {
                "deposit_exact_delta": deposit_exact,
                "final_roundtrip_equality": final_equal,
                "mask_transition_exact": masks_transitioned,
                "magic_reports_ok": reports_ok,
                "in_control_after_each": control_ok,
                "no_termination_or_timeout": no_timeout,
                "no_unrelated_slot_corruption": deposit_exact and final_equal,
                "no_quantity_duplication_or_loss": final_equal,
                "menu_closed_after_each": control_ok
                and int(state_after_deposit["game_mode"]) == 0x80
                and int(state_after_withdraw["game_mode"]) == 0x80,
            },
        }
        result["verdict"] = {
            "status": "PASS" if passed else "FAIL",
            "reason": "all production round-trip checks passed"
            if passed
            else "one or more production round-trip checks failed",
        }
        LOG.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2), flush=True)
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
