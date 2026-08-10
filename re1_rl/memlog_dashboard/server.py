"""Dependency-light HTTP service for local memlog inspection and control."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from re1_rl.env import ACTION_NAMES
from re1_rl.obs_explain import (
    action_presentation,
    explain_observation,
    filtered_reward_breakdown,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass(frozen=True)
class DashboardConfig:
    root: Path = ROOT
    bind: str = "127.0.0.1"
    port: int = 8787
    learner_url: str = "http://127.0.0.1:8765"
    stale_after_s: float = 5.0
    stop_timeout_s: float = 8.0
    launcher: Path | None = None
    memlog_subdirectory: str = "memlog"

    @property
    def memlog_dir(self) -> Path:
        sub = str(self.memlog_subdirectory or "memlog").strip() or "memlog"
        return self.root / "data" / sub

    @property
    def launcher_path(self) -> Path:
        return self.launcher or self.root / "fleet" / "local" / "run_memlog_agent.cmd"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Replace a JSON file atomically using a sibling temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as exc:
        return None, f"unreadable: {exc}"
    if not isinstance(value, dict):
        return None, "snapshot is not an object"
    return value, None


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return number
    if isinstance(value, str):
        text = value.strip()
        try:
            return _timestamp_seconds(float(text))
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def process_command(launcher: Path) -> tuple[list[str], int]:
    """Return an explicit launcher command and Windows process-group flags."""
    if os.name == "nt":
        return ["cmd.exe", "/d", "/c", str(launcher)], subprocess.CREATE_NEW_PROCESS_GROUP
    return [str(launcher)], 0


def owned_tree_kill_command(pid: int) -> list[str]:
    """Exact Windows child-tree termination command; never name-based."""
    return ["taskkill", "/PID", str(int(pid)), "/T", "/F"]


class OwnedMemlogProcess:
    """Tracks the one launcher child created by this dashboard instance."""

    def __init__(
        self,
        config: DashboardConfig,
        *,
        popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    ) -> None:
        self.config = config
        self._popen = popen
        self._lock = threading.Lock()
        self._process: subprocess.Popen[Any] | None = None
        self._run_id: str | None = None
        self._started_at: float | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            proc = self._process
            return {
                "owned": proc is not None,
                "pid": proc.pid if proc is not None else None,
                "running": proc is not None and proc.poll() is None,
                "exit_code": proc.poll() if proc is not None else None,
                "run_id": self._run_id,
                "started_at": self._started_at,
                "launcher": str(self.config.launcher_path),
            }

    def start(self, run_id: str | None = None) -> dict[str, Any]:
        launcher = self.config.launcher_path
        if not launcher.is_file():
            raise FileNotFoundError(f"memlog launcher not found: {launcher}")
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError(f"owned memlog process already running (pid {self._process.pid})")
            owned_run_id = str(run_id or uuid.uuid4().hex)
            command, creationflags = process_command(launcher)
            env = os.environ.copy()
            env["RE1_MEMLOG_RUN_ID"] = owned_run_id
            kwargs: dict[str, Any] = {
                "cwd": str(self.config.root),
                "env": env,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "creationflags": creationflags,
            }
            if os.name != "nt":
                kwargs["start_new_session"] = True
            self._process = self._popen(command, **kwargs)
            self._run_id = owned_run_id
            self._started_at = time.time()
            return self.status_unlocked()

    def status_unlocked(self) -> dict[str, Any]:
        proc = self._process
        return {
            "owned": proc is not None,
            "pid": proc.pid if proc is not None else None,
            "running": proc is not None and proc.poll() is None,
            "exit_code": proc.poll() if proc is not None else None,
            "run_id": self._run_id,
            "started_at": self._started_at,
            "launcher": str(self.config.launcher_path),
        }

    def observe_run(self, run_id: Any, heartbeat: Any) -> None:
        """Associate a fresh producer run with the child we just launched."""
        stamp = _timestamp_seconds(heartbeat)
        if not run_id or stamp is None:
            return
        with self._lock:
            if (
                self._process is not None
                and self._process.poll() is None
                and self._started_at is not None
                and stamp >= self._started_at - 1.0
            ):
                self._run_id = str(run_id)

    def stop(self, request_shutdown: Callable[[str], None]) -> dict[str, Any]:
        with self._lock:
            proc = self._process
            run_id = self._run_id
        if proc is None:
            raise RuntimeError("no dashboard-owned memlog process")
        if proc.poll() is not None:
            return self.status()
        if run_id:
            request_shutdown(run_id)
        try:
            proc.wait(timeout=self.config.stop_timeout_s)
        except subprocess.TimeoutExpired:
            self._terminate_owned_tree(proc)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        return self.status()

    @staticmethod
    def _terminate_owned_tree(proc: subprocess.Popen[Any]) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                owned_tree_kill_command(proc.pid),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()


class DashboardService:
    """Filesystem/API facade, separated from HTTP for focused tests."""

    def __init__(
        self,
        config: DashboardConfig,
        *,
        clock: Callable[[], float] = time.time,
        process: OwnedMemlogProcess | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.process = process or OwnedMemlogProcess(config)

    @property
    def latest_path(self) -> Path:
        return self.config.memlog_dir / "latest.json"

    @property
    def events_path(self) -> Path:
        preferred = self.config.memlog_dir / "reward_events.jsonl"
        legacy = self.config.memlog_dir / "events.jsonl"
        return preferred if preferred.exists() or not legacy.exists() else legacy

    @property
    def control_path(self) -> Path:
        return self.config.memlog_dir / "control.json"

    def latest(self) -> dict[str, Any]:
        snapshot, error = read_json(self.latest_path)
        if snapshot is None:
            return {
                "available": False,
                "error": error,
                "stale": True,
                "age_s": None,
                "snapshot": None,
            }
        stamp = _timestamp_seconds(
            snapshot.get(
                "time",
                snapshot.get(
                    "heartbeat_unix_s",
                    snapshot.get("timestamp", snapshot.get("updated_at")),
                ),
            )
        )
        if stamp is None:
            try:
                stamp = self.latest_path.stat().st_mtime
            except OSError:
                stamp = None
        age = max(0.0, self.clock() - stamp) if stamp is not None else None
        pre_step = snapshot.get("pre_step") if isinstance(snapshot.get("pre_step"), dict) else {}
        obs = snapshot.get(
            "obs",
            snapshot.get(
                "observation",
                snapshot.get(
                    "pre_step_obs",
                    snapshot.get("raw_obs", pre_step.get("observation", {})),
                ),
            ),
        )
        self.process.observe_run(snapshot.get("run_id"), stamp)
        enriched = dict(snapshot)
        enriched["observation_explained"] = explain_observation(obs)
        enriched["action_presentation"] = action_presentation(snapshot, ACTION_NAMES)
        reward_breakdown = snapshot.get(
            "reward_breakdown",
            snapshot.get("post_step", {}).get("reward_breakdown", {})
            if isinstance(snapshot.get("post_step"), dict) else {},
        )
        enriched["reward_events_filtered"] = filtered_reward_breakdown(reward_breakdown)
        return {
            "available": True,
            "error": None,
            "stale": age is None or age > self.config.stale_after_s,
            "age_s": age,
            "snapshot": enriched,
        }

    def events(self, limit: int = 200) -> dict[str, Any]:
        limit = max(1, min(int(limit), 2000))
        try:
            with self.events_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 2_000_000))
                raw = handle.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return {"events": [], "error": "missing", "cumulative_reward": 0.0}
        except OSError as exc:
            return {"events": [], "error": str(exc), "cumulative_reward": 0.0}
        lines = raw.splitlines()
        if size > 2_000_000 and lines:
            lines = lines[1:]
        out: list[dict[str, Any]] = []
        cumulative = 0.0
        latest, _ = read_json(self.latest_path)
        current_run = str(latest.get("run_id", "")) if latest else ""
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (
                not isinstance(event, dict)
                or (current_run and event.get("run_id") not in (None, "", current_run))
                or not self._event_visible(event)
            ):
                continue
            reward = self._event_reward(event)
            cumulative += reward
            normalized = dict(event)
            normalized["display_reward"] = reward
            out.append(normalized)
        return {
            "events": out[-limit:],
            "error": None,
            "cumulative_reward": cumulative,
        }

    @staticmethod
    def _event_reward(event: dict[str, Any]) -> float:
        breakdown = filtered_reward_breakdown(event.get("reward_breakdown"))
        if breakdown:
            return sum(breakdown.values())
        for key in ("reward", "value", "amount"):
            try:
                return float(event[key])
            except (KeyError, TypeError, ValueError):
                continue
        return 0.0

    @classmethod
    def _event_visible(cls, event: dict[str, Any]) -> bool:
        name = str(event.get("name", event.get("event", event.get("type", "")))).lower()
        if name in {"step", "step_penalty", "softlock", "contempt"}:
            return False
        if isinstance(event.get("reward_breakdown"), dict):
            return bool(filtered_reward_breakdown(event["reward_breakdown"]))
        return cls._event_reward(event) != 0.0

    def write_control(self, run_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        latest, _ = read_json(self.latest_path)
        latest_run_id = str(latest.get("run_id", "")) if latest else ""
        if latest_run_id and latest_run_id != run_id:
            raise ValueError(
                f"run_id mismatch: latest is {latest_run_id!r}, request was {run_id!r}"
            )
        existing, _ = read_json(self.control_path)
        payload = dict(existing or {})
        if payload.get("run_id") not in (None, "", run_id):
            payload = {}
        payload["run_id"] = run_id
        for key, value in updates.items():
            if key not in {"paused", "speed_pct", "shutdown"}:
                continue
            payload[key] = value
        payload["updated_at"] = self.clock()
        atomic_write_json(self.control_path, payload)
        return payload

    def control(self, operation: str, body: dict[str, Any]) -> dict[str, Any]:
        run_id = body.get("run_id")
        if operation == "pause":
            return self.write_control(str(run_id or ""), {"paused": True})
        if operation == "resume":
            return self.write_control(str(run_id or ""), {"paused": False})
        if operation == "speed":
            try:
                speed = int(body.get("speed_pct"))
            except (TypeError, ValueError):
                raise ValueError("speed_pct must be an integer") from None
            if not 1 <= speed <= 10_000:
                raise ValueError("speed_pct must be between 1 and 10000")
            return self.write_control(str(run_id or ""), {"speed_pct": speed})
        raise ValueError(f"unknown control operation: {operation}")

    def yawn_local_status(self) -> dict[str, Any]:
        from re1_rl.yawn_rails_worker_cache import load_local_yawn_manifest

        manifest = load_local_yawn_manifest(self.config.root)
        cells = sorted(
            [
                row
                for row in (manifest.get("cells") or [])
                if isinstance(row, dict) and "checkpoint_index" in row
            ],
            key=lambda row: int(row["checkpoint_index"]),
        )
        frontier = cells[-1] if cells else None
        quality = list((frontier or {}).get("quality") or [])
        return {
            "archive_version": int(manifest.get("archive_version", 0) or 0),
            "cell_count": len(cells),
            "route_id": manifest.get("route_id"),
            "frontier": (
                {
                    "checkpoint_index": int(frontier["checkpoint_index"]),
                    "checkpoint_id": str(frontier.get("checkpoint_id") or ""),
                    "room_id": str(frontier.get("room_id") or ""),
                    "hp": quality[0] if quality else None,
                    "ammo": quality[1] if len(quality) > 1 else None,
                }
                if frontier
                else None
            ),
        }

    def yawn_sync_pull(self, *, full: bool = False) -> dict[str, Any]:
        """Pull learner Yawn cells into local states/yawn_rails (hot, no worker restart)."""
        from re1_rl.distributed.worker_client import WorkerClient
        from re1_rl.yawn_rails_worker_cache import (
            load_local_yawn_manifest,
            poll_yawn_rails_manifest,
        )

        parsed = urlparse(self.config.learner_url)
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or 8765)
        client = WorkerClient(
            host,
            port,
            machine_name="memlog_dashboard",
            timeout=30.0,
        )
        if not client.health():
            raise RuntimeError(f"learner unreachable: {self.config.learner_url}")
        local_before = load_local_yawn_manifest(self.config.root)
        since = 0 if full else int(local_before.get("archive_version", 0) or 0)
        local_after = poll_yawn_rails_manifest(
            client,
            self.config.root,
            since_version=since,
        )
        stats = dict(local_after.get("cache_stats") or {})
        return {
            "archive_version": int(local_after.get("archive_version", 0) or 0),
            "cell_count": len(local_after.get("cells") or []),
            "fetched": int(stats.get("fetched_last_poll", 0) or 0),
            "pruned": int(stats.get("pruned_dirs_last_poll", 0) or 0),
            "full": bool(full),
            "frontier": self.yawn_local_status().get("frontier"),
        }

    def learner_status(self) -> dict[str, Any]:
        url = self.config.learner_url.rstrip("/") + "/status"
        try:
            with urlopen(url, timeout=0.7) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return {"available": True, "url": url, "data": payload, "error": None}
        except (HTTPError, URLError, OSError, ValueError) as exc:
            return {"available": False, "url": url, "data": None, "error": str(exc)}

    def status(self) -> dict[str, Any]:
        latest = self.latest()
        snapshot = latest.get("snapshot") or {}
        control, control_error = read_json(self.control_path)
        return {
            "latest": {
                "available": latest["available"],
                "stale": latest["stale"],
                "age_s": latest["age_s"],
                "error": latest["error"],
                "run_id": snapshot.get("run_id"),
                "seq": snapshot.get("seq"),
                "time": snapshot.get("time"),
                "speed": snapshot.get(
                    "speed",
                    snapshot.get("control", {}).get("speed_pct")
                    if isinstance(snapshot.get("control"), dict) else None,
                ),
                "policy_version": snapshot.get(
                    "policy_version",
                    snapshot.get("pre_step", {}).get("policy_version")
                    if isinstance(snapshot.get("pre_step"), dict) else None,
                ),
                "horizon": snapshot.get(
                    "horizon",
                    snapshot.get("horizon_step", snapshot.get("n_steps")),
                ),
            },
            "process": self.process.status(),
            "control": control,
            "control_error": control_error,
            "learner": self.learner_status(),
            "yawn_cells": self.yawn_local_status(),
        }

    def start(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.process.start(body.get("run_id"))

    def stop(self) -> dict[str, Any]:
        return self.process.stop(
            lambda run_id: self.write_control(run_id, {"shutdown": True})
        )


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "RE1MemlogDashboard/1"

    @property
    def service(self) -> DashboardService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[dashboard] {self.address_string()} {fmt % args}")

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid Content-Length") from None
        if length > 64 * 1024:
            raise ValueError("request body too large")
        if not length:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        path, _, query = self.path.partition("?")
        if path == "/api/status":
            self._json(200, self.service.status())
            return
        if path == "/api/latest":
            self._json(200, self.service.latest())
            return
        if path == "/api/events":
            limit = 200
            for part in query.split("&"):
                if part.startswith("limit="):
                    try:
                        limit = int(part[6:])
                    except ValueError:
                        pass
            self._json(200, self.service.events(limit))
            return
        asset = "index.html" if path in {"", "/"} else path.lstrip("/")
        if asset not in {"index.html", "app.js", "style.css"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file_path = STATIC_DIR / asset
        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }[file_path.suffix]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            if self.path.startswith("/api/control/"):
                operation = self.path.rsplit("/", 1)[-1]
                payload = self.service.control(operation, body)
            elif self.path == "/api/lifecycle/start":
                payload = self.service.start(body)
            elif self.path == "/api/lifecycle/stop":
                payload = self.service.stop()
            elif self.path == "/api/yawn-sync/pull":
                payload = self.service.yawn_sync_pull(
                    full=bool(body.get("full")),
                )
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._json(200, {"ok": True, "data": payload})
        except FileNotFoundError as exc:
            self._json(404, {"ok": False, "error": str(exc)})
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            self._json(409 if isinstance(exc, RuntimeError) else 400, {
                "ok": False, "error": str(exc)
            })


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: DashboardService) -> None:
        self.service = service
        super().__init__(address, DashboardHandler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--learner-url",
        default=os.environ.get("RE1_LEARNER_URL", "http://127.0.0.1:8765"),
    )
    parser.add_argument("--stale-after", type=float, default=5.0)
    parser.add_argument("--launcher", type=Path)
    parser.add_argument(
        "--memlog-dir",
        default=os.environ.get("RE1_MEMLOG_DIRECTORY", "memlog").strip() or "memlog",
        help="subdirectory under data/ (default: RE1_MEMLOG_DIRECTORY or memlog)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = DashboardConfig(
        root=args.root.resolve(),
        bind=args.bind,
        port=args.port,
        learner_url=args.learner_url,
        stale_after_s=args.stale_after,
        launcher=args.launcher.resolve() if args.launcher else None,
        memlog_subdirectory=str(args.memlog_dir),
    )
    server = DashboardHTTPServer((config.bind, config.port), DashboardService(config))
    print(f"RE1 memlog dashboard: http://{config.bind}:{config.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
