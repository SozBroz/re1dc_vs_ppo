"""Sample fleet RAM/VRAM/disk + learner health overnight.

Writes JSONL to data/_fleet_overnight_monitor.jsonl (default 10h, 60s interval).
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_ROOT / "data" / "_fleet_overnight_monitor.jsonl"
LEARNER_URL = "http://192.168.0.116:8765/status"
PS_SAMPLE = r"""
$os = Get-CimInstance Win32_OperatingSystem
$totalGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
$freeGb = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
$usedGb = [math]::Round($totalGb - $freeGb, 2)
$ramPct = if ($totalGb -gt 0) { [math]::Round(($usedGb / $totalGb) * 100, 1) } else { $null }
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$diskFreeGb = if ($disk) { [math]::Round($disk.FreeSpace / 1GB, 2) } else { $null }
$diskTotalGb = if ($disk) { [math]::Round($disk.Size / 1GB, 2) } else { $null }
$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$emu = @(Get-Process -Name 'EmuHawk' -ErrorAction SilentlyContinue).Count
$py = @(Get-Process -Name 'python','pythonw' -ErrorAction SilentlyContinue).Count
$gpu = $null
try {
  $nv = & nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits 2>$null
  if ($nv) {
    $p = ($nv -split ',') | ForEach-Object { $_.Trim() }
    $gpu = @{ vram_used_mb = [int]$p[0]; vram_total_mb = [int]$p[1]; gpu_util_pct = [int]$p[2] }
  }
} catch {}
[pscustomobject]@{ cpu_pct = [math]::Round([double]$cpu, 1); ram_used_gb = $usedGb; ram_total_gb = $totalGb; ram_used_pct = $ramPct; disk_free_gb = $diskFreeGb; disk_total_gb = $diskTotalGb; emuhawk_count = $emu; python_count = $py; gpu = $gpu } | ConvertTo-Json -Compress
""".strip()

HOSTS = [
    {"name": "pking", "mode": "local"},
    {"name": "wh2", "mode": "ssh", "target": "sshuser@192.168.0.116"},
    {"name": "wh1", "mode": "ssh", "target": "sshuser@192.168.0.203"},
]


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _parse_json_line(text: str) -> dict:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(text[:200])


def _sample_local() -> dict:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", PS_SAMPLE],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout)[:300]}
    out = _parse_json_line(r.stdout)
    out["ok"] = True
    return out


def _sample_ssh(target: str) -> dict:
    enc = base64.b64encode(PS_SAMPLE.encode("utf-16le")).decode("ascii")
    r = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            target,
            f"powershell -NoProfile -EncodedCommand {enc}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout)[:300]}
    out = _parse_json_line(r.stdout)
    out["ok"] = True
    return out


def _learner_status() -> dict:
    try:
        with urllib.request.urlopen(LEARNER_URL, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return {"ok": True, "body": json.loads(body)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=10.0)
    parser.add_argument("--interval-s", type=int, default=60)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + args.hours * 3600.0
    print(f"[overnight] logging to {args.out} for {args.hours}h every {args.interval_s}s", flush=True)

    while time.time() < end:
        row = {"ts": _now(), "hosts": {}, "learner": _learner_status()}
        for host in HOSTS:
            if host["mode"] == "local":
                row["hosts"][host["name"]] = _sample_local()
            else:
                row["hosts"][host["name"]] = _sample_ssh(host["target"])
        with args.out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"[overnight] {row['ts']} learner_ok={row['learner'].get('ok')}", flush=True)
        time.sleep(max(args.interval_s, 1))


if __name__ == "__main__":
    main()
