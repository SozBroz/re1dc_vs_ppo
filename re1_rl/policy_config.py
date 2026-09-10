"""Single source of truth for PPO policy sizing (planner-loyal campaign).

Mandate expansion (2026-09-09):
  - NatureCNN 512-d (transplanted)
  - Goal tower 256-d (+ planner_steps residual when RE1_PLANNER_LOYAL=1)
  - Goal FiLM on vision/spatial (identity init; always on for this POLICY_KWARGS)
  - No history / world towers under planner-loyal (those obs keys omitted)
  - Concat + LayerNorm fusion -> 2048-d
  - pi/vf trunks [1024, 1024, 1024]
  - flat 45-action MaskablePPO distribution
  - ~13.8M unique params (PARAM_TARGET 14M / HARD_CAP 16M)

Doc-04 vacuum IMPALA-3 package stays deferred.

Optional modality flags (env):
  RE1_MODALITY_DIAG=1       per-tower utilization diagnostics (periodic)
  RE1_GOAL_FILM=0           only to force FiLM off (default on via kwargs)
  RE1_MOD_DROP=1            structured modality dropout (stored masks)
  RE1_MOD_DROP_RATE=0.05    branch-outage probability
  RE1_DISC_LR=1             discriminative LR (default ON for mandate)
  RE1_USE_GROUPED_ENTROPY=1 grouped-entropy training ablation (learner only)
"""
from __future__ import annotations

from re1_rl.combat_efficient_extractor import FEATURES_DIM, RE1CombatEfficientExtractor
from re1_rl.inference_config import grouped_entropy_training_from_env

POLICY_KWARGS: dict = dict(
    net_arch=dict(pi=[1024, 1024, 1024], vf=[1024, 1024, 1024]),
    features_extractor_class=RE1CombatEfficientExtractor,
    features_extractor_kwargs=dict(
        cnn_output_dim=512,
        features_dim=FEATURES_DIM,
        goal_film=True,
    ),
)

# Learner algorithm class (workers only need the policy / state_dict).
PPO_ALGORITHM = "CombatEfficientPPO"
USE_GROUPED_ENTROPY = grouped_entropy_training_from_env()
AUX_COEF = 0.02
