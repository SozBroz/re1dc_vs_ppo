"""Training metrics JSONL logging + Baseline E fleet epoch records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION, make_re1_spaces
from re1_rl.env import ACTION_NAMES
from re1_rl.training_metrics_log import (
    append_training_record,
    build_fleet_epoch_record,
    build_update_record,
    emit_fleet_epoch_metrics,
    extract_logger_scalars,
    format_lag_hist,
    log_update_line,
    policy_version_lag_hist,
    training_metrics_jsonl_path,
    unique_contributor_machines,
)

N_ACTIONS = len(ACTION_NAMES)


def _fake_rollout(
    *,
    worker_id: str = "workhorse1",
    version: int = 1,
    n_steps: int = 4,
    n_envs: int = 2,
    curriculum_id: str = "curriculum/yawn_rails_one_leg.json",
) -> WorkerRollout:
    obs_space, _ = make_re1_spaces()
    obs = {
        key: np.zeros((n_steps, n_envs, *space.shape), dtype=space.dtype)
        for key, space in obs_space.spaces.items()
    }
    masks = np.ones((n_steps, n_envs, N_ACTIONS), dtype=np.bool_)
    return WorkerRollout(
        worker_id=worker_id,
        policy_version=version,
        n_envs=n_envs,
        n_steps=n_steps,
        obs=obs,
        actions=np.zeros((n_steps, n_envs), dtype=np.int64),
        rewards=np.zeros((n_steps, n_envs), dtype=np.float32),
        dones=np.zeros((n_steps, n_envs), dtype=np.bool_),
        values=np.zeros((n_steps, n_envs), dtype=np.float32),
        log_probs=np.zeros((n_steps, n_envs), dtype=np.float32),
        last_values=np.zeros(n_envs, dtype=np.float32),
        action_masks=masks,
        curriculum_id=curriculum_id,
        obs_schema_version=int(OBS_SCHEMA_VERSION),
    )


def test_append_and_build_record(tmp_path: Path) -> None:
    class FakeLogger:
        name_to_value = {
            "train/approx_kl": 0.012,
            "train/explained_variance": 0.45,
        }

    class FakeModel:
        num_timesteps = 10_000
        _logger = FakeLogger()

    path = training_metrics_jsonl_path(tmp_path, run_name="test")
    record = build_update_record(
        FakeModel(),  # type: ignore[arg-type]
        update=3,
        policy_version=3,
        rate_steps_s=123.4,
        extra={"batch/reward_mean": -0.001},
    )
    append_training_record(path, record)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["update"] == 3
    assert parsed["train/approx_kl"] == 0.012
    assert parsed["batch/reward_mean"] == -0.001


def test_extract_logger_scalars_empty() -> None:
    class M:
        _logger = None

    assert extract_logger_scalars(M()) == {}  # type: ignore[arg-type]


def test_build_fleet_epoch_record_fields() -> None:
    class FakeLogger:
        name_to_value = {
            "train/approx_kl": 0.02,
            "train/clip_fraction": 0.1,
            "train/entropy_loss": -0.5,
            "train/value_loss": 0.3,
            "train/loss": 0.4,
            "train/explained_variance": 0.6,
            "train/optimizer_steps": 12.0,
            "train/n_epochs_completed": 4.0,
            "train/effective_batch": 2048.0,
            "train/early_stop": 0.0,
        }

    class FakeModel:
        num_timesteps = 50_000
        _logger = FakeLogger()

    record = build_fleet_epoch_record(
        FakeModel(),  # type: ignore[arg-type]
        update=7,
        policy_version=8,
        accepted_steps=4096,
        contributors=["pking", "workhorse1"],
        curriculum_id="curriculum/yawn_rails_one_leg.json",
        collection_wall_s=360.0,
        train_wall_s=120.5,
        policy_version_lag_hist={"0": 3000, "1": 1096},
        policy_version_counts={"7": 3000, "6": 1096},
        relevance_keep_rate=0.9,
        relevance_step_keep_rate=0.85,
    )
    assert record["accepted_steps"] == 4096
    assert record["n_contributors"] == 2
    assert record["contributors"] == ["pking", "workhorse1"]
    assert record["curriculum_id"] == "curriculum/yawn_rails_one_leg.json"
    assert record["train/approx_kl"] == 0.02
    assert record["train/optimizer_steps"] == 12.0
    assert record["train/n_epochs_completed"] == 4.0
    assert record["train/effective_batch"] == 2048.0
    assert record["policy_version_lag_hist"] == {"0": 3000, "1": 1096}
    assert record["collection_wall_s"] == 360.0
    assert record["train_wall_s"] == 120.5
    assert record["relevance_step_keep_rate"] == 0.85


def test_zero_n_fleet_record_skips_stale_logger() -> None:
    class FakeLogger:
        name_to_value = {
            "train/approx_kl": 0.99,
            "train/optimizer_steps": 99.0,
        }

    class FakeModel:
        num_timesteps = 1000
        _logger = FakeLogger()

    record = build_fleet_epoch_record(
        FakeModel(),  # type: ignore[arg-type]
        update=2,
        policy_version=2,
        accepted_steps=0,
        contributors=[],
        collection_wall_s=10.0,
        train_wall_s=0.01,
    )
    assert record["accepted_steps"] == 0
    assert "train/approx_kl" not in record
    assert record["train/optimizer_steps"] == 0.0
    assert record["train/n_epochs_completed"] == 0.0
    assert record["train/effective_batch"] == 0.0


def test_emit_one_jsonl_and_one_line_per_epoch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = training_metrics_jsonl_path(tmp_path, run_name="fleet")
    class FakeModel:
        num_timesteps = 0
        _logger = None

    for update in (1, 2):
        record = build_fleet_epoch_record(
            FakeModel(),  # type: ignore[arg-type]
            update=update,
            policy_version=update,
            accepted_steps=0,
            contributors=["workhorse2"],
            collection_wall_s=1.0,
            train_wall_s=0.0,
        )
        emit_fleet_epoch_metrics(path, record)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["update"] == 1
    assert json.loads(lines[1])["update"] == 2
    out = capsys.readouterr().out
    assert out.count("[train:metrics]") == 2


def test_policy_version_lag_hist_and_contributors() -> None:
    rollouts = [
        _fake_rollout(worker_id="workhorse1:actor_0", version=5, n_steps=4, n_envs=2),
        _fake_rollout(worker_id="pking:actor_3", version=4, n_steps=4, n_envs=1),
        _fake_rollout(worker_id="workhorse1:actor_1", version=5, n_steps=2, n_envs=2),
    ]
    assert unique_contributor_machines(rollouts) == ["pking", "workhorse1"]
    hist = policy_version_lag_hist(rollouts, current_policy_version=5)
    # v5: 4*2 + 2*2 = 12 steps lag0; v4: 4*1 = 4 steps lag1
    assert hist == {"0": 12, "1": 4}
    assert format_lag_hist(hist) == "lag0=12,lag1=4"


def test_log_update_line_includes_fleet_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_update_line(
        {
            "update": 3,
            "num_timesteps": 100,
            "rate_steps_s": 10.0,
            "accepted_steps": 2048,
            "n_contributors": 2,
            "curriculum_id": "curriculum/yawn_rails_one_leg.json",
            "train/approx_kl": 0.01,
            "train/optimizer_steps": 8.0,
            "policy_version_lag_hist": {"0": 1000, "1": 1048},
            "collection_wall_s": 360.0,
            "train_wall_s": 90.0,
            "relevance_step_keep_rate": 0.95,
        }
    )
    line = capsys.readouterr().out.strip()
    assert line.startswith("[train:metrics]")
    assert "accepted=2048" in line
    assert "contrib=2" in line
    assert "ver_lag=lag0=1000,lag1=1048" in line
    assert "collect_s=360.0" in line
    assert "rel_step_keep=0.950" in line
