"""Async cutscene skip must not block env.step() while skip burns."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

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
    env._skip_cache_obs = {"frame": np.zeros((63, 84, 4), dtype=np.uint8)}
    env._skip_cache_state = None
    env._skip_cache_truncated = False
    env._stage = {"max_steps": 0}
    env._step_count = 0
    from re1_rl.progress import ProgressTracker

    env._progress = ProgressTracker()
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
    env._pending_episode_failure = None
    env._pending_skip_room_crossings = []
    env._cutscene_skip_entry_prev = None
    env._last_skip_frames = 0
    env._skip_session_frames = 0
    env._skip_session_frames_accounted = 0
    env._skip_frames_charged = 0
    env._enemy_fields = []
    env.bridge = MagicMock()
    env.project_root = Path(__file__).resolve().parents[1]
    env._enemy_motion = MagicMock()
    env._enemy_motion.update.side_effect = lambda enemies, *a, **k: enemies
    env._player_motion = MagicMock()
    env._player_motion.update.return_value = (0.0, 0.0)
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
    from re1_rl.reward import STEP_PENALTY

    assert reward == pytest.approx(STEP_PENALTY)
    assert not terminated
    assert info["cutscene_skip"] is True
    assert info["skip_step_frames_billed"] == env.frame_skip
    assert "frame" in obs


def test_fast_cutscene_step_recomputes_live_horizon() -> None:
    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._stage = {"max_steps": 1}
    env._skip_cache_truncated = False
    env.bridge.read_ram.return_value = {"player_hp": 96}

    _, _, terminated, truncated, _ = env._fast_cutscene_step(0)

    assert env._step_count == 1
    assert not terminated
    assert truncated


def test_fast_cutscene_step_counts_physical_frames_and_ends_cell_timeout() -> None:
    from re1_rl.leg_replay import new_leg_replay_buffer
    from re1_rl.progress import ProgressTracker
    from re1_rl.reward import RAILS_CELL_TIMEOUT_PENALTY

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._stage = {"mode": "yawn_rails", "max_steps": 5400}
    env._progress = ProgressTracker()
    env._progress.arm_cell_timeout(600)
    env._skip_session_frames = 600
    env._leg_replay = new_leg_replay_buffer()
    env.bridge.read_ram.return_value = {"player_hp": 96}

    _, reward, terminated, truncated, info = env._fast_cutscene_step(0)

    assert env._progress.leg_emulated_frames == 600
    assert env._leg_replay.skip_leg_frames == 600
    assert terminated
    assert not truncated
    assert info["episode_failure"] == "checkpoint_timeout"
    assert info["reward_breakdown"]["checkpoint_timeout"] == RAILS_CELL_TIMEOUT_PENALTY
    assert reward < RAILS_CELL_TIMEOUT_PENALTY


def test_fast_cutscene_terminal_consumes_inflight_chunk_and_death_wins() -> None:
    from re1_rl.leg_replay import new_leg_replay_buffer
    from re1_rl.progress import ProgressTracker

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._stage = {"mode": "yawn_rails", "max_steps": 5400}
    env._progress = ProgressTracker()
    env._progress.arm_cell_timeout(600)
    env._skip_session_frames = 600
    env._leg_replay = new_leg_replay_buffer()
    env.bridge.read_ram.return_value = {"player_hp": 96}

    def _finish_inflight_chunk() -> None:
        env._skip_session_frames = 1200
        env._bg_death = True

    def _refresh_dead_cache() -> None:
        env._skip_cache_state = {**env._prev_state, "dead": True, "hp": 0}
        env._skip_cache_obs = {"frame": np.zeros((63, 84, 4), dtype=np.uint8)}

    env._stop_bg_skip = _finish_inflight_chunk
    env._refresh_skip_cache = _refresh_dead_cache

    _, _, terminated, truncated, info = env._fast_cutscene_step(0)

    assert env._skip_session_frames_accounted == 1200
    assert env._progress.leg_emulated_frames == 1200
    assert env._leg_replay.skip_leg_frames == 1200
    assert terminated
    assert not truncated
    assert info["episode_failure"] == "death"
    assert info["reward_breakdown"]["death"] < 0
    assert "checkpoint_timeout" not in info["reward_breakdown"]


def test_fast_cutscene_step_charges_min_decision_when_chunk_not_landed() -> None:
    from re1_rl.reward import STEP_PENALTY
    from re1_rl.leg_replay import new_leg_replay_buffer

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._skip_session_frames = 0
    env._leg_replay = new_leg_replay_buffer()
    env.bridge.read_ram.return_value = {"player_hp": 96}
    _, reward, _, _, info = env._fast_cutscene_step(9)
    assert reward == pytest.approx(STEP_PENALTY)
    assert info["skip_step_frames_billed"] == 8
    assert info["policy_frames"] == 0
    assert info["skip_frames"] == 0
    assert info["reward_only_frames"] == 8
    assert env._skip_frames_charged == 8
    assert env._leg_replay.policy_leg_frames == 0
    assert env._leg_replay.skip_leg_frames == 0
    assert env._leg_replay.reward_only_leg_frames == 8


def test_fast_cutscene_step_ticks_idle_clock_under_planner_loyal() -> None:
    from re1_rl.leg_replay import new_leg_replay_buffer
    from re1_rl.progress import ProgressTracker
    from re1_rl.reward import CONTEMPT_GRACE_FRAMES, contempt_penalty_delta

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._planner_loyal_queue = MagicMock()
    env._progress = ProgressTracker()
    env._progress.note_stagnation_step(made_progress=False, step_frames=CONTEMPT_GRACE_FRAMES)
    env._skip_session_frames = 600
    env._leg_replay = new_leg_replay_buffer()
    env.bridge.read_ram.return_value = {"player_hp": 96}

    _, reward, _, _, info = env._fast_cutscene_step(9)

    assert env._progress.stagnation_frames == CONTEMPT_GRACE_FRAMES + 600
    expected = contempt_penalty_delta(CONTEMPT_GRACE_FRAMES, CONTEMPT_GRACE_FRAMES + 600)
    assert expected < 0.0
    assert info["reward_breakdown"]["softlock"] == pytest.approx(expected)
    assert reward < info["reward_breakdown"]["step"]


def test_fast_cutscene_step_leaves_idle_clock_alone_off_planner_loyal() -> None:
    from re1_rl.leg_replay import new_leg_replay_buffer
    from re1_rl.progress import ProgressTracker

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._progress = ProgressTracker()
    env._skip_session_frames = 600
    env._leg_replay = new_leg_replay_buffer()
    env.bridge.read_ram.return_value = {"player_hp": 96}

    _, _, _, _, info = env._fast_cutscene_step(9)

    assert env._progress.stagnation_frames == 0
    assert "softlock" not in info["reward_breakdown"]


def test_fast_cutscene_step_charges_step_penalty_for_skip_frames() -> None:
    from re1_rl.reward import step_penalty_for_frames
    from re1_rl.leg_replay import new_leg_replay_buffer

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._skip_session_frames = 120
    env._leg_replay = new_leg_replay_buffer()
    env.bridge.read_ram.return_value = {"player_hp": 96}
    _, reward, _, _, info = env._fast_cutscene_step(9)
    expected = step_penalty_for_frames(120, ref_frames=env.frame_skip)
    assert reward == pytest.approx(expected)
    assert info["reward_breakdown"]["step"] == pytest.approx(expected)
    assert info["skip_step_frames_billed"] == 120
    assert info["policy_frames"] == 0
    assert info["skip_frames"] == 120
    assert info["reward_only_frames"] == 0
    assert env._skip_frames_charged == 120
    assert env._leg_replay.policy_leg_frames == 0
    assert env._leg_replay.skip_leg_frames == 120


def test_fast_cutscene_opening_hold_is_policy_not_skip() -> None:
    from re1_rl.leg_replay import new_leg_replay_buffer

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._skip_session_frames = 18
    env._leg_replay = new_leg_replay_buffer()
    env.bridge.read_ram.return_value = {"player_hp": 96}
    _, _, _, _, info = env._fast_cutscene_step(9, opening_policy_frames=18)
    assert info["policy_frames"] == 18
    assert info["skip_frames"] == 0
    assert info["reward_only_frames"] == 0
    assert env._leg_replay.policy_leg_frames == 18
    assert env._leg_replay.skip_leg_frames == 0


def test_fast_cutscene_opening_plus_skip_chunk_splits_channels() -> None:
    from re1_rl.leg_replay import new_leg_replay_buffer

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._skip_session_frames = 618
    env._leg_replay = new_leg_replay_buffer()
    env.bridge.read_ram.return_value = {"player_hp": 96}
    _, _, _, _, info = env._fast_cutscene_step(9, opening_policy_frames=18)
    assert info["policy_frames"] == 18
    assert info["skip_frames"] == 600
    assert info["reward_only_frames"] == 0
    assert env._leg_replay.policy_leg_frames == 18
    assert env._leg_replay.skip_leg_frames == 600


def test_async_skip_step_penalty_distributes_across_fast_steps() -> None:
    from re1_rl.reward import step_penalty_for_frames

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._skip_session_frames = 100
    env.bridge.read_ram.return_value = {"player_hp": 96}
    _, r1, _, _, info1 = env._fast_cutscene_step(9)
    env._skip_session_frames = 250
    _, r2, _, _, info2 = env._fast_cutscene_step(9)
    assert info1["skip_step_frames_billed"] == 100
    assert info2["skip_step_frames_billed"] == 150
    assert r1 == pytest.approx(step_penalty_for_frames(100, ref_frames=8))
    assert r2 == pytest.approx(step_penalty_for_frames(150, ref_frames=8))
    assert env._skip_frames_charged == 250


def test_bill_async_skip_step_penalty_flushes_remainder() -> None:
    from re1_rl.reward import step_penalty_for_frames

    env = _stub_env(async_cutscene_skip=True)
    env._skip_session_frames = 80
    env._skip_frames_charged = 50
    reward, bd, delta = env._bill_async_skip_step_penalty()
    assert delta == 30
    assert reward == pytest.approx(step_penalty_for_frames(30, ref_frames=8))
    assert bd["step"] == pytest.approx(step_penalty_for_frames(30, ref_frames=8))
    assert env._skip_frames_charged == 80
    reward2, bd2, delta2 = env._bill_async_skip_step_penalty()
    assert delta2 == 0
    assert reward2 == 0.0
    assert bd2 == {}
    min_reward, min_bd, min_delta = env._bill_async_skip_step_penalty(min_frames=8)
    assert min_delta == 8
    assert min_reward == pytest.approx(step_penalty_for_frames(8, ref_frames=8))
    assert min_bd["step"] == pytest.approx(step_penalty_for_frames(8, ref_frames=8))
    assert env._skip_frames_charged == 88


def test_fast_cutscene_step_terminates_on_zero_hp() -> None:
    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._skip_cache_state = {"dead": True, "hp": 0, "room_id": "105"}
    env._death_step = MagicMock(
        return_value=(
            {"frame": np.zeros((63, 84, 4), dtype=np.uint8)},
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


def test_skip_crossing_queues_kenneth_failure_for_fast_step() -> None:
    from re1_rl.cutscene_reward import ILLEGAL_MAIN_HALL_FAILURE_REASON
    from re1_rl.progress import ProgressTracker
    from tests.test_scaffolding import make_planner

    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._progress = ProgressTracker()
    env._progress.first_visit("105")
    env._planner = make_planner()
    env._stage = {"success_room": None}
    env._poll_typewriter_save = lambda *_a, **_k: False
    env._after_reward_step = lambda *_a, **_k: None
    env._merge_post_skip_breakdown = RE1Env._merge_post_skip_breakdown.__get__(
        env, RE1Env
    )
    env._credit_async_skip_room_crossing = RE1Env._credit_async_skip_room_crossing.__get__(
        env, RE1Env
    )
    env._queue_kenneth_gate_failure_if_needed = (
        RE1Env._queue_kenneth_gate_failure_if_needed.__get__(env, RE1Env)
    )
    env._pending_skip_room_crossings = [
        (
            {
                "room_id": "105",
                "hp": 96,
                "in_control": True,
                "inventory": [],
                "inventory_slots": [],
            },
            {
                "room_id": "106",
                "hp": 96,
                "in_control": True,
                "inventory": [],
                "inventory_slots": [],
            },
        )
    ]
    env._cutscene_skip_entry_prev = {"room_id": "105", "hp": 96}
    env.bridge.read_ram.return_value = {"player_hp": 96}
    env._episode_failure_step = MagicMock(
        return_value=(
            {"frame": np.zeros((63, 84, 4), dtype=np.uint8)},
            -0.05,
            True,
            False,
            {"episode_failure": ILLEGAL_MAIN_HALL_FAILURE_REASON},
        )
    )

    env._credit_async_skip_room_crossing()
    assert env._progress.kenneth_gate_breached
    assert env._pending_episode_failure == ILLEGAL_MAIN_HALL_FAILURE_REASON
    assert "106" not in env._progress.visited_rooms

    _, _, terminated, _, info = env._fast_cutscene_step(0)
    env._episode_failure_step.assert_called_once()
    assert terminated
    assert info["episode_failure"] == ILLEGAL_MAIN_HALL_FAILURE_REASON
    assert not env._skipping_flag


def test_fast_cutscene_step_polls_hp_when_cache_stale() -> None:
    env = _stub_env(async_cutscene_skip=True)
    env._skipping_flag = True
    env._skip_cache_state = None
    env.bridge.read_ram.return_value = {"player_hp": 0}
    env._read_state = MagicMock(return_value={"hp": 0, "room_id": "105"})
    env._revive_zero_hp_under_yawn_floor = MagicMock(side_effect=lambda hp: hp)
    env._death_step = MagicMock(
        return_value=(
            {"frame": np.zeros((63, 84, 4), dtype=np.uint8)},
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
    from re1_rl.leg_replay import new_leg_replay_buffer
    from re1_rl.progress import ProgressTracker
    from re1_rl.reward import NEW_CUTSCENE_BONUS

    env = _stub_env(async_cutscene_skip=True)
    env._last_skip_frames = 60
    env._skip_session_frames = 450
    env._progress = ProgressTracker()
    env._leg_replay = new_leg_replay_buffer()
    env._cutscene_skip_entry_prev = None
    env._pending_skip_room_crossings = []
    env._ram_skip.last_skip_peak_scene_flag = 0x84
    env._ram_skip.last_skip_peak_msg_flag = 0x00
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
    env._record_leg_replay_step(0, policy_frames=8)
    assert env._leg_replay.policy_leg_frames == 8
    assert env._leg_replay.skip_leg_frames == 450


def test_post_skip_sync_message_text_no_cutscene() -> None:
    from re1_rl.progress import ProgressTracker

    env = _stub_env(async_cutscene_skip=True)
    env._skip_session_frames = 450
    env._progress = ProgressTracker()
    env._pending_skip_room_crossings = []
    env._ram_skip.last_skip_peak_scene_flag = 0x80
    env._ram_skip.last_skip_peak_msg_flag = MESSAGE_FLAG_MASK
    env._prev_state = {
        "room_id": "105",
        "hp": 96,
        "cam_id": 1,
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
            "room_id": "105",
            "cam_id": 1,
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
    assert env._post_skip_bd.get("new_cutscene", 0.0) == 0.0


def test_post_skip_door_crossing_pays_new_room_not_cutscene() -> None:
    from re1_rl.progress import ProgressTracker
    from re1_rl.reward import NEW_ROOM_BONUS

    env = _stub_env(async_cutscene_skip=True)
    env._last_skip_frames = 80
    env._skip_session_frames = 80
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


def test_room_cross_preserves_full_skip_session_duration() -> None:
    env = _stub_env(async_cutscene_skip=True)
    env._last_skip_frames = 120
    env._skip_session_frames = 220
    env._cutscene_skip_entry_prev = {
        "room_id": "105",
        "cam_id": 2,
        "hp": 96,
    }
    env._pending_skip_room_crossings = []
    env._read_state = MagicMock(
        return_value={
            "room_id": "104",
            "cam_id": 0,
            "hp": 96,
        }
    )

    env._note_async_skip_room_crossing()

    assert env._last_skip_frames == 0
    assert env._skip_session_frames == 220
    assert len(env._pending_skip_room_crossings) == 1


def test_post_cross_settle_qualifies_with_full_session_not_last_segment() -> None:
    from re1_rl.progress import ProgressTracker
    from re1_rl.reward import NEW_CUTSCENE_BONUS

    env = _stub_env(async_cutscene_skip=True)
    env._last_skip_frames = 20
    env._skip_session_frames = 450
    env._progress = ProgressTracker()
    env._progress.first_visit("105")
    env._progress.first_visit("104")
    env._pending_skip_room_crossings = []
    # A room crossing already restarted the segment entry at the destination.
    env._cutscene_skip_entry_prev = {
        "room_id": "104",
        "cam_id": 0,
        "hp": 96,
        "inventory": [],
        "stage_id": 0,
        "character_id": 1,
        "game_mode": 0x80,
        "game_state": 0x80800004,
        "scene_flag": 0x80,
        "msg_flag": 0,
    }
    env._prev_state = dict(env._cutscene_skip_entry_prev)
    env._read_state = MagicMock(
        return_value={
            **env._cutscene_skip_entry_prev,
            "x": 0,
            "y": 0,
            "z": 0,
            "facing": 0,
            "in_control": True,
            "dead": False,
            "inventory_slots": [],
            "new_items": [],
            "enemies": [],
            "interaction_prompt": False,
        }
    )

    env._apply_post_skip_sync()

    assert env._post_skip_bd.get("new_cutscene") == NEW_CUTSCENE_BONUS
    assert env._last_settled_skip_frames == 450


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
    env._push_frame = MagicMock(return_value=np.zeros((63, 84, 4), dtype=np.uint8))
    env._build_obs = MagicMock(return_value={"frame": np.zeros((63, 84, 4), dtype=np.uint8)})
    from re1_rl.progress import ProgressTracker

    env._progress = ProgressTracker()
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
    env._push_frame = MagicMock(return_value=np.zeros((63, 84, 4), dtype=np.uint8))
    env._build_obs = MagicMock(return_value={"frame": np.zeros((63, 84, 4), dtype=np.uint8)})
    from re1_rl.progress import ProgressTracker

    env._progress = ProgressTracker()
    env._planner.next_waypoint_room.return_value = "106"
    env._planner.waypoint_index = 0
    env._stage = {"max_steps": 0, "success_room": None}
    with patch("re1_rl.env.compute_reward", return_value=(0.0, {})):
        env.step(1)
    env._skip_uncontrolled.assert_not_called()
