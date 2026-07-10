"""Single source of truth for PPO policy sizing.

SB3 MultiInputPolicy defaults (cnn_output_dim=256, net_arch 2x64) bottleneck
the fusion of vision (256-d) with the 44-d proprio+goal compass. Emulation is
the throughput constraint, not GPU inference, so we widen for free:

- NatureCNN output 256 -> 512 (Nature DQN width)
- pi/vf MLP trunks 2x64 -> 2x256

2.10M params total (vs ~0.93M default). See docs/nn_architecture_and_encoding.md #5.

Obs keys and their extractor paths (SB3 CombinedExtractor):
  frame   84x84x4 uint8   -> NatureCNN -> 512
  proprio (28,) float32   -> flatten
  goal    (27,) float32   -> flatten
  spatial (128,) float32  -> flatten (egocentric items/enemies/exits/interactables)
  visited (16,16,1) f32   -> flatten 256 (kept float32 0..1 ON PURPOSE:
                             uint8 would trip is_image_space and NatureCNN
                             cannot take 16x16 input)
  rooms_visited (128,) f32 -> flatten (episode one-hot over room table)
  box     (34,) float32   -> flatten (item-box slots + free_slots + in_box_room)
  inventory (16,) f32     -> flatten (on-person 8 slots)
  history (65,) f32       -> flatten (room deque K=32)
  acquisitions (9,) f32   -> flatten (last 4 pickups)
  room_enemies (12,) f32  -> flatten (static roster counts)
  keys_held (37,) f32     -> flatten (ever-held key-item bitmask)
  affordances (40,) f32   -> flatten (top-8 held key item affordances)
  cutscene_ledger (16,) f32 -> flatten (milestone cutscene bits)
  milestones (12,) f32   -> flatten (derived episode milestones)
  maps_files (16,) f32   -> flatten (map/file pickup u16 bitfield)
Fusion input = 512 + 28 + 27 + 128 + 256 + 128 + 34 + 16 + 65 + 9 + 12 + 37 + 40 + 16 + 12 + 16 = 1336 -> 2x256 pi/vf trunks.

NOTE: PPO.load() restores the architecture stored in the checkpoint zip, so
resuming an old 2x64 checkpoint keeps the old sizing. Widening requires a
fresh run (or a manual weight transplant).
"""
from __future__ import annotations

POLICY_KWARGS: dict = dict(
    net_arch=dict(pi=[256, 256], vf=[256, 256]),
    features_extractor_kwargs=dict(cnn_output_dim=512),
)
