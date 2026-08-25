#!/usr/bin/env python3
"""Force Yawn rails cell sync outside the 60s worker poll / 6m rollout flush.

Typical watch workflow on pking::

    python scripts/yawn_rails_sync_now.py pull

Pulls the learner manifest and eagerly mirrors every changed ``cpNN`` bundle
into ``states/yawn_rails/``. Use ``--full`` to re-download all cells.

Push local captures to the learner (quality-gated, no training queue)::

    python scripts/yawn_rails_sync_now.py push --cell cp23
    python scripts/yawn_rails_sync_now.py push --all-local

Learner host/port: ``RE1_LEARNER_HOST`` / ``FLEET_LEARNER_HOST`` (default
192.168.0.229) and ``RE1_LEARNER_PORT`` / ``FLEET_LEARNER_PORT`` (8765).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from re1_rl.distributed.worker_client import WorkerClient
from re1_rl.go_explore_merge import CELL_META_NAME, CELL_SIDECAR_NAME, CELL_STATE_NAME
from re1_rl.yawn_rails_sync import (
    build_capture_proposal,
    cell_dir_name,
    cell_slot_dir,
    yawn_rails_root,
)
from re1_rl.yawn_rails_worker_cache import (
    load_local_yawn_manifest,
    poll_yawn_rails_manifest,
)


def _learner_client(host: str | None, port: int | None, *, timeout: float) -> WorkerClient:
    resolved_host = (
        (host or "").strip()
        or os.environ.get("RE1_LEARNER_HOST", "").strip()
        or os.environ.get("FLEET_LEARNER_HOST", "").strip()
        or "192.168.0.229"
    )
    port_raw = (
        str(port if port is not None else "")
        or os.environ.get("RE1_LEARNER_PORT", "").strip()
        or os.environ.get("FLEET_LEARNER_PORT", "").strip()
        or "8765"
    )
    return WorkerClient(
        resolved_host,
        int(port_raw),
        machine_name="yawn_sync_now",
        timeout=float(timeout),
    )


def _format_cell_row(row: dict[str, Any]) -> str:
    idx = int(row.get("checkpoint_index", -1))
    cid = str(row.get("checkpoint_id") or "")
    room = str(row.get("room_id") or "")
    quality = list(row.get("quality") or [])
    ammo = quality[1] if len(quality) > 1 else "?"
    hp = quality[0] if quality else "?"
    return f"cp{idx:02d} {cid:28s} room={room:4s} hp={hp:>3} ammo={ammo:>3}"


def _print_manifest_summary(label: str, manifest: dict[str, Any]) -> None:
    cells = sorted(
        [c for c in (manifest.get("cells") or []) if isinstance(c, dict)],
        key=lambda r: int(r.get("checkpoint_index", 0)),
    )
    print(
        f"{label}: archive_version={manifest.get('archive_version')} "
        f"cells={len(cells)} route={manifest.get('route_id')}"
    )
    if not cells:
        return
    tail = cells[-5:]
    if len(cells) > len(tail):
        print(f"  ... ({len(cells) - len(tail)} earlier)")
    for row in tail:
        print(f"  {_format_cell_row(row)}")


def cmd_status(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    client = _learner_client(args.learner_host, args.learner_port, timeout=args.timeout)
    if not client.health():
        print(f"learner unreachable: {client.base}", file=sys.stderr)
        return 1
    local = load_local_yawn_manifest(project_root)
    remote = client.fetch_yawn_rails_manifest(since_version=0)
    _print_manifest_summary("local ", local)
    _print_manifest_summary("remote", remote)
    local_ver = int(local.get("archive_version", 0) or 0)
    remote_ver = int(remote.get("archive_version", 0) or 0)
    if local_ver < remote_ver:
        print("action: run `pull` to update local cells")
    elif local_ver > remote_ver:
        print("action: local ahead of learner — consider `push`")
    else:
        print("action: versions match")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()

    def _once() -> dict[str, Any]:
        client = _learner_client(args.learner_host, args.learner_port, timeout=args.timeout)
        if not client.health():
            raise RuntimeError(f"learner unreachable: {client.base}")
        local_before = load_local_yawn_manifest(project_root)
        since = 0 if args.full else int(local_before.get("archive_version", 0) or 0)
        local_after = poll_yawn_rails_manifest(client, project_root, since_version=since)
        stats = dict(local_after.get("cache_stats") or {})
        print(
            f"pulled archive_version={local_after.get('archive_version')} "
            f"cells={len(local_after.get('cells') or [])} "
            f"fetched={stats.get('fetched_last_poll', 0)} "
            f"pruned={stats.get('pruned_dirs_last_poll', 0)}"
        )
        changed = list(local_after.get("cells") or [])
        if args.full:
            show = changed[-8:]
        else:
            before_idx = {
                int(r["checkpoint_index"])
                for r in (local_before.get("cells") or [])
                if isinstance(r, dict) and "checkpoint_index" in r
            }
            show = [
                r
                for r in changed
                if isinstance(r, dict)
                and int(r.get("checkpoint_index", -1)) not in before_idx
            ]
            if not show and int(stats.get("fetched_last_poll", 0) or 0) > 0:
                show = changed[-3:]
        for row in show:
            print(f"  {_format_cell_row(row)}")
        if not show and int(stats.get("fetched_last_poll", 0) or 0) == 0:
            print("  (no new bundles)")
        return local_after

    repeat = float(args.repeat or 0.0)
    if repeat <= 0:
        try:
            _once()
        except Exception as exc:
            print(f"pull failed: {exc}", file=sys.stderr)
            return 1
        return 0

    print(f"watching learner every {repeat:.0f}s (Ctrl+C to stop)")
    try:
        while True:
            try:
                _once()
            except Exception as exc:
                print(f"pull failed: {exc}", file=sys.stderr)
            time.sleep(repeat)
    except KeyboardInterrupt:
        print()
        return 0


def _parse_cell_spec(spec: str) -> int:
    text = str(spec).strip().lower()
    if text.startswith("cp"):
        text = text[2:]
    return int(text, 10)


def _proposal_from_cell_dir(
    cell_dir: Path,
    *,
    checkpoint_index: int,
    route_id: str,
    worker_id: str,
) -> dict[str, Any]:
    state_p = cell_dir / CELL_STATE_NAME
    side_p = cell_dir / CELL_SIDECAR_NAME
    if not state_p.is_file() or not side_p.is_file():
        raise FileNotFoundError(f"missing cell.State/sidecar in {cell_dir}")
    meta: dict[str, Any] = {}
    meta_p = cell_dir / CELL_META_NAME
    if meta_p.is_file():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    sidecar = json.loads(side_p.read_text(encoding="utf-8-sig"))
    quality = meta.get("quality")
    if not quality:
        from re1_rl.go_explore_capture import compute_quality

        quality = list(compute_quality(sidecar, state_bytes=state_p.read_bytes()))
    capacity = {
        key: meta.get(key)
        for key in (
            "inventory_free_slots",
            "next_checkpoint_id",
            "next_slots_needed",
            "inventory_feasible",
            "captured_in_box_room",
        )
        if key in meta
    }
    return build_capture_proposal(
        route_id=str(meta.get("route_id") or route_id),
        checkpoint_index=int(checkpoint_index),
        checkpoint_id=str(
            meta.get("checkpoint_id")
            or sidecar.get("checkpoint_id")
            or ""
        ),
        room_id=str(
            meta.get("room_id")
            or sidecar.get("captured_room_id")
            or ""
        ),
        quality=list(quality),
        state_path=state_p,
        sidecar_path=side_p,
        worker_id=worker_id,
        capacity=capacity,
    )


def _collect_push_dirs(args: argparse.Namespace, project_root: Path) -> list[tuple[int, Path]]:
    yr = yawn_rails_root(project_root)
    out: list[tuple[int, Path]] = []
    if args.all_local:
        cells_root = yr / "cells"
        if not cells_root.is_dir():
            raise FileNotFoundError(f"no cells dir: {cells_root}")
        for p in sorted(cells_root.iterdir()):
            if not p.is_dir() or not p.name.startswith("cp"):
                continue
            out.append((_parse_cell_spec(p.name), p))
        return out
    for spec in args.cell or []:
        idx = _parse_cell_spec(spec)
        cell_dir = Path(spec)
        if not cell_dir.is_absolute():
            if cell_dir.parts and cell_dir.parts[0] == "cells":
                cell_dir = yr / cell_dir
            elif not cell_dir.is_dir():
                cell_dir = cell_slot_dir(yr, idx)
        out.append((idx, cell_dir.resolve()))
    return out


def cmd_push(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    client = _learner_client(args.learner_host, args.learner_port, timeout=args.timeout)
    if not client.health():
        print(f"learner unreachable: {client.base}", file=sys.stderr)
        return 1
    try:
        cell_dirs = _collect_push_dirs(args, project_root)
    except (FileNotFoundError, ValueError) as exc:
        print(f"push failed: {exc}", file=sys.stderr)
        return 1
    if not cell_dirs:
        print("push failed: no cells selected", file=sys.stderr)
        return 1

    proposals: list[dict[str, Any]] = []
    for idx, cell_dir in cell_dirs:
        try:
            prop = _proposal_from_cell_dir(
                cell_dir,
                checkpoint_index=idx,
                route_id=str(args.route_id),
                worker_id="yawn_sync_now",
            )
        except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
            print(f"skip cp{idx:02d}: {exc}", file=sys.stderr)
            continue
        proposals.append(prop)
        print(f"queued cp{idx:02d} from {cell_dir}")

    if not proposals:
        print("push failed: no valid proposals", file=sys.stderr)
        return 1

    try:
        result = client.ingest_yawn_rails_proposals(proposals)
    except Exception as exc:
        print(f"push failed: {exc}", file=sys.stderr)
        return 1
    accepted = list(result.get("accepted") or [])
    print(
        f"pushed accepted={accepted} "
        f"archive_version={result.get('archive_version')} "
        f"cell_count={result.get('cell_count')}"
    )
    if args.pull_after:
        args.full = False
        return cmd_pull(args)
    return 0 if accepted else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--project-root",
        default=str(ROOT),
        help="repo root containing states/yawn_rails (default: repo)",
    )
    ap.add_argument("--learner-host", default=None, help="override learner HTTP host")
    ap.add_argument("--learner-port", type=int, default=None, help="override learner port")
    ap.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout seconds")

    sub = ap.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="compare local vs learner manifest")
    status.set_defaults(func=cmd_status)

    pull = sub.add_parser("pull", help="download learner cells to local store (default)")
    pull.add_argument(
        "--full",
        action="store_true",
        help="re-fetch every bundle (since_version=0)",
    )
    pull.add_argument(
        "--repeat",
        type=float,
        default=0.0,
        metavar="SEC",
        help="poll every SEC seconds until Ctrl+C (watch mode)",
    )
    pull.set_defaults(func=cmd_pull)

    push = sub.add_parser("push", help="ingest local cell dirs to learner")
    push.add_argument(
        "--cell",
        action="append",
        metavar="cpNN",
        help="cell id or path (repeatable)",
    )
    push.add_argument(
        "--all-local",
        action="store_true",
        help="push every cell under states/yawn_rails/cells/",
    )
    push.add_argument(
        "--route-id",
        default="yawn_quest_v2",
        help="route_id when meta.json lacks it",
    )
    push.add_argument(
        "--pull-after",
        action="store_true",
        help="run pull after successful push (same host learner+worker)",
    )
    push.set_defaults(func=cmd_push)

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
