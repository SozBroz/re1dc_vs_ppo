"""In-process ThreadingHTTPServer tests for Go-Explore fleet HTTP sync."""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.distributed.learner_server import LearnerState, start_learner_server
from re1_rl.distributed.rollout_codec import encode_rollout
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.distributed.worker_client import WorkerClient
from re1_rl.go_explore_merge import (
    CELL_STATE_NAME,
    GoExploreMerge,
    make_cell_bundle_zip,
)
from re1_rl.go_explore_worker_cache import (
    ensure_bundle_cached,
    load_local_manifest,
    poll_manifest,
)
from re1_rl.milestone_digest import cell_key_v2
from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, PROPRIO_DIM


def _tiny_rollout(
    *,
    policy_version: int,
    episode_infos: list[dict] | None = None,
) -> WorkerRollout:
    return WorkerRollout(
        worker_id="workhorse1:actor_0",
        policy_version=policy_version,
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
        episode_infos=list(episode_infos or []),
    )


def _bundle_prop(record_id: str, quality: list[int]) -> dict:
    key = cell_key_v2("20E", 0, 0, "gallery:idle")
    state = b"STATE_" + record_id.encode()
    side = {"captured_room_id": "20E", "bundle_id": record_id}
    blob = make_cell_bundle_zip(state_bytes=state, sidecar=side)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        state_sha = hashlib.sha256(zf.read(CELL_STATE_NAME)).hexdigest()
        side_sha = hashlib.sha256(zf.read("cell.sidecar.json")).hexdigest()
    return {
        "cell_key": key,
        "record_id": record_id,
        "quality": quality,
        "bundle_b64": base64.b64encode(blob).decode("ascii"),
        "state_sha256": state_sha,
        "sidecar_sha256": side_sha,
        "worker_id": "workhorse1",
    }


@pytest.fixture
def learner_http(tmp_path: Path):
    import torch

    archive = tmp_path / "go_explore" / "archive.json"
    merge = GoExploreMerge(archive)
    store = WeightStore()
    version = store.publish({"dummy": torch.zeros(1)})
    rollout_q: queue.Queue = queue.Queue()
    state = LearnerState(
        store,
        rollout_q,
        machine_name="test",
        max_staleness=8,
        go_explore_merge=merge,
    )
    state.set_current_version(version)
    server, _thread = start_learner_server(state, host="127.0.0.1", port=0)
    host, port = server.server_address
    client = WorkerClient(host, port, machine_name="test-worker", timeout=10.0)
    try:
        yield {
            "state": state,
            "merge": merge,
            "client": client,
            "version": version,
            "server": server,
            "tmp_path": tmp_path,
            "archive": archive,
        }
    finally:
        server.shutdown()
        server.server_close()


def test_manifest_and_bundle_roundtrip(learner_http) -> None:
    ctx = learner_http
    merge: GoExploreMerge = ctx["merge"]
    client: WorkerClient = ctx["client"]
    prop = _bundle_prop("cell001", [7, 4, 1, 2, 0])
    assert merge.ingest_proposals([prop]) == ["cell001"]

    man = client.fetch_go_explore_manifest(since_version=0)
    assert man["archive_version"] == 1
    assert len(man["cells"]) == 1
    assert man["cells"][0]["record_id"] == "cell001"

    man2 = client.fetch_go_explore_manifest(since_version=1)
    assert man2["cells"] == []

    blob = client.fetch_go_explore_bundle("cell001")
    assert blob[:2] == b"PK"
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        assert CELL_STATE_NAME in zf.namelist()
        assert zf.read(CELL_STATE_NAME).startswith(b"STATE_cell001")


def test_rollout_post_merges_capture(learner_http) -> None:
    ctx = learner_http
    client: WorkerClient = ctx["client"]
    merge: GoExploreMerge = ctx["merge"]
    prop = _bundle_prop("from_rollout", [4, 0, 0, 1, 0])
    rollout = _tiny_rollout(
        policy_version=ctx["version"],
        episode_infos=[{"go_explore_capture": [prop]}],
    )
    assert client.upload_rollout(rollout) is True
    assert "from_rollout" in {c.record_id for c in merge.archive.cells.values()}
    assert ctx["state"].go_explore_accepted >= 1


def test_worker_cache_poll_and_lazy_fetch(learner_http, tmp_path: Path) -> None:
    ctx = learner_http
    client: WorkerClient = ctx["client"]
    merge: GoExploreMerge = ctx["merge"]
    prop = _bundle_prop("cached1", [3, 2, 0, 0, 0])
    merge.ingest_proposals([prop])

    worker_root = tmp_path / "worker_cache"
    local = poll_manifest(client, since_version=0, local_root=worker_root)
    assert local["archive_version"] == 1
    assert load_local_manifest(worker_root)["cells"]
    assert not (worker_root / "cells" / "cached1").exists()

    dest = ensure_bundle_cached(client, "cached1", worker_root)
    assert dest is not None
    assert (dest / CELL_STATE_NAME).is_file()

    # Second call is a cache hit (no error).
    dest2 = ensure_bundle_cached(
        client,
        "cached1",
        worker_root,
        expected_sha256=local["cells"][0].get("bundle_sha256") or None,
    )
    assert dest2 == dest


def test_poll_manifest_replaces_snapshot_and_prunes_stale_cache(
    learner_http, tmp_path: Path
) -> None:
    ctx = learner_http
    client: WorkerClient = ctx["client"]
    merge: GoExploreMerge = ctx["merge"]
    merge.ingest_proposals([_bundle_prop("keep_me", [5, 3, 1, 1, 0])])
    merge.ingest_proposals([_bundle_prop("drop_me", [4, 2, 1, 1, 0])])

    worker_root = tmp_path / "worker_cache"
    poll_manifest(client, since_version=0, local_root=worker_root)
    stale_dir = worker_root / "cells" / "orphan_local"
    stale_dir.mkdir(parents=True)
    (stale_dir / CELL_STATE_NAME).write_bytes(b"x")
    (stale_dir / "cell.sidecar.json").write_text("{}", encoding="utf-8")

    # Simulate learner eviction: only keep_me remains.
    merge.archive.remove_cell("drop_me")
    merge.archive_version = int(merge.archive_version) + 1
    merge._persist_unlocked()

    local = poll_manifest(client, since_version=0, local_root=worker_root)
    ids = {str(r["record_id"]) for r in local["cells"]}
    assert ids == {"keep_me"}
    assert not (worker_root / "cells" / "orphan_local").exists()
    assert local["cache_stats"]["pruned_dirs_last_poll"] >= 1


def test_poll_manifest_reconciles_cell_count_without_rows(
    learner_http, tmp_path: Path
) -> None:
    ctx = learner_http
    client: WorkerClient = ctx["client"]
    merge: GoExploreMerge = ctx["merge"]
    merge.ingest_proposals([_bundle_prop("only_one", [3, 2, 0, 0, 0])])

    worker_root = tmp_path / "worker_cache"
    poll_manifest(client, since_version=0, local_root=worker_root)
    manifest_path = worker_root / "local_manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["cells"].append(
        {
            "record_id": "stale_local_only",
            "cell_key": "v2|r=105|x=0|z=0|m=gallery:idle",
            "room_id": "105",
            "quality": [1, 1, 0, 0, 0],
            "bytes": 100,
        }
    )
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    local = poll_manifest(client, since_version=int(raw["archive_version"]), local_root=worker_root)
    ids = {str(r["record_id"]) for r in local["cells"]}
    assert ids == {"only_one"}
