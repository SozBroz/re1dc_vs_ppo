"""Unit tests for memlog hop-learning target capture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from re1_rl.memlog_runtime import MemlogControlState, MemlogTelemetry
from re1_rl.planner_hop_score import compose_hop_learning_target


def test_publish_hop_learning_episode_records_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_PLANNER_HOP_SCORE_V1", "1")
    tel = MemlogTelemetry(tmp_path, run_id="t", rank=4, n_steps=8)
    S = 0.96
    locals_L = [0.0, -0.057, 0.0, -0.029]
    targets_Y = [compose_hop_learning_target(S, L) for L in locals_L]
    control = MemlogControlState(
        run_id="t", paused=False, speed_pct=100, shutdown=False
    )
    for i, L in enumerate(locals_L):
        tel.publish_step(
            obs={"frame": __import__("numpy").zeros((1,), dtype="uint8")},
            action_mask=__import__("numpy").ones(4, dtype=bool),
            action=0,
            value=0.0,
            logprob=0.0,
            policy_version=1,
            raw_logits=None,
            masked_probs=None,
            reward=float(L),
            info={
                "reward_breakdown": {"ammo_waste": float(L)} if L else {},
                "action_name": "ATTACK",
                "room_id": 100,
            },
            done=(i == len(locals_L) - 1),
            horizon_step=i,
            control=control,
        )
    # With hop live, raw sparse events are deferred until Y flush.
    assert not (tmp_path / "events.jsonl").exists() or not (
        tmp_path / "events.jsonl"
    ).read_text(encoding="utf-8").strip()

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
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    steps = [e for e in events if e.get("kind") == "hop_train_step"]
    summary = [e for e in events if e.get("kind") == "hop_learning_targets"]
    assert len(steps) == 4
    assert len(summary) == 1
    # Baseline steps share S; tax steps override to L.
    assert steps[0]["reward"] == pytest.approx(S)
    assert steps[1]["reward"] == pytest.approx(-0.057)
    assert steps[1]["reward_breakdown"]["overridden"] == 1.0
    assert steps[2]["reward"] == pytest.approx(S)
    assert steps[3]["reward"] == pytest.approx(-0.029)
    ev = summary[0]
    assert ev["tip"] == "pl74"
    assert ev["S"] == pytest.approx(0.96)
    assert ev["targets_Y"][1] == pytest.approx(-0.057)
    assert ev["n_overridden"] == 2
    assert ev["n_unique_Y"] == 3
    assert 1 in ev["overridden_steps"] and 3 in ev["overridden_steps"]
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["hop_learning_targets"]["n_overridden"] == 2
    assert latest["post_step"]["train_target_pending"] is False
    assert latest["post_step"]["reward"] == pytest.approx(targets_Y[-1])
