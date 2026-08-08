"""OPTIONS trap must dismiss / soft-continue — never hard-reset the episode."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_recover_options_menu_soft_continues_when_still_trapped() -> None:
    from re1_rl.env import RE1Env

    env = object.__new__(RE1Env)
    env._prev_hp = 80
    env._episode_start_hp = 80
    env._skipping_flag = True
    env._sticky_input = MagicMock()
    env._step_count = 10
    env._prev_state = {"room_id": "204", "hp": 80}
    env._progress = MagicMock()
    env._progress.visited_rooms = {"204"}
    env.bridge = MagicMock()
    env.bridge.port = 5759
    env.bridge.build_frame_stack.return_value = np.zeros((4, 84, 112), dtype=np.uint8)

    with (
        patch.object(env, "_try_dismiss_options_menu", return_value=(False, {"cleared": False})),
        patch.object(env, "_probe_outside_gameplay", return_value="options_menu"),
        patch.object(
            env,
            "_capture_step_obs",
            return_value=np.zeros((4, 84, 112), dtype=np.uint8),
        ),
        patch.object(
            env,
            "_read_state",
            return_value={"room_id": "204", "hp": 80},
        ),
        patch.object(
            env,
            "_build_obs",
            return_value={"frame": np.zeros((4, 84, 112), dtype=np.uint8)},
        ),
    ):
        obs, reward, terminated, truncated, info = env._recover_options_menu(7)

    assert terminated is False
    assert truncated is False
    assert reward == 0.0
    assert info.get("options_dismiss_persist") is True
    assert info.get("episode_failure") is None
    assert env._step_count == 11


def test_recover_options_menu_returns_none_when_cleared() -> None:
    from re1_rl.env import RE1Env

    env = object.__new__(RE1Env)
    env._prev_hp = 80
    env._episode_start_hp = 80
    env._sticky_input = MagicMock()

    with (
        patch.object(env, "_try_dismiss_options_menu", return_value=(True, {"cleared": True})),
        patch.object(env, "_probe_outside_gameplay", return_value=None),
    ):
        assert env._recover_options_menu(0) is None
