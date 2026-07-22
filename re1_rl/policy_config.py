"""Single source of truth for PPO policy sizing (Doc 04 Medium campaign).

Doc 04 Medium (WH2 8GB fit):
  - NatureCNN 512-d (transplant-compatible conv weights only)
  - Typed modality towers (no 975-d anonymous flatten)
  - Concat + LayerNorm fusion -> 1280-d
  - pi/vf trunks [512, 512]
  - affordances path-hint and goal compass omitted from forward

Fresh training required — old ~2M / 1615-d checkpoints are not shape-compatible.
See docs/campaign_doc04_medium.md and backup/pre-doc04-medium-2026-07-22.
"""
from __future__ import annotations

from re1_rl.doc04_medium_extractor import FEATURES_DIM, RE1Doc04MediumExtractor

POLICY_KWARGS: dict = dict(
    net_arch=dict(pi=[512, 512], vf=[512, 512]),
    features_extractor_class=RE1Doc04MediumExtractor,
    features_extractor_kwargs=dict(cnn_output_dim=512, features_dim=FEATURES_DIM),
)
