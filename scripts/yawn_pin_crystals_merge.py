#!/usr/bin/env python3
"""Pin Crystals_in_time cp00-18 + pking cp19, then quality-merge cp20+.

Operator restore after a learner weight save / fleet teardown. Pins skip the
quality gate. cp20+ keep the lexicographic quality winner across pking/WH1/WH2.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from re1_rl.go_explore_archive import quality_beats
from re1_rl.go_explore_merge import CELL_META_NAME, CELL_SIDECAR_NAME, CELL_STATE_NAME
from re1_rl.yawn_rails_sync import (
    cell_dir_name,
    cell_slot_dir,
    sha256_file,
    try_install_yawn_cell,
    yawn_rails_root,
)

PIN_THROUGH = 19
BACKUP_DEFAULT = ROOT / "backups" / "Crystals_in_time"
HOSTS = {
    "pking": {"kind": "local", "root": ROOT},
    "wh1": {"kind": "ssh", "ssh": "sshuser@192.168.0.203", "root": Path(r"D:\re1_rl")},
    "wh2": {
        "kind": "ssh",
        "ssh": "sshuser@192.168.0.116",
        "root": Path(r"C:\Users\sshuser\re1_rl"),
    },
}


def _quality(row: dict[str, Any] | None) -> list[int] | None:
    if not isinstance(row, dict):
        return None
    raw = row.get("quality")
    if not isinstance(raw, (list, tuple)) or len(raw) < 5:
        return None
    try:
        return [int(x) for x in raw]
    except (TypeError, ValueError):
        return None


def _load_manifest_cells(path: Path) -> dict[int, dict[str, Any]]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    out: dict[int, dict[str, Any]] = {}
    for row in raw.get("cells") or []:
        if not isinstance(row, dict):
            continue
        try:
            out[int(row["checkpoint_index"])] = row
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _row_from_cell_dir(cell_dir: Path, idx: int) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    meta_p = cell_dir / CELL_META_NAME
    if meta_p.is_file():
        try:
            loaded = json.loads(meta_p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            meta = loaded
    side: dict[str, Any] = {}
    side_p = cell_dir / CELL_SIDECAR_NAME
    if side_p.is_file():
        try:
            loaded = json.loads(side_p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            side = loaded
    quality = _quality(meta) or _quality(side) or [1, 0, 0, 1, 1]
    return {
        "checkpoint_index": idx,
        "checkpoint_id": str(meta.get("checkpoint_id") or ""),
        "room_id": str(meta.get("room_id") or side.get("captured_room_id") or ""),
        "route_id": str(meta.get("route_id") or "yawn_quest_v2"),
        "quality": list(quality),
        **{
            key: meta[key]
            for key in (
                "inventory_free_slots",
                "next_checkpoint_id",
                "next_slots_needed",
                "inventory_feasible",
                "captured_in_box_room",
            )
            if key in meta
        },
    }


def _ssh(host: str, cmd: str) -> bool:
    cfg = HOSTS[host]
    proc = subprocess.run(
        [
            "ssh.exe",
            "-o",
            "ConnectTimeout=20",
            "-o",
            "BatchMode=yes",
            str(cfg["ssh"]),
            cmd,
        ],
        check=False,
        capture_output=True,
    )
    return proc.returncode == 0


def _scp(src: str, dest: str) -> bool:
    proc = subprocess.run(
        ["scp.exe", "-o", "ConnectTimeout=30", "-o", "BatchMode=yes", src, dest],
        check=False,
        capture_output=True,
    )
    return proc.returncode == 0


def _fetch_remote_manifest(host: str, dest: Path) -> dict[int, dict[str, Any]]:
    cfg = HOSTS[host]
    remote = cfg["root"] / "states" / "yawn_rails" / "manifest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    ok = _scp(f"{cfg['ssh']}:{remote.as_posix()}", str(dest))
    if not ok:
        print(f"warn: could not fetch {host} manifest", flush=True)
        return {}
    return _load_manifest_cells(dest)


def _fetch_remote_cell(host: str, idx: int, dest: Path) -> bool:
    cfg = HOSTS[host]
    remote = cfg["root"] / "states" / "yawn_rails" / "cells" / cell_dir_name(idx)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    ssh = str(cfg["ssh"])
    remote_s = remote.as_posix()
    ok_state = _scp(f"{ssh}:{remote_s}/{CELL_STATE_NAME}", str(dest / CELL_STATE_NAME))
    ok_side = _scp(
        f"{ssh}:{remote_s}/{CELL_SIDECAR_NAME}", str(dest / CELL_SIDECAR_NAME)
    )
    _scp(f"{ssh}:{remote_s}/{CELL_META_NAME}", str(dest / CELL_META_NAME))
    _scp(f"{ssh}:{remote_s}/leg_replay.json", str(dest / "leg_replay.json"))
    return ok_state and ok_side and (dest / CELL_STATE_NAME).is_file()


def _install(project_root: Path, idx: int, staged: Path, *, force: bool) -> bool:
    row = _row_from_cell_dir(staged, idx)
    return try_install_yawn_cell(
        project_root,
        checkpoint_index=idx,
        staged_dir=staged,
        quality=row["quality"],
        row=row,
        holder="yawn_pin_crystals",
        force=force,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-root", default=str(ROOT))
    ap.add_argument("--backup", default=str(BACKUP_DEFAULT))
    ap.add_argument("--staging", default=str(ROOT / "_tmp" / "yawn_pin_staging"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--push-fleet", action="store_true")
    args = ap.parse_args()
    project = Path(args.project_root).resolve()
    backup = Path(args.backup).resolve()
    staging = Path(args.staging).resolve()
    yr = yawn_rails_root(project)
    staging.mkdir(parents=True, exist_ok=True)
    print(f"fetching remote manifests apply={args.apply} push={args.push_fleet}", flush=True)

    local_cells = _load_manifest_cells(yr / "manifest.json")
    remote_cells = {
        "wh1": _fetch_remote_manifest("wh1", staging / "wh1_manifest.json"),
        "wh2": _fetch_remote_manifest("wh2", staging / "wh2_manifest.json"),
    }

    installed: list[int] = []
    skipped: list[str] = []

    for idx in range(0, PIN_THROUGH):
        src = backup / cell_dir_name(idx)
        if not (src / CELL_STATE_NAME).is_file():
            skipped.append(f"cp{idx:02d} missing in backup")
            continue
        print(f"pin cp{idx:02d} from {src}")
        if args.apply:
            if _install(project, idx, src, force=True):
                installed.append(idx)
            else:
                skipped.append(f"cp{idx:02d} pin install failed")

    src19 = yr / "cells" / "cp19"
    if not (src19 / CELL_STATE_NAME).is_file():
        skipped.append("cp19 missing on pking")
    else:
        print(f"pin cp19 from pking {src19}")
        if args.apply:
            pin19 = staging / "cp19_pking"
            if pin19.exists():
                shutil.rmtree(pin19, ignore_errors=True)
            shutil.copytree(src19, pin19)
            if _install(project, 19, pin19, force=True):
                installed.append(19)
            else:
                skipped.append("cp19 pin install failed")

    idxs = set(local_cells)
    idxs.update(remote_cells.get("wh1") or {})
    idxs.update(remote_cells.get("wh2") or {})
    for idx in sorted(i for i in idxs if i > PIN_THROUGH):
        cands: list[tuple[str, list[int], dict[str, Any]]] = []
        for host, rows in (
            ("pking", local_cells),
            ("wh1", remote_cells.get("wh1") or {}),
            ("wh2", remote_cells.get("wh2") or {}),
        ):
            q = _quality(rows.get(idx))
            if q is None:
                continue
            cands.append((host, q, rows[idx]))
        if not cands:
            continue
        winner_host, winner_q, _row = cands[0]
        for host, q, row in cands[1:]:
            if quality_beats(q, winner_q):
                winner_host, winner_q, _row = host, q, row
        print(f"merge cp{idx:02d} winner={winner_host} q={winner_q}")
        if not args.apply:
            continue
        if winner_host == "pking":
            continue
        dest = staging / cell_dir_name(idx)
        if not _fetch_remote_cell(winner_host, idx, dest):
            skipped.append(f"cp{idx:02d} fetch from {winner_host} failed")
            continue
        if _install(project, idx, dest, force=True):
            installed.append(idx)
        else:
            skipped.append(f"cp{idx:02d} merge install failed")

    print(f"installed={ [f'cp{i:02d}' for i in installed] }")
    if skipped:
        print("skipped:")
        for line in skipped:
            print(f"  {line}")
    if not args.apply:
        print("dry-run (pass --apply to write)", flush=True)
        if not args.push_fleet:
            return 0 if not skipped else 1

    if args.push_fleet:
        tar_path = staging / "yawn_rails_canonical.tar"
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        names = ["manifest.json", "cells"]
        if (yr / "store.json").is_file():
            names.insert(1, "store.json")
        proc = subprocess.run(
            ["tar.exe", "-cf", str(tar_path), "-C", str(yr), *names],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"tar failed: {proc.stderr or proc.stdout}", flush=True)
            return 1
        failed: list[str] = []
        for host in ("wh1", "wh2"):
            cfg = HOSTS[host]
            remote_root = cfg["root"] / "states" / "yawn_rails"
            remote_tmp = cfg["root"] / "_tmp"
            remote_tar = remote_tmp / "yawn_rails_canonical.tar"
            _ssh(host, f'if not exist "{remote_tmp}" mkdir "{remote_tmp}"')
            if not _scp(str(tar_path), f"{cfg['ssh']}:{remote_tar.as_posix()}"):
                failed.append(f"{host} tar scp")
                continue
            ok = _ssh(
                host,
                f'cd /d "{remote_root}" && tar.exe -xf "{remote_tar}"',
            )
            if not ok:
                failed.append(f"{host} tar extract")
                continue
            print(f"pushed canonical yawn_rails archive to {host}", flush=True)
        if failed:
            print("push failures:", flush=True)
            for line in failed:
                print(f"  {line}", flush=True)
            return 1

        for idx in range(0, PIN_THROUGH + 1):
            slot = cell_slot_dir(yr, idx)
            state = slot / CELL_STATE_NAME
            if not state.is_file():
                print(f"VERIFY FAIL cp{idx:02d} missing State", flush=True)
                continue
            print(f"VERIFY local cp{idx:02d} sha={sha256_file(state)[:16]}", flush=True)
    return 0 if not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
