"""Mocked attack-from-entry tests for any equippable weapon."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.attack_entry_suite import (
    ENTRY_SCENARIOS,
    HARD_FAIL_OUTCOMES,
    evaluate_mock_attack,
)
from re1_rl.attack_macro import KNIFE_WEAPON_ID
from re1_rl.weapon_equip import EQUIPPABLE_WEAPON_IDS

# Primary regression weapons (user-requested).
PRIMARY_WEAPONS = (
    (KNIFE_WEAPON_ID, "knife"),
    (0x02, "beretta"),
)

# Ranged weapons not yet live-probed (mock smoke only).
UNDISCOVERED_RANGED = tuple(
    (wid, None)
    for wid in EQUIPPABLE_WEAPON_IDS
    if wid not in {KNIFE_WEAPON_ID, 0x02}
)


@pytest.mark.parametrize("scenario", [s.name for s in ENTRY_SCENARIOS])
@pytest.mark.parametrize(
    "weapon_id,weapon_name",
    PRIMARY_WEAPONS,
    ids=[name for _, name in PRIMARY_WEAPONS],
)
def test_attack_from_entry_mock_primary(scenario: str, weapon_id: int, weapon_name: str) -> None:
    bridge = MagicMock()
    result = evaluate_mock_attack(bridge, scenario=scenario, weapon_id=weapon_id)
    assert result.succeeded, (
        f"{weapon_name} failed scenario={scenario} "
        f"outcome={result.report.get('outcome')} report={result.report}"
    )
    assert result.report.get("outcome") not in HARD_FAIL_OUTCOMES
    if weapon_id == KNIFE_WEAPON_ID:
        assert result.report.get("macro_path") == "knife_neutral"
        assert result.report.get("aim_mode") == "neutral"
    else:
        assert str(result.report.get("macro_path", "")).startswith("ranged:")


@pytest.mark.parametrize("weapon_id", [wid for wid, _ in UNDISCOVERED_RANGED])
def test_attack_from_entry_mock_undiscovered_neutral(weapon_id: int) -> None:
    """Smoke: synthetic hooks should not hard-fail on idle entry."""
    bridge = MagicMock()
    result = evaluate_mock_attack(bridge, scenario="neutral_idle", weapon_id=weapon_id)
    assert result.report.get("outcome") not in HARD_FAIL_OUTCOMES
    assert result.succeeded


def test_entry_scenario_registry_covers_movement_variants() -> None:
    names = {s.name for s in ENTRY_SCENARIOS}
    for required in (
        "neutral_idle",
        "after_run",
        "locomotion_mid_run",
        "standing_recovery_latch",
    ):
        assert required in names
