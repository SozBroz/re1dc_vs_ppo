"""Tests for inference temperature and memlog experiment metadata."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.inference_config import (
    grouped_entropy_training_from_env,
    inference_temperature_from_env,
    memlog_experiment_metadata,
)


def test_inference_temperature_from_env(monkeypatch) -> None:
    monkeypatch.delenv("RE1_INFERENCE_TEMPERATURE", raising=False)
    assert inference_temperature_from_env() == 1.0
    monkeypatch.setenv("RE1_INFERENCE_TEMPERATURE", "0.8")
    assert inference_temperature_from_env() == 0.8
    monkeypatch.setenv("RE1_INFERENCE_TEMPERATURE", "bad")
    assert inference_temperature_from_env() == 1.0


def test_memlog_experiment_metadata(monkeypatch) -> None:
    monkeypatch.setenv("RE1_MEMLOG_EXPERIMENT", "fight_eval_t08")
    monkeypatch.setenv("RE1_INFERENCE_TEMPERATURE", "0.8")
    monkeypatch.setenv("RE1_EVAL_ONLY", "1")
    monkeypatch.delenv("RE1_USE_GROUPED_ENTROPY", raising=False)
    meta = memlog_experiment_metadata()
    assert meta["experiment_id"] == "fight_eval_t08"
    assert meta["inference_temperature"] == 0.8
    assert meta["eval_only"] is True
    assert meta["policy_train_variant"] == "baseline"

    monkeypatch.setenv("RE1_USE_GROUPED_ENTROPY", "1")
    assert grouped_entropy_training_from_env() is True
    assert memlog_experiment_metadata()["policy_train_variant"] == "grouped_entropy"
