"""Sticky movement + pulse input state (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES, _apply_action_input
from re1_rl.sticky_input import StickyInputState, human_buttons_to_step, human_step_gate


def _idx(name: str) -> int:
    return ACTION_NAMES.index(name)


def test_forward_sticks_up_across_steps() -> None:
    s = StickyInputState()
    sticky, pulse, _ = s.apply(_idx("forward"), ACTION_BUTTON_MAP)
    assert pulse is None
    assert sticky["up"] is True
    assert sticky["square"] is False

    sticky2, pulse2, _ = s.apply(_idx("forward"), ACTION_BUTTON_MAP)
    assert pulse2 is None
    assert sticky2["up"] is True


def test_run_forward_sticks_square() -> None:
    s = StickyInputState()
    _apply_action_input(s, _idx("run_forward"))
    sticky, _, _ = _apply_action_input(s, _idx("run_forward"))
    assert sticky["up"] is True
    assert sticky["square"] is True


def test_run_forward_diagonals_compose_via_sticky_latch() -> None:
    left = StickyInputState()
    _apply_action_input(left, _idx("run_forward"))
    left_sticky, _, _ = _apply_action_input(left, _idx("turn_left"))
    assert left_sticky == {
        "up": True,
        "down": False,
        "left": True,
        "right": False,
        "square": True,
    }

    right = StickyInputState()
    _apply_action_input(right, _idx("run_forward"))
    right_sticky, _, _ = _apply_action_input(right, _idx("turn_right"))
    assert right_sticky == {
        "up": True,
        "down": False,
        "left": False,
        "right": True,
        "square": True,
    }


def test_forward_clears_run() -> None:
    s = StickyInputState()
    _apply_action_input(s, _idx("run_forward"))
    sticky, _, _ = _apply_action_input(s, _idx("forward"))
    assert sticky["up"] is True
    assert sticky["square"] is False


def test_turn_keeps_forward_and_run() -> None:
    s = StickyInputState()
    _apply_action_input(s, _idx("run_forward"))
    sticky, _, _ = _apply_action_input(s, _idx("turn_left"))
    assert sticky["up"] is True
    assert sticky["square"] is True
    assert sticky["left"] is True
    assert sticky["right"] is False


def test_noop_clears_sticky() -> None:
    s = StickyInputState()
    s.apply(_idx("forward"), ACTION_BUTTON_MAP)
    sticky, _, _ = s.apply(_idx("noop"), ACTION_BUTTON_MAP)
    assert sticky == {
        "up": False,
        "down": False,
        "left": False,
        "right": False,
        "square": False,
    }


def test_interact_holds_cross_full_step() -> None:
    s = StickyInputState()
    _apply_action_input(s, _idx("forward"))
    sticky, pulse, pulse_hold = _apply_action_input(s, _idx("interact"))
    assert sticky["up"] is True
    assert pulse is None
    assert pulse_hold == {"cross": True}
    assert "cross" not in sticky


def test_knife_swing_clears_sticky_for_macro() -> None:
    from re1_rl.sticky_input import KNIFE_ACTION

    s = StickyInputState()
    s.apply(_idx("run_forward"), ACTION_BUTTON_MAP)
    sticky, pulse, pulse_hold = s.apply(KNIFE_ACTION, ACTION_BUTTON_MAP)
    assert sticky == {k: False for k in ("up", "down", "left", "right", "square")}
    assert pulse is None
    assert pulse_hold is None


def test_human_buttons_latch_directions_and_hold_face() -> None:
    sticky, pulse, pulse_hold = human_buttons_to_step(
        {"up": True, "cross": True},
    )
    assert sticky["up"] is True
    assert pulse is None
    assert pulse_hold == {"cross": True}

    sticky2, _, _ = human_buttons_to_step({"up": True})
    assert sticky2["up"] is True


def test_human_step_gate_one_chunk_per_press() -> None:
    assert human_step_gate({"up": True}, armed=True) == (True, False)
    assert human_step_gate({"up": True}, armed=False) == (False, False)
    assert human_step_gate({}, armed=False) == (False, True)
    assert human_step_gate({"up": True}, armed=True) == (True, False)
    # release re-arms; same movement again commits a second latched chunk
    assert human_step_gate({"up": True}, armed=True) == (True, False)


def test_attack_up_slot_is_macro_not_pulse() -> None:
    """Slot 8 is attack_up; no quickturn pulse buttons."""
    s = StickyInputState()
    s.apply(_idx("forward"), ACTION_BUTTON_MAP)
    sticky, pulse, _ = s.apply(_idx("attack_up"), ACTION_BUTTON_MAP)
    assert not pulse
    assert sticky.get("square") is False


def test_noop_pause_modal_delivers_cross_pulse_hold() -> None:
    """Pickup Yes/No: button_map remaps noop→Cross; sticky must not drop it."""
    from re1_rl.env import button_map_for_action

    s = StickyInputState()
    s.apply(_idx("forward"), ACTION_BUTTON_MAP)
    bmap = button_map_for_action(0, pause_menu_modal=True)
    sticky, pulse, pulse_hold = s.apply(0, bmap)
    assert sticky == {
        "up": False,
        "down": False,
        "left": False,
        "right": False,
        "square": False,
    }
    assert pulse is None
    assert pulse_hold == {"cross": True}

    # Plain noop still clears sticky and sends nothing.
    sticky2, pulse2, ph2 = s.apply(0, ACTION_BUTTON_MAP)
    assert sticky2["up"] is False
    assert pulse2 is None
    assert ph2 is None
