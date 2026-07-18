"""Offline tests for pking top-right step memlog (fixed path, truncate-in-place)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from re1_rl import step_diag as sd
from re1_rl.action_mask import ATTACK_ACTION, SELECT_SLOT_BASE
from re1_rl.window_grid import build_slots


def test_top_right_slot_is_rank_4_for_pking_grid() -> None:
    """5x4 row-major: top-right is local index 4 (col=4,row=0) → rank 4 / port 5759."""
    mon = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
    slots = build_slots(20, mon, cols=5, rows=4, gap=8)
    # slot i → (col, row) = (i % 5, i // 5)
    top_right = 4
    assert top_right % 5 == 4 and top_right // 5 == 0
    # top-right is rightmost among first row
    xs_row0 = [slots[i][0] for i in range(5)]
    assert slots[top_right][0] == max(xs_row0)
    assert slots[top_right][1] == min(s[1] for s in slots[:5])


def test_diag_port_filter(monkeypatch) -> None:
    monkeypatch.delenv("RE1_STEP_DIAG_PORT", raising=False)
    assert sd.diag_port_filter() is None
    assert not sd.diag_enabled_for_port(5759)
    monkeypatch.setenv("RE1_STEP_DIAG_PORT", "5759")
    assert sd.diag_port_filter() == 5759
    assert sd.diag_enabled_for_port(5759)
    assert not sd.diag_enabled_for_port(5755)


def test_truncate_in_place_not_unlink(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "pking_top_right_memlog.jsonl"
    log_path.write_text('{"event":"old"}\n', encoding="utf-8")
    inode_before = log_path.stat().st_ino if hasattr(log_path.stat(), "st_ino") else None

    monkeypatch.setenv("RE1_STEP_DIAG_PORT", "5759")
    monkeypatch.setenv("RE1_STEP_DIAG_LOG", str(log_path))
    sd._OPENED_PATHS.clear()

    logger = sd.try_make_logger(5759, project_root=tmp_path, rank=4, machine_name="pking")
    assert logger is not None
    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert '"run_start":true' in text.replace(" ", "")
    assert "old" not in text  # truncated
    # Same path object still present (never unlinked)
    assert log_path.exists()
    if inode_before is not None:
        # On Windows st_ino may be 0; only assert when meaningful.
        inode_after = log_path.stat().st_ino
        if inode_before and inode_after:
            assert inode_after == inode_before

    mask = np.zeros(46, dtype=bool)
    mask[ATTACK_ACTION] = True
    mask[SELECT_SLOT_BASE + 2] = True
    logger.note_value(1.23456789)
    logger.log_step(
        reward=0.123456789,
        terminated=False,
        truncated=False,
        action_masks=mask,
        inventory_slots=[("knife", 1), ("beretta", 1), ("herb_green", 1)],
        hooks=None,
        info={
            "visited_rooms": ["105", "104"],
            "reward_breakdown": {
                "step": -0.0002,
                "new_cutscene": 1.0,
                "enemy_damage": 0.05,
                "main_hall_before_kenneth": 0.0,
            },
        },
        action=1,
        action_name="forward",
    )
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "\n" not in lines[1]  # one JSON object per line
    step = json.loads(lines[1])
    for banned in (
        "event",
        "ts",
        "port",
        "rank",
        "hooks",
        "room",
        "terminated",
        "truncated",
        "attack_down_legal",
        "attack_up_legal",
    ):
        assert banned not in step
    assert step["reward"] == 0.12346
    assert step["ep_return_cum"] == 0.12346
    assert step["action"] == "forward"
    assert step["value"] == 1.23457
    assert step["attack_legal"] is True
    assert step["use_slots_legal"] == ["herb_green"]
    assert step["inventory"] == ["knife", "beretta", "herb_green"]
    assert step["rooms"] == ["104", "105"]
    assert step["big_rewards"] == [{"src": "new_cutscene", "r": 1.0}]

    logger.log_step(
        reward=-0.01,
        terminated=True,
        truncated=False,
        action_masks=mask,
        inventory_slots=[("knife", 1)],
        hooks=None,
        info={},
        action=0,
        action_name="noop",
    )
    end = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "event" not in end
    assert "ep_return_total" in end
    assert end["ep_return_total"] == round(0.12346 + -0.01, 5)


def test_second_logger_same_process_does_not_retruncate(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "pking_top_right_memlog.jsonl"
    monkeypatch.setenv("RE1_STEP_DIAG_PORT", "5759")
    monkeypatch.setenv("RE1_STEP_DIAG_LOG", str(log_path))
    sd._OPENED_PATHS.clear()

    a = sd.try_make_logger(5759, project_root=tmp_path)
    assert a is not None
    mask = np.ones(46, dtype=bool)
    a.log_step(
        reward=1.0,
        terminated=False,
        truncated=False,
        action_masks=mask,
        inventory_slots=[],
        hooks=None,
        info={},
        action=0,
    )
    n_before = len(log_path.read_text(encoding="utf-8").strip().splitlines())
    b = sd.try_make_logger(5759, project_root=tmp_path)
    assert b is not None
    n_after = len(log_path.read_text(encoding="utf-8").strip().splitlines())
    assert n_after == n_before  # no second RUN_START / truncate
