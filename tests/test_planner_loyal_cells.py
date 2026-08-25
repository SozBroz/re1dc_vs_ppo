"""Planner-loyal cell bootstrap + slot mapping."""
from __future__ import annotations

from pathlib import Path

from re1_rl.planner_loyal_cells import (
    TRAINING_START_INDEX,
    bootstrap_from_crystals,
    cell_dir_name,
    planner_loyal_root,
    slot_index_for_completed_step,
    training_start_paths,
)


def test_slot_mapping() -> None:
    assert slot_index_for_completed_step(0) == TRAINING_START_INDEX + 1
    assert slot_index_for_completed_step(1) == TRAINING_START_INDEX + 2
    assert cell_dir_name(5) == "pl05"


def test_bootstrap_crystals_tip_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    crystals = root / "backups" / "Crystals_in_time" / "cp05" / "cell.State"
    if not crystals.is_file():
        return  # skip if archive absent in CI
    result = bootstrap_from_crystals(root, force=False)
    tip = training_start_paths(root)
    assert tip["state"].is_file()
    assert tip["sidecar"].is_file()
    assert result["training_start_index"] == TRAINING_START_INDEX
    assert (planner_loyal_root(root) / "manifest.json").is_file()
    # Thin: no leg_replay on tip after bootstrap refresh strip.
    from re1_rl.planner_loyal_cells import _strip_fat_artifacts

    _strip_fat_artifacts(tip["cell_dir"])
    assert not (tip["cell_dir"] / "leg_replay.json").is_file()
