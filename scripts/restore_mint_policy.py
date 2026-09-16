#!/usr/bin/env python3
"""Restore archived mint/hunt weights onto the live WH3 learner.

Mint cells stamp ``meta.mint_policy.policy_version`` →
``data/mint_policies/weights/v<V>.pt``. After a march pin advance the learner
reloads base weights; this script POSTs ``/march/reset_weights`` with
``mint_policy_version`` so the train loop swaps that archive back in and
publishes a new fleet policy version.

Examples:
  # pl52→pl53 success mint (WH1, policy_version 90)
  venv\\Scripts\\python.exe -u scripts/restore_mint_policy.py --version 90

  # pl51→pl52 mint (WH2, policy_version 68)
  venv\\Scripts\\python.exe -u scripts/restore_mint_policy.py --version 68
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _learner() -> tuple[str, int]:
    host = (
        os.environ.get("RE1_LEARNER_HOST")
        or os.environ.get("FLEET_LEARNER_HOST")
        or "192.168.0.229"
    ).strip()
    port = int(
        os.environ.get("RE1_LEARNER_PORT")
        or os.environ.get("FLEET_LEARNER_PORT")
        or "8765"
    )
    return host, port


def _get(url: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(url: str, payload: dict, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--version",
        type=int,
        required=True,
        help="mint_policies archive version (e.g. 90 for pl52 hunt success)",
    )
    ap.add_argument(
        "--wait-s",
        type=float,
        default=90.0,
        help="seconds to wait for the learner train loop to apply the swap",
    )
    ap.add_argument(
        "--advance-id",
        default="",
        help="optional advance_id tag (default: restore-v<V>-<unix>)",
    )
    args = ap.parse_args()
    version = int(args.version)
    if version <= 0:
        print("ERROR: --version must be > 0", flush=True)
        return 2

    from re1_rl.distributed.weight_archive import find_weight_file

    local = find_weight_file(ROOT, version)
    print(
        f"[restore] local archive: "
        f"{local if local else f'MISSING data/mint_policies/weights/v{version}.pt'}",
        flush=True,
    )

    host, port = _learner()
    base = f"http://{host}:{port}"
    try:
        before = _get(f"{base}/weights/version")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ERROR: learner unreachable at {base}: {exc}", flush=True)
        return 2
    before_v = int(before.get("policy_version") or 0)
    print(f"[restore] learner {base} policy_version={before_v}", flush=True)

    advance_id = str(args.advance_id or "").strip() or f"restore-v{version}-{int(time.time())}"
    try:
        ack = _post(
            f"{base}/march/reset_weights",
            {
                "advance_id": advance_id,
                "mint_policy_version": version,
            },
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ERROR: POST /march/reset_weights failed: {exc}", flush=True)
        return 2
    print(f"[restore] queued: {ack}", flush=True)

    deadline = time.time() + float(args.wait_s)
    last = before_v
    while time.time() < deadline:
        time.sleep(2.0)
        try:
            cur = _get(f"{base}/weights/version")
            last = int(cur.get("policy_version") or 0)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            continue
        if last > before_v:
            print(
                f"[restore] OK fleet policy_version {before_v} -> {last} "
                f"(from mint archive v{version}, advance_id={advance_id})",
                flush=True,
            )
            return 0
    print(
        f"[restore] TIMEOUT waiting for apply "
        f"(still policy_version={last}; learner may need code with "
        f"mint_policy_version support, or archive missing on WH3)",
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
