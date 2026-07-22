"""Auto-capture PB bundles: BizHawk .State + episode sidecar + manifest row."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from re1_rl.pb_sidecar import SIDECAR_SCHEMA_VERSION, dump_episode_sidecar, utc_now_iso

MANIFEST_FILENAME = "manifest.jsonl"
_CAPTURE_ENV_VAR = "RE1_PB_CAPTURE"


def pb_capture_enabled() -> bool:
    """True when ``RE1_PB_CAPTURE=1`` (default off for fleet safety)."""
    return os.environ.get(_CAPTURE_ENV_VAR, "").strip() in {"1", "true", "TRUE", "yes", "YES"}


def pb_root_dir(project_root: Path | str) -> Path:
    """Local PB root (``RE1_PB_ROOT`` or ``<project>/states/pb``)."""
    from re1_rl.pb_champion import pb_root

    return pb_root(project_root)


def inventory_fingerprint(state: dict[str, Any]) -> str:
    inv = state.get("inventory") or []
    return ",".join(sorted(str(x) for x in inv))


def _timestamp_slug(captured_at_iso: str | None = None) -> str:
    if captured_at_iso:
        try:
            dt = datetime.fromisoformat(captured_at_iso.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_trigger_slug(trigger_id: str) -> str:
    return (
        str(trigger_id)
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("@", "_at_")
    )


def append_manifest_row(manifest_path: Path, row: dict[str, Any]) -> None:
    """Append one JSONL manifest row (creates parent dirs)."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def resolve_pb_bundle(options: dict[str, Any] | None) -> dict[str, str] | None:
    """Parse reset ``options`` for a PB bundle to restore."""
    if not options:
        return None
    bundle = options.get("pb_bundle")
    if isinstance(bundle, dict):
        state_path = bundle.get("state_path") or bundle.get("state")
        sidecar_path = bundle.get("sidecar_path") or bundle.get("sidecar")
        if state_path and sidecar_path:
            return {
                "state_path": str(state_path),
                "sidecar_path": str(sidecar_path),
            }
    state_path = options.get("pb_state_path")
    sidecar_path = options.get("pb_sidecar_path")
    if state_path and sidecar_path:
        return {
            "state_path": str(state_path),
            "sidecar_path": str(sidecar_path),
        }
    return None


def load_sidecar_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"sidecar must be a JSON object: {path}")
    return data


def maybe_capture_pb(
    env: Any,
    *,
    trigger_id: str,
    states_dir: Path | str,
    captured: set[str] | None = None,
) -> Path | None:
    """Capture once per (episode, *trigger_id*): .State + .sidecar.json + manifest.

    Returns the savestate path on success, else None (disabled, duplicate, or error).
    """
    if not pb_capture_enabled():
        return None

    trigger_id = str(trigger_id)
    episode_captured = captured
    if episode_captured is None:
        episode_captured = getattr(env, "_pb_captured_triggers", None)
    if episode_captured is None:
        episode_captured = set()
        env._pb_captured_triggers = episode_captured

    if trigger_id in episode_captured:
        return None

    states_dir = Path(states_dir)
    states_dir.mkdir(parents=True, exist_ok=True)

    bridge = getattr(env, "bridge", None)
    if bridge is None or not hasattr(bridge, "save_savestate"):
        return None

    state = {}
    try:
        read_state = getattr(env, "_read_state", None)
        if callable(read_state):
            state = read_state(track_items=False)
    except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
        state = {}

    from re1_rl.pb_champion import CHAMPION_MILESTONE, champion_dir, try_replace_champion
    from re1_rl.pb_sync import ensure_pb_sync_daemon, push_champion_async

    project_root = Path(getattr(env, "project_root", states_dir.parent.parent))
    captured_at = utc_now_iso()
    slug = _timestamp_slug(captured_at)

    # Typewriter v1: stage under a temp dir, promote into the single champion
    # slot, then delete staging — never leave parallel sidecars on disk.
    if trigger_id == CHAMPION_MILESTONE:
        stage_dir = champion_dir(project_root) / ".staging"
        stage_dir.mkdir(parents=True, exist_ok=True)
        state_path = stage_dir / f"candidate_{slug}.State"
        sidecar_path = stage_dir / f"candidate_{slug}.sidecar.json"
    else:
        base = f"{_safe_trigger_slug(trigger_id)}_{slug}"
        state_path = states_dir / f"{base}.State"
        sidecar_path = states_dir / f"{base}.sidecar.json"

    try:
        bridge.save_savestate(str(state_path))
    except (OSError, RuntimeError, ValueError) as exc:
        if state_path.is_file():
            state_path.unlink(missing_ok=True)
        raise RuntimeError(f"save_savestate failed for {trigger_id}: {exc}") from exc

    sidecar = dump_episode_sidecar(
        env,
        captured_room_id=str(state.get("room_id", "") or ""),
        captured_at_iso=captured_at,
    )
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True), encoding="utf-8")

    episode_captured.add(trigger_id)

    if trigger_id == CHAMPION_MILESTONE:
        replaced = try_replace_champion(
            project_root,
            state_path=state_path,
            sidecar_path=sidecar_path,
            state=state,
        )
        for staging in (state_path, sidecar_path):
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
        if not replaced:
            return None
        cdir = champion_dir(project_root)
        champ_state = cdir / "champion.State"
        champ_side = cdir / "champion.sidecar.json"
        try:
            rel_state = champ_state.relative_to(project_root).as_posix()
            rel_sidecar = champ_side.relative_to(project_root).as_posix()
        except ValueError:
            rel_state = champ_state.as_posix()
            rel_sidecar = champ_side.as_posix()
        append_manifest_row(
            states_dir / MANIFEST_FILENAME,
            {
                "trigger_id": trigger_id,
                "state_path": rel_state,
                "sidecar_path": rel_sidecar,
                "room_id": str(state.get("room_id", "") or ""),
                "inventory_fingerprint": inventory_fingerprint(state),
                "schema_version": SIDECAR_SCHEMA_VERSION,
                "captured_at_iso": captured_at,
                "episode_step": int(
                    state.get("step", getattr(env, "_step_count", 0)) or 0
                ),
                "champion": True,
            },
        )
        ensure_pb_sync_daemon(project_root)
        push_champion_async(project_root)
        return champ_state

    try:
        rel_state = state_path.relative_to(project_root).as_posix()
        rel_sidecar = sidecar_path.relative_to(project_root).as_posix()
    except ValueError:
        rel_state = state_path.as_posix()
        rel_sidecar = sidecar_path.as_posix()

    append_manifest_row(
        states_dir / MANIFEST_FILENAME,
        {
            "trigger_id": trigger_id,
            "state_path": rel_state,
            "sidecar_path": rel_sidecar,
            "room_id": str(state.get("room_id", "") or ""),
            "inventory_fingerprint": inventory_fingerprint(state),
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "captured_at_iso": captured_at,
            "episode_step": int(state.get("step", getattr(env, "_step_count", 0)) or 0),
        },
    )
    return state_path
