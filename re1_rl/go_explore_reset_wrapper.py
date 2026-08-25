"""Gym wrapper: mix fresh / PB / Go-Explore archive resets (local cache only)."""

from __future__ import annotations

import math
import os
import random
import json
from pathlib import Path
from typing import Any

import gymnasium as gym

from re1_rl.go_explore_worker_cache import (
    load_local_manifest,
    manifest_client_from_env,
    resolve_archive_bundle_for_reset,
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

_CURRICULUM_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}


def _load_curriculum_cached(path: Path) -> dict[str, Any]:
    key = str(path)
    stat = path.stat()
    mtime_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)))
    size = int(stat.st_size)
    cached = _CURRICULUM_CACHE.get(key)
    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
        return cached[2]
    stage = json.loads(path.read_text(encoding="utf-8"))
    _CURRICULUM_CACHE[key] = (mtime_ns, size, stage)
    return stage


def _pb_weight_from_env(default: float = 0.5) -> float:
    """Share of non-archive resets that use a PB sidecar (default 0.5 → 30/30 with archive=0.4)."""
    raw = os.environ.get("RE1_PB_FRESH_WEIGHT", "").strip()
    if not raw:
        return float(default)
    try:
        # RE1_PB_FRESH_WEIGHT is P(fresh|not archive); pb share is the complement.
        fresh = max(0.0, min(1.0, float(raw)))
        return 1.0 - fresh
    except ValueError:
        return float(default)


def _cell_quality_score(row: dict[str, Any]) -> float:
    q = row.get("quality")
    if not isinstance(q, (list, tuple)) or len(q) < 5:
        return 0.0
    # Prefer ammo + healing + free slots heavily; HP secondary.
    return (
        float(q[1]) * 2.0
        + float(q[2]) * 8.0
        + float(q[3]) * 4.0
        + float(q[4]) * 3.0
        + float(q[0]) * 0.05
    )


class GoExploreResetWrapper(gym.Wrapper):
    """Like ``PbChampionResetWrapper``, plus archive resets from local cache.

    Archive sampling uses ``local_manifest.json`` + ``cells/<record_id>/`` under
    ``go_explore_root``. Missing local bundles fall back to PB/fresh — never SMB.

    Default fleet mix (no focus room): 30% fresh / 30% any PB sidecar / 40% archive
    via ``RE1_GO_EXPLORE_RESET_WEIGHT=0.40`` and ``RE1_PB_FRESH_WEIGHT=0.50``.
    When ``RE1_RESET_FOCUS_ROOM`` is set, the 4-way mix still applies.
    """

    def __init__(
        self,
        env: gym.Env,
        project_root: Path | str | None = None,
        *,
        go_explore_root: Path | str | None = None,
        archive_weight: float | None = None,
        pb_weight: float | None = None,
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
        self._pb_weight = (
            float(pb_weight) if pb_weight is not None else _pb_weight_from_env(0.5)
        )
        self._rng = rng or random.Random()
        self._reset_mix = reset_mix_from_env()
        self._focus_room = focus_room_from_env()
        self._manifest_client = manifest_client_from_env()

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        opts = dict(options or {})
        base = getattr(self.env, "unwrapped", self.env)
        curriculum_path = Path(getattr(base, "curriculum_path", ""))
        if curriculum_path.is_file():
            try:
                stage = _load_curriculum_cached(curriculum_path)
            except (OSError, ValueError):
                stage = {}
            if stage.get("mode") == "yawn_rails":
                from re1_rl.planner_loyal import planner_loyal_enabled

                # Planner-loyal samples its own tip/frontier cells in env.reset.
                # Do not inject yawn pin route_start_index (e.g. cp120 → 121).
                if planner_loyal_enabled():
                    return self.env.reset(seed=seed, options=opts or None)
                if (
                    "pb_bundle" not in opts
                    and "pb_state_path" not in opts
                    and "route_start_index" not in opts
                ):
                    from re1_rl.yawn_rails import sample_one_leg_options

                    chooser = random.Random(seed) if seed is not None else self._rng
                    opts.update(
                        sample_one_leg_options(
                            self._pb_project_root, stage, rng=chooser
                        )
                    )
                return self.env.reset(seed=seed, options=opts or None)
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
        """Sample an archive cell, preferring resource-rich and under-covered rooms."""
        manifest = load_local_manifest(self._go_explore_root)
        cells = [c for c in (manifest.get("cells") or []) if isinstance(c, dict)]
        if not cells:
            return None

        # Skip known probe / stub rows (tiny or named probe ids).
        usable: list[dict[str, Any]] = []
        for row in cells:
            rid = str(row.get("record_id") or "")
            if rid in {"biglive", "probe_live_001"} or rid.startswith("probe_"):
                continue
            nbytes = int(row.get("bytes") or 0)
            if 0 < nbytes < 50_000:
                continue
            usable.append(row)
        if not usable:
            usable = list(cells)

        room_counts: dict[str, int] = {}
        for row in usable:
            room = str(row.get("room_id") or "").strip().upper()
            room_counts[room] = room_counts.get(room, 0) + 1

        # 80% weighted by quality / sparse-room; 20% uniform explore.
        if self._rng.random() < 0.20:
            order = list(usable)
            self._rng.shuffle(order)
        else:
            weights: list[float] = []
            for row in usable:
                room = str(row.get("room_id") or "").strip().upper()
                sparse = 1.0 / math.sqrt(max(1, room_counts.get(room, 1)))
                q = max(0.1, _cell_quality_score(row))
                weights.append(sparse * q)
            # Weighted shuffle without replacement (sample then remove).
            order = []
            pool = list(usable)
            wpool = list(weights)
            while pool:
                total = sum(wpool)
                if total <= 0:
                    order.extend(pool)
                    break
                u = self._rng.random() * total
                acc = 0.0
                pick = 0
                for i, w in enumerate(wpool):
                    acc += w
                    if u < acc:
                        pick = i
                        break
                order.append(pool.pop(pick))
                wpool.pop(pick)

        for row in order:
            bundle = resolve_archive_bundle_for_reset(
                self._go_explore_root,
                row,
                client=self._manifest_client,
            )
            if bundle is not None:
                return bundle
        return None

    def action_masks(self):
        fn = getattr(self.env, "action_masks", None)
        if callable(fn):
            return fn()
        return self.unwrapped.action_masks()
