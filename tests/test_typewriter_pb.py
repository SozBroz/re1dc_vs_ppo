"""Typewriter champion PB: multi-room detector, capture_ok, score v2, sync, mix."""

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
    champion_score_v2,
    score_beats,
    try_replace_champion,
    typewriter_champion_subdir,
    typewriter_milestone_id,
)
from re1_rl.pb_curriculum import sample_champion_or_fresh, sample_reset_bundle
from re1_rl.pb_milestones import detect_milestone_triggers, typewriter_save_capture_ok
from re1_rl.pb_sidecar import EpisodeSidecarParts, apply_episode_sidecar, dump_episode_sidecar
from re1_rl.pb_sync import sync_champion_once
from re1_rl.progress import ProgressTracker
from re1_rl.reward import SOFTLOCK_EXTENSION_FRAMES, TYPEWRITER_SAVE_BONUS, compute_reward
from re1_rl.typewriter_save import (
    TYPEWRITER_SAVE_MILESTONE,
    TypewriterSaveDetector,
    _SIDECAR_HOLDOFF_CONTROL_STREAK,
    ink_ribbon_consumed,
    typewriter_save_cutscene_disqualified,
)
from tests.test_scaffolding import make_planner


def _slots_state(
    *,
    ribbons: int = 1,
    bullets: int = 10,
    hp: int = 100,
    room: str = "106",
    extra_slots: list | None = None,
):
    slots = [["beretta", bullets], ["ink_ribbon", ribbons]]
    if extra_slots:
        slots = list(extra_slots) + slots
    inv = [s[0] for s in slots if s[0]]
    return {
        "room_id": room,
        "hp": hp,
        "inventory": inv,
        "inventory_slots": slots,
        "in_control": True,
        "x": 14000.0,
        "z": 17000.0,
    }


def test_typewriter_save_capture_ok() -> None:
    state = _slots_state(room="106")
    assert typewriter_save_capture_ok(
        state, room="106", kenneth_gate_breached=False
    )
    assert not typewriter_save_capture_ok(
        state, room="106", kenneth_gate_breached=True
    )
    assert not typewriter_save_capture_ok(
        state, room="118", kenneth_gate_breached=False
    )
    # Prologue allowlist is not a capture gate: room match + no Kenneth breach.
    wide_visit_state = _slots_state(room="106")
    assert typewriter_save_capture_ok(
        wide_visit_state, room="106", kenneth_gate_breached=False
    )


def test_detect_typewriter_ignores_prologue_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_PB_V1_TYPEWRITER_ONLY", "1")
    state = _slots_state(room="106")
    # Visited outside {105,104,106} and no Kenneth cinema — still captures.
    triggers = detect_milestone_triggers(
        {},
        state,
        {},
        typewriter_save_complete=True,
        typewriter_save_room="106",
        visited_rooms={"105", "104", "106", "107", "118"},
        rewarded_cutscenes=set(),
        kenneth_gate_breached=False,
    )
    assert triggers == [TYPEWRITER_SAVE_MILESTONE]

    breached = detect_milestone_triggers(
        {},
        state,
        {},
        typewriter_save_complete=True,
        typewriter_save_room="106",
        kenneth_gate_breached=True,
    )
    assert breached == []


def test_detect_typewriter_milestone_room_118(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_PB_V1_TYPEWRITER_ONLY", "1")
    state = _slots_state(room="118")
    triggers = detect_milestone_triggers(
        {},
        state,
        {},
        typewriter_save_complete=True,
        typewriter_save_room="118",
        kenneth_gate_breached=False,
    )
    assert triggers == [typewriter_milestone_id("118")]


def _run_save_complete(det: TypewriterSaveDetector, *, room: str = "106") -> bool:
    prev = _slots_state(ribbons=2, room=room)
    drop = _slots_state(ribbons=1, room=room)
    drop["in_control"] = True
    assert det.update(prev, drop) is False
    cinema = dict(drop)
    cinema["in_control"] = False
    assert det.update(drop, cinema) is False
    ctrl1 = dict(cinema)
    ctrl1["in_control"] = True
    assert det.update(cinema, ctrl1) is False
    ctrl2 = dict(ctrl1)
    return bool(det.update(ctrl1, ctrl2))


def test_detector_waits_for_save_cinema_then_stable_control() -> None:
    det = TypewriterSaveDetector()
    assert _run_save_complete(det) is True
    assert det.completed_room == "106"
    done = _slots_state(ribbons=1)
    done["in_control"] = True
    assert det.update(done, done) is False


@pytest.mark.parametrize("save_slot", [1, 2, 3, 5, 8, 15])
def test_detector_complete_independent_of_memory_card_slot(save_slot: int) -> None:
    """TypewriterSaveDetector keys off ribbon drop + cinema, not save slot."""
    del save_slot  # slot is not an input to the detector
    det = TypewriterSaveDetector()
    assert _run_save_complete(det) is True
    assert det.completed_room == "106"


def test_detector_logs_armed_and_complete(capsys: pytest.CaptureFixture[str]) -> None:
    det = TypewriterSaveDetector()
    assert _run_save_complete(det) is True
    out = capsys.readouterr().out
    assert "[typewriter_save] event=armed" in out
    assert "[typewriter_save] event=cinema" in out
    assert "[typewriter_save] event=complete" in out


def test_sidecar_holdoff_logs_begin_and_clear(capsys: pytest.CaptureFixture[str]) -> None:
    """PB start: uncontrolled→control must not complete a save without ribbon drop."""
    det = TypewriterSaveDetector()
    spawn = _slots_state(ribbons=1)
    spawn["in_control"] = False
    det.begin_episode(from_sidecar=True, state=spawn)
    assert det.sidecar_holdoff
    # Mimic load settle / ribbon flicker that would otherwise arm+complete.
    flicker = _slots_state(ribbons=0)
    flicker["in_control"] = False
    assert det.update(spawn, flicker) is False
    ctrl = _slots_state(ribbons=1)
    ctrl["in_control"] = True
    for _ in range(_SIDECAR_HOLDOFF_CONTROL_STREAK + 2):
        assert det.update(ctrl, ctrl) is False
    assert not det.sidecar_holdoff
    out = capsys.readouterr().out
    assert "[typewriter_save] event=holdoff_begin" in out
    assert "[typewriter_save] event=holdoff_clear" in out
    # Real save after holdoff clears still pays/detects.
    assert _run_save_complete(det) is True


def test_typewriter_save_reward_pays_on_complete_flag() -> None:
    progress = ProgressTracker()
    progress.first_visit("106")
    progress.claim_spawn_room_bonus()
    prev = _slots_state(ribbons=1)
    prev["in_control"] = True
    cur = dict(prev)
    cap_before = progress.softlock_cap_frames
    _, bd = compute_reward(
        prev,
        cur,
        make_planner(),
        progress=progress,
        typewriter_save_complete=True,
        return_breakdown=True,
    )
    assert bd["typewriter_save"] == pytest.approx(TYPEWRITER_SAVE_BONUS)
    assert bd["typewriter_save"] == pytest.approx(0.3)
    # Modest crumb — does not raise the 12 min idle floor by itself.
    assert progress.softlock_cap_frames == cap_before

    _, bd2 = compute_reward(
        prev,
        cur,
        make_planner(),
        progress=progress,
        typewriter_save_complete=False,
        return_breakdown=True,
    )
    assert bd2["typewriter_save"] == 0.0


def test_detector_works_in_non_106_room() -> None:
    det = TypewriterSaveDetector()
    prev = _slots_state(ribbons=2, room="118")
    drop = _slots_state(ribbons=1, room="118")
    drop["in_control"] = True
    assert det.update(prev, drop) is False
    assert det.armed_room == "118"
    cinema = dict(drop)
    cinema["in_control"] = False
    assert det.update(drop, cinema) is False
    ctrl1 = dict(cinema)
    ctrl1["in_control"] = True
    assert det.update(cinema, ctrl1) is False
    ctrl2 = dict(ctrl1)
    assert det.update(ctrl1, ctrl2) is True
    assert det.completed_room == "118"
    assert det.last_room == "118"


def test_detector_ribbon_drop_already_uncontrolled() -> None:
    det = TypewriterSaveDetector()
    prev = _slots_state(ribbons=2)
    mid = _slots_state(ribbons=1)
    mid["in_control"] = False
    assert det.update(prev, mid) is False
    done = dict(mid)
    done["in_control"] = True
    assert det.update(mid, done) is False  # streak 1
    assert det.update(done, done) is True  # streak 2


def test_ink_ribbon_consumed() -> None:
    assert ink_ribbon_consumed(_slots_state(ribbons=2), _slots_state(ribbons=1))
    assert not ink_ribbon_consumed(_slots_state(ribbons=1), _slots_state(ribbons=1))


def test_typewriter_save_cutscene_disqualified_non_106() -> None:
    prev = _slots_state(ribbons=2, room="118")
    new = _slots_state(ribbons=1, room="118")
    assert typewriter_save_cutscene_disqualified(prev, new)

    prev106 = _slots_state(ribbons=2, room="106")
    new106 = _slots_state(ribbons=1, room="106")
    assert typewriter_save_cutscene_disqualified(prev106, new106)

    # Non-typewriter room: ribbon drop is not a TW save cinema.
    prev_other = _slots_state(ribbons=2, room="107")
    new_other = _slots_state(ribbons=1, room="107")
    assert not typewriter_save_cutscene_disqualified(prev_other, new_other)


def test_champion_score_v2_herb_table() -> None:
    # green=1/3, red=2/3, blue=1/6; mixes are atom sums.
    green = champion_score_v2(
        inventory_slots=[["green_herb", 1]],
        box_cache=None,
        ever_held=(),
        visited_rooms=(),
        hp=100,
    )
    assert green[0] == round(1000.0 / 3.0)

    red = champion_score_v2(
        inventory_slots=[["red_herb", 1]],
        box_cache=None,
        ever_held=(),
        visited_rooms=(),
        hp=100,
    )
    assert red[0] == round(1000.0 * (2.0 / 3.0))

    mix_gr = champion_score_v2(
        inventory_slots=[["mixed_herbs_gr", 1]],
        box_cache=None,
        ever_held=(),
        visited_rooms=(),
        hp=100,
    )
    assert mix_gr[0] == round(1000.0 * (1.0 / 3.0 + 2.0 / 3.0))

    # Herb V beats empty; red (2/3) beats green (1/3).
    assert score_beats(red, green, candidate_version=2, incumbent_version=2)


def test_champion_score_v2_ever_held_key_credit() -> None:
    # Key physically present: counts once in V, not double via ever_held.
    present = champion_score_v2(
        inventory_slots=[["lockpick", 1], ["beretta", 10]],
        box_cache=None,
        ever_held=("lockpick",),
        visited_rooms=("105",),
        hp=80,
    )
    # Same inventory without lockpick, but ever_held still credits the key.
    credited = champion_score_v2(
        inventory_slots=[["beretta", 10]],
        box_cache=None,
        ever_held=("lockpick",),
        visited_rooms=("105",),
        hp=80,
    )
    bare = champion_score_v2(
        inventory_slots=[["beretta", 10]],
        box_cache=None,
        ever_held=(),
        visited_rooms=("105",),
        hp=80,
    )
    assert present[0] == credited[0]
    assert score_beats(credited, bare, candidate_version=2, incumbent_version=2)
    assert credited[0] == bare[0] + 1000


def test_champion_score_v2_visited_tie_break() -> None:
    base_kw = dict(
        inventory_slots=[["beretta", 10]],
        box_cache=None,
        ever_held=(),
        hp=90,
    )
    few = champion_score_v2(visited_rooms=("105", "106"), **base_kw)
    many = champion_score_v2(
        visited_rooms=("105", "104", "106", "118"), **base_kw
    )
    assert few[:4] == many[:4]
    assert many[4] > few[4]
    assert score_beats(many, few, candidate_version=2, incumbent_version=2)


def test_champion_score_legacy_v1_still_orders() -> None:
    rich = _slots_state(ribbons=1, bullets=20, hp=100)
    rich["inventory_slots"] = [
        ["beretta", 20],
        ["lockpick", 1],
        ["ink_ribbon", 1],
    ]
    poor = _slots_state(ribbons=5, bullets=20, hp=100)
    poor["inventory_slots"] = [["beretta", 20], ["ink_ribbon", 5]]
    assert score_beats(champion_score(rich), champion_score(poor))


def test_try_replace_champion_atomic_and_room_slot(tmp_path: Path) -> None:
    state_path = tmp_path / "a.State"
    sidecar_path = tmp_path / "a.sidecar.json"
    state_path.write_bytes(b"STATE")
    sidecar_path.write_text("{}", encoding="utf-8")
    state = _slots_state(ribbons=1, bullets=15, hp=90, room="106")
    assert try_replace_champion(
        tmp_path,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state=state,
        room_id="106",
        visited_rooms=("105", "106"),
    )
    cdir = tmp_path / "states" / "pb" / "champions" / "mainhall_typewriter"
    assert (cdir / "champion.State").is_file()
    assert (cdir / "champion.sidecar.json").is_file()
    rec = json.loads((cdir / "champion.json").read_text(encoding="utf-8"))
    side = json.loads((cdir / "champion.sidecar.json").read_text(encoding="utf-8"))
    assert rec["milestone_id"] == TYPEWRITER_SAVE_MILESTONE
    assert rec["score_version"] == 2
    assert len(rec["score"]) == 5
    assert rec["bundle_id"] and rec["bundle_id"] == side["bundle_id"]
    assert rec.get("state_sha256")

    worse = _slots_state(ribbons=4, bullets=5, hp=50)
    worse_state = tmp_path / "b.State"
    worse_side = tmp_path / "b.sidecar.json"
    worse_state.write_bytes(b"WORSE")
    worse_side.write_text("{}", encoding="utf-8")
    assert not try_replace_champion(
        tmp_path,
        state_path=worse_state,
        sidecar_path=worse_side,
        state=worse,
        room_id="106",
        visited_rooms=("105",),
    )
    assert (cdir / "champion.State").read_bytes() == b"STATE"

    # Separate slot for room 118.
    s118 = tmp_path / "c.State"
    side118 = tmp_path / "c.sidecar.json"
    s118.write_bytes(b"R118")
    side118.write_text("{}", encoding="utf-8")
    assert try_replace_champion(
        tmp_path,
        state_path=s118,
        sidecar_path=side118,
        state=_slots_state(room="118", bullets=8, hp=70),
        room_id="118",
        visited_rooms=("118",),
    )
    assert typewriter_champion_subdir("118") == "champions/typewriter_118"
    c118 = tmp_path / "states" / "pb" / "champions" / "typewriter_118"
    assert (c118 / "champion.State").read_bytes() == b"R118"
    rec118 = json.loads((c118 / "champion.json").read_text(encoding="utf-8"))
    assert rec118["milestone_id"] == "typewriter_save:118"


def test_apply_sidecar_resets_softlock() -> None:
    src = EpisodeSidecarParts(
        progress=ProgressTracker(),
        items=ItemTracker(todo=[]),
        episode_history=EpisodeHistory(),
    )
    src.progress.seed_spawn_room("105")
    src.progress.note_softlock_extension(1000)
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
        typewriter_save_room="106",
        visited_rooms={"105", "104", "106"},
        rewarded_cutscenes={"104:0:s0"},
        kenneth_gate_breached=False,
    )
    assert triggers == [TYPEWRITER_SAVE_MILESTONE]
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
        room_id="106",
        visited_rooms=("105", "106"),
    )
    actions = sync_champion_once(local)
    assert actions["push"] == "ok"
    shared_state = (
        shared / "champions" / "mainhall_typewriter" / "champion.State"
    )
    assert shared_state.read_bytes() == b"CHAMP"

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
    state_path = tmp_path / "c.State"
    sidecar_path = tmp_path / "c.sidecar.json"
    state_path.write_bytes(b"X")
    sidecar_path.write_text("{}", encoding="utf-8")
    try_replace_champion(
        tmp_path,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state=_slots_state(),
        room_id="106",
    )
    rng = random.Random(0)
    hits = [sample_champion_or_fresh(tmp_path, rng=rng) is not None for _ in range(40)]
    assert any(hits) and not all(hits)

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
