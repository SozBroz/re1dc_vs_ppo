"""Learner fleet tracking / epoch barrier (no BizHawk)."""

from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.learner_server import LearnerState, base_worker_id
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.weight_store import WeightStore
import numpy as np


def _rollout(worker_id: str, version: int = 1) -> WorkerRollout:
    return WorkerRollout(
        worker_id=worker_id,
        policy_version=version,
        n_envs=1,
        n_steps=4,
        obs={"x": np.zeros((4, 1), dtype=np.float32)},
        actions=np.zeros((4, 1), dtype=np.int64),
        rewards=np.zeros((4, 1), dtype=np.float32),
        dones=np.zeros((4, 1), dtype=np.bool_),
        values=np.zeros((4, 1), dtype=np.float32),
        log_probs=np.zeros((4, 1), dtype=np.float32),
        last_values=np.zeros((1,), dtype=np.float32),
        action_masks=np.ones((4, 1, 8), dtype=np.bool_),
    )


def test_base_worker_id_strips_actor_suffix() -> None:
    assert base_worker_id("pking:actor_3") == "pking"
    assert base_worker_id("workhorse1") == "workhorse1"


def test_epoch_waits_for_all_live_then_ready() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(store, q, machine_name="t", max_staleness=2, worker_liveness_s=60)
    state.set_current_version(1)
    state.register_worker("workhorse2", n_envs=8, is_local=True)
    state.register_worker("pking", n_envs=12)
    state.register_worker("workhorse1", n_envs=8)

    eid, expected = state.begin_epoch()
    assert eid == 1
    assert set(expected) == {"workhorse2", "pking", "workhorse1"}
    st = state.epoch_status()
    assert st["ready"] is False
    assert set(st["missing"]) == {"workhorse2", "pking", "workhorse1"}

    assert state.accept_rollout(_rollout("pking:actor_0"))[0]
    assert state.accept_rollout(_rollout("workhorse2"))[0]
    st = state.epoch_status()
    assert st["ready"] is False
    assert st["missing"] == ["workhorse1"]

    assert state.accept_rollout(_rollout("workhorse1:actor_1"))[0]
    st = state.epoch_status()
    assert st["ready"] is True
    assert st["missing"] == []


def test_dead_remote_dropped_from_expected() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(store, q, machine_name="t", max_staleness=2, worker_liveness_s=0.05)
    state.set_current_version(1)
    state.register_worker("workhorse2", n_envs=8, is_local=True)
    state.register_worker("pking", n_envs=12)
    state.begin_epoch()
    state.accept_rollout(_rollout("workhorse2"))
    time.sleep(0.08)
    st = state.epoch_status()
    # pking heartbeat aged out; local remains
    assert "pking" not in st["expected"]
    assert st["ready"] is True
    assert "workhorse2" in st["contributors"]


def test_pking_can_rejoin_next_epoch() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(store, q, machine_name="t", max_staleness=2, worker_liveness_s=60)
    state.set_current_version(1)
    state.register_worker("workhorse2", n_envs=8, is_local=True)
    state.begin_epoch()
    state.accept_rollout(_rollout("workhorse2"))
    assert state.epoch_status()["ready"] is True

    state.register_worker("pking", n_envs=12)
    # Still mid-epoch: pking not in expected until begin_epoch
    assert "pking" not in state.epoch_status()["expected"]

    eid, expected = state.begin_epoch()
    assert eid == 2
    assert "pking" in expected
    assert state.epoch_status()["ready"] is False


def test_multiple_posts_same_worker_accepted() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(store, q, machine_name="t", max_staleness=2, worker_liveness_s=60)
    state.set_current_version(1)
    state.register_worker("pking", n_envs=20)
    state.begin_epoch()

    def _partial(n_envs: int) -> WorkerRollout:
        return WorkerRollout(
            worker_id="pking",
            policy_version=1,
            n_envs=n_envs,
            n_steps=4,
            obs={"x": np.zeros((4, n_envs), dtype=np.float32)},
            actions=np.zeros((4, n_envs), dtype=np.int64),
            rewards=np.zeros((4, n_envs), dtype=np.float32),
            dones=np.zeros((4, n_envs), dtype=np.bool_),
            values=np.zeros((4, n_envs), dtype=np.float32),
            log_probs=np.zeros((4, n_envs), dtype=np.float32),
            last_values=np.zeros((n_envs,), dtype=np.float32),
            action_masks=np.ones((4, n_envs, 8), dtype=np.bool_),
        )

    assert state.accept_rollout(_partial(16))[0]
    assert state.accept_rollout(_partial(16))[0]
    assert state.accept_rollout(_partial(4))[0]
    assert q.qsize() == 3
