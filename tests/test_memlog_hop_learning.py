"""Unit tests for memlog hop-learning target capture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from re1_rl.memlog_runtime import MemlogTelemetry
from re1_rl.planner_hop_score import compose_hop_learning_target


def test_publish_hop_learning_episode_records_override(tmp_path: Path) -> None:
    tel = MemlogTelemetry(tmp_path, run_id="t", rank=4, n_steps=8)
    S = 0.96
    locals_L = [0.0, -0.057, 0.0, -0.029]
    targets_Y = [compose_hop_learning_target(S, L) for L in locals_L]
    tel.publish_hop_learning_episode(
        S=S,
        locals_L=locals_L,
        targets_Y=targets_Y,
        tip="pl74",
        report={
            "outcome": "planner_step_success",
            "A_spent": 1.75,
            "q_ammo_raw": -0.75,
        },
    )
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(events) == 1
    ev = json.loads(events[0])
    assert ev["kind"] == "hop_learning_targets"
    assert ev["tip"] == "pl74"
    assert ev["S"] == pytest.approx(0.96)
    assert ev["targets_Y"][1] == pytest.approx(-0.057)
    assert ev["n_overridden"] == 2
    assert 1 in ev["overridden_steps"] and 3 in ev["overridden_steps"]
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["hop_learning_targets"]["n_overridden"] == 2
