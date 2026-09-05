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
    assert man["cells"][0]["state_sha256"]
    assert man["cells"][0]["sidecar_sha256"]
    assert len(man["cells"][0]["state_sha256"]) == 64

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


def test_poll_refetches_when_meta_sha_missing_after_local_overwrite(
    yawn_http, tmp_path: Path
) -> None:
    """Rejected local overwrite left State dirty; missing meta must not cache-hit."""
    store: YawnRailsCellStore = yawn_http["store"]
    client: WorkerClient = yawn_http["client"]
    prop = _write_local_cell(tmp_path / "canon", idx=17, quality=[96, 45, 0, 8, 1])
    store.ingest_proposals([prop])

    worker_root = tmp_path / "worker_desync"
    poll_yawn_rails_manifest(client, worker_root, since_version=0)
    slot = worker_root / "states" / "yawn_rails" / "cells" / "cp17"
    assert (slot / "cell.State").read_bytes().startswith(b"STATE_cp17")

    # Simulate rejected local capture: overwrite State, drop meta sha binding.
    (slot / "cell.State").write_bytes(b"STATE_cp17_DIRTY_15_AMMO")
    meta_p = slot / "meta.json"
    if meta_p.is_file():
        meta_p.unlink()
    (slot / "cell.sidecar.json").write_text("{}", encoding="utf-8")

    local = poll_yawn_rails_manifest(client, worker_root, since_version=0)
    assert local["cells"][0]["quality"][1] == 45
    assert (slot / "cell.State").read_bytes().startswith(b"STATE_cp17")
    assert not (slot / "cell.State").read_bytes().startswith(b"STATE_cp17_DIRTY")


def test_poll_refetches_when_local_meta_sha_mismatches_learner(
    yawn_http, tmp_path: Path
) -> None:
    store: YawnRailsCellStore = yawn_http["store"]
    client: WorkerClient = yawn_http["client"]
    prop = _write_local_cell(tmp_path / "canon2", idx=16, quality=[96, 45, 0, 8, 1])
    store.ingest_proposals([prop])

    worker_root = tmp_path / "worker_mismatch"
    poll_yawn_rails_manifest(client, worker_root, since_version=0)
    slot = worker_root / "states" / "yawn_rails" / "cells" / "cp16"
    (slot / "cell.State").write_bytes(b"STATE_cp16_LOCAL_REJECT")
    (slot / "meta.json").write_text(
        json.dumps({"bundle_sha256": "deadbeef" * 8, "checkpoint_index": 16})
        + "\n",
        encoding="utf-8",
    )

    poll_yawn_rails_manifest(client, worker_root, since_version=0)
    assert (slot / "cell.State").read_bytes().startswith(b"STATE_cp16")
    assert b"LOCAL_REJECT" not in (slot / "cell.State").read_bytes()


def test_poll_drops_cell_when_fetch_fails_after_dirty_overwrite(
    yawn_http, tmp_path: Path
) -> None:
    """Fetch failure must not keep learner quality paired with dirty State."""
    store: YawnRailsCellStore = yawn_http["store"]
    client: WorkerClient = yawn_http["client"]
    prop = _write_local_cell(tmp_path / "canon3", idx=15, quality=[96, 45, 0, 8, 1])
    store.ingest_proposals([prop])

    worker_root = tmp_path / "worker_fetch_fail"
    poll_yawn_rails_manifest(client, worker_root, since_version=0)
    slot = worker_root / "states" / "yawn_rails" / "cells" / "cp15"
    (slot / "cell.State").write_bytes(b"STATE_cp15_DIRTY")
    (slot / "meta.json").write_text(
        json.dumps({"bundle_sha256": "cafebabe" * 8, "checkpoint_index": 15})
        + "\n",
        encoding="utf-8",
    )

    class _FailBundleClient:
        def fetch_yawn_rails_manifest(self, since_version: int = 0) -> dict:
            return client.fetch_yawn_rails_manifest(since_version=since_version)

        def fetch_yawn_rails_bundle(self, cell_id: str) -> bytes:
            raise RuntimeError("bundle unavailable")

    local = poll_yawn_rails_manifest(
        _FailBundleClient(), worker_root, since_version=0
    )
    assert local["cells"] == []


def test_http_ingest_endpoint(yawn_http, tmp_path: Path) -> None:
    client: WorkerClient = yawn_http["client"]
    store: YawnRailsCellStore = yawn_http["store"]
    prop = _write_local_cell(tmp_path / "ingest", idx=4, quality=[88, 12, 1, 2, 1])
    result = client.ingest_yawn_rails_proposals([prop])
    assert result["accepted"] == ["cp04"]
    assert int(result["archive_version"]) >= 1
    assert 4 in store.cells
    weak = _write_local_cell(tmp_path / "weak", idx=4, quality=[40, 1, 0, 1, 1])
    result2 = client.ingest_yawn_rails_proposals([weak])
    assert result2["accepted"] == []
    assert store.cells[4]["quality"][0] == 88


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


def test_poll_refetches_when_state_dirty_but_meta_bundle_sha_matches(
    yawn_http, tmp_path: Path
) -> None:
    """Matching meta.json token + dirty State must not cache-hit."""
    store: YawnRailsCellStore = yawn_http["store"]
    client: WorkerClient = yawn_http["client"]
    prop = _write_local_cell(tmp_path / "canon4", idx=14, quality=[96, 45, 0, 8, 1])
    store.ingest_proposals([prop])

    worker_root = tmp_path / "worker_meta_hit"
    poll_yawn_rails_manifest(client, worker_root, since_version=0)
    slot = worker_root / "states" / "yawn_rails" / "cells" / "cp14"
    learner_state = (yawn_http["store_root"] / "cells" / "cp14" / "cell.State").read_bytes()
    meta = json.loads((slot / "meta.json").read_text(encoding="utf-8"))
    (slot / "cell.State").write_bytes(b"STATE_cp14_DIRTY_AMMO")
    (slot / "meta.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")

    local = poll_yawn_rails_manifest(client, worker_root, since_version=0)
    assert local["cells"][0]["quality"][1] == 45
    assert (slot / "cell.State").read_bytes() == learner_state
    assert b"DIRTY" not in (slot / "cell.State").read_bytes()


def test_same_version_poll_heals_dirty_state(yawn_http, tmp_path: Path) -> None:
    """Ordinary since_version=current poll must still repair a silent overwrite."""
    store: YawnRailsCellStore = yawn_http["store"]
    client: WorkerClient = yawn_http["client"]
    prop = _write_local_cell(tmp_path / "canon5", idx=13, quality=[96, 45, 0, 8, 1])
    store.ingest_proposals([prop])

    worker_root = tmp_path / "worker_same_ver"
    first = poll_yawn_rails_manifest(client, worker_root, since_version=0)
    slot = worker_root / "states" / "yawn_rails" / "cells" / "cp13"
    learner_state = (yawn_http["store_root"] / "cells" / "cp13" / "cell.State").read_bytes()
    (slot / "cell.State").write_bytes(b"STATE_cp13_SILENT_OVERWRITE")

    local = poll_yawn_rails_manifest(
        client, worker_root, since_version=int(first["archive_version"])
    )
    assert (slot / "cell.State").read_bytes() == learner_state
    assert b"SILENT" not in (slot / "cell.State").read_bytes()
    assert local["cells"][0]["state_sha256"] == first["cells"][0]["state_sha256"]


def test_reset_skips_cell_when_state_sha_mismatches(tmp_path: Path) -> None:
    from re1_rl.yawn_rails_sync import slot_matches_content, yawn_cell_pb_bundle

    cell = tmp_path / "states" / "yawn_rails" / "cells" / "cp00"
    cell.mkdir(parents=True)
    state = cell / "cell.State"
    side = cell / "cell.sidecar.json"
    state.write_bytes(b"GOOD")
    side.write_text("{}", encoding="utf-8")
    good_sha = hashlib.sha256(b"GOOD").hexdigest()
    side_sha = hashlib.sha256(side.read_bytes()).hexdigest()
    row = {
        "state_path": "states/yawn_rails/cells/cp00/cell.State",
        "sidecar_path": "states/yawn_rails/cells/cp00/cell.sidecar.json",
        "state_sha256": good_sha,
        "sidecar_sha256": side_sha,
    }
    assert slot_matches_content(
        cell, state_sha256=row["state_sha256"], sidecar_sha256=row["sidecar_sha256"]
    )
    state.write_bytes(b"DIRTY")
    assert not slot_matches_content(
        cell, state_sha256=row["state_sha256"], sidecar_sha256=row["sidecar_sha256"]
    )
    bundle = yawn_cell_pb_bundle(row)
    assert bundle["state_sha256"] == good_sha


def test_slot_content_shas_prefers_cell_pst(tmp_path: Path) -> None:
    """C-RE1 grafts store bytes in cell.pst; leftover cell.State must not win."""
    from re1_rl.yawn_rails_sync import slot_content_shas, slot_matches_content

    cell = tmp_path / "pl112"
    cell.mkdir()
    (cell / "cell.sidecar.json").write_text("{}", encoding="utf-8")
    pst_bytes = b"PST_GRAFT"
    (cell / "cell.pst").write_bytes(pst_bytes)
    (cell / "cell.State").write_bytes(b"OLD_BIZHAWK")
    pst_sha = hashlib.sha256(pst_bytes).hexdigest()
    shas = slot_content_shas(cell)
    assert shas is not None
    assert shas[0] == pst_sha
    assert slot_matches_content(cell, state_sha256=pst_sha)

    pst_only = tmp_path / "pl070"
    pst_only.mkdir()
    (pst_only / "cell.sidecar.json").write_text("{}", encoding="utf-8")
    (pst_only / "cell.pst").write_bytes(pst_bytes)
    assert slot_matches_content(pst_only, state_sha256=pst_sha)


def test_drop_checkpoints_skips_fresh_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    store = YawnRailsCellStore(tmp_path / "learner_pl")
    prop0 = _write_local_cell(tmp_path / "cap0", idx=0)
    prop1 = _write_local_cell(tmp_path / "cap1", idx=1)
    assert store.ingest_proposals([prop0, prop1])
    assert 0 in store.cells and 1 in store.cells
    dropped = store.drop_checkpoints([0, 1])
    assert dropped == [1]
    assert 0 in store.cells
    assert 1 not in store.cells
    assert (tmp_path / "learner_pl" / "cells" / "pl00").is_dir()
    assert not (tmp_path / "learner_pl" / "cells" / "pl01").is_dir()


def test_prune_stale_removes_planner_loyal_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    cells = tmp_path / "states" / "planner_loyal" / "cells"
    (cells / "pl00").mkdir(parents=True)
    (cells / "pl01").mkdir()
    (cells / "cp02").mkdir()
    from re1_rl.yawn_rails_worker_cache import prune_stale_yawn_cells

    removed = prune_stale_yawn_cells(tmp_path, {0})
    assert removed == 1
    assert (cells / "pl00").is_dir()
    assert not (cells / "pl01").is_dir()
    assert (cells / "cp02").is_dir()
