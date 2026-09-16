"""Supervisor tests for persistent distributed async workers."""

from __future__ import annotations

import threading

from scripts.distributed_train_parallel import _run_async_worker_with_restarts


def test_async_worker_supervisor_retries_failures_until_stopped() -> None:
    stop = threading.Event()
    calls = 0
    health: list[int] = []

    def run_once() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"startup failure {calls}")

    def mark_degraded() -> None:
        health.append(0)
        if len(health) == 3:
            stop.set()

    _run_async_worker_with_restarts(
        run_once,
        stop_event=stop,
        machine_name="test",
        on_restart=mark_degraded,
        initial_delay_s=0.0,
        max_delay_s=0.0,
        max_attempts=20,
    )

    assert calls == 3
    assert health == [0, 0, 0]


def test_async_worker_supervisor_restarts_unexpected_clean_exit() -> None:
    stop = threading.Event()
    calls = 0

    def run_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            stop.set()

    _run_async_worker_with_restarts(
        run_once,
        stop_event=stop,
        machine_name="test",
        initial_delay_s=0.0,
        max_delay_s=0.0,
        max_attempts=20,
    )

    assert calls == 2


def test_async_worker_circuit_breaker_stops_spiral() -> None:
    stop = threading.Event()
    calls = 0
    restarts = 0

    def run_once() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    def on_restart() -> None:
        nonlocal restarts
        restarts += 1

    _run_async_worker_with_restarts(
        run_once,
        stop_event=stop,
        machine_name="test",
        on_restart=on_restart,
        initial_delay_s=0.0,
        max_delay_s=0.0,
        max_attempts=4,
        attempt_window_s=3600.0,
    )

    assert calls == 4
    assert restarts == 4
    assert not stop.is_set()


def test_recover_failure_must_not_leave_closed_conn_in_wait_list() -> None:
    """Closed pipe ends in parent_conns make wait() raise handle-is-closed."""
    from re1_rl.distributed.async_worker_runtime import _waitable_parent_conns

    class _Closed:
        closed = True

    class _Open:
        closed = False

    conns: list = [_Open(), _Closed(), None, _Open()]
    live = _waitable_parent_conns(conns)
    assert len(live) == 2
    assert conns[1] is None
    assert all(getattr(c, "closed", False) is False for c in live)
