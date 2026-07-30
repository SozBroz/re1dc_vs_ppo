"""Danger-room PB champions: capture triggers, scoring, reset mix."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.episode_history import EpisodeHistory
from re1_rl.item_todo import ItemTracker
from re1_rl.pb_capture import MANIFEST_FILENAME, maybe_capture_pb
from re1_rl.pb_champion import (
    DANGER_ROOM_SCORE_VERSION,
    danger_room_score_v2,
    list_filled_danger_room_champions,
    try_replace_champion,
)
from re1_rl.pb_curriculum import sample_training_start, sample_typewriter_start
from re1_rl.pb_milestones import (
    DANGER_ROOM_MILESTONES,
    detect_milestone_triggers,
    milestone_id_for_danger_room,
)
from re1_rl.progress import ProgressTracker


class _FakeBridge:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def save_savestate(self, path: str) -> None:
        self.saved.append(path)
        Path(path).write_bytes(b"FAKE_STATE")


class _FakeEnv:
    def __init__(self, tmp_path: Path, *, room_id: str = "108", hp: int = 80) -> None:
        self.project_root = tmp_path
        self.bridge = _FakeBridge()
        self._step_count = 99
        self._progress = ProgressTracker()
        self._progress.seed_spawn_room("105")
        self._progress.first_visit("105")
        self._progress.first_visit("107")
        self._items = ItemTracker(todo=[])
        self._items.ever_held = {"beretta", "knife"}
        self._episode_history = EpisodeHistory()
        self._episode_history.reset("105", step=0)
        self._box_cache = None
        self._pb_captured_triggers: set[str] = set()
        self._room_id = room_id
        self._hp = hp

    def _read_state(self, *, track_items: bool = True) -> dict:
        return {
            "room_id": self._room_id,
            "hp": self._hp,
            "inventory": ["beretta", "knife", "handgun_bullets"],
            "inventory_slots": [["beretta", 12], ["handgun_bullets", 30]],
            "step": self._step_count,
        }


def test_danger_room_milestone_helpers() -> None:
    assert DANGER_ROOM_MILESTONES == frozenset({"108", "202", "204"})
    assert milestone_id_for_danger_room("108") == "room:108"
    assert milestone_id_for_danger_room("203") is None


def test_detect_danger_room_while_typewriter_v1_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_PB_V1_TYPEWRITER_ONLY", "1")
    monkeypatch.setenv("RE1_PB_DANGER_ROOMS", "1")
    prev = {"room_id": "107"}
    state = {"room_id": "108"}
    triggers = detect_milestone_triggers(
        prev, state, {"new_room": 4.0}, kenneth_gate_breached=False
    )
    assert triggers == ["room:108"]


def test_danger_room_score_prefers_hp_over_loot() -> None:
    low_hp = danger_room_score_v2(
        inventory_slots=[["beretta", 1]],
        box_cache=None,
        ever_held=["beretta"],
        hp=40,
    )
    high_hp = danger_room_score_v2(
        inventory_slots=[["beretta", 1]],
        box_cache=None,
        ever_held=["beretta"],
        hp=90,
    )
    assert high_hp > low_hp


def test_maybe_capture_danger_room_installs_champion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_PB_CAPTURE", "1")
    env = _FakeEnv(tmp_path, room_id="204", hp=88)
    states_dir = tmp_path / "states" / "pb"
    out = maybe_capture_pb(env, trigger_id="room:204", states_dir=states_dir)
    assert out is not None
    assert out.name == "champion.State"
    assert (out.parent / "champion.sidecar.json").is_file()
    rec = json.loads((out.parent / "champion.json").read_text(encoding="utf-8"))
    assert rec["milestone_id"] == "room:204"
    assert rec["score_version"] == DANGER_ROOM_SCORE_VERSION
    filled = list_filled_danger_room_champions(tmp_path)
    assert len(filled) == 1
    assert filled[0]["room_id"] == "204"
    manifest = (states_dir / MANIFEST_FILENAME).read_text(encoding="utf-8").strip()
    assert "room:204" in manifest


def test_champion_replace_keeps_better_hp_arrival(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RE1_PB_SHARED_ROOT", raising=False)
    state_path = tmp_path / "108_a.State"
    sidecar_path = tmp_path / "108_a.sidecar.json"
    state_path.write_bytes(b"A")
    sidecar_path.write_text("{}", encoding="utf-8")
    assert try_replace_champion(
        tmp_path,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state={
            "room_id": "108",
            "hp": 50,
            "inventory_slots": [["beretta", 10]],
            "inventory": ["beretta"],
        },
        milestone_id="room:108",
        room_id="108",
    )
    state_path.write_bytes(b"B")
    assert not try_replace_champion(
        tmp_path,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state={
            "room_id": "108",
            "hp": 40,
            "inventory_slots": [["beretta", 10]],
            "inventory": ["beretta"],
        },
        milestone_id="room:108",
        room_id="108",
    )
    assert try_replace_champion(
        tmp_path,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state={
            "room_id": "108",
            "hp": 95,
            "inventory_slots": [["beretta", 10]],
            "inventory": ["beretta"],
        },
        milestone_id="room:108",
        room_id="108",
    )
    rec = json.loads(
        (tmp_path / "states/pb/champions/room_108/champion.json").read_text(
            encoding="utf-8"
        )
    )
    assert rec["hp"] == 95


def _seed_danger_champion(project_root: Path, room: str, hp: int, tag: bytes) -> None:
    state_path = project_root / f"danger_{room}.State"
    sidecar_path = project_root / f"danger_{room}.sidecar.json"
    state_path.write_bytes(tag)
    sidecar_path.write_text("{}", encoding="utf-8")
    assert try_replace_champion(
        project_root,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state={
            "room_id": room,
            "hp": hp,
            "inventory_slots": [["beretta", 10]],
            "inventory": ["beretta"],
        },
        milestone_id=f"room:{room}",
        room_id=room,
    )


def test_sample_training_start_mixes_typewriter_and_danger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RE1_PB_SHARED_ROOT", raising=False)
    from re1_rl.pb_champion import try_replace_champion as tw_replace

    tw_state = tmp_path / "106.State"
    tw_side = tmp_path / "106.sidecar.json"
    tw_state.write_bytes(b"T")
    tw_side.write_text("{}", encoding="utf-8")
    assert tw_replace(
        tmp_path,
        state_path=tw_state,
        sidecar_path=tw_side,
        state={
            "room_id": "106",
            "hp": 100,
            "inventory_slots": [["ink_ribbon", 1]],
            "inventory": ["ink_ribbon"],
        },
        room_id="106",
        visited_rooms=("106",),
    )
    _seed_danger_champion(tmp_path, "108", 90, b"D")

    rng = random.Random(3)
    n = 6000
    paths: set[str | None] = set()
    for _ in range(n):
        picked = sample_training_start(tmp_path, rng=rng)
        if picked is None:
            paths.add(None)
        else:
            paths.add(picked["state_path"])
    assert None in paths
    assert any("room_108" in (p or "") for p in paths if p)
    assert any("mainhall_typewriter" in (p or "") for p in paths if p)

    # Typewriter-only sampler ignores danger slots.
    rng2 = random.Random(3)
    danger_hits = 0
    for _ in range(n):
        picked = sample_typewriter_start(tmp_path, rng=rng2)
        if picked and "room_108" in picked["state_path"]:
            danger_hits += 1
    assert danger_hits == 0
