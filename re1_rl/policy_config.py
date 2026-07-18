"""Single source of truth for PPO policy sizing.

SB3 MultiInputPolicy defaults (cnn_output_dim=256, net_arch 2x64) bottleneck
the fusion of vision (256-d) with the 44-d proprio+goal compass. Emulation is
the throughput constraint, not GPU inference, so we widen for free:

- NatureCNN output 256 -> 512 (Nature DQN width)
- pi/vf MLP trunks 2x64 -> 2x256

Frame is 84x77x4 (HWC): full BizHawk frame is resized to 84x84 (pillarbox
bars included), then bar columns are pruned (4+3 px). SB3 VecTransposeImage
feeds NatureCNN (4, 84, 77); flatten after convs is 2688 (was 3136 at 84x84).
Resume auto-transplants compatible tensors via async_fleet.

Obs keys (RE1WorldAwareExtractor — see docs/world_aware_nn_architecture.md):
  frame   84x77x4 uint8   -> NatureCNN -> 512
  proprio..maps_files     -> flatten (legacy privileged; world_state excluded)
  world_state (475,) f32  -> world MLP join with frozen WorldCatalog buffers -> 64
  affordances (40,) f32   -> still flattened (deprecated; key hints live in world_state)

features_dim = 1523 = 512 CNN + ~947 flatten + 64 world_context.

Static almanac (map_neighbors, pickup catalog, key_*, files, combine) is
register_buffer only — rebuilt from JSON on learner load; NOT in rollouts.

NOTE: PPO.load() restores checkpoint architecture. Use
scripts/transplant_world_almanac.py for pre-almanac zips; async_fleet
calls reload_world_catalog_buffers after resume.
"""
from __future__ import annotations

from pathlib import Path

from re1_rl.features_extractor import RE1WorldAwareExtractor

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

POLICY_KWARGS: dict = dict(
    net_arch=dict(pi=[256, 256], vf=[256, 256]),
    features_extractor_class=RE1WorldAwareExtractor,
    features_extractor_kwargs=dict(cnn_output_dim=512, project_root=_PROJECT_ROOT),
)
