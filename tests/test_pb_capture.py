"""Unit tests for PB milestone taxonomy and capture (no BizHawk)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.episode_history import EpisodeHistory
from re1_rl.item_todo import ItemTracker
from re1_rl.pb_capture import (
    MANIFEST_FILENAME,
    append_manifest_row,
    inventory_fingerprint,
    maybe_capture_pb,
    pb_capture_enabled,
    resolve_pb_bundle,
)
from re1_rl.pb_milestones import (
    KEY_ITEM_MILESTONES,
    ROOM_MILESTONES,
    STORY_USE_MILESTONES,
    detect_milestone_triggers,
    milestone_id_for_new_key,
    milestone_id_for_room,
    milestone_id_for_story_use,
)
from re1_rl.pb_sidecar import EpisodeSidecarParts, apply_episode_sidecar, dump_episode_sidecar
from re1_rl.progress import ProgressTracker


class _FakeBridge:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def save_savestate(self, path: str) -> None:
        self.saved.append(path)
        Path(path).write_bytes(b"FAKE_STATE")


class _FakeEnv:
    def __init__(self, tmp_path: Path) -> None:
        self.project_root = tmp_path
        self.bridge = _FakeBridge()
        self._step_count = 42
        self._progress = ProgressTracker()
        self._progress.seed_spawn_room("105")
        self._items = ItemTracker(todo=[])
        self._items.ever_held = {"emblem"}
        self._episode_history = EpisodeHistory()
        self._episode_history.reset("105", step=0)
        self._box_cache = None
        self._pb_captured_triggers: set[str] = set()

    def _read_state(self, *, track_items: bool = True) -> dict:
        return {
            "room_id": "105",
            "inventory": ["knife", "emblem"],
            "step": self._step_count,
        }


def test_milestone_id_helpers() -> None:
    assert milestone_id_for_new_key("wooden_emblem") == "key:emblem"
    assert milestone_id_for_new_key("piano_notes") == "key:music_notes"
    assert milestone_id_for_new_key("beretta") is None
    assert milestone_id_for_room("20e") == "room:20E"
    assert milestone_id_for_room("106") is None
    assert milestone_id_for_story_use("gold_emblem@105_fireplace") == (
        "story_use:gold_emblem@105_fireplace"
    )
    assert milestone_id_for_story_use("draft@room") is None
    assert "shield_key" in KEY_ITEM_MILESTONES
    assert "20E" in ROOM_MILESTONES
    assert "music_notes@10F_piano" in STORY_USE_MILESTONES


def test_detect_milestone_triggers_key_room_story(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_PB_V1_TYPEWRITER_ONLY", "0")
    prev = {"room_id": "105"}
    state = {
        "room_id": "20E",
        "new_items": ["shield_key"],
        "story_use_success": "gold_emblem@105_fireplace",
    }
    bd = {"key_item": 4.0, "new_room": 1.0, "story_use": 4.0}
    triggers = detect_milestone_triggers(prev, state, bd)
    assert triggers == [
        "key:shield_key",
        "room:20E",
        "story_use:gold_emblem@105_fireplace",
    ]
    again = detect_milestone_triggers(prev, state, bd, already_captured=set(triggers))
    assert again == []


def test_pb_capture_disabled_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RE1_PB_CAPTURE", raising=False)
    assert pb_capture_enabled() is False
    env = _FakeEnv(tmp_path)
    assert maybe_capture_pb(env, trigger_id="key:emblem", states_dir=tmp_path / "pb") is None
    assert env.bridge.saved == []


def test_maybe_capture_pb_writes_bundle_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_PB_CAPTURE", "1")
    env = _FakeEnv(tmp_path)
    states_dir = tmp_path / "states" / "pb"
    out = maybe_capture_pb(env, trigger_id="key:emblem", states_dir=states_dir)
    assert out is not None
    assert out.is_file()
    sidecar = out.with_name(out.stem + ".sidecar.json")
    assert sidecar.is_file()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["ever_held"] == ["emblem"]
    assert data["captured_room_id"] == "105"

    manifest = json.loads((states_dir / MANIFEST_FILENAME).read_text(encoding="utf-8").strip())
    assert manifest["trigger_id"] == "key:emblem"
    assert manifest["room_id"] == "105"
    assert manifest["inventory_fingerprint"] == inventory_fingerprint(
        {"inventory": ["knife", "emblem"]}
    )

    dup = maybe_capture_pb(env, trigger_id="key:emblem", states_dir=states_dir)
    assert dup is None
    assert len(env.bridge.saved) == 1


def test_append_manifest_row_appends_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    append_manifest_row(path, {"a": 1})
    append_manifest_row(path, {"b": 2})
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["a"] == 1


def test_resolve_pb_bundle_options() -> None:
    assert resolve_pb_bundle(None) is None
    assert resolve_pb_bundle({}) is None
    assert resolve_pb_bundle(
        {"pb_bundle": {"state_path": "a.State", "sidecar_path": "a.sidecar.json"}}
    ) == {"state_path": "a.State", "sidecar_path": "a.sidecar.json"}
    assert resolve_pb_bundle(
        {"pb_state_path": "b.State", "pb_sidecar_path": "b.sidecar.json"}
    ) == {"state_path": "b.State", "sidecar_path": "b.sidecar.json"}


def test_apply_episode_sidecar_on_parts_round_trip() -> None:
    src = EpisodeSidecarParts(
        progress=ProgressTracker(),
        items=ItemTracker(todo=[]),
        episode_history=EpisodeHistory(),
    )
    src.progress.seed_spawn_room("105")
    src.progress.first_visit("20E")
    src.items.ever_held = {"shield_key", "emblem"}
    src.episode_history.reset("105", step=0)
    src.episode_history.on_step(
        {"room_id": "20E", "step": 10},
        prev_state={"room_id": "105", "step": 9},
        new_items=["shield_key"],
    )
    payload = dump_episode_sidecar(src, captured_room_id="20E")

    dst = EpisodeSidecarParts(
        progress=ProgressTracker(),
        items=ItemTracker(todo=[]),
        episode_history=EpisodeHistory(),
    )
    apply_episode_sidecar(dst, payload)
    assert "20E" in dst.progress.visited_rooms
    assert dst.items.ever_held == {"shield_key", "emblem"}
    assert any(room == "20E" for room, _ in dst.episode_history.room_deque.entries)
