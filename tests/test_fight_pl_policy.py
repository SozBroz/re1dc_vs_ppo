"""Fighting-PL adaptive walls + march frames factor."""
from __future__ import annotations

import json
import math
from pathlib import Path

from re1_rl.fight_pl_policy import (
    FIGHT_MARCH_FRAMES_FACTOR,
    FIGHT_STAGE_FACTORS,
    FIGHT_STAGE_FLOORS_F,
    fight_adaptive_cap_frames,
    is_fighting_hop,
    live_kit_meets_champion,
    march_frames_factor_for_target,
    march_requires_kit_bar,
    note_kit_qualified_success,
    parse_resources_kit,
)
from re1_rl.planner_hop_score import (
    PLANNER_DEFAULT_TIMEOUT_FRAMES,
    adaptive_cap_frames,
    adaptive_cap_frames_for_target,
    snapshot_adaptive_baseline,
)


def _kit_md(rows: list[tuple[int, int, int, int]]) -> str:
    # rows: (pl, hp, kills, pistol)
    lines = [
        "# Resources",
        "",
        "## Kit by PL",
        "",
        "| PL | HP | Kills | Pistol | Shotgun | Bazooka | Magnum | Frames |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for pl, hp, kills, pistol in rows:
        lines.append(
            f"| `pl{pl:02d}` | {hp} | {kills} | {pistol} | 0 | 0 | 0 | 100 |"
        )
    lines.extend(["", "## Other", ""])
    return "\n".join(lines) + "\n"


def _proj(tmp_path: Path, kit_rows, champ: dict[int, list[int]]) -> Path:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "planner_loyal_resources.md").write_text(
        _kit_md(kit_rows), encoding="utf-8"
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    cells = {
        str(k): list(v) for k, v in champ.items()
    }
    (tmp_path / "data" / "planner_march_champions.json").write_text(
        json.dumps({"cells": cells}), encoding="utf-8"
    )
    return tmp_path


def _write_cell(root: Path, idx: int, quality: list[int], *, frames: int = 4000):
    cell = root / "cells" / f"pl{idx:02d}"
    cell.mkdir(parents=True, exist_ok=True)
    (cell / "cell.pst").write_bytes(b"STATE")
    (cell / "meta.json").write_text(
        json.dumps(
            {
                "checkpoint_index": idx,
                "quality": quality,
                "leg_emulated_frames": frames,
                "state_sha256": f"sha-{idx}-{frames}",
            }
        ),
        encoding="utf-8",
    )


def test_parse_and_classify_fight_vs_nav(tmp_path):
    root = _proj(
        tmp_path,
        kit_rows=[
            (22, 96, 0, 45),
            (23, 96, 2, 47),  # kills up → fight
            (24, 96, 2, 47),  # flat → nav
            (25, 90, 2, 47),  # HP down → fight
            (26, 90, 2, 40),  # ammo down → fight
        ],
        champ={},
    )
    kit = parse_resources_kit(root)
    assert kit[23]["kills"] == 2
    assert is_fighting_hop(22, 23, root) is True
    assert is_fighting_hop(23, 24, root) is False
    assert is_fighting_hop(24, 25, root) is True
    assert is_fighting_hop(25, 26, root) is True
    # March kit bar: kills/HP only — ammo drip alone is nav-qualify.
    assert march_requires_kit_bar(22, 23, root) is True
    assert march_requires_kit_bar(23, 24, root) is False
    assert march_requires_kit_bar(24, 25, root) is True
    assert march_requires_kit_bar(25, 26, root) is False


def test_march_factor_fight_raises_nav_keeps_base(tmp_path):
    root = _proj(
        tmp_path,
        kit_rows=[(22, 96, 0, 45), (23, 96, 2, 47), (24, 96, 2, 47)],
        champ={},
    )
    assert march_frames_factor_for_target(23, root, base_factor=1.11) == (
        FIGHT_MARCH_FRAMES_FACTOR
    )
    assert march_frames_factor_for_target(24, root, base_factor=1.11) == 1.11


def test_fight_holds_full_wall_until_kit_and_quals(tmp_path):
    root = _proj(
        tmp_path,
        kit_rows=[(22, 96, 0, 45), (23, 96, 2, 47)],
        champ={23: [96, 2, 77, 0, 0, 0, 0, 0, -449, 0, 0]},
    )
    cells = root  # cells under project root for this unit test layout
    _write_cell(cells, 23, [96, 2, 71, 0, 0, 0, 0, 0, -400, 0, 0], frames=4002)
    baseline = snapshot_adaptive_baseline(cells)
    _write_cell(cells, 23, [96, 2, 71, 0, 0, 0, 0, 0, -380, 0, 0], frames=3800)
    # Mint observed, ammo below champ → full wall.
    assert live_kit_meets_champion(23, cells, root) is False
    assert (
        adaptive_cap_frames_for_target(
            23, cells, baseline, tip_slot=22, project_root=root
        )
        == PLANNER_DEFAULT_TIMEOUT_FRAMES
    )

    # Kit met but 0 sticky quals → still full wall.
    _write_cell(cells, 23, [96, 2, 77, 0, 0, 0, 0, 0, -380, 0, 0], frames=3800)
    assert live_kit_meets_champion(23, cells, root) is True
    # Ammo three below champ still unlocks kit (MARCH_AMMO_SLACK=3).
    _write_cell(cells, 23, [96, 2, 74, 0, 0, 0, 0, 0, -380, 0, 0], frames=3800)
    assert live_kit_meets_champion(23, cells, root) is True
    _write_cell(cells, 23, [96, 2, 73, 0, 0, 0, 0, 0, -380, 0, 0], frames=3800)
    assert live_kit_meets_champion(23, cells, root) is False
    _write_cell(cells, 23, [96, 2, 77, 0, 0, 0, 0, 0, -380, 0, 0], frames=3800)
    assert (
        fight_adaptive_cap_frames(
            tip_slot=22,
            target_slot=23,
            best_emulated_frames=3800,
            cells_root=cells,
            project_root=root,
        )
        == PLANNER_DEFAULT_TIMEOUT_FRAMES
    )

    for _ in range(3):
        note_kit_qualified_success(22, 23, project_root=root, emulated_frames=3800)
    stage0 = fight_adaptive_cap_frames(
        tip_slot=22,
        target_slot=23,
        best_emulated_frames=3800,
        cells_root=cells,
        project_root=root,
    )
    expect = int(math.ceil(3800 * FIGHT_STAGE_FACTORS[0] / 8.0) * 8)
    expect = min(max(expect, FIGHT_STAGE_FLOORS_F[0]), PLANNER_DEFAULT_TIMEOUT_FRAMES)
    assert stage0 == expect
    assert stage0 == 19000  # 5x * 3800
    assert stage0 > adaptive_cap_frames(3800, 0, boss=False)


def test_nav_mint_still_uses_3x_when_tip_given(tmp_path):
    root = _proj(
        tmp_path,
        kit_rows=[(10, 96, 0, 45), (11, 96, 0, 45)],
        champ={},
    )
    cells = root
    _write_cell(cells, 11, [96, 0, 45, 0, 0, 0, 0, 0, -449, 0, 0], frames=4490)
    baseline = snapshot_adaptive_baseline(cells)
    _write_cell(cells, 11, [96, 0, 45, 0, 0, 0, 0, 0, -400, 0, 0], frames=4002)
    capped = adaptive_cap_frames_for_target(
        11, cells, baseline, tip_slot=10, project_root=root
    )
    assert capped == adaptive_cap_frames(4002, 0, boss=False)
    assert capped == 12008
