"""Verbose failed-attack logging for hierarchical RE1 control.

Standalone module — ``env`` calls :class:`AttackTelemetry` after attack macros.
Disable console output with env var ``ATTACK_LOG=0`` (mirrors ``KNIFE_ANIM_LOG``).
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

# Log failed attacks when truthy (default on). Set ATTACK_LOG=0 to silence.
ATTACK_LOG_ENABLED = os.environ.get("ATTACK_LOG", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

_MISS_OUTCOMES = frozenset(
    {
        "aim_timeout",
        "settle_timeout",
        "no_damage",
        "aborted_interrupt",
        "no_weapon",
    }
)


class AttackTelemetry:
    """Per-episode attack counters and structured miss logging."""

    def __init__(self, port: Any = "?") -> None:
        self.port = port
        self.reset_episode()

    def reset_episode(self) -> None:
        self.attacks_total = 0
        self.attacks_hit = 0
        self.attacks_missed = 0
        self.misses_by_weapon: Counter[str] = Counter()
        self.misses_by_outcome: Counter[str] = Counter()

    def record(
        self,
        action_name: str,
        weapon: str | None,
        outcome: str,
        *,
        macro_report: dict | None = None,
        enemy_damage: int = 0,
        enemy_kills: int = 0,
        ammo_spent: int = 0,
        state: dict | None = None,
    ) -> dict[str, Any]:
        """Record one attack attempt; print on miss when logging is enabled."""
        self.attacks_total += 1
        weapon_label = weapon or "?"
        state = state or {}

        is_hit = (
            enemy_damage > 0
            or enemy_kills > 0
            or outcome == "hit"
        )
        is_miss = not is_hit and (
            outcome in _MISS_OUTCOMES or outcome != "hit"
        )

        if is_hit:
            self.attacks_hit += 1
        elif is_miss:
            self.attacks_missed += 1
            self.misses_by_weapon[weapon_label] += 1
            self.misses_by_outcome[outcome] += 1
            self._log_miss(
                weapon=weapon_label,
                outcome=outcome,
                ammo_spent=int(ammo_spent),
                state=state,
                macro_report=macro_report,
            )

        return {
            "action_name": action_name,
            "weapon": weapon_label,
            "outcome": outcome,
            "hit": is_hit,
            "missed": is_miss,
            "attack_missed": is_miss,
            "ammo_spent": int(ammo_spent) if is_miss else 0,
            "attack_weapon": weapon_label,
        }

    def episode_summary(self) -> dict[str, Any]:
        total = self.attacks_total
        hit_rate = self.attacks_hit / total if total else 0.0
        return {
            "attacks_total": self.attacks_total,
            "attacks_hit": self.attacks_hit,
            "attacks_missed": self.attacks_missed,
            "hit_rate": hit_rate,
            "misses_by_weapon": dict(self.misses_by_weapon),
            "misses_by_outcome": dict(self.misses_by_outcome),
        }

    def _log_miss(
        self,
        *,
        weapon: str,
        outcome: str,
        ammo_spent: int,
        state: dict,
        macro_report: dict | None,
    ) -> None:
        if not ATTACK_LOG_ENABLED:
            return

        report = macro_report or {}
        issues = report.get("issues") or []
        pre_state = report.get("pre_state") or {}
        hooks = pre_state.get("hooks", "?")
        room = state.get("room_id", "?")
        x = state.get("x", "?")
        z = state.get("z", "?")
        enemies = state.get("enemies") or []

        print(
            f"[attack_fail] port={self.port} weapon={weapon} outcome={outcome} "
            f"dmg=0 kills=0 ammo_spent={ammo_spent} room={room} "
            f"pos=({x},{z}) enemies={len(enemies)} "
            f"issues={len(issues)} hooks={hooks}",
            flush=True,
        )
        if issues:
            print("; ".join(str(i) for i in issues[:3]), flush=True)
