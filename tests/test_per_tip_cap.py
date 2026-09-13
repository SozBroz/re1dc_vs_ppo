"""Per-tip grind cap: 1.5x recorded hop frames, flag-gated with flat default."""
import json

from re1_rl import async_fleet
from re1_rl.planner_hop_score import (
    PLANNER_BOSS_TIMEOUT_FRAMES,
    PLANNER_DEFAULT_TIMEOUT_FRAMES,
    per_tip_cap_enabled,
    per_tip_cap_frames,
    planner_timeout_frames,
    recorded_frames_for_pl,
)


def test_flat_wall_unchanged_by_default(monkeypatch):
    monkeypatch.delenv("RE1_PL_PER_TIP_CAP", raising=False)
    assert not per_tip_cap_enabled()
    assert planner_timeout_frames(boss=False) == PLANNER_DEFAULT_TIMEOUT_FRAMES
    assert planner_timeout_frames(boss=True) == PLANNER_BOSS_TIMEOUT_FRAMES


def test_cap_math_typical_hop():
    # pl23: 449f -> 57 steps -> ceil(57*1.5)=86 + 50 slack = 136 steps,
    # but the 150-step floor binds -> 1200f (still 18x shorter than 21600f).
    assert per_tip_cap_frames(449, boss=False) == 150 * 8
    # 1000f binds the 1.5x rule: 125 steps -> 188 + 50 slack = 238 steps.
    assert per_tip_cap_frames(1000, boss=False) == 238 * 8


def test_cap_math_trivial_hop_hits_floor():
    # pl197: 4 frames -> floor 150 steps -> 1200f (not an insta-truncate).
    assert per_tip_cap_frames(4, boss=False) == 150 * 8


def test_cap_math_combat_hop_gets_slack():
    # pl125: 1228f -> 154 steps -> ceil(154*1.5)=231 + 50 slack = 281 steps.
    assert per_tip_cap_frames(1228, boss=False) == 281 * 8


def test_cap_never_exceeds_flat_wall():
    assert per_tip_cap_frames(20_000, boss=False) == PLANNER_DEFAULT_TIMEOUT_FRAMES
    assert per_tip_cap_frames(99_000, boss=True) == PLANNER_BOSS_TIMEOUT_FRAMES


def test_cap_zero_recorded_keeps_flat_wall():
    assert per_tip_cap_frames(0, boss=False) == PLANNER_DEFAULT_TIMEOUT_FRAMES


def test_recorded_frames_from_meta(tmp_path):
    cells = tmp_path / "cells" / "pl23"
    cells.mkdir(parents=True)
    (cells / "meta.json").write_text(
        json.dumps({"quality": [96, 2, 47, 100, 4, 1, 0, -30, -449, 0, 719]}),
        encoding="utf-8",
    )
    assert recorded_frames_for_pl(23, tmp_path) == 449
    assert recorded_frames_for_pl(24, tmp_path) == 0  # missing target -> flat wall


def test_recorded_frames_uses_dim_8_not_dim_7(tmp_path):
    # Real pl01 shape: dim 7 is a constant (-30), frames live at dim 8 (-42).
    cells = tmp_path / "cells" / "pl01"
    cells.mkdir(parents=True)
    (cells / "meta.json").write_text(
        json.dumps({"quality": [96, 0, 45, 100, 4, 1, 0, -30, -42, 0, 998]}),
        encoding="utf-8",
    )
    assert recorded_frames_for_pl(1, tmp_path) == 42


def test_grind_preset_overfits_but_keeps_gamma():
    grind = async_fleet.GRIND_EPOCH_HYPERPARAMS
    base = async_fleet.DISTRIBUTED_EPOCH_HYPERPARAMS
    assert grind["ent_coef"] == 0.0
    assert grind["clip_range"] > base["clip_range"]
    assert grind["target_kl"] is None
    assert grind["n_epochs"] > base["n_epochs"]
    assert grind["batch_size"] < base["batch_size"]
    assert grind["gamma"] == 1.0 == base["gamma"]
    # Full-game defaults untouched.
    assert base["ent_coef"] == 0.005
    assert base["clip_range"] == 0.10
    assert base["target_kl"] == 0.006
