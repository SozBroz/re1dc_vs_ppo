"""Planner-march NN reset: restart each leg from the base weights.

The march hunts one PL at a time with 100% starts, which specializes the live
policy on that hunt. After every pin advance the march POSTs
``/march/reset_weights``; the learner train loop calls
:func:`apply_march_reset` each spin, and on a pending request loads the base
checkpoint (the pre-march weights that could run ~99% of the stretches) into
the live model *in place* — same object, so callbacks and the weight store
keep working — then publishes a new policy version so the whole fleet pulls
the base weights on its next sync epoch.

In-place means: policy state dict swapped strict, optimizer state restored
best-effort, ``num_timesteps`` keeps counting (checkpoint filenames keep
increasing). Stale in-flight rollouts from the specialized policy must be
dropped by the caller (see ``run_learner_loop``): the relevance gate would
mostly reject them, but training base weights on them for even one batch is
pure noise.
"""

from __future__ import annotations

import gc
import os
import time
from pathlib import Path
from typing import Any

from re1_rl.distributed.log_util import log
from re1_rl.distributed.weights import export_policy_state_dict, load_policy_weights

_BASE_CKPT_ENV = "RE1_PLANNER_MARCH_BASE_CKPT"
_BASE_CKPT_NAME = "planner_march_base_weights.zip"
_LEGACY_BASE_NAME = "ppo_re1_652993821_steps.zip"
_CKPT_SUBDIR = Path("data/checkpoints/planner_loyal_shield_key")
_RETRY_S = 60.0


def _project_root() -> Path:
    root = os.environ.get("RE1_RL_ROOT", "").strip()
    if root:
        return Path(root)
    return Path.cwd()


def resolve_base_ckpt() -> Path | None:
    """Base NN weights for march resets (explicit env > protected copy > legacy)."""
    explicit = os.environ.get(_BASE_CKPT_ENV, "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    base = _project_root() / _CKPT_SUBDIR
    for name in (_BASE_CKPT_NAME, _LEGACY_BASE_NAME):
        path = base / name
        if path.is_file():
            return path
    return None


def apply_march_reset(
    *,
    model: Any,
    weight_store: Any,
    learner_state: Any,
    machine_name: str = "",
    loader: Any = None,
) -> bool:
    """Apply one pending march reset. True when the live model was swapped."""
    req = getattr(learner_state, "pending_march_reset", None)
    if not isinstance(req, dict):
        return False
    advance_id = str(req.get("advance_id") or "")
    if not advance_id:
        learner_state.pending_march_reset = None
        return False
    now = time.time()
    try:
        last_try = float(getattr(learner_state, "march_reset_last_attempt_unix", 0.0) or 0.0)
    except (TypeError, ValueError):
        last_try = 0.0
    if now - last_try < _RETRY_S:
        return False
    learner_state.march_reset_last_attempt_unix = now
    ckpt = resolve_base_ckpt()
    if ckpt is None:
        log(machine_name, "[march_reset] no base checkpoint found; reset stays pending")
        return False
    if loader is None:
        from re1_rl.async_fleet import load_async_learner

        loader = load_async_learner
    try:
        device = str(getattr(model, "device", "cpu") or "cpu")
        fresh = loader(device=device, resume=ckpt, tb_log=None)
        try:
            load_policy_weights(model, export_policy_state_dict(fresh))
            try:
                model.policy.optimizer.load_state_dict(
                    fresh.policy.optimizer.state_dict()
                )
            except (AttributeError, TypeError, ValueError) as exc:
                log(machine_name, f"[march_reset] optimizer restore skipped: {exc}")
        finally:
            try:
                del fresh
            except NameError:
                pass
            gc.collect()
    except Exception as exc:
        log(machine_name, f"[march_reset] base reload failed ({ckpt.name}): {exc}")
        return False
    version = weight_store.publish(export_policy_state_dict(model))
    learner_state.set_current_version(version)
    learner_state.march_reset_applied = {
        "last_applied_advance_id": advance_id,
        "last_applied_unix": int(now),
        "base_ckpt": ckpt.name,
    }
    learner_state.pending_march_reset = None
    log(
        machine_name,
        f"[march_reset] {advance_id} applied from {ckpt.name} "
        f"-> policy_version={version} (fleet reverts on next sync)",
    )
    return True
