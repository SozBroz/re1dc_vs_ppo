"""Canonical BizHawk paths for D:/re1_rl — import instead of guessing ROM names."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
ROM_BIN = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.bin"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"
BIZHAWK_STATE_DIR = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"

GAME_SERIAL = "SLUS-00551"


def assert_rom_present() -> Path:
    if not ROM.is_file():
        raise FileNotFoundError(f"missing ROM cue: {ROM}")
    if not ROM_BIN.is_file():
        raise FileNotFoundError(f"missing ROM bin: {ROM_BIN}")
    return ROM


def emuhawk_argv(*, port: int) -> list[str]:
    cue = assert_rom_present()
    return [
        str(EMUHAWK),
        str(cue.resolve()),
        f"--lua={LUA}",
        "--socket_ip=127.0.0.1",
        f"--socket_port={port}",
    ]


def newest_quicksave() -> Path:
    states = sorted(
        [
            p
            for p in BIZHAWK_STATE_DIR.glob("*.QuickSave*.State")
            if not p.name.endswith(".bak")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not states:
        raise FileNotFoundError(f"no QuickSave in {BIZHAWK_STATE_DIR}")
    return states[0]
