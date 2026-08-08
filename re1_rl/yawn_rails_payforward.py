"""Fight-cliff discovery and ripple grind for Yawn rails resets (cp18+).

Fighting CP: curated ``ammo(cpN) > ammo(cpN+1)``. Reset mix keeps 20% latest and
splits the other 80% equally across fights. After a fight improves its successor,
that fight's share is spent on the ripple tip until the stretch ends or a hop is
blocked (projected end quality cannot beat the incumbent).
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from re1_rl.go_explore_archive import Quality, normalize_quality, quality_beats

_SCHEMA = 1
_PAYFORWARD_ENV = "RE1_YAWN_PAYFORWARD_RIPPLE"
_STATE_ENV = "RE1_YAWN_PAYFORWARD_STATE"
_LATEST_WEIGHT = 0.20
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
_HEALING_NAMES = frozenset(
    {
        "green_herb",
        "red_herb",
        "blue_herb",
        "mixed_herb",
        "first_aid_spray",
        "first_aid_spray_alt",
    }
)

STATUS_GRIND = "grind_fight"
STATUS_RIPPLING = "rippling"
STATUS_BLOCKED = "blocked"
STATUS_DONE = "stretch_done"


def payforward_ripple_enabled(default: bool = False) -> bool:
    """Opt-in fight-ripple mix. Default off — fleet uses 50/50 latest/any-cp18+."""
    raw = os.environ.get(_PAYFORWARD_ENV, "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _ammo(q: Quality | list[int] | tuple[int, ...] | None) -> int:
    if q is None:
        return 0
    nq = normalize_quality(q)
    return int(nq[1])


def _cell_quality(row: dict[str, Any]) -> Quality:
    return normalize_quality(row.get("quality"))


@dataclass(frozen=True)
class FightStretch:
    fight_index: int
    stretch_end: int  # exclusive; next fight or max_index+1


def discover_fights(cells: list[dict[str, Any]]) -> list[FightStretch]:
    """Ammo cliffs among curated cells (any index set; caller filters cp18+)."""
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
    fights: list[int] = []
    for i, idx in enumerate(idxs):
        nxt = idxs[i + 1] if i + 1 < len(idxs) else None
        if nxt is None or nxt != idx + 1:
            # Only adjacent curated indices form a fight edge.
            continue
        if _ammo(by_idx[idx].get("quality")) > _ammo(by_idx[nxt].get("quality")):
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
    hp, ammo, healing, slots, poison, neg_ribbons, neg_box = normalize_quality(start_q)
    for name in _pickup_names(route_step):
        ammo += int(_PICKUP_AMMO_BONUS.get(name, 0))
        if name in _HEALING_NAMES:
            healing += 1
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
    )


def strip_pickup_bonuses(
    end_q: Quality | list[int] | tuple[int, ...],
    route_step: dict[str, Any] | None,
) -> Quality:
    """Reverse optimistic pickup bonuses (incumbent already includes the gain)."""
    hp, ammo, healing, slots, poison, neg_ribbons, neg_box = normalize_quality(end_q)
    for name in _pickup_names(route_step):
        ammo = max(0, ammo - int(_PICKUP_AMMO_BONUS.get(name, 0)))
        if name in _HEALING_NAMES:
            healing = max(0, healing - 1)
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

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
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
        discovered = discover_fights(cells)
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
                if fr.status in (STATUS_BLOCKED, STATUS_DONE):
                    continue
                expected = (
                    int(fr.fight_index) + 1
                    if fr.status == STATUS_GRIND
                    else int(fr.ripple_tip or -1) + 1
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
    ) -> int | None:
        """Return checkpoint_index to load, or None → caller uses legacy mix."""
        eligible = list(cells)
        if not eligible:
            return None
        by_idx = {int(r["checkpoint_index"]): r for r in eligible}
        idxs = sorted(by_idx)
        latest = idxs[-1]
        # Never persist on the sample path — dozens of actors would race
        # os.replace on Windows and crash the fleet (PermissionError).
        self.refresh_from_disk()
        self.reconcile(eligible, route=route, persist=False)
        if not self.fights:
            return None
        if rng.random() < float(latest_weight):
            return int(latest)
        fight_ids = sorted(self.fights)
        fr = self.fights[int(rng.choice(fight_ids))]
        pick = int(fr.sample_index())
        if pick not in by_idx:
            pick = int(fr.fight_index)
        if pick not in by_idx:
            return int(latest)
        return pick


_STORE_LOCK = threading.Lock()
_STORE_CACHE: dict[str, PayforwardRippleStore] = {}


def get_payforward_store(project_root: Path | str) -> PayforwardRippleStore:
    path = default_payforward_state_path(project_root)
    key = str(path.resolve()) if path.parent.exists() else str(path)
    with _STORE_LOCK:
        store = _STORE_CACHE.get(key)
        if store is None:
            store = PayforwardRippleStore(path)
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
    if not payforward_ripple_enabled(default=True):
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
    """Build reset options under fight-ripple mix, or None to use legacy."""
    if not payforward_ripple_enabled(default=True):
        return None
    if not cells:
        return None
    try:
        route_path = stage.get("route_path")
        route: list[dict[str, Any]] = []
        if route_path:
            rp = Path(project_root) / str(route_path)
            if rp.is_file():
                try:
                    raw = json.loads(rp.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        route = raw
                except (OSError, json.JSONDecodeError):
                    route = []
        if not route:
            route = _load_route(project_root)
        store = get_payforward_store(project_root)
        pick = store.choose_cell_index(cells, rng, route=route)
        if pick is None:
            return None
        by_idx = {int(r["checkpoint_index"]): r for r in cells}
        chosen = by_idx.get(int(pick))
        if chosen is None:
            return None
        start_index = int(chosen["checkpoint_index"]) + 1
        route_steps = list(stage.get("route_steps", []))
        remaining = max(1, len(route_steps) - start_index)
        return {
            "route_start_index": start_index,
            "leg_span": min(1, remaining),
            "reset_source": "route_cell",
            "pb_bundle": {
                "state_path": str(chosen["state_path"]),
                "sidecar_path": str(chosen["sidecar_path"]),
                "source": "yawn_rails",
            },
            "payforward_fight_ripple": True,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
