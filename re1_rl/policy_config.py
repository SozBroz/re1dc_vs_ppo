"""Single source of truth for PPO policy sizing (combat-efficient campaign).

Combat-efficient (WH2 8GB fit, hard cap 5.8M):
  - NatureCNN 512-d (transplant-compatible conv weights)
  - Typed modality towers + joint 128-d combat latent
  - Concat + LayerNorm fusion -> 1024-d
  - pi/vf trunks [512, 512]
  - flat 45-action MaskablePPO distribution
  - affordances path-hint and goal compass omitted from forward

Fresh training / one-time graft required — Doc04-medium 1280-d checkpoints are
not shape-compatible. See scripts/transplant_combat_efficient.py.
"""
from __future__ import annotations

from re1_rl.combat_efficient_extractor import FEATURES_DIM, RE1CombatEfficientExtractor

POLICY_KWARGS: dict = dict(
    net_arch=dict(pi=[512, 512], vf=[512, 512]),
    features_extractor_class=RE1CombatEfficientExtractor,
    features_extractor_kwargs=dict(cnn_output_dim=512, features_dim=FEATURES_DIM),
)

# Learner algorithm class (workers only need the policy / state_dict).
PPO_ALGORITHM = "CombatEfficientPPO"
USE_GROUPED_ENTROPY = False  # ablation flag; baseline uses stock MaskablePPO entropy
AUX_COEF = 0.02
