"""Zombie-grab detection and deterministic escape input."""

from __future__ import annotations

from typing import Any

GRAB_BITE_DAMAGE = 12
GRAB_ESCAPE_FRAMES = 8
GRAB_ESCAPE_CAPTURED_BEST = (
    {"cross": True, "left": True},
    {"up": True},
    {"up": True},
    {},
    {"cross": True},
    {"cross": True, "left": True},
    {"cross": True},
    {"cross": True, "down": True, "right": True},
)


def grab_bite_transition(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> bool:
    """Detect the observed grab signature at its first 12-HP bite tick."""
    if not previous or not current:
        return False
    hp_before = int(previous.get("hp", 0))
    hp_after = int(current.get("hp", 0))
    if hp_before <= 0 or hp_before - hp_after != GRAB_BITE_DAMAGE:
        return False
    return (
        bool(current.get("in_control", False))
        and int(current.get("player_anim", -1)) == 0
        and int(current.get("player_aux", -1)) == 0
        and int(previous.get("x", 0)) == int(current.get("x", 0))
        and int(previous.get("z", 0)) == int(current.get("z", 0))
    )


def grab_escape_frame_buttons() -> list[dict[str, bool]]:
    """Replay the densest eight-frame window from a successful human escape."""
    return [dict(buttons) for buttons in GRAB_ESCAPE_CAPTURED_BEST]


def execute_grab_escape_noop(bridge: Any) -> tuple[bool, int]:
    """Execute the production noop-owned grab escape schedule."""
    frame_buttons = grab_escape_frame_buttons()
    _, died = bridge.step(
        n=len(frame_buttons),
        sticky={},
        frame_buttons=frame_buttons,
        ring_stride=0,
        capture_final=True,
    )
    return bool(died), len(frame_buttons)
