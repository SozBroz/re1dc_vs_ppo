"""Training metrics JSONL logging."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.training_metrics_log import (
    append_training_record,
    build_update_record,
    extract_logger_scalars,
    training_metrics_jsonl_path,
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
