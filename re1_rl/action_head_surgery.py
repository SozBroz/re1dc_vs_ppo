"""Surgical edits to the discrete action head (bias / weight rows)."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np
import torch

from re1_rl.env import ACTION_NAMES

_MOVEMENT = ("forward", "back", "turn_left", "turn_right", "run_forward")


def drain_action_logits(
    model: Any,
    *,
    actions: Sequence[str] = ("equip",),
    factor: float = 100.0,
    weight_scale: float = 0.25,
    align_to_movement: bool = True,
) -> dict[str, Any]:
    """Bleed overconfident action rows toward a low prior.

    Matches the project's transplant convention of subtracting ``log(factor)``
    from bias (see legacy action-head expansion). Optionally anchors drained
    biases to the mean movement bias, and scales the corresponding weight row
    so feature-driven logits cannot immediately recreate the spam.
    """
    if factor <= 0:
        raise ValueError(f"factor must be > 0, got {factor}")
    if not (0.0 <= float(weight_scale) <= 1.0):
        raise ValueError(f"weight_scale must be in [0, 1], got {weight_scale}")

    action_net = model.policy.action_net
    bias = action_net.bias
    weight = action_net.weight
    if bias is None or weight is None:
        raise RuntimeError("policy.action_net missing bias/weight")

    name_to_idx = {name: i for i, name in enumerate(ACTION_NAMES)}
    missing = [a for a in actions if a not in name_to_idx]
    if missing:
        raise KeyError(f"unknown actions: {missing}")

    move_idx = [name_to_idx[a] for a in _MOVEMENT]
    delta = float(np.log(float(factor)))
    report: dict[str, Any] = {
        "factor": float(factor),
        "delta": delta,
        "weight_scale": float(weight_scale),
        "align_to_movement": bool(align_to_movement),
        "actions": {},
    }

    with torch.no_grad():
        move_mean = float(bias[move_idx].mean().detach().cpu())
        report["movement_bias_mean"] = move_mean
        for name in actions:
            i = name_to_idx[name]
            before_b = float(bias[i].detach().cpu())
            before_wn = float(weight[i].detach().norm().cpu())
            target_b = (move_mean - delta) if align_to_movement else (before_b - delta)
            bias[i].fill_(target_b)
            weight[i].mul_(float(weight_scale))
            after_b = float(bias[i].detach().cpu())
            after_wn = float(weight[i].detach().norm().cpu())
            report["actions"][name] = {
                "index": i,
                "bias_before": before_b,
                "bias_after": after_b,
                "weight_norm_before": before_wn,
                "weight_norm_after": after_wn,
            }
    return report


def boost_action_logits(
    model: Any,
    *,
    actions: Sequence[str] = ("attack",),
    factor: float = 2.0,
) -> dict[str, Any]:
    """Add a modest prior to action bias rows: ``bias += log(factor)``.

    Default ``factor=2`` (≈ +0.69) roughly doubles relative odds vs peers —
    enough to prefer an under-used action without the multi-hour unlearn of a
    ``log(100)`` transplant-scale shove. Does not touch weight rows.
    """
    if factor <= 0:
        raise ValueError(f"factor must be > 0, got {factor}")

    action_net = model.policy.action_net
    bias = action_net.bias
    if bias is None:
        raise RuntimeError("policy.action_net missing bias")

    name_to_idx = {name: i for i, name in enumerate(ACTION_NAMES)}
    missing = [a for a in actions if a not in name_to_idx]
    if missing:
        raise KeyError(f"unknown actions: {missing}")

    delta = float(np.log(float(factor)))
    report: dict[str, Any] = {
        "factor": float(factor),
        "delta": delta,
        "actions": {},
    }
    with torch.no_grad():
        for name in actions:
            i = name_to_idx[name]
            before_b = float(bias[i].detach().cpu())
            bias[i].add_(delta)
            after_b = float(bias[i].detach().cpu())
            report["actions"][name] = {
                "index": i,
                "bias_before": before_b,
                "bias_after": after_b,
            }
    return report


def format_drain_report(report: dict[str, Any]) -> Iterable[str]:
    yield (
        f"drain factor={report['factor']} delta={report['delta']:.3f} "
        f"weight_scale={report['weight_scale']} "
        f"move_bias_mean={report.get('movement_bias_mean', float('nan')):.3f}"
    )
    for name, row in report.get("actions", {}).items():
        yield (
            f"  {name}[{row['index']}]: bias {row['bias_before']:.3f}->{row['bias_after']:.3f} "
            f"||w|| {row['weight_norm_before']:.3f}->{row['weight_norm_after']:.3f}"
        )


def format_boost_report(report: dict[str, Any]) -> Iterable[str]:
    yield f"boost factor={report['factor']} delta={report['delta']:.3f}"
    for name, row in report.get("actions", {}).items():
        yield (
            f"  {name}[{row['index']}]: bias {row['bias_before']:.3f}->{row['bias_after']:.3f}"
        )
