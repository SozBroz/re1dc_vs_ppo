"""Auto-capture PB bundles: BizHawk .State + episode sidecar + manifest row."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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


def _is_typewriter_milestone_fallback(trigger_id: str) -> bool:
    return str(trigger_id).startswith("typewriter_save:")


def _parse_typewriter_room_fallback(trigger_id: str) -> str | None:
    s = str(trigger_id)
    prefix = "typewriter_save:"
    if not s.startswith(prefix):
        return None
    room = s[len(prefix) :].strip().upper()
    return room or None


def _typewriter_champion_dir_fallback(project_root: Path | str, room_id: str) -> Path:
    from re1_rl.pb_champion import pb_root

    room = str(room_id).strip().upper()
    if room == "106":
        sub = "champions/mainhall_typewriter"
    else:
        sub = f"champions/typewriter_{room}"
    return pb_root(project_root) / sub


def _champ_fn(name: str, fallback: Callable[..., Any]) -> Callable[..., Any]:
    from re1_rl import pb_champion as champ

    fn = getattr(champ, name, None)
    return fn if callable(fn) else fallback


def _inventory_slots_from_state(state: dict[str, Any]) -> list[tuple[str, int]]:
    from re1_rl.item_todo import canonical_item

    raw = state.get("inventory_slots")
    if raw is None:
        return [
            (canonical_item(str(n)), 1) for n in (state.get("inventory") or []) if n
        ]
    out: list[tuple[str, int]] = []
    for entry in raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            out.append((canonical_item(str(entry[0])), int(entry[1])))
        elif isinstance(entry, dict):
            out.append(
                (
                    canonical_item(str(entry.get("name") or entry.get("item") or "")),
                    int(entry.get("qty", 1) or 0),
                )
            )
    return out


def _box_cache_for_capture(env: Any) -> list[tuple[int, int]] | None:
    """Prefer a live ``read_box`` when the bridge can read RAM; else ``_box_cache``."""
    bridge = getattr(env, "bridge", None)
    if bridge is not None and hasattr(bridge, "read_block"):
        try:
            from re1_rl.item_box import read_box

            box = read_box(bridge)
            try:
                env._box_cache = box
            except (AttributeError, TypeError):
                pass
            return box
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass
    return getattr(env, "_box_cache", None)


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _try_replace_champion_call(
    project_root: Path,
    *,
    state_path: Path,
    sidecar_path: Path,
    state: dict[str, Any],
    score: tuple[int, ...],
    room_id: str,
) -> bool:
    import inspect

    from re1_rl.pb_champion import try_replace_champion

    params = inspect.signature(try_replace_champion).parameters
    kwargs: dict[str, Any] = {
        "state_path": state_path,
        "sidecar_path": sidecar_path,
        "state": state,
    }
    if "score" in params:
        kwargs["score"] = score
    if "room_id" in params:
        kwargs["room_id"] = room_id
    elif "room" in params:
        kwargs["room"] = room_id
    return bool(try_replace_champion(project_root, **kwargs))


def _score_for_typewriter_capture(
    env: Any,
    state: dict[str, Any],
    box_cache: list[tuple[int, int]] | None,
) -> tuple[int, ...]:
    from re1_rl import pb_champion as champ

    slots = _inventory_slots_from_state(state)
    items = getattr(env, "_items", None)
    progress = getattr(env, "_progress", None)
    ever_held = set(getattr(items, "ever_held", None) or ())
    visited = set(getattr(progress, "visited_rooms", None) or ())
    hp = int(state.get("hp", 0) or 0)

    score_v2 = getattr(champ, "champion_score_v2", None)
    if callable(score_v2):
        return tuple(
            score_v2(
                inventory_slots=slots,
                box_cache=box_cache,
                ever_held=ever_held,
                visited_rooms=visited,
                hp=hp,
            )
        )
    # Pre-v2 champion module: fall back to inventory-only score.
    return tuple(champ.champion_score(state))


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

    state: dict[str, Any] = {}
    try:
        read_state = getattr(env, "_read_state", None)
        if callable(read_state):
            state = read_state(track_items=False)
    except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
        state = {}

    from re1_rl.pb_sync import ensure_pb_sync_daemon, push_champion_async

    is_typewriter_milestone = _champ_fn(
        "is_typewriter_milestone", _is_typewriter_milestone_fallback
    )
    parse_typewriter_room = _champ_fn(
        "parse_typewriter_room", _parse_typewriter_room_fallback
    )
    typewriter_champion_dir = _champ_fn(
        "typewriter_champion_dir", _typewriter_champion_dir_fallback
    )

    project_root = Path(getattr(env, "project_root", states_dir.parent.parent))
    captured_at = utc_now_iso()
    slug = _timestamp_slug(captured_at)
    typewriter = bool(is_typewriter_milestone(trigger_id))
    room_id = ""
    if typewriter:
        room_id = str(
            parse_typewriter_room(trigger_id)
            or state.get("room_id", "")
            or ""
        ).strip().upper()
        if not room_id:
            return None
        stage_dir = typewriter_champion_dir(project_root, room_id) / ".staging"
        stage_dir.mkdir(parents=True, exist_ok=True)
        state_path = stage_dir / f"candidate_{slug}.State"
        sidecar_path = stage_dir / f"candidate_{slug}.sidecar.json"
    else:
        # Non-typewriter milestones: timestamped sidecars (when v1-only flag is off).
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

    if typewriter:
        box_cache = _box_cache_for_capture(env)
        score = _score_for_typewriter_capture(env, state, box_cache)
        replaced = _try_replace_champion_call(
            project_root,
            state_path=state_path,
            sidecar_path=sidecar_path,
            state=state,
            score=score,
            room_id=room_id,
        )
        _unlink_quiet(state_path)
        _unlink_quiet(sidecar_path)
        stage_dir = state_path.parent
        try:
            stage_dir.rmdir()
        except OSError:
            pass
        if not replaced:
            return None
        cdir = typewriter_champion_dir(project_root, room_id)
        champ_state = cdir / "champion.State"
        champ_side = cdir / "champion.sidecar.json"
        try:
            rel_state = champ_state.relative_to(project_root).as_posix()
            rel_sidecar = champ_side.relative_to(project_root).as_posix()
        except ValueError:
            rel_state = champ_state.as_posix()
            rel_sidecar = champ_side.as_posix()
        row: dict[str, Any] = {
            "trigger_id": trigger_id,
            "state_path": rel_state,
            "sidecar_path": rel_sidecar,
            "room_id": str(state.get("room_id", "") or room_id),
            "inventory_fingerprint": inventory_fingerprint(state),
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "captured_at_iso": captured_at,
            "episode_step": int(
                state.get("step", getattr(env, "_step_count", 0)) or 0
            ),
            "champion": True,
            "score": list(score),
        }
        from re1_rl import pb_champion as champ

        score_version = getattr(champ, "SCORE_VERSION", None)
        if score_version is not None:
            row["score_version"] = int(score_version)
        append_manifest_row(states_dir / MANIFEST_FILENAME, row)
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
