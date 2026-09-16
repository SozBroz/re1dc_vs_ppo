"""RE1 DC (SLUS-00551) glibc-style LCG seed for deterministic combat replay.

Proven in ``docs/turbo_runtime/08c_rng_dog_playback.md``: same seed + joypad
tape → bit-identical enemy timelines. The generator is:

    seed = *(u32*)0x800AE690
    seed = seed * 0x41C64E6D + 12345
    *(u32*)0x800AE690 = seed
    return (seed >> 16) & 0x7FFF

``u16@0x800C867A`` (LAB_TIMER) is a last-draw cache — do not poke it for
replay. Capture/restore goes through this module + ``leg_replay.json``.
"""

from __future__ import annotations

from typing import Any

# glibc/ANSI rand state (func_8005F6D0 / func_8005F700).
RNG_SEED = 0x800AE690
LCG_MUL = 0x41C64E6D
LCG_ADD = 12345


def lcg_step(seed: int) -> tuple[int, int]:
    """One ANSI/glibc step. Returns ``(new_seed, rand15)``."""
    seed = (int(seed) * LCG_MUL + LCG_ADD) & 0xFFFFFFFF
    return seed, (seed >> 16) & 0x7FFF


def read_rng_seed(bridge: Any) -> int | None:
    """Read ``u32@RNG_SEED`` via ``bridge.read_ram``. None on I/O failure."""
    try:
        raw = bridge.read_ram([("rng_seed", RNG_SEED, "u32")])
        return int(raw["rng_seed"]) & 0xFFFFFFFF
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError):
        return None


def poke_rng_seed(bridge: Any, seed: int) -> bool:
    """Write the LCG state (srand-equivalent). True when the write sticks."""
    want = int(seed) & 0xFFFFFFFF
    try:
        bridge.write_ram([("rng_seed", RNG_SEED, "u32", want)])
        got = read_rng_seed(bridge)
        return got is not None and got == want
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return False


def snapshot_leg_rng_seed(env: Any) -> int | None:
    """Read seed into ``env._leg_replay.rng_seed`` when a tape buffer is armed."""
    buf = getattr(env, "_leg_replay", None)
    if buf is None:
        return None
    bridge = getattr(env, "bridge", None)
    if bridge is None:
        return None
    seed = read_rng_seed(bridge)
    if seed is None:
        return None
    try:
        buf.rng_seed = int(seed) & 0xFFFFFFFF
    except (AttributeError, TypeError, ValueError):
        return None
    return int(seed) & 0xFFFFFFFF


def tape_rng_seed(tape: dict[str, Any] | None) -> int | None:
    """Extract a recorded seed from a ``leg_replay`` payload, if present."""
    if not isinstance(tape, dict):
        return None
    raw = tape.get("rng_seed")
    if raw is None:
        contract = tape.get("contract")
        if isinstance(contract, dict):
            raw = contract.get("rng_seed_value")
    try:
        if raw is None:
            return None
        return int(raw) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None


def apply_tape_rng_seed(bridge: Any, tape: dict[str, Any] | None) -> dict[str, Any]:
    """Poke ``tape['rng_seed']`` after loadstate, before joypad/actions play.

    Returns a small status dict for logs (never raises).
    """
    want = tape_rng_seed(tape)
    if want is None:
        return {"applied": False, "reason": "no_rng_seed_in_tape"}
    before = read_rng_seed(bridge)
    ok = poke_rng_seed(bridge, want)
    after = read_rng_seed(bridge)
    return {
        "applied": bool(ok),
        "requested": want,
        "before": before,
        "after": after,
    }
