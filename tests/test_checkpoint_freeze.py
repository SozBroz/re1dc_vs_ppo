"""Checkpoint capture waits for the next decision frame (no mid-step stutter)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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


def test_checkpoint_success_terminates_after_decision_capture() -> None:
    env = _term_env(captured=True)
    terminated, _truncated, reason = RE1Env._termination_flags(env, {"dead": False})
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
