"""Gym wrapper: mix fresh / PB / Go-Explore archive resets (local cache only)."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import gymnasium as gym

from re1_rl.go_explore_worker_cache import (
    load_local_manifest,
    resolve_local_bundle,
)
from re1_rl.reset_curriculum import archive_weight_from_env, sample_reset_source


class GoExploreResetWrapper(gym.Wrapper):
    """Like ``PbChampionResetWrapper``, plus archive resets from local cache.

    Archive sampling uses ``local_manifest.json`` + ``cells/<record_id>/`` under
    ``go_explore_root``. Missing local bundles fall back to PB/fresh — never SMB.
    """

    def __init__(
        self,
        env: gym.Env,
        project_root: Path | str | None = None,
        *,
        go_explore_root: Path | str | None = None,
        archive_weight: float | None = None,
        pb_weight: float = 0.5,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(env)
        root = project_root
        if root is None:
            root = getattr(env, "project_root", None) or getattr(
                getattr(env, "unwrapped", env), "project_root", Path.cwd()
            )
        self._pb_project_root = Path(root)
        if go_explore_root is not None:
            self._go_explore_root = Path(go_explore_root)
        else:
            self._go_explore_root = self._pb_project_root / "data" / "go_explore"
        self._archive_weight = (
            float(archive_weight)
            if archive_weight is not None
            else archive_weight_from_env(0.0)
        )
        self._pb_weight = float(pb_weight)
        self._rng = rng or random.Random()

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        opts = dict(options or {})
        if "pb_bundle" not in opts and "pb_state_path" not in opts:
            source = sample_reset_source(
                self._rng,
                archive_weight=self._archive_weight,
                pb_weight=self._pb_weight,
            )
            opts["reset_source"] = source
            if source == "archive":
                bundle = self._sample_archive_bundle()
                if bundle is not None:
                    opts["pb_bundle"] = bundle
                else:
                    # Missing local cell → fall through to PB / fresh.
                    source = "pb" if self._rng.random() < self._pb_weight else "fresh"
                    opts["reset_source"] = source
                    if source == "pb":
                        self._inject_pb(opts)
            elif source == "pb":
                self._inject_pb(opts)
        return self.env.reset(seed=seed, options=opts or None)

    def _inject_pb(self, opts: dict[str, Any]) -> None:
        from re1_rl.pb_curriculum import sample_training_start

        bundle = sample_training_start(self._pb_project_root, rng=self._rng)
        if bundle is not None:
            opts["pb_bundle"] = bundle

    def _sample_archive_bundle(self) -> dict[str, Any] | None:
        manifest = load_local_manifest(self._go_explore_root)
        cells = [c for c in (manifest.get("cells") or []) if isinstance(c, dict)]
        if not cells:
            return None
        row = self._rng.choice(cells)
        rid = str(row.get("record_id") or "")
        if not rid:
            return None
        resolved = resolve_local_bundle(self._go_explore_root, rid)
        if resolved is None:
            return None
        out = dict(resolved)
        if row.get("cell_key"):
            out["milestone_id"] = str(row["cell_key"])
        return out

    def action_masks(self):
        fn = getattr(self.env, "action_masks", None)
        if callable(fn):
            return fn()
        return self.unwrapped.action_masks()
