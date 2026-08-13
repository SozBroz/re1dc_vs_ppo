"""Checkpoint capture waits for the next decision frame (no mid-step stutter)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from re1_rl.env import RE1Env
from re1_rl.progress import ProgressTracker


def _term_env(*, captured: bool) -> SimpleNamespace:
    progress = ProgressTracker(leg_span=1)
    progress.checkpoint_success = True
    return SimpleNamespace(
        _stage={"mode": "yawn_rails", "max_steps": 3000},
        _progress=progress,
        _checkpoint_captured=captured,
        _episode_failure_override=None,
        _step_count=3,
        _episode_truncated=lambda: False,
    )


def test_checkpoint_success_does_not_terminate_until_captured() -> None:
    env = _term_env(captured=False)
    terminated, _truncated, reason = RE1Env._termination_flags(env, {"dead": False})
    assert terminated is False
    assert reason is None


def test_intermediate_capture_does_not_terminate_or_stick_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "re1_rl.yawn_rails.capture_successor_cell", lambda *_a, **_k: None
    )
    progress = ProgressTracker(leg_span=10)
    progress.checkpoint_success = False
    env = SimpleNamespace(
        _progress=progress,
        _checkpoint_freeze_pending=True,
        _checkpoint_captured=False,
        _macro_active=True,
        _apply_yawn_capture_ineligibility_penalty=lambda _bd: None,
    )
    RE1Env._finish_checkpoint_capture(
        env, {"room_id": "105"}, {"checkpoint_success": 12.0}
    )
    assert env._checkpoint_captured is False
    assert env._checkpoint_freeze_pending is False
    terminated, _trunc, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "yawn_rails", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=env._checkpoint_captured,
            _episode_failure_override=None,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is False
    assert reason is None


def test_last_leg_capture_terminates_after_decision_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "re1_rl.yawn_rails.capture_successor_cell", lambda *_a, **_k: None
    )
    progress = ProgressTracker(leg_span=1)
    progress.checkpoint_success = True
    env = SimpleNamespace(
        _progress=progress,
        _checkpoint_freeze_pending=True,
        _checkpoint_captured=False,
        _macro_active=True,
        _apply_yawn_capture_ineligibility_penalty=lambda _bd: None,
    )
    RE1Env._finish_checkpoint_capture(
        env, {"room_id": "210"}, {"checkpoint_success": 12.0}
    )
    assert env._checkpoint_captured is True
    terminated, _trunc, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "yawn_rails", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=True,
            _episode_failure_override=None,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "checkpoint_success"



def test_decision_capture_ignores_policy_action() -> None:
    finished = {"n": 0}
    bridge = MagicMock()
    env = SimpleNamespace(
        _checkpoint_freeze_pending=True,
        _checkpoint_captured=False,
        _macro_active=True,
        _skipping_flag=False,
        _prev_state={},
        bridge=bridge,
    )
    env._stop_bg_skip = lambda: None
    env._auto_accept_pause_pickup_modal = lambda: False
    env._dismiss_non_box_pause_menu_if_safe = lambda: False
    env._read_state = lambda: {
        "room_id": "108",
        "hp": 96,
        "in_control": True,
        "x": 1,
        "z": 2,
        "dead": False,
    }

    def _finish(state, _gate):
        finished["n"] += 1
        finished["state"] = state
        env._checkpoint_captured = True
        env._checkpoint_freeze_pending = False

    env._finish_checkpoint_capture = _finish
    env._checkpoint_freeze_obs = lambda action, state, gate: (
        {"ok": True},
        0.0,
        True,
        False,
        {"checkpoint_freeze": True, "ignored_action": action},
    )

    result = RE1Env._try_decision_checkpoint_capture(env, 5)
    assert finished["n"] == 1
    assert result is not None
    _obs, _rew, terminated, _trunc, info = result
    assert terminated is True
    assert info["checkpoint_freeze"] is True
    assert info["ignored_action"] == 5
    bridge.step.assert_not_called()
