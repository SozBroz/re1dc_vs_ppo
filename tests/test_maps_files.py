"""Maps/files flags obs tests."""

from __future__ import annotations

import numpy as np

from re1_rl.maps_files import MAPS_FILES_DIM, encode_maps_files_flags


def test_encode_maps_files_flags_bits() -> None:
    v = encode_maps_files_flags(0b1010)
    assert v.shape == (MAPS_FILES_DIM,)
    assert v[1] == 1.0
    assert v[3] == 1.0
    assert float(v.sum()) == 2.0
