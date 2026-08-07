"""Learned box-to-outcome logistics contracts."""

from __future__ import annotations

import inspect

import numpy as np
import pytest
import torch
from torch import nn

import re1_rl.loadout_learning as loadout_learning
from re1_rl.action_mask import DEPOSIT_ACTION_BASE, WITHDRAW_ACTION_BASE
from re1_rl.loadout_learning import (
    LOADOUT_FEATURE_DIM,
    LOADOUT_TRANSFER_BOUND,
    LOADOUT_VISIT_BOUND,
    LoadoutReplay,
    apply_bounded_loadout_guidance,
)
from re1_rl.obs_encoder import BOX_DIM, INVENTORY_OBS_DIM, LOGISTICS_DIM, ObsEncoder
from re1_rl.progress import ProgressTracker
from re1_rl.yawn_rails import apply_logistics_feasibility_mask
from tests.test_yawn_rails import ROOMS, _graph, _planner, _state


def test_horizon_aggregates_box_to_boss_route_semantics() -> None:
    encoder = ObsEncoder(ROOMS, _graph(), curriculum_stage_index=2)
    planner = _planner(start_index=47)
    state = _state("118", inventory=["shield_key", "shotgun"])
    state["inventory_slots"] = [
        ("shield_key", 1),
        ("shotgun", 7),
        ("", 0),
        ("", 0),
        ("", 0),
        ("", 0),
        ("", 0),
        ("", 0),
    ]
    logistics = encoder.encode_logistics(state, planner)
    assert logistics.shape == (LOGISTICS_DIM,)
    assert logistics[0] == 1.0
    assert logistics[10] == 1.0  # boss ahead
    assert logistics[11] == 1.0  # no later box before boss
    assert logistics[12] == 0.0
    assert logistics[13] > 0.0  # factual graph distance to boss
    assert logistics[15] == 1.0  # route requirements are already held


def test_segment_snapshot_survives_arbitrary_rollout_cut() -> None:
    progress = ProgressTracker()
    features = [0.25] * LOADOUT_FEATURE_DIM
    progress.begin_loadout_segment(
        features,
        waypoint_index=48,
        horizon_checkpoints=5,
        departure_room="118",
        departure_inventory=[("shield_key", 1), ("", 0)],
    )
    # A rollout boundary does not reset ProgressTracker or emit a fake label.
    assert progress.pop_loadout_sample() is None
    assert progress.loadout_segment is not None
    sample = progress.finish_loadout_segment(
        waypoint_index=53,
        survived=True,
        completed=True,
        outcome="boss_complete",
    )
    assert sample is not None
    assert sample["features"] == features
    assert sample["labels"] == [1.0, 1.0, 1.0]
    assert sample["departure_inventory"] == [("shield_key", 1), ("", 0)]


def test_replay_rejects_malformed_and_keeps_factual_labels() -> None:
    replay = LoadoutReplay(capacity=2)
    assert not replay.add({"features": [0.0], "labels": [1.0, 1.0, 1.0]})
    assert replay.add({
        "features": [0.0] * LOADOUT_FEATURE_DIM,
        "labels": [0.0, 1.0, 0.4],
    })
    x, y = replay.arrays()
    assert x.shape == (1, LOADOUT_FEATURE_DIM)
    assert y[0, :2].tolist() == [0.0, 1.0]
    assert y[0, 2] == pytest.approx(0.4)


class _InventoryValue(nn.Module):
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        value = x[:, LOGISTICS_DIM]
        return torch.stack([value, value * 0.0, value * 0.0], dim=1)


def test_guidance_is_box_gated_and_bounded_per_transfer_and_visit() -> None:
    steps = 8
    obs = {
        "logistics": np.zeros((steps, 1, LOGISTICS_DIM), dtype=np.float32),
        "inventory": np.zeros((steps, 1, INVENTORY_OBS_DIM), dtype=np.float32),
        "box": np.zeros((steps, 1, BOX_DIM), dtype=np.float32),
    }
    obs["box"][:, :, -1] = 1.0
    obs["inventory"][:, 0, 0] = np.arange(steps, dtype=np.float32)
    merged = {
        "obs": obs,
        "actions": np.full((steps, 1), DEPOSIT_ACTION_BASE, dtype=np.int64),
        "rewards": np.zeros((steps, 1), dtype=np.float32),
        "dones": np.zeros((steps, 1), dtype=np.bool_),
    }
    stats = apply_bounded_loadout_guidance(
        merged, _InventoryValue(), device=torch.device("cpu"), calibrated=True
    )
    assert np.max(np.abs(merged["rewards"])) <= LOADOUT_TRANSFER_BOUND + 1e-8
    assert abs(float(merged["rewards"].sum())) <= LOADOUT_VISIT_BOUND + 1e-8
    assert stats["transfers"] == steps - 1
    assert stats["predicted_after"] > stats["predicted_before"]

    outside = {key: value.copy() for key, value in obs.items()}
    outside["box"][:, :, -1] = 0.0
    merged["obs"] = outside
    merged["rewards"][:] = 0.0
    apply_bounded_loadout_guidance(
        merged, _InventoryValue(), device=torch.device("cpu"), calibrated=True
    )
    assert merged["rewards"].sum() == 0.0


def test_scorer_contains_no_prescribed_weapon_ammo_or_heal_targets() -> None:
    source = inspect.getsource(loadout_learning).lower()
    for forbidden in ("shotgun", "beretta", "handgun_bullets", "first_aid", "green_herb"):
        assert forbidden not in source


def test_feasibility_guard_keeps_required_items_and_pickup_headroom() -> None:
    inventory = [
        (0x35, 1),  # shield key: factual route requirement
        (0x03, 7),  # shotgun: factual route requirement
        (0x01, 0),
        (0x02, 15),
        (0x41, 1),
        (0x31, 1),
        (0, 0),
        (0, 0),
    ]
    box = [(0x44, 1)] + [(0, 0)] * 15
    mask = np.ones(45, dtype=np.bool_)
    apply_logistics_feasibility_mask(mask, inventory, box, _planner(start_index=47))
    assert not mask[DEPOSIT_ACTION_BASE]  # cannot omit sole required shield key
    assert not mask[DEPOSIT_ACTION_BASE + 1]  # cannot omit sole required shotgun
    assert not mask[WITHDRAW_ACTION_BASE]  # preserve two declared pickup slots
