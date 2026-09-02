"""Behavioural-cloning auxiliary term for PPO from human demos (DAPG-style).

Each ``CombatEfficientPPO.train()`` minibatch adds
``coef * mean(-log pi(a_demo | s_demo))`` over a random demo batch, with the
recorded legal-action mask applied. The demo directory is rescanned every
``reload_every`` train calls so freshly recorded episodes join without a
learner restart.

Env knobs (learner side):
    RE1_BC_DEMO_DIR      directory of demo ``*.npz`` (relative to project root)
    RE1_BC_COEF          starting coefficient (default 0.5)
    RE1_BC_COEF_DECAY    multiplicative decay per train() call (default 1.0)
    RE1_BC_COEF_MIN      floor after decay (default 0.05)
    RE1_BC_BATCH         demo minibatch size (default 128)
    RE1_BC_RELOAD_EVERY  rescan interval in train() calls (default 20)
    RE1_BC_INCLUDE_FAILS 1 to also learn from unsuccessful demos (default 0)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import torch as th

from re1_rl.demo_record import DemoDataset, demo_dir_signature, load_demo_dataset

DEMO_DIR_ENV = "RE1_BC_DEMO_DIR"
DEFAULT_BC_COEF = 0.5
DEFAULT_BC_COEF_DECAY = 1.0
DEFAULT_BC_COEF_MIN = 0.05
DEFAULT_BC_BATCH = 128
DEFAULT_BC_RELOAD_EVERY = 20


def _env_float(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _env_int(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def demo_dir_from_env(project_root: Path | str | None = None) -> Path | None:
    raw = (os.environ.get(DEMO_DIR_ENV) or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
        path = root / path
    return path


class DemoBCAux:
    """Holds the demo tensors and computes the masked BC log-likelihood term."""

    def __init__(
        self,
        demo_dir: Path,
        *,
        obs_shapes: dict[str, tuple[int, ...]],
        n_actions: int,
        device: th.device | str,
        obs_schema_version: int | None = None,
        coef: float = DEFAULT_BC_COEF,
        coef_decay: float = DEFAULT_BC_COEF_DECAY,
        coef_min: float = DEFAULT_BC_COEF_MIN,
        batch_size: int = DEFAULT_BC_BATCH,
        reload_every: int = DEFAULT_BC_RELOAD_EVERY,
        include_failures: bool = False,
        seed: int | None = None,
    ) -> None:
        self.demo_dir = Path(demo_dir)
        self.obs_shapes = dict(obs_shapes)
        self.n_actions = int(n_actions)
        self.device = th.device(device)
        self.obs_schema_version = obs_schema_version
        self.coef = float(max(coef, 0.0))
        self.coef_decay = float(min(max(coef_decay, 0.0), 1.0))
        self.coef_min = float(max(coef_min, 0.0))
        self.batch_size = int(max(batch_size, 1))
        self.reload_every = int(max(reload_every, 1))
        self.include_failures = bool(include_failures)
        self._rng = np.random.default_rng(seed)
        self._train_calls = 0
        self.n_batches = 0
        self._dataset: DemoDataset | None = None
        self._obs_t: dict[str, th.Tensor] = {}
        self._actions_t: th.Tensor | None = None
        self._masks_t: th.Tensor | None = None
        self.reload(force=True)

    @classmethod
    def from_env(
        cls,
        *,
        obs_shapes: dict[str, tuple[int, ...]],
        n_actions: int,
        device: th.device | str,
        obs_schema_version: int | None = None,
        project_root: Path | str | None = None,
    ) -> "DemoBCAux | None":
        demo_dir = demo_dir_from_env(project_root)
        if demo_dir is None:
            return None
        return cls(
            demo_dir,
            obs_shapes=obs_shapes,
            n_actions=n_actions,
            device=device,
            obs_schema_version=obs_schema_version,
            coef=_env_float("RE1_BC_COEF", DEFAULT_BC_COEF),
            coef_decay=_env_float("RE1_BC_COEF_DECAY", DEFAULT_BC_COEF_DECAY),
            coef_min=_env_float("RE1_BC_COEF_MIN", DEFAULT_BC_COEF_MIN),
            batch_size=_env_int("RE1_BC_BATCH", DEFAULT_BC_BATCH),
            reload_every=_env_int("RE1_BC_RELOAD_EVERY", DEFAULT_BC_RELOAD_EVERY),
            include_failures=(os.environ.get("RE1_BC_INCLUDE_FAILS") or "").strip() == "1",
        )

    # -- data -------------------------------------------------------------

    @property
    def n_samples(self) -> int:
        return 0 if self._dataset is None else len(self._dataset)

    @property
    def n_files(self) -> int:
        return 0 if self._dataset is None else len(self._dataset.files)

    @property
    def active(self) -> bool:
        return self.n_samples > 0 and self.coef > 0.0

    def reload(self, *, force: bool = False) -> bool:
        """Rescan the demo dir; rebuild tensors when the file set changed."""
        signature = demo_dir_signature(self.demo_dir)
        if not force and self._dataset is not None and signature == self._dataset.signature:
            return False
        if not force and self._dataset is None and not signature:
            return False
        dataset = load_demo_dataset(
            self.demo_dir,
            obs_shapes=self.obs_shapes,
            n_actions=self.n_actions,
            obs_schema_version=self.obs_schema_version,
            successful_only=not self.include_failures,
        )
        self._dataset = dataset
        if dataset is None:
            self._obs_t, self._actions_t, self._masks_t = {}, None, None
            print(f"[demo_bc] no usable demos under {self.demo_dir}", flush=True)
            return True
        self._obs_t = {k: th.as_tensor(v) for k, v in dataset.obs.items()}
        self._actions_t = th.as_tensor(dataset.actions, dtype=th.long)
        self._masks_t = th.as_tensor(dataset.masks, dtype=th.bool)
        print(
            f"[demo_bc] loaded {len(dataset)} decisions from {len(dataset.files)} demo(s) "
            f"under {self.demo_dir} coef={self.coef:.3f}",
            flush=True,
        )
        return True

    def on_train_call(self) -> None:
        """Once per ``train()``: decay coefficient, maybe rescan the demo dir."""
        self._train_calls += 1
        if self.coef_decay < 1.0:
            self.coef = max(self.coef_min, self.coef * self.coef_decay)
        if self._train_calls % self.reload_every == 0:
            self.reload()

    def sample(self) -> tuple[dict[str, th.Tensor], th.Tensor, th.Tensor] | None:
        if self._actions_t is None or self._masks_t is None or self.n_samples <= 0:
            return None
        n = self.n_samples
        take = min(self.batch_size, n)
        idx_np = self._rng.choice(n, size=take, replace=False)
        idx = th.as_tensor(idx_np, dtype=th.long)
        obs = {k: v[idx].to(self.device, non_blocking=True) for k, v in self._obs_t.items()}
        actions = self._actions_t[idx].to(self.device)
        masks = self._masks_t[idx].to(self.device)
        return obs, actions, masks

    # -- loss -------------------------------------------------------------

    def loss(self, policy: Any) -> tuple[th.Tensor, dict[str, float]] | None:
        """Masked negative log-likelihood of demo actions under ``policy``."""
        batch = self.sample()
        if batch is None:
            return None
        obs, actions, masks = batch
        self.n_batches += 1
        dist = policy.get_distribution(obs, action_masks=masks)
        log_prob = dist.log_prob(actions)
        bc_loss = -log_prob.mean()
        with th.no_grad():
            greedy = dist.distribution.probs.argmax(dim=-1)
            acc = (greedy == actions).float().mean()
        stats = {
            "train/bc_loss": float(bc_loss.detach()),
            "train/bc_acc": float(acc),
            "train/bc_n": float(self.n_samples),
            "train/bc_coef": float(self.coef),
        }
        return bc_loss, stats
