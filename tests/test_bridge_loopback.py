"""Loopback test of the BizHawk bridge wire protocol -- no emulator needed.

Spins up the real BizHawkClient TCP server, then connects a mock "Lua" client
(pure Python) that speaks the same length-prefixed JSON protocol as
lua/re1_client.lua. Verifies framing, ping, read_ram, buttons, frameadvance,
and read_block round-trip correctly.

Run: D:\\re1_rl\\venv\\Scripts\\python.exe -m pytest tests/test_bridge_loopback.py -q
 or: D:\\re1_rl\\venv\\Scripts\\python.exe tests/test_bridge_loopback.py
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient, BizHawkStartupError


def _recv_message(sock: socket.socket) -> str:
    len_str = b""
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("closed")
        if ch == b" ":
            break
        len_str += ch
    length = int(len_str.decode())
    if length == 0:
        return ""
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            raise ConnectionError("closed mid-body")
        body += chunk
    return body.decode("utf-8")


def _send_message(sock: socket.socket, payload: str) -> None:
    sock.sendall(f"{len(payload.encode())} ".encode("ascii") + payload.encode("utf-8"))


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


# Fake RAM that the mock Lua endpoint serves.
FAKE_RAM = {0x0C51AC: 140, 0x0C867C: 12345}


def _mock_lua(port: int) -> None:
    """Behaves like re1_client.lua: connect, answer commands, close on quit."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        _send_message(sock, json.dumps({"hello": "re1_client", "frame": 0}))
        while True:
            payload = _recv_message(sock)
            cmd = json.loads(payload)
            op = cmd.get("cmd")
            if op == "ping":
                resp = {"ok": True, "pong": cmd.get("n", 0)}
            elif op == "read_ram":
                values = {}
                for name, addr, _dtype in cmd["fields"]:
                    off = addr - 0x80000000
                    values[name] = FAKE_RAM.get(off, 0)
                resp = {"ok": True, "values": values}
            elif op == "write_ram":
                for _name, addr, _dtype, value in cmd["fields"]:
                    off = addr - 0x80000000
                    FAKE_RAM[off] = int(value)
                resp = {"ok": True}
            elif op == "read_block":
                resp = {"ok": True, "addr": cmd["addr"], "bytes": list(range(cmd["count"]))}
            elif op == "buttons":
                resp = {"ok": True}
            elif op == "clear_input":
                resp = {"ok": True}
            elif op == "frameadvance":
                resp = {"ok": True, "frame": cmd.get("n", 1)}
            elif op == "set_patches":
                # mirror lua/re1_client.lua: store and ack with patch count
                resp = {"ok": True, "n": len(cmd.get("always") or [])}
            elif op == "quit":
                _send_message(sock, json.dumps({"ok": True, "bye": True}))
                break
            else:
                resp = {"ok": False, "error": f"unknown {op}"}
            _send_message(sock, json.dumps(resp))
    finally:
        sock.close()


def test_bridge_loopback() -> None:
    client = BizHawkClient(port=5599)
    client.start_server()

    t = threading.Thread(target=_mock_lua, args=(5599,), daemon=True)
    t.start()
    client.wait_for_client()

    assert client.ping(7) == 7

    ram = client.read_ram([("player_hp", 0x800C51AC, "u16"), ("game_timer", 0x800C867C, "u32")])
    assert ram["player_hp"] == 140
    assert ram["game_timer"] == 12345

    client.send_buttons({"P1 Up": True})
    client.clear_latched_input()
    assert client.frameadvance(8) == 8

    block = client.read_block(0x800C8724, 4)
    assert block == [0, 1, 2, 3]

    client.write_ram([("game_mode", 0x800C3003, "u8", 0x80)])
    ram2 = client.read_ram([("game_mode", 0x800C3003, "u8")])
    assert ram2["game_mode"] == 0x80

    client.set_patches([], turbo=None)
    client.set_patches(
        [(0x8001A64E, "u16", 0x1000)],
        {
            "addr": 0x8007BAF6,
            "on_value": 0x2400,
            "off_value": 0x0044,
            "mode_addr": 0x800C3003,
            "mask": 0x80,
        },
    )

    client.quit()
    client.close()
    t.join(timeout=2)


def test_wait_for_client_reports_first_byte_timeout() -> None:
    port = _free_port()
    client = BizHawkClient(port=port, timeout=0.05, connect_timeout=1.0)
    client.start_server()

    def _silent_lua() -> None:
        sock = socket.create_connection(("127.0.0.1", port), timeout=1)
        try:
            time.sleep(0.15)
        finally:
            sock.close()

    thread = threading.Thread(target=_silent_lua, daemon=True)
    thread.start()
    with pytest.raises(BizHawkStartupError, match="first_byte") as exc_info:
        client.wait_for_client()
    assert exc_info.value.phase == "first_byte"
    assert client._client is None
    client.close()
    thread.join(timeout=1)


def test_wait_for_client_reports_malformed_hello_and_phases() -> None:
    port = _free_port()
    client = BizHawkClient(port=port, timeout=1.0, connect_timeout=1.0)
    client.start_server()
    phases: list[str] = []

    def _bad_lua() -> None:
        sock = socket.create_connection(("127.0.0.1", port), timeout=1)
        try:
            _send_message(sock, "[]")
        finally:
            sock.close()

    thread = threading.Thread(target=_bad_lua, daemon=True)
    thread.start()
    with pytest.raises(BizHawkStartupError, match="hello") as exc_info:
        client.wait_for_client(phases.append)
    assert exc_info.value.phase == "hello"
    assert phases[0].startswith("accepted Lua TCP")
    assert phases[1] == "received Lua handshake first byte"
    assert client._client is None
    client.close()
    thread.join(timeout=1)


if __name__ == "__main__":
    test_bridge_loopback()
    print("BRIDGE_LOOPBACK_PASS")
