"""Watch fleet hosts for the single typewriter champion; sync best; launch harness.

Polls pking / WH1 / WH2 champion.json via local FS + SSH. Keeps only one logical
champion (best score) and copies it onto every host. When a champion first
appears (or improves), prints the validate command and optionally launches
``scripts/validate_typewriter_champion.cmd`` on this machine (pking).
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
CHAMP_REL = Path("states/pb/champions/mainhall_typewriter")
CHAMP_FILES = ("champion.State", "champion.sidecar.json", "champion.json")

HOSTS = {
    "pking": {"kind": "local", "root": PROJECT_ROOT},
    "workhorse1": {"kind": "ssh", "ssh": "sshuser@192.168.0.203", "root": "D:/re1_rl"},
    "workhorse2": {"kind": "ssh", "ssh": "sshuser@192.168.0.116", "root": "C:/Users/sshuser/re1_rl"},
}


def _score(rec: dict | None) -> tuple[int, ...] | None:
    if not rec or "score" not in rec:
        return None
    try:
        return tuple(int(x) for x in rec["score"])
    except (TypeError, ValueError):
        return None


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


def fetch_record(host_id: str) -> dict | None:
    cfg = HOSTS[host_id]
    if cfg["kind"] == "local":
        return _read_local_json(Path(cfg["root"]) / CHAMP_REL / "champion.json")
    remote = f'{cfg["root"]}/{CHAMP_REL.as_posix()}/champion.json'
    text = _ssh_cat(cfg["ssh"], remote)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def pull_champion_tree(host_id: str, dest_dir: Path) -> bool:
    cfg = HOSTS[host_id]
    dest_dir.mkdir(parents=True, exist_ok=True)
    if cfg["kind"] == "local":
        src = Path(cfg["root"]) / CHAMP_REL
        ok = True
        for name in CHAMP_FILES:
            s = src / name
            if not s.is_file():
                ok = False
                continue
            shutil.copy2(s, dest_dir / name)
        return ok and (dest_dir / "champion.State").is_file()

    remote_dir = f'{cfg["root"]}/{CHAMP_REL.as_posix()}'
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
    return (dest_dir / "champion.State").is_file()


def push_champion_tree(host_id: str, src_dir: Path) -> bool:
    cfg = HOSTS[host_id]
    if cfg["kind"] == "local":
        dest = Path(cfg["root"]) / CHAMP_REL
        dest.mkdir(parents=True, exist_ok=True)
        for name in CHAMP_FILES:
            s = src_dir / name
            if s.is_file():
                shutil.copy2(s, dest / name)
        return True

    remote_dir = f'{cfg["root"]}/{CHAMP_REL.as_posix()}'
    # Ensure remote dir exists.
    subprocess.run(
        [
            "ssh",
            "-o",
            "ConnectTimeout=12",
            "-o",
            "BatchMode=yes",
            cfg["ssh"],
            f'mkdir "{remote_dir.replace("/", chr(92))}" 2>nul',
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
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
    return True


def best_host(records: dict[str, dict | None]) -> tuple[str, dict] | None:
    best: tuple[str, dict, tuple[int, ...]] | None = None
    for host_id, rec in records.items():
        sc = _score(rec)
        if sc is None or rec is None:
            continue
        if best is None or sc > best[2]:
            best = (host_id, rec, sc)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval-s", type=float, default=20.0)
    ap.add_argument("--once", action="store_true", help="one poll cycle then exit")
    ap.add_argument(
        "--launch-harness",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="open play_human validate console when champion first appears (default on)",
    )
    args = ap.parse_args()

    last_score: tuple[int, ...] | None = None
    launched = False

    while True:
        records = {hid: fetch_record(hid) for hid in HOSTS}
        summary = {
            hid: (_score(rec), (rec or {}).get("hp"), (rec or {}).get("room_id"))
            for hid, rec in records.items()
        }
        print(f"[watch] {time.strftime('%H:%M:%S')} scores={summary}", flush=True)

        winner = best_host(records)
        if winner is not None:
            host_id, rec = winner
            sc = _score(rec)
            assert sc is not None
            if last_score is None or sc > last_score:
                with tempfile.TemporaryDirectory(prefix="re1_pb_champ_") as tmp:
                    tmp_path = Path(tmp)
                    if not pull_champion_tree(host_id, tmp_path):
                        print(f"[watch] pull failed from {host_id}", flush=True)
                    else:
                        # Normalize paths in champion.json for local layout.
                        data = json.loads(
                            (tmp_path / "champion.json").read_text(encoding="utf-8")
                        )
                        data["state_path"] = (
                            "states/pb/champions/mainhall_typewriter/champion.State"
                        )
                        data["sidecar_path"] = (
                            "states/pb/champions/mainhall_typewriter/champion.sidecar.json"
                        )
                        (tmp_path / "champion.json").write_text(
                            json.dumps(data, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                        for hid in HOSTS:
                            ok = push_champion_tree(hid, tmp_path)
                            print(f"[watch] push -> {hid}: {'ok' if ok else 'FAIL'}", flush=True)
                        local_state = PROJECT_ROOT / CHAMP_REL / "champion.State"
                        print(
                            f"[watch] CHAMPION score={list(sc)} from={host_id}\n"
                            f"  validate: scripts\\validate_typewriter_champion.cmd\n"
                            f"  state: {local_state}",
                            flush=True,
                        )
                        if args.launch_harness and not launched and local_state.is_file():
                            launch_harness(local_state)
                            launched = True
                        last_score = sc

        if args.once:
            return 0
        time.sleep(max(5.0, float(args.interval_s)))


if __name__ == "__main__":
    sys.exit(main())
