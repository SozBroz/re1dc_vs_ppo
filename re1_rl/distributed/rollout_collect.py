"""Worker-side rollout collection using a local inference mirror."""

from __future__ import annotations

from typing import Any

import numpy as np
from stable_baselines3.common.vec_env import VecEnv

from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.rollout_types import WorkerRollout


def collect_rollout(
    vec_env: VecEnv,
    policy: InferencePolicy,
    *,
    n_steps: int,
    worker_id: str,
) -> WorkerRollout:
    n_envs = vec_env.num_envs
    obs = vec_env.reset()
    policy_version = policy.policy_version

    obs_bufs: dict[str, np.ndarray] = {}
    for key, arr in obs.items():
        obs_bufs[key] = np.zeros((n_steps, n_envs, *arr.shape[1:]), dtype=arr.dtype)

    actions = np.zeros((n_steps, n_envs), dtype=np.int64)
    rewards = np.zeros((n_steps, n_envs), dtype=np.float32)
    dones = np.zeros((n_steps, n_envs), dtype=np.bool_)
    values = np.zeros((n_steps, n_envs), dtype=np.float32)
    log_probs = np.zeros((n_steps, n_envs), dtype=np.float32)

    episode_infos: list[dict[str, Any]] = []

    for step in range(n_steps):
        act, val, lp = policy.predict_batch(obs)
        actions[step] = act
        values[step] = val
        log_probs[step] = lp

        obs, rew, done, infos = vec_env.step(act)
        rewards[step] = rew
        dones[step] = done
        for key in obs_bufs:
            obs_bufs[key][step] = obs[key]
        for info in infos:
            if info:
                episode_infos.append(dict(info))

    last_values = policy.predict_values(obs)

    return WorkerRollout(
        worker_id=worker_id,
        policy_version=policy_version,
        n_envs=n_envs,
        n_steps=n_steps,
        obs=obs_bufs,
        actions=actions,
        rewards=rewards,
        dones=dones,
        values=values,
        log_probs=log_probs,
        last_values=last_values,
        episode_infos=episode_infos,
    )
