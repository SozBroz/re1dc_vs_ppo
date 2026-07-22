"""PB bundle manifest + reset mix sampling (no BizHawk).

Imperator policy (hybrid):
  1. Humans define WHICH milestones matter (taxonomy / manifest rows).
  2. Workers auto-capture PB bundles when they hit those milestones.
  3. Reset distribution mixes fresh starts + archived PB starts.
  4. Discrete milestone cells gate capture — not opaque continuous scores alone.
     Optional ``meta["score"]`` may rank duplicates within the same milestone.

See ``docs/nn_architecture_review/10_pb_capture_and_curriculum_mix.md``.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class PbBundle:
    """Savestate + episode sidecar pair for a discrete milestone."""

    state_path: str
    sidecar_path: str
    milestone_id: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "state_path": self.state_path,
            "sidecar_path": self.sidecar_path,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PbBundle:
        return cls(
            milestone_id=str(data["milestone_id"]),
            state_path=str(data["state_path"]),
            sidecar_path=str(data["sidecar_path"]),
            meta=dict(data.get("meta") or {}),
        )


def load_pb_manifest(path: Path | str) -> list[PbBundle]:
    """Load a PB manifest JSON file. Missing file → empty list."""
    p = Path(path)
    if not p.is_file():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    version = int(raw.get("version", 0))
    if version != MANIFEST_VERSION:
        raise ValueError(
            f"unsupported PB manifest version {version!r}; expected {MANIFEST_VERSION}"
        )
    bundles: list[PbBundle] = []
    for row in raw.get("bundles") or []:
        if not isinstance(row, dict):
            raise ValueError(f"manifest bundle row must be object, got {type(row)!r}")
        bundles.append(PbBundle.from_json(row))
    return bundles


def sample_reset_bundle(
    bundles: Sequence[PbBundle],
    *,
    fresh_weight: float,
    rng: random.Random | None = None,
) -> PbBundle | None:
    """Sample a reset source: ``None`` = fresh default state, else a PB bundle.

    ``fresh_weight`` is the probability of a fresh start in ``[0, 1]``.
    When a PB is chosen, bundles are weighted uniformly (v1).
    """
    if not 0.0 <= fresh_weight <= 1.0:
        raise ValueError(f"fresh_weight must be in [0, 1], got {fresh_weight}")
    rng = rng or random.Random()
    pool = list(bundles)
    if not pool or rng.random() < fresh_weight:
        return None
    return rng.choice(pool)


def fresh_weight_from_env(default: float = 0.5) -> float:
    """``RE1_PB_FRESH_WEIGHT`` in ``[0, 1]``; default 0.5 when champion mix is on."""
    import os

    raw = os.environ.get("RE1_PB_FRESH_WEIGHT", "").strip()
    if not raw:
        return float(default)
    return max(0.0, min(1.0, float(raw)))


def sample_champion_or_fresh(
    project_root: Path | str,
    *,
    fresh_weight: float | None = None,
    rng: random.Random | None = None,
) -> dict[str, str] | None:
    """50/50 (configurable) mix: champion pb_bundle dict or ``None`` for fresh.

    Starts the delayed shared-root sync daemon when configured; does not block
    on a sync round-trip (lag is acceptable).
    """
    from re1_rl.pb_champion import champion_bundle_for_reset
    from re1_rl.pb_sync import ensure_pb_sync_daemon

    root = Path(project_root)
    ensure_pb_sync_daemon(root)

    bundle = champion_bundle_for_reset(root)
    if bundle is None:
        return None
    weight = fresh_weight_from_env(0.5) if fresh_weight is None else float(fresh_weight)
    pb = PbBundle(
        state_path=bundle["state_path"],
        sidecar_path=bundle["sidecar_path"],
        milestone_id=str(bundle.get("milestone_id") or "typewriter_save:106"),
    )
    chosen = sample_reset_bundle([pb], fresh_weight=weight, rng=rng)
    if chosen is None:
        return None
    return {
        "state_path": chosen.state_path,
        "sidecar_path": chosen.sidecar_path,
    }
