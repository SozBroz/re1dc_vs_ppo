"""Python side of the BizHawk Lua<->Python TCP socket bridge."""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import numpy as np

from re1_rl.memory_map import DEFAULT_RAM_FIELDS, PLAYER_HP

DEFAULT_SCREENSHOT_PATH = "D:/re1_rl/data/_frame.png"


class BizHawkClient:
    """TCP server; the BizHawk Lua script (re1_client.lua) connects to it.

    Wire format (BrainHawk / GymBizHawk style):
      - Both sides send length-prefixed UTF-8: ``{len} {payload}``.
      - Payload is JSON. Screenshots are transferred as PNG FILES (the Lua
        client writes via client.screenshot(path); Python reads the file).

    Validated offline by tests/test_bridge_loopback.py; live-tested against
    EmuHawk + re1_client.lua.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5555,
        timeout: float = 30.0,
        screenshot_path: str = DEFAULT_SCREENSHOT_PATH,
        connect_timeout: float | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.connect_timeout = connect_timeout if connect_timeout is not None else timeout
        self.screenshot_path = screenshot_path
        self._server: socket.socket | None = None
        self._client: socket.socket | None = None
        self._lock = threading.Lock()
        # Per-frame joypad.get() readback from the last step(echo_joypad=True).
        self.last_step_echo: list[str] | None = None

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
        srv.settimeout(self.connect_timeout)
        self._server = srv

    def wait_for_client(self) -> None:
        """Accept the BizHawk Lua connection and consume its hello message."""
        if self._server is None:
            self.start_server()
        assert self._server is not None
        self._client, _ = self._server.accept()
        self._client.settimeout(self.timeout)
        hello = json.loads(self._decode_message(self._client))
        if hello.get("hello") != "re1_client":
            raise ConnectionError(f"unexpected hello from Lua client: {hello!r}")

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

    def ping(self, n: int = 1) -> int:
        """Round-trip liveness check. Returns the echoed integer."""
        resp = self._request({"cmd": "ping", "n": int(n)})
        return int(resp.get("pong", -1))

    def read_ram(
        self,
        fields: list[tuple[str, int, str]] | None = None,
    ) -> dict[str, int | float]:
        """Read RAM fields. Each field is (name, ps1_bus_address, dtype).

        Fields whose address is None (e.g. ROOM_ID before it's found) are
        skipped so the env can run before every address is discovered.
        """
        field_specs = [f for f in (fields or DEFAULT_RAM_FIELDS) if f[1] is not None]
        resp = self._request({"cmd": "read_ram", "fields": field_specs})
        return {k: int(v) for k, v in resp.get("values", {}).items()}

    def read_block(self, address: int, count: int) -> list[int]:
        """Dump ``count`` contiguous bytes starting at a PS1 bus address."""
        resp = self._request({"cmd": "read_block", "addr": int(address), "count": int(count)})
        return [int(b) for b in resp.get("bytes", [])]

    def write_ram(
        self,
        fields: list[tuple[str, int, str, int]],
    ) -> None:
        """Write RAM fields. Each field is (name, ps1_bus_address, dtype, value)."""
        resp = self._request({"cmd": "write_ram", "fields": fields})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "write_ram failed"))

    def set_patches(
        self,
        always: list[tuple[int, str, int]],
        turbo: dict[str, int] | None = None,
    ) -> None:
        """Install GameShark-style engine patches, re-applied by the Lua side
        before every frame advance (and after every loadstate).

        ``always``: unconditional (ps1_bus_address, dtype, value) writes.
        ``turbo``: optional conditional patch dict with keys
          addr/on_value/off_value/mode_addr/mask -- on_value is written while
          (u8@mode_addr & mask) == 0 (i.e. cutscene), off_value otherwise.
        """
        self._request(
            {
                "cmd": "set_patches",
                "always": [list(p) for p in always],
                "turbo": turbo,
            }
        )

    def send_buttons(self, buttons: dict[str, bool]) -> None:
        """Apply joypad state for the next frame advance.

        Keys are friendly names mapped in lua/re1_client.lua BUTTON_MAP:
        up/down/left/right, cross/triangle/square/circle, start/select,
        r1/l1/r2/l2. (The Nymashock core uses unicode glyph button names
        internally; the Lua side translates.)
        """
        self._request({"cmd": "buttons", "buttons": buttons})

    def read_joypad(self, debug: bool = False) -> dict[str, bool] | tuple[dict[str, bool], dict[str, Any]]:
        """Host physical controller state via EmuHawk (joypad.getimmediate)."""
        req: dict[str, Any] = {"cmd": "read_joypad"}
        if debug:
            req["debug"] = True
        resp = self._request(req)
        btn_raw = resp.get("buttons", {})
        if isinstance(btn_raw, list):
            # dkjson encodes empty Lua tables as JSON arrays, not objects.
            buttons = {str(k): True for k in btn_raw if str(k) != "_"}
        else:
            buttons = {
                str(k): bool(v) for k, v in btn_raw.items() if str(k) != "_" and v
            }
        if debug:
            raw_in = resp.get("raw", {})
            if isinstance(raw_in, list):
                raw: dict[str, Any] = {}
            else:
                raw = {str(k): v for k, v in raw_in.items()}
            return buttons, raw
        return buttons

    def frameadvance(self, n: int = 1) -> int:
        resp = self._request({"cmd": "frameadvance", "n": int(n)})
        return int(resp.get("frame", -1))

    def step(
        self,
        buttons: dict[str, bool] | None = None,
        n: int = 1,
        *,
        sticky: dict[str, bool] | None = None,
        pulse: dict[str, bool] | None = None,
        pulse_hold: dict[str, bool] | None = None,
        pulse_frames_on: int = 2,
        pulse_frames_off: int = 2,
        pulse_from: int = 1,
        pulse_through: bool = False,
        frame_buttons: list[dict[str, bool]] | None = None,
        echo_joypad: bool = False,
        death_hp_addr: int | None = PLAYER_HP,
        abort_on_zero_hp: bool = True,
    ) -> tuple[int, bool]:
        """Advance ``n`` frames with input held each frame.

        Legacy: pass ``buttons`` only — held for the batch, then released.

        Sticky mode: pass ``sticky`` (full latched state: directions + square).
        Optional ``pulse`` face buttons tap on/off within the batch.
        Optional ``pulse_hold`` buttons stay pressed for every frame in the batch
        (e.g. R1 raised while cross pulses for knife_swing).
        ``pulse_from`` (1-based): first frame pulse keys apply (knife: 2 = stance
        frame 1 is R1-only).         ``pulse_through``: hold pulse keys from ``pulse_from``
        through end of step (knife swing) instead of on/off blink.

        ``frame_buttons``: optional length-``n`` list of full per-frame button
        dicts (knife macro). Bypasses sticky/pulse merge in Lua; still updates
        latched directions from ``sticky`` at step start.

        ``echo_joypad``: Lua reads back ``joypad.get()`` after every frame
        advance; the per-frame "held buttons" strings land in
        ``self.last_step_echo`` (input-delivery QA, e.g. knife macro).

        Sticky keys stay latched across steps until updated or cleared via noop.

        Returns (frame, death_during_step).
        """
        req: dict[str, Any] = {"cmd": "step", "n": int(n)}
        if echo_joypad:
            req["echo_joypad"] = True
        if frame_buttons is not None:
            req["sticky"] = sticky or {}
            req["frame_buttons"] = frame_buttons
            req["n"] = len(frame_buttons)
        elif sticky is not None:
            req["sticky"] = sticky
            req["pulse"] = pulse or {}
            req["pulse_hold"] = pulse_hold or {}
            req["pulse_on"] = int(pulse_frames_on)
            req["pulse_off"] = int(pulse_frames_off)
            req["pulse_from"] = int(pulse_from)
            req["pulse_through"] = bool(pulse_through)
        else:
            req["buttons"] = buttons or {}
        if death_hp_addr is not None:
            req["death_hp_addr"] = int(death_hp_addr)
            req["abort_on_zero_hp"] = bool(abort_on_zero_hp)
        resp = self._request(req)
        echo_raw = resp.get("joypad_echo")
        self.last_step_echo = (
            [str(s) for s in echo_raw] if isinstance(echo_raw, list) else None
        )
        return (
            int(resp.get("frame", -1)),
            bool(resp.get("death_during_step", False)),
        )

    def fast_forward(
        self,
        max_frames: int,
        *,
        mode_addr: int,
        mask: int,
        speed: int = 6400,
        restore_speed: int = 100,
        invisible: bool = True,
        msg_addr: int | None = None,
        msg_mask: int = 0x80,
        scene_addr: int | None = None,
        scene_mask: int = 0x10,
        death_hp_addr: int | None = None,
        abort_on_zero_hp: bool = False,
    ) -> dict[str, int | bool]:
        """Burn uncontrolled/dialogue/scene frames Lua-side (one round-trip
        per chunk).

        The Lua loop applies engine patches with turbo forced on. Pure
        cutscenes/doors advance with no button input; cross is tapped only for
        modal dialogue or scripted scene spans while ANY of these hold:
          - ``(u8@mode_addr & mask) == 0``          (cutscene / door)
          - ``(u8@msg_addr & msg_mask) != 0``       (modal message window)
          - ``scene_active_from_ram(u8@scene_addr)`` (bit 0x10 or Kenneth 0x84)
        """
        req: dict[str, Any] = {
            "cmd": "fast_forward",
            "max_frames": int(max_frames),
            "mode_addr": int(mode_addr),
            "mask": int(mask),
            "speed": int(speed),
            "restore_speed": int(restore_speed),
            "invisible": bool(invisible),
        }
        if msg_addr is not None:
            req["msg_addr"] = int(msg_addr)
            req["msg_mask"] = int(msg_mask)
        if scene_addr is not None:
            req["scene_addr"] = int(scene_addr)
            req["scene_mask"] = int(scene_mask)
        if death_hp_addr is not None:
            req["death_hp_addr"] = int(death_hp_addr)
            req["abort_on_zero_hp"] = bool(abort_on_zero_hp)
        resp = self._request(req)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "fast_forward failed"))
        return {
            "burned": int(resp.get("burned", 0)),
            "mode": int(resp.get("mode", 0)),
            "in_control": bool(resp.get("in_control", False)),
            "msg_open": bool(resp.get("msg_open", False)),
            "scene_active": bool(resp.get("scene_active", False)),
            "death_abort": bool(resp.get("death_abort", False)),
            "frame": int(resp.get("frame", -1)),
        }

    def load_savestate(self, path: str) -> None:
        self._request({"cmd": "loadstate", "path": path})

    def reboot(self) -> None:
        """Power-cycle the core (MainRAM + disc position reset; memory cards persist)."""
        self._request({"cmd": "reboot"})

    def save_savestate(self, path: str) -> None:
        self._request({"cmd": "savestate", "path": path})

    def set_speed(self, percent: int) -> None:
        self._request({"cmd": "speed", "percent": int(percent)})

    def set_invisible(self, on: bool) -> None:
        """Toggle BizHawk invisible emulation (no rendering; max speed)."""
        self._request({"cmd": "invisible", "on": bool(on)})

    def quit(self) -> None:
        try:
            self._request({"cmd": "quit"})
        except (OSError, ConnectionError):
            pass

    def screenshot(self, path: str | None = None) -> np.ndarray:
        """Ask BizHawk to write a PNG, then read it back as RGB uint8 (H,W,3).

        File-based transfer avoids base64-over-socket issues and works across
        BizHawk builds. ``path`` defaults to the shared frame file the Lua
        client writes to.
        """
        shot_path = path or self.screenshot_path
        resp = self._request({"cmd": "screenshot", "path": shot_path})
        written = resp.get("path", shot_path)
        import cv2

        # BizHawk writes asynchronously; retry briefly for the file to appear.
        bgr = None
        for _ in range(50):
            bgr = cv2.imread(written, cv2.IMREAD_COLOR)
            if bgr is not None:
                break
            time.sleep(0.005)
        if bgr is None:
            raise ValueError(f"Failed to read screenshot PNG from BizHawk at {written}")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
