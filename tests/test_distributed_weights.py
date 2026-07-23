"""Weight store and learner HTTP surface."""

from __future__ import annotations

import json
import queue
import sys
import urllib.error
import urllib.request
from pathlib import Path

import gymnasium as gym
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.learner_server import LearnerState, start_learner_server
from re1_rl.distributed.rollout_codec import encode_rollout
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.spaces import make_re1_spaces
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, PROPRIO_DIM
from re1_rl.distributed.weights import (
    export_policy_state_dict,
    policy_bytes_from_state_dict,
    state_dict_from_policy_bytes,
)
from re1_rl.policy_config import POLICY_KWARGS


def _tiny_model() -> PPO:
    obs_space, act_space = make_re1_spaces()

    class _StubEnv(gym.Env):
        def __init__(self) -> None:
            super().__init__()
            self.observation_space = obs_space
            self.action_space = act_space

        def reset(self, *, seed=None, options=None):
            return {k: s.sample() for k, s in self.observation_space.items()}, {}

        def step(self, action):
            obs, _ = self.reset()
            return obs, 0.0, False, False, {}

    env = DummyVecEnv([lambda: _StubEnv()])
    return PPO(
        "MultiInputPolicy",
        env,
        policy_kwargs=POLICY_KWARGS,
        n_steps=8,
        batch_size=8,
        n_epochs=1,
        device="cpu",
        verbose=0,
    )


def test_policy_bytes_roundtrip() -> None:
    model = _tiny_model()
    sd = export_policy_state_dict(model)
    blob = policy_bytes_from_state_dict(sd)
    restored = state_dict_from_policy_bytes(blob)
    for key in sd:
        assert torch.allclose(sd[key], restored[key])


def test_learner_http_weights_and_rollout() -> None:
    store = WeightStore()
    rollout_q: queue.Queue = queue.Queue()
    state = LearnerState(store, rollout_q, machine_name="test", max_staleness=1)
    model = _tiny_model()
    version = store.publish(export_policy_state_dict(model))
    state.set_current_version(version)
    state.max_staleness = 0

    server, _ = start_learner_server(state, host="127.0.0.1", port=0)
    host, port = server.server_address
    base = f"http://{host}:{port}"

    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as resp:
            assert resp.status == 200

        with urllib.request.urlopen(base + "/weights", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["policy_version"] == version
            assert payload["policy_bytes"]

        rollout = WorkerRollout(
            worker_id="w1",
            policy_version=version,
            n_envs=1,
            n_steps=2,
            obs={
                "frame": np.zeros((2, 1, 63, 84, 4), dtype=np.uint8),
                "proprio": np.zeros((2, 1, PROPRIO_DIM), dtype=np.float32),
                "goal": np.zeros((2, 1, GOAL_DIM), dtype=np.float32),
                "spatial": np.zeros((2, 1, 119), dtype=np.float32),
                "visited": np.zeros((2, 1, 16, 16, 1), dtype=np.float32),
                "box": np.zeros((2, 1, BOX_DIM), dtype=np.float32),
            },
            actions=np.zeros((2, 1), dtype=np.int64),
            rewards=np.zeros((2, 1), dtype=np.float32),
            dones=np.zeros((2, 1), dtype=np.bool_),
            values=np.zeros((2, 1), dtype=np.float32),
            log_probs=np.zeros((2, 1), dtype=np.float32),
            last_values=np.zeros((1,), dtype=np.float32),
            action_masks=np.ones((2, 1, 10), dtype=np.bool_),
        )
        req = urllib.request.Request(
            base + "/rollout",
            data=encode_rollout(rollout),
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert json.loads(resp.read())["accepted"] is True
        assert rollout_q.get_nowait().worker_id == "w1"

        stale = WorkerRollout(
            worker_id="w1",
            policy_version=max(version - 1, 0),
            n_envs=1,
            n_steps=2,
            obs=rollout.obs,
            actions=rollout.actions,
            rewards=rollout.rewards,
            dones=rollout.dones,
            values=rollout.values,
            log_probs=rollout.log_probs,
            last_values=rollout.last_values,
            action_masks=rollout.action_masks,
        )
        req2 = urllib.request.Request(
            base + "/rollout",
            data=encode_rollout(stale),
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req2, timeout=5)
            raise AssertionError("expected stale rollout rejection")
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
    finally:
        server.shutdown()
