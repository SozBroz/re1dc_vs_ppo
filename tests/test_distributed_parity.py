"""Parity guards: distributed spaces/resume match monolithic fleet (no BizHawk)."""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch
from torch import nn
from gymnasium import spaces
from stable_baselines3 import PPO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.async_fleet import (
    PPO_HYPERPARAMS,
    _copy_compatible_policy_weights,
    load_async_learner,
)
from re1_rl.checkpoint_io import resolve_resume_path, write_latest_pointer
from re1_rl.distributed.spaces import make_re1_policy_spaces, make_re1_spaces
from re1_rl.distributed.weights import _SpaceHolderEnv
from re1_rl.env import ACTION_NAMES, FRAME_SHAPE_CHW
from re1_rl.episode_history import ACQUISITION_LOG_DIM, ROOM_HISTORY_DIM
from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM
from re1_rl.item_affordances import AFFORDANCES_DIM
from re1_rl.world_state_encoder import WORLD_STATE_DIM
from re1_rl.key_items import KEYS_HELD_DIM
from re1_rl.maps_files import MAPS_FILES_DIM
from re1_rl.milestone_features import MILESTONE_DIM
from re1_rl.obs_encoder import (
    BOX_DIM,
    GOAL_DIM,
    INVENTORY_OBS_DIM,
    LOGISTICS_DIM,
    PROPRIO_DIM,
    ROOM_VISITED_DIM,
)
from re1_rl.weapon_damage import LAST_ATTACK_DIM, WEAPON_CARD_DIM
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
        "logistics",
        "weapon_card",
        "last_attack",
        "history",
        "acquisitions",
        "room_enemies",
        "keys_held",
        "affordances",
        "world_state",
        "cutscene_ledger",
        "milestones",
        "maps_files",
        "named_state",
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
    assert obs_space["logistics"].shape == (LOGISTICS_DIM,)
    assert obs_space["weapon_card"].shape == (WEAPON_CARD_DIM,)
    assert obs_space["last_attack"].shape == (LAST_ATTACK_DIM,)
    assert obs_space["history"].shape == (ROOM_HISTORY_DIM,)
    assert obs_space["acquisitions"].shape == (ACQUISITION_LOG_DIM,)
    assert obs_space["room_enemies"].shape == (ENEMY_ROSTER_DIM,)
    assert obs_space["keys_held"].shape == (KEYS_HELD_DIM,)
    assert obs_space["affordances"].shape == (AFFORDANCES_DIM,)
    assert obs_space["world_state"].shape == (WORLD_STATE_DIM,)
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
    from re1_rl.reward import RL_GAMMA, gamma_for_emulated_half_life, RAILS_CREDIT_HALF_LIFE_S

    assert PPO_HYPERPARAMS == dict(
        n_steps=1024,
        batch_size=512,
        n_epochs=4,
        learning_rate=3e-4,
        gamma=RL_GAMMA,
        ent_coef=0.005,
    )
    assert RL_GAMMA == gamma_for_emulated_half_life(RAILS_CREDIT_HALF_LIFE_S)


def test_make_re1_policy_spaces_frame_is_chw() -> None:
    obs_space, _ = make_re1_policy_spaces()
    assert obs_space["frame"].shape == FRAME_SHAPE_CHW


def test_legacy_action_head_transplant_clones_attack_with_low_prior() -> None:
    class Policy(nn.Module):
        def __init__(self, actions: int) -> None:
            super().__init__()
            self.action_net = nn.Linear(3, actions)

    old = Policy(44)
    new = Policy(45)
    with torch.no_grad():
        old.action_net.weight.copy_(torch.arange(132).reshape(44, 3))
        old.action_net.bias.copy_(torch.arange(44))

    _copy_compatible_policy_weights(old, new)

    assert torch.equal(new.action_net.weight[:44], old.action_net.weight)
    assert torch.equal(new.action_net.bias[:44], old.action_net.bias)
    assert torch.equal(new.action_net.weight[44], old.action_net.weight[8])
    assert np.isclose(
        float(new.action_net.bias[44].detach()),
        float(old.action_net.bias[8].detach()) - np.log(100.0),
    )


def test_goal_mlp_transplant_zero_pads_new_cell_time_column() -> None:
    class Extractor(nn.Module):
        def __init__(self, in_features: int) -> None:
            super().__init__()
            self.goal_mlp = nn.Sequential(nn.Linear(in_features, 4), nn.ReLU())

    class Policy(nn.Module):
        def __init__(self, in_features: int) -> None:
            super().__init__()
            self.features_extractor = Extractor(in_features)

    old = Policy(28)
    new = Policy(29)
    with torch.no_grad():
        old.features_extractor.goal_mlp[0].weight.fill_(3.0)
        old.features_extractor.goal_mlp[0].bias.fill_(4.0)
    _copy_compatible_policy_weights(old, new)
    weight = new.features_extractor.goal_mlp[0].weight
    assert weight.shape == (4, 29)
    assert torch.equal(weight[:, :28], old.features_extractor.goal_mlp[0].weight)
    assert torch.equal(weight[:, 28], torch.zeros(4))
    assert torch.equal(
        new.features_extractor.goal_mlp[0].bias,
        old.features_extractor.goal_mlp[0].bias,
    )


def test_goal_lookahead_transplant_preserves_legacy_goal_tower() -> None:
    class Extractor(nn.Module):
        def __init__(self, *, with_lookahead: bool) -> None:
            super().__init__()
            self.goal_mlp = nn.Sequential(nn.Linear(28, 4), nn.ReLU())
            if with_lookahead:
                self.goal_lookahead_out = nn.Linear(8, 4)

    class Policy(nn.Module):
        def __init__(self, *, with_lookahead: bool) -> None:
            super().__init__()
            self.features_extractor = Extractor(with_lookahead=with_lookahead)

    old = Policy(with_lookahead=False)
    new = Policy(with_lookahead=True)
    lookahead_before = new.features_extractor.goal_lookahead_out.weight.detach().clone()
    with torch.no_grad():
        old.features_extractor.goal_mlp[0].weight.fill_(3.0)
        old.features_extractor.goal_mlp[0].bias.fill_(4.0)
    _copy_compatible_policy_weights(old, new)
    weight = new.features_extractor.goal_mlp[0].weight
    assert torch.equal(weight, old.features_extractor.goal_mlp[0].weight)
    assert torch.equal(
        new.features_extractor.goal_mlp[0].bias,
        old.features_extractor.goal_mlp[0].bias,
    )
    assert torch.equal(
        new.features_extractor.goal_lookahead_out.weight,
        lookahead_before,
    )


def test_45_action_head_reorder_transplant_preserves_action_semantics() -> None:
    old_action_names = (
        "noop",
        "forward",
        "back",
        "turn_left",
        "turn_right",
        "run_forward",
        "attack_up",
        "interact",
        "attack",
        "use",
        "equip",
        *(f"deposit_slot_{i}" for i in range(8)),
        *(f"withdraw_box_{i}" for i in range(16)),
        "combine",
        *(f"select_slot_{i}" for i in range(8)),
        "attack_down",
    )
    new_action_names = tuple(ACTION_NAMES)

    class Policy(nn.Module):
        def __init__(self, actions: int) -> None:
            super().__init__()
            self.action_net = nn.Linear(3, actions)
            self.value_net = nn.Linear(3, 1)

    old = Policy(45)
    new = Policy(45)
    with torch.no_grad():
        old.action_net.weight.copy_(
            torch.arange(45, dtype=torch.float32).unsqueeze(1).repeat(1, 3)
        )
        old.action_net.bias.copy_(torch.arange(45, dtype=torch.float32) + 1000)
        old.value_net.weight.fill_(123.0)
        old.value_net.bias.fill_(456.0)

    _copy_compatible_policy_weights(old, new)

    old_index = {name: index for index, name in enumerate(old_action_names)}
    assert len(old_index) == 45
    assert len(new_action_names) == 45
    for new_index, name in enumerate(new_action_names):
        source_index = old_index[name]
        assert torch.equal(
            new.action_net.weight[new_index], old.action_net.weight[source_index]
        )
        assert torch.equal(
            new.action_net.bias[new_index], old.action_net.bias[source_index]
        )
    assert torch.equal(new.value_net.weight, old.value_net.weight)
    assert torch.equal(new.value_net.bias, old.value_net.bias)


def test_47_action_head_downgrade_drops_diagonal_runs() -> None:
    old_action_names = (
        "noop",
        "forward",
        "back",
        "turn_left",
        "turn_right",
        "run_forward",
        "run_forward_left",
        "run_forward_right",
        "attack_up",
        "attack",
        "attack_down",
        "interact",
        "use",
        "equip",
        *(f"deposit_slot_{i}" for i in range(8)),
        *(f"withdraw_box_{i}" for i in range(16)),
        "combine",
        *(f"select_slot_{i}" for i in range(8)),
    )
    new_action_names = tuple(ACTION_NAMES)

    class Policy(nn.Module):
        def __init__(self, actions: int) -> None:
            super().__init__()
            self.action_net = nn.Linear(3, actions)

    old = Policy(47)
    new = Policy(45)
    with torch.no_grad():
        old.action_net.weight.copy_(
            torch.arange(141, dtype=torch.float32).reshape(47, 3)
        )
        old.action_net.bias.copy_(torch.arange(47, dtype=torch.float32))

    _copy_compatible_policy_weights(old, new)

    old_index = {name: index for index, name in enumerate(old_action_names)}
    for new_index, name in enumerate(new_action_names):
        assert torch.equal(
            new.action_net.weight[new_index],
            old.action_net.weight[old_index[name]],
        )
        assert torch.equal(
            new.action_net.bias[new_index],
            old.action_net.bias[old_index[name]],
        )


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
        ent_coef=0.005,
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


def test_load_async_learner_raw_transplants_new_goal_module(tmp_path: Path) -> None:
    policy_obs, act_space = make_re1_policy_spaces()
    donor = PPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs, act_space),
        policy_kwargs=POLICY_KWARGS,
        n_steps=8,
        batch_size=8,
        n_epochs=1,
        device="cpu",
        verbose=0,
    )
    donor.num_timesteps = 4321
    ckpt = tmp_path / "legacy_goal_module.zip"
    donor.save(str(ckpt))
    legacy_sd = {
        key: value
        for key, value in donor.policy.state_dict().items()
        if "goal_lookahead" not in key
    }
    policy_buf = io.BytesIO()
    torch.save(legacy_sd, policy_buf)
    rewritten = tmp_path / "rewritten.zip"
    with zipfile.ZipFile(ckpt) as source, zipfile.ZipFile(rewritten, "w") as dest:
        for info in source.infolist():
            if info.filename != "policy.pth":
                dest.writestr(info, source.read(info.filename))
        dest.writestr("policy.pth", policy_buf.getvalue())
    rewritten.replace(ckpt)

    model = load_async_learner(device="cpu", resume=ckpt, tb_log=None)
    assert int(model.num_timesteps) == 4321
    assert hasattr(model.policy.features_extractor, "goal_lookahead_token")
    assert model.observation_space["goal"].shape == (GOAL_DIM,)


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
