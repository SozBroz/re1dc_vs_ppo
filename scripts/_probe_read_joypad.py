"""Verify read_joypad bridge command."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 7778

def main():
    from re1_rl.bizhawk_bridge import BizHawkClient
    bridge = BizHawkClient(port=PORT, timeout=30.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [str(ROOT / "tools/BizHawk-2.11.1/EmuHawk.exe"),
         str(ROOT / "roms/Resident Evil - Director's Cut.cue"),
         f"--lua={ROOT / 'lua/re1_client.lua'}",
         "--socket_ip=127.0.0.1", f"--socket_port={PORT}"],
        cwd=str(ROOT / "tools/BizHawk-2.11.1"),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    bridge.wait_for_client()
    for i in range(5):
        j = bridge.read_joypad()
        on = [k for k, v in j.items() if v]
        print(f"{i}: {on or '(none)'}", flush=True)
        time.sleep(0.5)
    bridge.quit()
    bridge.close()
    proc.terminate()

if __name__ == "__main__":
    main()
