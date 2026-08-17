"""PLR-style fresh-rollout sampler for Yawn rails atomic cells.

Levels are ``(checkpoint cell, segment span, reset variant)``. Sampling is for
**new** env resets under the current policy — not Go-Explore archive replay and
not PPO batch replay.

When enabled (``RE1_YAWN_PLR=1``), each endpoint widens ``max_legs`` independently
along ``1 → 2 → 3 → 4 → 6`` after sustained success. A uniform atomic-cell floor
keeps mature cells from being forgotten.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from re1_rl.yawn_rails_sync import yawn_cell_pb_bundle

LEG_LADDER: tuple[int, ...] = (1, 2, 3, 4, 6)
DEFAULT_UNIFORM_FLOOR = 0.25
DEFAULT_WIDEN_SUCCESS = 0.80
DEFAULT_WIDEN_MIN_EPISODES = 20
_PLR_ENV = "RE1_YAWN_PLR"
_FLOOR_ENV = "RE1_YAWN_PLR_UNIFORM_FLOOR"
_STATE_ENV = "RE1_YAWN_PLR_STATE"
_WIDEN_SUCCESS_ENV = "RE1_YAWN_PLR_WIDEN_SUCCESS"
_WIDEN_MIN_ENV = "RE1_YAWN_PLR_WIDEN_MIN_EPISODES"


def plr_enabled_from_env(default: bool = False) -> bool:
    raw = os.environ.get(_PLR_ENV, "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def uniform_floor_from_env(default: float = DEFAULT_UNIFORM_FLOOR) -> float:
    raw = os.environ.get(_FLOOR_ENV, "").strip()
    if not raw:
        return float(default)
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return float(default)


def widen_success_from_env(default: float = DEFAULT_WIDEN_SUCCESS) -> float:
    raw = os.environ.get(_WIDEN_SUCCESS_ENV, "").strip()
    if not raw:
        return float(default)
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return float(default)


def widen_min_episodes_from_env(default: int = DEFAULT_WIDEN_MIN_EPISODES) -> int:
    raw = os.environ.get(_WIDEN_MIN_ENV, "").strip()
    if not raw:
        return int(default)
    try:
        return max(1, int(raw))
    except ValueError:
        return int(default)


def next_leg_width(current: int) -> int | None:
    """Return the next ladder rung above ``current``, or None if already maxed."""
    cur = max(1, int(current))
    for rung in LEG_LADDER:
        if rung > cur:
            return int(rung)
    return None


def clamp_leg_span(requested: int, *, remaining: int, endpoint_max: int) -> int:
    """Bound leg span by remaining route steps and per-endpoint max."""
    return max(1, min(int(requested), max(1, int(remaining)), max(1, int(endpoint_max))))


def level_key(
    checkpoint_index: int,
    leg_span: int,
    reset_variant: str = "route_cell",
) -> str:
    return f"{int(checkpoint_index)}:{int(leg_span)}:{reset_variant}"


def parse_level_key(key: str) -> tuple[int, int, str]:
    parts = str(key).split(":")
    if len(parts) != 3:
        raise ValueError(f"invalid PLR level key: {key!r}")
    return int(parts[0]), int(parts[1]), str(parts[2])


@dataclass
class PlrLevelStats:
    score: float = 1.0
    visits: int = 0
    successes: int = 0
    last_seen_update: int = 0
    recent_success: list[int] = field(default_factory=list)


@dataclass
class YawnRailsPlrState:
    """In-memory + JSON-backed PLR bookkeeping (learner / local training)."""

    route_id: str = "yawn_quest_v2"
    update_count: int = 0
    levels: dict[str, PlrLevelStats] = field(default_factory=dict)
    endpoint_max_legs: dict[int, int] = field(default_factory=dict)
    # Held-out eval success by atomic cell index (-1 = route_initial).
    eval_success: dict[int, float] = field(default_factory=dict)
    eval_updated_unix: float = 0.0

    def max_legs_for(self, checkpoint_index: int) -> int:
        return max(1, int(self.endpoint_max_legs.get(int(checkpoint_index), 1)))

    def ensure_level(
        self,
        checkpoint_index: int,
        leg_span: int,
        reset_variant: str,
    ) -> PlrLevelStats:
        key = level_key(checkpoint_index, leg_span, reset_variant)
        stats = self.levels.get(key)
        if stats is None:
            stats = PlrLevelStats()
            self.levels[key] = stats
        return stats

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "route_id": self.route_id,
            "update_count": int(self.update_count),
            "endpoint_max_legs": {
                str(k): int(v) for k, v in sorted(self.endpoint_max_legs.items())
            },
            "eval_success": {
                str(k): float(v) for k, v in sorted(self.eval_success.items())
            },
            "eval_updated_unix": float(self.eval_updated_unix),
            "levels": {
                key: {
                    "score": float(stats.score),
                    "visits": int(stats.visits),
                    "successes": int(stats.successes),
                    "last_seen_update": int(stats.last_seen_update),
                    "recent_success": list(stats.recent_success[-64:]),
                }
                for key, stats in sorted(self.levels.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> YawnRailsPlrState:
        state = cls(route_id=str(data.get("route_id") or "yawn_quest_v2"))
        state.update_count = int(data.get("update_count") or 0)
        for raw_k, raw_v in (data.get("endpoint_max_legs") or {}).items():
            state.endpoint_max_legs[int(raw_k)] = max(1, int(raw_v))
        for raw_k, raw_v in (data.get("eval_success") or {}).items():
            state.eval_success[int(raw_k)] = float(raw_v)
        state.eval_updated_unix = float(data.get("eval_updated_unix") or 0.0)
        for key, row in (data.get("levels") or {}).items():
            if not isinstance(row, dict):
                continue
            state.levels[str(key)] = PlrLevelStats(
                score=float(row.get("score", 1.0)),
                visits=int(row.get("visits", 0)),
                successes=int(row.get("successes", 0)),
                last_seen_update=int(row.get("last_seen_update", 0)),
                recent_success=[int(x) for x in (row.get("recent_success") or [])],
            )
        return state


class YawnRailsPlrStore:
    """Thread-safe PLR state with atomic JSON persistence."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.state = YawnRailsPlrState()
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.state = YawnRailsPlrState.from_dict(data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.state.to_dict(), indent=2, sort_keys=True) + "\n"
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    def observe_episode(
        self,
        *,
        checkpoint_index: int,
        leg_span: int,
        reset_variant: str,
        success: bool,
        held_out: bool = False,
    ) -> None:
        """Update learning-progress / staleness scores from one episode outcome."""
        with self._lock:
            self.state.update_count += 1
            stats = self.state.ensure_level(checkpoint_index, leg_span, reset_variant)
            prev_seen = int(stats.last_seen_update)
            stats.visits += 1
            stats.successes += int(bool(success))
            stats.recent_success.append(1 if success else 0)
            if len(stats.recent_success) > 64:
                stats.recent_success = stats.recent_success[-64:]
            train_rate = (
                sum(stats.recent_success) / len(stats.recent_success)
                if stats.recent_success
                else 0.0
            )
            # Learning progress: prefer mid-success regimes; staleness boosts idle levels.
            progress = 1.0 - abs(0.5 - train_rate) * 2.0  # peak at ~50% success
            staleness = max(0.0, float(self.state.update_count - prev_seen))
            eval_rate = self.state.eval_success.get(int(checkpoint_index))
            regression = 0.0
            if eval_rate is not None:
                regression = max(0.0, float(eval_rate) - train_rate)
            stats.score = max(
                1e-3,
                0.35 + 1.5 * progress + 0.02 * min(staleness, 50.0) + 1.25 * regression,
            )
            stats.last_seen_update = int(self.state.update_count)
            if held_out:
                # Soft EMA of held-out success into the cell's eval channel.
                prev = self.state.eval_success.get(int(checkpoint_index))
                if prev is None:
                    self.state.eval_success[int(checkpoint_index)] = 1.0 if success else 0.0
                else:
                    self.state.eval_success[int(checkpoint_index)] = (
                        0.8 * float(prev) + 0.2 * (1.0 if success else 0.0)
                    )
                self.state.eval_updated_unix = time.time()
            self._maybe_widen_unlocked(int(checkpoint_index))
            self.save()

    def ingest_held_out_success_rates(self, rates: dict[int, float]) -> None:
        with self._lock:
            for idx, rate in rates.items():
                self.state.eval_success[int(idx)] = max(0.0, min(1.0, float(rate)))
            self.state.eval_updated_unix = time.time()
            self.save()

    def _maybe_widen_unlocked(self, checkpoint_index: int) -> None:
        threshold = widen_success_from_env()
        min_eps = widen_min_episodes_from_env()
        current = self.state.max_legs_for(checkpoint_index)
        nxt = next_leg_width(current)
        if nxt is None:
            return
        # Aggregate recent outcomes across levels at this endpoint with span==current.
        recent: list[int] = []
        for key, stats in self.state.levels.items():
            try:
                idx, span, _variant = parse_level_key(key)
            except ValueError:
                continue
            if idx != int(checkpoint_index) or span != current:
                continue
            recent.extend(stats.recent_success)
        if len(recent) < min_eps:
            return
        window = recent[-min_eps:]
        if sum(window) / len(window) >= threshold:
            self.state.endpoint_max_legs[int(checkpoint_index)] = int(nxt)


def default_plr_state_path(project_root: Path | str) -> Path:
    override = os.environ.get(_STATE_ENV, "").strip()
    if override:
        path = Path(override)
        return path if path.is_absolute() else Path(project_root) / path
    return Path(project_root) / "states" / "yawn_rails" / "plr_state.json"


_STORE_LOCK = threading.Lock()
_STORE_CACHE: dict[str, YawnRailsPlrStore] = {}


def get_plr_store(project_root: Path | str) -> YawnRailsPlrStore:
    path = default_plr_state_path(project_root)
    key = str(path.resolve()) if path.exists() or path.parent.exists() else str(path)
    with _STORE_LOCK:
        store = _STORE_CACHE.get(key)
        if store is None:
            store = YawnRailsPlrStore(path)
            _STORE_CACHE[key] = store
        return store


def cell_priority_scores(
    cell_indices: list[int],
    state: YawnRailsPlrState,
) -> dict[int, float]:
    """Max level score per atomic cell (fallback 1.0)."""
    out: dict[int, float] = {int(i): 1.0 for i in cell_indices}
    for key, stats in state.levels.items():
        try:
            idx, _span, _variant = parse_level_key(key)
        except ValueError:
            continue
        if idx not in out:
            continue
        out[idx] = max(out[idx], float(stats.score))
    # Prefer cells that regressed on held-out eval.
    for idx, eval_rate in state.eval_success.items():
        if idx not in out:
            continue
        # Boost low held-out success.
        out[idx] = max(out[idx], 1.0 + max(0.0, 0.7 - float(eval_rate)))
    return out


def sample_with_uniform_floor(
    cell_indices: list[int],
    scores: dict[int, float],
    rng: random.Random,
    *,
    uniform_floor: float,
) -> int:
    """Sample a cell with ``floor/N`` reserved mass and PLR over the remainder."""
    cells = [int(i) for i in cell_indices]
    if not cells:
        raise ValueError("no cells to sample")
    n = len(cells)
    floor = max(0.0, min(1.0, float(uniform_floor)))
    weights = [max(1e-6, float(scores.get(i, 1.0))) for i in cells]
    total = sum(weights)
    probs = []
    for w in weights:
        plr_mass = (1.0 - floor) * (w / total if total > 0 else 1.0 / n)
        probs.append(floor / n + plr_mass)
    # Renormalize for float safety.
    z = sum(probs)
    probs = [p / z for p in probs]
    u = rng.random()
    acc = 0.0
    for cell, p in zip(cells, probs):
        acc += p
        if u <= acc:
            return cell
    return cells[-1]


def sample_plr_options(
    project_root: Path | str,
    stage: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    rng: random.Random,
    store: YawnRailsPlrStore | None = None,
    uniform_floor: float | None = None,
) -> dict[str, Any]:
    """Choose reset options under PLR + per-endpoint max_legs discipline."""
    if not candidates:
        raise ValueError("PLR sampler requires at least one candidate cell row")
    root = Path(project_root)
    plr = store or get_plr_store(root)
    floor = (
        float(uniform_floor)
        if uniform_floor is not None
        else uniform_floor_from_env()
    )
    by_index: dict[int, dict[str, Any]] = {}
    for row in candidates:
        by_index[int(row["checkpoint_index"])] = row
    indices = sorted(by_index)
    scores = cell_priority_scores(indices, plr.state)
    chosen_idx = sample_with_uniform_floor(
        indices, scores, rng, uniform_floor=floor
    )
    chosen = by_index[chosen_idx]
    start_index = int(chosen_idx) + 1
    route_steps = list(stage.get("route_steps", []))
    remaining = max(1, len(route_steps) - start_index) if route_steps else 1
    endpoint_max = plr.state.max_legs_for(chosen_idx)
    # Never inherit a global 6-leg unlock; stage legs_per_episode is only an upper cap.
    stage_cap = int(stage.get("legs_per_episode") or endpoint_max)
    leg_span = clamp_leg_span(
        endpoint_max,
        remaining=remaining,
        endpoint_max=min(endpoint_max, max(1, stage_cap)),
    )
    reset_variant = "route_initial" if start_index <= 0 else "route_cell"
    plr.state.ensure_level(chosen_idx, leg_span, reset_variant)
    opts: dict[str, Any] = {
        "route_start_index": start_index,
        "leg_span": leg_span,
        "reset_source": reset_variant,
        "plr_level": level_key(chosen_idx, leg_span, reset_variant),
        "endpoint_max_legs": endpoint_max,
    }
    if start_index > 0:
        opts["pb_bundle"] = yawn_cell_pb_bundle(chosen)
    return opts


def observe_episode_infos(
    project_root: Path | str,
    infos: list[dict[str, Any]],
    *,
    store: YawnRailsPlrStore | None = None,
) -> int:
    """Update PLR from Monitor episode-end infos. Returns number observed."""
    if not plr_enabled_from_env():
        return 0
    plr = store or get_plr_store(project_root)
    n = 0
    for info in infos:
        if not info or "episode" not in info:
            continue
        # Rails atomic resets only — never Go-Explore archive / PB mix sources.
        reset_source = info.get("reset_source")
        if reset_source in {"archive", "pb", "focus_pb", "other_pb", "fresh"}:
            continue
        cell_index = info.get("rails_cell_index")
        if cell_index is None:
            start = info.get("route_start_index")
            if start is None and info.get("rails_cell_id") is None:
                continue
            if start is None:
                continue
            cell_index = int(start) - 1
        leg_span = int(info.get("leg_span") or 1)
        variant = str(
            reset_source
            or ("route_initial" if int(cell_index) < 0 else "route_cell")
        )
        if variant not in {"route_cell", "route_initial"}:
            variant = "route_initial" if int(cell_index) < 0 else "route_cell"
        outcome = info.get("episode_outcome") or info.get("episode_failure")
        success = outcome == "checkpoint_success"
        held_out = bool(info.get("held_out_eval"))
        plr.observe_episode(
            checkpoint_index=int(cell_index),
            leg_span=leg_span,
            reset_variant=variant,
            success=success,
            held_out=held_out,
        )
        n += 1
    return n
