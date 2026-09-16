"""Unit tests for glibc LCG seed capture/restore helpers."""

from __future__ import annotations

from types import SimpleNamespace

from re1_rl.leg_replay import new_leg_replay_buffer
from re1_rl.rng_seed import (
    RNG_SEED,
    apply_tape_rng_seed,
    lcg_step,
    poke_rng_seed,
    read_rng_seed,
    snapshot_leg_rng_seed,
    tape_rng_seed,
)


class _Bridge:
    def __init__(self, seed: int = 0x12345678) -> None:
        self.mem = {RNG_SEED: int(seed) & 0xFFFFFFFF}

    def read_ram(self, fields):
        out = {}
        for name, addr, _kind in fields:
            out[name] = int(self.mem.get(int(addr), 0)) & 0xFFFFFFFF
        return out

    def write_ram(self, fields):
        for _name, addr, _kind, value in fields:
            self.mem[int(addr)] = int(value) & 0xFFFFFFFF


def test_lcg_step_matches_glibc_constants() -> None:
    seed, draw = lcg_step(1)
    assert seed == (1 * 0x41C64E6D + 12345) & 0xFFFFFFFF
    assert draw == (seed >> 16) & 0x7FFF


def test_read_poke_roundtrip() -> None:
    bridge = _Bridge(0xABCDEF01)
    assert read_rng_seed(bridge) == 0xABCDEF01
    assert poke_rng_seed(bridge, 0x42) is True
    assert read_rng_seed(bridge) == 0x42


def test_snapshot_into_leg_buffer() -> None:
    env = SimpleNamespace(
        bridge=_Bridge(0x11112222),
        _leg_replay=new_leg_replay_buffer(),
    )
    assert snapshot_leg_rng_seed(env) == 0x11112222
    assert env._leg_replay.rng_seed == 0x11112222


def test_apply_tape_rng_seed() -> None:
    bridge = _Bridge(1)
    status = apply_tape_rng_seed(bridge, {"rng_seed": 0x55AA55AA})
    assert status["applied"] is True
    assert status["requested"] == 0x55AA55AA
    assert read_rng_seed(bridge) == 0x55AA55AA
    assert tape_rng_seed({"contract": {"rng_seed": True}}) is None
    assert apply_tape_rng_seed(bridge, {})["reason"] == "no_rng_seed_in_tape"
