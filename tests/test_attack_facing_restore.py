"""Facing helpers used by replay_leg door nudges (not attack macros)."""

from __future__ import annotations

from re1_rl.attack_macro import facing_signed_delta


def test_facing_signed_delta_left_decreases() -> None:
    assert facing_signed_delta(400, 100) == -300
    assert facing_signed_delta(100, 400) == 300
    assert facing_signed_delta(100, 100) == 0
    assert facing_signed_delta(10, 4090) == -16
