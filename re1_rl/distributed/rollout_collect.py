"""Worker-side rollout collection using a local inference mirror."""

from __future__ import annotations

from typing import Any

import numpy as np
from stable_baselines3.common.vec_env import VecEnv

from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.reward import softlock_reward_from_breakdown


def _stack_action_masks(vec_env: VecEnv) -> np.ndarray:
    """Fetch per-env bool masks via ActionMasker / env.action_masks()."""
    masks = vec_env.env_method("action_masks")
    return np.stack([np.asarray(m, dtype=bool) for m in masks], axis=0)


def _softlock_from_infos(infos: list[dict[str, Any]], n_envs: int) -> np.ndarray:
    out = np.zeros(n_envs, dtype=np.float32)
    for i, info in enumerate(infos):
        if not info:
            continue
        out[i] = softlock_reward_from_breakdown(info.get("reward_breakdown"))
    return out


def collect_rollout(
    vec_env: VecEnv,
    policy: InferencePolicy,
    *,
    n_steps: int,
    worker_id: str,
    obs: dict[str, np.ndarray] | None = None,
) -> tuple[WorkerRollout, dict[str, np.ndarray]]:
    """Collect ``n_steps`` lockstep transitions.

    Pass ``obs=None`` to ``reset()`` once at the start of a session. Pass the
    returned next-obs on subsequent calls so episodes continue across horizons
    (parity with desync actors).
    """
    n_envs = vec_env.num_envs
    if obs is None:
        obs = vec_env.reset()
    policy_version = policy.policy_version

    obs_bufs: dict[str, np.ndarray] = {}
    for key, arr in obs.items():
        obs_bufs[key] = np.zeros((n_steps, n_envs, *arr.shape[1:]), dtype=arr.dtype)

    actions = np.zeros((n_steps, n_envs), dtype=np.int64)
    rewards = np.zeros((n_steps, n_envs), dtype=np.float32)
    rewards_softlock = np.zeros((n_steps, n_envs), dtype=np.float32)
    dones = np.zeros((n_steps, n_envs), dtype=np.bool_)
    values = np.zeros((n_steps, n_envs), dtype=np.float32)
    log_probs = np.zeros((n_steps, n_envs), dtype=np.float32)
    n_actions = int(vec_env.action_space.n)
    action_masks = np.zeros((n_steps, n_envs, n_actions), dtype=np.bool_)

    episode_infos: list[dict[str, Any]] = []

    for step in range(n_steps):
        masks = _stack_action_masks(vec_env)
        action_masks[step] = masks
        act, val, lp = policy.predict_masked_batch(obs, masks)
        actions[step] = act
        values[step] = val
        log_probs[step] = lp

        for key in obs_bufs:
            obs_bufs[key][step] = obs[key]

        obs, rew, done, infos = vec_env.step(act)
        rewards[step] = rew
        rewards_softlock[step] = _softlock_from_infos(list(infos), n_envs)
        dones[step] = done
        for info in infos:
            if info:
                episode_infos.append(dict(info))

    last_values = policy.predict_values(obs)

    rollout = WorkerRollout(
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
        action_masks=action_masks,
        episode_infos=episode_infos,
        rewards_softlock=rewards_softlock,
    )
    return rollout, obs
