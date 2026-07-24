"""Transplant Doc04 checkpoint weights across catalog / obs-shape bumps.

Copies all matching tensors; remaps expanded ``inventory_mlp`` and
``world_context.mlp`` layer-1 input columns block-by-block (pickup rows,
key joins, files, combine recipes). New columns stay zero-init.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Iterable

import torch

from re1_rl.world_catalog import MAX_NEIGHBORS, NUM_ROOMS, WorldCatalog

# Checkpoint ppo_re1_27205353_steps.zip (pre-almanac125 fleet).
OLD_NUM_PICKUPS = 119
OLD_NUM_KEYS = 35
OLD_KEYS_HELD_DIM = 35


def world_mlp_blocks(
    num_pickups: int,
    num_keys: int,
    num_files: int,
    num_combine: int,
    file_code_width: int,
) -> list[tuple[str, int]]:
    room_topo = MAX_NEIGHBORS + 1 + 1 + 1 + MAX_NEIGHBORS
    return [
        ("room_topo", room_topo),
        ("pickup_active", num_pickups),
        ("pickup_gated", num_pickups),
        ("room_remaining", NUM_ROOMS),
        ("requires_join", num_keys),
        ("pickup_join", 4),
        ("gated_join", 2),
        ("room_rem_area", 1),
        ("room_rem_stage", 1),
        ("kp_join", num_keys),
        ("ku_join", num_keys),
        ("unlock_join", num_keys),
        ("door_join", num_keys),
        ("file_join", 4),
        ("held_codes", file_code_width),
        ("in_room_codes", file_code_width),
        ("file_in_room", num_files),
        ("combine_join", 4),
        ("recipe_avail", num_combine),
    ]


def _block_slices(blocks: Iterable[tuple[str, int]]) -> dict[str, slice]:
    slices: dict[str, slice] = {}
    start = 0
    for name, width in blocks:
        slices[name] = slice(start, start + width)
        start += width
    return slices


def remap_world_mlp_layer1(old_w: torch.Tensor, new_w: torch.Tensor, report: list[str]) -> None:
    """Remap world_context.mlp.0.weight columns old_in -> new_in."""
    catalog = WorldCatalog.from_files(Path(__file__).resolve().parents[1])
    old_blocks = world_mlp_blocks(
        OLD_NUM_PICKUPS,
        OLD_NUM_KEYS,
        num_files=7,  # inferred from 597-d checkpoint layout
        num_combine=10,
        file_code_width=catalog.file_code_width,
    )
    new_blocks = world_mlp_blocks(
        catalog.num_pickups,
        catalog.num_keys,
        num_files=catalog.num_files,
        num_combine=catalog.num_combine,
        file_code_width=catalog.file_code_width,
    )
    old_in = sum(w for _, w in old_blocks)
    new_in = sum(w for _, w in new_blocks)
    if old_w.shape[1] != old_in:
        # Fall back: infer old files/combine from weight width.
        tail = old_w.shape[1] - (old_in - 7 - 10)
        for nf in range(1, 20):
            for nc in range(1, 20):
                trial = world_mlp_blocks(
                    OLD_NUM_PICKUPS, OLD_NUM_KEYS, nf, nc, catalog.file_code_width
                )
                if sum(w for _, w in trial) == old_w.shape[1]:
                    old_blocks = trial
                    old_in = old_w.shape[1]
                    break
            if old_w.shape[1] == old_in:
                break
    if new_w.shape[1] != new_in:
        raise RuntimeError(f"new world_mlp in mismatch: {new_w.shape[1]} vs {new_in}")

    old_slices = _block_slices(old_blocks)
    new_slices = _block_slices(new_blocks)
    new_w.zero_()
    n_out = min(old_w.shape[0], new_w.shape[0])
    for name in old_slices:
        if name not in new_slices:
            report.append(f"world_mlp skip block {name}")
            continue
        o = old_slices[name]
        n = new_slices[name]
        copy_w = min(o.stop - o.start, n.stop - n.start)
        new_w[:n_out, n.start : n.start + copy_w] = old_w[:n_out, o.start : o.start + copy_w]
        report.append(f"world_mlp.{name} cols {copy_w}/{n.stop - n.start}")


def remap_inventory_mlp_layer1(old_w: torch.Tensor, new_w: torch.Tensor, report: list[str]) -> None:
    """inventory = inv + box + keys_held; copy legacy prefix, zero new key cols."""
    from re1_rl.obs_encoder import BOX_DIM, INVENTORY_OBS_DIM

    prefix = INVENTORY_OBS_DIM + BOX_DIM + OLD_KEYS_HELD_DIM
    if old_w.shape[1] != prefix:
        prefix = old_w.shape[1]
    copy_w = min(prefix, new_w.shape[1])
    n_out = min(old_w.shape[0], new_w.shape[0])
    new_w[:n_out, :copy_w] = old_w[:n_out, :copy_w]
    if new_w.shape[1] > copy_w:
        new_w[:n_out, copy_w:].zero_()
    report.append(f"inventory_mlp cols copied {copy_w}/{new_w.shape[1]}")


def _prefixes_for_extractor() -> tuple[str, ...]:
    return (
        "features_extractor.",
        "pi_features_extractor.",
        "vf_features_extractor.",
    )


def transplant_doc04_state_dict(
    old_sd: dict[str, torch.Tensor],
    new_sd: dict[str, torch.Tensor],
) -> dict[str, list[str]]:
    report: dict[str, list[str]] = {"copied": [], "remapped": [], "skipped": []}

    for key, old_t in old_sd.items():
        if key not in new_sd:
            report["skipped"].append(f"missing {key}")
            continue
        new_t = new_sd[key]
        if tuple(old_t.shape) == tuple(new_t.shape):
            new_t.copy_(old_t)
            report["copied"].append(key)
            continue
        if key.endswith("inventory_mlp.0.weight"):
            remap_inventory_mlp_layer1(old_t, new_t, report["remapped"])
            continue
        if key.endswith("world_context.mlp.0.weight"):
            remap_world_mlp_layer1(old_t, new_t, report["remapped"])
            continue
        if key.endswith(".0.bias") and (
            "inventory_mlp" in key or "world_context.mlp" in key
        ):
            n = min(old_t.shape[0], new_t.shape[0])
            new_t[:n].copy_(old_t[:n])
            report["remapped"].append(f"{key} bias {n}")
            continue
        report["skipped"].append(f"{key} {tuple(old_t.shape)} -> {tuple(new_t.shape)}")

    return report


def load_policy_state_dict(src_zip: Path) -> tuple[dict[str, torch.Tensor], int]:
    with zipfile.ZipFile(src_zip) as zf:
        raw_sd = torch.load(io.BytesIO(zf.read("policy.pth")), map_location="cpu")
        meta = json.loads(zf.read("data"))
    return raw_sd, int(meta.get("num_timesteps", 0))


def transplant_doc04_checkpoint(src_zip: Path, out_base: Path, *, device: str = "cpu"):
    """Load donor zip, transplant into current POLICY_KWARGS, save survivor."""
    from sb3_contrib import MaskablePPO

    from re1_rl.async_fleet import _policy_obs_and_act_spaces
    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.doc04_medium_extractor import reload_doc04_world_catalog_buffers
    from re1_rl.policy_config import POLICY_KWARGS

    old_sd, donor_steps = load_policy_state_dict(src_zip)
    policy_obs, act_space = _policy_obs_and_act_spaces()
    model = MaskablePPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs, act_space),
        policy_kwargs=POLICY_KWARGS,
        device=device,
        verbose=0,
    )
    new_sd = model.policy.state_dict()
    report = transplant_doc04_state_dict(old_sd, new_sd)
    model.policy.load_state_dict(new_sd, strict=False)
    reload_doc04_world_catalog_buffers(model.policy)
    model.num_timesteps = donor_steps
    out_base.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out_base))
    out_zip = out_base if out_base.suffix == ".zip" else Path(str(out_base) + ".zip")
    return model, out_zip, report
