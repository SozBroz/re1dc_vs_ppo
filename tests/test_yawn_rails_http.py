"""In-process HTTP tests for Yawn rails fleet cell sync."""

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
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.distributed.worker_client import WorkerClient
from re1_rl.go_explore_merge import CELL_STATE_NAME
from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, PROPRIO_DIM
from re1_rl.training_progress import slim_progress_info
from re1_rl.yawn_rails_sync import (
    YawnRailsCellStore,
    build_capture_proposal,
    cell_dir_name,
    pack_cell_bundle,
)
from re1_rl.yawn_rails_worker_cache import (
    load_local_yawn_manifest,
    poll_yawn_rails_manifest,
)


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


def _write_local_cell(tmp: Path, *, idx: int = 0, quality=(96, 15, 1, 2, 1)) -> dict:
    cell = tmp / "cells" / cell_dir_name(idx)
    cell.mkdir(parents=True, exist_ok=True)
    state_p = cell / "cell.State"
    side_p = cell / "cell.sidecar.json"
    state_p.write_bytes(b"STATE_cp%02d" % idx)
    side_p.write_text(
        json.dumps({"captured_room_id": "105", "checkpoint_index": idx}) + "\n",
        encoding="utf-8",
    )
    return build_capture_proposal(
        route_id="yawn_quest_v1",
        checkpoint_index=idx,
        checkpoint_id=f"cp_{idx}",
        room_id="105",
        quality=quality,
        state_path=state_p,
        sidecar_path=side_p,
        worker_id="workhorse1",
        capacity={
            "inventory_free_slots": 3,
            "next_checkpoint_id": f"cp_{idx + 1}",
            "next_slots_needed": 1,
            "inventory_feasible": True,
            "captured_in_box_room": False,
        },
    )


@pytest.fixture
def yawn_http(tmp_path: Path):
    import torch

    store_root = tmp_path / "learner_yawn"
    store = YawnRailsCellStore(store_root)
    weight_store = WeightStore()
    version = weight_store.publish({"dummy": torch.zeros(1)})
    rollout_q: queue.Queue = queue.Queue()
    state = LearnerState(
        weight_store,
        rollout_q,
        machine_name="test",
        max_staleness=8,
        go_explore_merge=None,
        yawn_rails_store=store,
    )
    state.set_current_version(version)
    server, _thread = start_learner_server(state, host="127.0.0.1", port=0)
    host, port = server.server_address
    client = WorkerClient(host, port, machine_name="test-worker", timeout=10.0)
    try:
        yield {
            "state": state,
            "store": store,
            "client": client,
            "version": version,
            "tmp_path": tmp_path,
            "store_root": store_root,
        }
    finally:
        server.shutdown()
        server.server_close()


def test_manifest_and_bundle_roundtrip(yawn_http, tmp_path: Path) -> None:
    store: YawnRailsCellStore = yawn_http["store"]
    client: WorkerClient = yawn_http["client"]
    prop = _write_local_cell(tmp_path / "cap0", idx=0, quality=[90, 10, 1, 2, 1])
    assert store.ingest_proposals([prop]) == ["cp00"]

    man = client.fetch_yawn_rails_manifest(since_version=0)
    assert man["archive_version"] == 1
    assert len(man["cells"]) == 1
    assert man["cells"][0]["checkpoint_index"] == 0
    assert man["cells"][0]["inventory_free_slots"] == 3
    assert man["cells"][0]["next_checkpoint_id"] == "cp_1"

    man2 = client.fetch_yawn_rails_manifest(since_version=1)
    assert man2["cells"] == []

    blob = client.fetch_yawn_rails_bundle("cp00")
    assert blob[:2] == b"PK"
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        assert CELL_STATE_NAME in zf.namelist()
        assert zf.read(CELL_STATE_NAME).startswith(b"STATE_cp00")


def test_rollout_post_merges_yawn_capture(yawn_http, tmp_path: Path) -> None:
    client: WorkerClient = yawn_http["client"]
    store: YawnRailsCellStore = yawn_http["store"]
    prop = _write_local_cell(tmp_path / "cap1", idx=3, quality=[80, 5, 0, 1, 1])
    rollout = _tiny_rollout(
        policy_version=yawn_http["version"],
        episode_infos=[{"yawn_rails_capture": [prop]}],
    )
    assert client.upload_rollout(rollout) is True
    assert 3 in store.cells
    assert yawn_http["state"].yawn_rails_accepted >= 1


def test_quality_replace_and_reject(yawn_http, tmp_path: Path) -> None:
    store: YawnRailsCellStore = yawn_http["store"]
    weak = _write_local_cell(tmp_path / "w", idx=1, quality=[50, 1, 0, 1, 1])
    strong = _write_local_cell(tmp_path / "s", idx=1, quality=[96, 20, 2, 3, 1])
    assert store.ingest_proposals([weak]) == ["cp01"]
    assert store.ingest_proposals([strong]) == ["cp01"]
    # Worse capture must not replace.
    worse = _write_local_cell(tmp_path / "x", idx=1, quality=[40, 0, 0, 1, 1])
    assert store.ingest_proposals([worse]) == []
    assert store.cells[1]["quality"][0] == 96


def test_capacity_metadata_upgrades_legacy_row_even_with_lower_quality(
    yawn_http, tmp_path: Path
) -> None:
    store: YawnRailsCellStore = yawn_http["store"]
    legacy = _write_local_cell(
        tmp_path / "legacy", idx=5, quality=[96, 20, 2, 3, 1]
    )
    for key in (
        "inventory_free_slots",
        "next_checkpoint_id",
        "next_slots_needed",
        "inventory_feasible",
        "captured_in_box_room",
    ):
        legacy.pop(key)
    assert store.ingest_proposals([legacy]) == ["cp05"]
    assert "inventory_feasible" not in store.cells[5]

    recaptured = _write_local_cell(
        tmp_path / "recaptured", idx=5, quality=[80, 10, 1, 2, 1]
    )
    assert store.ingest_proposals([recaptured]) == ["cp05"]
    assert store.cells[5]["inventory_feasible"] is True


def test_worker_eager_poll_mirrors_bundles(yawn_http, tmp_path: Path) -> None:
    store: YawnRailsCellStore = yawn_http["store"]
    client: WorkerClient = yawn_http["client"]
    prop = _write_local_cell(tmp_path / "cap2", idx=2, quality=[70, 4, 1, 1, 1])
    store.ingest_proposals([prop])

    worker_root = tmp_path / "worker_box"
    # Simulate project root: states/yawn_rails lives under worker_root.
    # yawn_rails_root(project) -> project/states/yawn_rails
    local = poll_yawn_rails_manifest(client, worker_root, since_version=0)
    assert local["archive_version"] == 1
    assert load_local_yawn_manifest(worker_root)["cells"]
    assert local["cells"][0]["inventory_feasible"] is True
    assert local["cells"][0]["next_slots_needed"] == 1
    mirrored = worker_root / "states" / "yawn_rails" / "cells" / "cp02" / "cell.State"
    assert mirrored.is_file()
    assert mirrored.read_bytes().startswith(b"STATE_cp02")


def test_slim_progress_keeps_yawn_rails_capture() -> None:
    prop = {
        "checkpoint_index": 0,
        "bundle_b64": base64.b64encode(b"PK").decode("ascii"),
        "quality": [1, 2, 3, 4, 5],
    }
    slim = slim_progress_info({"yawn_rails_capture": [prop], "state": {"huge": True}})
    assert "state" not in slim
    assert slim["yawn_rails_capture"][0]["checkpoint_index"] == 0


def test_pack_bundle_sha_stable(tmp_path: Path) -> None:
    prop = _write_local_cell(tmp_path, idx=7, quality=[1, 1, 0, 0, 1])
    blob = base64.b64decode(prop["bundle_b64"])
    assert hashlib.sha256(blob).hexdigest() == prop["bundle_sha256"]
    rebuilt = pack_cell_bundle(
        state_bytes=b"STATE_cp07",
        sidecar={"captured_room_id": "105", "checkpoint_index": 7},
        meta={"checkpoint_index": 7},
    )
    assert rebuilt[:2] == b"PK"
