"""Outcome-trained loadout value and bounded learner-side box guidance."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any, Iterable

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from re1_rl.action_mask import (
    DEPOSIT_ACTION_BASE,
    N_DEPOSIT_ACTIONS,
    N_WITHDRAW_ACTIONS,
    WITHDRAW_ACTION_BASE,
)
from re1_rl.obs_encoder import BOX_DIM, INVENTORY_OBS_DIM, LOGISTICS_DIM

LOADOUT_FEATURE_DIM = LOGISTICS_DIM + INVENTORY_OBS_DIM + BOX_DIM
LOADOUT_TARGET_DIM = 3  # completion, survival, normalized segment progress
LOADOUT_REPLAY_CAPACITY = 4096
LOADOUT_MIN_POSITIVE = 8
LOADOUT_MIN_NEGATIVE = 8
LOADOUT_MAX_BRIER = 0.30
LOADOUT_AUX_COEF = 0.02
LOADOUT_TRANSFER_BOUND = 0.01
LOADOUT_VISIT_BOUND = 0.05


class LoadoutValueNet(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(LOADOUT_FEATURE_DIM, 64),
            nn.ReLU(),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, LOADOUT_TARGET_DIM),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.net(features)


class LoadoutReplay:
    def __init__(self, capacity: int = LOADOUT_REPLAY_CAPACITY) -> None:
        self._rows: deque[tuple[np.ndarray, np.ndarray]] = deque(maxlen=int(capacity))

    def add(self, sample: dict[str, Any]) -> bool:
        x = np.asarray(sample.get("features"), dtype=np.float32)
        y = np.asarray(sample.get("labels"), dtype=np.float32)
        if x.shape != (LOADOUT_FEATURE_DIM,) or y.shape != (LOADOUT_TARGET_DIM,):
            return False
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            return False
        self._rows.append((x, np.clip(y, 0.0, 1.0)))
        return True

    def extend(self, samples: Iterable[dict[str, Any]]) -> int:
        return sum(self.add(sample) for sample in samples)

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        if not self._rows:
            return (
                np.zeros((0, LOADOUT_FEATURE_DIM), dtype=np.float32),
                np.zeros((0, LOADOUT_TARGET_DIM), dtype=np.float32),
            )
        return np.stack([row[0] for row in self._rows]), np.stack(
            [row[1] for row in self._rows]
        )

    def __len__(self) -> int:
        return len(self._rows)


def loadout_samples_from_infos(infos: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(info["logistics_sample"])
        for info in infos
        if isinstance(info, dict) and isinstance(info.get("logistics_sample"), dict)
    ]


def train_loadout_scorer(
    model: LoadoutValueNet,
    replay: LoadoutReplay,
    optimizer: torch.optim.Optimizer,
    *,
    device: torch.device,
    epochs: int = 4,
    batch_size: int = 128,
) -> dict[str, float]:
    x_np, y_np = replay.arrays()
    if len(x_np) == 0:
        return {"samples": 0.0, "calibrated": 0.0}
    x = torch.as_tensor(x_np, device=device)
    y = torch.as_tensor(y_np, device=device)
    model.train()
    for _ in range(max(1, int(epochs))):
        order = torch.randperm(x.shape[0], device=device)
        for start in range(0, x.shape[0], max(1, int(batch_size))):
            idx = order[start : start + batch_size]
            pred = model(x[idx])
            loss = (
                F.binary_cross_entropy_with_logits(pred[:, :2], y[idx, :2])
                + F.smooth_l1_loss(torch.sigmoid(pred[:, 2]), y[idx, 2])
            )
            optimizer.zero_grad()
            (LOADOUT_AUX_COEF * loss).backward()
            optimizer.step()
    model.eval()
    with torch.no_grad():
        prob = torch.sigmoid(model(x))
        brier = float(((prob[:, 0] - y[:, 0]) ** 2).mean().cpu())
    positives = int((y_np[:, 0] >= 0.5).sum())
    negatives = int((y_np[:, 0] < 0.5).sum())
    calibrated = (
        positives >= LOADOUT_MIN_POSITIVE
        and negatives >= LOADOUT_MIN_NEGATIVE
        and brier <= LOADOUT_MAX_BRIER
    )
    return {
        "samples": float(len(x_np)),
        "positives": float(positives),
        "negatives": float(negatives),
        "brier": brier,
        "calibrated": 1.0 if calibrated else 0.0,
    }


def frozen_copy(model: LoadoutValueNet) -> LoadoutValueNet:
    frozen = deepcopy(model).eval()
    for parameter in frozen.parameters():
        parameter.requires_grad_(False)
    return frozen


def _features(merged: dict[str, Any], step: int, env: int) -> np.ndarray:
    obs = merged["obs"]
    return np.concatenate(
        [
            np.asarray(obs["logistics"][step, env], dtype=np.float32),
            np.asarray(obs["inventory"][step, env], dtype=np.float32),
            np.asarray(obs["box"][step, env], dtype=np.float32),
        ]
    )


def apply_bounded_loadout_guidance(
    merged: dict[str, Any],
    scorer: LoadoutValueNet,
    *,
    device: torch.device,
    calibrated: bool,
) -> dict[str, float]:
    """Apply frozen potential deltas to legal box transfers already collected."""
    stats = {
        "transfers": 0.0,
        "total": 0.0,
        "calibrated": float(calibrated),
        "predicted_before": 0.0,
        "predicted_after": 0.0,
    }
    if not calibrated or not {"logistics", "inventory", "box"}.issubset(merged["obs"]):
        return stats
    rewards = merged["rewards"]
    actions = merged["actions"]
    dones = merged["dones"]
    n_steps, n_envs = rewards.shape
    scorer.eval()
    for env in range(n_envs):
        visit_total = 0.0
        for step in range(n_steps - 1):
            action = int(actions[step, env])
            is_transfer = (
                DEPOSIT_ACTION_BASE <= action < DEPOSIT_ACTION_BASE + N_DEPOSIT_ACTIONS
                or WITHDRAW_ACTION_BASE
                <= action
                < WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS
            )
            in_box = float(merged["obs"]["box"][step, env, -1]) > 0.5
            next_in_box = float(merged["obs"]["box"][step + 1, env, -1]) > 0.5
            if not in_box or not next_in_box or bool(dones[step, env]):
                visit_total = 0.0
                continue
            if not is_transfer:
                continue
            pair = np.stack(
                [_features(merged, step, env), _features(merged, step + 1, env)]
            )
            with torch.no_grad():
                values = torch.sigmoid(
                    scorer(torch.as_tensor(pair, device=device))
                )[:, 0]
            delta = float((values[1] - values[0]).cpu())
            delta = float(np.clip(delta, -LOADOUT_TRANSFER_BOUND, LOADOUT_TRANSFER_BOUND))
            allowed = float(
                np.clip(
                    visit_total + delta,
                    -LOADOUT_VISIT_BOUND,
                    LOADOUT_VISIT_BOUND,
                )
                - visit_total
            )
            rewards[step, env] += allowed
            visit_total += allowed
            stats["transfers"] += 1.0
            stats["total"] += allowed
            stats["predicted_before"] += float(values[0].cpu())
            stats["predicted_after"] += float(values[1].cpu())
    if stats["transfers"] > 0.0:
        stats["predicted_before"] /= stats["transfers"]
        stats["predicted_after"] /= stats["transfers"]
    return stats
