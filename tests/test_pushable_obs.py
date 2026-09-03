"""Pushable-object observation slots (armor + dining)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.armor_room_puzzle import (
    ARMOR_EAST_SCRIPT_TARGET,
    ARMOR_VENT_FAR_BEAT,
    ARMOR_WEST_SCRIPT_TARGET,
)
from re1_rl.pushable_obs import (
    PUSHABLE_SLOT_DIM,
    PUSHABLES_DIM,
    encode_pushables,
)
from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION, make_re1_spaces


def _armor_state(**kwargs):
    base = {
        "room_id": "205",
        "x": 14000,
        "z": 6500,
        "facing": 0,
        "game_state": 0,
        "armor_east_statue_x": 14035,
        "armor_east_statue_z": 6190,
        "armor_west_statue_x": 8795,
        "armor_west_statue_z": 7886,
        # Mirrors agree with primary (stable).
        "armor_east_statue_x_b": 14035,
        "armor_east_statue_z_b": 6190,
        "armor_east_statue_x_c": 14035,
        "armor_east_statue_z_c": 6190,
        "armor_west_statue_x_b": 8795,
        "armor_west_statue_z_b": 7886,
        "armor_west_statue_x_c": 8795,
        "armor_west_statue_z_c": 7886,
    }
    base.update(kwargs)
    return base


def test_pushables_zero_outside_puzzle() -> None:
    v = encode_pushables({"room_id": "106", "x": 0, "z": 0, "facing": 0})
    assert v.shape == (PUSHABLES_DIM,)
    assert float(v.sum()) == 0.0


def test_armor_pl80_slots_live_and_crumb_target() -> None:
    # East already on vent; west still at rest. Far-vent step → west is active.
    queue = SimpleNamespace(current={"beat_id": ARMOR_VENT_FAR_BEAT, "site_id": "armor_vent_far"})
    state = _armor_state(
        armor_east_statue_x=ARMOR_EAST_SCRIPT_TARGET[0],
        armor_east_statue_z=ARMOR_EAST_SCRIPT_TARGET[1],
        armor_east_statue_x_b=ARMOR_EAST_SCRIPT_TARGET[0],
        armor_east_statue_z_b=ARMOR_EAST_SCRIPT_TARGET[1],
        armor_east_statue_x_c=ARMOR_EAST_SCRIPT_TARGET[0],
        armor_east_statue_z_c=ARMOR_EAST_SCRIPT_TARGET[1],
    )
    v = encode_pushables(state, queue=queue)
    east = v[:PUSHABLE_SLOT_DIM]
    west = v[PUSHABLE_SLOT_DIM:]
    assert east[0] == 1.0 and west[0] == 1.0
    assert east[9] == 0.0  # not the active crumb target
    assert west[9] == 1.0  # west pays ±0.5 on shove
    assert east[10] == 1.0  # east seated on vent
    assert west[10] == 0.0
    # Remaining west→vent matches ARMOR_WEST_SCRIPT_TARGET - live west.
    rem_dx = (ARMOR_WEST_SCRIPT_TARGET[0] - 8795) / 4096.0
    rem_dz = (ARMOR_WEST_SCRIPT_TARGET[1] - 7886) / 4096.0
    assert west[6] == np.float32(np.clip(rem_dx, -2.0, 2.0))
    assert west[7] == np.float32(np.clip(rem_dz, -2.0, 2.0))
    # Jill→west compass distance is positive.
    assert west[3] > 0.0


def test_dining_pl95_pushables_live_on_loyal_queue() -> None:
    from re1_rl.dining_statue_puzzle import DINING_STATUE_DROP_XZ

    queue = SimpleNamespace(
        current={
            "beat_id": "push_statue_2f",
            "site_id": "dining_statue_knocked",
            "op": "do_puzzle",
            "room_id": "202",
        }
    )
    state = {
        "room_id": "202",
        "x": 16000,
        "z": 3000,
        "facing": 0,
        "dining_statue_knocked": False,
        "dining_statue_x": 19000,
        "dining_statue_z": 3452,
    }
    v = encode_pushables(state, queue=queue)
    slot = v[:PUSHABLE_SLOT_DIM]
    assert slot[0] == 1.0
    assert slot[9] == 1.0  # active
    rem_dx = (DINING_STATUE_DROP_XZ[0] - 19000) / 4096.0
    rem_dz = (DINING_STATUE_DROP_XZ[1] - 3452) / 4096.0
    assert slot[6] == np.float32(np.clip(rem_dx, -2.0, 2.0))
    assert slot[7] == np.float32(np.clip(rem_dz, -2.0, 2.0))
    # Enter-only step must not light pushables.
    queue.current = {"beat_id": "dining_2f_enter", "op": "traverse", "edge_id": "203->202"}
    assert float(encode_pushables(state, queue=queue).sum()) == 0.0


def test_spaces_include_pushables() -> None:
    assert OBS_SCHEMA_VERSION == 3
    obs, _ = make_re1_spaces()
    assert "pushables" in obs.spaces
    assert obs.spaces["pushables"].shape == (PUSHABLES_DIM,)


def test_extractor_pushables_residual_zero_init() -> None:
    import torch as th

    from re1_rl.combat_efficient_extractor import RE1CombatEfficientExtractor
    from re1_rl.distributed.spaces import make_re1_policy_spaces

    obs_space, _ = make_re1_policy_spaces()
    # Planner-loyal spaces include planner_steps when the env flag is on; force
    # the key in so the test matches fleet.
    import os

    os.environ["RE1_PLANNER_LOYAL"] = "1"
    obs_space, _ = make_re1_policy_spaces()
    assert "pushables" in obs_space.spaces
    extractor = RE1CombatEfficientExtractor(obs_space)
    assert extractor.pushables_proj is not None
    assert th.count_nonzero(extractor.pushables_proj[-1].weight) == 0
    batch = {
        k: th.zeros((2, *sp.shape), dtype=th.float32 if sp.dtype != np.uint8 else th.uint8)
        for k, sp in obs_space.spaces.items()
    }
    batch["frame"] = th.zeros((2, *obs_space.spaces["frame"].shape), dtype=th.uint8)
    out = extractor(batch)
    assert out.shape == (2, 1024)
    assert th.isfinite(out).all()
