"""Training progress tracker tests."""

from __future__ import annotations

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
        }
    )
    assert slim["room_id"] == "106"
    assert "state" not in slim


def test_tracker_first_room_and_rollout_summary(capsys) -> None:
    tracker = TrainingProgressTracker()
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
    model.logger.record.assert_any_call("re1/rooms_seen", 2)
