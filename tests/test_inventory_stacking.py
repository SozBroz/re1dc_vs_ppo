"""Stack limits and merge transfer logic (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.inventory_stacking import (  # noqa: E402
    apply_stack_transfer,
    max_transferable,
    stack_limit,
)


def test_handgun_stack_limit():
    assert stack_limit(0x02) == 60


def test_shotgun_shells_stack_limit():
    assert stack_limit(0x0C) == 15


def test_herbs_do_not_stack():
    assert stack_limit(0x44) == 1
    inv = [(0x44, 1), (0, 0)] + [(0, 0)] * 6
    box = [(0x44, 1)] + [(0, 0)] * 15
    new_box, new_inv, moved = apply_stack_transfer(box, inv, 0)
    assert moved == 1
    assert new_inv[1] == (0x44, 1)
    assert new_box[0] == (0, 0)
    # Cannot absorb a third into an existing herb stack without a free slot.
    full_inv = [(0x44, 1)] * 8
    assert max_transferable(full_inv, 0x44, 1) == 0


def test_withdraw_merge_15_plus_15():
    inv = [(0x02, 15)] + [(0, 0)] * 7
    box = [(0x02, 15)] + [(0, 0)] * 15
    new_box, new_inv, moved = apply_stack_transfer(box, inv, 0)
    assert moved == 15
    assert new_inv[0] == (0x02, 30)
    assert new_box[0] == (0, 0)


def test_withdraw_partial_overflow_stays_in_box():
    inv = [(0x02, 50)] + [(0, 0)] * 7
    box = [(0x02, 15)] + [(0, 0)] * 15
    new_box, new_inv, moved = apply_stack_transfer(box, inv, 0)
    assert moved == 10
    assert new_inv[0] == (0x02, 60)
    assert new_box[0] == (0x02, 5)


def test_deposit_merge_into_box():
    inv = [(0x02, 15)] + [(0, 0)] * 7
    box = [(0x02, 30)] + [(0, 0)] * 15
    new_inv, new_box, moved = apply_stack_transfer(inv, box, 0)
    assert moved == 15
    assert new_inv[0] == (0, 0)
    assert new_box[0] == (0x02, 45)


def test_deposit_full_box_but_merge_room():
    inv = [(0x02, 10)] + [(0, 0)] * 7
    box = [(0x0B, 1)] * 16  # sprays — no empty slots
    box[0] = (0x02, 50)
    assert max_transferable(box, 0x02, 10) == 10


def test_shells_cap_at_15():
    inv = [(0x0C, 10)] + [(0, 0)] * 7
    box = [(0x0C, 10)] + [(0, 0)] * 15
    new_box, new_inv, moved = apply_stack_transfer(box, inv, 0)
    assert moved == 5
    assert new_inv[0] == (0x0C, 15)
    assert new_box[0] == (0x0C, 5)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
