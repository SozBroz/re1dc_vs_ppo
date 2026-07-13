"""Stale-rollout relevance gate (truncated IS / PPO-ratio ownership).

When a rollout's ``policy_version`` is behind the learner by more than
``max_staleness``, hard-rejecting by version alone discards mid-horizon
leftovers that the *current* policy may still own.

Pragmatic first cut (not a full V-trace rewrite of PPO):

1. Recompute ``log π_new(a|s)`` under current MaskablePPO weights with the
   stored action masks (same path as ``evaluate_actions``).
2. Importance ratio ``ρ = exp(log π_new - log π_old)``.
3. A transition passes if ``ρ`` is finite, ``1/c ≤ ρ ≤ c``, and
   ``π_new(a|s) ≥ π_floor``.
4. Keep the rollout if at least ``keep_frac`` of its transitions pass;
   otherwise drop it.

Defaults favour reclaiming WH2 mid-horizon leftovers over strict on-policyness:
``c=2.0`` is wider than typical PPO ``ε=0.2`` clip so admitted samples can still
be clipped inside the surrogate. Truly garbage data (NaN / non-finite) fails
closed (transition drop; rollout drop if too few pass).

Refs: Schulman et al. PPO (2017); Espeholt et al. IMPALA V-trace (2018);
Schmitt et al. behaviour relevance (ICML 2020).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
from sb3_contrib import MaskablePPO
from stable_baselines3.common.utils import obs_as_tensor

from re1_rl.distributed.obs_preprocess import prepare_obs_for_policy
from re1_rl.distributed.rollout_types import WorkerRollout

# Wider than PPO ε≈0.2 so we reclaim stale-but-owned actions; surrogate still clips.
DEFAULT_RATIO_CLIP = 2.0
DEFAULT_PROB_FLOOR = 1e-8
DEFAULT_KEEP_FRAC = 0.5
DEFAULT_RELEVANCE_MAX_AGE = 8
DEFAULT_MICROBATCH = 64


@dataclass(frozen=True)
class RelevanceGateConfig:
    ratio_clip: float = DEFAULT_RATIO_CLIP
    prob_floor: float = DEFAULT_PROB_FLOOR
    keep_frac: float = DEFAULT_KEEP_FRAC
    microbatch: int = DEFAULT_MICROBATCH


@dataclass
class RelevanceGateStats:
    considered: int = 0
    kept: int = 0
    dropped: int = 0
    transitions_total: int = 0
    transitions_pass: int = 0
    transitions_fail: int = 0
    steps_kept: int = 0
    steps_dropped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "relevance_considered": self.considered,
            "relevance_kept": self.kept,
            "relevance_dropped": self.dropped,
            "relevance_transitions_total": self.transitions_total,
            "relevance_transitions_pass": self.transitions_pass,
            "relevance_transitions_fail": self.transitions_fail,
            "relevance_steps_kept": self.steps_kept,
            "relevance_steps_dropped": self.steps_dropped,
            "relevance_keep_rate": (
                float(self.kept) / float(self.considered) if self.considered else 0.0
            ),
            "relevance_step_keep_rate": (
                float(self.steps_kept)
                / float(self.steps_kept + self.steps_dropped)
                if (self.steps_kept + self.steps_dropped) > 0
                else 0.0
            ),
        }


def transition_relevance_mask(
    old_log_probs: np.ndarray,
    new_log_probs: np.ndarray,
    *,
    ratio_clip: float = DEFAULT_RATIO_CLIP,
    prob_floor: float = DEFAULT_PROB_FLOOR,
) -> np.ndarray:
    """Bool mask: True where π_new still owns the logged action enough to keep."""
    old_lp = np.asarray(old_log_probs, dtype=np.float64)
    new_lp = np.asarray(new_log_probs, dtype=np.float64)
    if old_lp.shape != new_lp.shape:
        raise ValueError(
            f"log_prob shape mismatch: old={old_lp.shape} new={new_lp.shape}"
        )
    finite = np.isfinite(old_lp) & np.isfinite(new_lp)
    # Clamp before exp to avoid overflow; non-finite already False via finite.
    log_ratio = np.clip(new_lp - old_lp, -20.0, 20.0)
    ratio = np.exp(log_ratio)
    lo = 1.0 / float(ratio_clip)
    hi = float(ratio_clip)
    in_band = (ratio >= lo) & (ratio <= hi)
    pi_new = np.exp(np.clip(new_lp, -20.0, 0.0))
    above_floor = pi_new >= float(prob_floor)
    return finite & in_band & above_floor


def compute_new_log_probs(
    model: MaskablePPO,
    rollout: WorkerRollout,
    *,
    microbatch: int = DEFAULT_MICROBATCH,
) -> np.ndarray:
    """``log π_new(a|s)`` under current weights, shape (n_steps, n_envs)."""
    if rollout.action_masks is None or rollout.action_masks.size == 0:
        raise ValueError("rollout missing action_masks (fail closed)")
    n_steps = int(rollout.n_steps)
    n_envs = int(rollout.n_envs)
    flat_n = n_steps * n_envs
    if flat_n == 0:
        return np.zeros((n_steps, n_envs), dtype=np.float32)

    obs_flat: dict[str, np.ndarray] = {}
    for key, arr in rollout.obs.items():
        # (T, E, ...) -> (T*E, ...)
        obs_flat[key] = np.asarray(arr).reshape(flat_n, *arr.shape[2:])
    actions_flat = np.asarray(rollout.actions).reshape(flat_n)
    masks_flat = np.asarray(rollout.action_masks).reshape(
        flat_n, rollout.action_masks.shape[-1]
    )

    out = np.empty(flat_n, dtype=np.float32)
    device = model.device
    mb = max(int(microbatch), 1)
    was_training = bool(model.policy.training)
    model.policy.set_training_mode(False)
    try:
        with torch.no_grad():
            for start in range(0, flat_n, mb):
                end = min(start + mb, flat_n)
                obs_mb = prepare_obs_for_policy(
                    {k: v[start:end] for k, v in obs_flat.items()},
                    model.observation_space,
                )
                obs_t = obs_as_tensor(obs_mb, device)
                act_t = torch.as_tensor(
                    actions_flat[start:end], device=device, dtype=torch.int64
                )
                mask_t = torch.as_tensor(
                    masks_flat[start:end], device=device, dtype=torch.bool
                )
                _values, log_probs, _entropy = model.policy.evaluate_actions(
                    obs_t, act_t, action_masks=mask_t
                )
                out[start:end] = log_probs.detach().float().cpu().numpy()
    finally:
        model.policy.set_training_mode(was_training)

    if not np.isfinite(out).all():
        # Fail closed per-transition via mask; keep array for diagnostics.
        pass
    return out.reshape(n_steps, n_envs)


def rollout_passes_relevance_gate(
    model: MaskablePPO,
    rollout: WorkerRollout,
    *,
    config: RelevanceGateConfig | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Return (keep, per-rollout stats)."""
    cfg = config or RelevanceGateConfig()
    new_lp = compute_new_log_probs(model, rollout, microbatch=cfg.microbatch)
    mask = transition_relevance_mask(
        rollout.log_probs,
        new_lp,
        ratio_clip=cfg.ratio_clip,
        prob_floor=cfg.prob_floor,
    )
    total = int(mask.size)
    n_pass = int(np.count_nonzero(mask))
    n_fail = total - n_pass
    frac = float(n_pass) / float(total) if total else 0.0
    keep = frac >= float(cfg.keep_frac) and total > 0
    ratios = np.exp(
        np.clip(
            np.asarray(new_lp, dtype=np.float64)
            - np.asarray(rollout.log_probs, dtype=np.float64),
            -20.0,
            20.0,
        )
    )
    finite_ratios = ratios[np.isfinite(ratios)]
    stats = {
        "keep": keep,
        "pass_frac": frac,
        "transitions_total": total,
        "transitions_pass": n_pass,
        "transitions_fail": n_fail,
        "ratio_median": float(np.median(finite_ratios)) if finite_ratios.size else float("nan"),
        "policy_version": int(rollout.policy_version),
        "worker_id": rollout.worker_id,
    }
    return keep, stats


def is_version_stale_for_gate(
    policy_version: int,
    current_policy_version: int,
    max_staleness: int,
) -> bool:
    """True when version would hard-reject under classic max_staleness."""
    min_ok = int(current_policy_version) - int(max_staleness)
    return int(policy_version) < min_ok


def filter_stale_rollouts(
    model: MaskablePPO,
    rollouts: list[WorkerRollout],
    *,
    current_policy_version: int,
    max_staleness: int,
    config: RelevanceGateConfig | None = None,
) -> tuple[list[WorkerRollout], RelevanceGateStats, list[dict[str, Any]]]:
    """Keep fresh rollouts; gate stale ones by π_new ownership."""
    cfg = config or RelevanceGateConfig()
    kept: list[WorkerRollout] = []
    stats = RelevanceGateStats()
    details: list[dict[str, Any]] = []
    for r in rollouts:
        if not is_version_stale_for_gate(
            r.policy_version, current_policy_version, max_staleness
        ):
            kept.append(r)
            continue
        stats.considered += 1
        ok, detail = rollout_passes_relevance_gate(model, r, config=cfg)
        details.append(detail)
        stats.transitions_total += int(detail["transitions_total"])
        stats.transitions_pass += int(detail["transitions_pass"])
        stats.transitions_fail += int(detail["transitions_fail"])
        if ok:
            stats.kept += 1
            stats.steps_kept += int(r.num_timesteps())
            kept.append(r)
        else:
            stats.dropped += 1
            stats.steps_dropped += int(r.num_timesteps())
    return kept, stats, details
