"""Planner-loyal hop score (±1 band): shadow telemetry + live settlement.

Live mode (``RE1_PLANNER_HOP_SCORE_V1=1`` / ``live``):
  S_success = S_base + B_kill with S_base in [0.20, 1.00];
  only kills (budget 2) may push above +1 (up to +1.50).
  divert -0.50, death -1.00, timeout -1.25.

Clipping is applied only when composing Q / B_kill. Raw (unclipped)
qualities and clip flags are always logged so overshoots are visible.
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

# ±1 success band (kill overshoot separate).
HOP_SUCCESS_FLOOR = 0.20
HOP_SUCCESS_SPAN = 0.80
W_HP = 0.40
W_AMMO = 0.35
W_HEAL = 0.15
W_TIME = 0.10
# Kill is NOT in Q — additive overshoot only.
K_BUDGET = 2  # kills for full B_kill
B_KILL_MAX = 0.50

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

FAIL_SCORE_DIVERT = -0.50
FAIL_SCORE_DEATH = -1.00
FAIL_SCORE_TIMEOUT = -1.25
# Back-compat aliases for older call sites / tests.
FAIL_SCORE_DEFAULT = FAIL_SCORE_DIVERT

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
DIVERT_FAILURES = frozenset(
    {
        "planner_divert",
        "wrong_room",
        "gallery_wrong",
        "armor_gas",
        "armor_inplace_statue_push",
        "main_hall_before_kenneth",
        "barry_return_before_kenneth",
        "capture_invalid",
        "forbidden_item",
        "shotgun_return",
        "unplanned_box",
        "unplanned_typewriter_save",
        "death_screen_ui",
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

# Dense / terminal keys replaced by hop_score under live.
LIVE_REPLACED_SCALAR_KEYS = frozenset(
    {
        "planner_step_success",
        "planner_divert",
        "planner_timeout",
        "death",
        "enemy_damage",
        "enemy_kill",
        "hp",
        "ammo_spend",
        "heal_use_tax",
        "weapon_reload",
        "step",
        "softlock",
        "gallery_wrong",
        "armor_gas",
        "armor_inplace_statue_push",
        "main_hall_before_kenneth",
    }
)

# Localized γ≈0 taxes that remain in the scalar stream under live hop-score.
HOP_LOCAL_SCALAR_KEYS = frozenset(
    {
        "attack_miss",
        "ammo_waste",
        "combat_overkill",
        "shotgun_dog_hit",
        "heavy_weapon_fodder_hit",
        "attack_dry_fire",
        "attack_macro_failure",
        "armor_statue_progress",
        "armor_approach",
        "dining_statue_progress",
    }
)

_MODE_ENV = "RE1_PLANNER_HOP_SCORE_V1"


def hop_score_mode() -> str:
    """Return ``off`` / ``shadow`` / ``live``."""
    raw = (os.environ.get(_MODE_ENV) or "").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return "off"
    if raw in {"1", "true", "on", "live"}:
        return "live"
    if raw == "shadow":
        return "shadow"
    from re1_rl.planner_loyal import planner_loyal_enabled

    if planner_loyal_enabled():
        # Fleet default is live ±1 settlement; set shadow explicitly to meter-only.
        return "live"
    return "off"


def hop_score_shadow_enabled() -> bool:
    return hop_score_mode() in {"shadow", "live"}


def hop_local_reward_from_bd(bd: dict[str, float] | None) -> float:
    """Sum localized γ≈0 channels still present under live hop-score."""
    if not bd:
        return 0.0
    total = 0.0
    for key in HOP_LOCAL_SCALAR_KEYS:
        total += float(bd.get(key, 0.0) or 0.0)
    return float(total)


def compose_hop_learning_target(S: float, L: float) -> float:
    """Per-step learning target from terminal hop score ``S`` and local ``L``.

    Default is ``S + L`` (γ_outcome=1 broadcast + γ_local=0). When a negative
    local tax lands on a *positive* hop outcome, the local **overrides** ``S``
    on that step only (``Y = L``) so misuse is not washed out by success.
    Positive locals never override a negative ``S``.
    """
    s = float(S)
    loc = float(L)
    if loc < 0.0 and s > 0.0:
        return loc
    return s + loc


def hop_score_live_enabled() -> bool:
    return hop_score_mode() == "live"


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


def _scored_kill_events(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Paid kill events (same filters as ``scored_kill_count``)."""
    events = state.get("combat_events")
    if not events:
        return []
    out: list[dict[str, Any]] = []
    for ev in events:
        if not ev.get("killed"):
            continue
        if ev.get("reward_denied") or ev.get("is_crow"):
            continue
        out.append(ev)
    return out


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


def _clip_flag(name: str, raw: float) -> str | None:
    if raw < 0.0:
        return f"{name}_lo"
    if raw > 1.0:
        return f"{name}_hi"
    return None


@dataclass
class PlannerHopMeters:
    """Per-hop gross meters for shadow / live hop score."""

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
    # (room_id, slot) already credited this hop — blocks death-anim / get-up
    # flicker from counting the same hostile twice (pl26 10A K=3 with E=2).
    _killed_slots: set[tuple[str, int]] = field(default_factory=set)

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

        room = str(state.get("room_id", "") or "")
        scored_events = _scored_kill_events(state)
        if scored_events:
            for ev in scored_events:
                slot = ev.get("slot")
                if slot is None:
                    self.k_scored += 1
                    continue
                key = (room, int(slot))
                if key in self._killed_slots:
                    continue
                self._killed_slots.add(key)
                self.k_scored += 1
        else:
            self.k_scored += scored_kill_count(state)
        raw = raw_vanish_kill_count(prev_state, state)
        self.k_raw_vanish += raw
        bogus = room_transition_bogus_kills(prev_state, state)
        self.k_transition_bogus += bogus

    def quality_bundle(self) -> dict[str, Any]:
        """Raw + clipped qualities, Q, B_kill, and clip flags (no silent cover-up)."""
        b_ammo = B_AMMO_BOSS if self.boss else B_AMMO_DEFAULT
        q_hp_raw = 1.0 - float(self.d_hp) / HP_DENOM
        q_ammo_raw = 1.0 - float(self.a_spent) / float(b_ammo)
        q_heal_raw = 1.0 - float(self.h_used) / 1.0
        q_time_raw = 1.0 - float(self.frames) / float(self.budget_frames)

        q_hp = _clip01(q_hp_raw)
        q_ammo = _clip01(q_ammo_raw)
        q_heal = _clip01(q_heal_raw)
        q_time = _clip01(q_time_raw)

        clip_flags: list[str] = []
        for name, raw in (
            ("q_hp", q_hp_raw),
            ("q_ammo", q_ammo_raw),
            ("q_heal", q_heal_raw),
            ("q_time", q_time_raw),
        ):
            flag = _clip_flag(name, raw)
            if flag:
                clip_flags.append(flag)

        q_kill_ratio_raw = (
            float(self.k_scored) / float(K_BUDGET) if int(self.e_start) > 0 else 0.0
        )
        if int(self.e_start) <= 0:
            b_kill_raw = 0.0
            b_kill = 0.0
        else:
            b_kill_raw = float(B_KILL_MAX) * float(q_kill_ratio_raw)
            b_kill = float(B_KILL_MAX) * _clip01(q_kill_ratio_raw)
            if q_kill_ratio_raw > 1.0:
                clip_flags.append("B_kill_hi")
            elif q_kill_ratio_raw < 0.0:
                clip_flags.append("B_kill_lo")

        # Legacy name in logs: fraction of kill budget used (clipped).
        q_kill = 0.0 if int(self.e_start) <= 0 else _clip01(q_kill_ratio_raw)

        q_mix = (
            W_HP * q_hp
            + W_AMMO * q_ammo
            + W_HEAL * q_heal
            + W_TIME * q_time
        )
        q_mix_raw = (
            W_HP * q_hp_raw
            + W_AMMO * q_ammo_raw
            + W_HEAL * q_heal_raw
            + W_TIME * q_time_raw
        )
        s_base = float(HOP_SUCCESS_FLOOR + HOP_SUCCESS_SPAN * q_mix)
        s_base_raw = float(HOP_SUCCESS_FLOOR + HOP_SUCCESS_SPAN * q_mix_raw)

        return {
            "q_hp_raw": q_hp_raw,
            "q_ammo_raw": q_ammo_raw,
            "q_heal_raw": q_heal_raw,
            "q_time_raw": q_time_raw,
            "q_kill_raw": q_kill_ratio_raw,
            "q_hp": q_hp,
            "q_ammo": q_ammo,
            "q_heal": q_heal,
            "q_time": q_time,
            "q_kill": q_kill,
            "Q_raw": q_mix_raw,
            "Q": q_mix,
            "B_kill_raw": b_kill_raw,
            "B_kill": b_kill,
            "S_base_raw": s_base_raw,
            "S_base": s_base,
            "clip_flags": clip_flags,
            "B_ammo": float(b_ammo),
            "K_budget": int(K_BUDGET),
        }

    def qualities(self) -> dict[str, float]:
        b = self.quality_bundle()
        return {
            "q_hp": float(b["q_hp"]),
            "q_ammo": float(b["q_ammo"]),
            "q_kill": float(b["q_kill"]),
            "q_heal": float(b["q_heal"]),
            "q_time": float(b["q_time"]),
        }

    def success_score(self) -> float:
        b = self.quality_bundle()
        return float(b["S_base"] + b["B_kill"])

    def fail_score(self, reason: str | None) -> float:
        key = str(reason or "").strip().lower()
        if key in TIMEOUT_FAILURES:
            return float(FAIL_SCORE_TIMEOUT)
        if key in DEATH_FAILURES:
            return float(FAIL_SCORE_DEATH)
        if key in DIVERT_FAILURES:
            return float(FAIL_SCORE_DIVERT)
        return float(FAIL_SCORE_DIVERT)

    def settle(
        self,
        *,
        outcome: str,
        failure: str | None = None,
    ) -> dict[str, Any]:
        """Idempotent settlement; returns a JSON-friendly report with raw math."""
        if self.settled and self.last_report:
            return dict(self.last_report)
        outcome_key = str(outcome or "").strip().lower()
        fail_key = str(failure or "").strip().lower() or None
        success = outcome_key in SUCCESS_OUTCOMES and not fail_key
        b = self.quality_bundle()
        if success:
            s = float(b["S_base"] + b["B_kill"])
            reason = outcome_key or "hop_success"
        else:
            reason = fail_key or outcome_key or "unknown"
            s = self.fail_score(reason)
        report = {
            "mode": hop_score_mode(),
            "tip": self.tip,
            "room_start": self.room_start,
            "boss": bool(self.boss),
            "outcome": reason,
            "success": bool(success),
            "S": round(float(s), 6),
            "S_base": round(float(b["S_base"]), 6),
            "S_base_raw": round(float(b["S_base_raw"]), 6),
            "B_kill": round(float(b["B_kill"]), 6),
            "B_kill_raw": round(float(b["B_kill_raw"]), 6),
            "Q": round(float(b["Q"]), 6),
            "Q_raw": round(float(b["Q_raw"]), 6),
            "q_hp": round(float(b["q_hp"]), 6),
            "q_ammo": round(float(b["q_ammo"]), 6),
            "q_kill": round(float(b["q_kill"]), 6),
            "q_heal": round(float(b["q_heal"]), 6),
            "q_time": round(float(b["q_time"]), 6),
            "q_hp_raw": round(float(b["q_hp_raw"]), 6),
            "q_ammo_raw": round(float(b["q_ammo_raw"]), 6),
            "q_kill_raw": round(float(b["q_kill_raw"]), 6),
            "q_heal_raw": round(float(b["q_heal_raw"]), 6),
            "q_time_raw": round(float(b["q_time_raw"]), 6),
            "clip_flags": ",".join(b["clip_flags"]) if b["clip_flags"] else "-",
            "E_start": int(self.e_start),
            "K": int(self.k_scored),
            "K_budget": int(b["K_budget"]),
            "K_raw_vanish": int(self.k_raw_vanish),
            "K_transition_bogus": int(self.k_transition_bogus),
            "D_hp": round(float(self.d_hp), 4),
            "A_spent": round(float(self.a_spent), 4),
            "B_ammo": round(float(b["B_ammo"]), 4),
            "H_used": round(float(self.h_used), 4),
            "F_elapsed": int(self.frames),
            "F_budget": int(self.budget_frames),
            "steps": int(self.steps),
        }
        self.settled = True
        self.last_report = report
        return dict(report)


def format_hop_score_shadow_line(report: dict[str, Any]) -> str:
    """One-line worker log for grepping ``[hop_score_shadow]`` / live."""
    tag = "[hop_score_live]" if hop_score_live_enabled() else "[hop_score_shadow]"
    return (
        f"{tag} tip={report.get('tip')!r} "
        f"outcome={report.get('outcome')!r} "
        f"S={float(report.get('S', 0.0)):.4f} "
        f"S_base={float(report.get('S_base', 0.0)):.4f} "
        f"S_base_raw={float(report.get('S_base_raw', 0.0)):.4f} "
        f"B_kill={float(report.get('B_kill', 0.0)):.4f} "
        f"B_kill_raw={float(report.get('B_kill_raw', 0.0)):.4f} "
        f"Q={float(report.get('Q', 0.0)):.4f} "
        f"Q_raw={float(report.get('Q_raw', 0.0)):.4f} "
        f"success={int(bool(report.get('success')))} "
        f"q_hp={float(report.get('q_hp', 0.0)):.4f} "
        f"q_hp_raw={float(report.get('q_hp_raw', 0.0)):.4f} "
        f"q_ammo={float(report.get('q_ammo', 0.0)):.4f} "
        f"q_ammo_raw={float(report.get('q_ammo_raw', 0.0)):.4f} "
        f"q_kill={float(report.get('q_kill', 0.0)):.4f} "
        f"q_kill_raw={float(report.get('q_kill_raw', 0.0)):.4f} "
        f"q_heal={float(report.get('q_heal', 0.0)):.4f} "
        f"q_heal_raw={float(report.get('q_heal_raw', 0.0)):.4f} "
        f"q_time={float(report.get('q_time', 0.0)):.4f} "
        f"q_time_raw={float(report.get('q_time_raw', 0.0)):.4f} "
        f"clip_flags={report.get('clip_flags', '-')!s} "
        f"E_start={int(report.get('E_start', 0) or 0)} "
        f"K={int(report.get('K', 0) or 0)} "
        f"K_budget={int(report.get('K_budget', K_BUDGET) or K_BUDGET)} "
        f"K_raw={int(report.get('K_raw_vanish', 0) or 0)} "
        f"K_bogus_transition={int(report.get('K_transition_bogus', 0) or 0)} "
        f"D_hp={float(report.get('D_hp', 0.0)):.2f} "
        f"A_spent={float(report.get('A_spent', 0.0)):.4f} "
        f"B_ammo={float(report.get('B_ammo', 0.0)):.4f} "
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


def zero_live_replaced_channels(bd: dict[str, float]) -> None:
    for key in LIVE_REPLACED_SCALAR_KEYS:
        bd[key] = 0.0
    # Telemetry aliases that mirrored terminals into the scalar path.
    # Do NOT clear checkpoint_success: it is a capture/freeze gate
    # (PLANNER_LOYAL_TELEMETRY_KEYS), not a reward channel. Zeroing it under
    # live hop-score prevented every mint after hop success.
    for key in ("wrong_room", "checkpoint_timeout"):
        if key in bd:
            bd[key] = 0.0


def apply_live_hop_score(
    bd: dict[str, float],
    meters: PlannerHopMeters | None,
    *,
    outcome: str,
    failure: str | None = None,
) -> dict[str, Any]:
    """Settle meters into ``bd['hop_score']`` and zero replaced dense/terminals.

    Idempotent if meters already settled. Always returns a report dict.
    """
    if meters is None:
        # No meters: still apply constant fail/success floor from outcome.
        fail_key = str(failure or outcome or "").strip().lower()
        if fail_key in SUCCESS_OUTCOMES or (
            failure is None and str(outcome).lower() in SUCCESS_OUTCOMES
        ):
            s = float(HOP_SUCCESS_FLOOR)
            report: dict[str, Any] = {
                "S": s,
                "outcome": outcome,
                "success": True,
                "tip": "",
            }
        else:
            tmp = PlannerHopMeters()
            s = float(tmp.fail_score(failure or outcome))
            report = {"S": s, "outcome": failure or outcome, "success": False, "tip": ""}
        zero_live_replaced_channels(bd)
        bd["hop_score"] = float(s)
        return report

    report = meters.settle(outcome=outcome, failure=failure)
    zero_live_replaced_channels(bd)
    bd["hop_score"] = float(report["S"])
    return report
