"""Planner-loyal hop score (shadow telemetry + future live settlement).

Shadow mode computes the redesign score without changing learning rewards.
Live mode (``RE1_PLANNER_HOP_SCORE_V1=1``) is not wired yet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from re1_rl.enemy_combat import (
    combat_reward_denied,
    enemy_combat_events,
    is_crow_enemy,
)

# Emulated-frame budgets (NTSC 60 fps).
PLANNER_DEFAULT_TIMEOUT_FRAMES = 6 * 60 * 60  # 21600 = 6 min
PLANNER_BOSS_TIMEOUT_FRAMES = 12 * 60 * 60  # 43200 = 12 min
PLANNER_DEFAULT_MAX_STEPS = PLANNER_DEFAULT_TIMEOUT_FRAMES // 8  # 2700
PLANNER_BOSS_MAX_STEPS = PLANNER_BOSS_TIMEOUT_FRAMES // 8  # 5400

HOP_SUCCESS_FLOOR = 0.25
HOP_SUCCESS_SPAN = 3.75
W_HP = 0.30
W_AMMO = 0.25
W_KILL = 0.25
W_HEAL = 0.10
W_TIME = 0.10

HP_BAND_LO = 1
HP_BAND_HI = 96
HP_DENOM = 95.0

AMMO_COST_PER_ROUND: dict[int, float] = {
    0x01: 0.0,  # knife
    0x02: 0.04,  # handgun
    0x03: 0.25,  # shotgun
    0x04: 0.40,  # dumdum
    0x05: 0.40,  # magnum
    0x06: 0.0,  # flamethrower
    0x07: 0.40,  # acid
    0x08: 0.40,  # explosive
    0x09: 0.40,  # flame
    0x0A: 0.75,  # rocket
}
B_AMMO_DEFAULT = 1.0
B_AMMO_BOSS = 4.0

HEAL_UNITS: dict[str, float] = {
    "blue_herb": 0.17,
    "green_herb": 0.33,
    "red_herb": 0.66,
    "mixed_herbs_gb": 0.52,
    "mixed_herbs_gg": 0.68,
    "mixed_herbs_ggb": 0.87,
    "first_aid_spray": 1.00,
    "first_aid_spray_alt": 1.00,
    "mixed_herbs_gr": 1.01,
    "mixed_herbs_ggg": 1.03,
    "mixed_herbs_grb": 1.20,
}

FAIL_SCORE_DEFAULT = -4.0
FAIL_SCORE_TIMEOUT = -6.0
TIMEOUT_FAILURES = frozenset(
    {
        "planner_timeout",
        "checkpoint_timeout",
        "softlock",
        "stagnation",
        "max_steps",
    }
)
DEATH_FAILURES = frozenset(
    {
        "death",
        "hp_death",
        "dead",
    }
)
SUCCESS_OUTCOMES = frozenset(
    {
        "hop_success",
        "planner_chunk_complete",
        "checkpoint_success",
        "planner_step_success",
    }
)

_MODE_ENV = "RE1_PLANNER_HOP_SCORE_V1"


def hop_score_mode() -> str:
    """Return ``off`` / ``shadow`` / ``live``.

    Default for planner-loyal workers is ``shadow`` so we collect end-of-hop
    telemetry without changing rewards. Set ``0`` to disable, ``1`` for live
    (not implemented yet — still behaves as shadow).
    """
    raw = (os.environ.get(_MODE_ENV) or "").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return "off"
    if raw in {"1", "true", "on", "live"}:
        # Live settlement is not hooked into reward yet; keep computing.
        return "live"
    if raw == "shadow":
        return "shadow"
    from re1_rl.planner_loyal import planner_loyal_enabled

    if planner_loyal_enabled():
        return "shadow"
    return "off"


def hop_score_shadow_enabled() -> bool:
    return hop_score_mode() in {"shadow", "live"}


def current_step_is_boss(queue: Any) -> bool:
    if queue is None:
        return False
    from re1_rl.planner_loyal import read_queue_current

    cur = read_queue_current(queue)
    return str(cur.get("op") or "").strip().lower() == "boss"


def planner_timeout_frames(*, boss: bool = False) -> int:
    return (
        int(PLANNER_BOSS_TIMEOUT_FRAMES)
        if boss
        else int(PLANNER_DEFAULT_TIMEOUT_FRAMES)
    )


def planner_max_steps_extension(*, boss: bool = False) -> int:
    return int(PLANNER_BOSS_MAX_STEPS) if boss else int(PLANNER_DEFAULT_MAX_STEPS)


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _hp_in_band(hp: int) -> bool:
    return HP_BAND_LO <= int(hp) <= HP_BAND_HI


def _combat_hp(ent: dict[str, Any]) -> int:
    if ent.get("yawn_translated") and "hp_raw" in ent:
        return max(0, int(ent.get("hp_raw") or 0))
    return int(ent.get("hp", 0) or 0)


def count_scorable_hostiles(
    enemies: list[dict[str, Any]] | None,
    *,
    room_id: str | None,
) -> int:
    """Living non-pest hostiles that can contribute to ``E_start`` / ``K``."""
    n = 0
    for ent in enemies or []:
        if ent.get("yawn_part"):
            continue
        if not ent.get("alive", True):
            continue
        if _combat_hp(ent) <= 0:
            continue
        if is_crow_enemy(ent, room_id=room_id):
            continue
        if combat_reward_denied(
            room_id=room_id,
            type_id=ent.get("type_id"),
            type_name=ent.get("type_name"),
        ):
            continue
        n += 1
    return n


def scored_kill_count(state: dict[str, Any]) -> int:
    """Kills that match today's paid ``enemy_kill`` channel (post room-gate)."""
    events = state.get("combat_events")
    if events:
        n = 0
        for ev in events:
            if not ev.get("killed"):
                continue
            if ev.get("reward_denied") or ev.get("is_crow"):
                continue
            n += 1
        return n
    return max(0, int(state.get("enemy_kills", 0) or 0))


def raw_vanish_kill_count(
    prev_state: dict[str, Any],
    state: dict[str, Any],
) -> int:
    """Naive HP-vanish kills ignoring room-change / interact gates (audit)."""
    prev_room = str(prev_state.get("room_id", "") or "")
    room = prev_room or str(state.get("room_id", "") or "")
    events = enemy_combat_events(
        list(prev_state.get("enemies") or []),
        list(state.get("enemies") or []),
        room_id=room,
    )
    return sum(
        1
        for ev in events
        if ev.get("killed") and not ev.get("is_crow")
    )


def room_transition_bogus_kills(
    prev_state: dict[str, Any],
    state: dict[str, Any],
) -> int:
    """HP vanishes across a room change — never score these as ``K``."""
    prev_room = str(prev_state.get("room_id", "") or "")
    curr_room = str(state.get("room_id", "") or "")
    if not prev_room or not curr_room or prev_room == curr_room:
        return 0
    return raw_vanish_kill_count(prev_state, state)


def _ammo_cost(weapon_id: int, rounds: int) -> float:
    wid = int(weapon_id) & 0xFF
    n = max(0, int(rounds))
    if n <= 0:
        return 0.0
    return float(AMMO_COST_PER_ROUND.get(wid, 0.04)) * float(n)


def _heal_units_used(prev_state: dict[str, Any], state: dict[str, Any]) -> float:
    from re1_rl.planner_loyal import _inventory_qty_totals

    prev = _inventory_qty_totals(prev_state)
    cur = _inventory_qty_totals(state)
    used = 0.0
    for name, before in prev.items():
        after = cur.get(name, 0)
        if after >= before:
            continue
        lost = before - after
        unit = HEAL_UNITS.get(str(name))
        if unit is None:
            continue
        used += float(unit) * float(lost)
    return used


@dataclass
class PlannerHopMeters:
    """Per-hop gross meters for shadow / future live hop score."""

    tip: str = ""
    room_start: str = ""
    boss: bool = False
    budget_frames: int = PLANNER_DEFAULT_TIMEOUT_FRAMES
    e_start: int = 0
    d_hp: float = 0.0
    a_spent: float = 0.0
    h_used: float = 0.0
    k_scored: int = 0
    k_raw_vanish: int = 0
    k_transition_bogus: int = 0
    frames: int = 0
    steps: int = 0
    settled: bool = False
    last_report: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def begin(
        cls,
        state: dict[str, Any],
        *,
        tip: str = "",
        boss: bool = False,
        budget_frames: int | None = None,
    ) -> PlannerHopMeters:
        room = str(state.get("room_id", "") or "")
        budget = (
            int(budget_frames)
            if budget_frames is not None
            else planner_timeout_frames(boss=boss)
        )
        return cls(
            tip=str(tip or ""),
            room_start=room,
            boss=bool(boss),
            budget_frames=max(1, budget),
            e_start=count_scorable_hostiles(
                list(state.get("enemies") or []),
                room_id=room,
            ),
        )

    def note_step(
        self,
        prev_state: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        if self.settled:
            return
        self.steps += 1
        self.frames += max(0, int(state.get("step_emulated_frames", 0) or 0))

        prev_hp = int(prev_state.get("hp", 0) or 0)
        hp = int(state.get("hp", 0) or 0)
        if _hp_in_band(prev_hp) and _hp_in_band(hp) and hp < prev_hp:
            self.d_hp += float(prev_hp - hp)

        ammo_spent = int(state.get("ammo_spent", 0) or 0)
        if ammo_spent > 0:
            wid = int(
                state.get("pending_miss_weapon_id")
                or state.get("equipped_weapon_id")
                or prev_state.get("equipped_weapon_id")
                or 0
            )
            self.a_spent += _ammo_cost(wid, ammo_spent)

        self.h_used += _heal_units_used(prev_state, state)

        scored = scored_kill_count(state)
        self.k_scored += scored
        raw = raw_vanish_kill_count(prev_state, state)
        self.k_raw_vanish += raw
        bogus = room_transition_bogus_kills(prev_state, state)
        self.k_transition_bogus += bogus

    def qualities(self) -> dict[str, float]:
        q_hp = _clip01(1.0 - float(self.d_hp) / HP_DENOM)
        b_ammo = B_AMMO_BOSS if self.boss else B_AMMO_DEFAULT
        q_ammo = _clip01(1.0 - float(self.a_spent) / float(b_ammo))
        if int(self.e_start) <= 0:
            q_kill = 1.0
        else:
            q_kill = _clip01(float(self.k_scored) / float(self.e_start))
        q_heal = _clip01(1.0 - float(self.h_used) / 1.0)
        q_time = _clip01(1.0 - float(self.frames) / float(self.budget_frames))
        return {
            "q_hp": q_hp,
            "q_ammo": q_ammo,
            "q_kill": q_kill,
            "q_heal": q_heal,
            "q_time": q_time,
        }

    def success_score(self) -> float:
        q = self.qualities()
        mixed = (
            W_HP * q["q_hp"]
            + W_AMMO * q["q_ammo"]
            + W_KILL * q["q_kill"]
            + W_HEAL * q["q_heal"]
            + W_TIME * q["q_time"]
        )
        return float(HOP_SUCCESS_FLOOR + HOP_SUCCESS_SPAN * mixed)

    def fail_score(self, reason: str | None) -> float:
        key = str(reason or "").strip().lower()
        if key in TIMEOUT_FAILURES:
            return float(FAIL_SCORE_TIMEOUT)
        if key in DEATH_FAILURES:
            return float(FAIL_SCORE_DEFAULT)
        return float(FAIL_SCORE_DEFAULT)

    def settle(
        self,
        *,
        outcome: str,
        failure: str | None = None,
    ) -> dict[str, Any]:
        """Idempotent settlement; returns a JSON-friendly report."""
        if self.settled and self.last_report:
            return dict(self.last_report)
        outcome_key = str(outcome or "").strip().lower()
        fail_key = str(failure or "").strip().lower() or None
        success = outcome_key in SUCCESS_OUTCOMES and not fail_key
        if success:
            s = self.success_score()
            reason = outcome_key or "hop_success"
        else:
            reason = fail_key or outcome_key or "unknown"
            s = self.fail_score(reason)
        q = self.qualities()
        report = {
            "mode": hop_score_mode(),
            "tip": self.tip,
            "room_start": self.room_start,
            "boss": bool(self.boss),
            "outcome": reason,
            "success": bool(success),
            "S": round(float(s), 6),
            "q_hp": round(q["q_hp"], 6),
            "q_ammo": round(q["q_ammo"], 6),
            "q_kill": round(q["q_kill"], 6),
            "q_heal": round(q["q_heal"], 6),
            "q_time": round(q["q_time"], 6),
            "E_start": int(self.e_start),
            "K": int(self.k_scored),
            "K_raw_vanish": int(self.k_raw_vanish),
            "K_transition_bogus": int(self.k_transition_bogus),
            "D_hp": round(float(self.d_hp), 4),
            "A_spent": round(float(self.a_spent), 4),
            "H_used": round(float(self.h_used), 4),
            "F_elapsed": int(self.frames),
            "F_budget": int(self.budget_frames),
            "steps": int(self.steps),
        }
        self.settled = True
        self.last_report = report
        return dict(report)


def format_hop_score_shadow_line(report: dict[str, Any]) -> str:
    """One-line worker log for grepping ``[hop_score_shadow]``."""
    return (
        f"[hop_score_shadow] tip={report.get('tip')!r} "
        f"outcome={report.get('outcome')!r} "
        f"S={float(report.get('S', 0.0)):.4f} "
        f"success={int(bool(report.get('success')))} "
        f"q_hp={float(report.get('q_hp', 0.0)):.4f} "
        f"q_ammo={float(report.get('q_ammo', 0.0)):.4f} "
        f"q_kill={float(report.get('q_kill', 0.0)):.4f} "
        f"q_heal={float(report.get('q_heal', 0.0)):.4f} "
        f"q_time={float(report.get('q_time', 0.0)):.4f} "
        f"E_start={int(report.get('E_start', 0) or 0)} "
        f"K={int(report.get('K', 0) or 0)} "
        f"K_raw={int(report.get('K_raw_vanish', 0) or 0)} "
        f"K_bogus_transition={int(report.get('K_transition_bogus', 0) or 0)} "
        f"D_hp={float(report.get('D_hp', 0.0)):.2f} "
        f"A_spent={float(report.get('A_spent', 0.0)):.4f} "
        f"H_used={float(report.get('H_used', 0.0)):.4f} "
        f"frames={int(report.get('F_elapsed', 0) or 0)}/"
        f"{int(report.get('F_budget', 0) or 0)} "
        f"boss={int(bool(report.get('boss')))}"
    )


def resolve_shadow_outcome(
    *,
    episode_failure: str | None,
    terminated: bool,
    truncated: bool,
    breakdown: dict[str, float] | None = None,
) -> tuple[str, str | None]:
    """Map env terminal fields to ``(outcome, failure_or_none)``."""
    bd = breakdown or {}
    fail = str(episode_failure or "").strip() or None
    if fail in SUCCESS_OUTCOMES:
        return fail, None
    if fail:
        return fail, fail
    if float(bd.get("planner_step_success", 0.0) or 0.0) > 0.0:
        return "planner_step_success", None
    if float(bd.get("checkpoint_success", 0.0) or 0.0) > 0.0:
        return "checkpoint_success", None
    if truncated:
        return "planner_timeout", "planner_timeout"
    if terminated:
        return "unknown", "unknown"
    return "ongoing", None
