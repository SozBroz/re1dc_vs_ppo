"""Report Go-Explore manifest redundancy, semantic buckets, and Yawn coverage.

Loads from canonical archive, local worker mirror, or learner HTTP manifest.

Usage:
    python scripts/go_explore_manifest_analytics.py
    python scripts/go_explore_manifest_analytics.py --learner http://192.168.0.116:8765
    python scripts/go_explore_manifest_analytics.py --manifest data/go_explore/local_manifest.json
    python scripts/go_explore_manifest_analytics.py --archive data/go_explore/archive.json --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.go_explore_analytics import (  # noqa: E402
    analyze_manifest,
    format_report_text,
    load_manifest_dict,
    load_manifest_from_archive,
    load_manifest_from_http,
    report_to_dict,
)


def _resolve_manifest(args: argparse.Namespace) -> tuple[dict, str]:
    if args.learner:
        base = str(args.learner).rstrip("/")
        return load_manifest_from_http(base), f"learner:{base}"
    if args.manifest:
        path = Path(args.manifest).resolve()
        return load_manifest_dict(path), str(path)
    if args.archive:
        path = Path(args.archive).resolve()
        return load_manifest_from_archive(path), str(path)
    # Default: local mirror then canonical archive.
    local = PROJECT_ROOT / "data" / "go_explore" / "local_manifest.json"
    archive = PROJECT_ROOT / "data" / "go_explore" / "archive.json"
    if local.is_file():
        return load_manifest_dict(local), str(local)
    if archive.is_file():
        return load_manifest_from_archive(archive), str(archive)
    raise FileNotFoundError(
        "no manifest source found; pass --learner, --manifest, or --archive"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Go-Explore manifest analytics")
    src = ap.add_mutually_exclusive_group()
    src.add_argument(
        "--learner",
        metavar="URL",
        help="Learner base URL (e.g. http://192.168.0.116:8765)",
    )
    src.add_argument(
        "--manifest",
        type=Path,
        help="Path to local_manifest.json or manifest snapshot JSON",
    )
    src.add_argument(
        "--archive",
        type=Path,
        help="Path to canonical archive.json (builds manifest from cells)",
    )
    ap.add_argument(
        "--pose-threshold",
        type=int,
        default=8,
        help="Flag semantic buckets with more than this many pose cells (default 8)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text report",
    )
    ap.add_argument(
        "--output",
        type=Path,
        help="Optional path to write report (text or JSON)",
    )
    args = ap.parse_args()

    try:
        manifest, source = _resolve_manifest(args)
    except (FileNotFoundError, ConnectionError, ValueError) as exc:
        print(f"[go_explore_analytics] FAIL: {exc}", flush=True)
        return 1

    report = analyze_manifest(
        manifest,
        source=source,
        pose_warn_threshold=max(1, int(args.pose_threshold)),
    )

    if args.json:
        payload = report_to_dict(report)
        text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    else:
        text = format_report_text(report) + "\n"

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[go_explore_analytics] wrote {out}", flush=True)

    print(text, end="" if text.endswith("\n") else "\n", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
