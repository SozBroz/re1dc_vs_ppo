"""Knife swing input script (one env decision).

RE1 (PS1) runs game logic at 30 fps: the engine samples the pad on every
OTHER emulated (~60 fps) frame. Button phases shorter than 2 emulated frames
can be invisible to the game.

Default path (``use_ram_gates=True``): RAM-gated macro using hunt-confirmed
player animation bytes (see ``memory_map``):
  0. Release all buttons; wait until settled (neutral idle or weapon-ready standing).
     If crouch aim (0x12/0x04/0) appears during settle, skip the aim phase.
  1. Hold R1+down until crouch-knife aim (anim 0x12, aux 0x04).
  2. Press cross on the first frame after aim is stable (2 emu frames).
  3. Hold cross through minimum swing duration, then R1+down through recovery
     until anim/aux return idle (0).
  4. Abort early (release buttons) if Jill enters a non-knife animation
     (likely hit/stagger) or crouch aim never stabilizes (cannot swing).

RAM-gated runs validate animation hooks each frame. Mismatches log as
``[knife_anim] port=...`` (disable with env ``KNIFE_ANIM_LOG=0``).

Legacy fixed schedule (``use_ram_gates=False``): blind 5/5/11 game-frame phases.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from typing import Any

from re1_rl.memory_map import (
    PLAYER_ACTION_AUX,
    PLAYER_ANIM_STATE,
    PLAYER_HP,
    PLAYER_RECOVERY_TIMER,
    player_died,
)

# Emulated frames per game-logic frame (PS1 RE1: 30fps logic on ~60fps video).
KNIFE_FRAME_SCALE = 2

# Phase lengths in GAME frames (operator-measured 5 / 5 / 11 = 21).
KNIFE_AIM_GAME_FRAMES = 5
KNIFE_SWING_GAME_FRAMES = 5
KNIFE_RECOVERY_GAME_FRAMES = 11

# Hunt-confirmed crouch knife aim (scripts/hunt_player_knife_anim.py).
CROUCH_KNIFE_AIM_ANIM = 0x12
CROUCH_KNIFE_ACTIVE_AUX = 0x04
KNIFE_RECOVERY_ANIM = 0x13
STANDING_KNIFE_ANIM = 0x14  # seen during some swings; still knife track
CROUCH_KNIFE_POST_ANIM = 0x15  # crouch settle after swing (live QA, aux 0x04)

# Standing weapon-idle hooks seen before knife aim in live fleet (not knife track bytes).
STANDING_PRE_KNIFE_HOOKS: frozenset[tuple[int, int]] = frozenset(
    {
        (0x01, 0x00),
        (0x02, 0x00),
        (0x03, 0x00),
        (0x04, 0x00),
        (0x05, 0x00),
        # 0x06 excluded — fleet interrupts correlate with locomotion, not standing idle
        (0x07, 0x00),
        (0x08, 0x00),
        (0x0D, 0x01),
    }
)

# Minimum emulated frames any button phase must hold (30fps pad sampling).
MIN_BUTTON_PHASE_FRAMES = 2

# Max emulated frames to wait in settle (neutral or crouch aim) before abort.
KNIFE_SETTLE_MAX_WAIT_FRAMES = 32

# Allowed deviation when comparing observed swing/recovery anim frame counts.
KNIFE_ANIM_FRAME_TOLERANCE = 2

# One RL step burns this many emulated frames when action == knife_swing (fixed schedule).
KNIFE_MACRO_FRAMES = (
    KNIFE_AIM_GAME_FRAMES + KNIFE_SWING_GAME_FRAMES + KNIFE_RECOVERY_GAME_FRAMES
) * KNIFE_FRAME_SCALE

AIM_BUTTONS = {"r1": True, "down": True}
SWING_BUTTONS = {"r1": True, "down": True, "cross": True}
STANDING_AIM_BUTTONS = {"r1": True}
STANDING_SWING_BUTTONS = {"r1": True, "cross": True}
QUICK_KNIFE_BUTTONS = {"cross": True}

_HOOK_FIELDS = [
    ("player_anim", PLAYER_ANIM_STATE, "u8"),
    ("player_action_aux", PLAYER_ACTION_AUX, "u8"),
    ("player_recovery_timer", PLAYER_RECOVERY_TIMER, "u8"),
]

# Log animation mismatches when truthy (default on). Set KNIFE_ANIM_LOG=0 to silence.
KNIFE_ANIM_LOG_ENABLED = os.environ.get("KNIFE_ANIM_LOG", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

# Acceptable animation labels per macro phase (hunt-confirmed crouch knife track).
_SETTLE_OK = frozenset(
    {
        "idle",
        "idle_recovery_latch",
        "standing_idle",
        "standing_recovery_latch",
        "locomotion",
        "crouch_transitional",
        "crouch_aim",
    }
)
_AIM_OK = frozenset(
    {
        "idle",
        "crouch_transitional",
        "crouch_aim",
        "crouch_post",
        "idle_recovery_latch",
        "standing_idle",
        "standing_recovery_latch",
        "standing_knife",
        "swing_recovery",
    }
)
_SWING_OK = frozenset(
    {
        "crouch_aim",
        "crouch_transitional",
        "crouch_post",
        "swing_recovery",
        "standing_knife",
        "idle",
    }
)
_RECOVERY_OK = frozenset(
    {
        "crouch_aim",
        "crouch_transitional",
        "crouch_post",
        "swing_recovery",
        "standing_knife",
        "idle",
    }
)


def knife_macro_frame_count() -> int:
    return KNIFE_MACRO_FRAMES


def expected_swing_recovery_emu_frames(
    swing_game: int = KNIFE_SWING_GAME_FRAMES,
    recovery_game: int = KNIFE_RECOVERY_GAME_FRAMES,
    scale: int = KNIFE_FRAME_SCALE,
) -> tuple[int, int]:
    """(min_swing_emu, recovery_emu) animation budgets from game-frame phase lengths."""
    swing_emu = max(MIN_BUTTON_PHASE_FRAMES, swing_game * scale)
    recovery_emu = recovery_game * scale
    return swing_emu, recovery_emu


def _is_swing_anim_label(label: str) -> bool:
    return label in ("standing_knife", "swing_recovery")


def _is_recovery_anim_label(label: str) -> bool:
    return label in ("swing_recovery", "standing_knife", "crouch_transitional")


def _frame_count_tolerance(expect: int) -> int:
    """Scale tolerance down for short phases (2-emu test macros)."""
    return max(2, min(KNIFE_ANIM_FRAME_TOLERANCE + 1, max(2, expect // 4)))


def read_knife_hooks(bridge: Any) -> tuple[int, int, int]:
    """Return (anim_state, action_aux, recovery_timer)."""
    raw = bridge.read_ram(_HOOK_FIELDS)
    return (
        int(raw["player_anim"]),
        int(raw["player_action_aux"]),
        int(raw["player_recovery_timer"]),
    )


def read_pre_knife_state(
    bridge: Any, *, hooks: tuple[int, int, int] | None = None
) -> dict[str, Any]:
    """RAM snapshot immediately before knife aim inputs (macro entry diagnostics)."""
    from re1_rl.memory_map import PLAYER_FACING

    if hooks is None:
        anim, aux, recovery = read_knife_hooks(bridge)
    else:
        anim, aux, recovery = hooks
    label = classify_knife_anim(anim, aux, recovery)
    facing: int | None = None
    if hooks is None:
        try:
            facing_raw = bridge.read_ram([("player_facing", PLAYER_FACING, "u16")])
            facing = int(facing_raw["player_facing"])
        except (KeyError, TypeError, AssertionError):
            pass
    return {
        "anim": anim,
        "aux": aux,
        "recovery": recovery,
        "label": label,
        "hooks": format_knife_hooks(anim, aux, recovery),
        "knife_action_ready": knife_action_ready(anim, aux, recovery),
        "knife_blocked": knife_action_blocked_by_recovery(anim, aux, recovery),
        "player_facing": facing,
    }


def format_knife_hooks(anim: int, aux: int, recovery: int) -> str:
    return f"anim=0x{anim:02X} aux=0x{aux:02X} recovery={recovery}"


def is_idle_recovery_latch(anim: int, aux: int, recovery: int) -> bool:
    """Anim returned neutral but recovery timer still draining (pre-action latch)."""
    return anim == 0 and aux == 0 and recovery > 0


def is_standing_pre_knife_hook(anim: int, aux: int) -> bool:
    return (anim, aux) in STANDING_PRE_KNIFE_HOOKS


def is_standing_recovery_latch(anim: int, aux: int, recovery: int) -> bool:
    """Standing weapon-idle with recovery timer still draining."""
    return recovery > 0 and is_standing_pre_knife_hook(anim, aux)


def is_standing_pre_knife_idle(anim: int, aux: int, recovery: int) -> bool:
    """Weapon-equipped standing idle, recovery timer clear."""
    return recovery == 0 and is_standing_pre_knife_hook(anim, aux)


def is_pre_knife_recovery_pending(anim: int, aux: int, recovery: int) -> bool:
    """Recovery latch on neutral or standing idle — wait before aim, do not abort."""
    return is_idle_recovery_latch(anim, aux, recovery) or is_standing_recovery_latch(
        anim, aux, recovery
    )


def knife_action_blocked_by_recovery(anim: int, aux: int, recovery: int) -> bool:
    """True when a new knife_swing should be masked (prior recovery still active)."""
    if is_pre_knife_recovery_pending(anim, aux, recovery):
        return True
    if anim == KNIFE_RECOVERY_ANIM and aux in (0, CROUCH_KNIFE_ACTIVE_AUX):
        return True
    if anim == CROUCH_KNIFE_POST_ANIM and aux == CROUCH_KNIFE_ACTIVE_AUX:
        return True
    if (
        anim == CROUCH_KNIFE_AIM_ANIM
        and aux == CROUCH_KNIFE_ACTIVE_AUX
        and recovery > 0
    ):
        return True
    return False


def is_knife_settle_complete(anim: int, aux: int, recovery: int) -> bool:
    """Neutral pad drained enough to start aim (idle or weapon-ready standing)."""
    return is_knife_animation_idle(anim, aux, recovery) or is_standing_pre_knife_idle(
        anim, aux, recovery
    )


def is_knife_settle_wait_state(anim: int, aux: int, recovery: int) -> bool:
    """RAM states expected while neutral pad drains before aim."""
    if is_knife_settle_complete(anim, aux, recovery):
        return True
    if is_idle_recovery_latch(anim, aux, recovery):
        return True
    if is_standing_recovery_latch(anim, aux, recovery):
        return True
    if anim == 0x06 and aux == 0:
        return True
    if anim == CROUCH_KNIFE_AIM_ANIM and aux in (0, CROUCH_KNIFE_ACTIVE_AUX):
        return True
    return False


def knife_action_ready(anim: int, aux: int, recovery: int) -> bool:
    """True when RAM says knife_swing may start (whitelist, fleet-confirmed)."""
    if knife_action_blocked_by_recovery(anim, aux, recovery):
        return False
    return is_knife_animation_idle(anim, aux, recovery) or is_standing_pre_knife_idle(
        anim, aux, recovery
    )


def classify_knife_anim(anim: int, aux: int, recovery: int) -> str:
    """Human-readable knife animation bucket for validation logs."""
    if is_knife_animation_idle(anim, aux, recovery):
        return "idle"
    if is_idle_recovery_latch(anim, aux, recovery):
        return "idle_recovery_latch"
    if is_standing_recovery_latch(anim, aux, recovery):
        return "standing_recovery_latch"
    if is_standing_pre_knife_idle(anim, aux, recovery):
        return "standing_idle"
    if is_crouch_knife_aim_ready(anim, aux, recovery):
        return "crouch_aim"
    if anim == KNIFE_RECOVERY_ANIM:
        return "swing_recovery"
    if anim == STANDING_KNIFE_ANIM:
        return "standing_knife"
    if anim == CROUCH_KNIFE_AIM_ANIM:
        return "crouch_transitional"
    if anim == CROUCH_KNIFE_POST_ANIM and aux == CROUCH_KNIFE_ACTIVE_AUX:
        return "crouch_post"
    if anim == 0x06 and aux == 0:
        return "locomotion"
    return "foreign"


def _phase_allows_state(phase: str, label: str) -> bool:
    if phase == "settle":
        return label in _SETTLE_OK
    if phase == "aim":
        return label in _AIM_OK
    if phase == "swing":
        return label in _SWING_OK
    if phase == "recovery":
        return label in _RECOVERY_OK
    return True


class KnifeAnimValidator:
    """Tracks expected crouch-knife animation milestones; logs only on mismatch."""

    def __init__(
        self,
        bridge: Any,
        *,
        swing_game: int = KNIFE_SWING_GAME_FRAMES,
        recovery_game: int = KNIFE_RECOVERY_GAME_FRAMES,
        scale: int = KNIFE_FRAME_SCALE,
    ) -> None:
        self._port = getattr(bridge, "port", "?")
        self.phase = "settle"
        self.frame = 0
        self.saw_settled_idle = False
        self.saw_crouch_aim = False
        self.saw_swing_anim = False
        self.saw_idle_end = False
        expect_swing, expect_recovery = expected_swing_recovery_emu_frames(
            swing_game, recovery_game, scale
        )
        self._expect_swing = expect_swing
        self._expect_recovery = expect_recovery
        self._swing_anim_frames = 0
        self._recovery_anim_frames = 0
        self._standing_knife_frames = 0
        self._swing_recovery_frames = 0
        self._knife_anim_since_aim = 0
        self._issues: list[str] = []
        self._logged: set[str] = set()
        self._trail: list[str] = []
        self._trail_cap = 16
        self.pre_state: dict[str, Any] | None = None

    def set_phase(self, phase: str) -> None:
        if phase == self.phase:
            return
        if phase == "swing" and not self.saw_crouch_aim:
            self._issue("entered swing phase without stable crouch_aim (0x12/0x04/0)")
        self.phase = phase

    def observe(self, anim: int, aux: int, recovery: int) -> None:
        self.frame += 1
        label = classify_knife_anim(anim, aux, recovery)
        if label == "crouch_aim":
            self.saw_crouch_aim = True
        if label in ("swing_recovery", "standing_knife"):
            self.saw_swing_anim = True
        if label == "standing_knife":
            self._standing_knife_frames += 1
        if label == "swing_recovery":
            self._swing_recovery_frames += 1
        if label == "idle" and self.phase == "recovery":
            self.saw_idle_end = True
        if is_knife_swing_recovery_tail(anim, aux, recovery):
            self.saw_idle_end = True

        if self.saw_crouch_aim and _is_swing_anim_label(label):
            if self._swing_anim_frames < self._expect_swing:
                self._swing_anim_frames += 1
            else:
                self._recovery_anim_frames += 1
            self._knife_anim_since_aim += 1

        hooks = format_knife_hooks(anim, aux, recovery)
        self._trail.append(f"f{self.frame}:{self.phase}:{label}:{hooks}")
        if len(self._trail) > self._trail_cap:
            self._trail.pop(0)

        if not _phase_allows_state(self.phase, label):
            self._issue(
                f"unexpected {label} during {self.phase} ({hooks}); "
                f"expected one of {_expected_labels(self.phase)}"
            )

    def finish(self, *, outcome: str, died: bool, frames: int) -> None:
        if outcome == "ok" and not died:
            if not self.saw_crouch_aim:
                self._issue("macro finished without ever seeing crouch_aim")
            if not self.saw_swing_anim:
                self._issue("macro finished without swing_recovery/standing_knife")
            if not self.saw_idle_end:
                self._issue("macro finished without idle tail (0/0/0)")
            self._check_swing_frame_count(where="macro_end")
            self._check_recovery_frame_count(where="macro_end")
        elif self.phase in ("swing", "recovery"):
            self._check_swing_frame_count(where=f"abort_{outcome}")
            if self.phase == "recovery":
                self._check_recovery_frame_count(where=f"abort_{outcome}")
        if self._issues:
            self._emit_summary(outcome=outcome, died=died, frames=frames)
        return self.report(outcome=outcome, died=died, frames=frames)

    def report(self, *, outcome: str, died: bool, frames: int) -> dict[str, Any]:
        swing_tol = _frame_count_tolerance(self._expect_swing)
        rec_tol = _frame_count_tolerance(self._expect_recovery)
        swing_ok = (
            self._expect_swing - swing_tol
            <= self._swing_anim_frames
            <= self._expect_swing + swing_tol
        )
        recovery_ok = (
            self._expect_recovery - rec_tol
            <= self._recovery_anim_frames
            <= self._expect_recovery + rec_tol
        )
        return {
            "outcome": outcome,
            "died": bool(died),
            "macro_frames": int(frames),
            "ok": (
                not died
                and outcome == "ok"
                and not self._issues
                and swing_ok
                and recovery_ok
            ),
            "issues": list(self._issues),
            "crouch_aim": bool(self.saw_crouch_aim),
            "swing_anim": bool(self.saw_swing_anim),
            "idle_end": bool(self.saw_idle_end),
            "swing_frames": int(self._swing_anim_frames),
            "expect_swing": int(self._expect_swing),
            "recovery_frames": int(self._recovery_anim_frames),
            "expect_recovery": int(self._expect_recovery),
            "standing_knife_frames": int(self._standing_knife_frames),
            "swing_recovery_label_frames": int(self._swing_recovery_frames),
            "swing_frame_ok": swing_ok,
            "recovery_frame_ok": recovery_ok,
            "pre_state": self.pre_state,
        }

    def _check_swing_frame_count(self, *, where: str) -> None:
        got = self._swing_anim_frames
        expect = self._expect_swing
        tol = _frame_count_tolerance(expect)
        if got < expect - tol:
            self._issue(
                f"swing anim too short at {where}: {got} frames "
                f"(expected ~{expect} emu ±{tol}; standing={self._standing_knife_frames} "
                f"recovery_anim={self._swing_recovery_frames})"
            )
        elif got > expect + tol:
            self._issue(
                f"swing anim too long at {where}: {got} frames "
                f"(expected ~{expect} emu ±{tol}; standing={self._standing_knife_frames} "
                f"recovery_anim={self._swing_recovery_frames})"
            )

    def _check_recovery_frame_count(self, *, where: str) -> None:
        got = self._recovery_anim_frames
        expect = self._expect_recovery
        tol = _frame_count_tolerance(expect)
        if got < expect - tol:
            self._issue(
                f"recovery anim too short at {where}: {got} frames "
                f"(expected ~{expect} emu ±{tol}; standing={self._standing_knife_frames} "
                f"recovery_anim={self._swing_recovery_frames})"
            )
        elif got > expect + tol:
            self._issue(
                f"recovery anim too long at {where}: {got} frames "
                f"(expected ~{expect} emu ±{tol}; standing={self._standing_knife_frames} "
                f"recovery_anim={self._swing_recovery_frames})"
            )

    def _issue(self, msg: str) -> None:
        self._issues.append(msg)
        key = f"{self.phase}:{msg}"
        if key in self._logged:
            return
        self._logged.add(key)
        _knife_anim_log(msg, port=self._port)

    def _emit_summary(self, *, outcome: str, died: bool, frames: int) -> None:
        milestones = (
            f"crouch_aim={int(self.saw_crouch_aim)} "
            f"swing_anim={int(self.saw_swing_anim)} "
            f"idle_end={int(self.saw_idle_end)} "
            f"swing_frames={self._swing_anim_frames}/{self._expect_swing} "
            f"recovery_frames={self._recovery_anim_frames}/{self._expect_recovery}"
        )
        pre = self.pre_state or {}
        pre_hooks = pre.get("hooks", "?")
        pre_label = pre.get("label", "?")
        _knife_anim_log(
            f"SUMMARY outcome={outcome} died={int(died)} frames={frames} "
            f"phase={self.phase} pre={pre_label} {pre_hooks} {milestones} "
            f"issues={len(self._issues)}",
            port=self._port,
        )
        for issue in self._issues:
            _knife_anim_log(f"  - {issue}", port=self._port)
        if self._trail:
            _knife_anim_log(f"  trail: {' | '.join(self._trail)}", port=self._port)


def _expected_labels(phase: str) -> str:
    allowed = {
        "settle": _SETTLE_OK,
        "aim": _AIM_OK,
        "swing": _SWING_OK,
        "recovery": _RECOVERY_OK,
    }.get(phase, frozenset())
    return "|".join(sorted(allowed)) or "?"


def _knife_anim_log(msg: str, *, port: Any) -> None:
    if not KNIFE_ANIM_LOG_ENABLED:
        return
    print(f"[knife_anim] port={port} {msg}", flush=True)


def is_crouch_knife_aim_ready(anim: int, aux: int, recovery: int) -> bool:
    """True when Jill is in crouched knife aim (not idle, not late recovery)."""
    return (
        anim == CROUCH_KNIFE_AIM_ANIM
        and aux == CROUCH_KNIFE_ACTIVE_AUX
        and recovery == 0
    )


def is_knife_animation_idle(anim: int, aux: int, recovery: int) -> bool:
    """True when player anim fully returned to neutral (0/0/0)."""
    return anim == 0 and aux == 0 and recovery == 0


def is_knife_swing_recovery_tail(anim: int, aux: int, recovery: int) -> bool:
    """True when crouch-knife recovery timer drained (after swing anim seen)."""
    return (
        anim == KNIFE_RECOVERY_ANIM
        and aux == CROUCH_KNIFE_ACTIVE_AUX
        and recovery == 0
    )


def is_knife_macro_track(anim: int, aux: int, recovery: int) -> bool:
    """True when Jill is idle or in a known knife aim/swing/recovery animation."""
    if is_knife_animation_idle(anim, aux, recovery):
        return True
    if is_idle_recovery_latch(anim, aux, recovery):
        return True
    if is_standing_pre_knife_hook(anim, aux):
        return True
    if is_crouch_knife_aim_ready(anim, aux, recovery):
        return True
    if anim in (
        CROUCH_KNIFE_AIM_ANIM,
        KNIFE_RECOVERY_ANIM,
        STANDING_KNIFE_ANIM,
        CROUCH_KNIFE_POST_ANIM,
    ) and aux in (
        0,
        CROUCH_KNIFE_ACTIVE_AUX,
    ):
        return True
    if anim == KNIFE_RECOVERY_ANIM and recovery > 0:
        return True
    return False


def is_knife_macro_interrupted(
    anim: int,
    aux: int,
    recovery: int,
    *,
    aim_achieved: bool,
    swing_started: bool,
    swing_frames: int = 0,
    min_swing_frames: int = 0,
) -> bool:
    """True when the player left the knife state machine (likely hit/stagger)."""
    if is_knife_macro_track(anim, aux, recovery):
        if swing_started and is_knife_animation_idle(anim, aux, recovery):
            # Hold cross through min_swing even if RAM briefly hits idle (turbo gaps).
            if min_swing_frames > 0 and swing_frames < min_swing_frames:
                return False
            return True
        return False
    return True


def _macro_aborted(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    echo_joypad: bool,
    prev_hp: int,
    episode_start_hp: int,
    frames_so_far: int,
) -> tuple[bool, int]:
    """Release all buttons; return (died, emulated_frames_burned)."""
    neutral = {k: False for k in empty_sticky}
    frames = frames_so_far + 1
    if _step_one_frame(
        bridge,
        neutral,
        empty_sticky=empty_sticky,
        echo_joypad=echo_joypad,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    ):
        return True, frames
    died = _macro_player_died(
        bridge, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    return died, frames

def build_knife_frame_buttons(
    aim: int = KNIFE_AIM_GAME_FRAMES,
    swing: int = KNIFE_SWING_GAME_FRAMES,
    recovery: int = KNIFE_RECOVERY_GAME_FRAMES,
    scale: int = KNIFE_FRAME_SCALE,
) -> list[dict[str, bool]]:
    """Explicit per-frame joypad state (legacy fixed schedule)."""
    frames: list[dict[str, bool]] = []
    frames += [dict(AIM_BUTTONS)] * (aim * scale)
    frames += [dict(SWING_BUTTONS)] * (swing * scale)
    frames += [dict(AIM_BUTTONS)] * (recovery * scale)
    return frames


def build_standing_knife_frame_buttons(
    aim: int = KNIFE_AIM_GAME_FRAMES,
    swing: int = KNIFE_SWING_GAME_FRAMES,
    recovery: int = KNIFE_RECOVERY_GAME_FRAMES,
    scale: int = KNIFE_FRAME_SCALE,
) -> list[dict[str, bool]]:
    """Standing aim knife: R1 aim, R1+cross swing, R1 recovery (no down)."""
    frames: list[dict[str, bool]] = []
    frames += [dict(STANDING_AIM_BUTTONS)] * (aim * scale)
    frames += [dict(STANDING_SWING_BUTTONS)] * (swing * scale)
    frames += [dict(STANDING_AIM_BUTTONS)] * (recovery * scale)
    return frames


def build_quick_knife_frame_buttons(
    swing: int = KNIFE_SWING_GAME_FRAMES,
    recovery: int = KNIFE_RECOVERY_GAME_FRAMES,
    scale: int = KNIFE_FRAME_SCALE,
) -> list[dict[str, bool]]:
    """Quick knife (no R1): cross slash then neutral recovery tail."""
    frames: list[dict[str, bool]] = []
    frames += [dict(QUICK_KNIFE_BUTTONS)] * (swing * scale)
    frames += [{}] * (recovery * scale)
    return frames


@dataclass(frozen=True)
class KnifeHookFrame:
    anim: int
    aux: int
    recovery: int
    label: str

    def key(self) -> tuple[int, int, int]:
        return (self.anim, self.aux, self.recovery)

    def hook_pair(self) -> tuple[int, int]:
        return (self.anim, self.aux)


def summarize_knife_trace(frames: list[KnifeHookFrame]) -> dict[str, Any]:
    """Aggregate anim labels and raw hook pairs from a per-frame trace."""
    counts = Counter(f.label for f in frames)
    hook_counts = Counter(f.hook_pair() for f in frames if f.label != "idle")
    anim_counts = Counter(f.anim for f in frames if f.label != "idle")
    return {
        "frames": len(frames),
        "label_counts": dict(counts),
        "hook_pair_counts": {
            f"0x{anim:02X}/0x{aux:02X}": n for (anim, aux), n in hook_counts.items()
        },
        "anim_counts": {f"0x{anim:02X}": n for anim, n in anim_counts.items()},
        "labels_seen": sorted(k for k in counts if k != "idle"),
        "saw_crouch_aim": counts.get("crouch_aim", 0) > 0,
        "saw_standing_knife": counts.get("standing_knife", 0) > 0,
        "saw_swing_recovery": counts.get("swing_recovery", 0) > 0,
    }


def compare_knife_stances(
    crouch: dict[str, Any], standing: dict[str, Any]
) -> tuple[bool, list[str]]:
    """Return (distinguishable, reasons). Live QA + unit tests share this."""
    reasons: list[str] = []
    ok = True

    if not crouch.get("saw_crouch_aim"):
        ok = False
        reasons.append("crouch trace never saw crouch_aim (0x12/0x04/0)")

    if crouch.get("saw_standing_knife") and not standing.get("saw_standing_knife"):
        reasons.append(
            "warning: crouch trace saw standing_knife (0x14) — marker not crouch-exclusive"
        )

    if standing.get("saw_crouch_aim"):
        ok = False
        reasons.append("standing trace saw crouch_aim — stances overlap on crouch marker")

    crouch_hooks = set(crouch.get("hook_pair_counts", {}))
    stand_hooks = set(standing.get("hook_pair_counts", {}))
    shared = crouch_hooks & stand_hooks
    crouch_only = crouch_hooks - stand_hooks
    stand_only = stand_hooks - crouch_hooks

    if not crouch_only:
        ok = False
        reasons.append("no crouch-exclusive anim/aux pairs")
    else:
        reasons.append(f"crouch-only hooks: {sorted(crouch_only)}")

    if not stand_only:
        ok = False
        reasons.append("no standing-exclusive anim/aux pairs")
    else:
        reasons.append(f"standing-only hooks: {sorted(stand_only)}")

    if shared:
        reasons.append(f"shared hooks (expected few): {sorted(shared)}")

    if standing.get("saw_standing_knife"):
        reasons.append("standing trace saw standing_knife (0x14)")
    elif not any(k.startswith("0x14/") for k in standing.get("hook_pair_counts", {})):
        reasons.append(
            "standing trace did not show 0x14 — may use different anim than expected"
        )

    if crouch.get("saw_swing_recovery") and standing.get("saw_swing_recovery"):
        reasons.append("both traces used swing_recovery (0x13) — split by aux/recovery")

    return ok, reasons


def trace_knife_button_schedule(
    bridge: Any,
    schedule: list[dict[str, bool]],
    *,
    empty_sticky: dict[str, bool] | None = None,
    warmup_frames: int = 4,
    tail_frames: int = 8,
    prev_hp: int = 96,
    episode_start_hp: int = 96,
    record_y: bool = False,
) -> list[KnifeHookFrame] | tuple[list[KnifeHookFrame], list[int]]:
    """Step one emulated frame at a time; record anim hooks (stance compare QA)."""
    from re1_rl.memory_map import PLAYER_Y

    sticky = empty_sticky or {k: False for k in ("up", "down", "left", "right", "square")}
    out: list[KnifeHookFrame] = []
    ys: list[int] = []

    def record() -> None:
        anim, aux, recovery = read_knife_hooks(bridge)
        out.append(
            KnifeHookFrame(
                anim=anim,
                aux=aux,
                recovery=recovery,
                label=classify_knife_anim(anim, aux, recovery),
            )
        )
        if record_y:
            ys.append(int(bridge.read_ram([("player_y", PLAYER_Y, "s16")])["player_y"]))

    for _ in range(warmup_frames):
        bridge.step(n=1, sticky=sticky, frame_buttons=[{}])
        record()

    for buttons in schedule:
        _step_one_frame(
            bridge,
            buttons,
            empty_sticky=sticky,
            echo_joypad=False,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        record()

    for _ in range(tail_frames):
        bridge.step(n=1, sticky=sticky, frame_buttons=[{}])
        record()

    if record_y:
        return out, ys
    return out


def probe_crouch_aim_entry(
    bridge: Any,
    *,
    strategy: str = "simultaneous",
    r1_preamble_frames: int = 8,
    max_frames: int = 60,
    empty_sticky: dict[str, bool] | None = None,
    prev_hp: int = 96,
    episode_start_hp: int = 96,
) -> dict[str, Any]:
    """Try aim→crouch from current RAM state; report whether engine accepts inputs.

    strategy:
      simultaneous — hold R1+down from frame 1 (production macro today)
      r1_first     — R1-only preamble, then R1+down
    """
    if strategy not in ("simultaneous", "r1_first"):
        raise ValueError(f"unknown aim probe strategy: {strategy!r}")

    pre = read_pre_knife_state(bridge)
    sticky = empty_sticky or {
        k: False for k in ("up", "down", "left", "right", "square")
    }
    facing_start = pre.get("player_facing")
    trail: list[str] = []
    stable_run = 0
    need_stable = MIN_BUTTON_PHASE_FRAMES
    reached = False
    frames_to_aim: int | None = None

    for frame in range(max_frames):
        if strategy == "r1_first" and frame < r1_preamble_frames:
            buttons = STANDING_AIM_BUTTONS
        else:
            buttons = AIM_BUTTONS
        if _step_one_frame(
            bridge,
            buttons,
            empty_sticky=sticky,
            echo_joypad=False,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        ):
            break
        anim, aux, recovery = read_knife_hooks(bridge)
        label = classify_knife_anim(anim, aux, recovery)
        trail.append(f"f{frame + 1}:{label}:{format_knife_hooks(anim, aux, recovery)}")
        if is_crouch_knife_aim_ready(anim, aux, recovery):
            stable_run += 1
            if stable_run >= need_stable and frames_to_aim is None:
                frames_to_aim = frame + 2 - need_stable
                reached = True
        else:
            stable_run = 0

    anim, aux, recovery = read_knife_hooks(bridge)
    from re1_rl.memory_map import PLAYER_FACING

    facing_end: int | None = None
    try:
        facing_end = int(
            bridge.read_ram([("player_facing", PLAYER_FACING, "u16")])["player_facing"]
        )
    except (KeyError, TypeError):
        pass
    delta: int | None = None
    if facing_start is not None and facing_end is not None:
        delta = (facing_end - facing_start) % 4096
        if delta > 2048:
            delta -= 4096
    return {
        "pre_state": pre,
        "strategy": strategy,
        "reached_crouch_aim": reached,
        "frames_to_crouch_aim": frames_to_aim,
        "facing_delta": delta,
        "trail_tail": trail[-12:],
        "final_hooks": format_knife_hooks(anim, aux, recovery),
        "final_label": classify_knife_anim(anim, aux, recovery),
    }


def probe_standing_aim_hooks(
    bridge: Any,
    *,
    aim_frames: int = 40,
    empty_sticky: dict[str, bool] | None = None,
) -> list[tuple[int, int, int, str]]:
    """Hold R1 only; sample hooks per frame (standing aim probe)."""
    sticky = empty_sticky or {k: False for k in ("up", "down", "left", "right", "square")}
    samples: list[tuple[int, int, int, str]] = []
    for _ in range(aim_frames):
        bridge.step(n=1, sticky=sticky, frame_buttons=[dict(STANDING_AIM_BUTTONS)])
        anim, aux, recovery = read_knife_hooks(bridge)
        samples.append((anim, aux, recovery, classify_knife_anim(anim, aux, recovery)))
    return samples


def _macro_player_died(
    bridge: Any, *, prev_hp: int, episode_start_hp: int
) -> bool:
    hp = int(bridge.read_ram([("player_hp", PLAYER_HP, "u16")])["player_hp"])
    return player_died(hp, prev_hp=prev_hp, episode_start_hp=episode_start_hp)


def _step_one_frame(
    bridge: Any,
    buttons: dict[str, bool],
    *,
    empty_sticky: dict[str, bool],
    echo_joypad: bool,
    prev_hp: int,
    episode_start_hp: int,
) -> bool:
    """Advance one emulated frame; return True if player died."""
    _, died = bridge.step(
        n=1,
        sticky=empty_sticky,
        frame_buttons=[buttons],
        echo_joypad=echo_joypad,
    )
    if died:
        return True
    return _macro_player_died(
        bridge, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )


def _execute_knife_macro_ram_gated(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    aim_game: int,
    swing_game: int,
    recovery_game: int,
    scale: int,
    echo_joypad: bool,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """RAM-gated crouch knife: aim until hooks ready, swing, hold through recovery."""
    min_swing = max(MIN_BUTTON_PHASE_FRAMES, swing_game * scale)
    aim_ready_streak = MIN_BUTTON_PHASE_FRAMES
    max_aim_wait = max(aim_game * scale * 6, 48)
    max_total = (aim_game + swing_game + recovery_game) * scale * 4

    if echo_joypad:
        bridge.last_step_echo = []

    total = 0
    anim_val = KnifeAnimValidator(
        bridge,
        swing_game=swing_game,
        recovery_game=recovery_game,
        scale=scale,
    )
    entry_hooks = read_knife_hooks(bridge)
    anim_val.pre_state = read_pre_knife_state(bridge, hooks=entry_hooks)

    def _abort(frames_so_far: int, *, outcome: str) -> tuple[bool, int]:
        died, frames = _macro_aborted(
            bridge,
            empty_sticky=empty_sticky,
            echo_joypad=echo_joypad,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            frames_so_far=frames_so_far,
        )
        bridge.last_knife_anim_report = anim_val.finish(
            outcome=outcome, died=died, frames=frames
        )
        return died, frames

    def _death(frames_so_far: int) -> tuple[bool, int]:
        bridge.last_knife_anim_report = anim_val.finish(
            outcome="death", died=True, frames=frames_so_far
        )
        return True, frames_so_far

    entry_anim, entry_aux, entry_recovery = entry_hooks
    entry_standing_ready = is_standing_pre_knife_idle(
        entry_anim, entry_aux, entry_recovery
    )

    neutral = {k: False for k in empty_sticky}
    max_settle_wait = KNIFE_SETTLE_MAX_WAIT_FRAMES

    # Phase 0: release all buttons until settled (0/0/0 or weapon-ready standing).
    settle_run = 0
    early_aim_run = 0
    settle_wait = 0
    aim_precooked = False
    if entry_standing_ready:
        # Knife already equipped + standing idle: neutral release only triggers
        # standing_recovery_latch; skip pad-drain settle and go straight to aim.
        anim_val.saw_settled_idle = True
        settle_run = aim_ready_streak
    while settle_run < aim_ready_streak and early_aim_run < aim_ready_streak:
        if settle_wait >= max_settle_wait:
            anim_val._issue(
                f"settle timeout after {settle_wait} frames "
                f"(never stabilized idle 0/0/0 or standing weapon idle)"
            )
            return _abort(total, outcome="settle_timeout")
        if _macro_player_died(
            bridge, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        ):
            return _death(total)
        if _step_one_frame(
            bridge,
            neutral,
            empty_sticky=empty_sticky,
            echo_joypad=echo_joypad,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        ):
            return _death(total + 1)
        total += 1
        settle_wait += 1
        anim, aux, recovery = read_knife_hooks(bridge)
        anim_val.observe(anim, aux, recovery)
        if not is_knife_settle_wait_state(anim, aux, recovery):
            anim_val._issue(
                f"interrupted during settle ({format_knife_hooks(anim, aux, recovery)})"
            )
            return _abort(total, outcome="aborted_interrupt")
        if is_crouch_knife_aim_ready(anim, aux, recovery):
            early_aim_run += 1
            settle_run = 0
            if early_aim_run >= aim_ready_streak:
                aim_precooked = True
                anim_val.saw_crouch_aim = True
                anim_val.saw_settled_idle = True
        elif is_knife_settle_complete(anim, aux, recovery):
            settle_run += 1
            early_aim_run = 0
            if settle_run >= aim_ready_streak:
                anim_val.saw_settled_idle = True
        else:
            settle_run = 0
            early_aim_run = 0

    if aim_precooked:
        anim_val.set_phase("swing")
    else:
        anim_val.set_phase("aim")

    # Phase 1: R1+down until crouch aim animation is stable (skip if settle reached aim).
    ready_run = aim_ready_streak if aim_precooked else 0
    if not aim_precooked:
        while ready_run < aim_ready_streak:
            if total >= max_aim_wait:
                break
            if _macro_player_died(
                bridge, prev_hp=prev_hp, episode_start_hp=episode_start_hp
            ):
                return _death(total)
            if _step_one_frame(
                bridge,
                AIM_BUTTONS,
                empty_sticky=empty_sticky,
                echo_joypad=echo_joypad,
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            ):
                return _death(total + 1)
            total += 1
            anim, aux, recovery = read_knife_hooks(bridge)
            anim_val.observe(anim, aux, recovery)
            if is_knife_macro_interrupted(
                anim, aux, recovery, aim_achieved=False, swing_started=False
            ):
                anim_val._issue(
                    f"interrupted during aim ({format_knife_hooks(anim, aux, recovery)})"
                )
                return _abort(total, outcome="aborted_interrupt")
            if is_crouch_knife_aim_ready(anim, aux, recovery):
                ready_run += 1
            else:
                ready_run = 0

    aim_achieved = ready_run >= aim_ready_streak
    if not aim_achieved:
        anim_val._issue(
            f"aim timeout after {total} frames (never stabilized crouch_aim 0x12/0x04/0)"
        )
        return _abort(total, outcome="aim_timeout")

    # Phase 2: cross on first frame after stable crouch aim; hold through swing.
    if not aim_precooked:
        anim_val.set_phase("swing")
    swing_frames = 0
    saw_swing_recovery_anim = False
    swing_idle_streak = 0
    while swing_frames < min_swing:
        if total >= max_total:
            anim_val._issue(f"swing phase exceeded max_total={max_total}")
            return _abort(total, outcome="swing_timeout")
        if _step_one_frame(
            bridge,
            SWING_BUTTONS,
            empty_sticky=empty_sticky,
            echo_joypad=echo_joypad,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        ):
            return _death(total + 1)
        swing_frames += 1
        total += 1
        anim, aux, recovery = read_knife_hooks(bridge)
        if anim == KNIFE_RECOVERY_ANIM and recovery > 0:
            saw_swing_recovery_anim = True
        anim_val.observe(anim, aux, recovery)
        if not is_knife_macro_track(anim, aux, recovery):
            anim_val._issue(
                f"interrupted during swing ({format_knife_hooks(anim, aux, recovery)})"
            )
            return _abort(total, outcome="aborted_interrupt")
        if is_knife_animation_idle(anim, aux, recovery):
            swing_idle_streak += 1
            if (
                swing_frames >= min_swing
                and swing_idle_streak >= aim_ready_streak
                and not saw_swing_recovery_anim
            ):
                anim_val._issue(
                    f"interrupted during swing ({format_knife_hooks(anim, aux, recovery)})"
                )
                return _abort(total, outcome="aborted_interrupt")
        else:
            swing_idle_streak = 0

    # Phase 3: R1+down (no cross) until animation returns idle.
    anim_val.set_phase("recovery")
    max_recovery_wait = max(recovery_game * scale * 4, 32)
    recovery_wait = 0
    recovered = False
    while total < max_total and recovery_wait < max_recovery_wait:
        if _macro_player_died(
            bridge, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        ):
            return _death(total)
        anim, aux, recovery = read_knife_hooks(bridge)
        if anim == KNIFE_RECOVERY_ANIM and recovery > 0:
            saw_swing_recovery_anim = True
        anim_val.observe(anim, aux, recovery)
        if is_knife_animation_idle(anim, aux, recovery):
            recovered = True
            break
        if (
            saw_swing_recovery_anim
            and is_knife_swing_recovery_tail(anim, aux, recovery)
        ):
            recovered = True
            break
        if is_knife_macro_interrupted(
            anim, aux, recovery, aim_achieved=True, swing_started=True
        ):
            anim_val._issue(
                f"interrupted during recovery ({format_knife_hooks(anim, aux, recovery)})"
            )
            return _abort(total, outcome="aborted_interrupt")
        if _step_one_frame(
            bridge,
            AIM_BUTTONS,
            empty_sticky=empty_sticky,
            echo_joypad=echo_joypad,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        ):
            return _death(total + 1)
        total += 1
        recovery_wait += 1

    if not recovered:
        anim_val._issue(
            f"recovery timeout after {recovery_wait} frames "
            f"(last {format_knife_hooks(anim, aux, recovery)})"
        )
        return _abort(total, outcome="recovery_timeout")

    died = _macro_player_died(
        bridge, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    bridge.last_knife_anim_report = anim_val.finish(
        outcome="ok", died=died, frames=total
    )
    return died, total


def _execute_knife_macro_fixed(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    phases: tuple[int, int, int] | None,
    scale: int | None,
    echo_joypad: bool,
) -> tuple[bool, int]:
    kwargs: dict[str, int] = {}
    if phases is not None:
        kwargs["aim"], kwargs["swing"], kwargs["recovery"] = phases
    if scale is not None:
        kwargs["scale"] = scale
    schedule = build_knife_frame_buttons(**kwargs)
    _, died = bridge.step(
        n=len(schedule),
        sticky=empty_sticky,
        frame_buttons=schedule,
        echo_joypad=echo_joypad,
    )
    if echo_joypad and not died:
        echo = getattr(bridge, "last_step_echo", None)
        if isinstance(echo, list):
            want = ["+".join(sorted(k for k, v in f.items() if v)) for f in schedule]
            if echo != want:
                n_bad = sum(1 for w, g in zip(want, echo) if w != g) + abs(
                    len(want) - len(echo)
                )
                print(
                    f"[knife_macro] INPUT LOSS port={getattr(bridge, 'port', '?')}: "
                    f"{n_bad}/{len(want)} frames not delivered as scheduled",
                    flush=True,
                )
    return bool(died), len(schedule)


def execute_knife_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    phases: tuple[int, int, int] | None = None,
    scale: int | None = None,
    echo_joypad: bool = False,
    use_ram_gates: bool = True,
    prev_hp: int = 0,
    episode_start_hp: int = 0,
) -> tuple[bool, int]:
    """Run knife input; return (died, emulated_frames_burned)."""
    if use_ram_gates:
        aim_g, swing_g, rec_g = (
            phases
            if phases is not None
            else (
                KNIFE_AIM_GAME_FRAMES,
                KNIFE_SWING_GAME_FRAMES,
                KNIFE_RECOVERY_GAME_FRAMES,
            )
        )
        sc = scale if scale is not None else KNIFE_FRAME_SCALE
        return _execute_knife_macro_ram_gated(
            bridge,
            empty_sticky=empty_sticky,
            aim_game=aim_g,
            swing_game=swing_g,
            recovery_game=rec_g,
            scale=sc,
            echo_joypad=echo_joypad,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
    return _execute_knife_macro_fixed(
        bridge,
        empty_sticky=empty_sticky,
        phases=phases,
        scale=scale,
        echo_joypad=echo_joypad,
    )
