from __future__ import annotations

import queue
import time

from re1_rl.yawn_rails_immediate_ingest import offer_immediate_yawn_ingest


def test_offer_noop_without_learner_host(monkeypatch) -> None:
    monkeypatch.delenv("RE1_LEARNER_HOST", raising=False)
    monkeypatch.delenv("LEARNER_HOST", raising=False)
    monkeypatch.delenv("FLEET_LEARNER_HOST", raising=False)
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "1")
    assert offer_immediate_yawn_ingest({"checkpoint_index": 4}) is False


def test_offer_noop_when_sync_off(monkeypatch) -> None:
    monkeypatch.setenv("LEARNER_HOST", "127.0.0.1")
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    assert offer_immediate_yawn_ingest({"checkpoint_index": 4}) is False


def test_offer_drops_when_queue_full(monkeypatch) -> None:
    monkeypatch.setenv("LEARNER_HOST", "127.0.0.1")
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "1")
    blocked: queue.Queue = queue.Queue(maxsize=1)
    blocked.put_nowait({"checkpoint_index": 0})
    monkeypatch.setattr(
        "re1_rl.yawn_rails_immediate_ingest._ensure_worker", lambda: blocked
    )
    assert offer_immediate_yawn_ingest({"checkpoint_index": 1}) is False


def test_offer_posts_on_background_thread(monkeypatch) -> None:
    monkeypatch.setenv("LEARNER_HOST", "127.0.0.1")
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "1")
    seen: list[list] = []

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def ingest_yawn_rails_proposals(self, proposals):
            seen.append(list(proposals))
            return {"accepted": ["cp04"]}

    import re1_rl.yawn_rails_immediate_ingest as mod

    monkeypatch.setattr(mod, "_QUEUE", None)
    monkeypatch.setattr(mod, "_STARTED", False)
    monkeypatch.setattr(
        "re1_rl.distributed.worker_client.WorkerClient", _FakeClient
    )
    assert offer_immediate_yawn_ingest({"checkpoint_index": 4}) is True
    deadline = time.monotonic() + 2.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.02)
    assert seen == [[{"checkpoint_index": 4}]]
