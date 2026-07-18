"""Async cutscene skip must not block env.step() while skip burns."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.env import RE1Env
from re1_rl.memory_map import IN_CONTROL_MASK, MESSAGE_FLAG_MASK
from re1_rl.obs_encoder import PROPRIO_DIM


def _stub_env(async_cutscene_skip: bool) -> RE1Env:
    env = RE1Env.__new__(RE1Env)
    env._async_cutscene_skip = async_cutscene_skip
    env._bg_skip_stop = __import__("threading").Event()
    env._bg_skip_thread = None
    env._skipping_flag = False
    env._bg_death = False
    env._skip_cache_obs = {"frame": np.zeros((84, 77, 4), dtype=np.uint8)}
    env._skip_cache_state = None
    env._skip_cache_truncated = False
    env._stage = {"max_steps": 0}
    env._step_count = 0
    env._prev_state = {"room_id": "105", "hp": 96, "x": 0, "z": 0, "facing": 0}
    env._prev_hp = 96
    env._planner = MagicMock()
    env._encoder = MagicMock()
    env._encoder.encode_proprio.return_value = np.zeros(PROPRIO_DIM, dtype=np.float32)
    env._encoder.encode_goal.return_value = np.zeros(24, dtype=np.float32)
    env._spatial = MagicMock()
    env._spatial.encode.return_value = np.zeros(64, dtype=np.float32)
    env._visited = MagicMock()
    env._visited.plane.return_value = np.zeros((16, 16), dtype=np.float32)
    env.graph = MagicMock()
    env.room_items = MagicMock()
    env.room_items.loaded = False
    env._items = MagicMock()
    env._items.progress.return_value = (0, 0)
    env._items.next_needed.return_value = None
    env._episode_start_hp = 96
    env._episode_min_hp = 96
    env._post_skip_sync = False
    env._post_skip_reward = 0.0
    env._post_skip_bd = {}
    env._last_skip_frames = 0
    env._enemy_fields = []
    env.bridge = MagicMock()
    env.frame_skip = 8
    env._ram_skip = MagicMock()
    env._sticky_input = MagicMock()
    env._sticky_input.apply.return_value = ({}, {}, None)
    from gymnasium import spaces

    env.action_space = spaces.Discrete(len(__import__("re1_rl.env", fromlist=["ACTION_NAMES"]).ACTION_NAMES))
    env._prev_action = 0
    return env


def test_action_masks_noop_only_during_skip() -> None:
    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    # Stale prev_state still claims in_control — must not leak combat masks.
    env._prev_state = {
        "room_id": "105",
        "hp": 96,
        "x": 0,
        "z": 0,
        "facing": 0,
        "in_control": True,
    }
    masks = env.action_masks()
    assert masks.dtype == bool
    assert int(masks.sum()) == 1
    assert masks[0]
    assert not masks[1:].any()


def test_fast_cutscene_step_returns_immediately() -> None:
    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env.bridge.read_ram.return_value = {"player_hp": 96}
    t0 = time.perf_counter()
    obs, reward, terminated, truncated, info = env._fast_cutscene_step(0)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05
    assert reward == 0.0
    assert not terminated
    assert info["cutscene_skip"] is True
    assert "frame" in obs


def test_fast_cutscene_step_terminates_on_zero_hp() -> None:
    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._skip_cache_state = {"dead": True, "hp": 0, "room_id": "105"}
    env._death_step = MagicMock(
        return_value=(
            {"frame": np.zeros((84, 77, 4), dtype=np.uint8)},
            0.0,
            True,
            False,
            {"died_during_skip": True},
        )
    )
    obs, reward, terminated, truncated, info = env._fast_cutscene_step(0)
    env._death_step.assert_called_once()
    assert terminated
    assert reward == 0.0
    assert not env._skipping_flag


def test_fast_cutscene_step_polls_hp_when_cache_stale() -> None:
    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._skip_cache_state = None
    env.bridge.read_ram.return_value = {"player_hp": 0}
    env._death_step = MagicMock(
        return_value=(
            {"frame": np.zeros((84, 77, 4), dtype=np.uint8)},
            0.0,
            True,
            False,
            {"died_during_skip": True},
        )
    )
    _, _, terminated, _, _ = env._fast_cutscene_step(0)
    env._death_step.assert_called_once()
    assert terminated


def test_post_skip_sync_pays_cutscene_bonus_when_frames_recorded() -> None:
    from re1_rl.progress import ProgressTracker
    from re1_rl.reward import NEW_CUTSCENE_BONUS

    env = _stub_env(async_cutscene_skip=True)
    env._last_skip_frames = 60
    env._progress = ProgressTracker()
    env._cutscene_skip_entry_prev = None
    env._pending_skip_room_crossings = []
    env._prev_state = {
        "room_id": "104",
        "hp": 96,
        "cam_id": 0,
        "inventory": [],
        "stage_id": 0,
        "character_id": 1,
        "game_mode": 0x80,
        "game_state": 0x80800004,
        "scene_flag": 0x84,
        "msg_flag": 0,
    }
    env._cutscene_skip_entry_prev = dict(env._prev_state)
    env._read_state = MagicMock(
        return_value={
            "hp": 96,
            "room_id": "104",
            "cam_id": 0,
            "x": 0,
            "y": 0,
            "z": 0,
            "facing": 0,
            "in_control": True,
            "dead": False,
            "inventory": [],
            "inventory_slots": [],
            "new_items": [],
            "enemies": [],
            "interaction_prompt": False,
            "game_mode": 0x80,
            "game_state": 0x80800004,
            "scene_flag": 0x80,
            "msg_flag": 0,
            "stage_id": 0,
            "character_id": 1,
        }
    )
    env._apply_post_skip_sync()
    assert env._post_skip_bd.get("new_cutscene") == NEW_CUTSCENE_BONUS


def test_post_skip_door_crossing_pays_new_room_not_cutscene() -> None:
    from re1_rl.progress import ProgressTracker
    from re1_rl.reward import NEW_CUTSCENE_BONUS, NEW_ROOM_BONUS

    env = _stub_env(async_cutscene_skip=True)
    env._last_skip_frames = 80
    env._progress = ProgressTracker()
    env._progress.first_visit("105")
    env._pending_skip_room_crossings = []
    env._prev_state = {
        "room_id": "105",
        "hp": 96,
        "cam_id": 2,
        "inventory": [],
        "stage_id": 0,
        "character_id": 1,
        "game_mode": 0x80,
        "game_state": 0x80800004,
        "scene_flag": 0x80,
        "msg_flag": 0,
    }
    env._cutscene_skip_entry_prev = dict(env._prev_state)
    env._read_state = MagicMock(
        return_value={
            "hp": 96,
            "room_id": "104",
            "cam_id": 0,
            "x": 0,
            "y": 0,
            "z": 0,
            "facing": 0,
            "in_control": True,
            "dead": False,
            "inventory": [],
            "inventory_slots": [],
            "new_items": [],
            "enemies": [],
            "interaction_prompt": False,
            "game_mode": 0x80,
            "game_state": 0x80800004,
            "scene_flag": 0x80,
            "msg_flag": 0,
            "stage_id": 0,
            "character_id": 1,
        }
    )
    env._apply_post_skip_sync()
    assert env._post_skip_bd.get("new_room") == NEW_ROOM_BONUS
    assert env._post_skip_bd.get("new_cutscene", 0.0) == 0.0
    assert env._post_skip_bd.get("new_cutscene", 0.0) != NEW_CUTSCENE_BONUS


def test_sync_mode_still_calls_skip_uncontrolled(monkeypatch) -> None:
    env = _stub_env(async_cutscene_skip=False)
    env._start_bg_skip = MagicMock()
    env._skip_uncontrolled = MagicMock(return_value=(120, False))
    env.bridge.step.return_value = (1, False)
    env.bridge.screenshot.return_value = np.zeros((240, 320, 3), dtype=np.uint8)
    env._read_state = MagicMock(
        return_value={
            "hp": 96,
            "room_id": "105",
            "x": 0,
            "y": 0,
            "z": 0,
            "facing": 0,
            "in_control": True,
            "dead": False,
            "inventory": [],
            "inventory_slots": [],
            "new_items": [],
            "enemies": [],
            "interaction_prompt": False,
        }
    )
    env._push_frame = MagicMock(return_value=np.zeros((84, 77, 4), dtype=np.uint8))
    env._build_obs = MagicMock(return_value={"frame": np.zeros((84, 77, 4), dtype=np.uint8)})
    env._progress = MagicMock()
    env._planner.next_waypoint_room.return_value = "106"
    env._planner.waypoint_index = 0
    env._stage = {"max_steps": 0, "success_room": None}
    with patch("re1_rl.env.compute_reward", return_value=(0.0, {})):
        env.step(1)
    env._skip_uncontrolled.assert_called_once()


def test_refresh_cache_does_not_consume_new_items() -> None:
    from re1_rl.item_todo import ItemTracker

    env = _stub_env(async_cutscene_skip=True)
    env._items = ItemTracker(todo=[])
    env._items.ever_held = set()
    env.bridge.read_ram.return_value = {
        "stage_id": 0,
        "room_id": 5,
        "player_hp": 96,
        "player_x": 0,
        "player_y": 0,
        "player_z": 0,
        "player_facing": 0,
        "cam_id": 0,
        "character_id": 1,
        "game_mode": 0xC2,
    }
    with patch("re1_rl.env.decode_inventory", return_value=[("emblem", 1)]):
        state = env._read_state(track_items=False)
    assert state["new_items"] == ["emblem"]
    assert env._items.ever_held == set()
    with patch("re1_rl.env.decode_inventory", return_value=[("emblem", 1)]):
        state2 = env._read_state(track_items=True)
    assert state2["new_items"] == ["emblem"]
    assert "emblem" in env._items.ever_held

    env = _stub_env(async_cutscene_skip=True)
    env._start_bg_skip = MagicMock()
    env._probe_needs_skip = MagicMock(return_value=False)
    env._skip_uncontrolled = MagicMock()
    env.bridge.step.return_value = (1, False)
    env.bridge.screenshot.return_value = np.zeros((240, 320, 3), dtype=np.uint8)
    env._read_state = MagicMock(
        return_value={
            "hp": 96,
            "room_id": "105",
            "x": 0,
            "y": 0,
            "z": 0,
            "facing": 0,
            "in_control": True,
            "dead": False,
            "inventory": [],
            "inventory_slots": [],
            "new_items": [],
            "enemies": [],
            "interaction_prompt": False,
        }
    )
    env._push_frame = MagicMock(return_value=np.zeros((84, 77, 4), dtype=np.uint8))
    env._build_obs = MagicMock(return_value={"frame": np.zeros((84, 77, 4), dtype=np.uint8)})
    env._progress = MagicMock()
    env._planner.next_waypoint_room.return_value = "106"
    env._planner.waypoint_index = 0
    env._stage = {"max_steps": 0, "success_room": None}
    with patch("re1_rl.env.compute_reward", return_value=(0.0, {})):
        env.step(1)
    env._skip_uncontrolled.assert_not_called()
