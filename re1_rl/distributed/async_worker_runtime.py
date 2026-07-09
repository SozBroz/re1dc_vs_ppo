"""Desync actor fleet for distributed workers (local inference, upload rollouts).

Mirrors monolithic ``async_fleet`` collection: each env is an independent actor
process; the worker process serves ``need`` / ``act`` via a local
``InferencePolicy`` and sinks completed ``WorkerRollout`` batches to the learner
(in-process queue or HTTP). No per-step network for actions.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from multiprocessing.connection import Connection, wait
from typing import Any

import numpy as np

from re1_rl.async_fleet import (
    _actor_process,
    _obs_batch_for_one,
    _wait_for_actor_spawn,
)
from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.log_util import log
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.worker_client import WorkerClient
from re1_rl.distributed.worker_runtime import _try_sync_remote_weights


def worker_rollout_from_actor_msg(
    msg: dict[str, Any],
    *,
    policy: InferencePolicy,
    worker_id: str,
    n_steps: int,
) -> WorkerRollout:
    """Build a 1-env ``WorkerRollout`` from an actor ``rollout`` pipe message."""
    rank = int(msg["rank"])
    last_values = policy.predict_values(_obs_batch_for_one(msg["last_obs"]))
    obs = {k: np.expand_dims(v, axis=1) for k, v in msg["obs"].items()}
    return WorkerRollout(
        worker_id=f"{worker_id}:actor_{rank}",
        policy_version=int(policy.policy_version),
        n_envs=1,
        n_steps=n_steps,
        obs=obs,
        actions=np.expand_dims(msg["actions"], 1),
        rewards=np.expand_dims(msg["rewards"], 1),
        dones=np.expand_dims(msg["dones"], 1),
        values=np.expand_dims(msg["values"], 1),
        log_probs=np.expand_dims(msg["log_probs"], 1),
        last_values=last_values,
        episode_infos=list(msg.get("episode_infos") or []),
    )


def _serve_need(
    conn: Connection,
    msg: dict[str, Any],
    policy: InferencePolicy,
) -> None:
    obs_batch = _obs_batch_for_one(msg["obs"])
    masks = msg.get("action_masks")
    if masks is not None:
        act, val, lp = policy.predict_masked(
            obs_batch, np.asarray(masks, dtype=bool)
        )
    else:
        act_a, val_a, lp_a = policy.predict_batch(obs_batch)
        act, val, lp = int(act_a[0]), float(val_a[0]), float(lp_a[0])
    conn.send({"t": "act", "action": act, "value": val, "logprob": lp})


def _deliver_rollout(
    rollout: WorkerRollout,
    *,
    machine_name: str,
    rollout_sink: queue.Queue | WorkerClient,
    is_local: bool,
) -> None:
    if is_local:
        assert isinstance(rollout_sink, queue.Queue)
        rollout_sink.put(rollout)
        log(
            machine_name,
            f"delivered rollout v{rollout.policy_version} "
            f"({rollout.num_timesteps()} steps) to learner queue "
            f"[{rollout.worker_id}]",
        )
        return
    assert isinstance(rollout_sink, WorkerClient)
    accepted = rollout_sink.upload_rollout(rollout)
    if accepted:
        log(
            machine_name,
            f"uploaded rollout v{rollout.policy_version} "
            f"({rollout.num_timesteps()} steps) [{rollout.worker_id}]",
        )


def _shutdown_actors(
    stop_flag: mp.synchronize.Synchronized,
    parent_conns: list[Connection],
    processes: list[mp.Process],
) -> None:
    stop_flag.value = True
    for conn in parent_conns:
        try:
            conn.send({"t": "stop"})
        except (BrokenPipeError, OSError):
            pass
        try:
            conn.close()
        except OSError:
            pass
    for proc in processes:
        proc.join(timeout=30)
        if proc.is_alive():
            proc.terminate()


def run_async_worker_loop(
    policy: InferencePolicy,
    *,
    machine_name: str,
    worker_id: str,
    n_envs: int,
    n_steps: int,
    curriculum: str,
    base_port: int,
    training_speed: int,
    skip_chunk: int,
    capture_checkpoints: bool,
    stop_event: threading.Event,
    rollout_sink: queue.Queue | WorkerClient,
    is_local: bool,
) -> None:
    """Spawn desync actors and serve local inference until ``stop_event``."""
    log(
        machine_name,
        f"async worker starting ({worker_id}, {n_envs} desync actors, "
        f"n_steps={n_steps})",
    )
    stop_flag = mp.Value("b", False)
    ctx = mp.get_context("spawn")
    processes: list[mp.Process] = []
    parent_conns: list[Connection] = []

    try:
        for rank in range(n_envs):
            parent_conn, child_conn = ctx.Pipe(duplex=True)
            proc = ctx.Process(
                target=_actor_process,
                args=(rank, child_conn),
                kwargs={
                    "curriculum": curriculum,
                    "base_port": base_port,
                    "training_speed": training_speed,
                    "skip_chunk": skip_chunk,
                    "n_steps": n_steps,
                    "stop_flag": stop_flag,
                    "capture_checkpoints": capture_checkpoints,
                },
                name=f"dist-async-actor-{rank}",
            )
            proc.start()
            child_conn.close()
            processes.append(proc)
            parent_conns.append(parent_conn)

        _wait_for_actor_spawn(parent_conns, n_envs, processes=processes)
        log(machine_name, f"async worker fleet ready ({n_envs} actors)")
        for conn in parent_conns:
            conn.send({"t": "start"})

        local_version = policy.policy_version
        while not stop_event.is_set() and not stop_flag.value:
            if policy.policy_version <= 0:
                time.sleep(0.1)
                continue

            ready = wait(parent_conns, timeout=1.0)
            if not ready:
                if not any(p.is_alive() for p in processes):
                    log(machine_name, "all async actors exited")
                    break
                continue

            for conn in ready:
                if not conn.poll():
                    continue
                msg = conn.recv()
                kind = msg.get("t")
                if kind == "need":
                    _serve_need(conn, msg, policy)
                elif kind == "rollout":
                    if not is_local and isinstance(rollout_sink, WorkerClient):
                        local_version = _try_sync_remote_weights(
                            rollout_sink,
                            policy,
                            machine_name=machine_name,
                            local_version=local_version,
                        )
                    rollout = worker_rollout_from_actor_msg(
                        msg,
                        policy=policy,
                        worker_id=worker_id,
                        n_steps=n_steps,
                    )
                    _deliver_rollout(
                        rollout,
                        machine_name=machine_name,
                        rollout_sink=rollout_sink,
                        is_local=is_local,
                    )
                elif kind in ("spawn_progress", "spawned", "spawn_error"):
                    continue
    finally:
        _shutdown_actors(stop_flag, parent_conns, processes)
        log(machine_name, "async worker loop stopped")
