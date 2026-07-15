"""Attack macro logging and per-episode counters for hierarchical RE1 control.

``env`` calls :class:`AttackTelemetry` after each knife/attack macro and reward.
Disable console output with env var ``ATTACK_LOG=0`` (mirrors ``KNIFE_ANIM_LOG``).
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

# Log every attack swing when truthy (default on). Set ATTACK_LOG=0 to silence.
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
    """Per-episode attack counters and structured swing logging."""

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
        prev_state: dict | None = None,
        reward: float | None = None,
        reward_breakdown: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Record one attack attempt; log every swing when logging is enabled."""
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

        self._log_swing(
            action_name=action_name,
            weapon=weapon_label,
            outcome=outcome,
            hit=is_hit,
            ammo_spent=int(ammo_spent),
            enemy_damage=int(enemy_damage),
            enemy_kills=int(enemy_kills),
            state=state,
            prev_state=prev_state,
            macro_report=macro_report,
            reward=reward,
            reward_breakdown=reward_breakdown,
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
            "attacks_total": total,
            "attacks_hit": self.attacks_hit,
            "attacks_missed": self.attacks_missed,
            "hit_rate": hit_rate,
            "misses_by_weapon": dict(self.misses_by_weapon),
            "misses_by_outcome": dict(self.misses_by_outcome),
        }

    def _log_swing(
        self,
        *,
        action_name: str,
        weapon: str,
        outcome: str,
        hit: bool,
        ammo_spent: int,
        enemy_damage: int,
        enemy_kills: int,
        state: dict,
        prev_state: dict | None,
        macro_report: dict | None,
        reward: float | None,
        reward_breakdown: dict[str, float] | None,
    ) -> None:
        if not ATTACK_LOG_ENABLED:
            return

        report = macro_report or {}
        issues = report.get("issues") or []
        pre_state = report.get("pre_state") or {}
        hooks = pre_state.get("hooks", "?")
        frames = report.get("frames", "?")
        room = state.get("room_id", "?")
        x = state.get("x", "?")
        z = state.get("z", "?")
        enemies = state.get("enemies") or []
        bd = reward_breakdown or {}
        rew_s = "?" if reward is None else f"{float(reward):+.6f}"
        step_r = bd.get("step", 0.0)
        dmg_r = bd.get("enemy_damage", 0.0)
        kill_r = bd.get("enemy_kill", 0.0)

        print(
            f"[attack_swing] port={self.port} action={action_name} weapon={weapon} "
            f"hit={int(hit)} outcome={outcome} dmg={enemy_damage} kills={enemy_kills} "
            f"ammo={ammo_spent} reward={rew_s} step={step_r:+.6f} "
            f"bd_enemy_dmg={dmg_r:+.6f} bd_kill={kill_r:+.6f} "
            f"room={room} pos=({x},{z}) enemies={len(enemies)} "
            f"frames={frames} issues={len(issues)} hooks={hooks}",
            flush=True,
        )
        if issues:
            print("; ".join(str(i) for i in issues[:3]), flush=True)
        try:
            from re1_rl.attack_log_context import (
                build_attack_log_context,
                format_attack_context_line,
            )

            ctx = build_attack_log_context(prev_state, state)
            print(format_attack_context_line(ctx), flush=True)
        except (ImportError, TypeError, ValueError, KeyError):
            pass
