"""Shared world/almanac context MLP used by policy feature extractors."""

from __future__ import annotations

from pathlib import Path

import torch as th
from torch import nn

from re1_rl.item_affordances import KEY_HINTS_DIM
from re1_rl.obs_encoder import MAX_ITEM_ID, PROPRIO_FIELDS
from re1_rl.world_catalog import NUM_ROOMS, WorldCatalog

_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROPRIO_ROOM_INDEX = next(i for i, (name, _) in enumerate(PROPRIO_FIELDS) if name == "room_index")

NUM_PICKUP_ROWS = 125
PICKUP_ACTIVE_OFF = 0
PICKUP_GATED_OFF = NUM_PICKUP_ROWS
ROOM_REMAINING_OFF = PICKUP_GATED_OFF + NUM_PICKUP_ROWS
KEY_PENDING_OFF = ROOM_REMAINING_OFF + NUM_ROOMS
WORLD_STATE_DIM = KEY_PENDING_OFF + KEY_HINTS_DIM


def world_mlp_input_dim(
    num_keys: int,
    *,
    num_files: int,
    num_combine: int,
    file_code_width: int,
) -> int:
    from re1_rl.world_catalog import MAX_NEIGHBORS

    room_topo = MAX_NEIGHBORS + 1 + 1 + 1 + MAX_NEIGHBORS
    pickup_join_scalars = 4
    gated_join_scalars = 2
    room_rem_scalars = 2
    key_join = num_keys * 4
    file_join_scalars = 4
    file_codes = file_code_width * 2
    combine_join_scalars = 4
    return (
        room_topo
        + NUM_PICKUP_ROWS
        + NUM_PICKUP_ROWS
        + NUM_ROOMS
        + num_keys
        + pickup_join_scalars
        + gated_join_scalars
        + room_rem_scalars
        + key_join
        + file_join_scalars
        + file_codes
        + num_files
        + combine_join_scalars
        + num_combine
    )


class WorldContextModule(nn.Module):
    """Frozen catalog buffers + dynamic gather -> world context vector."""

    def __init__(
        self,
        *,
        output_dim: int,
        hidden_dim: int,
        project_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        root = Path(project_root) if project_root else _DEFAULT_PROJECT_ROOT
        if not (root / "data" / "rooms.json").is_file():
            root = _DEFAULT_PROJECT_ROOT
        catalog = WorldCatalog.from_files(root)

        self._world_mlp_in = world_mlp_input_dim(
            catalog.num_keys,
            num_files=catalog.num_files,
            num_combine=catalog.num_combine,
            file_code_width=catalog.file_code_width,
        )
        self._num_pickups = catalog.num_pickups
        self._num_keys = catalog.num_keys
        self._output_dim = output_dim

        self.mlp = nn.Sequential(
            nn.Linear(self._world_mlp_in, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim),
            nn.ReLU(),
        )

        for name, tensor in catalog.as_torch_buffers().items():
            self.register_buffer(name, tensor, persistent=False)

    @property
    def output_dim(self) -> int:
        return self._output_dim

    def _room_index(self, proprio: th.Tensor) -> th.Tensor:
        raw = proprio[:, PROPRIO_ROOM_INDEX] * 128.0
        return raw.long().clamp(0, NUM_ROOMS - 1)

    def _gather_room_row(self, buf: th.Tensor, room: th.Tensor) -> th.Tensor:
        return buf.index_select(0, room.reshape(-1))

    def _world_state_tensor(
        self,
        observations: dict[str, th.Tensor],
        batch: int,
        device: th.device,
    ) -> th.Tensor:
        if "world_state" in observations:
            return observations["world_state"]
        return th.zeros(batch, WORLD_STATE_DIM, device=device, dtype=th.float32)

    def _key_pending_block(self, observations: dict[str, th.Tensor], ws: th.Tensor) -> th.Tensor:
        if "key_hints" in observations:
            return observations["key_hints"]
        return ws[:, KEY_PENDING_OFF : KEY_PENDING_OFF + KEY_HINTS_DIM]

    def _inventory_item_ids(self, observations: dict[str, th.Tensor]) -> th.Tensor:
        inv = observations.get("inventory")
        if inv is None:
            batch = observations["proprio"].shape[0]
            device = observations["proprio"].device
            return th.zeros(batch, 8, device=device, dtype=th.float32)
        return inv[:, 0::2] * float(MAX_ITEM_ID)

    def build_features(self, observations: dict[str, th.Tensor]) -> th.Tensor:
        proprio = observations["proprio"]
        batch = proprio.shape[0]
        device = proprio.device
        room = self._room_index(proprio)

        neighbors = self._gather_room_row(self.map_neighbors, room)
        degree = self._gather_room_row(self.map_degree, room).unsqueeze(-1)
        area = self._gather_room_row(self.room_area, room).unsqueeze(-1)
        stage = self._gather_room_row(self.room_stage, room).unsqueeze(-1)
        link_key = self._gather_room_row(self.link_requires_key, room)
        room_topo = th.cat([neighbors, degree, area, stage, link_key], dim=-1)

        ws = self._world_state_tensor(observations, batch, device)
        pickup_active = ws[:, PICKUP_ACTIVE_OFF : PICKUP_ACTIVE_OFF + NUM_PICKUP_ROWS]
        pickup_gated = ws[:, PICKUP_GATED_OFF : PICKUP_GATED_OFF + NUM_PICKUP_ROWS]
        room_remaining = ws[:, ROOM_REMAINING_OFF : ROOM_REMAINING_OFF + NUM_ROOMS]

        requires_join = pickup_active @ self.pickup_requires_mask

        pickup_join = th.stack(
            [
                (pickup_active * self.pickup_item_id.unsqueeze(0)).sum(dim=1),
                (pickup_active * self.pickup_category.unsqueeze(0)).sum(dim=1),
                (pickup_active * self.pickup_key_flag.unsqueeze(0)).sum(dim=1),
                pickup_active.sum(dim=1),
            ],
            dim=-1,
        )
        gated_join = th.stack(
            [
                (pickup_gated * self.pickup_item_id.unsqueeze(0)).sum(dim=1),
                pickup_gated.sum(dim=1),
            ],
            dim=-1,
        )
        room_rem_area = (room_remaining * self.room_area.unsqueeze(0)).sum(dim=1, keepdim=True)
        room_rem_stage = (room_remaining * self.room_stage.unsqueeze(0)).sum(dim=1, keepdim=True)

        key_block = self._key_pending_block(observations, ws)
        kp = key_block[:, 0 : self._num_keys]
        ku = key_block[:, self._num_keys : 2 * self._num_keys]
        ka = key_block[:, 2 * self._num_keys : 3 * self._num_keys]
        kp_join = kp * self.key_pickup_room.unsqueeze(0)
        ku_join = ku * self.key_use_room.unsqueeze(0)
        unlock_join = ku * self.key_unlock_room.unsqueeze(0)
        door_join = ka * self.key_door_from.unsqueeze(0)

        inv_ids = self._inventory_item_ids(observations)
        file_in_room = (self.file_room_idx.unsqueeze(0) == room.unsqueeze(-1)).float()
        file_held = (inv_ids.unsqueeze(-1) == self.file_id.unsqueeze(0).unsqueeze(0)).any(dim=1).float()
        file_join = th.stack(
            [
                (file_in_room * self.file_id.unsqueeze(0)).sum(dim=1),
                file_in_room.sum(dim=1),
                (file_held * self.file_id.unsqueeze(0)).sum(dim=1),
                file_held.sum(dim=1),
            ],
            dim=-1,
        )
        held_codes = file_held @ self.file_code_const
        in_room_codes = file_in_room @ self.file_code_const

        has_a = (inv_ids.unsqueeze(-1) == self.combine_src_a.unsqueeze(0).unsqueeze(0)).any(dim=1)
        has_b = (inv_ids.unsqueeze(-1) == self.combine_src_b.unsqueeze(0).unsqueeze(0)).any(dim=1)
        recipe_avail = (has_a & has_b).float()
        combine_join = th.stack(
            [
                (recipe_avail * self.combine_dst.unsqueeze(0)).sum(dim=1),
                recipe_avail.sum(dim=1),
                (recipe_avail * self.combine_src_a.unsqueeze(0)).sum(dim=1),
                (recipe_avail * self.combine_src_b.unsqueeze(0)).sum(dim=1),
            ],
            dim=-1,
        )

        return th.cat(
            [
                room_topo,
                pickup_active,
                pickup_gated,
                room_remaining,
                requires_join,
                pickup_join,
                gated_join,
                room_rem_area,
                room_rem_stage,
                kp_join,
                ku_join,
                unlock_join,
                door_join,
                file_join,
                held_codes,
                in_room_codes,
                file_in_room,
                combine_join,
                recipe_avail,
            ],
            dim=-1,
        )

    def forward(self, observations: dict[str, th.Tensor]) -> th.Tensor:
        return self.mlp(self.build_features(observations))


def reload_world_catalog_buffers(module: nn.Module, project_root: str | Path | None = None) -> None:
    """Reload static almanac buffers from JSON onto a WorldContextModule."""
    root = Path(project_root) if project_root else _DEFAULT_PROJECT_ROOT
    if not (root / "data" / "rooms.json").is_file():
        root = _DEFAULT_PROJECT_ROOT
    if not isinstance(module, WorldContextModule):
        raise TypeError(f"expected WorldContextModule, got {type(module)}")
    buffers = WorldCatalog.from_files(root).as_torch_buffers()
    device = next(module.parameters()).device
    for name, tensor in buffers.items():
        buf = getattr(module, name)
        buf.copy_(tensor.to(device=device, dtype=buf.dtype))
