"""Single source of truth for PPO policy sizing (planner-loyal campaign).

Planner-loyal baseline (near combat-efficient size; fresh ckpt required):
  - NatureCNN 512-d
  - Goal tower 256-d (+ planner_steps residual when RE1_PLANNER_LOYAL=1)
  - No history / world towers under planner-loyal (those obs keys omitted)
  - Concat + LayerNorm fusion -> 1024-d
  - pi/vf trunks [512, 512]
  - flat 45-action MaskablePPO distribution

Doc-04 vacuum (2048-d + 3x1024 trunks / ~24M) stays deferred — that package
needs IMPALA-3 + typed encoders, not a width-only cherry-pick.

Optional modality flags (env, all default OFF — fleet path unchanged):
  RE1_MODALITY_DIAG=1       per-tower utilization diagnostics (periodic)
  RE1_GOAL_FILM=1           identity-init FiLM on vision/spatial from goal
  RE1_MOD_DROP=1            structured modality dropout (stored masks)
  RE1_MOD_DROP_RATE=0.05    branch-outage probability
  RE1_DISC_LR=1             discriminative LR (mature towers × RE1_DISC_LR_MULT)
  RE1_USE_GROUPED_ENTROPY=1 grouped-entropy training ablation (learner only)
"""
from __future__ import annotations

from re1_rl.combat_efficient_extractor import FEATURES_DIM, RE1CombatEfficientExtractor
from re1_rl.inference_config import grouped_entropy_training_from_env

POLICY_KWARGS: dict = dict(
    net_arch=dict(pi=[512, 512], vf=[512, 512]),
    features_extractor_class=RE1CombatEfficientExtractor,
    # goal_film / mod_drop read from env inside extractor when kwargs omitted
    features_extractor_kwargs=dict(cnn_output_dim=512, features_dim=FEATURES_DIM),
)

# Learner algorithm class (workers only need the policy / state_dict).
PPO_ALGORITHM = "CombatEfficientPPO"
USE_GROUPED_ENTROPY = grouped_entropy_training_from_env()
AUX_COEF = 0.02
