"""Worker rollout loop for local and remote machines."""

from __future__ import annotations

import queue
import threading
import time
from typing import TYPE_CHECKING

from stable_baselines3.common.vec_env import VecEnv

from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.log_util import log
from re1_rl.distributed.rollout_collect import collect_rollout
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.distributed.worker_client import WorkerClient

if TYPE_CHECKING:
    pass


def _local_weight_sync_loop(
    weight_store: WeightStore,
    policy: InferencePolicy,
    *,
    machine_name: str,
    stop_event: threading.Event,
) -> None:
    local_version = 0
    while not stop_event.is_set():
        version = weight_store.policy_version
        if version > local_version:
            state_dict = weight_store.get_state_dict()
            if state_dict is not None:
                policy.load_from_state_dict(state_dict, version)
                local_version = version
                log(machine_name, f"local weight sync -> policy_version={version}")
        time.sleep(0.25)


def _remote_weight_sync_loop(
    client: WorkerClient,
    policy: InferencePolicy,
    *,
    machine_name: str,
    stop_event: threading.Event,
    poll_s: float = 1.0,
) -> None:
    local_version = 0
    while not stop_event.is_set():
        try:
            version, data = client.fetch_weights(min_version=local_version + 1)
            if version > local_version and data:
                policy.load_from_bytes(data, version)
                local_version = version
                log(machine_name, f"remote weight sync -> policy_version={version}")
        except Exception as exc:
            log(machine_name, f"weight sync error: {exc}")
        time.sleep(poll_s)


def warmup_local_policy(
    weight_store: WeightStore,
    policy: InferencePolicy,
    *,
    machine_name: str,
    timeout_s: float,
) -> int:
    log(machine_name, "waiting for initial learner weights (in-process)")
    version = weight_store.wait_for_version(1, timeout=timeout_s)
    state_dict = weight_store.get_state_dict()
    if state_dict is None:
        raise RuntimeError("learner published version without state_dict")
    policy.load_from_state_dict(state_dict, version)
    log(machine_name, f"warmup complete at policy_version={version}")
    return version


def warmup_remote_policy(
    client: WorkerClient,
    policy: InferencePolicy,
    *,
    machine_name: str,
    timeout_s: float,
) -> int:
    log(machine_name, f"waiting for learner at {client.base}")
    client.wait_for_learner(timeout_s)
    version, data = client.fetch_weights()
    if not data:
        raise RuntimeError("learner returned empty policy_bytes during warmup")
    policy.load_from_bytes(data, version)
    log(machine_name, f"warmup complete at policy_version={version}")
    return version


def _try_sync_remote_weights(
    client: WorkerClient,
    policy: InferencePolicy,
    *,
    machine_name: str,
    local_version: int,
) -> int:
    """Pull newer weights once (rollout boundary); returns updated local version."""
    try:
        version, data = client.fetch_weights(min_version=local_version + 1)
        if version > local_version and data:
            policy.load_from_bytes(data, version)
            log(machine_name, f"remote weight sync -> policy_version={version}")
            return version
    except Exception as exc:
        log(machine_name, f"weight sync error: {exc}")
    return local_version


def run_worker_loop(
    vec_env: VecEnv,
    policy: InferencePolicy,
    *,
    machine_name: str,
    worker_id: str,
    n_steps: int,
    stop_event: threading.Event,
    rollout_sink: queue.Queue | WorkerClient,
    is_local: bool,
) -> None:
    log(machine_name, f"worker loop started ({worker_id}, {vec_env.num_envs} envs)")
    local_version = policy.policy_version
    while not stop_event.is_set():
        if policy.policy_version <= 0:
            time.sleep(0.1)
            continue
        if not is_local and isinstance(rollout_sink, WorkerClient):
            local_version = _try_sync_remote_weights(
                rollout_sink,
                policy,
                machine_name=machine_name,
                local_version=local_version,
            )
        rollout = collect_rollout(
            vec_env,
            policy,
            n_steps=n_steps,
            worker_id=worker_id,
        )
        if is_local:
            assert isinstance(rollout_sink, queue.Queue)
            rollout_sink.put(rollout)
            log(
                machine_name,
                f"delivered rollout v{rollout.policy_version} "
                f"({rollout.num_timesteps()} steps) to learner queue",
            )
        else:
            assert isinstance(rollout_sink, WorkerClient)
            accepted = rollout_sink.upload_rollout(rollout)
            if accepted:
                log(
                    machine_name,
                    f"uploaded rollout v{rollout.policy_version} "
                    f"({rollout.num_timesteps()} steps)",
                )
    log(machine_name, "worker loop stopped")
