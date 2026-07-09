"""AttackTelemetry counters and logging (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl import attack_telemetry as at
from re1_rl.attack_telemetry import AttackTelemetry


def _state(**kw):
    base = {
        "room_id": "104",
        "x": 30000,
        "z": 7500,
        "enemies": [{"hp": 100}],
    }
    base.update(kw)
    return base


def test_counters_accumulate() -> None:
    tel = AttackTelemetry(port=7)
    tel.record("knife", "knife", "no_damage", state=_state())
    tel.record("shoot", "handgun", "hit", enemy_damage=10, state=_state())
    tel.record("shoot", "handgun", "aim_timeout", ammo_spent=1, state=_state())

    assert tel.attacks_total == 3
    assert tel.attacks_hit == 1
    assert tel.attacks_missed == 2
    assert tel.misses_by_weapon["knife"] == 1
    assert tel.misses_by_weapon["handgun"] == 1
    assert tel.misses_by_outcome["no_damage"] == 1
    assert tel.misses_by_outcome["aim_timeout"] == 1


def test_missed_attack_prints_line(capsys) -> None:
    tel = AttackTelemetry(port="biz")
    report = {
        "issues": ["swing too short", "no crouch aim", "extra"],
        "pre_state": {"hooks": "0/0/0", "label": "idle"},
    }
    tel.record(
        "knife",
        "knife",
        "no_damage",
        macro_report=report,
        state=_state(room_id="105"),
    )
    out = capsys.readouterr().out
    assert "[attack_fail] port=biz weapon=knife outcome=no_damage" in out
    assert "room=105" in out
    assert "issues=3 hooks=0/0/0" in out
    assert "swing too short; no crouch aim; extra" in out


def test_attack_log_zero_silences(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ATTACK_LOG", "0")
    monkeypatch.setattr(at, "ATTACK_LOG_ENABLED", False)
    tel = AttackTelemetry(port=1)
    tel.record("knife", "knife", "no_damage", state=_state())
    assert capsys.readouterr().out == ""


def test_episode_summary_math() -> None:
    tel = AttackTelemetry()
    tel.record("a", "knife", "no_damage", state=_state())
    tel.record("b", "knife", "hit", enemy_damage=5, state=_state())
    tel.record("c", "handgun", "settle_timeout", state=_state())

    summary = tel.episode_summary()
    assert summary["attacks_total"] == 3
    assert summary["attacks_hit"] == 1
    assert summary["attacks_missed"] == 2
    assert summary["hit_rate"] == 1 / 3
    assert summary["misses_by_weapon"] == {"knife": 1, "handgun": 1}
    assert summary["misses_by_outcome"] == {"no_damage": 1, "settle_timeout": 1}

    tel.reset_episode()
    assert tel.attacks_total == 0
    assert tel.episode_summary()["hit_rate"] == 0.0


def test_record_returns_state_hints() -> None:
    tel = AttackTelemetry()
    rec = tel.record(
        "shoot",
        "handgun",
        "aim_timeout",
        ammo_spent=2,
        state=_state(),
    )
    assert rec["missed"] is True
    assert rec["attack_missed"] is True
    assert rec["ammo_spent"] == 2
    assert rec["attack_weapon"] == "handgun"

    hit = tel.record(
        "shoot",
        "handgun",
        "hit",
        enemy_damage=10,
        ammo_spent=1,
        state=_state(),
    )
    assert hit["hit"] is True
    assert hit["missed"] is False
    assert hit["ammo_spent"] == 0
