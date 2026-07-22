"""Doc 04 Medium extractor: typed modality towers + concat/LN fusion (WH2-fit)."""

from __future__ import annotations

from pathlib import Path

import torch as th
from gymnasium import spaces
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor, NatureCNN
from stable_baselines3.common.type_aliases import TensorDict
from torch import nn

from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM
from re1_rl.episode_history import ACQUISITION_LOG_DIM, ROOM_HISTORY_DIM
from re1_rl.key_items import KEYS_HELD_DIM
from re1_rl.maps_files import MAPS_FILES_DIM
from re1_rl.milestone_features import MILESTONE_DIM
from re1_rl.obs_encoder import BOX_DIM, INVENTORY_OBS_DIM, PROPRIO_DIM, ROOM_VISITED_DIM
from re1_rl.obs_encoder import PROPRIO_FIELDS
from re1_rl.room_signature import ENEMY_ROSTER_DIM
from re1_rl.spatial_encoder import SPATIAL_DIM, VISITED_SHAPE
from re1_rl.weapon_damage import LAST_ATTACK_DIM, WEAPON_CARD_DIM
from re1_rl.world_catalog import NUM_ROOMS
from re1_rl.world_context_module import WorldContextModule, reload_world_catalog_buffers

_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROPRIO_ROOM_INDEX = next(i for i, (name, _) in enumerate(PROPRIO_FIELDS) if name == "room_index")

VISION_DIM = 512
CONTROL_DIM = 64
SPATIAL_TOWER_DIM = 128
INVENTORY_TOWER_DIM = 128
HISTORY_TOWER_DIM = 128
FLAGS_TOWER_DIM = 64
COMBAT_TOWER_DIM = 64
WORLD_CONTEXT_DIM = 256
ROOM_EMBED_DIM = 64

TOWER_OUT_DIM = (
    VISION_DIM
    + CONTROL_DIM
    + SPATIAL_TOWER_DIM
    + INVENTORY_TOWER_DIM
    + HISTORY_TOWER_DIM
    + FLAGS_TOWER_DIM
    + COMBAT_TOWER_DIM
    + WORLD_CONTEXT_DIM
)  # 1344

FEATURES_DIM = 1280

# Keys intentionally dropped from fusion (north-star / digestibility).
_OMIT_OBS_KEYS = frozenset({"frame", "world_state", "key_hints", "goal", "affordances"})


class RE1Doc04MediumExtractor(BaseFeaturesExtractor):
    """NatureCNN + typed towers; no anonymous flatten or gated fusion."""

    def __init__(
        self,
        observation_space: spaces.Dict,
        cnn_output_dim: int = VISION_DIM,
        project_root: str | Path | None = None,
        normalized_image: bool = False,
        features_dim: int = FEATURES_DIM,
    ) -> None:
        if not isinstance(observation_space, spaces.Dict):
            raise TypeError(f"RE1Doc04MediumExtractor expects Dict obs, got {type(observation_space)}")
        if "frame" not in observation_space.spaces:
            raise ValueError("observation_space must include 'frame'")

        super().__init__(observation_space, features_dim=features_dim)

        root = Path(project_root) if project_root else _DEFAULT_PROJECT_ROOT
        self._cnn_output_dim = cnn_output_dim

        self.cnn_extractor = NatureCNN(
            observation_space.spaces["frame"],
            features_dim=cnn_output_dim,
            normalized_image=normalized_image,
        )

        self.room_embedding = nn.Embedding(NUM_ROOMS, ROOM_EMBED_DIM)
        self.control_mlp = nn.Sequential(
            nn.Linear(PROPRIO_DIM - 1 + ROOM_EMBED_DIM, 128),
            nn.ReLU(),
            nn.Linear(128, CONTROL_DIM),
            nn.ReLU(),
        )

        self.spatial_mlp = nn.Sequential(
            nn.Linear(SPATIAL_DIM, 192),
            nn.ReLU(),
            nn.Linear(192, SPATIAL_TOWER_DIM),
            nn.ReLU(),
        )

        inv_in = INVENTORY_OBS_DIM + BOX_DIM + KEYS_HELD_DIM
        self.inventory_mlp = nn.Sequential(
            nn.Linear(inv_in, 192),
            nn.ReLU(),
            nn.Linear(192, INVENTORY_TOWER_DIM),
            nn.ReLU(),
        )

        hist_in = (
            ROOM_HISTORY_DIM
            + ACQUISITION_LOG_DIM
            + ROOM_VISITED_DIM
            + MILESTONE_DIM
            + CUTSCENE_LEDGER_DIM
        )
        self.history_mlp = nn.Sequential(
            nn.Linear(hist_in, 256),
            nn.ReLU(),
            nn.Linear(256, HISTORY_TOWER_DIM),
            nn.ReLU(),
        )

        visited_flat = int(VISITED_SHAPE[0] * VISITED_SHAPE[1] * VISITED_SHAPE[2])
        flags_in = MAPS_FILES_DIM + visited_flat
        self.flags_mlp = nn.Sequential(
            nn.Linear(flags_in, 128),
            nn.ReLU(),
            nn.Linear(128, FLAGS_TOWER_DIM),
            nn.ReLU(),
        )

        combat_in = WEAPON_CARD_DIM + LAST_ATTACK_DIM + ENEMY_ROSTER_DIM
        self.combat_mlp = nn.Sequential(
            nn.Linear(combat_in, 128),
            nn.ReLU(),
            nn.Linear(128, COMBAT_TOWER_DIM),
            nn.ReLU(),
        )

        self.world_context = WorldContextModule(
            output_dim=WORLD_CONTEXT_DIM,
            hidden_dim=384,
            project_root=root,
        )

        self.fusion_norm = nn.LayerNorm(TOWER_OUT_DIM)
        self.fusion_proj = nn.Sequential(
            nn.Linear(TOWER_OUT_DIM, features_dim),
            nn.ReLU(),
        )

    def _room_index(self, proprio: th.Tensor) -> th.Tensor:
        raw = proprio[:, PROPRIO_ROOM_INDEX] * 128.0
        return raw.long().clamp(0, NUM_ROOMS - 1)

    def _control_features(self, observations: TensorDict) -> th.Tensor:
        proprio = observations["proprio"]
        room = self._room_index(proprio)
        room_emb = self.room_embedding(room)
        scalars = th.cat([proprio[:, :PROPRIO_ROOM_INDEX], proprio[:, PROPRIO_ROOM_INDEX + 1 :]], dim=-1)
        return self.control_mlp(th.cat([scalars, room_emb], dim=-1))

    def _optional_tensor(
        self,
        observations: TensorDict,
        key: str,
        dim: int,
    ) -> th.Tensor:
        tensor = observations.get(key)
        if tensor is None:
            batch = observations["proprio"].shape[0]
            device = observations["proprio"].device
            return th.zeros(batch, dim, device=device, dtype=th.float32)
        if tensor.dim() > 2:
            return tensor.flatten(start_dim=1)
        return tensor

    def forward(self, observations: TensorDict) -> th.Tensor:
        frame = observations["frame"]
        if frame.dtype != th.float32:
            frame = frame.float()
        vision = self.cnn_extractor(frame)

        spatial = self.spatial_mlp(self._optional_tensor(observations, "spatial", SPATIAL_DIM))
        inventory = self.inventory_mlp(
            th.cat(
                [
                    self._optional_tensor(observations, "inventory", INVENTORY_OBS_DIM),
                    self._optional_tensor(observations, "box", BOX_DIM),
                    self._optional_tensor(observations, "keys_held", KEYS_HELD_DIM),
                ],
                dim=-1,
            )
        )
        history = self.history_mlp(
            th.cat(
                [
                    self._optional_tensor(observations, "history", ROOM_HISTORY_DIM),
                    self._optional_tensor(observations, "acquisitions", ACQUISITION_LOG_DIM),
                    self._optional_tensor(observations, "rooms_visited", ROOM_VISITED_DIM),
                    self._optional_tensor(observations, "milestones", MILESTONE_DIM),
                    self._optional_tensor(observations, "cutscene_ledger", CUTSCENE_LEDGER_DIM),
                ],
                dim=-1,
            )
        )
        visited = self._optional_tensor(
            observations,
            "visited",
            int(VISITED_SHAPE[0] * VISITED_SHAPE[1] * VISITED_SHAPE[2]),
        )
        flags = self.flags_mlp(
            th.cat(
                [
                    self._optional_tensor(observations, "maps_files", MAPS_FILES_DIM),
                    visited,
                ],
                dim=-1,
            )
        )
        combat = self.combat_mlp(
            th.cat(
                [
                    self._optional_tensor(observations, "weapon_card", WEAPON_CARD_DIM),
                    self._optional_tensor(observations, "last_attack", LAST_ATTACK_DIM),
                    self._optional_tensor(observations, "room_enemies", ENEMY_ROSTER_DIM),
                ],
                dim=-1,
            )
        )

        fused = th.cat(
            [
                vision,
                self._control_features(observations),
                spatial,
                inventory,
                history,
                flags,
                combat,
                self.world_context(observations),
            ],
            dim=-1,
        )
        return self.fusion_proj(self.fusion_norm(fused))


def reload_doc04_world_catalog_buffers(policy: nn.Module, project_root: str | Path | None = None) -> None:
    """Reload almanac buffers on Doc04 medium policies."""
    module: nn.Module = policy
    if hasattr(module, "policy"):
        module = module.policy
    extractor = getattr(module, "features_extractor", module)
    if not isinstance(extractor, RE1Doc04MediumExtractor):
        raise TypeError(
            f"reload_doc04_world_catalog_buffers expected RE1Doc04MediumExtractor, got {type(extractor)}"
        )
    reload_world_catalog_buffers(extractor.world_context, project_root)
