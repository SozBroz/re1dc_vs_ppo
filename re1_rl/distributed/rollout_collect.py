"""Worker-side rollout collection using a local inference mirror."""

from __future__ import annotations

from typing import Any

import numpy as np
from stable_baselines3.common.vec_env import VecEnv

from re1_rl.combat_targets import (
    COMBAT_TARGET_DIM,
    WORLD_EVENT_DIM,
    empty_combat_target,
    empty_world_event_mask,
    empty_world_event_target,
    pack_combat_target_from_info,
    pack_world_event_target_from_info,
)
from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.rollout_types import WorkerRollout, normalize_curriculum_id
from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION
from re1_rl.modality_ablations import MOD_DROP_DIM, ModDropEpisodeState
from re1_rl.modality_config import mod_drop_enabled


def _stack_action_masks(vec_env: VecEnv) -> np.ndarray:
    """Fetch per-env bool masks via ActionMasker / env.action_masks()."""
    masks = vec_env.env_method("action_masks")
    return np.stack([np.asarray(m, dtype=bool) for m in masks], axis=0)


def _pack_step_targets(
    actions: np.ndarray,
    infos: list[dict[str, Any]],
    *,
    prev_rooms: list[str | None],
    prev_hps: list[float | None],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str | None], list[float | None]]:
    n_envs = len(actions)
    combat = np.zeros((n_envs, COMBAT_TARGET_DIM), dtype=np.float32)
    world = np.zeros((n_envs, WORLD_EVENT_DIM), dtype=np.float32)
    wmask = np.zeros((n_envs, WORLD_EVENT_DIM), dtype=np.float32)
    next_rooms: list[str | None] = []
    next_hps: list[float | None] = []
    for i in range(n_envs):
        info = infos[i] if i < len(infos) and isinstance(infos[i], dict) else {}
        combat[i] = pack_combat_target_from_info(
            int(actions[i]), info, prev_hp=prev_hps[i] if i < len(prev_hps) else None
        )
        y, m = pack_world_event_target_from_info(
            int(actions[i]),
            info,
            prev_room=prev_rooms[i] if i < len(prev_rooms) else None,
        )
        world[i] = y
        wmask[i] = m
        room = info.get("room_id")
        next_rooms.append(str(room) if room is not None else prev_rooms[i] if i < len(prev_rooms) else None)
        hp = info.get("hp")
        next_hps.append(float(hp) if hp is not None else (prev_hps[i] if i < len(prev_hps) else None))
    return combat, world, wmask, next_rooms, next_hps


def collect_rollout(
    vec_env: VecEnv,
    policy: InferencePolicy,
    *,
    n_steps: int,
    worker_id: str,
    obs: dict[str, np.ndarray] | None = None,
    curriculum: str = "",
) -> tuple[WorkerRollout, dict[str, np.ndarray]]:
    """Collect ``n_steps`` lockstep transitions.

    Pass ``obs=None`` to ``reset()`` once at the start of a session. Pass the
    returned next-obs on subsequent calls so episodes continue across horizons
    (parity with desync actors).

    Aux targets align post-action outcomes to the pre-action obs already stored
    for that transition (no same-transition outcome leakage into obs).
    """
    n_envs = vec_env.num_envs
    if obs is None:
        obs = vec_env.reset()
    policy_version = policy.policy_version
    curriculum_id = normalize_curriculum_id(curriculum)

    obs_bufs: dict[str, np.ndarray] = {}
    for key, arr in obs.items():
        obs_bufs[key] = np.zeros((n_steps, n_envs, *arr.shape[1:]), dtype=arr.dtype)

    actions = np.zeros((n_steps, n_envs), dtype=np.int64)
    rewards = np.zeros((n_steps, n_envs), dtype=np.float32)
    dones = np.zeros((n_steps, n_envs), dtype=np.bool_)
    values = np.zeros((n_steps, n_envs), dtype=np.float32)
    log_probs = np.zeros((n_steps, n_envs), dtype=np.float32)
    n_actions = int(vec_env.action_space.n)
    action_masks = np.zeros((n_steps, n_envs, n_actions), dtype=np.bool_)
    combat_targets = np.zeros((n_steps, n_envs, COMBAT_TARGET_DIM), dtype=np.float32)
    world_event_targets = np.zeros((n_steps, n_envs, WORLD_EVENT_DIM), dtype=np.float32)
    world_event_masks = np.zeros((n_steps, n_envs, WORLD_EVENT_DIM), dtype=np.float32)
    for e in range(n_envs):
        combat_targets[:, e] = empty_combat_target()
        world_event_targets[:, e] = empty_world_event_target()
        world_event_masks[:, e] = empty_world_event_mask()

    use_mod_drop = mod_drop_enabled()
    mod_drop_state = ModDropEpisodeState(n_envs) if use_mod_drop else None
    mod_drop_masks = (
        np.ones((n_steps, n_envs, MOD_DROP_DIM), dtype=np.float32) if use_mod_drop else None
    )

    episode_infos: list[dict[str, Any]] = []
    prev_rooms: list[str | None] = [None] * n_envs
    prev_hps: list[float | None] = [None] * n_envs

    for step in range(n_steps):
        masks = _stack_action_masks(vec_env)
        action_masks[step] = masks
        # Mask chosen before action; fixed for episode; stored for PPO epochs.
        if mod_drop_state is not None and mod_drop_masks is not None:
            mod_drop_masks[step] = mod_drop_state.masks
            policy.set_mod_drop_masks(mod_drop_state.masks)
        act, val, lp = policy.predict_masked_batch(obs, masks)
        actions[step] = act
        values[step] = val
        log_probs[step] = lp

        for key in obs_bufs:
            obs_bufs[key][step] = obs[key]

        obs, rew, done, infos = vec_env.step(act)
        rewards[step] = rew
        dones[step] = done
        combat, world, wmask, prev_rooms, prev_hps = _pack_step_targets(
            act, list(infos), prev_rooms=prev_rooms, prev_hps=prev_hps
        )
        combat_targets[step] = combat
        world_event_targets[step] = world
        world_event_masks[step] = wmask
        for info in infos:
            if info:
                episode_infos.append(dict(info))
        # Reset room/hp tracking on episode boundaries for next transition.
        for i, d in enumerate(done):
            if d:
                prev_rooms[i] = None
                prev_hps[i] = None
        if mod_drop_state is not None:
            mod_drop_state.on_dones(done)

    if use_mod_drop:
        policy.set_mod_drop_masks(None)
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
        combat_targets=combat_targets,
        world_event_targets=world_event_targets,
        world_event_masks=world_event_masks,
        mod_drop_masks=mod_drop_masks,
        curriculum_id=curriculum_id,
        obs_schema_version=int(OBS_SCHEMA_VERSION),
    )
    return rollout, obs
