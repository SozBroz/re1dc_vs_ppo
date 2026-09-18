"""Finished-episode-only training gate (hop-score §5.3)."""

from __future__ import annotations

import os
import queue
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.learner_server import LearnerState
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.planner_hop_score import (
    rollout_ends_finished,
    train_finished_episodes_only,
)


def test_rollout_ends_finished_1d_and_2d() -> None:
    assert rollout_ends_finished(np.array([False, False, True]))
    assert not rollout_ends_finished(np.array([False, False, False]))
    assert rollout_ends_finished(np.array([[False], [True]]))
    assert not rollout_ends_finished(np.array([[False], [False]]))
    assert not rollout_ends_finished(np.array([], dtype=bool))


def test_train_finished_episodes_only_defaults_to_hop_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RE1_TRAIN_FINISHED_EPISODES_ONLY", raising=False)
    monkeypatch.setenv("RE1_PLANNER_HOP_SCORE_V1", "live")
    assert train_finished_episodes_only() is True
    monkeypatch.setenv("RE1_PLANNER_HOP_SCORE_V1", "0")
    assert train_finished_episodes_only() is False
    monkeypatch.setenv("RE1_TRAIN_FINISHED_EPISODES_ONLY", "1")
    assert train_finished_episodes_only() is True
    monkeypatch.setenv("RE1_PLANNER_HOP_SCORE_V1", "live")
    monkeypatch.setenv("RE1_TRAIN_FINISHED_EPISODES_ONLY", "0")
    assert train_finished_episodes_only() is False


def _rollout(*, finished: bool) -> WorkerRollout:
    dones = np.zeros((4, 1), dtype=np.bool_)
    if finished:
        dones[-1, 0] = True
    return WorkerRollout(
        worker_id="pking",
        policy_version=1,
        n_envs=1,
        n_steps=4,
        obs={"x": np.zeros((4, 1), dtype=np.float32)},
        actions=np.zeros((4, 1), dtype=np.int64),
        rewards=np.zeros((4, 1), dtype=np.float32),
        dones=dones,
        values=np.zeros((4, 1), dtype=np.float32),
        log_probs=np.zeros((4, 1), dtype=np.float32),
        last_values=np.zeros((1,), dtype=np.float32),
        action_masks=np.ones((4, 1, 8), dtype=np.bool_),
    )


def test_learner_rejects_unfinished_when_finished_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_TRAIN_FINISHED_EPISODES_ONLY", "1")
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(store, q, machine_name="t", max_staleness=2, worker_liveness_s=60)
    state.set_current_version(1)
    state.register_worker("pking", n_envs=1)
    state.begin_epoch()

    ok, reason = state.accept_rollout(_rollout(finished=False))
    assert ok is False
    assert reason == "unfinished_episode"

    ok2, reason2 = state.accept_rollout(_rollout(finished=True))
    assert ok2 is True
    assert reason2 == "ok"
