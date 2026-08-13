"""Equal-weight held-out eval schedule and metrics aggregation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from re1_rl.yawn_rails import validate_manifest_cells
from re1_rl.yawn_rails_eval import (
    aggregate_cell_metrics,
    build_eval_report,
    equal_weight_eval_schedule,
    eval_scalars_for_logger,
    list_eval_cells,
    macro_average_success,
    reset_options_for_eval_slot,
    worst_decile_success,
)

ROOT = Path(__file__).resolve().parents[1]


def _stage() -> dict:
    return json.loads(
        (ROOT / "curriculum/yawn_rails_one_leg.json").read_text(encoding="utf-8")
    )


def test_real_manifest_keeps_early_route_starts() -> None:
    stage = _stage()
    errors = validate_manifest_cells(ROOT, stage, require_contiguous_prefix=2)
    assert errors == []
    cells = list_eval_cells(ROOT, stage)
    ids = [c["cell_id"] for c in cells]
    assert ids[0] == "route_initial"
    assert "cp00" in ids
    assert "cp01" in ids
    # cp02–cp07 are intentionally dropped from the live start mix / recapture.


def test_equal_weight_schedule_balances_cells() -> None:
    cells = [
        {"checkpoint_index": -1, "cell_id": "route_initial", "source": "route_initial"},
        {
            "checkpoint_index": 0,
            "cell_id": "cp00",
            "source": "route_cell",
            "state_path": "states/yawn_rails/cells/cp00/cell.State",
            "sidecar_path": "states/yawn_rails/cells/cp00/cell.sidecar.json",
        },
        {
            "checkpoint_index": 3,
            "cell_id": "cp03",
            "source": "route_cell",
            "state_path": "states/yawn_rails/cells/cp03/cell.State",
            "sidecar_path": "states/yawn_rails/cells/cp03/cell.sidecar.json",
        },
    ]
    schedule = equal_weight_eval_schedule(cells, episodes_per_cell=3)
    assert len(schedule) == 9
    counts = {c["cell_id"]: 0 for c in cells}
    for slot in schedule:
        counts[slot["cell_id"]] += 1
        assert slot["held_out_eval"] is True
        assert slot["leg_span"] == 1
    assert counts == {"route_initial": 3, "cp00": 3, "cp03": 3}
    opts = reset_options_for_eval_slot(schedule[1])
    assert opts["held_out_eval"] is True
    assert opts["route_start_index"] == 1
    assert "pb_bundle" in opts


def test_aggregate_metrics_macro_and_worst_decile() -> None:
    infos = []
    # cp00: 100% success, cp01: 0%, cp02: 50%, cp03: 0%
    for cell_index, outcomes in {
        0: ["checkpoint_success", "checkpoint_success"],
        1: ["truncation", "truncation"],
        2: ["checkpoint_success", "main_hall_before_kenneth"],
        3: ["death", "truncation"],
    }.items():
        for i, outcome in enumerate(outcomes):
            infos.append({
                "episode": {"r": 1.0 if outcome == "checkpoint_success" else 0.0, "l": 10 + i},
                "rails_cell_id": f"cp{cell_index:02d}",
                "rails_cell_index": cell_index,
                "episode_outcome": outcome,
                "held_out_eval": True,
            })
    buckets = aggregate_cell_metrics(infos, held_out_only=True)
    assert set(buckets) == {"cp00", "cp01", "cp02", "cp03"}
    assert buckets["cp00"].summary()["success_rate"] == pytest.approx(1.0)
    assert buckets["cp01"].summary()["truncation_rate"] == pytest.approx(1.0)
    assert buckets["cp02"].summary()["illegal_gate_rate"] == pytest.approx(0.5)
    macro = macro_average_success(buckets)
    assert macro == pytest.approx((1.0 + 0.0 + 0.5 + 0.0) / 4)
    worst = worst_decile_success(buckets)
    assert worst == pytest.approx(0.0)
    report = build_eval_report(buckets, policy_version=7, equal_weight=True)
    scalars = eval_scalars_for_logger(report)
    assert "eval/yawn_cell/macro_avg_success" in scalars
    assert "eval/yawn_cell/cp03/success_rate" in scalars
    assert "train/" not in "".join(scalars)
