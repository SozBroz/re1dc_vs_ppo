"""Item-box observation vector: encode_box, BOX_FIELDS, explain_obs."""

from __future__ import annotations

import numpy as np
import pytest

from re1_rl.obs_encoder import (
    BOX_DIM,
    BOX_FIELDS,
    MAX_ITEM_ID,
    encode_box,
    explain_obs,
    format_obs_table,
)
from re1_rl.weapon_damage import AMMO_QTY_NORM


def test_box_dim_matches_fields():
    assert BOX_DIM == 34
    assert BOX_DIM == len(BOX_FIELDS)


def test_encode_box_two_stacks():
    # handgun (0x01) qty 3 in slot 0; shotgun shells (0x0A) qty 7 in slot 1
    v = encode_box([(0x01, 3), (0x0A, 7)], in_box_room=False)
    assert v.shape == (BOX_DIM,)
    assert v[0] == pytest.approx(0x01 / float(MAX_ITEM_ID))
    assert v[1] == pytest.approx(3 / AMMO_QTY_NORM)
    assert v[2] == pytest.approx(0x0A / float(MAX_ITEM_ID))
    assert v[3] == pytest.approx(7 / AMMO_QTY_NORM)
    assert v[4:32] == pytest.approx(0.0)
    assert v[32] == pytest.approx(14 / 16.0)  # 14 empty slots
    assert v[33] == 0.0


def test_encode_box_empty_and_none():
    for box in ([], None):
        v = encode_box(box, in_box_room=False)
        assert np.all(v[:32] == 0.0)
        assert v[32] == pytest.approx(1.0)
        assert v[33] == 0.0


def test_encode_box_qty_clips_above_ammo_norm():
    v = encode_box([(0x05, 400)], in_box_room=False)
    assert v[1] == pytest.approx(1.0)
    v2 = encode_box([(0x05, 99)], in_box_room=False)
    assert v2[1] == pytest.approx(99 / AMMO_QTY_NORM)


def test_encode_box_free_slots_math():
    v = encode_box(
        [(0x01, 1), (0, 0), (0x02, 2), (0, 0), (0, 0)],
        in_box_room=False,
    )
    assert v[32] == pytest.approx(14 / 16.0)


def test_encode_box_in_box_room_flag():
    assert encode_box(None, in_box_room=True)[33] == 1.0
    assert encode_box(None, in_box_room=False)[33] == 0.0


def test_encode_box_short_list_zero_pads():
    v = encode_box([(0x03, 5)], in_box_room=False)
    assert v[0] == pytest.approx(0x03 / float(MAX_ITEM_ID))
    assert v[1] == pytest.approx(5 / AMMO_QTY_NORM)
    assert v[2] == 0.0
    assert v[32] == pytest.approx(15 / 16.0)


def test_explain_obs_includes_box_section():
    box = encode_box([(0x01, 2)], in_box_room=True)
    ex = explain_obs({"box": box})
    assert "box" in ex
    assert len(ex["box"]) == BOX_DIM
    names = [row["name"] for row in ex["box"]]
    assert names[0] == "box0_item_id"
    assert names[-1] == "in_box_room"
    assert ex["box"][-1]["value"] == 1.0


def test_format_obs_table_suppresses_zero_box_slots():
    box = encode_box([(0x01, 2)], in_box_room=True)
    table = format_obs_table({"box": box})
    assert "--- box ---" in table
    assert "box0_item_id" in table
    assert "box1_item_id" not in table
    assert "box_free_slots" in table
    assert "in_box_room" in table
