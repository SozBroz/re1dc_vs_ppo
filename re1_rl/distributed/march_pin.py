"""Fleet-wide master pin: learner owns ``planner_loyal_reset_pin.env``.

Workers pull the pin on each march tick and publish on advance / manual pin
moves. Local files are caches only — WH3 ``:8765`` is the source of truth so
pking march rewrites cannot leave workhorses stranded on a stale INDEX.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_PIN_REL = Path("data/planner_loyal_reset_pin.env")
_META_REL = Path("data/planner_march_pin_master.json")
_INDEX_KEY = "RE1_PLANNER_RESET_PIN_INDEX"
_INDEX_RE = re.compile(
    rf"^(\s*{re.escape(_INDEX_KEY)}\s*=\s*)(\d+)(\s*)$", re.MULTILINE
)
_TIMEOUT_S = 5.0
_SYNC_MIN_S = 2.0
_LAST_SYNC_MONO: dict[str, float] = {}
_LAST_SYNC_VERSION: dict[str, int] = {}


def _project_root(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = (os.environ.get("RE1_RL_ROOT") or "").strip()
    return Path(env) if env else Path.cwd()


def pin_path(project_root: Path | str | None = None) -> Path:
    return _project_root(project_root) / _PIN_REL


def meta_path(project_root: Path | str | None = None) -> Path:
    return _project_root(project_root) / _META_REL


def parse_pin_index(text: str) -> int | None:
    m = _INDEX_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(2))
    except ValueError:
        return None


def rewrite_index_text(text: str, new_idx: int) -> str:
    """Return pin-file text with INDEX rewritten (inserts key if missing)."""
    body = text if text.endswith("\n") or text == "" else text + "\n"
    if _INDEX_RE.search(body):
        return _INDEX_RE.sub(rf"\g<1>{int(new_idx)}\g<3>", body, count=1)
    return body + f"{_INDEX_KEY}={int(new_idx)}\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_meta(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 0, "updated_unix": 0.0, "index": None}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 0, "updated_unix": 0.0, "index": None}
    return doc if isinstance(doc, dict) else {"version": 0, "updated_unix": 0.0, "index": None}


def _write_meta(path: Path, meta: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(meta, indent=1, sort_keys=True) + "\n")


class MarchPinStore:
    """Learner-side authoritative pin file + monotonic version."""

    def __init__(self, project_root: Path | str | None = None) -> None:
        self.root = _project_root(project_root)
        self.path = pin_path(self.root)
        self.meta_path = meta_path(self.root)
        self.lock = threading.Lock()
        self._ensure_bootstrapped()

    def _ensure_bootstrapped(self) -> None:
        with self.lock:
            if not self.path.is_file():
                _atomic_write(
                    self.path,
                    (
                        "# Auto-created master pin (learner).\n"
                        f"{_INDEX_KEY}=0\n"
                        "RE1_PLANNER_MARCH=1\n"
                    ),
                )
            meta = _read_meta(self.meta_path)
            try:
                version = int(meta.get("version") or 0)
            except (TypeError, ValueError):
                version = 0
            if version <= 0:
                text = self.path.read_text(encoding="utf-8")
                _write_meta(
                    self.meta_path,
                    {
                        "version": 1,
                        "updated_unix": time.time(),
                        "index": parse_pin_index(text),
                    },
                )

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            text = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
            meta = _read_meta(self.meta_path)
            try:
                version = int(meta.get("version") or 0)
            except (TypeError, ValueError):
                version = 0
            return {
                "ok": True,
                "version": version,
                "index": parse_pin_index(text),
                "text": text,
                "updated_unix": float(meta.get("updated_unix") or 0.0),
            }

    def publish(
        self,
        *,
        text: str | None = None,
        index: int | None = None,
        expected_version: int | None = None,
        expected_index: int | None = None,
        force: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """CAS publish. Returns ``(ok, snapshot_or_error)``."""
        with self.lock:
            cur_text = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
            meta = _read_meta(self.meta_path)
            try:
                cur_version = int(meta.get("version") or 0)
            except (TypeError, ValueError):
                cur_version = 0
            cur_index = parse_pin_index(cur_text)

            if not force and expected_version is not None and int(expected_version) != cur_version:
                return False, {
                    "ok": False,
                    "error": "version_mismatch",
                    "version": cur_version,
                    "index": cur_index,
                }
            if (
                not force
                and expected_index is not None
                and cur_index is not None
                and int(expected_index) != int(cur_index)
            ):
                return False, {
                    "ok": False,
                    "error": "index_mismatch",
                    "version": cur_version,
                    "index": cur_index,
                }

            if text is not None:
                new_text = text
            elif index is not None:
                new_text = rewrite_index_text(cur_text, int(index))
            else:
                return False, {"ok": False, "error": "text_or_index_required"}

            new_index = parse_pin_index(new_text)
            _atomic_write(self.path, new_text if new_text.endswith("\n") else new_text + "\n")
            new_version = cur_version + 1
            now = time.time()
            _write_meta(
                self.meta_path,
                {
                    "version": new_version,
                    "updated_unix": now,
                    "index": new_index,
                },
            )
            return True, {
                "ok": True,
                "version": new_version,
                "index": new_index,
                "text": new_text if new_text.endswith("\n") else new_text + "\n",
                "updated_unix": now,
            }


def _learner_addr() -> tuple[str, int] | None:
    host = (
        os.environ.get("RE1_LEARNER_HOST")
        or os.environ.get("FLEET_LEARNER_HOST")
        or ""
    ).strip()
    port_raw = (
        os.environ.get("RE1_LEARNER_PORT")
        or os.environ.get("FLEET_LEARNER_PORT")
        or "8765"
    ).strip()
    if not host:
        return None
    try:
        return host, int(port_raw)
    except ValueError:
        return None


def fetch_master_pin(*, timeout_s: float = _TIMEOUT_S) -> dict[str, Any] | None:
    addr = _learner_addr()
    if addr is None:
        return None
    host, port = addr
    try:
        with urllib.request.urlopen(
            f"http://{host}:{port}/march/pin", timeout=timeout_s
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.HTTPError):
        return None
    return payload if isinstance(payload, dict) and payload.get("ok") is True else None


def publish_master_pin(
    *,
    text: str | None = None,
    index: int | None = None,
    expected_version: int | None = None,
    expected_index: int | None = None,
    force: bool = False,
    timeout_s: float = _TIMEOUT_S,
) -> dict[str, Any] | None:
    addr = _learner_addr()
    if addr is None:
        # Unit tests / offline: treat as success so local march still works.
        return {"ok": True, "offline": True, "index": index, "version": None}
    host, port = addr
    body = {
        "text": text,
        "index": index,
        "expected_version": expected_version,
        "expected_index": expected_index,
        "force": bool(force),
    }
    req = urllib.request.Request(
        f"http://{host}:{port}/march/pin",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def sync_pin_from_learner(
    project_root: Path | str | None = None,
    *,
    force: bool = False,
) -> bool:
    """Mirror learner master pin into the local pin file. True if local changed."""
    root = _project_root(project_root)
    root_key = str(root)
    now = time.monotonic()
    if not force and now - _LAST_SYNC_MONO.get(root_key, 0.0) < _SYNC_MIN_S:
        return False
    snap = fetch_master_pin()
    if not snap:
        return False
    try:
        version = int(snap.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    text = snap.get("text")
    if not isinstance(text, str) or not text.strip():
        return False
    if not force and _LAST_SYNC_VERSION.get(root_key) == version:
        local = pin_path(root)
        if local.is_file() and local.read_text(encoding="utf-8") == text:
            _LAST_SYNC_MONO[root_key] = now
            return False
    local = pin_path(root)
    prev = local.read_text(encoding="utf-8") if local.is_file() else None
    if prev == text:
        _LAST_SYNC_MONO[root_key] = now
        _LAST_SYNC_VERSION[root_key] = version
        return False
    _atomic_write(local, text if text.endswith("\n") else text + "\n")
    _LAST_SYNC_MONO[root_key] = now
    _LAST_SYNC_VERSION[root_key] = version
    idx = snap.get("index")
    print(
        f"[march_pin] synced master pin INDEX={idx} version={version} -> {local}",
        flush=True,
    )
    return True


def publish_local_pin(
    project_root: Path | str | None = None,
    *,
    expected_version: int | None = None,
    expected_index: int | None = None,
    force: bool = False,
) -> bool:
    """Push local pin file bytes to the learner master."""
    root = _project_root(project_root)
    path = pin_path(root)
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    payload = publish_master_pin(
        text=text,
        expected_version=expected_version,
        expected_index=expected_index,
        force=force,
    )
    if not payload or payload.get("ok") is not True:
        print(f"[march_pin] publish refused: {payload!r}", flush=True)
        return False
    if payload.get("offline"):
        return True
    try:
        _LAST_SYNC_VERSION[str(root)] = int(payload.get("version") or 0)
    except (TypeError, ValueError):
        pass
    print(
        f"[march_pin] published INDEX={payload.get('index')} "
        f"version={payload.get('version')}",
        flush=True,
    )
    return True


def publish_index_advance(
    project_root: Path | str | None = None,
    *,
    new_idx: int,
    expected_index: int,
    expected_version: int | None = None,
) -> dict[str, Any] | None:
    """CAS advance INDEX on the learner, then mirror the returned text locally."""
    root = _project_root(project_root)
    payload = publish_master_pin(
        index=int(new_idx),
        expected_index=int(expected_index),
        expected_version=expected_version,
        force=False,
    )
    if not payload or payload.get("ok") is not True:
        sync_pin_from_learner(root, force=True)
        return payload
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        _atomic_write(
            pin_path(root),
            text if text.endswith("\n") else text + "\n",
        )
        try:
            _LAST_SYNC_VERSION[str(root)] = int(payload.get("version") or 0)
        except (TypeError, ValueError):
            pass
    else:
        path = pin_path(root)
        if path.is_file():
            _atomic_write(path, rewrite_index_text(path.read_text(encoding="utf-8"), new_idx))
    return payload
