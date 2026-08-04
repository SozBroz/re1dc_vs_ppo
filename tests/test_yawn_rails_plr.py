"""PLR sampler: uniform atomic-cell floor + per-endpoint max_legs."""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import pytest

from re1_rl.yawn_rails import sample_one_leg_options
from re1_rl.yawn_rails_plr import (
    LEG_LADDER,
    YawnRailsPlrState,
    YawnRailsPlrStore,
    clamp_leg_span,
    next_leg_width,
    sample_plr_options,
    sample_with_uniform_floor,
)


ROOT = Path(__file__).resolve().parents[1]


def test_leg_ladder_and_clamp() -> None:
    assert LEG_LADDER == (1, 2, 3, 4, 6)
    assert next_leg_width(1) == 2
    assert next_leg_width(4) == 6
    assert next_leg_width(6) is None
    assert clamp_leg_span(6, remaining=2, endpoint_max=6) == 2
    assert clamp_leg_span(6, remaining=10, endpoint_max=2) == 2


def test_uniform_floor_reserves_equal_mass() -> None:
    cells = [-1, 0, 1, 2, 3]
    scores = {-1: 100.0, 0: 0.01, 1: 0.01, 2: 0.01, 3: 0.01}
    rng = random.Random(0)
    counts: Counter[int] = Counter()
    n = 5000
    for _ in range(n):
        counts[
            sample_with_uniform_floor(cells, scores, rng, uniform_floor=1.0)
        ] += 1
    for cell in cells:
        assert 0.15 < counts[cell] / n < 0.25


def test_plr_sample_respects_endpoint_max_not_global_six(tmp_path: Path) -> None:
    stage = {
        "route_id": "test",
        "route_steps": list(range(1, 20)),
        "legs_per_episode": 6,
        "cells_manifest": "manifest.json",
    }
    cells = []
    for idx in range(3):
        cell = tmp_path / f"states/cp{idx}"
        cell.mkdir(parents=True)
        (cell / "cell.State").write_bytes(b"state" * 100)
        (cell / "cell.sidecar.json").write_text("{}", encoding="utf-8")
        cells.append({
            "checkpoint_index": idx,
            "state_path": f"states/cp{idx}/cell.State",
            "sidecar_path": f"states/cp{idx}/cell.sidecar.json",
        })
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "route_id": "test", "cells": cells}),
        encoding="utf-8",
    )
    store = YawnRailsPlrStore(tmp_path / "plr_state.json")
    store.state.endpoint_max_legs[0] = 1
    store.state.endpoint_max_legs[1] = 2
    store.state.endpoint_max_legs[2] = 4
    store.save()

    for _ in range(40):
        opts = sample_plr_options(
            tmp_path,
            stage,
            [{"checkpoint_index": -1, "source": "route_initial"}, *cells],
            rng=random.Random(_),
            store=store,
            uniform_floor=1.0,
        )
        cell_index = int(opts["route_start_index"]) - 1
        assert opts["leg_span"] <= store.state.max_legs_for(cell_index)
        assert opts["leg_span"] <= 4  # never jumps to global 6 early
        assert opts["endpoint_max_legs"] == store.state.max_legs_for(cell_index)


def test_endpoint_widens_along_ladder_after_sustained_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_YAWN_PLR_WIDEN_MIN_EPISODES", "5")
    monkeypatch.setenv("RE1_YAWN_PLR_WIDEN_SUCCESS", "0.8")
    store = YawnRailsPlrStore(tmp_path / "plr_state.json")
    assert store.state.max_legs_for(3) == 1
    for _ in range(5):
        store.observe_episode(
            checkpoint_index=3,
            leg_span=1,
            reset_variant="route_cell",
            success=True,
        )
    assert store.state.max_legs_for(3) == 2


def test_sample_one_leg_uses_plr_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_YAWN_PLR", "1")
    monkeypatch.setenv("RE1_YAWN_PLR_STATE", str(tmp_path / "plr.json"))
    cell = tmp_path / "states/cp0"
    cell.mkdir(parents=True)
    (cell / "cell.State").write_bytes(b"state" * 100)
    (cell / "cell.sidecar.json").write_text("{}", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "route_id": "test",
            "cells": [{
                "checkpoint_index": 0,
                "state_path": "states/cp0/cell.State",
                "sidecar_path": "states/cp0/cell.sidecar.json",
            }],
        }),
        encoding="utf-8",
    )
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 10)),
        "legs_per_episode": 6,
    }
    opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(1))
    assert "plr_level" in opts
    assert opts["leg_span"] == 1
    assert opts["endpoint_max_legs"] == 1


def test_plr_state_roundtrip(tmp_path: Path) -> None:
    store = YawnRailsPlrStore(tmp_path / "plr.json")
    store.state.endpoint_max_legs[1] = 3
    store.state.eval_success[1] = 0.4
    store.state.ensure_level(1, 3, "route_cell").score = 2.5
    store.save()
    loaded = YawnRailsPlrStore(tmp_path / "plr.json")
    assert loaded.state.max_legs_for(1) == 3
    assert loaded.state.eval_success[1] == pytest.approx(0.4)
    key_stats = loaded.state.levels["1:3:route_cell"]
    assert key_stats.score == pytest.approx(2.5)
    assert YawnRailsPlrState.from_dict(store.state.to_dict()).route_id == "yawn_quest_v2"
