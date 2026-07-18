#!/usr/bin/env python3
"""Live combined tail of [episode] lines from pking, wh1, and wh2 training logs."""

from __future__ import annotations

import argparse
import base64
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

EPISODE_RE = re.compile(r"\[episode\]")
ROLLOUT_RE = re.compile(r"\[rollout\].*ep_rew=")


def line_matches(line: str, *, include_rollouts: bool) -> bool:
    if EPISODE_RE.search(line):
        return True
    return include_rollouts and ROLLOUT_RE.search(line) is not None


def encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def recent_local(path: Path, count: int, *, include_rollouts: bool) -> list[str]:
    if not path.is_file():
        return []
    matched: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line_matches(line, include_rollouts=include_rollouts):
                matched.append(line.rstrip("\n\r"))
    return matched[-count:]


def recent_remote(host: str, path: Path, count: int, *, include_rollouts: bool) -> list[str]:
    needle = 'findstr /C:"[episode]"'
    if include_rollouts:
        needle += ' /C:"[rollout]"'
    remote_cmd = f'{needle} "{path}"'
    try:
        proc = subprocess.run(
            ["ssh", host, remote_cmd],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except FileNotFoundError:
        return []
    if proc.returncode not in (0, 1):
        return []
    matched: list[str] = []
    for line in proc.stdout.splitlines():
        if line_matches(line, include_rollouts=include_rollouts):
            matched.append(line.rstrip("\n\r"))
    return matched[-count:]


def emit(label: str, line: str) -> None:
    print(f"[{label}] {line}", flush=True)


def follow_local(label: str, path: Path, out_q: queue.Queue[str], *, include_rollouts: bool) -> None:
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
            if line_matches(line, include_rollouts=include_rollouts):
                out_q.put(f"{label}\t{line.rstrip()}")


def follow_remote(label: str, host: str, path: Path, out_q: queue.Queue[str], *, include_rollouts: bool) -> None:
    rollout_clause = " -or $_ -match '\\[rollout\\].*ep_rew='" if include_rollouts else ""
    ps_path = str(path).replace("'", "''")
    script = (
        f"$p = '{ps_path}'; "
        "while (-not (Test-Path -LiteralPath $p)) { Start-Sleep -Seconds 2 }; "
        f"Get-Content -LiteralPath $p -Tail 0 -Wait | "
        "Where-Object { $_ -match '\\[episode\\]'" + rollout_clause + " }"
    )
    encoded = encode_powershell(script)
    proc = subprocess.Popen(
        ["ssh", host, "powershell", "-NoProfile", "-EncodedCommand", encoded],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n\r")
        if line:
            out_q.put(f"{label}\t{line}")
    err = proc.stderr.read() if proc.stderr is not None else ""
    if err.strip():
        out_q.put(f"__status__:{label}:ssh ended ({err.strip()})")


def print_recent(sources: tuple[tuple[str, str | None, Path], ...], count: int, *, include_rollouts: bool) -> None:
    for label, host, path in sources:
        if host is None:
            lines = recent_local(path, count, include_rollouts=include_rollouts)
        else:
            lines = recent_remote(host, path, count, include_rollouts=include_rollouts)
        if not lines:
            print(f"[{label}] (no recent lines)", flush=True)
            continue
        for line in lines:
            emit(label, line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--last", type=int, default=0, help="print last N matching lines per source before follow")
    parser.add_argument("--no-follow", action="store_true", help="snapshot only; do not stream new lines")
    parser.add_argument("--include-rollouts", action="store_true", help="also show [rollout] ep_rew= aggregates")
    args = parser.parse_args()

    if args.last > 0:
        print_recent(DEFAULT_SOURCES, args.last, include_rollouts=args.include_rollouts)
        if args.no_follow:
            return 0
        print("", flush=True)

    if args.no_follow and args.last <= 0:
        parser.error("use --last N for snapshot mode, or omit --no-follow to stream live")

    out_q: queue.Queue[str] = queue.Queue()
    threads: list[threading.Thread] = []
    for label, host, path in DEFAULT_SOURCES:
        if host is None:
            target = follow_local
            kwargs = {"label": label, "path": path, "out_q": out_q, "include_rollouts": args.include_rollouts}
        else:
            target = follow_remote
            kwargs = {
                "label": label,
                "host": host,
                "path": path,
                "out_q": out_q,
                "include_rollouts": args.include_rollouts,
            }
        thread = threading.Thread(target=target, kwargs=kwargs, daemon=True, name=f"tail-{label}")
        thread.start()
        threads.append(thread)

    print("Following [episode] lines from pking + wh1 + wh2 (Ctrl+C to stop)...", flush=True)
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
