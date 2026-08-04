"""Per-tower utilization diagnostics for RE1CombatEfficientExtractor.

Cheap periodic path: activation RMS / effective rank / ReLU dormant fraction,
fusion-interface gradient RMS, policy/value/aux grad norms, update-to-weight
ratios where cheap, and counterfactual action KL (tower zero / goal swap).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import torch as th
import torch.nn.functional as F
from torch import nn

from re1_rl.combat_efficient_extractor import RE1CombatEfficientExtractor
from re1_rl.modality_ablations import (
    MOD_DROP_BRANCHES,
    MOD_DROP_DIM,
    TOWER_ORDER,
    tower_slices,
)
from re1_rl.modality_config import modality_diag_enabled, modality_diag_every_n_updates


def activation_rms(x: th.Tensor) -> float:
    return float(th.sqrt(th.mean(x.detach().float() ** 2)).cpu())


def relu_dormant_fraction(x: th.Tensor) -> float:
    """Fraction of units ≤ 0 (post-ReLU dormant proxy)."""
    flat = x.detach().float().reshape(-1)
    if flat.numel() == 0:
        return 0.0
    return float((flat <= 0).float().mean().cpu())


def effective_rank(x: th.Tensor, *, eps: float = 1e-5) -> float:
    """Shannon effective rank of batch×feature activations (Wu et al. style)."""
    mat = x.detach().float()
    if mat.dim() > 2:
        mat = mat.reshape(mat.shape[0], -1)
    if mat.shape[0] < 2 or mat.shape[1] < 2:
        return float(min(mat.shape))
    mat = mat - mat.mean(dim=0, keepdim=True)
    try:
        singular = th.linalg.svdvals(mat.cpu())
    except RuntimeError:
        return 0.0
    power = singular.clamp(min=0.0) ** 2
    total = float(power.sum().item())
    if total <= eps:
        return 0.0
    p = power / total
    p = p[p > eps]
    entropy = float(-(p * th.log(p)).sum().item())
    return float(np.exp(entropy))


def grad_rms(param_or_tensor: th.Tensor | None) -> float:
    if param_or_tensor is None or param_or_tensor.grad is None:
        return 0.0
    g = param_or_tensor.grad.detach().float()
    return float(th.sqrt(th.mean(g ** 2)).cpu())


def module_grad_rms(module: nn.Module) -> float:
    total_sq = 0.0
    n = 0
    for p in module.parameters():
        if p.grad is None:
            continue
        g = p.grad.detach().float().reshape(-1)
        total_sq += float(th.sum(g * g).cpu())
        n += int(g.numel())
    if n == 0:
        return 0.0
    return float(np.sqrt(total_sq / n))


def update_to_weight_ratio(param: nn.Parameter, prev: th.Tensor) -> float:
    """‖Δθ‖ / ‖θ‖ after an optimizer step (cheap scalar)."""
    with th.no_grad():
        delta = (param.detach() - prev).float()
        w = param.detach().float()
        wn = float(th.linalg.vector_norm(w).cpu())
        if wn < 1e-12:
            return 0.0
        return float(th.linalg.vector_norm(delta).cpu()) / wn


def tower_activation_stats(parts: Mapping[str, th.Tensor]) -> dict[str, float]:
    stats: dict[str, float] = {}
    for name, tensor in parts.items():
        stats[f"modality/{name}_rms"] = activation_rms(tensor)
        stats[f"modality/{name}_dormant"] = relu_dormant_fraction(tensor)
        stats[f"modality/{name}_eff_rank"] = effective_rank(tensor)
    return stats


def _policy_logits_and_value(
    policy: nn.Module,
    observations: dict[str, th.Tensor],
    action_masks: th.Tensor | None,
) -> tuple[th.Tensor, th.Tensor]:
    features = policy.extract_features(observations)
    if getattr(policy, "share_features_extractor", True):
        latent_pi, latent_vf = policy.mlp_extractor(features)
    else:
        pi_features, vf_features = features
        latent_pi = policy.mlp_extractor.forward_actor(pi_features)
        latent_vf = policy.mlp_extractor.forward_critic(vf_features)
    logits = policy.action_net(latent_pi)
    values = policy.value_net(latent_vf).flatten()
    if action_masks is not None:
        logits = logits.clone()
        logits[~action_masks.bool()] = -1e8
    return logits, values


def _kl_categorical(logits_p: th.Tensor, logits_q: th.Tensor) -> float:
    log_p = F.log_softmax(logits_p.float(), dim=-1)
    log_q = F.log_softmax(logits_q.float(), dim=-1)
    p = log_p.exp()
    kl = th.sum(p * (log_p - log_q), dim=-1)
    return float(kl.mean().cpu())


def _forward_from_parts(
    policy: nn.Module,
    extractor: RE1CombatEfficientExtractor,
    parts: dict[str, th.Tensor],
    action_masks: th.Tensor | None,
) -> tuple[th.Tensor, th.Tensor]:
    ordered = [parts[n] for n in TOWER_ORDER if n in parts]
    tower = th.cat(ordered, dim=-1)
    fused = extractor.fusion_proj(extractor.fusion_norm(tower))
    if getattr(policy, "share_features_extractor", True):
        latent_pi, latent_vf = policy.mlp_extractor(fused)
    else:
        latent_pi = policy.mlp_extractor.forward_actor(fused)
        latent_vf = policy.mlp_extractor.forward_critic(fused)
    logits = policy.action_net(latent_pi)
    values = policy.value_net(latent_vf).flatten()
    if action_masks is not None:
        logits = logits.clone()
        logits[~action_masks.bool()] = -1e8
    return logits, values


def counterfactual_tower_kl(
    policy: nn.Module,
    observations: dict[str, th.Tensor],
    *,
    tower_name: str,
    action_masks: th.Tensor | None = None,
) -> dict[str, float]:
    """Fix physical state; zero one tower (or swap goals); measure policy KL + value shift."""
    extractor = policy.features_extractor
    if not isinstance(extractor, RE1CombatEfficientExtractor):
        return {}

    with th.no_grad():
        base_logits, base_values = _policy_logits_and_value(
            policy, observations, action_masks
        )

        if tower_name == "goal_swap":
            obs_cf = dict(observations)
            goal = observations["goal"].clone()
            if goal.shape[0] > 1:
                obs_cf["goal"] = goal.roll(1, dims=0)
            else:
                obs_cf["goal"] = th.zeros_like(goal)
            cf_logits, cf_values = _policy_logits_and_value(
                policy, obs_cf, action_masks
            )
            tag = "goal_swap"
        elif tower_name == "goal":
            extractor.forward_features(observations, return_aux=True)
            parts = dict(extractor._last_tower_parts or {})
            if "goal" in parts:
                parts["goal"] = th.zeros_like(parts["goal"])
            cf_logits, cf_values = _forward_from_parts(
                policy, extractor, parts, action_masks
            )
            tag = "goal"
        elif tower_name in MOD_DROP_BRANCHES:
            saved = extractor._mod_drop_batch
            try:
                presence = th.ones(
                    observations["proprio"].shape[0],
                    MOD_DROP_DIM,
                    device=observations["proprio"].device,
                    dtype=th.float32,
                )
                presence[:, MOD_DROP_BRANCHES.index(tower_name)] = 0.0
                extractor.set_mod_drop_batch(presence)
                cf_logits, cf_values = _policy_logits_and_value(
                    policy, observations, action_masks
                )
            finally:
                extractor.set_mod_drop_batch(saved)
            tag = tower_name
        else:
            return {}

        return {
            f"modality/cf_kl_{tag}": _kl_categorical(base_logits, cf_logits),
            f"modality/cf_value_shift_{tag}": float(
                (cf_values - base_values).abs().mean().cpu()
            ),
        }


def compute_tower_diagnostics(
    extractor: RE1CombatEfficientExtractor,
    observations: dict[str, th.Tensor],
) -> dict[str, float]:
    """Activation diagnostics on a dummy/eval batch (no optimizer step)."""
    was_training = extractor.training
    extractor.eval()
    try:
        with th.no_grad():
            extractor.forward_features(observations, return_aux=True)
        parts = extractor._last_tower_parts or {}
        stats = tower_activation_stats(parts)
        if extractor._last_tower_concat is not None:
            stats["modality/tower_concat_rms"] = activation_rms(
                extractor._last_tower_concat
            )
        return stats
    finally:
        extractor.train(was_training)


def compute_fusion_grad_diagnostics(
    policy: nn.Module,
    observations: dict[str, th.Tensor],
    *,
    action_masks: th.Tensor | None = None,
) -> dict[str, float]:
    """One backward through policy+value(+aux) to measure fusion / head grads."""
    extractor = policy.features_extractor
    if not isinstance(extractor, RE1CombatEfficientExtractor):
        return {}

    policy.zero_grad(set_to_none=True)
    fused, aux = extractor.forward_features(observations, return_aux=True)  # type: ignore[misc]
    tower = aux["tower_concat"]
    tower.retain_grad()

    if getattr(policy, "share_features_extractor", True):
        latent_pi, latent_vf = policy.mlp_extractor(fused)
    else:
        latent_pi = policy.mlp_extractor.forward_actor(fused)
        latent_vf = policy.mlp_extractor.forward_critic(fused)
    logits = policy.action_net(latent_pi)
    values = policy.value_net(latent_vf).flatten()
    if action_masks is not None:
        logits = th.where(action_masks.bool(), logits, th.full_like(logits, -1e8))
    log_p = F.log_softmax(logits, dim=-1)
    policy_obj = -(log_p.exp() * log_p).sum(dim=-1).mean()
    value_obj = values.pow(2).mean()
    aux_obj = aux["outcome_pred"].pow(2).mean() + aux["world_event_pred"].pow(2).mean()
    (policy_obj + value_obj + 0.01 * aux_obj).backward()

    stats: dict[str, float] = {
        "modality/grad_rms_fusion_interface": grad_rms(tower),
        "modality/grad_rms_policy_head": module_grad_rms(policy.action_net),
        "modality/grad_rms_value_head": module_grad_rms(policy.value_net),
        "modality/grad_rms_aux_outcome": module_grad_rms(extractor.outcome_head),
        "modality/grad_rms_aux_world": module_grad_rms(extractor.world_event_head),
    }
    slices = tower_slices(persistent_enabled=extractor._persistent_enabled)
    if tower.grad is not None:
        for name, sl in slices.items():
            g = tower.grad[:, sl]
            stats[f"modality/grad_rms_tower_{name}"] = float(
                th.sqrt(th.mean(g.float() ** 2)).cpu()
            )
    policy.zero_grad(set_to_none=True)
    return stats


def compute_update_to_weight_snapshot(policy: nn.Module) -> dict[str, th.Tensor]:
    """Capture fusion / goal weights for Δθ/‖θ‖ after the next step."""
    extractor = getattr(policy, "features_extractor", None)
    snap: dict[str, th.Tensor] = {}
    if not isinstance(extractor, RE1CombatEfficientExtractor):
        return snap
    targets = (
        ("fusion_proj", extractor.fusion_proj[0].weight),
        ("goal_mlp", extractor.goal_mlp[0].weight),
    )
    for name, param in targets:
        if isinstance(param, nn.Parameter):
            snap[name] = param.detach().clone()
    return snap


def finalize_update_to_weight(
    policy: nn.Module, snapshot: Mapping[str, th.Tensor]
) -> dict[str, float]:
    extractor = getattr(policy, "features_extractor", None)
    if not isinstance(extractor, RE1CombatEfficientExtractor) or not snapshot:
        return {}
    current = {
        "fusion_proj": extractor.fusion_proj[0].weight,
        "goal_mlp": extractor.goal_mlp[0].weight,
    }
    stats: dict[str, float] = {}
    for key, prev in snapshot.items():
        param = current.get(key)
        if isinstance(param, nn.Parameter):
            stats[f"modality/u2w_{key}"] = update_to_weight_ratio(param, prev)
    return stats


def run_modality_diagnostics(
    model: Any,
    observations: dict[str, th.Tensor],
    *,
    action_masks: th.Tensor | None = None,
    counterfactual_towers: Sequence[str] | None = None,
) -> dict[str, float]:
    """Full diagnostic suite on a small batch. Caller logs periodically."""
    policy = model.policy
    extractor = policy.features_extractor
    if not isinstance(extractor, RE1CombatEfficientExtractor):
        return {}

    stats = compute_tower_diagnostics(extractor, observations)
    stats.update(
        compute_fusion_grad_diagnostics(
            policy, observations, action_masks=action_masks
        )
    )
    towers = (
        list(counterfactual_towers)
        if counterfactual_towers is not None
        else ["goal", "goal_swap", "vision", "world", "history"]
    )
    for name in towers:
        stats.update(
            counterfactual_tower_kl(
                policy, observations, tower_name=name, action_masks=action_masks
            )
        )
    return stats


def should_run_modality_diagnostics(model: Any) -> bool:
    if not modality_diag_enabled():
        return False
    every = modality_diag_every_n_updates()
    n = int(getattr(model, "_n_updates", 0))
    return n > 0 and (n % every == 0)


def maybe_log_modality_diagnostics(
    model: Any,
    observations: dict[str, th.Tensor] | None,
    *,
    action_masks: th.Tensor | None = None,
) -> dict[str, float]:
    """Run + logger.record when enabled and due. No-op when flag off / no batch."""
    if observations is None or not should_run_modality_diagnostics(model):
        return {}
    batch = {
        k: v[:8] if isinstance(v, th.Tensor) else v for k, v in observations.items()
    }
    masks = None if action_masks is None else action_masks[:8]
    stats = run_modality_diagnostics(model, batch, action_masks=masks)
    logger = getattr(model, "logger", None)
    if logger is not None:
        for key, value in stats.items():
            logger.record(key, float(value))
    return stats
