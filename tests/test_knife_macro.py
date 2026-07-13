"""Phased knife swing macro (aim / swing / recovery at 30fps game logic)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES
from re1_rl.knife_macro import (
    KNIFE_SETTLE_MAX_WAIT_FRAMES,
    KNIFE_SETTLE_MID_SWING_MAX_WAIT_FRAMES,
    KNIFE_AIM_GAME_FRAMES,
    KNIFE_FRAME_SCALE,
    KNIFE_MACRO_FRAMES,
    KNIFE_RECOVERY_GAME_FRAMES,
    KNIFE_SWING_GAME_FRAMES,
    KnifeAnimValidator,
    build_knife_frame_buttons,
    classify_knife_anim,
    execute_knife_macro,
    expected_swing_recovery_emu_frames,
    is_crouch_knife_aim_ready,
    is_idle_recovery_latch,
    is_knife_animation_idle,
    is_knife_foreign_anim,
    is_knife_locomotion,
    is_knife_mid_swing_state,
    is_knife_slash_anim,
    is_knife_swing_recovery_tail,
    is_knife_macro_interrupted,
    is_knife_macro_track,
    is_knife_settle_complete,
    is_knife_settle_wait_state,
    is_standing_pre_knife_idle,
    is_standing_pre_knife_hook,
    is_standing_recovery_latch,
    knife_action_blocked_by_recovery,
    knife_action_ready,
    knife_macro_frame_count,
    read_knife_hooks,
)
from re1_rl.sticky_input import KNIFE_ACTION, StickyInputState


def test_knife_slash_anim_is_0x14_not_0x13() -> None:
    assert is_knife_slash_anim(0x14, 0x04, 8)
    assert is_knife_slash_anim(0x14, 0x00, 0)
    assert not is_knife_slash_anim(0x13, 0x04, 13)
    assert not is_knife_slash_anim(0x12, 0x04, 0)


def test_ram_gated_no_slash_when_only_aim_hold_0x13() -> None:
    """0x13 aim-hold must not report outcome=ok / swing_anim (false swing logs)."""
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    # Aim then only 0x13 forever — old bug treated this as swung.
    hook_seq = [(0, 0, 0), (0, 0, 0), (0x12, 0x04, 0), (0x12, 0x04, 0)] + [
        (0x13, 0x04, 8)
    ] * 80
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0x13, 0x04, 8)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    report = bridge.last_knife_anim_report
    assert report["outcome"] in ("no_slash", "swing_timeout")
    assert report["swing_anim"] is False
    assert report["outcome"] != "ok"


def test_knife_macro_covers_full_21_game_frame_animation() -> None:
    assert (KNIFE_AIM_GAME_FRAMES, KNIFE_SWING_GAME_FRAMES, KNIFE_RECOVERY_GAME_FRAMES) == (
        5,
        5,
        11,
    )
    assert KNIFE_FRAME_SCALE == 2  # 30fps game logic on ~60fps emulated frames
    assert knife_macro_frame_count() == KNIFE_MACRO_FRAMES == 21 * 2


def test_knife_frame_schedule_has_aim_swing_recovery_phases() -> None:
    frames = build_knife_frame_buttons()
    assert len(frames) == KNIFE_MACRO_FRAMES
    aim_end = KNIFE_AIM_GAME_FRAMES * KNIFE_FRAME_SCALE
    swing_end = aim_end + KNIFE_SWING_GAME_FRAMES * KNIFE_FRAME_SCALE
    for i, btn in enumerate(frames):
        assert btn["r1"] is True
        assert btn["down"] is True
        if aim_end <= i < swing_end:
            assert btn.get("cross") is True, f"frame {i + 1}: cross must be held in swing"
        else:
            assert "cross" not in btn, f"frame {i + 1}: cross outside swing phase"


def test_every_button_phase_survives_30fps_sampling() -> None:
    """No press/release phase shorter than 2 emulated frames (game polls at 30fps)."""
    frames = build_knife_frame_buttons()
    for key in ("r1", "down", "cross"):
        run_len = 0
        prev = None
        for btn in frames:
            cur = btn.get(key, False)
            if cur == prev or prev is None:
                run_len += 1
            else:
                assert run_len >= 2, f"{key} phase of {run_len} frame(s) invisible at 30fps"
                run_len = 1
            prev = cur
        assert run_len >= 2


def test_phase_override_changes_schedule() -> None:
    frames = build_knife_frame_buttons(aim=2, swing=3, recovery=4, scale=1)
    assert len(frames) == 9
    assert [f.get("cross", False) for f in frames] == [False] * 2 + [True] * 3 + [False] * 4


def test_knife_swing_clears_sticky_movement() -> None:
    s = StickyInputState()
    s.apply(ACTION_NAMES.index("run_forward"), ACTION_BUTTON_MAP)
    sticky, pulse, pulse_hold = s.apply(KNIFE_ACTION, ACTION_BUTTON_MAP)
    assert sticky == {k: False for k in sticky}
    assert pulse is None
    assert pulse_hold is None


def test_execute_knife_macro_uses_frame_schedule() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    schedule = build_knife_frame_buttons()
    died, frames = execute_knife_macro(bridge, empty_sticky=empty, use_ram_gates=False)
    assert not died
    assert frames == KNIFE_MACRO_FRAMES
    bridge.step.assert_called_once_with(
        n=KNIFE_MACRO_FRAMES,
        sticky=empty,
        frame_buttons=schedule,
        echo_joypad=False,
    )


def test_crouch_aim_ready_and_idle_hooks() -> None:
    assert is_crouch_knife_aim_ready(0x12, 0x04, 0)
    assert not is_crouch_knife_aim_ready(0x00, 0x00, 0)
    assert not is_crouch_knife_aim_ready(0x13, 0x04, 0)
    assert is_knife_animation_idle(0, 0, 0)
    assert not is_knife_animation_idle(0x13, 0x04, 0)
    assert is_knife_swing_recovery_tail(0x13, 0x04, 0)


def test_execute_knife_macro_ram_gated_steps_until_idle() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    # idle -> aim after 1 step -> swing -> recovery done
    hook_seq = [
        (0, 0, 0),
        (0, 0, 0),
        (0, 0, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x13, 0x04, 4),
        (0x13, 0x04, 4),
        (0x13, 0x04, 2),
        (0x13, 0x04, 0),
        (0x00, 0x00, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert bridge.step.call_count >= 5


def test_knife_macro_track_and_interrupt() -> None:
    assert is_knife_macro_track(0, 0, 0)
    assert is_knife_macro_track(0, 0, 2)
    assert is_idle_recovery_latch(0, 0, 2)
    assert is_standing_pre_knife_idle(0x0D, 0x01, 0)
    assert is_knife_macro_track(0x0D, 0x01, 0)
    assert is_knife_macro_track(0x12, 0x04, 0)
    assert is_knife_macro_track(0x13, 0x04, 8)
    assert not is_knife_macro_track(0x20, 0x00, 0)
    assert not is_knife_macro_interrupted(0, 0, 2, aim_achieved=False, swing_started=False)
    assert not is_knife_macro_interrupted(0x0D, 0x01, 2, aim_achieved=False, swing_started=False)
    assert is_knife_macro_interrupted(0x20, 0x00, 0, aim_achieved=False, swing_started=False)
    assert is_knife_macro_interrupted(0, 0, 0, aim_achieved=True, swing_started=True)


def test_ram_gated_waits_through_idle_recovery_latch() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0, 0, 2),
        (0, 0, 1),
        (0, 0, 0),
        (0, 0, 0),
        (0, 0, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    rep = bridge.last_knife_anim_report
    assert rep["outcome"] == "ok"
    assert rep["crouch_aim"]


def test_ram_gated_waits_through_standing_idle_hook() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0x0D, 0x01, 0),
        (0, 0, 0),
        (0, 0, 0),
        (0, 0, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert bridge.last_knife_anim_report["outcome"] == "ok"


def test_ram_gated_waits_through_standing_recovery_latch() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0x0D, 0x01, 2),
        (0x0D, 0x01, 1),
        (0x0D, 0x01, 0),
        (0, 0, 0),
        (0, 0, 0),
        (0, 0, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert bridge.last_knife_anim_report["outcome"] == "ok"


def test_knife_recovery_blocks_action() -> None:
    assert knife_action_blocked_by_recovery(0, 0, 2)
    assert knife_action_blocked_by_recovery(0x0D, 0x01, 2)
    assert knife_action_blocked_by_recovery(0x13, 0x04, 8)
    assert knife_action_blocked_by_recovery(0x15, 0x04, 0)
    assert not knife_action_blocked_by_recovery(0, 0, 0)
    assert not knife_action_blocked_by_recovery(0x0D, 0x01, 0)


def test_knife_settle_wait_state_covers_locomotion_and_latches() -> None:
    assert is_knife_settle_wait_state(0, 0, 0)
    assert is_knife_settle_wait_state(0, 0, 2)
    assert is_knife_settle_wait_state(0x0D, 0x01, 0)
    assert is_knife_settle_wait_state(0x06, 0x00, 0)
    assert is_knife_settle_wait_state(0x12, 0x04, 0)
    # Post-swing crouch hold must not abort settle (consecutive swings).
    assert is_knife_settle_wait_state(0x15, 0x04, 0)
    assert is_knife_settle_wait_state(0x13, 0x04, 0)
    assert is_knife_settle_wait_state(0x13, 0x04, 3)
    # Mid-swing entry drains under neutral.
    assert is_knife_settle_wait_state(0x14, 0x00, 0)
    assert is_knife_settle_wait_state(0x14, 0x04, 2)
    assert not is_knife_settle_wait_state(0x20, 0x00, 0)
    assert is_knife_foreign_anim(0x20, 0x00, 0)
    assert not is_knife_foreign_anim(0x14, 0x04, 0)
    assert not is_knife_foreign_anim(0x06, 0x00, 0)
    assert is_knife_mid_swing_state(0x14, 0x04, 0)
    assert is_knife_locomotion(0x06, 0x00)
    assert not is_knife_macro_interrupted(
        0x06, 0x00, 0, aim_achieved=False, swing_started=False, allow_locomotion=True
    )
    assert is_knife_macro_interrupted(
        0x06, 0x00, 0, aim_achieved=False, swing_started=False, allow_locomotion=False
    )


def test_ram_gated_settles_through_standing_knife_mid_swing() -> None:
    """Entry mid-swing (0x14) must drain under settle, then aim/swing — not abort."""
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0x14, 0x04, 4),  # mid-swing entry
        (0x14, 0x04, 2),
        (0x13, 0x04, 0),  # recovery tail
        (0, 0, 0),
        (0, 0, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    report = bridge.last_knife_anim_report
    assert report["outcome"] == "ok"
    assert report["crouch_aim"] is True


def test_ram_gated_aim_tolerates_brief_locomotion() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0, 0, 0),  # entry
        (0, 0, 0),  # settle
        (0, 0, 0),  # settle complete
        (0x06, 0x00, 0),  # walk residue during aim
        (0x06, 0x00, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert bridge.last_knife_anim_report["outcome"] == "ok"


def test_ram_gated_settles_through_crouch_post_then_swings() -> None:
    """Entry crouch_post used to abort settle; must drain to idle then aim/swing."""
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0x15, 0x04, 0),  # entry / settle wait
        (0x15, 0x04, 0),
        (0, 0, 0),  # drained idle
        (0, 0, 0),
        (0x12, 0x04, 0),  # aim
        (0x12, 0x04, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),  # recovery tail
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    report = bridge.last_knife_anim_report
    assert report["outcome"] == "ok"
    assert report["crouch_aim"] is True


def test_knife_settle_complete_includes_standing_weapon_idle() -> None:
    assert is_knife_settle_complete(0, 0, 0)
    assert is_knife_settle_complete(0x0D, 0x01, 0)
    assert not is_knife_settle_complete(0x0D, 0x01, 2)


def test_ram_gated_settles_on_standing_idle_without_neutral_idle() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0x0D, 0x01, 0),
        (0x0D, 0x01, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert bridge.last_knife_anim_report["outcome"] == "ok"


def test_ram_gated_skips_aim_when_crouch_aim_during_settle() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0x01, 0x00, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    rep = bridge.last_knife_anim_report
    assert rep["outcome"] == "ok"
    assert rep["crouch_aim"]


def test_ram_gated_holds_swing_through_transient_idle() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0, 0, 0),
        (0, 0, 0),
        (0, 0, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x12, 0x04, 2),
        (0, 0, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert bridge.last_knife_anim_report["outcome"] == "ok"


def test_knife_action_ready_whitelist() -> None:
    assert knife_action_ready(0, 0, 0)
    assert knife_action_ready(0x0D, 0x01, 0)
    assert not knife_action_ready(0x0D, 0x01, 2)
    assert not knife_action_ready(0x13, 0x04, 0)
    assert not knife_action_ready(0x06, 0x00, 0)
    assert not is_knife_macro_track(0x06, 0x00, 0)
    assert is_standing_recovery_latch(0x0D, 0x01, 2)
    assert is_standing_pre_knife_hook(0x0D, 0x01)


def test_ram_gated_skips_settle_when_entry_standing_idle_then_recovery_latch() -> None:
    """Repro agent-ram #1140: standing idle entry, latch after neutral release."""
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [(0x0D, 0x01, 2)] * 18 + [
        (0x12, 0x04, 0),
        (0x12, 0x04, 0),
        (0x13, 0x04, 2),
        (0x14, 0x04, 8),  # real slash
        (0x14, 0x04, 4),
        (0x13, 0x04, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert bridge.last_knife_anim_report["outcome"] == "ok"
    assert bridge.last_knife_anim_report["outcome"] != "settle_timeout"


def test_ram_gated_settle_aborts_after_max_wait_without_neutral_or_crouch_aim() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        return {"player_anim": 0, "player_action_aux": 0, "player_recovery_timer": 3}

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=1,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert frames <= KNIFE_SETTLE_MAX_WAIT_FRAMES + 2
    assert bridge.last_knife_anim_report["outcome"] == "settle_timeout"


def test_ram_gated_aborts_on_hit_animation_during_aim() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_reads = iter([(0x20, 0x00, 0), (0x20, 0x00, 0)])

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        a, x, r = next(hook_reads)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert bridge.step.call_count <= 3
    assert bridge.last_knife_anim_report["outcome"] == "aborted_interrupt"
    # Must release (neutral) — do not hold aim/swing through a bite.
    released = False
    for call in bridge.step.call_args_list:
        for frame in call.kwargs.get("frame_buttons") or []:
            if frame == {} or not any(frame.values()):
                released = True
            assert not frame.get("cross"), "must not swing through foreign/hurt anim"
    assert released


def test_ram_gated_does_not_wait_out_bite_during_settle() -> None:
    """Bite/grab mid-settle: release immediately; never drain the hurt anim."""
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    # One settle frame of locomotion, then foreign bite — abort, don't spin on 0x20.
    hook_reads = iter(
        [(0x06, 0x00, 0), (0x20, 0x00, 0)] + [(0x20, 0x00, 0)] * 80
    )

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        a, x, r = next(hook_reads)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert frames < 10
    assert bridge.last_knife_anim_report["outcome"] == "aborted_interrupt"
    assert KNIFE_SETTLE_MID_SWING_MAX_WAIT_FRAMES > KNIFE_SETTLE_MAX_WAIT_FRAMES


def test_ram_gated_aborts_when_crouch_aim_never_ready() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        return {"player_anim": 0, "player_action_aux": 0, "player_recovery_timer": 0}

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=1,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    for call in bridge.step.call_args_list:
        for frame in call.kwargs.get("frame_buttons") or []:
            assert not frame.get("cross"), "must not swing when crouch aim never ready"


def test_ram_gated_aborts_if_knocked_idle_mid_swing() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_reads = iter(
        [
            (0, 0, 0),
            (0, 0, 0),
            (0, 0, 0),
            (0x12, 0x04, 0),
            (0x12, 0x04, 0),
            (0x00, 0x00, 0),
        ]
    )

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_reads)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert bridge.last_knife_anim_report["outcome"] == "aborted_interrupt"


def test_ram_gated_aborts_when_recovery_never_finishes() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_reads = iter(
        [(0, 0, 0), (0, 0, 0), (0, 0, 0), (0x12, 0x04, 0), (0x12, 0x04, 0), (0x12, 0x04, 0)]
        + [(0x13, 0x04, 2)] * 50
    )

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hook_reads)
        except StopIteration:
            a, x, r = (0x13, 0x04, 2)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=1,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    # aim(2) + swing(2) + recovery cap(32) + release
    assert bridge.step.call_count <= 40


def test_ram_gated_death_aborts_animation_wait() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_reads = iter(
        [
            (0, 0, 0),
            (0, 0, 0),
            (0, 0, 0),
            (0x12, 0x04, 0),
            (0x12, 0x04, 0),
            (0x13, 0x04, 8),
            (0x13, 0x04, 4),
        ]
    )

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "player_hp" in names:
            return {"player_hp": 0}
        if "player_anim" in names:
            try:
                a, x, r = next(hook_reads)
            except StopIteration:
                a, x, r = (0x13, 0x04, 2)
            return {
                "player_anim": a,
                "player_action_aux": x,
                "player_recovery_timer": r,
            }
        raise AssertionError(fields)

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames = execute_knife_macro(
        bridge,
        empty_sticky=empty,
        phases=(1, 1, 1),
        scale=2,
        use_ram_gates=True,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert died


def test_expected_swing_recovery_emu_frames() -> None:
    assert expected_swing_recovery_emu_frames() == (10, 22)
    assert expected_swing_recovery_emu_frames(1, 1, 2) == (2, 2)


def test_anim_validator_swing_frame_count(monkeypatch, capsys) -> None:
    monkeypatch.setenv("KNIFE_ANIM_LOG", "1")
    import re1_rl.knife_macro as km

    monkeypatch.setattr(km, "KNIFE_ANIM_LOG_ENABLED", True)
    val = KnifeAnimValidator(
        bridge=type("B", (), {"port": 1})(),
        swing_game=5,
        recovery_game=11,
        scale=2,
    )
    val.observe(0x12, 0x04, 0)
    val.observe(0x12, 0x04, 0)
    val.set_phase("swing")
    val.finish(outcome="ok", died=False, frames=4)
    out = capsys.readouterr().out
    assert "swing anim too short" in out
    assert "expected ~10" in out


def test_anim_validator_recovery_frame_count(monkeypatch, capsys) -> None:
    monkeypatch.setenv("KNIFE_ANIM_LOG", "1")
    import re1_rl.knife_macro as km

    monkeypatch.setattr(km, "KNIFE_ANIM_LOG_ENABLED", True)
    val = KnifeAnimValidator(
        bridge=type("B", (), {"port": 2})(),
        swing_game=1,
        recovery_game=2,
        scale=2,
    )
    val.observe(0x12, 0x04, 0)
    val.observe(0x12, 0x04, 0)
    val.set_phase("swing")
    val.observe(0x13, 0x04, 4)
    val.observe(0x13, 0x04, 2)
    val.set_phase("recovery")
    val.observe(0x13, 0x04, 1)
    val.finish(outcome="ok", died=False, frames=8)
    out = capsys.readouterr().out
    assert "recovery anim too short" in out
    assert "expected ~4" in out


def test_classify_knife_anim_buckets() -> None:
    assert classify_knife_anim(0, 0, 0) == "idle"
    assert classify_knife_anim(0, 0, 2) == "idle_recovery_latch"
    assert classify_knife_anim(0x0D, 0x01, 2) == "standing_recovery_latch"
    assert classify_knife_anim(0x0D, 0x01, 0) == "standing_idle"
    assert classify_knife_anim(0x12, 0x04, 0) == "crouch_aim"
    assert classify_knife_anim(0x13, 0x04, 8) == "swing_recovery"
    assert classify_knife_anim(0x14, 0x00, 0) == "standing_knife"
    assert classify_knife_anim(0x12, 0x00, 0) == "crouch_transitional"
    assert classify_knife_anim(0x15, 0x04, 0) == "crouch_post"
    assert classify_knife_anim(0x20, 0x00, 0) == "foreign"


def test_anim_validator_logs_foreign_during_aim(monkeypatch, capsys) -> None:
    monkeypatch.setenv("KNIFE_ANIM_LOG", "1")
    import re1_rl.knife_macro as km

    monkeypatch.setattr(km, "KNIFE_ANIM_LOG_ENABLED", True)
    val = KnifeAnimValidator(bridge=type("B", (), {"port": 9999})())
    val.observe(0x20, 0x00, 0)
    out = capsys.readouterr().out
    assert "[knife_anim]" in out
    assert "foreign" in out
    assert "0x20" in out


def test_anim_validator_summary_on_failed_macro(monkeypatch, capsys) -> None:
    monkeypatch.setenv("KNIFE_ANIM_LOG", "1")
    import re1_rl.knife_macro as km

    monkeypatch.setattr(km, "KNIFE_ANIM_LOG_ENABLED", True)
    val = KnifeAnimValidator(bridge=type("B", (), {"port": 5555})())
    val.set_phase("swing")
    val.finish(outcome="aim_timeout", died=False, frames=12)
    out = capsys.readouterr().out
    assert "SUMMARY" in out
    assert "crouch_aim=0" in out


def test_execute_knife_macro_phase_override_and_echo() -> None:
    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    execute_knife_macro(
        bridge, empty_sticky=empty, phases=(2, 3, 4), echo_joypad=True, use_ram_gates=False
    )
    kwargs = bridge.step.call_args.kwargs
    assert kwargs["n"] == 9 * KNIFE_FRAME_SCALE
    assert kwargs["echo_joypad"] is True
    assert len(kwargs["frame_buttons"]) == 9 * KNIFE_FRAME_SCALE
