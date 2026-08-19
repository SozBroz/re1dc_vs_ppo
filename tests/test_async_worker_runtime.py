"""Async distributed worker helpers (no BizHawk)."""

from __future__ import annotations

import queue
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.async_worker_runtime import (
    _flush_local_epoch,
    _pack_and_deliver_rollouts,
    _serve_need,
    _stale_actor_indices,
    _terminate_actor_process,
    pack_rollouts,
    worker_rollout_from_actor_msg,
)
from re1_rl.async_fleet import _serve_needs_batch
from re1_rl.async_fleet import _wait_for_actor_spawn
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.env import ACTION_NAMES
from re1_rl.async_fleet import DISTRIBUTED_EPOCH_HYPERPARAMS, PPO_HYPERPARAMS

N_ACTIONS = len(ACTION_NAMES)


class _FakeProcess:
    def __init__(self, alive: bool) -> None:
        self.alive = bool(alive)
        self.pid = 123
        self.joins = 0
        self.exitcode = None if alive else 1

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float) -> None:
        self.joins += 1


def test_stale_actor_indices_detect_dead_and_silent_ranks() -> None:
    processes = [_FakeProcess(True), _FakeProcess(False), _FakeProcess(True)]

    stale = _stale_actor_indices(
        processes,
        [90.0, 99.0, 0.0],
        now=100.0,
        timeout_s=10.0,
        exempt_indices={2},
    )

    assert stale == [0, 1]


def test_stale_actor_indices_activity_refresh_prevents_restart() -> None:
    processes = [_FakeProcess(True), _FakeProcess(True)]

    stale = _stale_actor_indices(
        processes,
        [99.9, 95.1],
        now=100.0,
        timeout_s=5.0,
    )

    assert stale == []


def test_dead_actor_cleanup_still_kills_recorded_emuhawk(monkeypatch) -> None:
    from re1_rl.distributed import async_worker_runtime as awr

    calls: list[list[str]] = []
    monkeypatch.setattr(awr.os, "name", "nt")
    monkeypatch.setattr(
        "re1_rl.distributed.async_worker_runtime.subprocess.run",
        lambda argv, **_kwargs: calls.append(list(argv)),
    )
    proc = _FakeProcess(False)

    _terminate_actor_process(proc, emuhawk_pid=456, timeout_s=0.01)

    assert proc.joins == 1
    assert calls == [["taskkill", "/PID", "456", "/T", "/F"]]


class _FakePolicy:
    def __init__(self) -> None:
        self.policy_version = 7
        self.masked_calls = 0
        self.batch_calls = 0
        self.diagnostic_calls = 0

    def predict_masked(self, obs, masks):
        self.masked_calls += 1
        assert masks.dtype == bool
        assert masks.shape[-1] == N_ACTIONS
        return 3, 0.5, -0.1

    def predict_masked_batch(self, obs, masks):
        self.masked_calls += 1
        n = int(masks.shape[0])
        assert masks.dtype == bool
        assert masks.shape == (n, N_ACTIONS)
        return (
            np.full(n, 3, dtype=np.int64),
            np.full(n, 0.5, dtype=np.float32),
            np.full(n, -0.1, dtype=np.float32),
        )

    def predict_masked_batch_with_diagnostics(self, obs, masks):
        self.diagnostic_calls += 1
        n = int(masks.shape[0])
        return (
            np.full(n, 4, dtype=np.int64),
            np.full(n, 0.75, dtype=np.float32),
            np.full(n, -0.25, dtype=np.float32),
            np.full((n, N_ACTIONS), 2.0, dtype=np.float32),
            np.full((n, N_ACTIONS), 1.0 / N_ACTIONS, dtype=np.float32),
        )

    def predict_batch(self, obs):
        self.batch_calls += 1
        return np.array([1]), np.array([0.2], dtype=np.float32), np.array([-0.2], dtype=np.float32)

    def predict_values(self, obs):
        return np.array([1.25], dtype=np.float32)


def _fake_obs() -> dict[str, np.ndarray]:
    return {
        "frame": np.zeros((63, 84, 4), dtype=np.uint8),
        "proprio": np.zeros((8,), dtype=np.float32),
    }


def test_flush_local_epoch_syncs_weights_at_barrier() -> None:
    """Local WH2 policy updates only at epoch flush, not mid-horizon."""
    from re1_rl.distributed.weight_store import WeightStore

    class _Pol:
        def __init__(self) -> None:
            self.policy_version = 1
            self.loads: list[int] = []

        def load_from_state_dict(self, state_dict, version) -> None:
            self.policy_version = int(version)
            self.loads.append(int(version))

    store = WeightStore()
    import torch

    store.publish({"w": torch.zeros(1)})  # version 1
    store.publish({"w": torch.ones(1)})  # version 2
    pol = _Pol()
    q: queue.Queue = queue.Queue()
    retained, capacity_full = _flush_local_epoch(
        [],
        rollout_sink=q,
        machine_name="workhorse2",
        worker_id="workhorse2",
        policy=pol,
        weight_store=store,
    )
    assert retained == []
    assert capacity_full is False
    assert pol.loads == [2]
    assert pol.policy_version == 2


def test_serve_need_includes_policy_version() -> None:
    policy = _FakePolicy()
    conn = MagicMock()
    masks = np.ones(N_ACTIONS, dtype=bool)
    masks[0] = False
    _serve_need(conn, {"t": "need", "obs": _fake_obs(), "action_masks": masks}, policy)
    assert policy.masked_calls == 1
    assert policy.batch_calls == 0
    conn.send.assert_called_once()
    payload = conn.send.call_args[0][0]
    assert payload["t"] == "act"
    assert payload["action"] == 3
    assert payload["value"] == 0.5
    assert payload["logprob"] == pytest.approx(-0.1)
    assert payload["policy_version"] == 7


def test_serve_needs_batch_one_forward_for_many_needs() -> None:
    policy = _FakePolicy()
    masks = np.ones(N_ACTIONS, dtype=bool)
    pairs = [
        (MagicMock(), {"t": "need", "obs": _fake_obs(), "action_masks": masks})
        for _ in range(4)
    ]
    _serve_needs_batch(pairs, policy, max_batch=32)
    assert policy.masked_calls == 1
    for conn, _ in pairs:
        payload = conn.send.call_args[0][0]
        assert payload["action"] == 3


def test_serve_needs_batch_reports_broken_response_pipe() -> None:
    policy = _FakePolicy()
    masks = np.ones(N_ACTIONS, dtype=bool)
    broken = MagicMock()
    broken.send.side_effect = BrokenPipeError()

    failed = _serve_needs_batch(
        [(broken, {"t": "need", "obs": _fake_obs(), "action_masks": masks})],
        policy,
        max_batch=1,
    )

    assert failed == [broken]


def test_serve_needs_batch_routes_diagnostics_only_to_requester() -> None:
    policy = _FakePolicy()
    masks = np.ones(N_ACTIONS, dtype=bool)
    regular = MagicMock()
    diagnostic = MagicMock()
    _serve_needs_batch(
        [
            (regular, {"t": "need", "obs": _fake_obs(), "action_masks": masks}),
            (
                diagnostic,
                {
                    "t": "need",
                    "obs": _fake_obs(),
                    "action_masks": masks,
                    "want_policy_diagnostics": True,
                },
            ),
        ],
        policy,
        max_batch=32,
    )
    assert policy.masked_calls == 1
    assert policy.diagnostic_calls == 1
    normal_payload = regular.send.call_args[0][0]
    diag_payload = diagnostic.send.call_args[0][0]
    assert "raw_logits" not in normal_payload
    assert "masked_probs" not in normal_payload
    assert diag_payload["action"] == 4
    assert diag_payload["raw_logits"].shape == (N_ACTIONS,)
    assert diag_payload["masked_probs"].shape == (N_ACTIONS,)


def test_wait_for_actor_spawn_accepts_arbitrary_logical_ranks() -> None:
    import multiprocessing as mp

    parents = []
    children = []
    for rank in (0, 3, 5):
        parent, child = mp.Pipe(duplex=True)
        child.send({"t": "spawned", "rank": rank, "emuhawk_pid": 9000 + rank})
        parents.append(parent)
        children.append(child)
    try:
        pids = _wait_for_actor_spawn(
            parents,
            3,
            actor_ranks=[0, 3, 5],
            timeout_s=1.0,
        )
        assert pids == {0: 9000, 3: 9003, 5: 9005}
    finally:
        for conn in parents + children:
            conn.close()


def test_wait_for_actor_spawn_preserves_reported_startup_error() -> None:
    import multiprocessing as mp

    parent, child = mp.Pipe(duplex=True)
    child.send({"t": "spawn_error", "rank": 7, "error": "first_byte timeout"})
    try:
        with pytest.raises(RuntimeError, match="first_byte timeout"):
            _wait_for_actor_spawn(
                [parent],
                1,
                processes=[_FakeProcess(False)],
                actor_ranks=[7],
                timeout_s=1.0,
            )
    finally:
        parent.close()
        child.close()


def test_serve_need_falls_back_to_predict_batch() -> None:
    policy = _FakePolicy()
    conn = MagicMock()
    _serve_need(conn, {"t": "need", "obs": _fake_obs()}, policy)
    assert policy.batch_calls == 1
    assert policy.masked_calls == 0
    payload = conn.send.call_args[0][0]
    assert payload["action"] == 1


def test_worker_rollout_from_actor_msg_shapes() -> None:
    policy = _FakePolicy()
    n_steps = 4
    masks = np.ones((n_steps, N_ACTIONS), dtype=bool)
    msg: dict[str, Any] = {
        "t": "rollout",
        "rank": 2,
        "obs": {
            "frame": np.zeros((n_steps, 63, 84, 4), dtype=np.uint8),
            "proprio": np.zeros((n_steps, 8), dtype=np.float32),
        },
        "actions": np.arange(n_steps, dtype=np.int64),
        "rewards": np.ones(n_steps, dtype=np.float32),
        "dones": np.zeros(n_steps, dtype=np.bool_),
        "values": np.full(n_steps, 0.3, dtype=np.float32),
        "log_probs": np.full(n_steps, -0.4, dtype=np.float32),
        "action_masks": masks,
        "policy_version": 5,
        "last_obs": _fake_obs(),
        "episode_infos": [{"room_id": "104"}],
    }
    rollout = worker_rollout_from_actor_msg(
        msg, policy=policy, worker_id="pking", n_steps=n_steps
    )
    assert rollout.worker_id == "pking:actor_2"
    assert rollout.policy_version == 5  # horizon stamp, not live policy (7)
    assert rollout.n_envs == 1
    assert rollout.n_steps == n_steps
    assert rollout.num_timesteps() == n_steps
    assert rollout.actions.shape == (n_steps, 1)
    assert rollout.rewards.shape == (n_steps, 1)
    assert rollout.obs["frame"].shape == (n_steps, 1, 63, 84, 4)
    assert rollout.action_masks.shape == (n_steps, 1, N_ACTIONS)
    assert rollout.last_values.shape == (1,)
    assert rollout.episode_infos == [{"room_id": "104"}]


def _mini_rollout(worker_id: str, *, n_steps: int = 4, version: int = 3) -> WorkerRollout:
    return WorkerRollout(
        worker_id=worker_id,
        policy_version=version,
        n_envs=1,
        n_steps=n_steps,
        obs={
            "frame": np.zeros((n_steps, 1, 8, 8, 4), dtype=np.uint8),
            "proprio": np.zeros((n_steps, 1, 4), dtype=np.float32),
        },
        actions=np.zeros((n_steps, 1), dtype=np.int64),
        rewards=np.ones((n_steps, 1), dtype=np.float32),
        dones=np.zeros((n_steps, 1), dtype=np.bool_),
        values=np.zeros((n_steps, 1), dtype=np.float32),
        log_probs=np.zeros((n_steps, 1), dtype=np.float32),
        last_values=np.zeros((1,), dtype=np.float32),
        action_masks=np.ones((n_steps, 1, N_ACTIONS), dtype=np.bool_),
        episode_infos=[],
    )


def test_pack_rollouts_merges_env_axis() -> None:
    a = _mini_rollout("a:actor_0")
    b = _mini_rollout("a:actor_1")
    packed = pack_rollouts([a, b], worker_id="a")
    assert packed.n_envs == 2
    assert packed.n_steps == 4
    assert packed.policy_version == 3
    assert packed.actions.shape == (4, 2)
    assert packed.num_timesteps() == 8


def test_flush_local_epoch_splits_mixed_policy_versions() -> None:
    """WH2 local worker: background weight sync can mix versions in one epoch."""
    q: queue.Queue = queue.Queue()
    buffered = [
        _mini_rollout("workhorse2:actor_0", version=2),
        _mini_rollout("workhorse2:actor_1", version=3),
        _mini_rollout("workhorse2:actor_2", version=3),
    ]
    _flush_local_epoch(
        buffered,
        rollout_sink=q,
        machine_name="workhorse2",
        worker_id="workhorse2",
    )
    assert q.qsize() == 2
    first = q.get_nowait()
    second = q.get_nowait()
    versions = sorted({first.policy_version, second.policy_version})
    assert versions == [2, 3]
    assert first.policy_version == 2
    assert second.policy_version == 3
    assert second.n_envs == 2


def test_pack_and_deliver_retains_failed_chunks() -> None:
    buffered = [
        _mini_rollout("w:actor_0"),
        _mini_rollout("w:actor_1"),
        _mini_rollout("w:actor_2"),
    ]

    def deliver(packed: WorkerRollout) -> bool:
        return packed.n_envs == 1

    n_posts, retained, capacity_full = _pack_and_deliver_rollouts(
        buffered,
        worker_id="w",
        pack_max_envs=2,
        deliver=deliver,
    )
    assert n_posts == 1
    assert capacity_full is False
    assert len(retained) == 2
    assert {r.worker_id for r in retained} == {"w:actor_0", "w:actor_1"}


def test_pack_and_deliver_drops_on_capacity_full() -> None:
    buffered = [
        _mini_rollout("w:actor_0"),
        _mini_rollout("w:actor_1"),
        _mini_rollout("w:actor_2"),
    ]

    def deliver(_packed: WorkerRollout) -> str:
        return "capacity_full"

    n_posts, retained, capacity_full = _pack_and_deliver_rollouts(
        buffered,
        worker_id="w",
        pack_max_envs=16,
        deliver=deliver,
    )
    assert n_posts == 0
    assert capacity_full is True
    assert retained == []


def test_safe_upload_retries_then_retains(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    from re1_rl.distributed import async_worker_runtime as awr

    sleeps: list[float] = []
    monkeypatch.setattr(awr.time, "sleep", lambda s: sleeps.append(s))

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def upload_rollout(self, rollout: WorkerRollout) -> bool:
            self.calls += 1
            raise urllib.error.URLError(TimeoutError("timed out"))

    client = _FlakyClient()
    result = awr._safe_upload(client, "pking", _mini_rollout("w"), retries=3)
    assert result == "error"
    assert client.calls == 3
    assert sleeps == [2.0, 5.0]


def test_safe_upload_succeeds_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    from re1_rl.distributed import async_worker_runtime as awr

    monkeypatch.setattr(awr.time, "sleep", lambda s: None)

    class _RecoveringClient:
        def __init__(self) -> None:
            self.calls = 0

        def upload_rollout(self, rollout: WorkerRollout) -> bool:
            self.calls += 1
            if self.calls < 2:
                raise urllib.error.URLError(TimeoutError("timed out"))
            return True

    client = _RecoveringClient()
    result = awr._safe_upload(client, "pking", _mini_rollout("w"), retries=3)
    assert result == "ok"
    assert client.calls == 2


def test_flush_remote_epoch_eval_only_skips_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    from re1_rl.distributed import async_worker_runtime as awr

    uploads: list[WorkerRollout] = []

    class _Client:
        def upload_rollout(self, rollout: WorkerRollout) -> bool:
            uploads.append(rollout)
            return True

        def fetch_weights(self, min_version: int = 0):
            return 0, b""

    class _Policy:
        policy_version = 1

    retained, capacity_full = awr._flush_remote_epoch(
        [_mini_rollout("pking-fight-eval")],
        client=_Client(),
        policy=_Policy(),
        machine_name="pking",
        worker_id="pking-fight-eval",
        eval_only=True,
    )
    assert uploads == []
    assert retained == []
    assert capacity_full is False


def test_flush_local_epoch_retains_when_sink_rejects() -> None:
    class _RejectFirstSink:
        def __init__(self) -> None:
            self.accepted: list[WorkerRollout] = []
            self.last_reject_reason = "rejected"

        def put(self, rollout: WorkerRollout) -> bool:
            if not self.accepted:
                self.last_reject_reason = "rejected"
                return False
            self.accepted.append(rollout)
            return True

    sink = _RejectFirstSink()
    buffered = [
        _mini_rollout("workhorse2:actor_0"),
        _mini_rollout("workhorse2:actor_1"),
    ]
    retained, capacity_full = _flush_local_epoch(
        buffered,
        rollout_sink=sink,
        machine_name="workhorse2",
        worker_id="workhorse2",
    )
    assert capacity_full is False
    assert len(retained) == 2
    assert len(sink.accepted) == 0


def test_flush_local_epoch_drops_on_capacity_full() -> None:
    class _CapacitySink:
        def __init__(self) -> None:
            self.last_reject_reason = "capacity_full"

        def put(self, rollout: WorkerRollout) -> bool:
            self.last_reject_reason = "capacity_full"
            return False

    sink = _CapacitySink()
    buffered = [
        _mini_rollout("workhorse2:actor_0"),
        _mini_rollout("workhorse2:actor_1"),
    ]
    retained, capacity_full = _flush_local_epoch(
        buffered,
        rollout_sink=sink,
        machine_name="workhorse2",
        worker_id="workhorse2",
    )
    assert capacity_full is True
    assert retained == []


def test_distributed_epoch_hyperparams_gentler_than_monolithic() -> None:
    assert DISTRIBUTED_EPOCH_HYPERPARAMS["learning_rate"] < PPO_HYPERPARAMS["learning_rate"]
    assert DISTRIBUTED_EPOCH_HYPERPARAMS["n_epochs"] <= PPO_HYPERPARAMS["n_epochs"]
    assert DISTRIBUTED_EPOCH_HYPERPARAMS["batch_size"] >= PPO_HYPERPARAMS["batch_size"]
    # MC horizon ≈6× γ half-life; longer than monolithic n_steps=1024 at γ≈25s HL.
    assert DISTRIBUTED_EPOCH_HYPERPARAMS["n_steps"] == 1125
