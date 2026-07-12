"""Training progress tracker tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.training_progress import TrainingProgressTracker, slim_progress_info


def test_slim_progress_info_drops_state() -> None:
    slim = slim_progress_info(
        {
            "room_id": "106",
            "max_waypoint": 2,
            "state": {"hp": 96, "room_id": "106", "x": 1},
            "reward_breakdown": {"waypoint": 0.2},
            "visited_rooms": ["105", "106"],
            "n_rooms_visited": 2,
            "episode": {"r": -1.0, "l": 50},
            "episode_failure": "hp_death",
        }
    )
    assert slim["room_id"] == "106"
    assert slim["visited_rooms"] == ["105", "106"]
    assert slim["n_rooms_visited"] == 2
    assert slim["episode"]["r"] == -1.0
    assert slim["episode_failure"] == "hp_death"
    assert "state" not in slim


def test_slim_progress_info_keeps_pickups() -> None:
    slim = slim_progress_info(
        {
            "room_id": "10F",
            "new_items": ["emblem"],
            "ever_held": ["knife", "beretta", "emblem"],
            "state": {"noise": 1},
        }
    )
    assert slim["new_items"] == ["emblem"]
    assert slim["ever_held"] == ["knife", "beretta", "emblem"]
    assert "state" not in slim


def test_tracker_first_room_and_rollout_summary(capsys) -> None:
    tracker = TrainingProgressTracker(machine_name="t")
    tracker.consume_infos(
        [{"room_id": "105", "max_waypoint": 0, "reward_breakdown": {}}],
        num_timesteps=100,
    )
    tracker.consume_infos(
        [{"room_id": "106", "max_waypoint": 1, "reward_breakdown": {"new_room": 1.0}}],
        num_timesteps=200,
    )
    model = MagicMock()
    model.ep_info_buffer = [{"r": -0.5, "l": 120}]
    tracker.log_rollout_end(model, num_timesteps=5120)
    out = capsys.readouterr().out
    assert "[progress] first visit to room 105" in out
    assert "[progress] first visit to room 106" in out
    assert "new_room_hits=1" in out
    assert "machine=t" in out
    model.logger.record.assert_any_call("re1/rooms_seen", 2)


def test_tracker_episode_best_rooms(tmp_path: Path, capsys) -> None:
    best_path = tmp_path / "best_rooms_t.jsonl"
    tracker = TrainingProgressTracker(
        machine_name="t",
        best_log_path=best_path,
    )
    tracker.consume_infos(
        [
            {
                "room_id": "106",
                "max_waypoint": 1,
                "visited_rooms": ["105", "106"],
                "n_rooms_visited": 2,
                "bridge_port": 5555,
                "episode": {"r": -2.0, "l": 80},
                "episode_failure": "hp_death",
                "reward_breakdown": {},
            }
        ],
        num_timesteps=500,
    )
    out = capsys.readouterr().out
    assert "[episode] machine=t" in out
    assert "rooms=2" in out
    assert "ids=['105', '106']" in out
    assert "[PB-rooms] machine=t best episode rooms=2" in out
    assert tracker.best_episode_n_rooms == 2
    assert tracker.best_episode_room_ids == ["105", "106"]
    note = json.loads(best_path.read_text(encoding="utf-8").strip())
    assert note["n_rooms"] == 2
    assert note["room_ids"] == ["105", "106"]
    latest = best_path.with_name("best_rooms_t_latest.json")
    assert latest.is_file()


def test_tracker_logs_weapon_and_key_pickups(capsys) -> None:
    tracker = TrainingProgressTracker(machine_name="t")
    tracker.consume_infos(
        [
            {
                "room_id": "10F",
                "new_items": ["emblem"],
                "ever_held": ["knife", "beretta", "emblem"],
                "reward_breakdown": {},
            }
        ],
        num_timesteps=300,
    )
    tracker.consume_infos(
        [
            {
                "room_id": "10F",
                "visited_rooms": ["105", "10F"],
                "n_rooms_visited": 2,
                "ever_held": ["knife", "beretta", "emblem"],
                "episode": {"r": 1.0, "l": 40},
                "reward_breakdown": {},
            }
        ],
        num_timesteps=400,
    )
    out = capsys.readouterr().out
    assert "first pickup key=emblem" in out
    assert "keys=['emblem']" in out
    assert "weapons=['beretta', 'knife']" in out
    assert "emblem" in tracker.items_seen
