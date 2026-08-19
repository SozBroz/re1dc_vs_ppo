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
    )

    assert calls == 2
