"""Desync actor fleet for distributed workers (local inference, epoch sync).

Mirrors monolithic ``async_fleet`` collection: each env is an independent actor
process; the worker process serves ``need`` / ``act`` via a local
``InferencePolicy``. Remotes buffer rollouts and touch the network once per
``sync_interval_s`` (upload burst + weight pull). Local workers enqueue
in-process with no HTTP.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any

import numpy as np

from re1_rl.async_fleet import (
    DEFAULT_SYNC_INTERVAL_S,
    _actor_process,
    _obs_batch_for_one,
    _wait_for_actor_spawn,
)
from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.log_util import log
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.worker_client import WorkerClient
from re1_rl.training_progress import TrainingProgressTracker


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


def pack_rollouts(rollouts: list[WorkerRollout], *, worker_id: str) -> WorkerRollout:
    """Merge same-horizon 1-env rollouts into one multi-env batch for a single POST."""
    if not rollouts:
        raise ValueError("empty rollout list")
    n_steps = rollouts[0].n_steps
    version = rollouts[0].policy_version
    for r in rollouts:
        if r.n_steps != n_steps:
            raise ValueError("pack_rollouts requires identical n_steps")
        if r.policy_version != version:
            raise ValueError("pack_rollouts requires identical policy_version")
    total_envs = sum(r.n_envs for r in rollouts)
    obs = {
        key: np.concatenate([r.obs[key] for r in rollouts], axis=1)
        for key in rollouts[0].obs
    }
    return WorkerRollout(
        worker_id=worker_id,
        policy_version=version,
        n_envs=total_envs,
        n_steps=n_steps,
        obs=obs,
        actions=np.concatenate([r.actions for r in rollouts], axis=1),
        rewards=np.concatenate([r.rewards for r in rollouts], axis=1),
        dones=np.concatenate([r.dones for r in rollouts], axis=1),
        values=np.concatenate([r.values for r in rollouts], axis=1),
        log_probs=np.concatenate([r.log_probs for r in rollouts], axis=1),
        last_values=np.concatenate([r.last_values for r in rollouts], axis=0),
        episode_infos=[info for r in rollouts for info in r.episode_infos],
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


def _deliver_local(
    rollout: WorkerRollout,
    *,
    machine_name: str,
    rollout_sink: queue.Queue,
) -> None:
    rollout_sink.put(rollout)
    log(
        machine_name,
        f"delivered rollout v{rollout.policy_version} "
        f"({rollout.num_timesteps()} steps) to learner queue "
        f"[{rollout.worker_id}]",
    )


def _pack_and_deliver_rollouts(
    group: list[WorkerRollout],
    *,
    worker_id: str,
    pack_max_envs: int,
    deliver,
) -> int:
    """Pack same-version rollouts into <=pack_max_envs chunks; return put count."""
    chunk: list[WorkerRollout] = []
    chunk_envs = 0
    n_posts = 0
    for r in group:
        if chunk and chunk_envs + r.n_envs > pack_max_envs:
            deliver(pack_rollouts(chunk, worker_id=worker_id))
            n_posts += 1
            chunk, chunk_envs = [], 0
        chunk.append(r)
        chunk_envs += r.n_envs
    if chunk:
        deliver(pack_rollouts(chunk, worker_id=worker_id))
        n_posts += 1
    return n_posts


def _flush_remote_epoch(
    buffered: list[WorkerRollout],
    *,
    client: WorkerClient,
    policy: InferencePolicy,
    machine_name: str,
    worker_id: str,
    pack_max_envs: int = 16,
) -> list[WorkerRollout]:
    """Upload buffered experience (burst), then pull weights once. Returns []."""
    if not buffered:
        log(machine_name, "sync epoch: no rollouts buffered; weight pull only")
    else:
        total_steps = sum(r.num_timesteps() for r in buffered)
        # Pack into modest multi-env POSTs to avoid one giant HTTP body.
        by_ver: dict[int, list[WorkerRollout]] = {}
        for r in buffered:
            by_ver.setdefault(r.policy_version, []).append(r)
        n_posts = 0
        for ver, group in by_ver.items():
            ver_posts = _pack_and_deliver_rollouts(
                group,
                worker_id=worker_id,
                pack_max_envs=pack_max_envs,
                deliver=client.upload_rollout,
            )
            n_posts += ver_posts
            log(
                machine_name,
                f"sync epoch upload v{ver}: {len(group)} actor-rollouts "
                f"in {ver_posts} POST(s)",
            )
        log(
            machine_name,
            f"sync epoch flushed {len(buffered)} actor-rollouts "
            f"({total_steps} steps, {n_posts} POSTs)",
        )

    try:
        version, data = client.fetch_weights(min_version=policy.policy_version + 1)
        if version > policy.policy_version and data:
            policy.load_from_bytes(data, version)
            log(machine_name, f"sync epoch weight pull -> policy_version={version}")
        else:
            version, data = client.fetch_weights(min_version=0)
            if version > policy.policy_version and data:
                policy.load_from_bytes(data, version)
                log(
                    machine_name,
                    f"sync epoch weight pull (refresh) -> policy_version={version}",
                )
            else:
                log(
                    machine_name,
                    f"sync epoch: no newer weights "
                    f"(local=v{policy.policy_version}, remote=v{version})",
                )
    except Exception as exc:
        log(machine_name, f"sync epoch weight pull error: {exc}")
    return []


def _flush_local_epoch(
    buffered: list[WorkerRollout],
    *,
    rollout_sink: queue.Queue,
    machine_name: str,
    worker_id: str,
) -> list[WorkerRollout]:
    if not buffered:
        log(machine_name, "sync epoch (local): no rollouts buffered")
        return []
    total_steps = sum(r.num_timesteps() for r in buffered)
    by_ver: dict[int, list[WorkerRollout]] = {}
    for r in buffered:
        by_ver.setdefault(r.policy_version, []).append(r)
    n_posts = 0
    for ver, group in by_ver.items():
        ver_posts = _pack_and_deliver_rollouts(
            group,
            worker_id=worker_id,
            pack_max_envs=16,
            deliver=rollout_sink.put,
        )
        n_posts += ver_posts
        log(
            machine_name,
            f"sync epoch (local) v{ver}: {len(group)} actor-rollouts "
            f"in {ver_posts} queue put(s)",
        )
    log(
        machine_name,
        f"sync epoch (local) flushed {len(buffered)} actor-rollouts "
        f"({total_steps} steps, {n_posts} queue puts)",
    )
    return []


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
    sync_interval_s: float = DEFAULT_SYNC_INTERVAL_S,
    heartbeat_s: float = 30.0,
    project_root: Path | None = None,
    headless: bool = True,
) -> None:
    """Spawn desync actors and serve local inference until ``stop_event``.

    Both local and remote workers buffer rollouts and flush every
    ``sync_interval_s``. Remotes then pull weights; locals only enqueue.
    Remotes also heartbeat so the learner can drop dead machines.
    """
    log(
        machine_name,
        f"async worker starting ({worker_id}, {n_envs} desync actors, "
        f"n_steps={n_steps}, sync_interval_s={sync_interval_s:.0f}, "
        f"headless={headless})",
    )
    root = Path(project_root) if project_root else Path.cwd()
    best_log = root / "data" / "logs" / f"best_rooms_{machine_name}.jsonl"
    progress = TrainingProgressTracker(
        prefix=f"progress:{machine_name}",
        machine_name=machine_name,
        best_log_path=best_log,
    )
    local_steps = 0
    stop_flag = mp.Value("b", False)
    ctx = mp.get_context("spawn")
    processes: list[mp.Process] = []
    parent_conns: list[Connection] = []
    buffered: list[WorkerRollout] = []
    epoch_t0 = time.monotonic()
    last_heartbeat = 0.0
    hb_stop = threading.Event()

    def _heartbeat_loop() -> None:
        if is_local or not isinstance(rollout_sink, WorkerClient):
            return
        while not hb_stop.is_set() and not stop_event.is_set():
            try:
                rollout_sink.heartbeat(worker_id, n_envs)
            except Exception as exc:
                log(machine_name, f"heartbeat error: {exc}")
            hb_stop.wait(heartbeat_s)

    hb_thread = threading.Thread(
        target=_heartbeat_loop, name="worker-heartbeat", daemon=True
    )

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
                    "headless": headless,
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

        if not is_local and isinstance(rollout_sink, WorkerClient):
            try:
                rollout_sink.heartbeat(worker_id, n_envs)
                last_heartbeat = time.monotonic()
            except Exception as exc:
                log(machine_name, f"initial heartbeat error: {exc}")
            hb_thread.start()

        while not stop_event.is_set() and not stop_flag.value:
            if policy.policy_version <= 0:
                time.sleep(0.1)
                continue

            if (time.monotonic() - epoch_t0) >= sync_interval_s:
                epoch_infos = [
                    info for r in buffered for info in (r.episode_infos or [])
                ]
                if is_local and isinstance(rollout_sink, queue.Queue):
                    buffered = _flush_local_epoch(
                        buffered,
                        rollout_sink=rollout_sink,
                        machine_name=machine_name,
                        worker_id=worker_id,
                    )
                elif isinstance(rollout_sink, WorkerClient):
                    buffered = _flush_remote_epoch(
                        buffered,
                        client=rollout_sink,
                        policy=policy,
                        machine_name=machine_name,
                        worker_id=worker_id,
                    )
                if epoch_infos:
                    progress.log_rollout_end(
                        None,
                        num_timesteps=local_steps,
                        episode_infos=epoch_infos,
                    )
                epoch_t0 = time.monotonic()

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
                    rollout = worker_rollout_from_actor_msg(
                        msg,
                        policy=policy,
                        worker_id=worker_id,
                        n_steps=n_steps,
                    )
                    local_steps += int(rollout.num_timesteps())
                    progress.consume_infos(
                        rollout.episode_infos,
                        num_timesteps=local_steps,
                    )
                    buffered.append(rollout)
                elif kind in ("spawn_progress", "spawned", "spawn_error"):
                    continue

        if buffered:
            if is_local and isinstance(rollout_sink, queue.Queue):
                _flush_local_epoch(
                    buffered,
                    rollout_sink=rollout_sink,
                    machine_name=machine_name,
                    worker_id=worker_id,
                )
            elif isinstance(rollout_sink, WorkerClient):
                _flush_remote_epoch(
                    buffered,
                    client=rollout_sink,
                    policy=policy,
                    machine_name=machine_name,
                    worker_id=worker_id,
                )
    finally:
        hb_stop.set()
        if not is_local and isinstance(rollout_sink, WorkerClient):
            try:
                rollout_sink.unregister(worker_id)
            except Exception:
                pass
        _shutdown_actors(stop_flag, parent_conns, processes)
        log(machine_name, "async worker loop stopped")
