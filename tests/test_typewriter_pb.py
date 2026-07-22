"""Typewriter champion PB: gates, score, softlock, sync, reset mix."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.episode_history import EpisodeHistory
from re1_rl.item_todo import ItemTracker
from re1_rl.pb_champion import (
    champion_score,
    score_beats,
    try_replace_champion,
)
from re1_rl.pb_curriculum import sample_champion_or_fresh, sample_reset_bundle
from re1_rl.pb_milestones import detect_milestone_triggers, typewriter_save_gates_ok
from re1_rl.pb_sidecar import EpisodeSidecarParts, apply_episode_sidecar, dump_episode_sidecar
from re1_rl.pb_sync import sync_champion_once
from re1_rl.progress import ProgressTracker
from re1_rl.reward import SOFTLOCK_EXTENSION_FRAMES
from re1_rl.typewriter_save import (
    TYPEWRITER_SAVE_MILESTONE,
    TypewriterSaveDetector,
    ink_ribbon_consumed,
    visited_rooms_allow_prologue_pb,
)


def _slots_state(*, ribbons: int = 1, bullets: int = 10, hp: int = 100, room: str = "106"):
    slots = [["beretta", bullets], ["ink_ribbon", ribbons]]
    return {
        "room_id": room,
        "hp": hp,
        "inventory": ["beretta", "ink_ribbon"],
        "inventory_slots": slots,
        "in_control": True,
        "x": 14000.0,
        "z": 17000.0,
    }


def test_visited_rooms_allowlist() -> None:
    assert visited_rooms_allow_prologue_pb({"105", "104", "106"})
    assert not visited_rooms_allow_prologue_pb({"105", "104", "106", "107"})
    assert not visited_rooms_allow_prologue_pb(set())


def test_typewriter_gates() -> None:
    state = _slots_state()
    visited = {"105", "104", "106"}
    cutscenes = {"104:0:s0"}
    assert typewriter_save_gates_ok(
        state,
        visited_rooms=visited,
        rewarded_cutscenes=cutscenes,
        kenneth_gate_breached=False,
    )
    assert not typewriter_save_gates_ok(
        state,
        visited_rooms=visited,
        rewarded_cutscenes=cutscenes,
        kenneth_gate_breached=True,
    )
    assert not typewriter_save_gates_ok(
        state,
        visited_rooms={"105", "104", "106", "107"},
        rewarded_cutscenes=cutscenes,
        kenneth_gate_breached=False,
    )
    assert not typewriter_save_gates_ok(
        state,
        visited_rooms=visited,
        rewarded_cutscenes=set(),
        kenneth_gate_breached=False,
    )


def test_detector_fires_on_ribbon_drop_then_control() -> None:
    det = TypewriterSaveDetector()
    prev = _slots_state(ribbons=2)
    mid = _slots_state(ribbons=1)
    mid["in_control"] = False
    assert det.update(prev, mid) is False
    done = dict(mid)
    done["in_control"] = True
    assert det.update(mid, done) is True
    assert det.update(done, done) is False


def test_ink_ribbon_consumed() -> None:
    assert ink_ribbon_consumed(_slots_state(ribbons=2), _slots_state(ribbons=1))
    assert not ink_ribbon_consumed(_slots_state(ribbons=1), _slots_state(ribbons=1))


def test_champion_score_prefers_loot_over_ribbons() -> None:
    rich = _slots_state(ribbons=1, bullets=20, hp=100)
    rich["inventory_slots"] = [
        ["beretta", 20],
        ["lockpick", 1],
        ["ink_ribbon", 1],
    ]
    poor = _slots_state(ribbons=5, bullets=20, hp=100)
    poor["inventory_slots"] = [["beretta", 20], ["ink_ribbon", 5]]
    assert score_beats(champion_score(rich), champion_score(poor))
    # Same valuable slots: fewer ribbons wins.
    a = _slots_state(ribbons=1, bullets=10, hp=80)
    b = _slots_state(ribbons=3, bullets=10, hp=80)
    assert score_beats(champion_score(a), champion_score(b))


def test_try_replace_champion_atomic(tmp_path: Path) -> None:
    state_path = tmp_path / "a.State"
    sidecar_path = tmp_path / "a.sidecar.json"
    state_path.write_bytes(b"STATE")
    sidecar_path.write_text("{}", encoding="utf-8")
    state = _slots_state(ribbons=1, bullets=15, hp=90)
    assert try_replace_champion(
        tmp_path, state_path=state_path, sidecar_path=sidecar_path, state=state
    )
    cdir = tmp_path / "states" / "pb" / "champions" / "mainhall_typewriter"
    assert (cdir / "champion.State").is_file()
    rec = json.loads((cdir / "champion.json").read_text(encoding="utf-8"))
    assert rec["milestone_id"] == TYPEWRITER_SAVE_MILESTONE
    # Worse score does not replace.
    worse = _slots_state(ribbons=4, bullets=5, hp=50)
    worse_state = tmp_path / "b.State"
    worse_side = tmp_path / "b.sidecar.json"
    worse_state.write_bytes(b"WORSE")
    worse_side.write_text("{}", encoding="utf-8")
    assert not try_replace_champion(
        tmp_path, state_path=worse_state, sidecar_path=worse_side, state=worse
    )
    assert (cdir / "champion.State").read_bytes() == b"STATE"


def test_apply_sidecar_resets_softlock() -> None:
    src = EpisodeSidecarParts(
        progress=ProgressTracker(),
        items=ItemTracker(todo=[]),
        episode_history=EpisodeHistory(),
    )
    src.progress.seed_spawn_room("105")
    src.progress.note_softlock_extension(1000)
    # note_softlock_extension clears stagnation; accumulate after.
    src.progress.note_stagnation_step(made_progress=False, step_frames=500)
    payload = dump_episode_sidecar(src, captured_room_id="106")
    assert payload["progress"]["stagnation_frames"] == 500
    assert payload["progress"]["softlock_cap_frames"] == 1000

    dst = EpisodeSidecarParts(
        progress=ProgressTracker(),
        items=ItemTracker(todo=[]),
        episode_history=EpisodeHistory(),
    )
    apply_episode_sidecar(dst, payload, reset_softlock=True)
    assert dst.progress.stagnation_frames == 0
    assert dst.progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES


def test_detect_typewriter_milestone_v1_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RE1_PB_V1_TYPEWRITER_ONLY", "1")
    state = _slots_state()
    triggers = detect_milestone_triggers(
        {},
        state,
        {"key_item": 4.0, "new_room": 1.0},
        typewriter_save_complete=True,
        visited_rooms={"105", "104", "106"},
        rewarded_cutscenes={"104:0:s0"},
        kenneth_gate_breached=False,
    )
    assert triggers == [TYPEWRITER_SAVE_MILESTONE]
    # Key/room suppressed in v1 even with breakdown.
    state2 = {
        "room_id": "20E",
        "new_items": ["shield_key"],
        "story_use_success": "gold_emblem@105_fireplace",
    }
    assert detect_milestone_triggers(
        {"room_id": "105"},
        state2,
        {"key_item": 4.0, "new_room": 1.0, "story_use": 4.0},
    ) == []


def test_sync_champion_push_pull(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "local_proj"
    shared = tmp_path / "shared_pb"
    local.mkdir()
    shared.mkdir()
    monkeypatch.setenv("RE1_PB_SHARED_ROOT", str(shared))
    monkeypatch.delenv("RE1_PB_ROOT", raising=False)

    state_path = local / "cap.State"
    sidecar_path = local / "cap.sidecar.json"
    state_path.write_bytes(b"CHAMP")
    sidecar_path.write_text("{}", encoding="utf-8")
    assert try_replace_champion(
        local,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state=_slots_state(ribbons=1, bullets=12, hp=88),
    )
    actions = sync_champion_once(local)
    assert actions["push"] == "ok"
    shared_state = (
        shared / "champions" / "mainhall_typewriter" / "champion.State"
    )
    assert shared_state.read_bytes() == b"CHAMP"

    # Second machine: empty local, pull from shared.
    other = tmp_path / "other_proj"
    other.mkdir()
    actions2 = sync_champion_once(other)
    assert actions2["pull"] == "ok"
    other_state = (
        other
        / "states"
        / "pb"
        / "champions"
        / "mainhall_typewriter"
        / "champion.State"
    )
    assert other_state.read_bytes() == b"CHAMP"


def test_sample_champion_or_fresh_mix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RE1_PB_SHARED_ROOT", raising=False)
    monkeypatch.setenv("RE1_PB_FRESH_WEIGHT", "0.5")
    state_path = tmp_path / "c.State"
    sidecar_path = tmp_path / "c.sidecar.json"
    state_path.write_bytes(b"X")
    sidecar_path.write_text("{}", encoding="utf-8")
    try_replace_champion(
        tmp_path,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state=_slots_state(),
    )
    rng = random.Random(0)
    hits = [sample_champion_or_fresh(tmp_path, rng=rng) is not None for _ in range(40)]
    assert any(hits) and not all(hits)

    # No champion → always fresh.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert sample_champion_or_fresh(empty, rng=random.Random(1)) is None


def test_sample_reset_bundle_fresh_weight() -> None:
    from re1_rl.pb_curriculum import PbBundle

    bundles = [
        PbBundle("a.State", "a.sidecar.json", "m1"),
        PbBundle("b.State", "b.sidecar.json", "m1"),
    ]
    assert sample_reset_bundle(bundles, fresh_weight=1.0, rng=random.Random(0)) is None
    assert sample_reset_bundle(bundles, fresh_weight=0.0, rng=random.Random(0)) is not None
