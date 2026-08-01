"""MaskablePPO with combat/world auxiliaries and optional grouped entropy.

Keeps the flat 45-action categorical distribution and MaskablePPO log-prob
semantics. Aux targets are attached as flat arrays aligned with the rollout
buffer order (column-major env-major flatten matching SB3).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch as th
from gymnasium import spaces
from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.policies import MaskableActorCriticPolicy
from stable_baselines3.common.type_aliases import GymEnv, Schedule
from stable_baselines3.common.utils import explained_variance
from torch.nn import functional as F

from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION, ATTACK_UP_ACTION
from re1_rl.combat_efficient_extractor import RE1CombatEfficientExtractor
from re1_rl.combat_targets import (
    COMBAT_OUTCOME_DIM,
    COMBAT_TARGET_DIM,
    WORLD_EVENT_DIM,
    combat_target_to_outcome_vector,
)

ATTACK_GROUP = frozenset({ATTACK_ACTION, ATTACK_UP_ACTION, ATTACK_DOWN_ACTION})
DEFAULT_AUX_COEF = 0.02
MAX_AUX_COEF = 0.05


def grouped_entropy_from_logits(
    logits: th.Tensor,
    action_masks: th.Tensor | None,
) -> th.Tensor:
    """Engage-excluded conditional entropy over flat masked logits.

    - No entropy bonus on attack-group vs noncombat-group mass.
    - Retain conditional entropy among legal noncombat actions.
    - Retain attack-height entropy only within the combat group mass.
    """
    if action_masks is not None:
        hugely_neg = th.finfo(logits.dtype).min
        logits = th.where(action_masks.bool(), logits, hugely_neg)

    log_probs = F.log_softmax(logits, dim=-1)
    probs = log_probs.exp()
    n_actions = logits.shape[-1]
    attack_idx = sorted(a for a in ATTACK_GROUP if a < n_actions)
    noncombat_idx = [i for i in range(n_actions) if i not in ATTACK_GROUP]

    attack_mass = probs[:, attack_idx].sum(dim=-1).clamp(min=1e-8)
    noncombat_mass = probs[:, noncombat_idx].sum(dim=-1).clamp(min=1e-8)

    # Conditional entropy within noncombat
    nc_probs = probs[:, noncombat_idx] / noncombat_mass.unsqueeze(-1)
    nc_log = th.log(nc_probs.clamp(min=1e-8))
    h_nc = -(nc_probs * nc_log).sum(dim=-1)

    # Conditional entropy within attack heights
    at_probs = probs[:, attack_idx] / attack_mass.unsqueeze(-1)
    at_log = th.log(at_probs.clamp(min=1e-8))
    h_at = -(at_probs * at_log).sum(dim=-1)

    # Weight by group mass (but do not add H(group choice)).
    return noncombat_mass * h_nc + attack_mass * h_at


def combat_auxiliary_loss(
    outcome_pred: th.Tensor,
    combat_targets: th.Tensor,
    *,
    world_pred: th.Tensor | None = None,
    world_targets: th.Tensor | None = None,
    world_masks: th.Tensor | None = None,
) -> tuple[th.Tensor, dict[str, float]]:
    """Balanced BCE + Huber on executed height only; optional world events."""
    device = outcome_pred.device
    batch = outcome_pred.shape[0]
    y = th.zeros(batch, COMBAT_OUTCOME_DIM, device=device)
    m = th.zeros(batch, COMBAT_OUTCOME_DIM, device=device)
    ct = combat_targets.detach().cpu().numpy()
    for i in range(batch):
        yi, mi = combat_target_to_outcome_vector(ct[i])
        y[i] = th.as_tensor(yi, device=device)
        m[i] = th.as_tensor(mi, device=device)

    # Binary channels: hit, wasted, kill, macro_failure (indices 0,1,3,5 per height)
    # Continuous: damage, ammo_spent (2,4)
    bin_idx = []
    cont_idx = []
    for h in range(3):
        base = h * 6
        bin_idx.extend([base + 0, base + 1, base + 3, base + 5])
        cont_idx.extend([base + 2, base + 4])

    loss = th.zeros((), device=device)
    stats: dict[str, float] = {}
    mask_sum = m.sum().clamp(min=1.0)

    if m[:, bin_idx].sum() > 0:
        logits = outcome_pred[:, bin_idx]
        target = y[:, bin_idx]
        bm = m[:, bin_idx]
        # Balanced hit/miss: weight positives and negatives in supervised cells.
        pos = (target * bm).sum().clamp(min=1.0)
        neg = ((1.0 - target) * bm).sum().clamp(min=1.0)
        weights = bm * (target * (neg / pos) + (1.0 - target))
        bce = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
        bin_loss = (bce * weights).sum() / weights.sum().clamp(min=1.0)
        loss = loss + bin_loss
        stats["train/aux_combat_bce"] = float(bin_loss.detach().cpu())

    if m[:, cont_idx].sum() > 0:
        # Continuous heads share the linear outcome layer; sigmoid maps to [0, 1]
        # before Huber against normalized damage / ammo targets.
        pred = th.sigmoid(outcome_pred[:, cont_idx])
        target = y[:, cont_idx]
        cm = m[:, cont_idx]
        huber = F.smooth_l1_loss(pred, target, reduction="none")
        cont_loss = (huber * cm).sum() / cm.sum().clamp(min=1.0)
        loss = loss + cont_loss
        stats["train/aux_combat_huber"] = float(cont_loss.detach().cpu())

    if world_pred is not None and world_targets is not None:
        wm = world_masks if world_masks is not None else th.ones_like(world_targets)
        wbce = F.binary_cross_entropy_with_logits(world_pred, world_targets, reduction="none")
        w_loss = (wbce * wm).sum() / wm.sum().clamp(min=1.0)
        loss = loss + 0.5 * w_loss
        stats["train/aux_world_bce"] = float(w_loss.detach().cpu())

    stats["train/aux_combat_mask_frac"] = float((m.sum() / (batch * COMBAT_OUTCOME_DIM)).detach().cpu())
    stats["train/aux_loss"] = float(loss.detach().cpu())
    return loss, stats


class CombatEfficientPPO(MaskablePPO):
    """MaskablePPO + combat/world aux + optional grouped entropy ablation."""

    def __init__(
        self,
        policy: str | type[MaskableActorCriticPolicy],
        env: GymEnv | str,
        learning_rate: float | Schedule = 3e-4,
        n_steps: int = 2048,
        batch_size: int | None = 64,
        n_epochs: int = 10,
        gamma: float = 0.99,
        gae_lambda: float = 1.0,
        clip_range: float | Schedule = 0.2,
        clip_range_vf: None | float | Schedule = None,
        normalize_advantage: bool = True,
        ent_coef: float = 0.0,
        vf_coef: float = 0.5,
        max_grad_norm: float = 0.5,
        target_kl: float | None = None,
        stats_window_size: int = 100,
        tensorboard_log: str | None = None,
        policy_kwargs: dict[str, Any] | None = None,
        verbose: int = 0,
        seed: int | None = None,
        device: th.device | str = "auto",
        _init_setup_model: bool = True,
        aux_coef: float = DEFAULT_AUX_COEF,
        use_grouped_entropy: bool = False,
        **kwargs: Any,
    ) -> None:
        self.aux_coef = float(min(max(aux_coef, 0.0), MAX_AUX_COEF))
        self.use_grouped_entropy = bool(use_grouped_entropy)
        self._combat_targets_flat: np.ndarray | None = None
        self._world_targets_flat: np.ndarray | None = None
        self._world_masks_flat: np.ndarray | None = None
        super().__init__(
            policy=policy,
            env=env,
            learning_rate=learning_rate,
            n_steps=n_steps,
            batch_size=batch_size,
            n_epochs=n_epochs,
            gamma=gamma,
            gae_lambda=gae_lambda,
            clip_range=clip_range,
            clip_range_vf=clip_range_vf,
            normalize_advantage=normalize_advantage,
            ent_coef=ent_coef,
            vf_coef=vf_coef,
            max_grad_norm=max_grad_norm,
            target_kl=target_kl,
            stats_window_size=stats_window_size,
            tensorboard_log=tensorboard_log,
            policy_kwargs=policy_kwargs,
            verbose=verbose,
            seed=seed,
            device=device,
            _init_setup_model=_init_setup_model,
            **kwargs,
        )

    def set_auxiliary_targets(
        self,
        combat_targets: np.ndarray | None,
        world_targets: np.ndarray | None = None,
        world_masks: np.ndarray | None = None,
    ) -> None:
        """Attach flat (n_steps * n_envs, dim) targets matching buffer order."""
        if combat_targets is None:
            self._combat_targets_flat = None
        else:
            arr = np.asarray(combat_targets, dtype=np.float32)
            if arr.ndim == 3:
                # (n_steps, n_envs, dim) → SB3 swap_and_flatten order
                arr = arr.swapaxes(0, 1).reshape(-1, arr.shape[-1])
            self._combat_targets_flat = arr
        if world_targets is None:
            self._world_targets_flat = None
        else:
            arr = np.asarray(world_targets, dtype=np.float32)
            if arr.ndim == 3:
                arr = arr.swapaxes(0, 1).reshape(-1, arr.shape[-1])
            self._world_targets_flat = arr
        if world_masks is None:
            self._world_masks_flat = None
        else:
            arr = np.asarray(world_masks, dtype=np.float32)
            if arr.ndim == 3:
                arr = arr.swapaxes(0, 1).reshape(-1, arr.shape[-1])
            self._world_masks_flat = arr

    def _aux_batch(
        self, indices: np.ndarray
    ) -> tuple[th.Tensor | None, th.Tensor | None, th.Tensor | None]:
        if self._combat_targets_flat is None:
            return None, None, None
        ct = th.as_tensor(self._combat_targets_flat[indices], device=self.device)
        wt = (
            th.as_tensor(self._world_targets_flat[indices], device=self.device)
            if self._world_targets_flat is not None
            else None
        )
        wm = (
            th.as_tensor(self._world_masks_flat[indices], device=self.device)
            if self._world_masks_flat is not None
            else None
        )
        return ct, wt, wm

    def train(self) -> None:
        self.policy.set_training_mode(True)
        self._update_learning_rate(self.policy.optimizer)
        clip_range = self.clip_range(self._current_progress_remaining)  # type: ignore[operator]
        clip_range_vf = None
        if self.clip_range_vf is not None:
            clip_range_vf = self.clip_range_vf(self._current_progress_remaining)  # type: ignore[operator]

        entropy_losses: list[float] = []
        pg_losses: list[float] = []
        value_losses: list[float] = []
        clip_fractions: list[float] = []
        aux_losses: list[float] = []
        aux_stats_acc: dict[str, list[float]] = {}
        continue_training = True

        buffer = self.rollout_buffer
        assert buffer.full, "rollout buffer must be full before CombatEfficientPPO.train()"
        n_samples = int(buffer.buffer_size * buffer.n_envs)
        last_loss = 0.0

        # Match MaskableDictRolloutBuffer.get() flatten-once semantics.
        if not buffer.generator_ready:
            for key, obs in buffer.observations.items():
                buffer.observations[key] = buffer.swap_and_flatten(obs)
            for tensor in ("actions", "values", "log_probs", "advantages", "returns", "action_masks"):
                buffer.__dict__[tensor] = buffer.swap_and_flatten(buffer.__dict__[tensor])
            buffer.generator_ready = True

        for epoch in range(self.n_epochs):
            approx_kl_divs = []
            indices = np.random.permutation(n_samples)
            start = 0
            batch_size = self.batch_size or n_samples
            while start < n_samples:
                batch_inds = indices[start : start + batch_size]
                start += batch_size
                rollout_data = buffer._get_samples(batch_inds)  # noqa: SLF001

                actions = rollout_data.actions
                if isinstance(self.action_space, spaces.Discrete):
                    actions = rollout_data.actions.long().flatten()

                values, log_prob, entropy = self.policy.evaluate_actions(
                    rollout_data.observations,
                    actions,
                    action_masks=rollout_data.action_masks,
                )
                values = values.flatten()
                advantages = rollout_data.advantages
                if self.normalize_advantage:
                    advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

                ratio = th.exp(log_prob - rollout_data.old_log_prob)
                policy_loss_1 = advantages * ratio
                policy_loss_2 = advantages * th.clamp(ratio, 1 - clip_range, 1 + clip_range)
                policy_loss = -th.min(policy_loss_1, policy_loss_2).mean()
                pg_losses.append(policy_loss.item())
                clip_fractions.append(th.mean((th.abs(ratio - 1) > clip_range).float()).item())

                if clip_range_vf is None:
                    values_pred = values
                else:
                    values_pred = rollout_data.old_values + th.clamp(
                        values - rollout_data.old_values, -clip_range_vf, clip_range_vf
                    )
                value_loss = F.mse_loss(rollout_data.returns, values_pred)
                value_losses.append(value_loss.item())

                if self.use_grouped_entropy:
                    features = self.policy.extract_features(rollout_data.observations)
                    if self.policy.share_features_extractor:
                        latent_pi, _latent_vf = self.policy.mlp_extractor(features)
                    else:
                        pi_features, _vf_features = features
                        latent_pi = self.policy.mlp_extractor.forward_actor(pi_features)
                    logits = self.policy.action_net(latent_pi)
                    ent = grouped_entropy_from_logits(logits, rollout_data.action_masks)
                    entropy_loss = -th.mean(ent)
                elif entropy is None:
                    entropy_loss = -th.mean(-log_prob)
                else:
                    entropy_loss = -th.mean(entropy)
                entropy_losses.append(entropy_loss.item())

                loss = policy_loss + self.ent_coef * entropy_loss + self.vf_coef * value_loss

                extractor = self.policy.features_extractor
                if isinstance(extractor, RE1CombatEfficientExtractor) and self.aux_coef > 0:
                    aux = extractor.predict_aux(rollout_data.observations)
                    ct, wt, wm = self._aux_batch(batch_inds)
                    if ct is not None:
                        aux_loss, aux_stats = combat_auxiliary_loss(
                            aux["outcome_pred"],
                            ct,
                            world_pred=aux.get("world_event_pred"),
                            world_targets=wt,
                            world_masks=wm,
                        )
                        loss = loss + self.aux_coef * aux_loss
                        aux_losses.append(float(aux_loss.detach().cpu()))
                        for k, v in aux_stats.items():
                            aux_stats_acc.setdefault(k, []).append(v)

                with th.no_grad():
                    log_ratio = log_prob - rollout_data.old_log_prob
                    approx_kl_div = th.mean((th.exp(log_ratio) - 1) - log_ratio).cpu().numpy()
                    approx_kl_divs.append(approx_kl_div)

                if self.target_kl is not None and approx_kl_div > 1.5 * self.target_kl:
                    continue_training = False
                    if self.verbose >= 1:
                        print(f"Early stopping at step {epoch} due to reaching max kl: {approx_kl_div:.2f}")
                    break

                self.policy.optimizer.zero_grad()
                loss.backward()
                th.nn.utils.clip_grad_norm_(self.policy.parameters(), self.max_grad_norm)
                self.policy.optimizer.step()
                last_loss = float(loss.detach().cpu())

            self._n_updates += 1
            if not continue_training:
                break

        explained_var = explained_variance(
            self.rollout_buffer.values.flatten(), self.rollout_buffer.returns.flatten()
        )
        self.logger.record("train/entropy_loss", np.mean(entropy_losses) if entropy_losses else 0.0)
        self.logger.record("train/policy_gradient_loss", np.mean(pg_losses) if pg_losses else 0.0)
        self.logger.record("train/value_loss", np.mean(value_losses) if value_losses else 0.0)
        self.logger.record("train/approx_kl", np.mean(approx_kl_divs) if approx_kl_divs else 0.0)
        self.logger.record("train/clip_fraction", np.mean(clip_fractions) if clip_fractions else 0.0)
        self.logger.record("train/loss", last_loss)
        self.logger.record("train/explained_variance", explained_var)
        self.logger.record("train/n_updates", self._n_updates)
        self.logger.record("train/clip_range", clip_range)
        if self.use_grouped_entropy:
            self.logger.record("train/grouped_entropy", 1.0)
        if aux_losses:
            self.logger.record("train/aux_loss", float(np.mean(aux_losses)))
            self.logger.record("train/aux_coef", self.aux_coef)
            for k, vals in aux_stats_acc.items():
                self.logger.record(k, float(np.mean(vals)))
        if clip_range_vf is not None:
            self.logger.record("train/clip_range_vf", clip_range_vf)
