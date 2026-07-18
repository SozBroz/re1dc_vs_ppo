"""Sticky movement + pulse input state (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES
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
    s.apply(_idx("run_forward"), ACTION_BUTTON_MAP)
    sticky, _, _ = s.apply(_idx("run_forward"), ACTION_BUTTON_MAP)
    assert sticky["up"] is True
    assert sticky["square"] is True


def test_forward_clears_run() -> None:
    s = StickyInputState()
    s.apply(_idx("run_forward"), ACTION_BUTTON_MAP)
    sticky, _, _ = s.apply(_idx("forward"), ACTION_BUTTON_MAP)
    assert sticky["up"] is True
    assert sticky["square"] is False


def test_turn_keeps_forward_and_run() -> None:
    s = StickyInputState()
    s.apply(_idx("run_forward"), ACTION_BUTTON_MAP)
    sticky, _, _ = s.apply(_idx("turn_left"), ACTION_BUTTON_MAP)
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
    s.apply(_idx("forward"), ACTION_BUTTON_MAP)
    sticky, pulse, pulse_hold = s.apply(_idx("interact"), ACTION_BUTTON_MAP)
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
    """Slot 6 is attack_up; no quickturn pulse buttons."""
    s = StickyInputState()
    s.apply(_idx("forward"), ACTION_BUTTON_MAP)
    sticky, pulse, _ = s.apply(_idx("attack_up"), ACTION_BUTTON_MAP)
    assert not pulse
    assert sticky.get("square") is False
