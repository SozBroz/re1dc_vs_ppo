"""Attach to GOG ResidentEvil.exe and read game state via pymem.

TODO: PC port addresses differ from PS1 bus addresses in memory_map.py.
      Build a Cheat Engine table for ResidentEvil.exe and fill PC_ADDRESSES.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROCESS_NAME = "ResidentEvil.exe"

# Placeholder virtual addresses — must be relocated per game build.
PC_ADDRESSES: dict[str, int] = {
    "player_hp": 0x0,  # TODO: CE pointer chain
    "room_id": 0x0,
    "game_timer": 0x0,
}


@dataclass
class ProcessMemory:
    """Thin pymem wrapper for the RE1 PC port."""

    process_name: str = PROCESS_NAME
    _pm: Any = None

    def attach(self) -> None:
        import pymem
        import pymem.process

        self._pm = pymem.Pymem(self.process_name)

    def is_attached(self) -> bool:
        return self._pm is not None

    def read_u16(self, name: str) -> int:
        if not self._pm:
            raise RuntimeError("Not attached; call attach() first")
        addr = PC_ADDRESSES.get(name, 0)
        if addr == 0:
            return 0
        return int(self._pm.read_short(addr))

    def read_u32(self, name: str) -> int:
        if not self._pm:
            raise RuntimeError("Not attached; call attach() first")
        addr = PC_ADDRESSES.get(name, 0)
        if addr == 0:
            return 0
        return int(self._pm.read_int(addr))

    def snapshot(self) -> dict[str, int]:
        return {key: self.read_u16(key) if "hp" in key else self.read_u32(key) for key in PC_ADDRESSES}
