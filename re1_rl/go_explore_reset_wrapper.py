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
from re1_rl.reset_curriculum import (
    ResetMixSource,
    archive_weight_from_env,
    focus_room_from_env,
    reset_mix_from_env,
    sample_reset_mix,
    sample_reset_source,
)


class GoExploreResetWrapper(gym.Wrapper):
    """Like ``PbChampionResetWrapper``, plus archive resets from local cache.

    Archive sampling uses ``local_manifest.json`` + ``cells/<record_id>/`` under
    ``go_explore_root``. Missing local bundles fall back to PB/fresh — never SMB.

    When ``RE1_RESET_FOCUS_ROOM`` is set (default mix 30/30/30/10), sampling uses
    fresh | focus-room PB | other PB | archive. Otherwise the legacy 3-way mix
    (``RE1_GO_EXPLORE_RESET_WEIGHT`` + ``pb_weight``) applies.
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
        self._reset_mix = reset_mix_from_env()
        self._focus_room = focus_room_from_env()

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        opts = dict(options or {})
        if "pb_bundle" not in opts and "pb_state_path" not in opts:
            if self._reset_mix is not None and self._focus_room:
                self._apply_focus_mix(opts)
            else:
                self._apply_legacy_mix(opts)
        return self.env.reset(seed=seed, options=opts or None)

    def _apply_legacy_mix(self, opts: dict[str, Any]) -> None:
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
                source = "pb" if self._rng.random() < self._pb_weight else "fresh"
                opts["reset_source"] = source
                if source == "pb":
                    self._inject_training_pb(opts)
        elif source == "pb":
            self._inject_training_pb(opts)

    def _apply_focus_mix(self, opts: dict[str, Any]) -> None:
        from re1_rl.pb_curriculum import (
            champion_for_room,
            sample_focus_room_start,
            sample_other_champion_start,
        )

        assert self._reset_mix is not None
        focus_room = self._focus_room
        focus_pb_available = champion_for_room(self._pb_project_root, focus_room) is not None
        other_pb_available = (
            sample_other_champion_start(
                self._pb_project_root,
                exclude_room_id=focus_room,
                rng=self._rng,
            )
            is not None
        )
        archive_available = self._archive_cells_available()
        source = sample_reset_mix(
            self._rng,
            self._reset_mix,
            focus_pb_available=focus_pb_available,
            other_pb_available=other_pb_available,
            archive_available=archive_available,
        )
        opts["reset_source"] = source
        opts["reset_focus_room"] = focus_room
        bundle = self._resolve_focus_mix_bundle(source, focus_room)
        if bundle is not None:
            opts["pb_bundle"] = bundle
        elif source != "fresh":
            opts["reset_source"] = "fresh"

    def _resolve_focus_mix_bundle(
        self,
        source: ResetMixSource,
        focus_room: str,
    ) -> dict[str, Any] | None:
        from re1_rl.pb_curriculum import (
            sample_focus_room_start,
            sample_other_champion_start,
        )

        if source == "fresh":
            return None
        if source == "focus_pb":
            return sample_focus_room_start(
                self._pb_project_root,
                focus_room,
                rng=self._rng,
            )
        if source == "other_pb":
            return sample_other_champion_start(
                self._pb_project_root,
                exclude_room_id=focus_room,
                rng=self._rng,
            )
        if source == "archive":
            return self._sample_archive_bundle()
        return None

    def _inject_training_pb(self, opts: dict[str, Any]) -> None:
        from re1_rl.pb_curriculum import sample_training_start

        bundle = sample_training_start(self._pb_project_root, rng=self._rng)
        if bundle is not None:
            opts["pb_bundle"] = bundle

    def _archive_cells_available(self) -> bool:
        manifest = load_local_manifest(self._go_explore_root)
        cells = [c for c in (manifest.get("cells") or []) if isinstance(c, dict)]
        if not cells:
            return False
        for row in cells:
            rid = str(row.get("record_id") or "")
            if rid and resolve_local_bundle(self._go_explore_root, rid) is not None:
                return True
        return False

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
