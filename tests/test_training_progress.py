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


def test_slim_progress_info_keeps_actor_rank() -> None:
    slim = slim_progress_info(
        {
            "room_id": "106",
            "actor_rank": 3,
            "episode": {"r": -1.0, "l": 50},
            "state": {"hp": 96},
        }
    )
    assert slim["actor_rank"] == 3
    assert "state" not in slim


def test_slim_progress_info_keeps_go_explore_capture() -> None:
    proposal = {"cell_key": "v2|r=105|x=0|z=0|m=d", "bundle_b64": "AAAA"}
    slim = slim_progress_info(
        {
            "room_id": "105",
            "state": {"hp": 96},
            "go_explore_capture": [proposal],
        }
    )
    assert "state" not in slim
    assert slim["go_explore_capture"] == [proposal]


def test_slim_progress_info_keeps_yawn_rails_eval_fields() -> None:
    slim = slim_progress_info(
        {
            "room_id": "106",
            "state": {"hp": 96},
            "episode": {"r": 12.0, "l": 80},
            "episode_outcome": "checkpoint_success",
            "rails_cell_id": "cp03",
            "rails_cell_index": 3,
            "route_start_index": 4,
            "leg_span": 1,
            "reset_source": "route_cell",
            "held_out_eval": True,
        }
    )
    assert slim["rails_cell_id"] == "cp03"
    assert slim["rails_cell_index"] == 3
    assert slim["episode_outcome"] == "checkpoint_success"
    assert slim["held_out_eval"] is True
    assert "state" not in slim


def test_slim_progress_info_transport_to_merge() -> None:
    from re1_rl.go_explore_merge import extract_proposals_from_infos

    proposal = {"cell_key": "v2|r=105|x=0|z=0|m=d", "record_id": "abc"}
    slim = slim_progress_info({"go_explore_capture": [proposal]})
    extracted = extract_proposals_from_infos([slim])
    assert extracted == [proposal]


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
                "actor_rank": 2,
                "episode": {"r": -2.0, "l": 80},
                "episode_failure": "hp_death",
                "reward_breakdown": {},
            }
        ],
        num_timesteps=500,
    )
    out = capsys.readouterr().out
    assert "[episode] machine=t" in out
    assert "worker=2" in out
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


def test_slim_progress_info_keeps_planner_divert_fields() -> None:
    slim = slim_progress_info(
        {
            "room_id": "107",
            "episode_failure": "planner_divert",
            "planner_divert_reason": "wrong_traverse:106->105 got 107",
            "failure_target": "105",
            "state": {"hp": 96},
        }
    )
    assert slim["episode_failure"] == "planner_divert"
    assert slim["planner_divert_reason"] == "wrong_traverse:106->105 got 107"
    assert slim["failure_target"] == "105"
    assert slim["room_id"] == "107"
    assert "state" not in slim


def test_tracker_logs_room_target_divert_on_planner_divert(capsys) -> None:
    tracker = TrainingProgressTracker(machine_name="t")
    tracker.consume_infos(
        [
            {
                "room_id": "107",
                "visited_rooms": ["106", "107"],
                "n_rooms_visited": 2,
                "bridge_port": 5555,
                "episode": {"r": -4.2, "l": 12},
                "episode_failure": "planner_divert",
                "failure_target": "105",
                "planner_divert_reason": "wrong_traverse:106->105 got 107",
                "reward_breakdown": {"planner_divert": -4.0},
            }
        ],
        num_timesteps=80,
    )
    out = capsys.readouterr().out
    assert "fail='planner_divert'" in out
    assert "room='107'" in out
    assert "target='105'" in out
    assert "divert='wrong_traverse:106->105 got 107'" in out


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
