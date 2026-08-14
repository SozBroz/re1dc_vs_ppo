"""Combat-macro return-to-entry-heading (RE1 R1 auto-aim amplifier)."""

from __future__ import annotations

from re1_rl.attack_macro import (
    FACING_RESTORE_NEUTRAL_FRAMES,
    FACING_RESTORE_TOL,
    _finish_attack_with_facing,
    _restore_entry_facing,
    facing_signed_delta,
)


class _FacingBridge:
    def __init__(self, facing: int, *, step_delta: int = 32) -> None:
        self.facing = int(facing) & 0xFFF
        self.step_delta = int(step_delta)
        self.buttons: list[dict[str, bool]] = []

    def read_ram(self, fields):
        names = {f[0] for f in fields}
        out = {}
        if "player_facing" in names:
            out["player_facing"] = self.facing
        if "player_hp" in names:
            out["player_hp"] = 96
        return out

    def step(self, n=1, sticky=None, frame_buttons=None, echo_joypad=False):
        del n, sticky, echo_joypad
        btn = (frame_buttons or [{}])[0]
        self.buttons.append(dict(btn))
        if btn.get("left"):
            self.facing = (self.facing - self.step_delta) & 0xFFF
        elif btn.get("right"):
            self.facing = (self.facing + self.step_delta) & 0xFFF
        return 0, False


def test_facing_signed_delta_left_decreases() -> None:
    assert facing_signed_delta(400, 100) == -300
    assert facing_signed_delta(100, 400) == 300
    assert facing_signed_delta(100, 100) == 0
    assert facing_signed_delta(10, 4090) == -16


def test_restore_turns_left_back_to_entry() -> None:
    bridge = _FacingBridge(400)
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, frames, info = _restore_entry_facing(
        bridge,
        empty,
        facing_before=100,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert info["facing_restored"]
    assert info["entry_facing"] == 100
    assert info["autoaim_facing"] == 400
    assert info["final_facing"] == info["facing_final"]
    assert abs(facing_signed_delta(bridge.facing, 100)) <= FACING_RESTORE_TOL
    assert any(b.get("left") for b in bridge.buttons)
    assert not any(b.get("right") or b.get("up") or b.get("down") for b in bridge.buttons)
    assert frames >= FACING_RESTORE_NEUTRAL_FRAMES


def test_restore_turns_right_across_wrap() -> None:
    bridge = _FacingBridge(4000)
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, frames, info = _restore_entry_facing(
        bridge,
        empty,
        facing_before=10,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert info["facing_restored"]
    assert abs(facing_signed_delta(bridge.facing, 10)) <= FACING_RESTORE_TOL
    assert any(b.get("right") for b in bridge.buttons)
    assert frames > 0


def test_restore_skips_when_entry_facing_unknown() -> None:
    bridge = _FacingBridge(400)
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, frames, info = _restore_entry_facing(
        bridge,
        empty,
        facing_before=None,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert frames == 0
    assert info["facing_restored"] is False
    assert bridge.buttons == []
    assert bridge.facing == 400


def test_finish_skips_restore_for_knife() -> None:
    bridge = _FacingBridge(400)
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, frames, report = _finish_attack_with_facing(
        bridge,
        empty,
        100,
        False,
        12,
        {"frames": 12, "weapon_id": 1},
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert frames == 12
    assert report["facing_restored"] is False
    assert bridge.buttons == []


def test_finish_does_not_restore_on_death() -> None:
    bridge = _FacingBridge(400)
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, frames, report = _finish_attack_with_facing(
        bridge,
        empty,
        100,
        True,
        12,
        {"frames": 12},
        prev_hp=96,
        episode_start_hp=96,
    )
    assert died
    assert frames == 12
    assert report["facing_restored"] is False
    assert bridge.buttons == []
    assert bridge.facing == 400
