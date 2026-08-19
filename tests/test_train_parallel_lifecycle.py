"""EmuHawk ownership cleanup tests without launching the emulator."""

from __future__ import annotations

import subprocess

from scripts.train_parallel import _stop_owned_emuhawk


class _Bridge:
    def __init__(self) -> None:
        self.closes = 0

    def close(self) -> None:
        self.closes += 1


class _StubbornProcess:
    pid = 1234

    def __init__(self) -> None:
        self.terminates = 0
        self.kills = 0
        self.waits = 0

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminates += 1

    def kill(self) -> None:
        self.kills += 1

    def wait(self, timeout: float) -> None:
        self.waits += 1
        if self.waits == 1:
            raise subprocess.TimeoutExpired("EmuHawk", timeout)


def test_stop_owned_emuhawk_escalates_and_is_idempotent(monkeypatch) -> None:
    released: list[int] = []
    monkeypatch.setattr(
        "re1_rl.window_grid.release_emu_port",
        lambda pid, **_kwargs: released.append(int(pid)),
    )
    proc = _StubbornProcess()
    bridge = _Bridge()

    _stop_owned_emuhawk(proc, bridge, timeout_s=0.01)
    _stop_owned_emuhawk(proc, bridge, timeout_s=0.01)

    assert bridge.closes == 1
    assert released == [1234]
    assert proc.terminates == 1
    assert proc.kills == 1
    assert proc.waits == 2
