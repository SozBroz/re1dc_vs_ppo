"""Watch fleet hosts for per-room typewriter champions; sync each slot; launch harness.

Polls pking / WH1 / WH2 ``champions/*/champion.json`` via local FS + SSH.
For **each slot independently**, keeps the best host's tree and copies that
slot onto every other host — never overwrites other rooms with a global winner.

When any champion first appears (or improves), prints the validate command and
optionally launches ``scripts/validate_typewriter_champion.cmd`` on this machine
(pking), preferring ``mainhall_typewriter`` if that slot improved.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHAMPS_REL = Path("states/pb/champions")
CHAMP_FILES = ("champion.State", "champion.sidecar.json", "champion.json")
LOCK_NAME = "champion.sync.lock"

HOSTS = {
    "pking": {"kind": "local", "root": PROJECT_ROOT},
    "workhorse1": {"kind": "ssh", "ssh": "sshuser@192.168.0.203", "root": "D:/re1_rl"},
    "workhorse2": {"kind": "ssh", "ssh": "sshuser@192.168.0.116", "root": "C:/Users/sshuser/re1_rl"},
}


def _is_slot_dirname(name: str) -> bool:
    return name == "mainhall_typewriter" or name.startswith("typewriter_")


def _score(rec: dict | None) -> tuple[int, ...] | None:
    if not rec or "score" not in rec:
        return None
    try:
        return tuple(int(x) for x in rec["score"])
    except (TypeError, ValueError):
        return None


def _score_version(rec: dict | None) -> int | None:
    if not rec or "score_version" not in rec:
        return None
    try:
        return int(rec["score_version"])
    except (TypeError, ValueError):
        return None


def _score_beats(candidate: tuple[int, ...], incumbent: tuple[int, ...] | None, *,
                 candidate_version: int, incumbent_version: int | None) -> bool:
    """Local copy of pb_champion.score_beats to keep the watch script standalone."""
    if incumbent is None:
        return True
    if incumbent_version == candidate_version:
        return tuple(candidate) > tuple(incumbent)
    if incumbent_version is None:
        if len(candidate) == len(incumbent):
            return tuple(candidate) > tuple(incumbent)
        return True
    if incumbent_version < candidate_version:
        return True
    return False


def _read_local_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _ssh_cat(ssh: str, remote_path: str) -> str | None:
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=12",
                "-o",
                "BatchMode=yes",
                ssh,
                f'type "{remote_path.replace("/", chr(92))}"',
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _ssh_dir_listing(ssh: str, remote_dir: str) -> list[str]:
    """``dir /b`` of a remote directory; empty on failure."""
    win = remote_dir.replace("/", chr(92))
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=12",
                "-o",
                "BatchMode=yes",
                ssh,
                f'dir /b "{win}"',
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def list_slot_names(host_id: str) -> set[str]:
    cfg = HOSTS[host_id]
    if cfg["kind"] == "local":
        root = Path(cfg["root"]) / CHAMPS_REL
        if not root.is_dir():
            return set()
        return {
            p.name
            for p in root.iterdir()
            if p.is_dir() and _is_slot_dirname(p.name)
        }
    remote = f'{cfg["root"]}/{CHAMPS_REL.as_posix()}'
    names = set()
    for name in _ssh_dir_listing(cfg["ssh"], remote):
        if _is_slot_dirname(name):
            names.add(name)
    return names


def fetch_all_records(host_id: str) -> dict[str, dict | None]:
    """Map slot_name → champion.json record (or None) for one host."""
    slots = list_slot_names(host_id)
    out: dict[str, dict | None] = {}
    for slot in slots:
        out[slot] = fetch_record(host_id, slot)
    return out


def fetch_record(host_id: str, slot_name: str) -> dict | None:
    cfg = HOSTS[host_id]
    rel = CHAMPS_REL / slot_name / "champion.json"
    if cfg["kind"] == "local":
        return _read_local_json(Path(cfg["root"]) / rel)
    remote = f'{cfg["root"]}/{rel.as_posix()}'
    text = _ssh_cat(cfg["ssh"], remote)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _remote_win_path(posix_root: str, rel: Path) -> str:
    return f"{posix_root}/{rel.as_posix()}".replace("/", "\\")


def _host_locked(host_id: str, slot_name: str) -> bool:
    """True if destination holds a fresh champion.sync.lock."""
    cfg = HOSTS[host_id]
    rel = CHAMPS_REL / slot_name / LOCK_NAME
    if cfg["kind"] == "local":
        return (Path(cfg["root"]) / rel).is_file()
    remote = f'{cfg["root"]}/{rel.as_posix()}'
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            "ConnectTimeout=12",
            "-o",
            "BatchMode=yes",
            cfg["ssh"],
            f'if exist "{_remote_win_path(cfg["root"], CHAMPS_REL / slot_name / LOCK_NAME)}" (exit 0) else (exit 1)',
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.returncode == 0


def pull_champion_tree(host_id: str, slot_name: str, dest_dir: Path) -> bool:
    cfg = HOSTS[host_id]
    dest_dir.mkdir(parents=True, exist_ok=True)
    rel = CHAMPS_REL / slot_name
    if _host_locked(host_id, slot_name):
        print(f"[watch] skip pull slot={slot_name} from={host_id}: locked", flush=True)
        return False
    if cfg["kind"] == "local":
        src = Path(cfg["root"]) / rel
        sys.path.insert(0, str(PROJECT_ROOT))
        from re1_rl.pb_bundle_io import verify_champion_bundle

        ok, reason = verify_champion_bundle(src, require_unlocked=True)
        if not ok:
            print(f"[watch] skip pull slot={slot_name} from={host_id}: {reason}", flush=True)
            return False
        for name in CHAMP_FILES:
            s = src / name
            if not s.is_file():
                return False
            shutil.copy2(s, dest_dir / name)
        return True

    remote_dir = f'{cfg["root"]}/{rel.as_posix()}'
    for name in CHAMP_FILES:
        remote = f"{cfg['ssh']}:{remote_dir}/{name}"
        local = dest_dir / name
        try:
            proc = subprocess.run(
                ["scp", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", remote, str(local)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if proc.returncode != 0:
            return False
    sys.path.insert(0, str(PROJECT_ROOT))
    from re1_rl.pb_bundle_io import verify_champion_bundle

    ok, reason = verify_champion_bundle(dest_dir, require_unlocked=False)
    if not ok:
        print(f"[watch] pulled incoherent slot={slot_name} from={host_id}: {reason}", flush=True)
        return False
    return True


def push_champion_tree(host_id: str, slot_name: str, src_dir: Path) -> bool:
    """Push one slot tree under lock. Never deletes or touches other slots."""
    cfg = HOSTS[host_id]
    rel = CHAMPS_REL / slot_name
    sys.path.insert(0, str(PROJECT_ROOT))
    from re1_rl.pb_bundle_io import install_champion_bundle, verify_champion_bundle

    ok, reason = verify_champion_bundle(src_dir, require_unlocked=False)
    if not ok:
        print(f"[watch] refuse push incoherent src slot={slot_name}: {reason}", flush=True)
        return False

    if cfg["kind"] == "local":
        dest = Path(cfg["root"]) / rel
        data = json.loads((src_dir / "champion.json").read_text(encoding="utf-8"))
        try:
            install_champion_bundle(
                dest,
                state_src=src_dir / "champion.State",
                sidecar_src=src_dir / "champion.sidecar.json",
                record=data,
                holder="watch:pking",
                bundle_id=str(data.get("bundle_id") or "") or None,
            )
        except RuntimeError as exc:
            print(f"[watch] push locked/fail slot={slot_name} -> {host_id}: {exc}", flush=True)
            return False
        return True

    if _host_locked(host_id, slot_name):
        print(f"[watch] skip push slot={slot_name} -> {host_id}: locked", flush=True)
        return False

    remote_dir = f'{cfg["root"]}/{rel.as_posix()}'
    win_dir = _remote_win_path(cfg["root"], rel)
    # Ensure remote slot dir exists (mkdir only — never wipe).
    subprocess.run(
        [
            "ssh",
            "-o",
            "ConnectTimeout=12",
            "-o",
            "BatchMode=yes",
            cfg["ssh"],
            f'mkdir "{win_dir}" 2>nul',
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # Plant lock first so workers skip this slot until State+sidecar+json land.
    lock_local = src_dir / LOCK_NAME
    lock_local.write_text(
        json.dumps({"holder": "watch:pking", "created_unix": time.time()}, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [
                "scp",
                "-o",
                "ConnectTimeout=12",
                "-o",
                "BatchMode=yes",
                str(lock_local),
                f"{cfg['ssh']}:{remote_dir}/{LOCK_NAME}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            return False
        for name in CHAMP_FILES:
            local = src_dir / name
            if not local.is_file():
                continue
            remote = f"{cfg['ssh']}:{remote_dir}/{name}"
            try:
                proc = subprocess.run(
                    ["scp", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", str(local), remote],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return False
            if proc.returncode != 0:
                return False
    finally:
        # Always drop remote + local lock so training is not wedged.
        subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=12",
                "-o",
                "BatchMode=yes",
                cfg["ssh"],
                f'del /f /q "{win_dir}\\{LOCK_NAME}" 2>nul',
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            lock_local.unlink()
        except OSError:
            pass
    return True


def best_host_for_slot(
    records_by_host: dict[str, dict | None],
) -> tuple[str, dict] | None:
    """Pick the best host for a single slot from host→record map."""
    best: tuple[str, dict, tuple[int, ...], int | None] | None = None
    for host_id, rec in records_by_host.items():
        sc = _score(rec)
        if sc is None or rec is None:
            continue
        ver = _score_version(rec)
        cand_ver = ver if ver is not None else 1
        if best is None:
            best = (host_id, rec, sc, ver)
            continue
        if _score_beats(
            sc,
            best[2],
            candidate_version=cand_ver,
            incumbent_version=best[3],
        ):
            best = (host_id, rec, sc, ver)
    if best is None:
        return None
    return best[0], best[1]


def launch_harness(state_path: Path) -> None:
    cmd = [
        "cmd",
        "/c",
        str(PROJECT_ROOT / "scripts" / "validate_typewriter_champion.cmd"),
        str(state_path),
    ]
    print(f"[watch] launching harness: {' '.join(cmd)}", flush=True)
    subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


def _normalize_json_paths(data: dict, slot_name: str) -> dict:
    sub = f"states/pb/champions/{slot_name}"
    data = dict(data)
    data["state_path"] = f"{sub}/champion.State"
    data["sidecar_path"] = f"{sub}/champion.sidecar.json"
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval-s", type=float, default=20.0)
    ap.add_argument("--once", action="store_true", help="one poll cycle then exit")
    ap.add_argument(
        "--launch-harness",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="open play_human validate console when a champion first appears (default on)",
    )
    args = ap.parse_args()

    # Per-slot last known best score (after fleet sync).
    last_scores: dict[str, tuple[int, ...]] = {}
    launched = False

    while True:
        # host → slot → record
        all_recs: dict[str, dict[str, dict | None]] = {
            hid: fetch_all_records(hid) for hid in HOSTS
        }
        all_slots: set[str] = set()
        for by_slot in all_recs.values():
            all_slots.update(by_slot.keys())

        summary: dict[str, dict[str, object]] = {}
        for hid, by_slot in all_recs.items():
            summary[hid] = {
                slot: (_score(rec), (rec or {}).get("hp"), (rec or {}).get("room_id"))
                for slot, rec in sorted(by_slot.items())
            }
        print(f"[watch] {time.strftime('%H:%M:%S')} slots={sorted(all_slots)} scores={summary}", flush=True)

        improved_slots: list[tuple[str, str, tuple[int, ...]]] = []
        # (slot, winner_host, score)

        for slot in sorted(all_slots):
            records = {hid: all_recs[hid].get(slot) for hid in HOSTS}
            winner = best_host_for_slot(records)
            if winner is None:
                continue
            host_id, rec = winner
            sc = _score(rec)
            assert sc is not None
            prev = last_scores.get(slot)
            cand_ver = _score_version(rec)
            cand_ver_n = cand_ver if cand_ver is not None else 1
            if not _score_beats(
                sc, prev, candidate_version=cand_ver_n, incumbent_version=None
            ):
                continue

            with tempfile.TemporaryDirectory(prefix=f"re1_pb_{slot}_") as tmp:
                tmp_path = Path(tmp)
                if not pull_champion_tree(host_id, slot, tmp_path):
                    print(f"[watch] pull failed slot={slot} from={host_id}", flush=True)
                    continue
                data = json.loads((tmp_path / "champion.json").read_text(encoding="utf-8"))
                data = _normalize_json_paths(data, slot)
                (tmp_path / "champion.json").write_text(
                    json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                for hid in HOSTS:
                    ok = push_champion_tree(hid, slot, tmp_path)
                    print(
                        f"[watch] push slot={slot} -> {hid}: {'ok' if ok else 'FAIL'}",
                        flush=True,
                    )
                local_state = PROJECT_ROOT / CHAMPS_REL / slot / "champion.State"
                print(
                    f"[watch] CHAMPION slot={slot} score={list(sc)} from={host_id}\n"
                    f"  validate: scripts\\validate_typewriter_champion.cmd\n"
                    f"  state: {local_state}",
                    flush=True,
                )
                last_scores[slot] = sc
                improved_slots.append((slot, host_id, sc))

        if args.launch_harness and not launched and improved_slots:
            # Prefer mainhall if present among improved, else first improved.
            pick = None
            for slot, _hid, _sc in improved_slots:
                if slot == "mainhall_typewriter":
                    pick = slot
                    break
            if pick is None:
                pick = improved_slots[0][0]
            local_state = PROJECT_ROOT / CHAMPS_REL / pick / "champion.State"
            if local_state.is_file():
                launch_harness(local_state)
                launched = True

        if args.once:
            return 0
        time.sleep(max(5.0, float(args.interval_s)))


if __name__ == "__main__":
    sys.exit(main())
