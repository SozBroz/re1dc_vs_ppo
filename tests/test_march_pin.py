"""Learner-authoritative march pin store + CAS."""

from __future__ import annotations

from pathlib import Path

from re1_rl.distributed.march_pin import (
    MarchPinStore,
    parse_pin_index,
    rewrite_index_text,
    sync_pin_from_learner,
)


def test_rewrite_index_text_updates_existing():
    text = "RE1_PLANNER_RESET_PIN_INDEX=7\nRE1_PLANNER_MARCH=1\n"
    out = rewrite_index_text(text, 9)
    assert parse_pin_index(out) == 9
    assert "RE1_PLANNER_MARCH=1" in out


def test_store_cas_advance(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RE1_RL_ROOT", str(tmp_path))
    pin = tmp_path / "data" / "planner_loyal_reset_pin.env"
    pin.parent.mkdir(parents=True)
    pin.write_text(
        "RE1_PLANNER_RESET_PIN_INDEX=3\nRE1_PLANNER_MARCH=1\n",
        encoding="utf-8",
    )
    store = MarchPinStore(tmp_path)
    snap = store.snapshot()
    assert snap["index"] == 3
    assert snap["version"] == 1

    ok, out = store.publish(index=4, expected_index=3, expected_version=1)
    assert ok is True
    assert out["index"] == 4
    assert out["version"] == 2
    assert parse_pin_index(pin.read_text(encoding="utf-8")) == 4

    ok, err = store.publish(index=5, expected_index=3, expected_version=2)
    assert ok is False
    assert err["error"] == "index_mismatch"
    assert err["index"] == 4


def test_sync_noop_without_learner(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RE1_LEARNER_HOST", raising=False)
    monkeypatch.delenv("FLEET_LEARNER_HOST", raising=False)
    assert sync_pin_from_learner(tmp_path) is False
