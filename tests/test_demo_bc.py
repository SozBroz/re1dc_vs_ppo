"""Human demo recording format + BC auxiliary term."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import torch as th

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.demo_bc import DemoBCAux
from re1_rl.demo_record import (
    DemoEpisode,
    buttons_to_action,
    demo_dir_signature,
    load_demo_dataset,
    write_demo,
)
from re1_rl.env import ACTION_BUTTON_MAP
from re1_rl.sticky_input import INTERACT_ACTION, StickyInputState, empty_sticky

N_ACTIONS = 45


def _sticky(**held: bool) -> dict[str, bool]:
    out = empty_sticky()
    out.update(held)
    return out


def test_buttons_to_action_basic_moves() -> None:
    idle = empty_sticky()
    assert buttons_to_action({}, idle, button_map=ACTION_BUTTON_MAP) == 0
    assert buttons_to_action({"up": True}, idle, button_map=ACTION_BUTTON_MAP) == 1
    assert buttons_to_action({"up": True, "square": True}, idle, button_map=ACTION_BUTTON_MAP) == 5
    assert buttons_to_action({"down": True}, idle, button_map=ACTION_BUTTON_MAP) == 2
    assert buttons_to_action({"left": True}, idle, button_map=ACTION_BUTTON_MAP) == 3
    assert buttons_to_action({"right": True}, idle, button_map=ACTION_BUTTON_MAP) == 4
    assert buttons_to_action({"cross": True}, idle, button_map=ACTION_BUTTON_MAP) == INTERACT_ACTION
    assert buttons_to_action({"cross": True, "r1": True}, idle, button_map=ACTION_BUTTON_MAP) == 7


def test_buttons_to_action_walk_and_turn_uses_latch() -> None:
    # Already walking forward, human adds left: turn_left keeps the forward latch.
    walking = _sticky(up=True)
    a = buttons_to_action({"up": True, "left": True}, walking, button_map=ACTION_BUTTON_MAP)
    assert a == 3
    probe = StickyInputState()
    probe._sticky.update(walking)  # noqa: SLF001
    got, _, _ = probe.apply(a, ACTION_BUTTON_MAP)
    assert got["up"] and got["left"]
    # Releasing the turn while still walking: forward clears left/right, keeps up.
    turning = _sticky(up=True, left=True)
    assert buttons_to_action({"up": True}, turning, button_map=ACTION_BUTTON_MAP) == 1
    # Flip turn side while walking: turn_left clears right, keeps up.
    turning_right = _sticky(up=True, right=True)
    assert buttons_to_action({"up": True, "left": True}, turning_right, button_map=ACTION_BUTTON_MAP) == 3
    # Run+turn → run straight (no noop).
    running_turn = _sticky(up=True, left=True, square=True)
    assert buttons_to_action(
        {"up": True, "square": True}, running_turn, button_map=ACTION_BUTTON_MAP
    ) == 5
    # Run -> walk drops square via forward.
    running = _sticky(up=True, square=True)
    assert buttons_to_action({"up": True}, running, button_map=ACTION_BUTTON_MAP) == 1


def _fake_obs(t: int) -> dict[str, np.ndarray]:
    return {
        "frame": np.full((63, 84, 4), t % 255, dtype=np.uint8),
        "proprio": np.full((6,), 0.1 * t, dtype=np.float32),
    }


def _write_episode(path: Path, *, n: int, success: bool, action: int = 1) -> Path:
    ep = DemoEpisode()
    for t in range(n):
        mask = np.zeros(N_ACTIONS, dtype=bool)
        mask[[0, 1, 2, 3, 4, 5, 9]] = True
        ep.add(_fake_obs(t), action, mask)
        ep.note_reward(0.01 * t)
    meta = {"obs_schema_version": 2, "n_actions": N_ACTIONS, "success": success, "start_cell": "pl79"}
    return write_demo(path, ep, meta)


def test_demo_round_trip_and_frame_layout(tmp_path: Path) -> None:
    p = _write_episode(tmp_path / "pl79_a_ok.npz", n=5, success=True)
    _write_episode(tmp_path / "pl79_b_fail.npz", n=3, success=False)
    with np.load(p, allow_pickle=False) as data:
        meta = json.loads(str(data["meta"]))
        assert meta["steps"] == 5
        assert data["obs__frame"].shape == (5, 63, 84, 4)
        assert data["reward"].shape == (5,)
    shapes = {"frame": (4, 63, 84), "proprio": (6,)}
    ds = load_demo_dataset(tmp_path, obs_shapes=shapes, n_actions=N_ACTIONS, obs_schema_version=2)
    assert ds is not None
    assert len(ds) == 5
    assert ds.obs["frame"].shape == (5, 4, 63, 84)
    assert ds.obs["frame"][3, 0, 0, 0] == 3
    assert ds.masks.shape == (5, N_ACTIONS)
    ds_all = load_demo_dataset(
        tmp_path, obs_shapes=shapes, n_actions=N_ACTIONS, obs_schema_version=2, successful_only=False
    )
    assert ds_all is not None and len(ds_all) == 8
    # Schema / action-count mismatches are skipped, never merged.
    assert load_demo_dataset(tmp_path, obs_shapes=shapes, n_actions=44, obs_schema_version=2) is None
    assert load_demo_dataset(tmp_path, obs_shapes=shapes, n_actions=N_ACTIONS, obs_schema_version=3) is None
    assert len(demo_dir_signature(tmp_path)) == 2


class _TinyMaskedPolicy:
    """Stand-in for MaskableActorCriticPolicy.get_distribution on a tiny Dict obs."""

    def __init__(self, n_actions: int) -> None:
        self.linear = th.nn.Linear(6, n_actions)

    def parameters(self):
        return self.linear.parameters()

    def get_distribution(self, obs: dict[str, th.Tensor], action_masks: th.Tensor | None = None):
        from sb3_contrib.common.maskable.distributions import MaskableCategoricalDistribution

        logits = self.linear(obs["proprio"].float())
        dist = MaskableCategoricalDistribution(logits.shape[-1])
        dist.proba_distribution(action_logits=logits)
        if action_masks is not None:
            dist.apply_masking(action_masks)
        return dist


def test_demo_bc_aux_loss_trains_and_hot_reloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_episode(tmp_path / "pl79_a_ok.npz", n=6, success=True, action=1)
    shapes = {"frame": (4, 63, 84), "proprio": (6,)}
    aux = DemoBCAux(
        tmp_path,
        obs_shapes=shapes,
        n_actions=N_ACTIONS,
        device="cpu",
        obs_schema_version=2,
        coef=0.5,
        coef_decay=0.5,
        coef_min=0.1,
        batch_size=4,
        reload_every=2,
        seed=0,
    )
    assert aux.active and aux.n_samples == 6 and aux.n_files == 1
    policy = _TinyMaskedPolicy(N_ACTIONS)
    opt = th.optim.Adam(policy.parameters(), lr=0.1)
    first = None
    for _ in range(30):
        out = aux.loss(policy)
        assert out is not None
        loss, stats = out
        first = float(loss.detach()) if first is None else first
        opt.zero_grad()
        loss.backward()
        opt.step()
    loss, stats = aux.loss(policy)  # type: ignore[misc]
    assert float(loss.detach()) < first
    assert stats["train/bc_acc"] == 1.0
    aux.on_train_call()
    assert aux.coef == pytest.approx(0.25)
    _write_episode(tmp_path / "pl79_b_ok.npz", n=4, success=True, action=2)
    aux.on_train_call()  # 2nd call -> reload_every hit
    assert aux.n_samples == 10 and aux.n_files == 2
    aux.on_train_call()
    aux.on_train_call()
    assert aux.coef == pytest.approx(0.1)


def test_combat_ppo_train_adds_bc_term_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_combat_efficient_distributed import _fake_rollout, _tiny_combat_model
    from re1_rl.distributed.learner_train import train_on_rollouts
    from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION, make_re1_spaces
    from re1_rl.env import ACTION_NAMES

    env_obs_space, _ = make_re1_spaces()
    ep = DemoEpisode()
    for t in range(6):
        obs = {k: np.zeros(sp.shape, dtype=sp.dtype) for k, sp in env_obs_space.spaces.items()}
        obs["frame"][:] = t
        mask = np.zeros(len(ACTION_NAMES), dtype=bool)
        mask[:6] = True
        ep.add(obs, 1, mask)
    write_demo(
        tmp_path / "pl79_x_ok.npz",
        ep,
        {"obs_schema_version": int(OBS_SCHEMA_VERSION), "n_actions": len(ACTION_NAMES), "success": True},
    )
    monkeypatch.setenv("RE1_BC_DEMO_DIR", str(tmp_path))
    monkeypatch.setenv("RE1_BC_BATCH", "4")
    monkeypatch.setenv("RE1_BC_COEF", "0.5")
    model = _tiny_combat_model()
    steps = train_on_rollouts(model, [_fake_rollout()])
    assert steps == 16
    aux = model._demo_bc  # noqa: SLF001
    assert aux is not None and aux.active and aux.n_samples == 6
    assert aux.n_batches >= 1  # one BC batch per PPO minibatch
    assert aux.coef == pytest.approx(0.5)
    for p in model.policy.parameters():
        assert th.isfinite(p).all()
    # Never pickled with the checkpoint.
    assert "_demo_bc" in model._excluded_save_params()  # noqa: SLF001


def test_demo_bc_aux_from_env_disabled_without_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RE1_BC_DEMO_DIR", raising=False)
    assert DemoBCAux.from_env(obs_shapes={"proprio": (6,)}, n_actions=N_ACTIONS, device="cpu") is None
