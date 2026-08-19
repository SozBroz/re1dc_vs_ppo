"""Desync actor fleet + central inference learner (default training path)."""

from __future__ import annotations

import math
import multiprocessing as mp
import os
import time
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from re1_rl.reward import RL_GAMMA

DISTRIBUTED_MC_HALF_LIVES = 6


def distributed_n_steps(*, half_lives: int = DISTRIBUTED_MC_HALF_LIVES) -> int:
    """MC rollout horizon in policy steps (≈6× γ half-life at RL_GAMMA)."""
    per_half_life = math.log(0.5) / math.log(RL_GAMMA)
    return int(round(half_lives * per_half_life))


_DISTRIBUTED_N_STEPS = distributed_n_steps()

PPO_HYPERPARAMS: dict[str, Any] = dict(
    n_steps=1024,
    batch_size=512,
    n_epochs=4,
    learning_rate=3e-4,
    gamma=RL_GAMMA,
    ent_coef=0.005,
)

# Distributed 6-minute sync epochs: larger on-policy batches, gentler updates.
# Used only by ``scripts/distributed_train_parallel.py`` (not monolithic async).
#
# n_steps vs sync_interval_s (wall) vs emulated time:
#   - sync_interval_s=360 is WALL clock (upload burst + weight pull cadence).
#   - Actors cut MC/bootstrap rollouts at n_steps, then buffer until the wall flush.
#   - Env step ≈ 8 frames @ 60fps ⇒ 8/60 s emulated; γ half-life ≈ 25s
#     emulated (RAILS_CREDIT_HALF_LIFE_S). n_steps targets 6 half-lives
#     (≈1125 steps ≈ 150s emulated).
#   - Credit assignment is per n_steps segment, not the whole sync window.
DISTRIBUTED_EPOCH_HYPERPARAMS: dict[str, Any] = dict(
    n_steps=_DISTRIBUTED_N_STEPS,
    batch_size=2048,  # Doc04 medium + WH2 8GB VRAM; was 4096 on ~2M policy
    n_epochs=4,
    learning_rate=1e-4,
    gamma=RL_GAMMA,
    ent_coef=0.005,
)
DEFAULT_SYNC_INTERVAL_S = 360.0

_LEGACY_45_ACTION_NAMES = (
    "noop",
    "forward",
    "back",
    "turn_left",
    "turn_right",
    "run_forward",
    "attack_up",
    "interact",
    "attack",
    "use",
    "equip",
    *(f"deposit_slot_{i}" for i in range(8)),
    *(f"withdraw_box_{i}" for i in range(16)),
    "combine",
    *(f"select_slot_{i}" for i in range(8)),
    "attack_down",
)
_CURRENT_45_ACTION_NAMES = (
    "noop",
    "forward",
    "back",
    "turn_left",
    "turn_right",
    "run_forward",
    "attack_up",
    "attack",
    "attack_down",
    "interact",
    "use",
    "equip",
    *(f"deposit_slot_{i}" for i in range(8)),
    *(f"withdraw_box_{i}" for i in range(16)),
    "combine",
    *(f"select_slot_{i}" for i in range(8)),
)
_LEGACY_GENERIC_ATTACK_ACTION = 8


def _obs_batch_for_one(obs: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: np.expand_dims(v, 0) for k, v in obs.items()}


def _obs_batch_for_many(need_msgs: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    """Stack per-env obs dicts into one batch (n_envs, ...)."""
    if not need_msgs:
        raise ValueError("empty need_msgs")
    first = need_msgs[0]["obs"]
    return {
        key: np.stack([msg["obs"][key] for msg in need_msgs], axis=0)
        for key in first
    }


def _apply_mod_drop_from_needs(policy: Any, msgs: list[dict[str, Any]]) -> None:
    """Set stored ModDrop presence on the inference policy when actors send it."""
    if not hasattr(policy, "set_mod_drop_masks"):
        return
    if not msgs or any(m.get("mod_drop_mask") is None for m in msgs):
        policy.set_mod_drop_masks(None)
        return
    stacked = np.stack(
        [np.asarray(m["mod_drop_mask"], dtype=np.float32) for m in msgs], axis=0
    )
    policy.set_mod_drop_masks(stacked)


def _serve_needs_batch(
    pairs: list[tuple[Connection, dict[str, Any]]],
    policy: Any,
    *,
    max_batch: int = 32,
) -> list[Connection]:
    """Answer one or more actor ``need`` messages with batched inference."""
    if not pairs:
        return []
    failed: list[Connection] = []

    def _send(conn: Connection, payload: dict[str, Any]) -> None:
        try:
            conn.send(payload)
        except (BrokenPipeError, EOFError, OSError):
            if conn not in failed:
                failed.append(conn)

    def _serve_regular(chunk: list[tuple[Connection, dict[str, Any]]]) -> None:
        if not chunk:
            return
        msgs = [msg for _, msg in chunk]
        obs_batch = _obs_batch_for_many(msgs)
        masks_list = [msg.get("action_masks") for msg in msgs]
        if any(m is None for m in masks_list):
            for conn, msg in chunk:
                obs_one = _obs_batch_for_one(msg["obs"])
                masks = msg.get("action_masks")
                policy_version = int(getattr(policy, "policy_version", 0) or 0)
                _apply_mod_drop_from_needs(policy, [msg])
                try:
                    if masks is not None:
                        act, val, lp = policy.predict_masked(
                            obs_one, np.asarray(masks, dtype=bool)
                        )
                    else:
                        act_a, val_a, lp_a = policy.predict_batch(obs_one)
                        act, val, lp = int(act_a[0]), float(val_a[0]), float(lp_a[0])
                finally:
                    if hasattr(policy, "set_mod_drop_masks"):
                        policy.set_mod_drop_masks(None)
                _send(
                    conn,
                    {
                        "t": "act",
                        "action": act,
                        "value": val,
                        "logprob": lp,
                        "policy_version": policy_version,
                    }
                )
            return
        masks = np.asarray(masks_list, dtype=bool)
        _apply_mod_drop_from_needs(policy, msgs)
        try:
            actions, values, log_probs = policy.predict_masked_batch(obs_batch, masks)
        finally:
            if hasattr(policy, "set_mod_drop_masks"):
                policy.set_mod_drop_masks(None)
        policy_version = int(getattr(policy, "policy_version", 0) or 0)
        for i, (conn, _) in enumerate(chunk):
            _send(
                conn,
                {
                    "t": "act",
                    "action": int(actions[i]),
                    "value": float(values[i]),
                    "logprob": float(log_probs[i]),
                    "policy_version": policy_version,
                }
            )

    def _serve_diagnostics(chunk: list[tuple[Connection, dict[str, Any]]]) -> None:
        if not chunk:
            return
        usable = [
            pair for pair in chunk if pair[1].get("action_masks") is not None
        ]
        fallback = [
            pair for pair in chunk if pair[1].get("action_masks") is None
        ]
        _serve_regular(fallback)
        if not usable:
            return
        msgs = [msg for _, msg in usable]
        obs_batch = _obs_batch_for_many(msgs)
        masks = np.asarray([msg["action_masks"] for msg in msgs], dtype=bool)
        _apply_mod_drop_from_needs(policy, msgs)
        try:
            actions, values, log_probs, raw_logits, masked_probs = (
                policy.predict_masked_batch_with_diagnostics(obs_batch, masks)
            )
        finally:
            if hasattr(policy, "set_mod_drop_masks"):
                policy.set_mod_drop_masks(None)
        policy_version = int(getattr(policy, "policy_version", 0) or 0)
        for i, (conn, _) in enumerate(usable):
            _send(
                conn,
                {
                    "t": "act",
                    "action": int(actions[i]),
                    "value": float(values[i]),
                    "logprob": float(log_probs[i]),
                    "policy_version": policy_version,
                    "raw_logits": np.asarray(raw_logits[i]),
                    "masked_probs": np.asarray(masked_probs[i]),
                }
            )

    chunk_size = max(1, int(max_batch))
    for start in range(0, len(pairs), chunk_size):
        chunk = pairs[start : start + chunk_size]
        regular = [
            pair for pair in chunk
            if not bool(pair[1].get("want_policy_diagnostics"))
        ]
        diagnostic = [
            pair for pair in chunk
            if bool(pair[1].get("want_policy_diagnostics"))
        ]
        _serve_regular(regular)
        _serve_diagnostics(diagnostic)
    return failed


def _drain_actor_messages(
    ready: list[Connection],
    all_conns: list[Connection],
    *,
    max_need_batch: int,
    batch_window_s: float = 0.002,
) -> tuple[
    list[tuple[Connection, dict[str, Any]]],
    list[tuple[Connection, dict[str, Any]]],
    list[Connection],
]:
    """Collect ``need`` / ``rollout`` messages; briefly coalesce stray needs."""
    needs: list[tuple[Connection, dict[str, Any]]] = []
    rollouts: list[tuple[Connection, dict[str, Any]]] = []
    failed: list[Connection] = []

    def _take(conn: Connection) -> None:
        while True:
            try:
                if not conn.poll():
                    break
                msg = conn.recv()
            except (BrokenPipeError, EOFError, OSError):
                if conn not in failed:
                    failed.append(conn)
                break
            kind = msg.get("t")
            if kind == "need":
                needs.append((conn, msg))
            elif kind == "rollout":
                rollouts.append((conn, msg))

    for conn in ready:
        _take(conn)

    if (
        needs
        and batch_window_s > 0
        and len(needs) < max(1, int(max_need_batch))
    ):
        deadline = time.monotonic() + batch_window_s
        while time.monotonic() < deadline and len(needs) < max(1, int(max_need_batch)):
            got = False
            for conn in all_conns:
                try:
                    if not conn.poll():
                        continue
                    got = True
                    msg = conn.recv()
                except (BrokenPipeError, EOFError, OSError):
                    if conn not in failed:
                        failed.append(conn)
                    continue
                kind = msg.get("t")
                if kind == "need":
                    needs.append((conn, msg))
                elif kind == "rollout":
                    rollouts.append((conn, msg))
            if not got:
                time.sleep(0.0002)

    return needs, rollouts, failed

def _policy_obs_and_act_spaces():
    from re1_rl.distributed.spaces import make_re1_policy_spaces

    return make_re1_policy_spaces()


def _checkpoint_spaces_compatible(model) -> bool:
    policy_obs, act_space = _policy_obs_and_act_spaces()
    loaded_keys = set(model.observation_space.spaces.keys())
    current_keys = set(policy_obs.spaces.keys())
    if loaded_keys != current_keys:
        return False
    if int(model.action_space.n) != int(act_space.n):
        return False
    for key, space in policy_obs.spaces.items():
        if tuple(model.observation_space.spaces[key].shape) != tuple(space.shape):
            return False
    return True


def _checkpoint_missing_policy_keys(checkpoint: Path, model) -> list[str]:
    """Inspect donor policy keys so SB3's non-exact fallback is never silent."""
    import io
    import zipfile

    import torch

    try:
        with zipfile.ZipFile(checkpoint) as zf:
            donor = torch.load(
                io.BytesIO(zf.read("policy.pth")),
                map_location="cpu",
                weights_only=True,
            )
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        raise RuntimeError(
            f"cannot inspect resume checkpoint policy state: {checkpoint}"
        ) from exc
    return sorted(set(model.policy.state_dict()) - set(donor))


_LEGACY_47_ACTION_NAMES = (
    "noop",
    "forward",
    "back",
    "turn_left",
    "turn_right",
    "run_forward",
    "run_forward_left",
    "run_forward_right",
    "attack_up",
    "attack",
    "attack_down",
    "interact",
    "use",
    "equip",
    *(f"deposit_slot_{i}" for i in range(8)),
    *(f"withdraw_box_{i}" for i in range(16)),
    "combine",
    *(f"select_slot_{i}" for i in range(8)),
)


def _remap_action_head_tensor(
    old: Any,
    new: Any,
    old_names: tuple[str, ...],
    new_names: tuple[str, ...],
) -> Any:
    old_index = {name: index for index, name in enumerate(old_names)}
    remapped = new.clone()
    for new_index, name in enumerate(new_names):
        remapped[new_index] = old[old_index[name]]
    return remapped


def _copy_compatible_policy_weights(src_policy, dst_policy) -> int:
    """Copy compatible tensors, expanding a legacy action head safely.

    ``strict=False`` still errors on shape mismatches for shared keys; filter first.
    The canonical 45-action reorder migration follows action names across the
    reordered head. Other expansions retain the legacy attack-clone behavior.
    """
    src = src_policy.state_dict()
    dst = dst_policy.state_dict()
    filtered = {
        k: v for k, v in src.items()
        if k in dst and tuple(dst[k].shape) == tuple(v.shape)
    }
    goal_input_key = "features_extractor.goal_mlp.0.weight"
    if goal_input_key in src and goal_input_key in dst:
        old = src[goal_input_key]
        new = dst[goal_input_key]
        if (
            old.ndim == new.ndim == 2
            and old.shape[0] == new.shape[0]
            and old.shape[1] < new.shape[1]
        ):
            widened = new.clone()
            widened.zero_()
            widened[:, : old.shape[1]] = old
            filtered[goal_input_key] = widened
    for key in ("action_net.weight", "action_net.bias"):
        if key not in src or key not in dst:
            continue
        old = src[key]
        new = dst[key]
        if old.ndim != new.ndim:
            continue
        if old.ndim == 2 and old.shape[1:] != new.shape[1:]:
            continue

        remapped = None
        if old.shape[0] == new.shape[0] == 45 and (
            tuple(_LEGACY_45_ACTION_NAMES) != tuple(_CURRENT_45_ACTION_NAMES)
        ):
            remapped = _remap_action_head_tensor(
                old, new, _LEGACY_45_ACTION_NAMES, _CURRENT_45_ACTION_NAMES
            )
        elif old.shape[0] == 47 and new.shape[0] == 45:
            remapped = _remap_action_head_tensor(
                old, new, _LEGACY_47_ACTION_NAMES, _CURRENT_45_ACTION_NAMES
            )
        elif old.shape[0] < new.shape[0]:
            expanded = new.clone()
            expanded[: old.shape[0]] = old
            expanded[old.shape[0] :] = old[_LEGACY_GENERIC_ATTACK_ACTION]
            if old.ndim == 1:
                expanded[old.shape[0] :] -= float(np.log(100.0))
            remapped = expanded

        if remapped is not None:
            filtered[key] = remapped
    dst_policy.load_state_dict(filtered, strict=False)
    return len(filtered)


def _transplant_state_dict_with_input_pad(
    old_sd: dict[str, Any],
    new_sd: dict[str, Any],
) -> dict[str, list[str]]:
    """Copy matching tensors; zero-pad widened linear input columns in-place."""
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
        if (
            (key.endswith(".mlp.0.weight") or key.endswith(".goal_mlp.0.weight"))
            and old_t.ndim == 2
            and new_t.ndim == 2
            and old_t.shape[0] == new_t.shape[0]
            and old_t.shape[1] < new_t.shape[1]
        ):
            new_t.zero_()
            new_t[:, : old_t.shape[1]] = old_t
            report["remapped"].append(
                f"{key} pad_in {old_t.shape[1]}->{new_t.shape[1]}"
            )
            continue
        report["skipped"].append(f"{key} {tuple(old_t.shape)} -> {tuple(new_t.shape)}")
    return report


def transplant_combat_efficient_checkpoint(
    src_zip: Path,
    out_base: Path,
    *,
    device: str = "cpu",
):
    """Load donor zip, transplant into current CombatEfficientPPO, save survivor."""
    import io
    import json
    import zipfile

    import torch

    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.policy_config import POLICY_KWARGS

    LearnerCls, extra = _make_learner_cls()
    policy_obs, act_space = _policy_obs_and_act_spaces()
    hp = {
        **PPO_HYPERPARAMS,
        "verbose": 0,
        "device": device,
        "policy_kwargs": POLICY_KWARGS,
        **extra,
    }
    with zipfile.ZipFile(src_zip) as zf:
        old_sd = torch.load(io.BytesIO(zf.read("policy.pth")), map_location="cpu")
        meta = json.loads(zf.read("data"))
    donor_steps = int(meta.get("num_timesteps", 0) or 0)
    model = LearnerCls(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs, act_space),
        **hp,
    )
    new_sd = model.policy.state_dict()
    report = _transplant_state_dict_with_input_pad(old_sd, new_sd)
    model.policy.load_state_dict(new_sd, strict=False)
    _reload_world_catalog_buffers_if_needed(model)
    model.num_timesteps = donor_steps
    out_base.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out_base))
    out_zip = out_base if out_base.suffix == ".zip" else Path(str(out_base) + ".zip")
    return model, out_zip, report


def _make_learner_cls():
    from re1_rl.combat_ppo import CombatEfficientPPO
    from re1_rl.policy_config import AUX_COEF, USE_GROUPED_ENTROPY

    return CombatEfficientPPO, {
        "aux_coef": AUX_COEF,
        "use_grouped_entropy": USE_GROUPED_ENTROPY,
        "gae_lambda": 1.0,
    }


def _transplant_into_current_spaces(model, *, tb_log: str | None, hp: dict):
    from re1_rl.distributed.weights import _SpaceHolderEnv

    LearnerCls, extra = _make_learner_cls()
    policy_obs, act_space = _policy_obs_and_act_spaces()
    fresh = LearnerCls(
        "MultiInputPolicy",
        _SpaceHolderEnv(policy_obs, act_space),
        **{**hp, **extra},
    )
    n_copied = _copy_compatible_policy_weights(model.policy, fresh.policy)
    fresh.num_timesteps = int(model.num_timesteps)
    print(
        "[train:async] checkpoint obs/action layout mismatch; "
        f"transplanted {n_copied} compatible tensors into current architecture",
        flush=True,
    )
    return fresh


def _reload_world_catalog_buffers_if_needed(model) -> None:
    from re1_rl.combat_efficient_extractor import (
        RE1CombatEfficientExtractor,
        reload_combat_efficient_world_catalog_buffers,
    )
    from re1_rl.doc04_medium_extractor import (
        RE1Doc04MediumExtractor,
        reload_doc04_world_catalog_buffers,
    )
    from re1_rl.features_extractor import RE1WorldAwareExtractor, reload_world_catalog_buffers

    extractor = model.policy.features_extractor
    if isinstance(extractor, RE1CombatEfficientExtractor):
        reload_combat_efficient_world_catalog_buffers(model.policy)
        print("[train:async] reloaded world catalog buffers from data files", flush=True)
    elif isinstance(extractor, RE1Doc04MediumExtractor):
        reload_doc04_world_catalog_buffers(model.policy)
        print("[train:async] reloaded world catalog buffers from data files", flush=True)
    elif isinstance(extractor, RE1WorldAwareExtractor):
        reload_world_catalog_buffers(model.policy)
        print("[train:async] reloaded world catalog buffers from data files", flush=True)


def load_async_learner(*, device: str, resume: Path | None, tb_log: str | None):
    """CombatEfficientPPO learner shell; accepts PPO / MaskablePPO checkpoint zips."""
    from sb3_contrib import MaskablePPO
    from stable_baselines3 import PPO

    from re1_rl.distributed.weights import _SpaceHolderEnv
    from re1_rl.policy_config import POLICY_KWARGS

    LearnerCls, extra = _make_learner_cls()
    hp = {
        **PPO_HYPERPARAMS,
        "verbose": 1,
        "device": device,
        "policy_kwargs": POLICY_KWARGS,
        **extra,
    }
    if tb_log:
        hp["tensorboard_log"] = tb_log

    def _fresh_maskable(obs_space=None, act_space=None):
        policy_obs_space, default_act = _policy_obs_and_act_spaces()
        return LearnerCls(
            "MultiInputPolicy",
            _SpaceHolderEnv(
                obs_space if obs_space is not None else policy_obs_space,
                act_space if act_space is not None else default_act,
            ),
            **hp,
        )

    if resume is not None and resume.is_file():
        loaded = None
        load_kind = "CombatEfficientPPO"
        try:
            loaded = LearnerCls.load(str(resume), device=device)
            load_kind = "CombatEfficientPPO"
        except (TypeError, ValueError, RuntimeError):
            try:
                loaded = MaskablePPO.load(str(resume), device=device)
                load_kind = "MaskablePPO"
            except (TypeError, ValueError, RuntimeError):
                try:
                    plain = PPO.load(str(resume), device=device)
                    loaded = _fresh_maskable(plain.observation_space, plain.action_space)
                    _copy_compatible_policy_weights(plain.policy, loaded.policy)
                    loaded.num_timesteps = int(plain.num_timesteps)
                    load_kind = "PPO"
                except (TypeError, ValueError, RuntimeError):
                    try:
                        import io
                        import json
                        import zipfile

                        import torch

                        with zipfile.ZipFile(resume) as zf:
                            old_sd = torch.load(
                                io.BytesIO(zf.read("policy.pth")),
                                map_location="cpu",
                                weights_only=True,
                            )
                            meta = json.loads(zf.read("data"))
                        loaded = _fresh_maskable()
                        new_sd = loaded.policy.state_dict()
                        report = _transplant_state_dict_with_input_pad(old_sd, new_sd)
                        loaded.policy.load_state_dict(new_sd, strict=False)
                        loaded.num_timesteps = int(meta.get("num_timesteps", 0) or 0)
                        load_kind = (
                            "raw-policy transplant "
                            f"({len(report['copied'])} copied, "
                            f"{len(report['remapped'])} remapped)"
                        )
                    except (OSError, KeyError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
                        raise RuntimeError(
                            f"failed to load or transplant resume checkpoint {resume}"
                        ) from exc

        if tb_log:
            loaded.tensorboard_log = tb_log
        print(
            f"[train:async] resumed {load_kind} into CombatEfficientPPO from {resume} "
            f"(num_timesteps={loaded.num_timesteps})",
            flush=True,
        )
        if _checkpoint_spaces_compatible(loaded):
            missing_keys = _checkpoint_missing_policy_keys(resume, loaded)
            if missing_keys:
                print(
                    "[train:async] checkpoint policy layout mismatch; "
                    f"{len(missing_keys)} current tensors absent from donor; "
                    "using compatible-weight transplant",
                    flush=True,
                )
                loaded = _transplant_into_current_spaces(
                    loaded, tb_log=tb_log, hp=hp,
                )
        if not _checkpoint_spaces_compatible(loaded):
            loaded = _transplant_into_current_spaces(
                loaded, tb_log=tb_log, hp=hp,
            )
        _reload_world_catalog_buffers_if_needed(loaded)
        return loaded

    return _fresh_maskable()


def _actor_process(
    rank: int,
    conn: Connection,
    *,
    curriculum: str,
    base_port: int,
    training_speed: int,
    skip_chunk: int,
    n_steps: int,
    stop_flag: mp.synchronize.Synchronized,
    capture_checkpoints: bool,
    headless: bool = True,
    screenshot_mmf: bool | None = None,
    memlog_directory: str | None = None,
) -> None:
    from scripts.train_parallel import make_env
    from re1_rl.training_progress import slim_progress_info

    try:
        import cv2

        cv2.setNumThreads(1)
    except ImportError:
        pass

    try:
        env = make_env(
            rank,
            curriculum,
            base_port,
            capture_checkpoints,
            training_speed=training_speed,
            skip_chunk=skip_chunk,
            async_cutscene_skip=True,
            headless=headless,
            screenshot_mmf=screenshot_mmf,
            spawn_progress=lambda phase: conn.send(
                {"t": "spawn_progress", "rank": rank, "phase": phase}
            ),
        )()
    except Exception as exc:
        try:
            conn.send({"t": "spawn_error", "rank": rank, "error": repr(exc)})
        except (BrokenPipeError, EOFError, OSError):
            pass
        raise
    raw_env = getattr(env, "unwrapped", env)
    try:
        conn.send(
            {
                "t": "spawned",
                "rank": rank,
                "emuhawk_pid": getattr(raw_env, "_emuhawk_pid", None),
            }
        )
        msg = conn.recv()
    except (BrokenPipeError, EOFError, OSError):
        env.close()
        raise
    if msg.get("t") == "stop":
        env.close()
        return
    if msg.get("t") != "start":
        env.close()
        return

    # PbChampionResetWrapper (make_env) mixes champion vs fresh on reset.
    try:
        obs, _ = env.reset()
    except BaseException:
        env.close()
        raise

    obs_bufs: dict[str, np.ndarray] | None = None
    mask_bufs: np.ndarray | None = None
    mod_drop_bufs: np.ndarray | None = None
    actions = np.zeros(n_steps, dtype=np.int64)
    rewards = np.zeros(n_steps, dtype=np.float32)
    dones = np.zeros(n_steps, dtype=np.bool_)
    values = np.zeros(n_steps, dtype=np.float32)
    log_probs = np.zeros(n_steps, dtype=np.float32)
    episode_infos: list[dict[str, Any]] = []
    step_i = 0
    horizon_policy_version = 0
    memlog_control = None
    memlog_telemetry = None

    from re1_rl.modality_config import mod_drop_enabled

    use_mod_drop = mod_drop_enabled()
    mod_drop_state = None
    MOD_DROP_DIM = 0
    if use_mod_drop:
        from re1_rl.modality_ablations import MOD_DROP_DIM, ModDropEpisodeState

        mod_drop_state = ModDropEpisodeState(1)

    def _reset_bufs() -> None:
        nonlocal obs_bufs, mask_bufs, mod_drop_bufs, step_i, episode_infos, horizon_policy_version
        obs_bufs = {
            k: np.zeros((n_steps, *env.observation_space[k].shape), dtype=env.observation_space[k].dtype)
            for k in env.observation_space.spaces
        }
        n_actions = int(env.action_space.n)
        mask_bufs = np.zeros((n_steps, n_actions), dtype=np.bool_)
        mod_drop_bufs = (
            np.ones((n_steps, MOD_DROP_DIM), dtype=np.float32) if use_mod_drop else None
        )
        step_i = 0
        episode_infos = []
        horizon_policy_version = 0

    def _emit_rollout(n: int) -> None:
        assert obs_bufs is not None and mask_bufs is not None
        payload: dict[str, Any] = {
            "t": "rollout",
            "rank": rank,
            "n_steps": int(n),
            "obs": {k: v[:n].copy() for k, v in obs_bufs.items()},
            "actions": actions[:n].copy(),
            "rewards": rewards[:n].copy(),
            "dones": dones[:n].copy(),
            "values": values[:n].copy(),
            "log_probs": log_probs[:n].copy(),
            "action_masks": mask_bufs[:n].copy(),
            "policy_version": horizon_policy_version,
            "last_obs": obs,
            "episode_infos": episode_infos,
        }
        if mod_drop_bufs is not None:
            payload["mod_drop_masks"] = mod_drop_bufs[:n].copy()
        conn.send(payload)

    _reset_bufs()

    try:
        if memlog_directory is not None:
            from re1_rl.memlog_runtime import MemlogControl, MemlogTelemetry

            raw_env = getattr(env, "unwrapped", env)
            memlog_control = MemlogControl(
                Path(memlog_directory),
                bridge=raw_env.bridge,
                ram_skipper=raw_env._ram_skip,
                initial_speed=training_speed,
                rank=rank,
                run_id=os.environ.get("RE1_MEMLOG_RUN_ID"),
            )
            memlog_telemetry = MemlogTelemetry(
                Path(memlog_directory),
                run_id=memlog_control.state.run_id,
                rank=rank,
                n_steps=n_steps,
            )

        def _wait_for_control() -> bool:
            if memlog_control is None:
                return False
            state = memlog_control.wait_until_runnable(
                heartbeat=(
                    lambda current: memlog_telemetry.heartbeat(
                        current, horizon_step=step_i
                    )
                    if memlog_telemetry is not None
                    else None
                )
            )
            return bool(state.shutdown)

        while not stop_flag.value:
            if _wait_for_control():
                break
            req: dict[str, Any] = {"t": "need", "rank": rank, "obs": obs}
            masks_now = None
            if hasattr(env, "action_masks"):
                masks_now = np.asarray(env.action_masks(), dtype=bool)
                req["action_masks"] = masks_now
            if mod_drop_state is not None:
                # Chosen before action; fixed until episode done.
                req["mod_drop_mask"] = mod_drop_state.masks[0].copy()
            _diag_env = getattr(getattr(env, "unwrapped", env), "_step_diag", None)
            _footage_env = getattr(getattr(env, "unwrapped", env), "_footage_trace", None)
            if (
                memlog_telemetry is not None
                or _diag_env is not None
                or _footage_env is not None
            ):
                req["want_policy_diagnostics"] = True
            conn.send(req)
            msg = conn.recv()
            if msg.get("t") == "stop":
                break
            if msg.get("t") != "act":
                continue

            action = int(msg["action"])
            value = float(msg["value"])
            logprob = float(msg["logprob"])
            if step_i == 0:
                horizon_policy_version = int(msg.get("policy_version", 0) or 0)

            obs_before = obs
            masks_before = masks_now
            if _wait_for_control():
                break
            # Top-right memlog (RE1_STEP_DIAG_PORT): stash critic V + probs.
            try:
                _diag = getattr(getattr(env, "unwrapped", env), "_step_diag", None)
                if _diag is not None:
                    _diag.note_value(value)
                    if msg.get("masked_probs") is not None:
                        _diag.note_masked_probs(msg.get("masked_probs"))
            except (AttributeError, TypeError, ValueError):
                pass
            obs, rew, done, trunc, info = env.step(action)
            if _footage_env is not None:
                try:
                    _footage_env.append(
                        action=action,
                        action_mask=masks_before
                        if masks_before is not None
                        else np.ones(int(env.action_space.n), dtype=bool),
                        masked_probs=msg.get("masked_probs"),
                        policy_version=int(msg.get("policy_version", 0) or 0),
                        n_actions=int(env.action_space.n),
                    )
                except (TypeError, ValueError, AttributeError):
                    pass
            if memlog_telemetry is not None and memlog_control is not None:
                telemetry_mask = masks_before
                if telemetry_mask is None:
                    telemetry_mask = np.ones(int(env.action_space.n), dtype=bool)
                memlog_telemetry.publish_step(
                    obs=obs_before,
                    action_mask=telemetry_mask,
                    action=action,
                    value=value,
                    logprob=logprob,
                    policy_version=int(msg.get("policy_version", 0) or 0),
                    raw_logits=msg.get("raw_logits"),
                    masked_probs=msg.get("masked_probs"),
                    reward=float(rew),
                    info=info,
                    done=bool(done or trunc),
                    horizon_step=step_i,
                    control=memlog_control.state,
                )
                if (done or trunc) and memlog_telemetry.should_shutdown():
                    memlog_control.request_shutdown()
            if info:
                slim_src = info
                if done or trunc:
                    slim_src = {**info, "actor_rank": rank}
                episode_infos.append(slim_progress_info(slim_src))

            # Exclude pure cutscene-skip ticks from the PPO buffer (zero reward,
            # frozen obs). Post-skip credit lands on the next live control step.
            if info.get("cutscene_skip") and not (done or trunc):
                continue

            assert obs_bufs is not None and mask_bufs is not None
            for key in obs_bufs:
                obs_bufs[key][step_i] = obs_before[key]
            if masks_before is None:
                masks_before = np.ones(int(env.action_space.n), dtype=bool)
            mask_bufs[step_i] = masks_before
            if mod_drop_bufs is not None and mod_drop_state is not None:
                mod_drop_bufs[step_i] = mod_drop_state.masks[0]
            actions[step_i] = action
            values[step_i] = value
            log_probs[step_i] = logprob
            rewards[step_i] = float(rew)
            dones[step_i] = bool(done or trunc)
            step_i += 1

            if done or trunc:
                try:
                    from re1_rl.fight_eval_episodes import record_fight_eval_episode

                    record_fight_eval_episode(info or {}, rank=rank)
                except Exception:
                    pass
                if step_i > 0:
                    _emit_rollout(step_i)
                    _reset_bufs()
                if mod_drop_state is not None:
                    mod_drop_state.on_dones([True])
                if _wait_for_control():
                    break
                obs, _ = env.reset()
            elif step_i >= n_steps:
                _emit_rollout(n_steps)
                _reset_bufs()
    finally:
        if step_i > 0:
            try:
                _emit_rollout(step_i)
            except (BrokenPipeError, EOFError, OSError):
                pass
        try:
            env.close()
        except Exception:
            pass


def _wait_for_actor_spawn(
    conns: list[Connection],
    n_envs: int,
    *,
    processes: list[mp.Process] | None = None,
    actor_ranks: list[int] | None = None,
    timeout_s: float = 600.0,
) -> dict[int, int]:
    expected_ranks = (
        set(range(int(n_envs)))
        if actor_ranks is None
        else {int(rank) for rank in actor_ranks}
    )
    if len(expected_ranks) != int(n_envs):
        raise ValueError("n_envs must equal the number of unique actor ranks")
    ordered_ranks = (
        list(range(int(n_envs)))
        if actor_ranks is None
        else [int(rank) for rank in actor_ranks]
    )
    spawned: set[int] = set()
    errors: dict[int, str] = {}
    emuhawk_pids: dict[int, int] = {}
    deadline = time.perf_counter() + timeout_s
    last_report = 0.0
    while spawned != expected_ranks:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            missing = sorted(expected_ranks - spawned)
            err_lines = [f"  rank {r}: {errors[r]}" for r in sorted(errors)]
            detail = "\n".join(err_lines) if err_lines else ""
            raise TimeoutError(
                f"timed out waiting for actors {missing}"
                + (f"\n{detail}" if detail else "")
            )
        if processes and time.perf_counter() - last_report >= 10.0:
            alive = sum(1 for p in processes if p.is_alive())
            dead = [
                rank
                for rank, proc in zip(ordered_ranks, processes)
                if not proc.is_alive() and rank not in spawned
            ]
            print(
                f"[train:async] warmup {len(spawned)}/{n_envs} spawned, "
                f"{alive} actors alive"
                + (f", dead ranks {dead}" if dead else ""),
                flush=True,
            )
            last_report = time.perf_counter()
        ready = wait(conns, timeout=min(1.0, remaining))
        for conn in ready:
            try:
                if not conn.poll():
                    continue
                msg = conn.recv()
            except (BrokenPipeError, EOFError, OSError):
                continue
            if msg.get("t") == "spawned":
                rank = int(msg["rank"])
                spawned.add(rank)
                pid = msg.get("emuhawk_pid")
                if pid is not None:
                    emuhawk_pids[rank] = int(pid)
            elif msg.get("t") == "spawn_error":
                r = int(msg["rank"])
                errors[r] = str(msg.get("error", "unknown"))
                print(f"[train:async] actor {r} spawn failed: {errors[r]}", flush=True)
            elif msg.get("t") == "spawn_progress":
                print(
                    f"[train:async] actor {int(msg['rank'])}: {msg.get('phase', '')}",
                    flush=True,
                )
        if processes:
            for rank, proc in zip(ordered_ranks, processes):
                if rank in spawned or proc.is_alive():
                    continue
                proc.join(timeout=0)
                detail = errors.get(rank)
                raise RuntimeError(
                    f"actor {rank} died during warmup (exit={proc.exitcode})"
                    + (f": {detail}" if detail else "")
                )
    print(f"[train:async] all {n_envs} actors connected", flush=True)
    return emuhawk_pids


def run_async_fleet_training(
    *,
    n_envs: int,
    train_steps: int,
    curriculum: str,
    base_port: int,
    training_speed: int,
    skip_chunk: int,
    capture_checkpoints: bool,
    resume_path: Path | None,
    ckpt_dir: Path,
    run_name: str | None,
    device: str,
    tb_log: str,
    headless: bool = True,
    screenshot_mmf: bool | None = None,
    inference_batch_max: int = 32,
) -> int:
    from re1_rl.checkpoint_io import (
        atomic_model_save,
        checkpoint_timestep_interval,
        write_latest_pointer,
    )
    from re1_rl.distributed.inference_policy import InferencePolicy
    from re1_rl.distributed.learner_train import train_on_rollouts
    from re1_rl.distributed.rollout_types import WorkerRollout
    from re1_rl.distributed.weights import export_policy_state_dict
    from re1_rl.training_metrics_log import (
        append_training_record,
        build_update_record,
        configure_training_logger,
        log_update_line,
        rollout_batch_reward_stats,
        training_metrics_jsonl_path,
    )
    from re1_rl.training_progress import TrainingProgressTracker

    n_steps = int(PPO_HYPERPARAMS["n_steps"])
    batch_threshold = n_steps * n_envs
    save_interval = checkpoint_timestep_interval(n_envs)
    model = load_async_learner(device=device, resume=resume_path, tb_log=tb_log)
    next_save = (model.num_timesteps // save_interval + 1) * save_interval

    tb_run_dir = Path(tb_log) / (run_name or "async")
    configure_training_logger(model, log_dir=tb_run_dir)
    metrics_jsonl = training_metrics_jsonl_path(PROJECT_ROOT, run_name=run_name)
    print(f"[train:async] metrics jsonl -> {metrics_jsonl}", flush=True)
    print(f"[train:async] tensorboard/csv -> {tb_run_dir}", flush=True)

    policy = InferencePolicy(model.observation_space, model.action_space, device)
    policy_version = 1
    policy.load_from_state_dict(export_policy_state_dict(model), policy_version)

    print(
        f"[train:async] {n_envs} desync actors, target={train_steps} steps, "
        f"batch_threshold={batch_threshold}, "
        f"checkpoint_every={save_interval} steps, headless={headless}, "
        f"screenshot_mmf={screenshot_mmf}, inference_batch_max={inference_batch_max}",
        flush=True,
    )

    stop_flag = mp.Value("b", False)
    ctx = mp.get_context("spawn")
    processes: list[mp.Process] = []
    parent_conns: list[Connection] = []

    warmup_t0 = time.perf_counter()
    print("[train:async] warming up fleet (spawn + BizHawk connect)...", flush=True)
    for rank in range(n_envs):
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=_actor_process,
            args=(rank, child_conn),
            kwargs={
                "curriculum": curriculum,
                "base_port": base_port,
                "training_speed": training_speed,
                "skip_chunk": skip_chunk,
                "n_steps": n_steps,
                "stop_flag": stop_flag,
                "capture_checkpoints": capture_checkpoints,
                "headless": headless,
                "screenshot_mmf": screenshot_mmf,
            },
            name=f"async-actor-{rank}",
        )
        proc.start()
        child_conn.close()
        processes.append(proc)
        parent_conns.append(parent_conn)

    _wait_for_actor_spawn(parent_conns, n_envs, processes=processes)
    print(f"[train:async] fleet ready in {time.perf_counter() - warmup_t0:.1f}s", flush=True)
    for conn in parent_conns:
        conn.send({"t": "start"})

    pending: list[WorkerRollout] = []
    pending_steps = 0
    n_updates = 0
    t0 = time.perf_counter()
    progress = TrainingProgressTracker(prefix="progress")

    try:
        while model.num_timesteps < train_steps and not stop_flag.value:
            ready = wait(parent_conns, timeout=1.0)
            if not ready:
                if not any(p.is_alive() for p in processes):
                    break
                continue

            needs, rollouts, _failed = _drain_actor_messages(
                ready,
                parent_conns,
                max_need_batch=inference_batch_max,
            )
            if needs:
                _serve_needs_batch(needs, policy, max_batch=inference_batch_max)
            for conn, msg in rollouts:
                rank = int(msg["rank"])
                last_values = policy.predict_values(_obs_batch_for_one(msg["last_obs"]))
                obs = {k: np.expand_dims(v, axis=1) for k, v in msg["obs"].items()}
                from re1_rl.distributed.rollout_types import normalize_curriculum_id
                from re1_rl.distributed.spaces import OBS_SCHEMA_VERSION

                mod_drop = msg.get("mod_drop_masks")
                mod_drop_masks = (
                    None
                    if mod_drop is None
                    else np.expand_dims(np.asarray(mod_drop, dtype=np.float32), 1)
                )
                pending.append(
                    WorkerRollout(
                        worker_id=f"actor_{rank}",
                        policy_version=int(msg.get("policy_version", policy_version)),
                        n_envs=1,
                        n_steps=n_steps,
                        obs=obs,
                        actions=np.expand_dims(msg["actions"], 1),
                        rewards=np.expand_dims(msg["rewards"], 1),
                        dones=np.expand_dims(msg["dones"], 1),
                        values=np.expand_dims(msg["values"], 1),
                        log_probs=np.expand_dims(msg["log_probs"], 1),
                        last_values=last_values,
                        action_masks=np.expand_dims(
                            np.asarray(msg["action_masks"], dtype=np.bool_), 1
                        ),
                        episode_infos=list(msg.get("episode_infos") or []),
                        mod_drop_masks=mod_drop_masks,
                        curriculum_id=normalize_curriculum_id(curriculum),
                        obs_schema_version=int(OBS_SCHEMA_VERSION),
                    )
                )
                pending_steps += n_steps

            if pending_steps < batch_threshold:
                continue

            batch_rollouts = list(pending)
            batch_infos: list[dict[str, Any]] = []
            for rollout in batch_rollouts:
                batch_infos.extend(rollout.episode_infos)
            train_on_rollouts(model, batch_rollouts)
            progress.consume_infos(batch_infos, num_timesteps=int(model.num_timesteps))
            progress.log_rollout_end(
                model,
                num_timesteps=int(model.num_timesteps),
                episode_infos=batch_infos,
            )
            try:
                from re1_rl.yawn_rails_plr import observe_episode_infos, plr_enabled_from_env
                from re1_rl.yawn_rails_eval import maybe_log_equal_weight_from_infos

                if plr_enabled_from_env():
                    observe_episode_infos(PROJECT_ROOT, batch_infos)
                maybe_log_equal_weight_from_infos(
                    PROJECT_ROOT,
                    batch_infos,
                    update=n_updates + 1,
                    policy_version=policy_version,
                    num_timesteps=int(model.num_timesteps),
                    model=model,
                    run_name=run_name,
                )
            except Exception as exc:
                print(f"[train:async] yawn rails eval/plr side-job skipped: {exc}", flush=True)
            policy_version += 1
            policy.load_from_state_dict(export_policy_state_dict(model), policy_version)
            n_updates += 1
            pending.clear()
            pending_steps = 0

            elapsed = time.perf_counter() - t0
            rate = model.num_timesteps / elapsed if elapsed > 0 else 0.0
            record = build_update_record(
                model,
                update=n_updates,
                policy_version=policy_version,
                rate_steps_s=rate,
                extra={
                    "n_envs": n_envs,
                    "n_rollouts": len(batch_rollouts),
                    **rollout_batch_reward_stats(batch_rollouts),
                },
            )
            append_training_record(metrics_jsonl, record)
            log_update_line(record)

            while model.num_timesteps >= next_save:
                ckpt_path = ckpt_dir / f"ppo_re1_{next_save}_steps.zip"
                saved = atomic_model_save(model, ckpt_path)
                write_latest_pointer(ckpt_dir, saved)
                print(f"[train:async] checkpoint {saved}", flush=True)
                next_save += save_interval

    except KeyboardInterrupt:
        print("[train:async] interrupted", flush=True)
    finally:
        stop_flag.value = True
        for conn in parent_conns:
            try:
                conn.send({"t": "stop"})
            except (BrokenPipeError, OSError):
                pass
            conn.close()
        for proc in processes:
            proc.join(timeout=30)
            if proc.is_alive():
                proc.terminate()

        suffix = f"_{run_name}" if run_name else ""
        final_alias = PROJECT_ROOT / "data" / f"ppo_re1_final{suffix}.zip"
        try:
            from re1_rl.checkpoint_io import zip_path

            saved = atomic_model_save(model, zip_path(final_alias))
            write_latest_pointer(ckpt_dir, saved)
            print(f"[train:async] saved {saved}", flush=True)
        except OSError as exc:
            print(f"[train:async] WARNING: final save failed: {exc}", flush=True)

    return int(model.num_timesteps)
