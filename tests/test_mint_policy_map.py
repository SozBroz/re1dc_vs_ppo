"""Durable mint-policy map for post-burn march weight restores."""

from __future__ import annotations

import json

from re1_rl.mint_policy_map import (
    load_map,
    note_mint,
    resolve_hunt_mint_version,
    snapshot_from_cells,
)


def test_note_and_resolve_requires_archive(tmp_path) -> None:
    cells = tmp_path / "states" / "planner_loyal" / "cells" / "pl23"
    cells.mkdir(parents=True)
    (cells / "meta.json").write_text(
        json.dumps(
            {
                "checkpoint_index": 23,
                "mint_policy": {
                    "policy_version": 117,
                    "machine": "workhorse2",
                    "minted_at_unix": 1.0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    doc = snapshot_from_cells(tmp_path)
    assert doc["slots"]["23"]["policy_version"] == 117
    assert resolve_hunt_mint_version(22, project_root=tmp_path) is None  # no archive yet

    wdir = tmp_path / "data" / "mint_policies" / "weights"
    wdir.mkdir(parents=True)
    (wdir / "v117.pt").write_bytes(b"fake-weights")
    assert resolve_hunt_mint_version(22, project_root=tmp_path) == 117
    assert resolve_hunt_mint_version(22, project_root=tmp_path, require_archive=False) == 117


def test_note_mint_upserts(tmp_path) -> None:
    assert note_mint(40, {"policy_version": 12}, project_root=tmp_path) == 12
    assert load_map(tmp_path)["slots"]["40"]["policy_version"] == 12
    assert note_mint(40, {"policy_version": 99, "machine": "pking"}, project_root=tmp_path) == 99
    assert load_map(tmp_path)["slots"]["40"]["machine"] == "pking"


def test_request_learner_reset_includes_mint_version(monkeypatch) -> None:
    from re1_rl import planner_march

    seen: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(planner_march.urllib.request, "urlopen", fake_urlopen)
    assert planner_march._request_learner_reset(
        "127.0.0.1", 8765, "pin-move-51-to-22", mint_policy_version=117
    )
    assert seen["body"]["advance_id"] == "pin-move-51-to-22"
    assert seen["body"]["mint_policy_version"] == 117
