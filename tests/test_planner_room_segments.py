"""Unit tests for planner room-exit segment chaining."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from re1_rl.planner_room_segments import (
    RoomSegmentTracker,
    clear_room_segments_cache,
    is_segment_exit,
    is_segment_midhop,
    load_room_segments,
    record_best_s_room,
    room_segments_enabled,
    segment_budget_frames,
    segment_covering_slot,
    segment_for_tip_slot,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_room_segments_cache()
    yield
    clear_room_segments_cache()


def test_segments_file_loads_prototypes(project_root: Path | None = None) -> None:
    root = Path(__file__).resolve().parents[1]
    data = load_room_segments(root, force=True)
    segs = data.get("segments") or []
    assert len(segs) >= 2
    proto = [s for s in segs if s.get("prototype")]
    ids = {s["id"] for s in proto}
    assert "opening_emblem_to_tea" in ids
    assert "l_hallway_bullets" in ids


def test_segment_covering_prototype_only(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("RE1_PLANNER_ROOM_SEGMENTS", "1")
    monkeypatch.setenv("RE1_PLANNER_ROOM_SEGMENTS_PROTOTYPE_ONLY", "1")
    clear_room_segments_cache()
    assert segment_covering_slot(1, root, prototype_only=True)["id"] == (
        "opening_emblem_to_tea"
    )
    assert segment_covering_slot(23, root, prototype_only=True)["id"] == (
        "l_hallway_bullets"
    )
    # Non-prototype segments hidden when prototype_only.
    assert segment_covering_slot(12, root, prototype_only=True) is None
    assert segment_covering_slot(12, root, prototype_only=False)["id"] == (
        "bar_piano_gold_swap"
    )


def test_midhop_and_exit_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("RE1_PLANNER_ROOM_SEGMENTS", "1")
    monkeypatch.setenv("RE1_PLANNER_ROOM_SEGMENTS_PROTOTYPE_ONLY", "1")
    clear_room_segments_cache()
    # Opening remint steps carry explicit slot_index on the chunk; without
    # steps, slot_index_for_completed_step maps idx→pl07+. Use tip helpers
    # and direct slot covering for the flag logic with synthetic steps.
    steps = [
        {"slot_index": 1, "op": "acquire"},  # completed 0 → pl01
        {"slot_index": 2, "op": "traverse"},  # completed 1 → pl02
    ]
    assert is_segment_midhop(0, root, steps=steps) is True
    assert is_segment_exit(0, root, steps=steps) is False
    assert is_segment_midhop(1, root, steps=steps) is False
    assert is_segment_exit(1, root, steps=steps) is True


def test_flag_off_disables_midhop(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("RE1_PLANNER_ROOM_SEGMENTS", raising=False)
    clear_room_segments_cache()
    assert room_segments_enabled() is False
    steps = [{"slot_index": 1}, {"slot_index": 2}]
    assert is_segment_midhop(0, root, steps=steps) is False


def test_shared_budget_scales_with_hops() -> None:
    root = Path(__file__).resolve().parents[1]
    seg = segment_covering_slot(22, root, prototype_only=False)
    assert seg is not None
    # pl22..pl24 = 3 hops × 21600
    assert segment_budget_frames(seg, root) == 3 * 21600


def test_additive_tracker_and_best(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_PLANNER_ROOM_SEGMENTS", "1")
    seg = {
        "id": "opening_emblem_to_tea",
        "pl_first": 1,
        "pl_last": 2,
        "prototype": True,
    }
    tracker = RoomSegmentTracker.begin(seg, project_root=tmp_path)
    # Point best-scores file at tmp via monkeypatch of cwd-relative write:
    # record_best_s_room uses project_root.
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    # Minimal segments file so load works if called.
    (tmp_path / "data" / "planner_loyal_room_segments.json").write_text(
        json.dumps({"segments": [seg], "budget_frames_per_hop": 21600}),
        encoding="utf-8",
    )
    clear_room_segments_cache()
    tracker = RoomSegmentTracker.begin(seg, project_root=tmp_path)
    assert tracker.budget_frames == 2 * 21600
    tracker.note_hop_success({"S": 0.5}, 0.5)
    tracker.note_hop_success({"S": 0.4}, 0.4)
    assert abs(tracker.s_room - 0.9) < 1e-9
    rec = record_best_s_room(tracker, project_root=tmp_path)
    assert rec is not None
    assert abs(float(rec["S_room"]) - 0.9) < 1e-9
    # Worse score does not overwrite.
    tracker2 = RoomSegmentTracker.begin(seg, project_root=tmp_path)
    tracker2.note_hop_success(None, 0.1)
    tracker2.note_hop_success(None, 0.1)
    assert record_best_s_room(tracker2, project_root=tmp_path) is None
    # Better score wins.
    tracker3 = RoomSegmentTracker.begin(seg, project_root=tmp_path)
    tracker3.note_hop_success(None, 0.6)
    tracker3.note_hop_success(None, 0.5)
    rec3 = record_best_s_room(tracker3, project_root=tmp_path)
    assert rec3 is not None
    assert abs(float(rec3["S_room"]) - 1.1) < 1e-9


def test_tip_slot_resolves_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("RE1_PLANNER_ROOM_SEGMENTS", "1")
    monkeypatch.setenv("RE1_PLANNER_ROOM_SEGMENTS_PROTOTYPE_ONLY", "1")
    clear_room_segments_cache()
    # Tip pl00 → next mint pl01 is in opening_emblem_to_tea.
    assert segment_for_tip_slot(0, root)["id"] == "opening_emblem_to_tea"
    # Tip pl21 → next mint pl22 is in l_hallway_bullets.
    assert segment_for_tip_slot(21, root)["id"] == "l_hallway_bullets"


def test_gallery_and_armor_stay_pl_by_pl(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("RE1_PLANNER_ROOM_SEGMENTS", "1")
    monkeypatch.setenv("RE1_PLANNER_ROOM_SEGMENTS_PROTOTYPE_ONLY", "0")
    clear_room_segments_cache()
    # Gallery portraits (pl41-pl49) and armor room (pl82-pl85) are not segments.
    for slot in (41, 44, 49, 82, 84, 85):
        assert segment_covering_slot(slot, root, prototype_only=False) is None
    # Neighbours still chain.
    assert segment_covering_slot(36, root, prototype_only=False)["id"] == (
        "shotgun_living_room"
    )
    assert segment_covering_slot(92, root, prototype_only=False)["id"] == (
        "place_sun_crest"
    )


def test_pl_by_pl_flag_skips_segment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import json as _json

    segs = [
        {"id": "room_a", "pl_first": 10, "pl_last": 12},
        {"id": "room_b", "pl_first": 13, "pl_last": 14, "pl_by_pl": True},
    ]
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "planner_loyal_room_segments.json").write_text(
        _json.dumps({"segments": segs, "budget_frames_per_hop": 21600}),
        encoding="utf-8",
    )
    clear_room_segments_cache()
    assert segment_covering_slot(11, tmp_path, prototype_only=False)["id"] == "room_a"
    assert segment_covering_slot(13, tmp_path, prototype_only=False) is None
