"""Combat-efficient policy extractor: typed towers + joint combat latent.

Preserves NatureCNN and flat fusion into 1024-d features for [512,512] pi/vf.
Named persistent-state tower is conditional on verified RAM fields only.
"""

from __future__ import annotations

from pathlib import Path

import torch as th
from gymnasium import spaces
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor, NatureCNN
from stable_baselines3.common.type_aliases import TensorDict
from torch import nn

from re1_rl.combat_targets import COMBAT_OUTCOME_DIM, WORLD_EVENT_DIM
from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM
from re1_rl.episode_history import ACQUISITION_LOG_DIM, ACQUISITION_LOG_K, ROOM_DEQUE_K, ROOM_HISTORY_DIM
from re1_rl.key_items import KEYS_HELD_DIM
from re1_rl.maps_files import MAPS_FILES_DIM
from re1_rl.milestone_features import MILESTONE_DIM
from re1_rl.named_state import NAMED_STATE_DIM
from re1_rl.obs_encoder import (
    BOX_DIM,
    INVENTORY_OBS_DIM,
    INVENTORY_SLOTS,
    MAX_ITEM_ID,
    PROPRIO_DIM,
    PROPRIO_FIELDS,
    ROOM_VISITED_DIM,
)
from re1_rl.room_signature import ENEMY_ROSTER_DIM
from re1_rl.spatial_encoder import (
    ENEMY_SLOT_DIM,
    ENEMY_SLOTS,
    EXIT_SLOT_DIM,
    EXIT_SLOTS,
    INTERACTABLE_SLOTS,
    ITEM_SLOTS,
    SPATIAL_DIM,
    VISITED_SHAPE,
)
from re1_rl.weapon_damage import LAST_ATTACK_DIM, WEAPON_CARD_DIM
from re1_rl.world_catalog import NUM_ROOMS
from re1_rl.world_context_module import WorldContextModule, reload_world_catalog_buffers

_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROPRIO_ROOM_INDEX = next(i for i, (name, _) in enumerate(PROPRIO_FIELDS) if name == "room_index")
PROPRIO_CAM_INDEX = next(i for i, (name, _) in enumerate(PROPRIO_FIELDS) if name == "cam_id")

VISION_DIM = 512
CONTROL_DIM = 64
SPATIAL_TOWER_DIM = 192
INVENTORY_TOWER_DIM = 160
HISTORY_TOWER_DIM = 192
FLAGS_TOWER_DIM = 64
JOINT_COMBAT_DIM = 128
WORLD_CONTEXT_DIM = 320
ROOM_EMBED_DIM = 64
CAM_EMBED_DIM = 16
NUM_CAMERAS = 16
ITEM_EMBED_DIM = 16
ENEMY_TYPE_EMBED_DIM = 8
MAX_ENEMY_TYPE_ID = 32

# Verified named persistent-state (see named_state.py). No interaction_prompt.
PERSISTENT_STATE_DIM = NAMED_STATE_DIM
PERSISTENT_TOWER_DIM = 96
NAMED_STATE_OBS_KEY = "named_state"

FEATURES_DIM = 1024
PARAM_HARD_CAP = 5_800_000
PARAM_TARGET = 5_610_000

_OMIT_OBS_KEYS = frozenset({"frame", "world_state", "key_hints", "goal", "affordances"})

_INTERACTABLE_SLOT_DIM = 4
_ITEM_SLOT_DIM = 8


def _tower_out_dim(*, persistent_enabled: bool) -> int:
    width = (
        VISION_DIM
        + CONTROL_DIM
        + SPATIAL_TOWER_DIM
        + INVENTORY_TOWER_DIM
        + HISTORY_TOWER_DIM
        + FLAGS_TOWER_DIM
        + JOINT_COMBAT_DIM
        + WORLD_CONTEXT_DIM
    )
    if persistent_enabled:
        width += PERSISTENT_TOWER_DIM
    return width


TOWER_OUT_DIM = _tower_out_dim(persistent_enabled=PERSISTENT_STATE_DIM > 0)  # 1728 when named_state on


class _MaskedPool(nn.Module):
    """Masked mean + max pool over token dim → 2 * token_dim."""

    def forward(self, tokens: th.Tensor, mask: th.Tensor) -> th.Tensor:
        # tokens: (B, N, D), mask: (B, N) float {0,1}
        mask_f = mask.unsqueeze(-1)
        summed = (tokens * mask_f).sum(dim=1)
        denom = mask_f.sum(dim=1).clamp(min=1.0)
        mean = summed / denom
        neg_inf = th.finfo(tokens.dtype).min
        masked_for_max = tokens.masked_fill(mask_f <= 0, neg_inf)
        mx, _ = masked_for_max.max(dim=1)
        mx = th.where(mask_f.squeeze(-1).sum(dim=1, keepdim=True) > 0, mx, th.zeros_like(mx))
        return th.cat([mean, mx], dim=-1)


class TypedSpatialEncoder(nn.Module):
    def __init__(self, output_dim: int = SPATIAL_TOWER_DIM) -> None:
        super().__init__()
        self.item_id_emb = nn.Embedding(MAX_ITEM_ID + 1, ITEM_EMBED_DIM)
        self.enemy_type_emb = nn.Embedding(MAX_ENEMY_TYPE_ID + 1, ENEMY_TYPE_EMBED_DIM)
        item_in = (_ITEM_SLOT_DIM - 1) + ITEM_EMBED_DIM  # replace raw id with emb
        enemy_in = (ENEMY_SLOT_DIM - 1) + ENEMY_TYPE_EMBED_DIM
        self.item_enc = nn.Sequential(nn.Linear(item_in, 64), nn.ReLU(), nn.Linear(64, 48))
        self.enemy_enc = nn.Sequential(nn.Linear(enemy_in, 64), nn.ReLU(), nn.Linear(64, 48))
        self.exit_enc = nn.Sequential(nn.Linear(EXIT_SLOT_DIM, 32), nn.ReLU(), nn.Linear(32, 32))
        self.interact_enc = nn.Sequential(
            nn.Linear(_INTERACTABLE_SLOT_DIM, 24), nn.ReLU(), nn.Linear(24, 24)
        )
        self.pool = _MaskedPool()
        # scalars: items_obtainable, enemy_count, num_exits, interactables_here
        pooled = 48 * 2 + 48 * 2 + 32 * 2 + 24 * 2 + 4
        self.out = nn.Sequential(
            nn.Linear(pooled, 256),
            nn.ReLU(),
            nn.Linear(256, output_dim),
            nn.ReLU(),
        )

    def forward(self, spatial: th.Tensor) -> th.Tensor:
        b = spatial.shape[0]
        i = 0
        items_obtainable = spatial[:, i : i + 1]
        i += 1
        item_slots = spatial[:, i : i + ITEM_SLOTS * _ITEM_SLOT_DIM].reshape(b, ITEM_SLOTS, _ITEM_SLOT_DIM)
        i += ITEM_SLOTS * _ITEM_SLOT_DIM
        enemy_count = spatial[:, i : i + 1]
        i += 1
        enemy_slots = spatial[:, i : i + ENEMY_SLOTS * ENEMY_SLOT_DIM].reshape(
            b, ENEMY_SLOTS, ENEMY_SLOT_DIM
        )
        i += ENEMY_SLOTS * ENEMY_SLOT_DIM
        num_exits = spatial[:, i : i + 1]
        i += 1
        exit_slots = spatial[:, i : i + EXIT_SLOTS * EXIT_SLOT_DIM].reshape(b, EXIT_SLOTS, EXIT_SLOT_DIM)
        i += EXIT_SLOTS * EXIT_SLOT_DIM
        interactables_here = spatial[:, i : i + 1]
        i += 1
        interact_slots = spatial[:, i : i + INTERACTABLE_SLOTS * _INTERACTABLE_SLOT_DIM].reshape(
            b, INTERACTABLE_SLOTS, _INTERACTABLE_SLOT_DIM
        )

        item_ids = (item_slots[:, :, 5] * float(MAX_ITEM_ID)).round().long().clamp(0, MAX_ITEM_ID)
        item_mask = (item_slots.abs().sum(dim=-1) > 0).float()
        item_feat = th.cat(
            [item_slots[:, :, :5], self.item_id_emb(item_ids), item_slots[:, :, 6:]], dim=-1
        )
        item_tok = self.item_enc(item_feat)

        enemy_ids = (enemy_slots[:, :, 5] * MAX_ENEMY_TYPE_ID).round().long().clamp(0, MAX_ENEMY_TYPE_ID)
        enemy_mask = enemy_slots[:, :, 7].clamp(0, 1)  # alive
        # Drop raw type_id column (index 5); use learned type embedding.
        enemy_feat = th.cat(
            [
                enemy_slots[:, :, :5],
                self.enemy_type_emb(enemy_ids),
                enemy_slots[:, :, 6:],
            ],
            dim=-1,
        )
        enemy_tok = self.enemy_enc(enemy_feat)

        exit_mask = exit_slots[:, :, 4].clamp(0, 1)  # known
        exit_tok = self.exit_enc(exit_slots)
        interact_mask = (interact_slots.abs().sum(dim=-1) > 0).float()
        interact_tok = self.interact_enc(interact_slots)

        pooled = th.cat(
            [
                self.pool(item_tok, item_mask),
                self.pool(enemy_tok, enemy_mask),
                self.pool(exit_tok, exit_mask),
                self.pool(interact_tok, interact_mask),
                items_obtainable,
                enemy_count,
                num_exits,
                interactables_here,
            ],
            dim=-1,
        )
        return self.out(pooled)


class TypedInventoryEncoder(nn.Module):
    def __init__(self, output_dim: int = INVENTORY_TOWER_DIM) -> None:
        super().__init__()
        self.item_emb = nn.Embedding(MAX_ITEM_ID + 1, ITEM_EMBED_DIM)
        self.slot_enc = nn.Sequential(
            nn.Linear(ITEM_EMBED_DIM + 1, 32),
            nn.ReLU(),
            nn.Linear(32, 32),
        )
        self.pool = _MaskedPool()
        # carried pool (64) + box pool (64) + keys + box free/in_room
        in_dim = 64 + 64 + KEYS_HELD_DIM + 2
        self.out = nn.Sequential(
            nn.Linear(in_dim, 192),
            nn.ReLU(),
            nn.Linear(192, output_dim),
            nn.ReLU(),
        )

    def _encode_slots(self, flat: th.Tensor, n_slots: int) -> tuple[th.Tensor, th.Tensor]:
        b = flat.shape[0]
        slots = flat[:, : n_slots * 2].reshape(b, n_slots, 2)
        ids = (slots[:, :, 0] * float(MAX_ITEM_ID)).round().long().clamp(0, MAX_ITEM_ID)
        qty = slots[:, :, 1:2]
        mask = (ids > 0).float()
        tok = self.slot_enc(th.cat([self.item_emb(ids), qty], dim=-1))
        return self.pool(tok, mask), mask

    def forward(self, inventory: th.Tensor, box: th.Tensor, keys_held: th.Tensor) -> th.Tensor:
        carried, _ = self._encode_slots(inventory, INVENTORY_SLOTS)
        box_slots = box[:, :32]
        box_pool, _ = self._encode_slots(box_slots, 16)
        box_meta = box[:, 32:34] if box.shape[-1] >= 34 else th.zeros(
            box.shape[0], 2, device=box.device, dtype=box.dtype
        )
        return self.out(th.cat([carried, box_pool, keys_held, box_meta], dim=-1))


class TypedHistoryEncoder(nn.Module):
    """Shared room/item embeddings + masked pooling (not a giant flatten MLP)."""

    def __init__(self, output_dim: int = HISTORY_TOWER_DIM) -> None:
        super().__init__()
        self.room_emb = nn.Embedding(NUM_ROOMS, ROOM_EMBED_DIM)
        self.item_emb = nn.Embedding(MAX_ITEM_ID + 1, ITEM_EMBED_DIM)
        self.room_tok = nn.Sequential(
            nn.Linear(ROOM_EMBED_DIM + 1, 32),
            nn.ReLU(),
            nn.Linear(32, 32),
        )
        self.acq_tok = nn.Sequential(
            nn.Linear(ITEM_EMBED_DIM + ROOM_EMBED_DIM, 32),
            nn.ReLU(),
            nn.Linear(32, 32),
        )
        self.pool = _MaskedPool()
        # pooled rooms (64) + pooled acq (64) + valid fracs (2) + visited/milestones/ledger
        in_dim = 64 + 64 + 2 + ROOM_VISITED_DIM + MILESTONE_DIM + CUTSCENE_LEDGER_DIM
        self.mlp = nn.Sequential(
            nn.Linear(in_dim, 256),
            nn.ReLU(),
            nn.Linear(256, output_dim),
            nn.ReLU(),
        )

    def forward(
        self,
        history: th.Tensor,
        acquisitions: th.Tensor,
        rooms_visited: th.Tensor,
        milestones: th.Tensor,
        cutscene_ledger: th.Tensor,
    ) -> th.Tensor:
        valid_r = history[:, :1]
        room_idx = (history[:, 1::2][:, :ROOM_DEQUE_K] * 128.0).long().clamp(0, NUM_ROOMS - 1)
        ages = history[:, 2::2][:, :ROOM_DEQUE_K]
        # Mask padded FIFO slots via valid_fraction ≈ filled/K.
        k_r = ROOM_DEQUE_K
        fill_r = (valid_r * float(k_r)).clamp(0, k_r)
        arange_r = th.arange(k_r, device=history.device).unsqueeze(0)
        room_mask = (arange_r < fill_r).float()
        room_tok = self.room_tok(th.cat([self.room_emb(room_idx), ages.unsqueeze(-1)], dim=-1))

        valid_a = acquisitions[:, :1]
        item_ids = (acquisitions[:, 1::2][:, :ACQUISITION_LOG_K] * float(MAX_ITEM_ID)).round().long()
        item_ids = item_ids.clamp(0, MAX_ITEM_ID)
        acq_rooms = (acquisitions[:, 2::2][:, :ACQUISITION_LOG_K] * 128.0).long().clamp(0, NUM_ROOMS - 1)
        k_a = ACQUISITION_LOG_K
        fill_a = (valid_a * float(k_a)).clamp(0, k_a)
        arange_a = th.arange(k_a, device=history.device).unsqueeze(0)
        acq_mask = (arange_a < fill_a).float()
        acq_tok = self.acq_tok(th.cat([self.item_emb(item_ids), self.room_emb(acq_rooms)], dim=-1))

        return self.mlp(
            th.cat(
                [
                    valid_r,
                    self.pool(room_tok, room_mask),
                    valid_a,
                    self.pool(acq_tok, acq_mask),
                    rooms_visited,
                    milestones,
                    cutscene_ledger,
                ],
                dim=-1,
            )
        )


class PersistentStateEncoder(nn.Module):
    """Conditional tower; constructed only when verified fields exist."""

    def __init__(self, input_dim: int, output_dim: int = PERSISTENT_TOWER_DIM) -> None:
        super().__init__()
        if input_dim <= 0:
            raise ValueError("PersistentStateEncoder requires input_dim > 0")
        self.mlp = nn.Sequential(
            nn.Linear(input_dim, 192),
            nn.ReLU(),
            nn.Linear(192, output_dim),
            nn.ReLU(),
        )

    def forward(self, x: th.Tensor) -> th.Tensor:
        return self.mlp(x)


class RE1CombatEfficientExtractor(BaseFeaturesExtractor):
    """NatureCNN + typed towers + joint combat latent + aux heads."""

    def __init__(
        self,
        observation_space: spaces.Dict,
        cnn_output_dim: int = VISION_DIM,
        project_root: str | Path | None = None,
        normalized_image: bool = False,
        features_dim: int = FEATURES_DIM,
        persistent_state_dim: int | None = None,
    ) -> None:
        if not isinstance(observation_space, spaces.Dict):
            raise TypeError(
                f"RE1CombatEfficientExtractor expects Dict obs, got {type(observation_space)}"
            )
        if "frame" not in observation_space.spaces:
            raise ValueError("observation_space must include 'frame'")

        super().__init__(observation_space, features_dim=features_dim)
        root = Path(project_root) if project_root else _DEFAULT_PROJECT_ROOT
        self._cnn_output_dim = cnn_output_dim
        self._persistent_dim = (
            PERSISTENT_STATE_DIM if persistent_state_dim is None else int(persistent_state_dim)
        )
        self._persistent_enabled = self._persistent_dim > 0
        self._tower_out_dim = _tower_out_dim(persistent_enabled=self._persistent_enabled)

        self.cnn_extractor = NatureCNN(
            observation_space.spaces["frame"],
            features_dim=cnn_output_dim,
            normalized_image=normalized_image,
        )

        self.room_embedding = nn.Embedding(NUM_ROOMS, ROOM_EMBED_DIM)
        self.camera_embedding = nn.Embedding(NUM_CAMERAS, CAM_EMBED_DIM)
        # Drop room_index + cam_id scalars; replace with embeddings.
        control_in = PROPRIO_DIM - 2 + ROOM_EMBED_DIM + CAM_EMBED_DIM
        self.control_mlp = nn.Sequential(
            nn.Linear(control_in, 128),
            nn.ReLU(),
            nn.Linear(128, CONTROL_DIM),
            nn.ReLU(),
        )

        self.spatial_encoder = TypedSpatialEncoder(SPATIAL_TOWER_DIM)
        self.inventory_encoder = TypedInventoryEncoder(INVENTORY_TOWER_DIM)
        self.history_encoder = TypedHistoryEncoder(HISTORY_TOWER_DIM)

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
            nn.Linear(128, 128),
            nn.ReLU(),
        )

        # Joint combat: project vision + modality encodings → 128.
        self.vision_combat_proj = nn.Sequential(
            nn.Linear(cnn_output_dim, 64),
            nn.ReLU(),
        )
        joint_in = 64 + SPATIAL_TOWER_DIM + 128 + INVENTORY_TOWER_DIM + CONTROL_DIM
        self.joint_combat = nn.Sequential(
            nn.Linear(joint_in, 192),
            nn.ReLU(),
            nn.Linear(192, JOINT_COMBAT_DIM),
            nn.ReLU(),
        )

        self.outcome_head = nn.Sequential(
            nn.Linear(JOINT_COMBAT_DIM, 64),
            nn.ReLU(),
            nn.Linear(64, COMBAT_OUTCOME_DIM),
        )

        self.world_context = WorldContextModule(
            output_dim=WORLD_CONTEXT_DIM,
            hidden_dim=512,
            project_root=root,
        )

        if self._persistent_enabled:
            self.persistent_encoder = PersistentStateEncoder(self._persistent_dim)
        else:
            self.persistent_encoder = None

        # Compact semantic concat for whole-game aux (not full tower width).
        self.world_event_proj = nn.Sequential(
            nn.Linear(self._tower_out_dim, 128),
            nn.ReLU(),
        )
        self.world_event_head = nn.Linear(128, WORLD_EVENT_DIM)

        self.fusion_norm = nn.LayerNorm(self._tower_out_dim)
        self.fusion_proj = nn.Sequential(
            nn.Linear(self._tower_out_dim, features_dim),
            nn.ReLU(),
        )

        # Caches for auxiliary losses (set during forward when return_aux=True).
        self._last_combat_latent: th.Tensor | None = None
        self._last_outcome_pred: th.Tensor | None = None
        self._last_world_event_pred: th.Tensor | None = None
        self._last_tower_concat: th.Tensor | None = None

    def _room_index(self, proprio: th.Tensor) -> th.Tensor:
        raw = proprio[:, PROPRIO_ROOM_INDEX] * 128.0
        return raw.long().clamp(0, NUM_ROOMS - 1)

    def _cam_index(self, proprio: th.Tensor) -> th.Tensor:
        raw = proprio[:, PROPRIO_CAM_INDEX] * float(NUM_CAMERAS)
        return raw.long().clamp(0, NUM_CAMERAS - 1)

    def _control_features(self, observations: TensorDict) -> th.Tensor:
        proprio = observations["proprio"]
        room_emb = self.room_embedding(self._room_index(proprio))
        cam_emb = self.camera_embedding(self._cam_index(proprio))
        # Keep all proprio scalars except room_index and cam_id.
        idx = list(range(PROPRIO_DIM))
        idx.remove(PROPRIO_ROOM_INDEX)
        idx.remove(PROPRIO_CAM_INDEX)
        scalars = proprio[:, idx]
        return self.control_mlp(th.cat([scalars, room_emb, cam_emb], dim=-1))

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

    def forward_features(
        self,
        observations: TensorDict,
        *,
        return_aux: bool = False,
    ) -> th.Tensor | tuple[th.Tensor, dict[str, th.Tensor]]:
        frame = observations["frame"]
        if frame.dtype != th.float32:
            frame = frame.float()
        vision = self.cnn_extractor(frame)
        control = self._control_features(observations)
        spatial = self.spatial_encoder(self._optional_tensor(observations, "spatial", SPATIAL_DIM))
        inventory = self.inventory_encoder(
            self._optional_tensor(observations, "inventory", INVENTORY_OBS_DIM),
            self._optional_tensor(observations, "box", BOX_DIM),
            self._optional_tensor(observations, "keys_held", KEYS_HELD_DIM),
        )
        history = self.history_encoder(
            self._optional_tensor(observations, "history", ROOM_HISTORY_DIM),
            self._optional_tensor(observations, "acquisitions", ACQUISITION_LOG_DIM),
            self._optional_tensor(observations, "rooms_visited", ROOM_VISITED_DIM),
            self._optional_tensor(observations, "milestones", MILESTONE_DIM),
            self._optional_tensor(observations, "cutscene_ledger", CUTSCENE_LEDGER_DIM),
        )
        visited = self._optional_tensor(
            observations,
            "visited",
            int(VISITED_SHAPE[0] * VISITED_SHAPE[1] * VISITED_SHAPE[2]),
        )
        flags = self.flags_mlp(
            th.cat(
                [self._optional_tensor(observations, "maps_files", MAPS_FILES_DIM), visited],
                dim=-1,
            )
        )
        combat_enc = self.combat_mlp(
            th.cat(
                [
                    self._optional_tensor(observations, "weapon_card", WEAPON_CARD_DIM),
                    self._optional_tensor(observations, "last_attack", LAST_ATTACK_DIM),
                    self._optional_tensor(observations, "room_enemies", ENEMY_ROSTER_DIM),
                ],
                dim=-1,
            )
        )
        # last_attack here is from the *previous* transition when used as
        # rollout obs[t] (env clears/refills per step). Do not inject
        # same-transition post-action outcomes into this path.
        joint = self.joint_combat(
            th.cat(
                [
                    self.vision_combat_proj(vision),
                    spatial,
                    combat_enc,
                    inventory,
                    control,
                ],
                dim=-1,
            )
        )
        parts = [
            vision,
            control,
            spatial,
            inventory,
            history,
            flags,
            joint,
            self.world_context(observations),
        ]
        if self._persistent_enabled and self.persistent_encoder is not None:
            pers = self._optional_tensor(observations, NAMED_STATE_OBS_KEY, self._persistent_dim)
            parts.append(self.persistent_encoder(pers))
        tower = th.cat(parts, dim=-1)
        fused = self.fusion_proj(self.fusion_norm(tower))

        outcome_pred = self.outcome_head(joint)
        world_event_pred = self.world_event_head(self.world_event_proj(tower))
        self._last_combat_latent = joint
        self._last_outcome_pred = outcome_pred
        self._last_world_event_pred = world_event_pred
        self._last_tower_concat = tower

        if return_aux:
            return fused, {
                "combat_latent": joint,
                "outcome_pred": outcome_pred,
                "world_event_pred": world_event_pred,
                "tower_concat": tower,
            }
        return fused

    def forward(self, observations: TensorDict) -> th.Tensor:
        out = self.forward_features(observations, return_aux=False)
        assert isinstance(out, th.Tensor)
        return out

    def predict_aux(self, observations: TensorDict) -> dict[str, th.Tensor]:
        _, aux = self.forward_features(observations, return_aux=True)  # type: ignore[misc]
        return aux


def count_extractor_params(extractor: nn.Module) -> int:
    return sum(p.numel() for p in extractor.parameters() if p.requires_grad)


def reload_combat_efficient_world_catalog_buffers(
    policy: nn.Module, project_root: str | Path | None = None
) -> None:
    module: nn.Module = policy
    if hasattr(module, "policy"):
        module = module.policy
    extractor = getattr(module, "features_extractor", module)
    if not isinstance(extractor, RE1CombatEfficientExtractor):
        raise TypeError(
            "reload_combat_efficient_world_catalog_buffers expected "
            f"RE1CombatEfficientExtractor, got {type(extractor)}"
        )
    reload_world_catalog_buffers(extractor.world_context, project_root)
