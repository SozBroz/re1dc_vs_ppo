"""Ablation infrastructure: FiLM, ModDrop, discriminative LR, freeze helpers.

Never uses ordinary unstored ``nn.Dropout``. ModDrop masks must be sampled
before action, stored in the rollout, and reused for every PPO epoch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np
import torch as th
from torch import nn

from re1_rl.modality_config import (
    discriminative_lr_enabled,
    discriminative_lr_mult,
    mod_drop_enabled,
    mod_drop_rate,
)

# Semantic branches eligible for ModDrop. Goal/compass are NEVER dropped.
MOD_DROP_BRANCHES: tuple[str, ...] = (
    "vision",
    "control",
    "spatial",
    "inventory",
    "history",
    "flags",
    "joint",
    "world",
    "persistent",
)
MOD_DROP_DIM = len(MOD_DROP_BRANCHES)
PROTECTED_BRANCHES: frozenset[str] = frozenset({"goal"})

# Must match combat_efficient_extractor tower widths (kept local to avoid import cycles).
TOWER_DIMS: dict[str, int] = {
    "vision": 512,
    "control": 64,
    "spatial": 192,
    "inventory": 160,
    "history": 192,
    "flags": 64,
    "goal": 48,
    "joint": 128,
    "world": 320,
    "persistent": 96,
}

# Concat order in RE1CombatEfficientExtractor.forward_features
TOWER_ORDER: tuple[str, ...] = (
    "vision",
    "control",
    "spatial",
    "inventory",
    "history",
    "flags",
    "goal",
    "joint",
    "world",
    "persistent",
)


def tower_slices(*, persistent_enabled: bool = True) -> dict[str, slice]:
    """Byte-accurate slices into the tower concat vector."""
    out: dict[str, slice] = {}
    start = 0
    for name in TOWER_ORDER:
        if name == "persistent" and not persistent_enabled:
            continue
        width = TOWER_DIMS[name]
        out[name] = slice(start, start + width)
        start += width
    return out


class IdentityFiLM(nn.Module):
    """FiLM: ``γ(g) ⊙ h + β(g)`` with identity init (γ=1, β=0 → KL≈0)."""

    def __init__(self, cond_dim: int, feature_dim: int) -> None:
        super().__init__()
        self.feature_dim = int(feature_dim)
        self.net = nn.Linear(cond_dim, feature_dim * 2)
        nn.init.zeros_(self.net.weight)
        nn.init.zeros_(self.net.bias)
        with th.no_grad():
            self.net.bias[:feature_dim].fill_(1.0)

    def forward(self, h: th.Tensor, cond: th.Tensor) -> th.Tensor:
        gb = self.net(cond)
        gamma, beta = gb.chunk(2, dim=-1)
        return gamma * h + beta


def full_keep_mask(batch: int, *, dtype: np.dtype = np.float32) -> np.ndarray:
    """Presence=1 for every droppable branch."""
    return np.ones((batch, MOD_DROP_DIM), dtype=dtype)


def sample_mod_drop_mask(
    batch: int,
    *,
    rate: float | None = None,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Sample per-row ModDrop presence masks.

    Majority rows keep all branches. With probability ``rate``, drop exactly one
    non-goal branch (presence bit 0). Never drops goal/compass.
    """
    rate = mod_drop_rate() if rate is None else float(rate)
    rng = np.random.default_rng() if rng is None else rng
    masks = full_keep_mask(batch)
    if rate <= 0.0 or batch <= 0:
        return masks
    drop_rows = rng.random(batch) < rate
    if not np.any(drop_rows):
        return masks
    n_drop = int(np.sum(drop_rows))
    which = rng.integers(0, MOD_DROP_DIM, size=n_drop)
    row_idx = np.flatnonzero(drop_rows)
    masks[row_idx, which] = 0.0
    return masks


@dataclass
class ModDropEpisodeState:
    """Per-env mask fixed for an episode/segment; resample on done."""

    n_envs: int
    rate: float = field(default_factory=mod_drop_rate)
    rng: np.random.Generator = field(default_factory=np.random.default_rng)
    masks: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.masks = sample_mod_drop_mask(self.n_envs, rate=self.rate, rng=self.rng)

    def on_dones(self, dones: np.ndarray | Sequence[bool]) -> None:
        dones_arr = np.asarray(dones, dtype=bool).reshape(-1)
        for i, done in enumerate(dones_arr):
            if done and i < self.n_envs:
                self.masks[i] = sample_mod_drop_mask(1, rate=self.rate, rng=self.rng)[0]


def apply_mod_drop_to_parts(
    parts: dict[str, th.Tensor],
    presence: th.Tensor,
) -> dict[str, th.Tensor]:
    """Zero droppable towers according to presence (1=keep, 0=drop). Goal untouched."""
    if presence.dim() != 2 or presence.shape[1] != MOD_DROP_DIM:
        raise ValueError(
            f"mod_drop presence expected (B, {MOD_DROP_DIM}), got {tuple(presence.shape)}"
        )
    out = dict(parts)
    for i, name in enumerate(MOD_DROP_BRANCHES):
        if name not in out:
            continue
        out[name] = out[name] * presence[:, i : i + 1]
    return out


def mature_tower_param_names() -> frozenset[str]:
    return frozenset({"cnn_extractor", "world_context"})


def goal_history_fusion_param_names() -> frozenset[str]:
    return frozenset(
        {
            "goal_mlp",
            "goal_lookahead_token",
            "goal_lookahead_out",
            "logistics_mlp",
            "history_encoder",
            "fusion_norm",
            "fusion_proj",
            "goal_film_vision",
            "goal_film_spatial",
            "mod_drop_presence",
        }
    )


def build_discriminative_param_groups(
    policy: nn.Module,
    base_lr: float,
    *,
    mature_mult: float | None = None,
) -> list[dict[str, Any]]:
    """Mature CNN/world at reduced LR; goal/history/fusion and rest at full LR."""
    mult = discriminative_lr_mult() if mature_mult is None else float(mature_mult)
    extractor = getattr(policy, "features_extractor", None)
    if extractor is None:
        return [{"params": list(policy.parameters()), "lr": float(base_lr)}]

    mature_ids: set[int] = set()
    full_focus_ids: set[int] = set()
    for name, module in extractor.named_children():
        ids = {id(p) for p in module.parameters()}
        if name in mature_tower_param_names():
            mature_ids |= ids
        if name in goal_history_fusion_param_names():
            full_focus_ids |= ids

    mature_params: list[nn.Parameter] = []
    full_params: list[nn.Parameter] = []
    other_params: list[nn.Parameter] = []
    for param in policy.parameters():
        if not param.requires_grad:
            continue
        pid = id(param)
        if pid in mature_ids:
            mature_params.append(param)
        elif pid in full_focus_ids:
            full_params.append(param)
        else:
            other_params.append(param)

    groups: list[dict[str, Any]] = []
    if mature_params:
        groups.append({"params": mature_params, "lr": float(base_lr) * mult})
    if full_params:
        groups.append({"params": full_params, "lr": float(base_lr)})
    if other_params:
        # Mid-tier towers: interpolate toward mature (geometric mean of 1 and mult).
        mid_lr = float(base_lr) * float(np.sqrt(mult))
        groups.append({"params": other_params, "lr": mid_lr})
    if not groups:
        groups.append({"params": list(policy.parameters()), "lr": float(base_lr)})
    return groups


def maybe_apply_discriminative_optimizer(model: Any) -> bool:
    """Rebuild Adam with discriminative groups when ``RE1_DISC_LR=1``. Returns True if applied."""
    if not discriminative_lr_enabled():
        return False
    policy = model.policy
    base_lr = float(model.lr_schedule(1.0)) if callable(model.lr_schedule) else float(
        getattr(model, "learning_rate", 3e-4)
    )
    groups = build_discriminative_param_groups(policy, base_lr)
    model.policy.optimizer = th.optim.Adam(groups)
    return True


@dataclass
class ModuleFreezeHandle:
    """Temporary ``requires_grad=False`` for selected modules over N updates."""

    modules: list[nn.Module]
    remaining: int
    _saved: list[list[bool]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._saved = []
        for module in self.modules:
            flags = [bool(p.requires_grad) for p in module.parameters()]
            self._saved.append(flags)
            for p in module.parameters():
                p.requires_grad_(False)

    def tick(self, n: int = 1) -> bool:
        """Countdown; restore grads when remaining hits 0. Returns True if still frozen."""
        self.remaining = max(0, self.remaining - int(n))
        if self.remaining > 0:
            return True
        for module, flags in zip(self.modules, self._saved):
            for param, was in zip(module.parameters(), flags):
                param.requires_grad_(was)
        self._saved = []
        return False


def freeze_modules_for_n_updates(
    modules: Iterable[nn.Module],
    n_updates: int,
) -> ModuleFreezeHandle:
    """Protect mature towers during a short curriculum transition (not 'squeeze')."""
    mods = [m for m in modules if m is not None]
    if n_updates <= 0 or not mods:
        raise ValueError("freeze_modules_for_n_updates requires modules and n_updates > 0")
    return ModuleFreezeHandle(modules=mods, remaining=int(n_updates))


def resolve_extractor_modules(extractor: nn.Module, names: Sequence[str]) -> list[nn.Module]:
    found: list[nn.Module] = []
    for name in names:
        mod = getattr(extractor, name, None)
        if mod is None:
            raise AttributeError(f"extractor has no module {name!r}")
        found.append(mod)
    return found


def mod_drop_active() -> bool:
    return mod_drop_enabled()
