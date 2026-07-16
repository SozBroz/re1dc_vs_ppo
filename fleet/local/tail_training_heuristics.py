#!/usr/bin/env python3
"""Combined live tail of RE1 training heuristic lines from pking + wh1 + wh2.

Shows progression / exploration signals from TrainingProgressTracker and related
fleet lines:

  [episode]   per-episode rooms / keys / weapons / items / waypoint / fail
  [PB-rooms]  new personal-best room count on that machine
  [progress]  first room visit, pickups, first-held items
  [rollout]   epoch/rollout aggregates (best_ep_rooms, hit counters, room set)

Optional:
  --include-attacks  also show ``[attack_swing]`` / ``[attack_fail]`` macro lines
  --include-trains   also show learner ``epoch train ...`` / ``epoch train failed``

Usage (from D:\\re1_rl on pking):
  python fleet/local/tail_training_heuristics.py
  python fleet/local/tail_training_heuristics.py --last 40
  python fleet/local/tail_training_heuristics.py --last 40 --include-trains
  python fleet/local/tail_training_heuristics.py --last 20 --no-follow
  python fleet/local/tail_training_heuristics.py --include-attacks --include-trains
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

DEFAULT_SOURCES: tuple[tuple[str, str | None, Path], ...] = (
    ("pking", None, Path(r"D:\re1_rl\data\logs\worker_pking.log")),
    ("wh1", "workhorse1", Path(r"D:\re1_rl\data\logs\worker_workhorse1.log")),
    ("wh2", "workhorse2", Path(r"C:\Users\sshuser\re1_rl\data\logs\learner_wh2_25.log")),
)

REMOTE_TAIL_LINES = 50_000
FLEET_HOSTS_JSON = Path(__file__).resolve().parents[1] / "fleet_hosts.json"

# Heuristic tags emitted by training_progress.TrainingProgressTracker.
BASE_HEURISTIC_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\[episode\]"),
    re.compile(r"\[PB-rooms\]"),
    re.compile(r"\[progress\]"),
    re.compile(r"\[rollout\].*ep_rew="),
)
ATTACK_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\[attack_swing\]"),
    re.compile(r"\[attack_fail\]"),  # legacy; pre-attack_swing logs
)
TRAIN_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"epoch train \d+ steps"),
    re.compile(r"epoch train failed"),
)


def _heuristic_res(*, include_attacks: bool) -> tuple[re.Pattern[str], ...]:
    if include_attacks:
        return BASE_HEURISTIC_RES + ATTACK_RES
    return BASE_HEURISTIC_RES


def line_matches(
    line: str, *, include_trains: bool, include_attacks: bool
) -> bool:
    if any(rx.search(line) for rx in _heuristic_res(include_attacks=include_attacks)):
        return True
    return include_trains and any(rx.search(line) for rx in TRAIN_RES)


def encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _load_fleet_ips() -> dict[str, str]:
    ips: dict[str, str] = {}
    if FLEET_HOSTS_JSON.is_file():
        try:
            data = json.loads(FLEET_HOSTS_JSON.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        else:
            for key in ("workhorse1", "workhorse2"):
                entry = data.get(key)
                if isinstance(entry, dict):
                    ip = entry.get("lan_ip")
                    if isinstance(ip, str) and ip:
                        ips[key] = ip
    for key, env_name in (
        ("workhorse1", "FLEET_WH1_HOST"),
        ("workhorse2", "FLEET_WH2_HOST"),
    ):
        env_ip = os.environ.get(env_name, "").strip()
        if env_ip:
            ips[key] = env_ip
    return ips


def resolve_ssh_host(host: str) -> str:
    """Map fleet alias to sshuser@<lan_ip>; pass through user@host targets."""
    if "@" in host:
        return host
    ip = _load_fleet_ips().get(host)
    if ip:
        return f"sshuser@{ip}"
    return host


def _ps_match_clause(*, include_trains: bool, include_attacks: bool) -> str:
    clauses = [
        "$_ -match '\\[episode\\]'",
        "$_ -match '\\[PB-rooms\\]'",
        "$_ -match '\\[progress\\]'",
        "$_ -match '\\[rollout\\].*ep_rew='",
    ]
    if include_attacks:
        clauses.append("$_ -match '\\[attack_swing\\]'")
        clauses.append("$_ -match '\\[attack_fail\\]'")
    if include_trains:
        clauses.append("$_ -match 'epoch train \\d+ steps'")
        clauses.append("$_ -match 'epoch train failed'")
    return " -or ".join(clauses)


def recent_local(
    path: Path, count: int, *, include_trains: bool, include_attacks: bool
) -> list[str]:
    if not path.is_file():
        return []
    matched: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line_matches(
                line, include_trains=include_trains, include_attacks=include_attacks
            ):
                matched.append(line.rstrip("\n\r"))
    return matched[-count:]


def recent_remote(
    host: str, path: Path, count: int, *, include_trains: bool, include_attacks: bool
) -> list[str]:
    ssh_target = resolve_ssh_host(host)
    ps_path = str(path).replace("'", "''")
    match = _ps_match_clause(
        include_trains=include_trains, include_attacks=include_attacks
    )
    script = (
        f"$p = '{ps_path}'; "
        "if (-not (Test-Path -LiteralPath $p)) { exit 2 }; "
        f"Get-Content -LiteralPath $p -Tail {REMOTE_TAIL_LINES} | "
        f"Where-Object {{ {match} }}"
    )
    encoded = encode_powershell(script)
    try:
        proc = subprocess.run(
            [
                "ssh",
                ssh_target,
                "powershell",
                "-NoProfile",
                "-EncodedCommand",
                encoded,
            ],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    matched: list[str] = []
    for line in proc.stdout.splitlines():
        if line_matches(
            line, include_trains=include_trains, include_attacks=include_attacks
        ):
            matched.append(line.rstrip("\n\r"))
    return matched[-count:]


def emit(label: str, line: str) -> None:
    # Color-ish tags via ANSI when stdout is a TTY; plain otherwise.
    tag = ""
    if "[PB-rooms]" in line:
        tag = "PB"
    elif "[episode]" in line:
        tag = "EP"
    elif "[progress]" in line:
        tag = "PR"
    elif "[rollout]" in line:
        tag = "RO"
    elif "[attack_swing]" in line or "[attack_fail]" in line:
        tag = "ATK"
    elif "epoch train failed" in line:
        tag = "FAIL"
    elif "epoch train" in line:
        tag = "TR"
    prefix = f"[{label}:{tag}]" if tag else f"[{label}]"
    if sys.stdout.isatty():
        colors = {
            "PB": "\033[95m",
            "EP": "\033[96m",
            "PR": "\033[92m",
            "RO": "\033[93m",
            "ATK": "\033[31m",
            "TR": "\033[94m",
            "FAIL": "\033[91m",
        }
        reset = "\033[0m"
        color = colors.get(tag, "")
        print(f"{color}{prefix}{reset} {line}", flush=True)
    else:
        print(f"{prefix} {line}", flush=True)


def follow_local(
    label: str,
    path: Path,
    out_q: queue.Queue[str],
    *,
    include_trains: bool,
    include_attacks: bool,
) -> None:
    while not path.is_file():
        out_q.put(f"__status__:{label}:waiting for {path}")
        time.sleep(2.0)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if not line:
                time.sleep(0.2)
                continue
            if line_matches(
                line, include_trains=include_trains, include_attacks=include_attacks
            ):
                out_q.put(f"{label}\t{line.rstrip()}")


def follow_remote(
    label: str,
    host: str,
    path: Path,
    out_q: queue.Queue[str],
    *,
    include_trains: bool,
    include_attacks: bool,
) -> None:
    ps_path = str(path).replace("'", "''")
    match = _ps_match_clause(
        include_trains=include_trains, include_attacks=include_attacks
    )
    script = (
        f"$p = '{ps_path}'; "
        "while (-not (Test-Path -LiteralPath $p)) { Start-Sleep -Seconds 2 }; "
        f"Get-Content -LiteralPath $p -Tail 0 -Wait | Where-Object {{ {match} }}"
    )
    encoded = encode_powershell(script)
    proc = subprocess.Popen(
        [
            "ssh",
            resolve_ssh_host(host),
            "powershell",
            "-NoProfile",
            "-EncodedCommand",
            encoded,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n\r")
        if line and line_matches(
            line, include_trains=include_trains, include_attacks=include_attacks
        ):
            out_q.put(f"{label}\t{line}")
    err = proc.stderr.read() if proc.stderr is not None else ""
    if err.strip():
        out_q.put(f"__status__:{label}:ssh ended ({err.strip()[:200]})")


def print_recent(
    sources: tuple[tuple[str, str | None, Path], ...],
    count: int,
    *,
    include_trains: bool,
    include_attacks: bool,
) -> None:
    for label, host, path in sources:
        if host is None:
            lines = recent_local(
                path,
                count,
                include_trains=include_trains,
                include_attacks=include_attacks,
            )
        else:
            lines = recent_remote(
                host,
                path,
                count,
                include_trains=include_trains,
                include_attacks=include_attacks,
            )
        print(f"=== {label} (last {count}) ===", flush=True)
        if not lines:
            print(f"[{label}] (no recent heuristic lines)", flush=True)
            continue
        for line in lines:
            emit(label, line)
        print("", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--last",
        type=int,
        default=25,
        help="print last N matching lines per source before follow (default 25)",
    )
    parser.add_argument(
        "--no-follow",
        action="store_true",
        help="snapshot only; do not stream new lines",
    )
    parser.add_argument(
        "--include-attacks",
        action="store_true",
        help="also show [attack_swing] / [attack_fail] macro lines",
    )
    parser.add_argument(
        "--include-trains",
        action="store_true",
        help="also show learner epoch train success/fail lines",
    )
    args = parser.parse_args()

    if args.last > 0:
        print_recent(
            DEFAULT_SOURCES,
            args.last,
            include_trains=args.include_trains,
            include_attacks=args.include_attacks,
        )
        if args.no_follow:
            return 0
        print("", flush=True)

    out_q: queue.Queue[str] = queue.Queue()
    for label, host, path in DEFAULT_SOURCES:
        if host is None:
            target = follow_local
            kwargs = {
                "label": label,
                "path": path,
                "out_q": out_q,
                "include_trains": args.include_trains,
                "include_attacks": args.include_attacks,
            }
        else:
            target = follow_remote
            kwargs = {
                "label": label,
                "host": host,
                "path": path,
                "out_q": out_q,
                "include_trains": args.include_trains,
                "include_attacks": args.include_attacks,
            }
        threading.Thread(
            target=target, kwargs=kwargs, daemon=True, name=f"tail-{label}"
        ).start()

    modes = "episode/PB/progress/rollout"
    if args.include_attacks:
        modes += "/attack"
    if args.include_trains:
        modes += "/epoch-train"
    print(
        f"Following [{modes}] from pking + wh1 + wh2 (Ctrl+C to stop)...",
        flush=True,
    )
    try:
        while True:
            try:
                item = out_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item.startswith("__status__:"):
                _, label, message = item.split(":", 2)
                print(f"[{label}] {message}", file=sys.stderr, flush=True)
                continue
            label, line = item.split("\t", 1)
            emit(label, line)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
