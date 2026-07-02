"""Python side of the BizHawk Lua<->Python TCP socket bridge."""

from __future__ import annotations

import base64
import json
import socket
import threading
from typing import Any

import numpy as np

from re1_rl.memory_map import DEFAULT_RAM_FIELDS


class BizHawkClient:
    """TCP server that BizHawk's Lua script connects to via comm.socketServer*.

    Wire format (BrainHawk / GymBizHawk style):
      - Server sends length-prefixed UTF-8: ``{len} {payload}``
      - Client replies the same way.
      - Payload is JSON for structured commands; screenshots are base64 PNG.

    TODO: Validate message framing against live BizHawk octoshock + re1_client.lua.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5555, timeout: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._server: socket.socket | None = None
        self._client: socket.socket | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def start_server(self) -> None:
        """Bind and listen; Lua client connects outbound."""
        if self._server is not None:
            return
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(1)
        srv.settimeout(self.timeout)
        self._server = srv

    def wait_for_client(self) -> None:
        """Accept the BizHawk Lua connection."""
        if self._server is None:
            self.start_server()
        assert self._server is not None
        self._client, _ = self._server.accept()
        self._client.settimeout(self.timeout)

    def close(self) -> None:
        for sock in (self._client, self._server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._client = None
        self._server = None

    # ------------------------------------------------------------------
    # Protocol helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_message(payload: str) -> bytes:
        data = payload.encode("utf-8")
        header = f"{len(data)} ".encode("ascii")
        return header + data

    @staticmethod
    def _decode_message(sock: socket.socket) -> str:
        """Read one BizHawk length-prefixed message."""
        length_buf = bytearray()
        while True:
            ch = sock.recv(1)
            if not ch:
                raise ConnectionError("BizHawk client disconnected")
            if ch == b" ":
                break
            length_buf.extend(ch)
        length = int(length_buf.decode("ascii"))
        body = bytearray()
        while len(body) < length:
            chunk = sock.recv(length - len(body))
            if not chunk:
                raise ConnectionError("BizHawk client disconnected mid-message")
            body.extend(chunk)
        return body.decode("utf-8")

    def _request(self, command: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("No BizHawk client connected; call wait_for_client() first")
        payload = json.dumps(command)
        with self._lock:
            self._client.sendall(self._encode_message(payload))
            response = self._decode_message(self._client)
        return json.loads(response)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_ram(
        self,
        fields: list[tuple[str, int, str]] | None = None,
    ) -> dict[str, int | float]:
        """Read RAM fields. Each field is (name, ps1_bus_address, dtype)."""
        field_specs = fields or DEFAULT_RAM_FIELDS
        resp = self._request({"cmd": "read_ram", "fields": field_specs})
        return {k: int(v) for k, v in resp.get("values", {}).items()}

    def send_buttons(self, buttons: dict[str, bool]) -> None:
        """Apply joypad state for one frame batch (see lua/re1_client.lua)."""
        self._request({"cmd": "buttons", "buttons": buttons})

    def frameadvance(self, n: int = 1) -> None:
        self._request({"cmd": "frameadvance", "n": int(n)})

    def load_savestate(self, path: str) -> None:
        self._request({"cmd": "loadstate", "path": path})

    def screenshot(self) -> np.ndarray:
        """Return RGB uint8 array (H, W, 3)."""
        resp = self._request({"cmd": "screenshot"})
        png_b64 = resp.get("png_b64", "")
        if not png_b64:
            # Stub fallback for offline tests
            return np.zeros((240, 320, 3), dtype=np.uint8)
        import cv2

        raw = base64.b64decode(png_b64)
        arr = np.frombuffer(raw, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("Failed to decode screenshot PNG from BizHawk")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
