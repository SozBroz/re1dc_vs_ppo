"""Parity guards: distributed spaces/resume match monolithic fleet (no BizHawk)."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.async_fleet import PPO_HYPERPARAMS
from re1_rl.checkpoint_io import resolve_resume_path, write_latest_pointer
from re1_rl.distributed.spaces import make_re1_spaces
from re1_rl.env import ACTION_NAMES
from re1_rl.episode_history import ACQUISITION_LOG_DIM, ROOM_HISTORY_DIM
from re1_rl.key_items import KEYS_HELD_DIM
from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, INVENTORY_OBS_DIM, PROPRIO_DIM, ROOM_VISITED_DIM
from re1_rl.room_signature import ENEMY_ROSTER_DIM
from re1_rl.spatial_encoder import SPATIAL_DIM, VISITED_SHAPE

# Guidebook / episode-history keys shipped in RE1Env (see policy_config fusion).
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
        gamma=0.99,
        ent_coef=0.01,
    )
