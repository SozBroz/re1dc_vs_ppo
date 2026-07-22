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
    """``RE1_PB_FRESH_WEIGHT`` in ``[0, 1]``; default 0.5 when champion mix is on.

    Deprecated for the default typewriter sampler (``sample_typewriter_start``);
    kept for legacy ``sample_reset_bundle`` callers.
    """
    import os

    raw = os.environ.get("RE1_PB_FRESH_WEIGHT", "").strip()
    if not raw:
        return float(default)
    return max(0.0, min(1.0, float(raw)))


def _list_filled_champions_fallback(project_root: Path | str) -> list[dict[str, Any]]:
    """Scan ``champions/mainhall_typewriter`` + ``typewriter_*`` when API missing."""
    from re1_rl.pb_champion import CHAMPION_JSON, pb_root

    champs_root = pb_root(project_root) / "champions"
    if not champs_root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted(champs_root.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        name = d.name
        if name != "mainhall_typewriter" and not name.startswith("typewriter_"):
            continue
        rec_path = d / CHAMPION_JSON
        state_file = d / "champion.State"
        if not rec_path.is_file() or not state_file.is_file():
            continue
        try:
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rec, dict):
            continue
        if not rec.get("state_path") or not rec.get("sidecar_path"):
            continue
        out.append(
            {
                "state_path": str(rec["state_path"]),
                "sidecar_path": str(rec["sidecar_path"]),
                "milestone_id": str(rec.get("milestone_id") or ""),
                "room_id": str(rec.get("room_id") or ""),
            }
        )
    return out


def _list_filled_champions(project_root: Path | str) -> list[dict[str, Any]]:
    from re1_rl import pb_champion as champ

    fn = getattr(champ, "list_filled_champions", None)
    if callable(fn):
        return list(fn(project_root))
    return _list_filled_champions_fallback(project_root)


def _bundle_dict_from_record(rec: dict[str, Any]) -> dict[str, str]:
    return {
        "state_path": str(rec["state_path"]),
        "sidecar_path": str(rec["sidecar_path"]),
    }


def typewriter_mix_weights(n_filled: int) -> tuple[float, float]:
    """Return ``(p_fresh, p_each_sidecar)`` for ``N`` filled champion slots.

    - N=0: fresh only (caller short-circuits)
    - N=1: 1/2 fresh, 1/2 that sidecar
    - N>=2: fresh pinned at 1/3; each sidecar gets (2/3)/N
    """
    n = int(n_filled)
    if n <= 0:
        return (1.0, 0.0)
    if n == 1:
        return (0.5, 0.5)
    return (1.0 / 3.0, (2.0 / 3.0) / float(n))


def sample_typewriter_start(
    project_root: Path | str,
    rng: random.Random | None = None,
) -> dict[str, str] | None:
    """Weighted mix of fresh vs filled typewriter champion sidecars.

    ``None`` means a fresh episode start. Filled slots come from
    ``list_filled_champions``. ``RE1_PB_FRESH_WEIGHT`` is ignored here.
    """
    from re1_rl.pb_sync import ensure_pb_sync_daemon

    root = Path(project_root)
    ensure_pb_sync_daemon(root)

    filled = _list_filled_champions(root)
    n = len(filled)
    if n == 0:
        return None

    rng = rng or random.Random()
    p_fresh, p_each = typewriter_mix_weights(n)
    # Outcomes: index 0 = fresh; 1..N = filled[i-1]
    weights = [p_fresh] + [p_each] * n
    pick = int(rng.choices(range(n + 1), weights=weights, k=1)[0])
    if pick == 0:
        return None
    return _bundle_dict_from_record(filled[pick - 1])


def sample_champion_or_fresh(
    project_root: Path | str,
    *,
    fresh_weight: float | None = None,
    rng: random.Random | None = None,
) -> dict[str, str] | None:
    """Alias for ``sample_typewriter_start`` (multi-room mix).

    ``fresh_weight`` / ``RE1_PB_FRESH_WEIGHT`` are deprecated and ignored.
    """
    del fresh_weight  # deprecated; typewriter mix uses fixed N-dependent floors
    return sample_typewriter_start(project_root, rng=rng)
