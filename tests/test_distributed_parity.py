"""Parity guards: distributed spaces/resume match monolithic fleet (no BizHawk)."""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

from gymnasium import spaces
from stable_baselines3 import PPO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.async_fleet import PPO_HYPERPARAMS, load_async_learner
from re1_rl.checkpoint_io import resolve_resume_path, write_latest_pointer
from re1_rl.distributed.spaces import make_re1_policy_spaces, make_re1_spaces
from re1_rl.distributed.weights import _SpaceHolderEnv
from re1_rl.env import ACTION_NAMES, FRAME_SHAPE_CHW
from re1_rl.episode_history import ACQUISITION_LOG_DIM, ROOM_HISTORY_DIM
from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM
from re1_rl.item_affordances import AFFORDANCES_DIM
from re1_rl.key_items import KEYS_HELD_DIM
from re1_rl.maps_files import MAPS_FILES_DIM
from re1_rl.milestone_features import MILESTONE_DIM
from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, INVENTORY_OBS_DIM, PROPRIO_DIM, ROOM_VISITED_DIM
from re1_rl.policy_config import POLICY_KWARGS
from re1_rl.room_signature import ENEMY_ROSTER_DIM
from re1_rl.spatial_encoder import SPATIAL_DIM, VISITED_SHAPE

# Privileged obs keys shipped in RE1Env (see policy_config fusion).
GUIDEBOOK_OBS_KEYS = frozenset(
    {
        "frame",
        "proprio",
        "goal",
        "spatial",
        "visited",
        "rooms_visited",
        "box",
        "inventory",
        "history",
        "acquisitions",
        "room_enemies",
        "keys_held",
        "affordances",
        "cutscene_ledger",
        "milestones",
        "maps_files",
    }
)


def _make_fake_ckpt(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("data", "{}")
        zf.writestr("policy.pth", "x")


def test_make_re1_spaces_guidebook_keys_match_env() -> None:
    obs_space, act_space = make_re1_spaces()
    assert set(obs_space.spaces.keys()) == GUIDEBOOK_OBS_KEYS
    assert int(act_space.n) == len(ACTION_NAMES)
    assert obs_space["proprio"].shape == (PROPRIO_DIM,)
    assert obs_space["goal"].shape == (GOAL_DIM,)
    assert obs_space["spatial"].shape == (SPATIAL_DIM,)
    assert obs_space["visited"].shape == VISITED_SHAPE
    assert obs_space["rooms_visited"].shape == (ROOM_VISITED_DIM,)
    assert obs_space["box"].shape == (BOX_DIM,)
    assert obs_space["inventory"].shape == (INVENTORY_OBS_DIM,)
    assert obs_space["history"].shape == (ROOM_HISTORY_DIM,)
    assert obs_space["acquisitions"].shape == (ACQUISITION_LOG_DIM,)
    assert obs_space["room_enemies"].shape == (ENEMY_ROSTER_DIM,)
    assert obs_space["keys_held"].shape == (KEYS_HELD_DIM,)
    assert obs_space["affordances"].shape == (AFFORDANCES_DIM,)
    assert obs_space["cutscene_ledger"].shape == (CUTSCENE_LEDGER_DIM,)
    assert obs_space["milestones"].shape == (MILESTONE_DIM,)
    assert obs_space["maps_files"].shape == (MAPS_FILES_DIM,)


def test_resolve_resume_path_uses_latest_json(tmp_path: Path) -> None:
    ckpt_dir = tmp_path / "data" / "checkpoints" / "parity_run"
    pointed = ckpt_dir / "ppo_re1_12345_steps.zip"
    older = ckpt_dir / "ppo_re1_100_steps.zip"
    _make_fake_ckpt(older)
    _make_fake_ckpt(pointed)
    write_latest_pointer(ckpt_dir, pointed, steps=12345)

    resolved = resolve_resume_path(None, project_root=tmp_path, ckpt_dir=ckpt_dir)
    assert resolved is not None
    assert resolved.resolve() == pointed.resolve()


def test_distributed_ppo_hyperparams_match_async_fleet() -> None:
    assert PPO_HYPERPARAMS == dict(
        n_steps=256,
        batch_size=512,
        n_epochs=4,
        learning_rate=3e-4,
        gamma=0.995,
        ent_coef=0.01,
    )


def test_make_re1_policy_spaces_frame_is_chw() -> None:
    obs_space, _ = make_re1_policy_spaces()
    assert obs_space["frame"].shape == FRAME_SHAPE_CHW


def test_load_async_learner_fresh_uses_policy_chw_spaces() -> None:
    from sb3_contrib import MaskablePPO

    model = load_async_learner(device="cpu", resume=None, tb_log=None)
    assert isinstance(model, MaskablePPO)
    policy_obs, act_space = make_re1_policy_spaces()
    assert model.observation_space["frame"].shape == FRAME_SHAPE_CHW
    assert set(model.observation_space.spaces.keys()) == set(policy_obs.spaces.keys())
    assert int(model.action_space.n) == int(act_space.n)


def test_load_async_learner_transplants_missing_obs_key(tmp_path: Path) -> None:
    """Legacy checkpoint missing an obs key must transplant into current spaces."""
    policy_obs, act_space = make_re1_policy_spaces()
    reduced = spaces.Dict(
        {k: v for k, v in policy_obs.spaces.items() if k != "keys_held"}
    )
    legacy = PPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(reduced, act_space),
        policy_kwargs=POLICY_KWARGS,
        n_steps=8,
        batch_size=8,
        n_epochs=1,
        learning_rate=3e-4,
        gamma=0.995,
        ent_coef=0.01,
        device="cpu",
        verbose=0,
    )
    legacy.num_timesteps = 1234
    ckpt = tmp_path / "legacy_missing_keys_held.zip"
    legacy.save(str(ckpt))

    model = load_async_learner(device="cpu", resume=ckpt, tb_log=None)
    assert "keys_held" in model.observation_space.spaces
    assert model.observation_space["frame"].shape == FRAME_SHAPE_CHW
    assert set(model.observation_space.spaces.keys()) == set(policy_obs.spaces.keys())
    assert int(model.num_timesteps) == 1234


def test_distributed_build_learner_reuses_load_async_learner(tmp_path: Path, monkeypatch) -> None:
    """Distributed learner build must call load_async_learner (no bare PPO.load)."""
    import re1_rl.checkpoint_io as checkpoint_io
    import scripts.distributed_train_parallel as dtp

    calls: list[dict] = []
    sentinel = SimpleNamespace()

    def _fake_load(*, device, resume, tb_log):
        calls.append({"device": device, "resume": resume, "tb_log": tb_log})
        return sentinel

    monkeypatch.setattr(dtp, "load_async_learner", _fake_load)
    monkeypatch.setattr(dtp, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(checkpoint_io, "resolve_resume_path", lambda *a, **k: None)

    args = argparse.Namespace(
        resume=None,
        run_name="parity_run",
        n_steps=128,
        machine_name="test",
    )
    model, ckpt_dir = dtp._build_learner_model(args, "cpu")
    assert model is sentinel
    assert ckpt_dir == tmp_path / "data" / "checkpoints" / "parity_run"
    assert len(calls) == 1
    assert calls[0]["device"] == "cpu"
    assert calls[0]["resume"] is None
    assert calls[0]["tb_log"].endswith("parity_run")
    assert sentinel.n_steps == 128
