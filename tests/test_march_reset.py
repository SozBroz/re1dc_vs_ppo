"""March NN reset: in-place base reload + learner HTTP round-trip."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import torch

from re1_rl.distributed import march_reset
from re1_rl.distributed.march_reset import apply_march_reset
from re1_rl import planner_march


class _FakeOptimizer:
    def __init__(self) -> None:
        self.restored = None

    def state_dict(self) -> dict:
        return {"step": 7}

    def load_state_dict(self, state: dict) -> None:
        self.restored = state


class _FakePolicy:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.loaded = None
        self.optimizer = _FakeOptimizer()

    def state_dict(self) -> dict:
        return {"w": torch.full((2,), float(len(self.tag)))}

    def load_state_dict(self, state: dict, strict: bool = True) -> None:
        self.loaded = state

    def set_training_mode(self, mode: bool) -> None:
        pass


class _FakeModel:
    def __init__(self, tag: str) -> None:
        self.policy = _FakePolicy(tag)
        self.device = "cpu"
        self.num_timesteps = 123


class _FakeStore:
    def __init__(self) -> None:
        self.version = 41
        self.published = []

    def publish(self, state_dict: dict) -> int:
        self.version += 1
        self.published.append(state_dict)
        return self.version


# NOTE: _fake_learner's lambda needs the namespace; build it properly below.
def _make_learner(advance_id: str | None) -> SimpleNamespace:
    ns = SimpleNamespace(
        pending_march_reset=(
            {"advance_id": advance_id, "requested_unix": 1.0}
            if advance_id is not None
            else None
        ),
        march_reset_last_attempt_unix=0.0,
        march_reset_applied={},
        current_version=0,
    )
    ns.set_current_version = lambda v: setattr(ns, "current_version", v)
    return ns


def _loader(device: str, resume, tb_log) -> _FakeModel:
    assert str(resume).endswith(".zip")
    return _FakeModel("base")


def test_apply_swaps_policy_and_publishes(tmp_path, monkeypatch) -> None:
    ckpt = tmp_path / "planner_march_base_weights.zip"
    ckpt.write_bytes(b"fakezip")
    monkeypatch.setenv("RE1_PLANNER_MARCH_BASE_CKPT", str(ckpt))
    monkeypatch.delenv("RE1_RL_ROOT", raising=False)
    live = _FakeModel("specialized")
    store = _FakeStore()
    learner = _make_learner("pl03->pl04@99")
    assert apply_march_reset(
        model=live, weight_store=store, learner_state=learner,
        machine_name="test", loader=_loader,
    ) is True
    # Base weights (tag "base" -> 4.0) landed in the live policy.
    assert live.policy.loaded is not None
    assert float(live.policy.loaded["w"][0]) == 4.0
    assert live.policy.optimizer.restored == {"step": 7}
    assert store.version == 42 and len(store.published) == 1
    assert learner.current_version == 42
    assert learner.march_reset_applied["last_applied_advance_id"] == "pl03->pl04@99"
    assert learner.pending_march_reset is None


def test_apply_no_pending_is_noop() -> None:
    live = _FakeModel("specialized")
    store = _FakeStore()
    assert apply_march_reset(
        model=live, weight_store=store, learner_state=_make_learner(None),
        machine_name="test", loader=_loader,
    ) is False
    assert store.version == 41


def test_apply_missing_ckpt_stays_pending(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "RE1_PLANNER_MARCH_BASE_CKPT", str(tmp_path / "nope.zip")
    )
    learner = _make_learner("pl03->pl04@99")
    assert apply_march_reset(
        model=_FakeModel("x"), weight_store=_FakeStore(), learner_state=learner,
        machine_name="test", loader=_loader,
    ) is False
    assert learner.pending_march_reset is not None


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_reset_request_ok(monkeypatch) -> None:
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"ok": True, "advance_id": "a1"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert planner_march._request_learner_reset("h", 8765, "a1") is True
    assert seen["url"] == "http://h:8765/march/reset_weights"
    assert seen["body"] == {"advance_id": "a1"}


def test_reset_request_refused(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: _FakeResp({"ok": False})
    )
    assert planner_march._request_learner_reset("h", 8765, "a1") is False


def test_reset_ack_parses_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: _FakeResp(
            {"march_reset": {"last_applied_advance_id": "a1"}}
        ),
    )
    assert planner_march._learner_reset_applied("h", 8765) == "a1"


def test_reset_ack_unknown_on_error(monkeypatch) -> None:
    def boom(url, timeout=None):
        raise OSError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert planner_march._learner_reset_applied("h", 8765) is None
