"""Structured console logging for typewriter-save detection and PB capture."""

from __future__ import annotations

import os
from typing import Any


def _fmt_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, (list, tuple)):
        inner = ",".join(_fmt_value(v) for v in value)
        return f"[{inner}]"
    return repr(value)


def log_typewriter_save(event: str, /, **fields: Any) -> None:
    """Emit one grep-friendly line: ``[typewriter_save] event=...``."""
    parts = [f"[typewriter_save] event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={_fmt_value(value)}")
    print(" ".join(parts), flush=True)


def log_ctx_from_env(env: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    machine = os.environ.get("RE1_MACHINE_NAME", "").strip()
    if machine:
        ctx["machine"] = machine
    bridge = getattr(env, "bridge", None)
    if bridge is not None:
        port = getattr(bridge, "port", None)
        if port is not None:
            ctx["port"] = port
    env_step = getattr(env, "_step_count", None)
    if env_step is not None:
        ctx["env_step"] = int(env_step)
    return ctx


def state_fields(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {}
    from re1_rl.typewriter_save import count_ink_ribbons

    out: dict[str, Any] = {
        "state_room": state.get("room_id"),
        "ribbons": count_ink_ribbons(state),
        "in_control": bool(state.get("in_control", False)),
    }
    step = state.get("step")
    if step is not None:
        out["step"] = int(step)
    x, z = state.get("x"), state.get("z")
    if x is not None and z is not None:
        out["pos"] = (int(float(x)), int(float(z)))
    return out
