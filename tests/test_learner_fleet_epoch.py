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


def test_epoch_excludes_live_workers_with_zero_healthy_actors() -> None:
    store = WeightStore()
    state = LearnerState(
        store,
        queue.Queue(),
        machine_name="t",
        max_staleness=2,
        worker_liveness_s=60,
    )
    state.register_worker("workhorse2", n_envs=28, is_local=True)
    state.register_worker("pking", n_envs=0)
    state.register_worker("workhorse1", n_envs=8)

    _, expected = state.begin_epoch()
    assert set(expected) == {"workhorse2", "workhorse1"}

    state.heartbeat_worker("workhorse1", n_envs=0)
    assert state.refresh_expected() == ["workhorse2"]


def test_dead_remote_stays_in_expected() -> None:
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
    # Heartbeat aged out of workers, but the epoch snapshot stays so grace
    # can train instead of n_expected hitting 0 and deadlocking.
    assert "pking" in st["expected"]
    assert st["n_live"] == 1
    assert st["n_expected"] == 2
    assert st["ready"] is False
    assert "pking" in st["missing"]
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


def test_capacity_full_rejects_after_cohort_fills() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(
        store,
        q,
        machine_name="t",
        max_staleness=2,
        worker_liveness_s=60,
        max_pending_steps=10,
    )
    state.set_current_version(1)
    state.begin_epoch()

    first = WorkerRollout(
        worker_id="pking",
        policy_version=1,
        n_envs=2,
        n_steps=4,
        obs={"x": np.zeros((4, 2), dtype=np.float32)},
        actions=np.zeros((4, 2), dtype=np.int64),
        rewards=np.zeros((4, 2), dtype=np.float32),
        dones=np.zeros((4, 2), dtype=np.bool_),
        values=np.zeros((4, 2), dtype=np.float32),
        log_probs=np.zeros((4, 2), dtype=np.float32),
        last_values=np.zeros((2,), dtype=np.float32),
        action_masks=np.ones((4, 2, 8), dtype=np.bool_),
    )
    ok, reason = state.accept_rollout(first)
    assert ok and reason == "ok"
    assert state.admitted_steps() == 8
    assert state.cohort_full() is False

    second = WorkerRollout(
        worker_id="workhorse1",
        policy_version=1,
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
    # 8 + 4 > 10 → capacity_full, and that remainder marks the cohort done.
    ok, reason = state.accept_rollout(second)
    assert not ok
    assert reason == "capacity_full"
    assert state.rollouts_rejected_capacity == 1
    assert state.cohort_full() is True
    assert q.qsize() == 1


def test_capacity_allows_first_oversized_rollout() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(
        store,
        q,
        machine_name="t",
        max_staleness=2,
        worker_liveness_s=60,
        max_pending_steps=4,
    )
    state.set_current_version(1)
    state.begin_epoch()
    big = WorkerRollout(
        worker_id="pking",
        policy_version=1,
        n_envs=2,
        n_steps=4,
        obs={"x": np.zeros((4, 2), dtype=np.float32)},
        actions=np.zeros((4, 2), dtype=np.int64),
        rewards=np.zeros((4, 2), dtype=np.float32),
        dones=np.zeros((4, 2), dtype=np.bool_),
        values=np.zeros((4, 2), dtype=np.float32),
        log_probs=np.zeros((4, 2), dtype=np.float32),
        last_values=np.zeros((2,), dtype=np.float32),
        action_masks=np.ones((4, 2, 8), dtype=np.bool_),
    )
    ok, reason = state.accept_rollout(big)
    assert ok and reason == "ok"
    assert state.admitted_steps() == 8
    assert state.cohort_full() is True
    assert state.accept_rollout(_rollout("workhorse1")) == (False, "capacity_full")


def test_begin_epoch_resets_admitted_steps() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(
        store,
        q,
        machine_name="t",
        max_staleness=2,
        worker_liveness_s=60,
        max_pending_steps=100,
    )
    state.set_current_version(1)
    state.begin_epoch()
    assert state.accept_rollout(_rollout("pking"))[0]
    assert state.admitted_steps() == 4
    state.begin_epoch()
    assert state.admitted_steps() == 0
    assert state.cohort_full() is False


def test_begin_epoch_reopens_admission_for_overlap() -> None:
    """Train-overlap path: reopen cohort while a prior cohort is held aside."""
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(
        store,
        q,
        machine_name="t",
        max_staleness=2,
        worker_liveness_s=60,
        max_pending_steps=8,
    )
    state.set_current_version(1)
    state.begin_epoch()
    assert state.accept_rollout(_rollout("pking"))[0]
    assert state.accept_rollout(_rollout("workhorse1"))[0]
    assert state.cohort_full() is True
    assert state.accept_rollout(_rollout("workhorse2")) == (False, "capacity_full")

    # Snapshot for train would drain ``q``; reopen admission for next cohort.
    train_q: list = []
    while not q.empty():
        train_q.append(q.get_nowait())
    assert len(train_q) == 2
    state.begin_epoch()
    assert state.cohort_full() is False
    assert state.accept_rollout(_rollout("pking"))[0]
    assert state.admitted_steps() == 4
    assert q.qsize() == 1


def test_heartbeat_wipe_does_not_empty_expected() -> None:
    """All remotes looking dead must not clear the epoch expected set."""
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(store, q, machine_name="t", max_staleness=2, worker_liveness_s=0.05)
    state.set_current_version(1)
    state.register_worker("pking", n_envs=12)
    state.register_worker("workhorse1", n_envs=8)
    eid, expected = state.begin_epoch()
    assert eid == 1
    assert set(expected) == {"pking", "workhorse1"}
    assert state.accept_rollout(_rollout("pking"))[0]
    time.sleep(0.08)
    st = state.epoch_status()
    assert st["n_live"] == 0
    assert st["n_expected"] == 2
    assert set(st["expected"]) == {"pking", "workhorse1"}
    assert st["ready"] is False


def test_refresh_expected_does_not_reset_epoch() -> None:
    store = WeightStore()
    q: queue.Queue = queue.Queue()
    state = LearnerState(
        store,
        q,
        machine_name="t",
        max_staleness=2,
        worker_liveness_s=60,
        max_pending_steps=100,
    )
    state.set_current_version(1)
    eid, expected = state.begin_epoch()
    assert eid == 1
    assert expected == []
    assert state.accept_rollout(_rollout("pking"))[0]
    assert state.admitted_steps() == 4
    state.register_worker("pking", n_envs=12)
    state.register_worker("workhorse1", n_envs=8)
    refreshed = state.refresh_expected()
    assert set(refreshed) == {"pking", "workhorse1"}
    st = state.epoch_status()
    assert st["epoch_id"] == 1
    assert state.admitted_steps() == 4
    assert state.cohort_full() is False
