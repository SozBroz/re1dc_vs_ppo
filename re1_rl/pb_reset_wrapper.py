"""Gym wrapper: mix champion PB resets into automatic VecEnv / fleet resets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import gymnasium as gym


class PbChampionResetWrapper(gym.Wrapper):
    """When ``reset`` has no ``options.pb_bundle``, sample champion vs fresh.

    Must wrap the live env stack used by actors (outermost or above Monitor).
    If no champion exists on disk, resets stay fresh — there is nothing to apply.
    Never delete ``states/pb/champions/...`` on code deploys; that disables sidecars.
    """

    def __init__(self, env: gym.Env, project_root: Path | str | None = None) -> None:
        super().__init__(env)
        root = project_root
        if root is None:
            root = getattr(env, "project_root", None) or getattr(
                getattr(env, "unwrapped", env), "project_root", Path.cwd()
            )
        self._pb_project_root = Path(root)

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        opts = dict(options or {})
        if "pb_bundle" not in opts and "pb_state_path" not in opts:
            from re1_rl.pb_curriculum import sample_champion_or_fresh

            bundle = sample_champion_or_fresh(self._pb_project_root)
            if bundle is not None:
                opts["pb_bundle"] = bundle
        return self.env.reset(seed=seed, options=opts or None)

    def action_masks(self):
        # Forward through ActionMasker / env when this wrapper is outermost.
        fn = getattr(self.env, "action_masks", None)
        if callable(fn):
            return fn()
        return self.unwrapped.action_masks()
