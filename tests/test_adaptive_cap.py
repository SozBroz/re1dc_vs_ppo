"""Adaptive episode cap: full wall until a new mint lands, then ~1.2x best.

Gated by RE1_PL_ADAPTIVE_CAP=1 (default off); --adaptive-cap on
scripts/distributed_train_parallel.py mirrors the --per-tip-cap pattern.
"""
import json
import math

from re1_rl.planner_hop_score import (
    ADAPTIVE_CAP_FACTOR,
    PLANNER_BOSS_TIMEOUT_FRAMES,
    PLANNER_DEFAULT_TIMEOUT_FRAMES,
    adaptive_cap_enabled,
    adaptive_cap_frames,
    adaptive_cap_frames_for_target,
    adaptive_mint_observed,
    per_tip_cap_frames,
    planner_timeout_frames,
    recorded_frames_for_pl,
    snapshot_adaptive_baseline,
)


def _write_cell(root, idx, frames, sha="state-sha"):
    cell = root / "cells" / f"pl{idx:02d}"
    cell.mkdir(parents=True, exist_ok=True)
    (cell / "cell.pst").write_bytes(b"STATE_%02d" % idx)
    (cell / "meta.json").write_text(
        json.dumps(
            {
                "checkpoint_index": idx,
                # dim 7 is a constant (-30); leg frames live at dim 8.
                "quality": [96, 0, 45, 100, 4, 1, 0, -30, -int(frames), 0, 0],
                "state_sha256": sha,
                "sidecar_sha256": "side-%s" % sha,
            }
        ),
        encoding="utf-8",
    )


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("RE1_PL_ADAPTIVE_CAP", raising=False)
    assert not adaptive_cap_enabled()
    for off in ("0", "false", "off", "no", ""):
        monkeypatch.setenv("RE1_PL_ADAPTIVE_CAP", off)
        assert not adaptive_cap_enabled()
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("RE1_PL_ADAPTIVE_CAP", on)
        assert adaptive_cap_enabled()


def test_cap_math_floor_binds_when_best_equals_recorded():
    # best == recorded -> 1.2x tight end can never beat the 1.5x+slack floor.
    assert adaptive_cap_frames(1000, 1000, boss=False) == per_tip_cap_frames(
        1000, boss=False
    )
    assert adaptive_cap_frames(449, 449, boss=False) == 150 * 8


def test_cap_math_tight_binds_when_best_exceeds_recorded():
    # 1.2*2000 = 2400f tight vs 1.5x floor of a 449f record (1200f).
    tight = int(math.ceil(1.2 * 2000 / 8.0) * 8)
    assert tight == 2400
    assert adaptive_cap_frames(2000, 449, boss=False) == 2400


def test_cap_math_boss_variant():
    wall = PLANNER_BOSS_TIMEOUT_FRAMES
    assert planner_timeout_frames(boss=True) == wall
    # Tight end binds over the 2.0x boss floor.
    assert adaptive_cap_frames(2000, 449, boss=True) == 2400
    # Floor binds for a fast hop under the boss factor.
    assert adaptive_cap_frames(100, 100, boss=True) == per_tip_cap_frames(
        100, boss=True
    )


def test_cap_never_exceeds_wall():
    assert adaptive_cap_frames(20_000, 20_000, boss=False) == (
        PLANNER_DEFAULT_TIMEOUT_FRAMES
    )
    assert adaptive_cap_frames(99_000, 99_000, boss=True) == (
        PLANNER_BOSS_TIMEOUT_FRAMES
    )
    # Unknown best -> static floor (which itself never exceeds the wall).
    assert adaptive_cap_frames(0, 1000, boss=False) == per_tip_cap_frames(
        1000, boss=False
    )
    assert adaptive_cap_frames(0, 0, boss=False) == PLANNER_DEFAULT_TIMEOUT_FRAMES


def test_no_mint_keeps_full_wall(tmp_path):
    _write_cell(tmp_path, 23, 449, sha="incumbent")
    baseline = snapshot_adaptive_baseline(tmp_path)
    observed, best = adaptive_mint_observed(baseline, 23, tmp_path)
    assert observed is False
    assert best == 0
    assert adaptive_cap_frames_for_target(
        23, tmp_path, baseline, boss=False
    ) == PLANNER_DEFAULT_TIMEOUT_FRAMES


def test_new_mint_with_better_frames_tightens(tmp_path):
    _write_cell(tmp_path, 23, 449, sha="incumbent")
    baseline = snapshot_adaptive_baseline(tmp_path)
    # Simulated remint this run: fewer leg frames + changed sha.
    _write_cell(tmp_path, 23, 400, sha="fresh")
    observed, best = adaptive_mint_observed(baseline, 23, tmp_path)
    assert observed is True
    assert best == 400
    assert recorded_frames_for_pl(23, tmp_path) == 400
    capped = adaptive_cap_frames_for_target(23, tmp_path, baseline, boss=False)
    assert capped == adaptive_cap_frames(400, 400, boss=False)
    assert capped < PLANNER_DEFAULT_TIMEOUT_FRAMES


def test_mint_uses_dim_8_not_dim_7(tmp_path):
    _write_cell(tmp_path, 1, 42, sha="incumbent")
    baseline = snapshot_adaptive_baseline(tmp_path)
    _write_cell(tmp_path, 1, 36, sha="fresh")
    observed, best = adaptive_mint_observed(baseline, 1, tmp_path)
    assert (observed, best) == (True, 36)


def test_appearing_cell_counts_as_mint(tmp_path):
    baseline = snapshot_adaptive_baseline(tmp_path)
    assert baseline.get(24) is None
    _write_cell(tmp_path, 24, 300, sha="first")
    observed, best = adaptive_mint_observed(baseline, 24, tmp_path)
    assert (observed, best) == (True, 300)


def test_corrupt_meta_fails_open_to_full_wall(tmp_path):
    _write_cell(tmp_path, 23, 449, sha="incumbent")
    baseline = snapshot_adaptive_baseline(tmp_path)
    (tmp_path / "cells" / "pl23" / "meta.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    observed, best = adaptive_mint_observed(baseline, 23, tmp_path)
    assert (observed, best) == (False, 0)
    assert adaptive_cap_frames_for_target(
        23, tmp_path, baseline, boss=False
    ) == PLANNER_DEFAULT_TIMEOUT_FRAMES
    # Missing snapshot also fails open (never tighten without a baseline).
    assert adaptive_mint_observed(None, 23, tmp_path) == (False, 0)
    assert adaptive_cap_frames_for_target(
        23, tmp_path, None, boss=False
    ) == PLANNER_DEFAULT_TIMEOUT_FRAMES


def test_identical_rewrite_is_not_a_mint(tmp_path):
    _write_cell(tmp_path, 23, 449, sha="same")
    baseline = snapshot_adaptive_baseline(tmp_path)
    observed, best = adaptive_mint_observed(baseline, 23, tmp_path)
    assert (observed, best) == (False, 0)


def test_grind_default_isolation(monkeypatch, tmp_path):
    # Flag off -> helpers report disabled and flat walls are untouched,
    # even with the adaptive flag's sibling env set or cells on disk.
    monkeypatch.delenv("RE1_PL_ADAPTIVE_CAP", raising=False)
    monkeypatch.delenv("RE1_PL_PER_TIP_CAP", raising=False)
    assert not adaptive_cap_enabled()
    assert planner_timeout_frames(boss=False) == PLANNER_DEFAULT_TIMEOUT_FRAMES
    assert planner_timeout_frames(boss=True) == PLANNER_BOSS_TIMEOUT_FRAMES
    assert ADAPTIVE_CAP_FACTOR == 1.2
    _write_cell(tmp_path, 23, 449, sha="incumbent")
    baseline = snapshot_adaptive_baseline(tmp_path)
    # Pure helpers: mint math does not depend on the flag; gating lives in
    # env.py / CLI, so enabling the flag changes no static default.
    monkeypatch.setenv("RE1_PL_ADAPTIVE_CAP", "1")
    assert adaptive_cap_enabled()
    assert planner_timeout_frames(boss=False) == PLANNER_DEFAULT_TIMEOUT_FRAMES
    assert per_tip_cap_frames(449, boss=False) == 150 * 8
    observed, _ = adaptive_mint_observed(baseline, 23, tmp_path)
    assert observed is False


def test_cli_flag_sets_env_and_parses(monkeypatch):
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import scripts.distributed_train_parallel as dtp

    args = dtp.build_parser().parse_args(["--machine-name", "t", "--adaptive-cap"])
    assert bool(args.adaptive_cap) is True
    assert dtp.build_parser().parse_args(["--machine-name", "t"]).adaptive_cap is False
    # --adaptive-cap wires the worker env like --per-tip-cap does.
    monkeypatch.delenv("RE1_PL_ADAPTIVE_CAP", raising=False)
    ns = dtp.build_parser().parse_args(["--machine-name", "t", "--adaptive-cap"])
    dtp._apply_grind_defaults(ns)
    assert os.environ.get("RE1_PL_ADAPTIVE_CAP") == "1"
