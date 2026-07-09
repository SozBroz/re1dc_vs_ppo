"""Dump EmuHawk joypad.getimmediate() raw keys."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7780


def main() -> int:
    from re1_rl.bizhawk_bridge import BizHawkClient

    bridge = BizHawkClient(port=PORT, timeout=30.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(ROOT / "tools/BizHawk-2.11.1/EmuHawk.exe"),
            str(ROOT / "roms/Resident Evil - Director's Cut.cue"),
            f"--lua={ROOT / 'lua/re1_client.lua'}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
        ],
        cwd=str(ROOT / "tools/BizHawk-2.11.1"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        for i in range(5):
            out = bridge.read_joypad(debug=True)
            buttons, raw = out
            on = [k for k, v in buttons.items() if v]
            stick = {k: v for k, v in raw.items() if "Stick" in k}
            print(f"{i}: parsed={on or '(none)'} keys={len(raw)} stick={stick}", flush=True)
            time.sleep(0.3)
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        bridge.close()
        proc.terminate()
        proc.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
