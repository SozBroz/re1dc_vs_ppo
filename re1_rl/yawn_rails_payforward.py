"""Fight-cliff discovery and progression-biased Yawn rails resets.

When ``RE1_YAWN_PAYFORWARD_RIPPLE=1``, reset mix is **40%** the current frontier
fight cell (first curated fight not yet ammo-efficient) and **60%** uniform over
**all** loadable cells from ``cp00`` — no latest-cell bias.

Fight efficiency uses curated beretta budgets (7 per zombie, 50% waste cap).
The frontier advances cp18 → cp26 → … as each fight's successor meets its
min-tolerated net ammo shift.
"""

from __future__ import annotations

import json
import math
import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from re1_rl.go_explore_archive import Quality, normalize_quality, quality_beats
from re1_rl.yawn_rails_sync import yawn_cell_pb_bundle

_SCHEMA = 1
_PAYFORWARD_ENV = "RE1_YAWN_PAYFORWARD_RIPPLE"
_STATE_ENV = "RE1_YAWN_PAYFORWARD_STATE"
_FORCE_FIGHTS_ENV = "RE1_YAWN_PAYFORWARD_FORCE_FIGHTS"
_IGNORE_FIGHTS_ENV = "RE1_YAWN_PAYFORWARD_IGNORE_FIGHTS"
_FIGHT_BIAS_WEIGHT = 0.40
_FIGHT_BIAS_ENV = "RE1_YAWN_FIGHT_BIAS_WEIGHT"
_FIGHT_BIAS_INDEX_ENV = "RE1_YAWN_FIGHT_BIAS_INDEX"
_LATEST_WEIGHT = 0.20  # legacy ripple store only
_FIGHT_BUDGET = 0.80

# Optimistic HG-eq ammo bonuses for route items_gained (pickup legs).
# Shotgun: wall rack is ~7 shells; 7*25//4=43 under-shoots observed cp22→23
# gains (~+47), so use a cushion — false "blocked" stops the whole ripple.
_PICKUP_AMMO_BONUS: dict[str, int] = {
    "shotgun": 50,
    "handgun_bullets": 15,
    "shotgun_shells": 50,
    "acid_rounds": 15,
    "explosive_rounds": 15,
    "flame_rounds": 15,
}

STATUS_GRIND = "grind_fight"
STATUS_RIPPLING = "rippling"
STATUS_BLOCKED = "blocked"
STATUS_DONE = "stretch_done"


def payforward_ripple_enabled(default: bool = False) -> bool:
    """Opt-in fight-progression reset mix (40/60 fight/uniform)."""
    raw = os.environ.get(_PAYFORWARD_ENV, "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def fight_bias_weight_from_env(
    project_root: Path | str | None = None,
) -> float:
    """``RE1_YAWN_FIGHT_BIAS_WEIGHT`` — fraction on fight-bias cell (default 0.40).

    Hot-reloadable via ``data/yawn_reset_pin.env`` (or ``RE1_YAWN_RESET_PIN_FILE``).
    """
    from re1_rl.yawn_rails import _pin_env_raw

    raw = _pin_env_raw(_FIGHT_BIAS_ENV, project_root)
    if not raw:
        return float(_FIGHT_BIAS_WEIGHT)
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return float(_FIGHT_BIAS_WEIGHT)


def fight_bias_index_from_env(
    project_root: Path | str | None = None,
) -> int | None:
    """``RE1_YAWN_FIGHT_BIAS_INDEX=N`` — fixed cpNN for the fight-bias reset branch.

    When set and loadable, replaces :func:`frontier_fight_index` for payforward resets.
    Hot-reloadable via ``data/yawn_reset_pin.env`` (or ``RE1_YAWN_RESET_PIN_FILE``).
    """
    from re1_rl.yawn_rails import _pin_env_raw

    raw = _pin_env_raw(_FIGHT_BIAS_INDEX_ENV, project_root)
    if not raw:
        return None
    try:
        idx = int(raw, 10)
    except ValueError:
        return None
    return idx if idx >= 0 else None


def _index_set_from_env(env_name: str) -> set[int]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def force_fight_indices_from_env() -> set[int]:
    """``RE1_YAWN_PAYFORWARD_FORCE_FIGHTS=45`` — always treat as fights."""
    return _index_set_from_env(_FORCE_FIGHTS_ENV)


def ignore_fight_indices_from_env() -> set[int]:
    """``RE1_YAWN_PAYFORWARD_IGNORE_FIGHTS=52`` — drop ammo-cliff fights."""
    return _index_set_from_env(_IGNORE_FIGHTS_ENV)


def _ammo(q: Quality | list[int] | tuple[int, ...] | None) -> int:
    if q is None:
        return 0
    nq = normalize_quality(q)
    return int(nq[1])


def _cell_quality(row: dict[str, Any]) -> Quality:
    return normalize_quality(row.get("quality"))


@dataclass(frozen=True)
class FightLegRule:
    """Optional validation for a payforward fight edge (tip checkpoint_id)."""

    disallow: bool = False
    min_ammo_drop: int = 0
    min_kills_room: str | None = None
    min_kills: int = 0


# Curated legs where ammo cliffs alone are misleading (navigate-only returns,
# or mandatory hallway clears). ``min_ammo_drop`` uses damage-weighted quality[1].
ZOMBIE_BERETTA_SPEND = 7
FIGHT_WASTE_FACTOR = 1.5  # 50% ammo waste tolerance above ideal


def zombie_fight_spend(zombies: int) -> tuple[int, int]:
    """Return ``(ideal_beretta, max_beretta)`` for ``zombies`` cleared on the leg."""
    n = max(0, int(zombies))
    ideal = ZOMBIE_BERETTA_SPEND * n
    max_spend = int(math.ceil(ideal * FIGHT_WASTE_FACTOR))
    return ideal, max_spend


_TWO_ZOMBIE_MIN_DROP = zombie_fight_spend(2)[0]
_ONE_ZOMBIE_MIN_DROP = zombie_fight_spend(1)[0]


FIGHT_LEG_RULES: dict[str, FightLegRule] = {
    # cp19: L Passage ammo — both window dogs before the pickup cell installs.
    "ammo_108": FightLegRule(
        min_kills_room="108",
        min_kills=2,
    ),
    # cp26: Back Passage — 2 zombies @ 7 beretta each before gallery.
    "back_passage_10A": FightLegRule(
        min_ammo_drop=_TWO_ZOMBIE_MIN_DROP,
        min_kills_room="10A",
        min_kills=2,
    ),
    # cp36/cp39: navigate-only 10A legs; bogus cliffs from poisoned lineage.
    "back_passage_return_10A": FightLegRule(disallow=True),
    "back_passage_post_crest_10A": FightLegRule(disallow=True),
    # cp37: courtyard — Cerberus dog before crest gate (~5 beretta ideal).
    "courtyard_enter_11A": FightLegRule(
        min_ammo_drop=5,
        min_kills_room="11A",
        min_kills=1,
    ),
    # cp40: East stairs — 1 zombie on the way to the storeroom.
    "east_stairs_101": FightLegRule(
        min_ammo_drop=_ONE_ZOMBIE_MIN_DROP,
        min_kills_room="10B",
        min_kills=1,
    ),
    # cp43: storeroom return — nav leg; small ammo cliff is miss waste, not a fight.
    "east_stairs_101_post_storeroom": FightLegRule(disallow=True),
    # cp44/cp45/cp53: 2-zombie shotgun rooms.
    "east_stairs_201": FightLegRule(min_ammo_drop=_TWO_ZOMBIE_MIN_DROP),
    "c_passage_204": FightLegRule(
        min_ammo_drop=_TWO_ZOMBIE_MIN_DROP,
        min_kills_room="204",
        min_kills=2,
    ),
    "dining_2f_enter_202": FightLegRule(min_ammo_drop=_TWO_ZOMBIE_MIN_DROP),
}


@dataclass(frozen=True)
class FightAmmoTarget:
    """Curated ammo budget for a payforward fight edge (tip cpN → cpN+1)."""

    fight_index: int
    ideal_spend: int
    max_spend: int
    min_net_delta: int
    successor_pickup: int = 0


def _zombie_fight_target(fight_index: int, zombies: int) -> FightAmmoTarget:
    ideal, max_spend = zombie_fight_spend(zombies)
    return FightAmmoTarget(
        fight_index=int(fight_index),
        ideal_spend=int(ideal),
        max_spend=int(max_spend),
        min_net_delta=-int(max_spend),
    )


# Ordered fight curriculum; frontier advances when successor meets budget.
FIGHT_PROGRESSION: tuple[FightAmmoTarget, ...] = (
    FightAmmoTarget(18, 10, 15, 0, successor_pickup=15),
    _zombie_fight_target(26, 2),
    FightAmmoTarget(37, 5, 8, -8),
    _zombie_fight_target(40, 1),
    _zombie_fight_target(44, 2),
    _zombie_fight_target(45, 2),
    _zombie_fight_target(53, 2),
)


def fight_target_for_index(fight_index: int) -> FightAmmoTarget | None:
    for target in FIGHT_PROGRESSION:
        if int(target.fight_index) == int(fight_index):
            return target
    return None


def fight_spend_beretta(
    tip_row: dict[str, Any],
    succ_row: dict[str, Any],
    *,
    successor_pickup: int = 0,
) -> int:
    """Manifest HG-eq spend crossing tip → successor (pickup on successor leg)."""
    pickup = max(0, int(successor_pickup))
    return pickup + _ammo(tip_row.get("quality")) - _ammo(succ_row.get("quality"))


def fight_efficiency_met(
    tip_row: dict[str, Any],
    succ_row: dict[str, Any],
    target: FightAmmoTarget,
    *,
    project_root: Path | None = None,
) -> bool:
    """True when successor quality reflects an acceptable fight on this leg."""
    net = _ammo(succ_row.get("quality")) - _ammo(tip_row.get("quality"))
    if net < int(target.min_net_delta):
        return False
    spend = fight_spend_beretta(
        tip_row, succ_row, successor_pickup=int(target.successor_pickup)
    )
    if spend > int(target.max_spend):
        return False
    if spend < int(target.ideal_spend):
        return False
    tip_id = str(tip_row.get("checkpoint_id") or "")
    rule = FIGHT_LEG_RULES.get(tip_id)
    if rule is not None and rule.min_kills > 0 and rule.min_kills_room:
        kills = _leg_kills_from_sidecar(
            succ_row, str(rule.min_kills_room), project_root
        )
        if kills is not None and kills < int(rule.min_kills):
            return False
    return True


def frontier_fight_index(
    cells_by_idx: dict[int, dict[str, Any]],
    *,
    project_root: Path | None = None,
) -> int | None:
    """First fight in :data:`FIGHT_PROGRESSION` whose successor is not yet efficient."""
    for target in FIGHT_PROGRESSION:
        f = int(target.fight_index)
        if f not in cells_by_idx:
            continue
        succ = f + 1
        if succ not in cells_by_idx:
            return f
        if not fight_efficiency_met(
            cells_by_idx[f],
            cells_by_idx[succ],
            target,
            project_root=project_root,
        ):
            return f
    return None


def frontier_fight_cell(
    cells: list[dict[str, Any]],
    *,
    project_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Loadable cell for the current fight-progression frontier (or last fight)."""
    if not cells:
        return None
    by_idx = {int(r["checkpoint_index"]): r for r in cells}
    root = Path(project_root) if project_root is not None else None
    frontier = frontier_fight_index(by_idx, project_root=root)
    if frontier is not None and frontier in by_idx:
        return by_idx[frontier]
    for target in reversed(FIGHT_PROGRESSION):
        f = int(target.fight_index)
        if f in by_idx:
            return by_idx[f]
    return None


def choose_progression_reset_index(
    cells: list[dict[str, Any]],
    rng: random.Random,
    *,
    project_root: Path | str | None = None,
    fight_bias: float | None = None,
) -> int | None:
    """40% frontier fight cell; 60% uniform over all loadable cells (cp00+)."""
    if not cells:
        return None
    by_idx = {int(r["checkpoint_index"]): r for r in cells}
    idxs = sorted(by_idx)
    root = Path(project_root) if project_root is not None else None
    override = fight_bias_index_from_env(root)
    if override is not None and int(override) in by_idx:
        frontier = int(override)
    else:
        frontier = frontier_fight_index(by_idx, project_root=root)
    bias = float(_FIGHT_BIAS_WEIGHT if fight_bias is None else fight_bias)
    if frontier is not None and frontier in by_idx and rng.random() < bias:
        return int(frontier)
    return int(idxs[rng.randrange(len(idxs))])


def _leg_kills_from_sidecar(
    row: dict[str, Any],
    room_id: str,
    project_root: Path | None,
) -> int | None:
    """Paid kills in ``room_id`` recorded on the successor cell sidecar.

    Returns ``None`` when the sidecar is missing or predates ``leg_kills_by_room``.
    """
    if project_root is None:
        return None
    rel = str(row.get("sidecar_path") or "").strip()
    if not rel:
        return None
    path = Path(project_root) / rel
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    progress = data.get("progress")
    if not isinstance(progress, dict):
        return None
    raw = progress.get("leg_kills_by_room")
    if not isinstance(raw, dict):
        return None
    try:
        return int(raw.get(str(room_id).upper(), 0) or 0)
    except (TypeError, ValueError):
        return 0


def fight_leg_valid(
    tip_row: dict[str, Any],
    succ_row: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> bool:
    """True when a natural ammo cliff reflects a real fight on this leg."""
    tip_id = str(tip_row.get("checkpoint_id") or "")
    rule = FIGHT_LEG_RULES.get(tip_id)
    if rule is not None and rule.disallow:
        return False
    drop = _ammo(tip_row.get("quality")) - _ammo(succ_row.get("quality"))
    if rule is None:
        return drop > 0
    ammo_ok = drop >= int(rule.min_ammo_drop)
    if rule.min_kills > 0 and rule.min_kills_room:
        kills = _leg_kills_from_sidecar(succ_row, rule.min_kills_room, project_root)
        if kills is not None:
            return kills >= int(rule.min_kills)
        if rule.min_ammo_drop > 0:
            return ammo_ok
        return False
    if rule.min_ammo_drop > 0:
        return ammo_ok
    return True


@dataclass(frozen=True)
class FightStretch:
    fight_index: int
    stretch_end: int  # exclusive; next fight or max_index+1


def discover_fights(
    cells: list[dict[str, Any]],
    *,
    force: set[int] | None = None,
    ignore: set[int] | None = None,
    project_root: Path | None = None,
) -> list[FightStretch]:
    """Ammo cliffs among curated cells (any index set; caller filters cp18+).

    ``force`` / ``RE1_YAWN_PAYFORWARD_FORCE_FIGHTS`` adds fight edges without a
    cliff. ``ignore`` / ``RE1_YAWN_PAYFORWARD_IGNORE_FIGHTS`` drops cliffs so a
    prior fight can ripple through (e.g. force 45 + ignore 52 → 45..54).

    Curated legs in :data:`FIGHT_LEG_RULES` require plausible ammo spend and/or
    sidecar kill evidence (e.g. cp26 Back Passage needs ~2 SG or 2 kills in 10A).
    """
    by_idx: dict[int, dict[str, Any]] = {}
    for row in cells:
        try:
            idx = int(row["checkpoint_index"])
        except (KeyError, TypeError, ValueError):
            continue
        by_idx[idx] = row
    if not by_idx:
        return []
    idxs = sorted(by_idx)
    forced = force if force is not None else force_fight_indices_from_env()
    ignored = ignore if ignore is not None else ignore_fight_indices_from_env()
    fights: list[int] = []
    for i, idx in enumerate(idxs):
        nxt = idxs[i + 1] if i + 1 < len(idxs) else None
        if nxt is None or nxt != idx + 1:
            # Only adjacent curated indices form a fight edge.
            continue
        if idx in ignored and idx not in forced:
            continue
        if idx in forced:
            fights.append(idx)
            continue
        cliff = _ammo(by_idx[idx].get("quality")) > _ammo(
            by_idx[nxt].get("quality")
        )
        if cliff and fight_leg_valid(
            by_idx[idx], by_idx[nxt], project_root=project_root
        ):
            fights.append(idx)
    max_idx = idxs[-1]
    out: list[FightStretch] = []
    for i, f in enumerate(fights):
        end = fights[i + 1] if i + 1 < len(fights) else max_idx + 1
        out.append(FightStretch(fight_index=f, stretch_end=int(end)))
    return out


def _pickup_names(route_step: dict[str, Any] | None) -> list[str]:
    if not route_step:
        return []
    from re1_rl.item_todo import canonical_item

    return [
        canonical_item(str(x))
        for x in (route_step.get("items_gained") or [])
        if str(x).strip()
    ]


def project_end_quality(
    start_q: Quality | list[int] | tuple[int, ...],
    route_step: dict[str, Any] | None,
) -> Quality:
    """Optimistic quality after completing ``route_step`` (no HP loss)."""
    from re1_rl.go_explore_capture import healing_weight_centi

    hp, ammo, healing, slots, poison, neg_ribbons, neg_box, neg_frames = (
        normalize_quality(start_q)
    )
    for name in _pickup_names(route_step):
        ammo += int(_PICKUP_AMMO_BONUS.get(name, 0))
        healing += healing_weight_centi(name)
        # New distinct item → ever_held / slots bump (optimistic).
        if name:
            slots += 1
    return (
        int(hp),
        int(ammo),
        int(healing),
        int(slots),
        int(poison),
        int(neg_ribbons),
        int(neg_box),
        int(neg_frames),
    )


def strip_pickup_bonuses(
    end_q: Quality | list[int] | tuple[int, ...],
    route_step: dict[str, Any] | None,
) -> Quality:
    """Reverse optimistic pickup bonuses (incumbent already includes the gain)."""
    from re1_rl.go_explore_capture import healing_weight_centi

    hp, ammo, healing, slots, poison, neg_ribbons, neg_box, neg_frames = (
        normalize_quality(end_q)
    )
    for name in _pickup_names(route_step):
        ammo = max(0, ammo - int(_PICKUP_AMMO_BONUS.get(name, 0)))
        healing = max(0, healing - healing_weight_centi(name))
        if name:
            slots = max(0, slots - 1)
    return (
        int(hp),
        int(ammo),
        int(healing),
        int(slots),
        int(poison),
        int(neg_ribbons),
        int(neg_box),
        int(neg_frames),
    )


def hop_blocked(
    tip_q: Quality | list[int] | tuple[int, ...],
    succ_q: Quality | list[int] | tuple[int, ...],
    route_step: dict[str, Any] | None,
) -> bool:
    """True only if tip cannot beat succ even with optimistic pickup accounting.

    Two checks (either clears the hop):
    1. tip + projected gains beats incumbent end quality
    2. tip beats incumbent with those gains stripped (pre-pickup vs pre-pickup)
    """
    projected = project_end_quality(tip_q, route_step)
    if quality_beats(projected, succ_q):
        return False
    if _pickup_names(route_step):
        succ_base = strip_pickup_bonuses(succ_q, route_step)
        if quality_beats(normalize_quality(tip_q), succ_base):
            return False
    return True


def default_payforward_state_path(project_root: Path | str) -> Path:
    override = os.environ.get(_STATE_ENV, "").strip()
    if override:
        path = Path(override)
        return path if path.is_absolute() else Path(project_root) / path
    from re1_rl.yawn_rails_sync import yawn_rails_root

    return yawn_rails_root(project_root) / "payforward_ripple.json"


def _route_step_by_checkpoint_id(
    route: list[dict[str, Any]],
    checkpoint_id: str,
) -> dict[str, Any] | None:
    want = str(checkpoint_id or "")
    if not want:
        return None
    for step in route:
        if str(step.get("checkpoint_id") or "") == want:
            return step
    return None


def _next_checkpoint_id_for_cell(
    row: dict[str, Any],
    succ_row: dict[str, Any] | None,
) -> str:
    nid = str(row.get("next_checkpoint_id") or "")
    if nid:
        return nid
    if succ_row is not None:
        return str(succ_row.get("checkpoint_id") or "")
    return ""


@dataclass
class FightRuntime:
    fight_index: int
    stretch_end: int
    status: str = STATUS_GRIND
    ripple_tip: int | None = None
    blocked_at: int | None = None

    def sample_index(self) -> int:
        if self.status == STATUS_RIPPLING and self.ripple_tip is not None:
            return int(self.ripple_tip)
        return int(self.fight_index)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fight_index": int(self.fight_index),
            "stretch_end": int(self.stretch_end),
            "status": str(self.status),
            "ripple_tip": self.ripple_tip,
            "blocked_at": self.blocked_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FightRuntime:
        tip = raw.get("ripple_tip")
        blocked = raw.get("blocked_at")
        return cls(
            fight_index=int(raw["fight_index"]),
            stretch_end=int(raw.get("stretch_end", int(raw["fight_index"]) + 1)),
            status=str(raw.get("status") or STATUS_GRIND),
            ripple_tip=int(tip) if tip is not None else None,
            blocked_at=int(blocked) if blocked is not None else None,
        )


class PayforwardRippleStore:
    """JSON-backed per-fight ripple state."""

    def __init__(self, path: Path, *, project_root: Path | str | None = None) -> None:
        self.path = Path(path)
        self.project_root = (
            Path(project_root).resolve() if project_root is not None else None
        )
        self._lock = threading.RLock()
        self.fights: dict[int, FightRuntime] = {}
        self.updated_unix: float = 0.0
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return
        if int(data.get("schema_version", 0) or 0) != _SCHEMA:
            return
        self.updated_unix = float(data.get("updated_unix", 0.0) or 0.0)
        self.fights = {}
        for key, raw in (data.get("by_fight") or {}).items():
            if not isinstance(raw, dict):
                continue
            try:
                fr = FightRuntime.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                continue
            self.fights[int(fr.fight_index)] = fr

    def save(self) -> None:
        """Best-effort atomic write. Never raise — multi-actor Windows races are OK."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _SCHEMA,
            "updated_unix": time.time(),
            "fights": sorted(self.fights),
            "by_fight": {
                str(k): v.to_dict() for k, v in sorted(self.fights.items())
            },
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
        except OSError:
            return
        for attempt in range(8):
            try:
                os.replace(tmp, self.path)
                self.updated_unix = float(payload["updated_unix"])
                return
            except OSError:
                time.sleep(0.01 * (attempt + 1))
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass

    def refresh_from_disk(self) -> None:
        """Reload JSON if another process advanced tips (install path)."""
        if not self.path.is_file():
            return
        try:
            mtime = float(self.path.stat().st_mtime)
        except OSError:
            return
        if mtime <= float(self.updated_unix) + 1e-6:
            return
        self.load()

    def _merge_discovered(
        self,
        cells: list[dict[str, Any]],
    ) -> tuple[list[FightStretch], dict[int, dict[str, Any]]]:
        """Rebind fight entries to current discovery (no blocked evaluation)."""
        discovered = discover_fights(cells, project_root=self.project_root)
        by_idx: dict[int, dict[str, Any]] = {}
        for row in cells:
            try:
                by_idx[int(row["checkpoint_index"])] = row
            except (KeyError, TypeError, ValueError):
                continue
        prev = dict(self.fights)
        self.fights = {}
        for stretch in discovered:
            f = int(stretch.fight_index)
            end = int(stretch.stretch_end)
            old = prev.get(f)
            if old is None:
                fr = FightRuntime(fight_index=f, stretch_end=end, status=STATUS_GRIND)
            else:
                fr = FightRuntime(
                    fight_index=f,
                    stretch_end=end,
                    status=str(old.status),
                    ripple_tip=old.ripple_tip,
                    blocked_at=old.blocked_at,
                )
                if fr.status == STATUS_RIPPLING and fr.ripple_tip is not None:
                    tip = int(fr.ripple_tip)
                    if tip < f + 1:
                        tip = f + 1
                    if tip >= end:
                        fr.status = STATUS_DONE
                        fr.ripple_tip = None
                    else:
                        fr.ripple_tip = tip
                if fr.status == STATUS_BLOCKED and fr.blocked_at is not None:
                    if int(fr.blocked_at) >= end:
                        fr.status = STATUS_GRIND
                        fr.blocked_at = None
            self.fights[f] = fr
        return list(discovered), by_idx

    def _apply_blocked_checks(
        self,
        by_idx: dict[int, dict[str, Any]],
        route: list[dict[str, Any]] | None,
    ) -> None:
        if route is None:
            return
        for fr in self.fights.values():
            # Re-open a sticky block when tip/succ qualities improve enough.
            if fr.status == STATUS_BLOCKED and fr.blocked_at is not None:
                tip = int(fr.blocked_at)
                succ = tip + 1
                if tip in by_idx and succ in by_idx:
                    nid = _next_checkpoint_id_for_cell(by_idx[tip], by_idx.get(succ))
                    step = _route_step_by_checkpoint_id(route, nid)
                    if not hop_blocked(
                        _cell_quality(by_idx[tip]),
                        _cell_quality(by_idx[succ]),
                        step,
                    ):
                        fr.status = STATUS_RIPPLING
                        fr.ripple_tip = tip
                        fr.blocked_at = None
                continue
            if fr.status != STATUS_RIPPLING or fr.ripple_tip is None:
                continue
            tip = int(fr.ripple_tip)
            succ = tip + 1
            if tip not in by_idx or succ not in by_idx:
                continue
            nid = _next_checkpoint_id_for_cell(by_idx[tip], by_idx.get(succ))
            step = _route_step_by_checkpoint_id(route, nid)
            if hop_blocked(
                _cell_quality(by_idx[tip]),
                _cell_quality(by_idx[succ]),
                step,
            ):
                fr.status = STATUS_BLOCKED
                fr.blocked_at = tip
                fr.ripple_tip = None

    def reconcile(
        self,
        cells: list[dict[str, Any]],
        *,
        route: list[dict[str, Any]] | None = None,
        persist: bool = True,
        check_blocked: bool = True,
    ) -> list[FightStretch]:
        """Merge discovery with persisted tips; optionally check blocked hops."""
        with self._lock:
            discovered, by_idx = self._merge_discovered(cells)
            if check_blocked:
                self._apply_blocked_checks(by_idx, route)
            if persist:
                self.save()
            return list(discovered)

    def on_install(
        self,
        installed_index: int,
        cells: list[dict[str, Any]],
        *,
        route: list[dict[str, Any]] | None = None,
    ) -> None:
        """Advance ripple when ``installed_index`` is the expected successor."""
        idx = int(installed_index)
        with self._lock:
            # Merge first without blocked checks: the just-installed successor
            # often makes the old tip unable to beat it — that is success, not
            # a blocked hop. Advance, then evaluate the *new* tip.
            _, by_idx = self._merge_discovered(cells)
            for fr in self.fights.values():
                # Blocked stretches stay parked on the fight until hop clears.
                # DONE re-opens when the fight successor improves again so a
                # new better loadout can ripple through to the next fight.
                if fr.status == STATUS_BLOCKED:
                    continue
                expected = (
                    int(fr.fight_index) + 1
                    if fr.status in (STATUS_GRIND, STATUS_DONE)
                    or fr.ripple_tip is None
                    else int(fr.ripple_tip) + 1
                )
                if idx != expected:
                    continue
                # Successful improve of expected successor.
                new_tip = idx
                end = int(fr.stretch_end)
                if new_tip + 1 >= end:
                    fr.status = STATUS_DONE
                    fr.ripple_tip = None
                    fr.blocked_at = None
                    continue
                succ = new_tip + 1
                if new_tip not in by_idx or succ not in by_idx:
                    fr.status = STATUS_RIPPLING
                    fr.ripple_tip = new_tip
                    fr.blocked_at = None
                    continue
                nid = _next_checkpoint_id_for_cell(by_idx[new_tip], by_idx.get(succ))
                step = _route_step_by_checkpoint_id(route or [], nid)
                if hop_blocked(
                    _cell_quality(by_idx[new_tip]),
                    _cell_quality(by_idx[succ]),
                    step,
                ):
                    fr.status = STATUS_BLOCKED
                    fr.blocked_at = new_tip
                    fr.ripple_tip = None
                else:
                    fr.status = STATUS_RIPPLING
                    fr.ripple_tip = new_tip
                    fr.blocked_at = None
            self._apply_blocked_checks(by_idx, route)
            self.save()

    def choose_cell_index(
        self,
        cells: list[dict[str, Any]],
        rng: random.Random,
        *,
        route: list[dict[str, Any]] | None = None,
        latest_weight: float = _LATEST_WEIGHT,
        project_root: Path | str | None = None,
    ) -> int | None:
        """Return checkpoint_index to load (progression mix; no latest bias)."""
        del route, latest_weight  # ripple tips retired from reset sampling
        return choose_progression_reset_index(
            cells,
            rng,
            project_root=project_root or self.project_root,
        )


_STORE_LOCK = threading.Lock()
_STORE_CACHE: dict[str, PayforwardRippleStore] = {}


def get_payforward_store(project_root: Path | str) -> PayforwardRippleStore:
    path = default_payforward_state_path(project_root)
    key = str(path.resolve()) if path.parent.exists() else str(path)
    with _STORE_LOCK:
        store = _STORE_CACHE.get(key)
        if store is None:
            store = PayforwardRippleStore(path, project_root=project_root)
            _STORE_CACHE[key] = store
        return store


def notify_payforward_install(
    project_root: Path | str,
    *,
    installed_index: int,
    cells: list[dict[str, Any]] | None = None,
    route: list[dict[str, Any]] | None = None,
) -> None:
    """Best-effort ripple advance after a curated cell install."""
    if not payforward_ripple_enabled(default=False):
        return
    try:
        store = get_payforward_store(project_root)
        if cells is None:
            from re1_rl.yawn_rails_sync import yawn_rails_root

            man_p = yawn_rails_root(project_root) / "manifest.json"
            if not man_p.is_file():
                return
            man = json.loads(man_p.read_text(encoding="utf-8-sig"))
            cells = [
                r
                for r in (man.get("cells") or [])
                if isinstance(r, dict)
                and int(r.get("checkpoint_index", -1)) >= 18
            ]
        if route is None:
            route = _load_route(project_root)
        store.on_install(int(installed_index), list(cells), route=route)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return


def _load_route(project_root: Path | str) -> list[dict[str, Any]]:
    root = Path(project_root)
    path = root / "data" / "yawn_checkpoint_route.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def sample_payforward_options(
    project_root: Path,
    stage: dict[str, Any],
    cells: list[dict[str, Any]],
    *,
    rng: random.Random,
) -> dict[str, Any] | None:
    """Build reset options under fight-progression mix, or None to use legacy."""
    if not payforward_ripple_enabled(default=False):
        return None
    if not cells:
        return None
    try:
        pick = choose_progression_reset_index(cells, rng, project_root=project_root)
        if pick is None:
            return None
        by_idx = {int(r["checkpoint_index"]): r for r in cells}
        chosen = by_idx.get(int(pick))
        if chosen is None:
            return None
        start_index = int(chosen["checkpoint_index"]) + 1
        route_steps = list(stage.get("route_steps", []))
        remaining = max(1, len(route_steps) - start_index) if route_steps else 1
        return {
            "route_start_index": start_index,
            "leg_span": remaining,
            "reset_source": "route_cell",
            "pb_bundle": yawn_cell_pb_bundle(chosen),
            "payforward_fight_progression": True,
            "payforward_frontier_fight": frontier_fight_index(by_idx, project_root=project_root),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def sample_frontier_fight_options(
    project_root: Path,
    stage: dict[str, Any],
    cells: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Always reset from the fight-progression frontier cell (memlog grind)."""
    chosen = frontier_fight_cell(cells, project_root=project_root)
    if chosen is None:
        return None
    start_index = int(chosen["checkpoint_index"]) + 1
    route_steps = list(stage.get("route_steps", []))
    remaining = max(1, len(route_steps) - start_index) if route_steps else 1
    by_idx = {int(r["checkpoint_index"]): r for r in cells}
    return {
        "route_start_index": start_index,
        "leg_span": remaining,
        "reset_source": "route_cell_frontier_fight",
        "pb_bundle": yawn_cell_pb_bundle(chosen),
        "payforward_frontier_fight": int(chosen["checkpoint_index"]),
        "payforward_frontier_index": frontier_fight_index(
            by_idx, project_root=project_root
        ),
    }
