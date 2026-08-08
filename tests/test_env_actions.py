"""Action-space wiring tests (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION, ATTACK_UP_ACTION
from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES, button_map_for_action
from re1_rl.pushable import RUN_FORWARD_ACTION, TURN_LEFT_ACTION, TURN_RIGHT_ACTION
from scripts.play_ppo_harness import _resolve_movement_actions


def test_exact_action_names_order() -> None:
    assert ACTION_NAMES == [
        "noop",
        "forward",
        "back",
        "turn_left",
        "turn_right",
        "run_forward",
        "attack_up",
        "attack",
        "attack_down",
        "interact",
        "use",
        "equip",
        *(f"deposit_slot_{i}" for i in range(8)),
        *(f"withdraw_box_{i}" for i in range(16)),
        "combine",
        *(f"select_slot_{i}" for i in range(8)),
    ]


def test_exact_action_button_maps() -> None:
    expected = {
        0: {},
        1: {"up": True},
        2: {"down": True},
        3: {"left": True},
        4: {"right": True},
        5: {"up": True, "square": True},
        6: {},
        7: {},
        8: {},
        9: {"cross": True},
        **{i: {} for i in range(10, 45)},
    }
    assert ACTION_BUTTON_MAP == expected


def test_noop_taps_cross_on_pause_menu_yes_no() -> None:
    """Pickup confirm clears in_control; masked noop must still press Cross."""
    assert button_map_for_action(0, pause_menu_modal=False)[0] == {}
    assert button_map_for_action(0, pause_menu_modal=True)[0] == {"cross": True}
    assert button_map_for_action(9, pause_menu_modal=True)[9] == {"cross": True}


def test_pause_modal_not_treated_as_box_ui_mask() -> None:
    """Chemical Yes/No in room 118 must keep noop legal (not box withdraw/close)."""
    from re1_rl.action_mask import (
        BOX_CLOSE_ACTION,
        BOX_PHASE_CHOOSE,
        BOX_WITHDRAW_ACTION,
        action_mask,
    )
    from re1_rl.env import ACTION_NAMES

    m = action_mask(
        len(ACTION_NAMES),
        None,
        in_control=False,
        box_ui_open=False,
        in_box_room=True,
        room_id="118",
        box_phase=BOX_PHASE_CHOOSE,
        inventory=[(0x01, 0)] + [(0, 0)] * 7,
        box=[(0x0B, 15)] + [(0, 0)] * 15,
    )
    assert m[0]
    assert not m[BOX_WITHDRAW_ACTION]
    assert not m[BOX_CLOSE_ACTION]


def test_attacks_are_adjacent() -> None:
    assert (ATTACK_UP_ACTION, ATTACK_ACTION, ATTACK_DOWN_ACTION) == (6, 7, 8)


def test_harness_composes_diagonal_run_actions() -> None:
    assert _resolve_movement_actions(
        {"up": True, "left": True, "square": True}
    ) == [RUN_FORWARD_ACTION, TURN_LEFT_ACTION]
    assert _resolve_movement_actions(
        {"up": True, "right": True, "square": True}
    ) == [RUN_FORWARD_ACTION, TURN_RIGHT_ACTION]
