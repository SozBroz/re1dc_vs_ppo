"""Packed fleet PPO train + curriculum/schema identity + Baseline E metrics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from sb3_contrib import MaskablePPO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.learner_train import train_on_rollouts
from re1_rl.distributed.packed_train import (
    filter_rollouts_by_identity,
    train_packed_on_rollouts,
)
from re1_rl.distributed.rollout_types import WorkerRollout, normalize_curriculum_id
from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION, make_re1_policy_spaces
from re1_rl.distributed.weights import _SpaceHolderEnv
from re1_rl.env import ACTION_NAMES
from re1_rl.policy_config import POLICY_KWARGS
from re1_rl.training_metrics_log import (
    build_fleet_epoch_record,
    emit_fleet_epoch_metrics,
    ensure_training_logger,
    policy_version_lag_hist,
    training_metrics_jsonl_path,
)

N_ACTIONS = len(ACTION_NAMES)


def _tiny_model() -> MaskablePPO:
    obs_space, act_space = make_re1_policy_spaces()
    return MaskablePPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(obs_space, act_space),
        policy_kwargs=POLICY_KWARGS,
        n_steps=8,
        batch_size=8,
        n_epochs=1,
        device="cpu",
        verbose=0,
    )


def _fake_rollout(
    n_steps: int = 8,
    n_envs: int = 2,
    version: int = 1,
    *,
    worker_id: str = "w",
    curriculum_id: str = "curriculum/yawn_rails_one_leg.json",
    obs_schema_version: int = OBS_SCHEMA_VERSION,
) -> WorkerRollout:
    obs_space, _ = make_re1_policy_spaces()
    # Use env-layout spaces for fake frames (HWC) if needed — policy spaces are CHW.
    from re1_rl.distributed.spaces import make_re1_spaces

    env_obs, _ = make_re1_spaces()
    obs = {
        key: np.zeros((n_steps, n_envs, *space.shape), dtype=space.dtype)
        for key, space in env_obs.spaces.items()
    }
    obs["frame"] = np.random.randint(
        0, 255, (n_steps, n_envs, *env_obs["frame"].shape), dtype=np.uint8
    )
    masks = np.ones((n_steps, n_envs, N_ACTIONS), dtype=np.bool_)
    masks[..., N_ACTIONS // 2 :] = False
    return WorkerRollout(
        worker_id=worker_id,
        policy_version=version,
        n_envs=n_envs,
        n_steps=n_steps,
        obs=obs,
        actions=np.random.randint(0, N_ACTIONS // 2, (n_steps, n_envs), dtype=np.int64),
        rewards=np.random.randn(n_steps, n_envs).astype(np.float32),
        dones=np.zeros((n_steps, n_envs), dtype=np.bool_),
        values=np.random.randn(n_steps, n_envs).astype(np.float32),
        log_probs=np.random.randn(n_steps, n_envs).astype(np.float32) * 0.01,
        last_values=np.random.randn(n_envs).astype(np.float32),
        action_masks=masks,
        curriculum_id=curriculum_id,
        obs_schema_version=obs_schema_version,
    )


def test_normalize_curriculum_id() -> None:
    assert (
        normalize_curriculum_id(r"D:\re1_rl\curriculum\yawn_rails_one_leg.json")
        == "curriculum/yawn_rails_one_leg.json"
    )
    assert normalize_curriculum_id("curriculum/m0_dining_to_main_hall.json") == (
        "curriculum/m0_dining_to_main_hall.json"
    )


def test_identity_filter_drops_mismatch() -> None:
    ok = _fake_rollout(curriculum_id="curriculum/yawn_rails_one_leg.json")
    bad = _fake_rollout(curriculum_id="curriculum/m0_dining_to_main_hall.json")
    kept = filter_rollouts_by_identity(
        [ok, bad],
        expected_curriculum_id="curriculum/yawn_rails_one_leg.json",
        expected_obs_schema_version=OBS_SCHEMA_VERSION,
    )
    assert kept == [ok]


def test_identity_filter_drops_bad_schema() -> None:
    ok = _fake_rollout(obs_schema_version=OBS_SCHEMA_VERSION)
    bad = _fake_rollout(obs_schema_version=0)
    kept = filter_rollouts_by_identity(
        [ok, bad],
        expected_obs_schema_version=OBS_SCHEMA_VERSION,
    )
    assert kept == [ok]


def test_packed_train_single_train_call() -> None:
    model = _tiny_model()
    calls = {"n": 0}
    real_train = model.train

    def _counting_train() -> None:
        calls["n"] += 1
        return real_train()

    model.train = _counting_train  # type: ignore[method-assign]
    before = model.num_timesteps
    steps = train_packed_on_rollouts(
        model,
        [_fake_rollout(version=1), _fake_rollout(version=3, n_steps=4)],
    )
    assert calls["n"] == 1
    assert steps == 16 + 8
    assert model.num_timesteps == before + steps


def test_fill_packed_buffer_vectorized_full_and_finite() -> None:
    from re1_rl.distributed.learner_train import merge_rollouts
    from re1_rl.distributed.packed_train import (
        _merged_to_flat_segment,
        fill_packed_rollout_buffer,
    )

    model = _tiny_model()
    flat = _merged_to_flat_segment(model, merge_rollouts([_fake_rollout(n_steps=4, n_envs=2)]))
    buf = fill_packed_rollout_buffer(model, flat)
    n = int(flat["n"])
    assert buf.buffer_size == n
    assert buf.n_envs == 1
    assert buf.full is True
    assert buf.pos == n
    assert buf.observations["frame"].shape[0] == n
    assert np.isfinite(buf.returns).all()
    assert np.isfinite(buf.advantages).all()
    assert buf.action_masks.shape == (n, 1, N_ACTIONS)


def test_train_on_rollouts_uses_packed_path() -> None:
    model = _tiny_model()
    before = model.num_timesteps
    steps = train_on_rollouts(
        model,
        [_fake_rollout(version=1), _fake_rollout(version=2)],
    )
    assert steps == 32
    assert model.num_timesteps == before + 32


def test_learner_state_rejects_curriculum_mismatch() -> None:
    from queue import Queue

    from re1_rl.distributed.learner_server import LearnerState
    from re1_rl.distributed.weight_store import WeightStore

    state = LearnerState(
        WeightStore(),
        Queue(),
        machine_name="t",
        max_staleness=2,
        expected_curriculum_id="curriculum/yawn_rails_one_leg.json",
        expected_obs_schema_version=OBS_SCHEMA_VERSION,
    )
    ok, reason = state.accept_rollout(
        _fake_rollout(curriculum_id="curriculum/m0_dining_to_main_hall.json")
    )
    assert not ok
    assert reason == "curriculum_mismatch"
    ok2, reason2 = state.accept_rollout(_fake_rollout())
    assert ok2
    assert reason2 == "ok"


def test_mixed_version_packed_fleet_emits_one_jsonl(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Baseline E: one packed train → one JSONL line with fleet fields."""
    model = _tiny_model()
    ensure_training_logger(model)
    rollouts = [
        _fake_rollout(version=1, worker_id="workhorse1:actor_0", n_steps=8, n_envs=2),
        _fake_rollout(version=3, worker_id="pking:actor_1", n_steps=4, n_envs=2),
    ]
    current_version = 3
    lag_hist = policy_version_lag_hist(
        rollouts, current_policy_version=current_version
    )
    fleet_metrics: dict = {}
    steps = train_on_rollouts(
        model,
        rollouts,
        current_policy_version=current_version,
        fleet_metrics=fleet_metrics,
    )
    assert steps == 16 + 8
    assert fleet_metrics["accepted_steps"] == steps
    assert set(fleet_metrics["contributors"]) == {"pking", "workhorse1"}
    assert fleet_metrics["curriculum_id"] == "curriculum/yawn_rails_one_leg.json"
    assert fleet_metrics["policy_version_counts"] == {"1": 16, "3": 8}
    assert lag_hist == {"0": 8, "2": 16}

    path = training_metrics_jsonl_path(tmp_path, run_name="packed_fleet")
    record = build_fleet_epoch_record(
        model,
        update=1,
        policy_version=current_version + 1,
        accepted_steps=int(fleet_metrics["accepted_steps"]),
        contributors=list(fleet_metrics["contributors"]),
        curriculum_id=str(fleet_metrics["curriculum_id"]),
        collection_wall_s=12.5,
        train_wall_s=3.25,
        policy_version_lag_hist=lag_hist,
        policy_version_counts=fleet_metrics["policy_version_counts"],
        relevance_step_keep_rate=1.0,
        extra={"logger_scalars": fleet_metrics.get("logger_scalars") or {}},
    )
    emit_fleet_epoch_metrics(path, record)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["accepted_steps"] == steps
    assert parsed["n_contributors"] == 2
    assert parsed["curriculum_id"] == "curriculum/yawn_rails_one_leg.json"
    assert parsed["policy_version_lag_hist"] == {"0": 8, "2": 16}
    assert parsed["policy_version_counts"] == {"1": 16, "3": 8}
    assert parsed["collection_wall_s"] == 12.5
    assert parsed["train_wall_s"] == 3.25
    assert "train/approx_kl" in parsed or "train/loss" in parsed
    out = capsys.readouterr().out
    assert out.count("[train:metrics]") == 1
    assert "accepted=" in out


def test_zero_n_fleet_metrics_when_empty_batch() -> None:
    model = _tiny_model()
    fleet_metrics: dict = {}
    steps = train_on_rollouts(model, [], fleet_metrics=fleet_metrics)
    assert steps == 0
    assert fleet_metrics["accepted_steps"] == 0
    record = build_fleet_epoch_record(
        model,
        update=9,
        policy_version=9,
        accepted_steps=0,
        contributors=[],
        collection_wall_s=360.0,
        train_wall_s=0.0,
    )
    assert record["accepted_steps"] == 0
    assert record["train/optimizer_steps"] == 0.0
    assert "train/approx_kl" not in record
