"""Sticky movement + pulsed face-button input for RE1 env steps."""

from __future__ import annotations

STICKY_KEYS = ("up", "down", "left", "right", "square")
FACE_KEYS = ("cross", "triangle", "circle", "r1", "r2", "l1", "l2")

# quickturn — tap within a step, never latched across steps
QUICKTURN_ACTION = 6
INTERACT_ACTION = 7
# Cross hold for shelf push / examine — longer than a normal frame_skip batch.
INTERACT_HOLD_EXTRA_FRAMES = 10
# Interact hold = frame_skip + EXTRA (18 frames at skip=8). Consider
# tap/release/tap/release (~8 frames) instead if shelf probes allow.
PULSE_ACTIONS = frozenset({QUICKTURN_ACTION})
# knife_swing uses re1_rl.knife_macro (phased aim/swing/recovery script);
# clears sticky here only
KNIFE_ACTION = 8


class StickyInputState:
    """Directions and run (square) latch until changed or noop."""

    def __init__(self) -> None:
        self._sticky = {k: False for k in STICKY_KEYS}

    def reset(self) -> None:
        for k in STICKY_KEYS:
            self._sticky[k] = False

    def as_dict(self) -> dict[str, bool]:
        return dict(self._sticky)

    def apply(
        self, action: int, button_map: dict[int, dict[str, bool]]
    ) -> tuple[dict[str, bool], dict[str, bool] | None, dict[str, bool] | None]:
        """Update latched state; return (sticky, pulse|None, pulse_hold|None)."""
        pulse: dict[str, bool] | None = None
        pulse_hold: dict[str, bool] | None = None
        if action == 0:
            self.reset()
        elif action == KNIFE_ACTION:
            # Movement cleared; env runs knife_macro with explicit frame schedule.
            self.reset()
        elif action == INTERACT_ACTION:
            # Full-step Cross hold (shelf push, examine) for the whole frame_skip
            # batch (plus INTERACT_HOLD_EXTRA_FRAMES in env). Forward/run into a
            # pushable uses pushable.PUSHABLE_HOLD_FRAMES (30) via RE1Env /
            # play_human — not this pulse path.
            pulse_hold = dict(button_map.get(action, {}))
        elif action in PULSE_ACTIONS:
            pulse = dict(button_map.get(action, {}))
        else:
            btn = button_map.get(action, {})
            if "up" in btn or "down" in btn:
                self._sticky["up"] = bool(btn.get("up"))
                self._sticky["down"] = bool(btn.get("down"))
            if "left" in btn or "right" in btn:
                self._sticky["left"] = bool(btn.get("left"))
                self._sticky["right"] = bool(btn.get("right"))
            if action == 5:
                self._sticky["square"] = True
            elif action == 1:
                self._sticky["square"] = False
        return self.as_dict(), pulse, pulse_hold


def human_buttons_to_step(
    buttons: dict[str, bool],
) -> tuple[dict[str, bool], dict[str, bool] | None, dict[str, bool] | None]:
    """Map polled keyboard/gamepad to sticky ``bridge.step`` args (human play).

    Directions + square latch for one ``frame_skip`` batch per press. Face
    buttons use ``pulse_hold`` so a held Cross registers every frame in the
    batch, not the 2-on/2-off training pulse used for quickturn.
    """
    sticky = {k: bool(buttons.get(k)) for k in STICKY_KEYS}
    pulse_hold = {k: True for k in FACE_KEYS if buttons.get(k)}
    return sticky, None, (pulse_hold if pulse_hold else None)


def empty_sticky() -> dict[str, bool]:
    return {k: False for k in STICKY_KEYS}


def human_step_gate(
    buttons: dict[str, bool], *, armed: bool
) -> tuple[bool, bool]:
    """Return (should_advance, armed_next).

  Human play commits one ``frame_skip`` chunk per press: hold does not repeat.
  Release to neutral re-arms; the next press advances another ``frame_skip``
  batch with sticky latch.
    """
    if not buttons:
        return False, True
    if not armed:
        return False, False
    return True, False
