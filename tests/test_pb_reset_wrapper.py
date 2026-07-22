"""PbChampionResetWrapper: sidecar injection + mix sampling on env.reset()."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import gymnasium as gym
import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.pb_curriculum import sample_typewriter_start, typewriter_mix_weights
from re1_rl.pb_reset_wrapper import PbChampionResetWrapper


class _RecordResetEnv(gym.Env):
    observation_space = gym.spaces.Dict({})
    action_space = gym.spaces.Discrete(1)

    def __init__(self) -> None:
        super().__init__()
        self.last_options: dict | None = None

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        self.last_options = dict(options or {})
        return {}, {}

    def action_masks(self) -> np.ndarray:
        return np.ones(1, dtype=bool)


def test_wrapper_injects_sampled_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = {"state_path": "a.State", "sidecar_path": "a.sidecar.json"}
    calls: list[Path] = []

    def _fake_sample(root: Path, rng=None):
        calls.append(Path(root))
        return bundle

    monkeypatch.setattr(
        "re1_rl.pb_curriculum.sample_typewriter_start",
        _fake_sample,
    )

    inner = _RecordResetEnv()
    wrapped = PbChampionResetWrapper(inner, project_root=tmp_path)
    wrapped.reset()
    assert calls == [tmp_path]
    assert inner.last_options is not None
    assert inner.last_options.get("pb_bundle") == bundle


def test_wrapper_fresh_when_sample_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "re1_rl.pb_curriculum.sample_typewriter_start",
        lambda *_a, **_k: None,
    )
    inner = _RecordResetEnv()
    wrapped = PbChampionResetWrapper(inner, project_root=tmp_path)
    wrapped.reset()
    assert inner.last_options == {}


def test_wrapper_preserves_explicit_pb_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def _boom(*_a, **_k):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("re1_rl.pb_curriculum.sample_typewriter_start", _boom)
    explicit = {"state_path": "x.State", "sidecar_path": "x.sidecar.json"}
    inner = _RecordResetEnv()
    wrapped = PbChampionResetWrapper(inner, project_root=tmp_path)
    wrapped.reset(options={"pb_bundle": explicit})
    assert not called
    assert inner.last_options is not None
    assert inner.last_options.get("pb_bundle") == explicit


def test_wrapper_resamples_each_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    n_calls = 0

    def _count(*_a, **_k):
        nonlocal n_calls
        n_calls += 1
        return None

    monkeypatch.setattr("re1_rl.pb_curriculum.sample_typewriter_start", _count)
    inner = _RecordResetEnv()
    wrapped = PbChampionResetWrapper(inner, project_root=tmp_path)
    wrapped.reset()
    wrapped.reset()
    assert n_calls == 2


def test_sample_typewriter_start_matches_n_dependent_mix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Statistical check: wrapper's sampler honors typewriter_mix_weights."""
    from re1_rl.pb_champion import try_replace_champion

    monkeypatch.delenv("RE1_PB_SHARED_ROOT", raising=False)
    state_path = tmp_path / "106.State"
    sidecar_path = tmp_path / "106.sidecar.json"
    state_path.write_bytes(b"A")
    sidecar_path.write_text("{}", encoding="utf-8")
    assert try_replace_champion(
        tmp_path,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state={
            "room_id": "106",
            "hp": 100,
            "inventory_slots": [["ink_ribbon", 1]],
            "inventory": ["ink_ribbon"],
        },
        room_id="106",
        visited_rooms=("106",),
    )

    p_fresh, p_each = typewriter_mix_weights(1)
    assert p_fresh == pytest.approx(0.5)
    assert p_each == pytest.approx(0.5)

    rng = random.Random(99)
    n = 5000
    fresh = sum(
        1 for _ in range(n) if sample_typewriter_start(tmp_path, rng=rng) is None
    )
    assert 0.45 <= fresh / n <= 0.55


def test_warm_pb_champions_pulls_shared_on_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("RE1_PB_SHARED_ROOT", str(shared))
    sync_calls: list[Path] = []
    monkeypatch.setattr(
        "re1_rl.pb_sync.sync_champion_once",
        lambda root: sync_calls.append(Path(root)) or {"pull": "skip"},
    )
    monkeypatch.setattr("re1_rl.pb_sync.list_filled_champions", lambda _r: [])

    from re1_rl.pb_sync import warm_pb_champions_for_training

    status = warm_pb_champions_for_training(tmp_path)
    assert status["n_filled"] == 0
    assert status["p_fresh"] == pytest.approx(1.0)
    assert len(sync_calls) >= 1
