"""Fight-cliff discovery and ripple grind for pay-forward resets."""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

from re1_rl.go_explore_archive import quality_beats
from re1_rl.yawn_rails_payforward import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_GRIND,
    STATUS_RIPPLING,
    PayforwardRippleStore,
    choose_progression_reset_index,
    discover_fights,
    fight_efficiency_met,
    fight_target_for_index,
    frontier_fight_index,
    fight_leg_valid,
    hop_blocked,
    project_end_quality,
    sample_payforward_options,
)


def _row(idx: int, ammo: int, hp: int = 80, **extra: object) -> dict:
    q = [hp, ammo, 1, 10, 1, 0, 0]
    row = {
        "checkpoint_index": idx,
        "checkpoint_id": f"cp{idx:02d}_id",
        "next_checkpoint_id": f"cp{idx + 1:02d}_id",
        "quality": q,
        "state_path": f"states/yawn_rails/cells/cp{idx:02d}/cell.State",
        "sidecar_path": f"states/yawn_rails/cells/cp{idx:02d}/cell.sidecar.json",
    }
    row.update(extra)
    return row


def test_discover_fights_ammo_cliffs_and_stretches() -> None:
    cells = [
        _row(18, 75),
        _row(19, 56),
        _row(20, 56),
        _row(34, 103),
        _row(35, 66),
        _row(36, 66),
        _row(37, 66),
        _row(38, 59),
    ]
    fights = discover_fights(cells)
    assert [f.fight_index for f in fights] == [18, 34, 37]
    assert fights[0].stretch_end == 34
    assert fights[1].stretch_end == 37
    assert fights[2].stretch_end == 39  # max_idx+1


def test_discover_ignores_ammo_gains() -> None:
    cells = [_row(22, 56), _row(23, 103)]
    assert discover_fights(cells) == []


def test_discover_force_fight_without_ammo_cliff(monkeypatch) -> None:
    monkeypatch.delenv("RE1_YAWN_PAYFORWARD_FORCE_FIGHTS", raising=False)
    monkeypatch.delenv("RE1_YAWN_PAYFORWARD_IGNORE_FIGHTS", raising=False)
    cells = [_row(44, 50), _row(45, 50), _row(46, 50), _row(47, 40)]
    assert [f.fight_index for f in discover_fights(cells)] == [46]
    forced = discover_fights(cells, force={45})
    assert [f.fight_index for f in forced] == [45, 46]
    assert forced[0].stretch_end == 46


def test_discover_ignore_fight_extends_prior_stretch(monkeypatch) -> None:
    monkeypatch.delenv("RE1_YAWN_PAYFORWARD_FORCE_FIGHTS", raising=False)
    monkeypatch.delenv("RE1_YAWN_PAYFORWARD_IGNORE_FIGHTS", raising=False)
    cells = [
        _row(45, 53),
        _row(46, 0),
        _row(51, 180),
        _row(52, 180),
        _row(53, 120),
    ]
    # Natural cliffs: 45 and 52. Ignoring 52 lets 45 ripple to end.
    assert [f.fight_index for f in discover_fights(cells)] == [45, 52]
    ignored = discover_fights(cells, force={45}, ignore={52})
    assert [f.fight_index for f in ignored] == [45]
    assert ignored[0].stretch_end == 54


def test_done_stretch_reopens_on_fight_successor_improve(tmp_path: Path) -> None:
    # Keep post-fight ammo flat so only cp18 is a fight edge.
    cells = [_row(18, 70)] + [_row(i, 40, hp=80) for i in range(19, 24)]
    store = PayforwardRippleStore(tmp_path / "pf_done_reopen.json")
    route = [{"checkpoint_id": f"cp{i:02d}_id", "items_gained": []} for i in range(19, 24)]
    store.reconcile(cells, route=route)
    assert store.fights[18].stretch_end == 24
    # Walk ripple to stretch_done at next-fight boundary (max+1).
    for idx in range(19, 24):
        cells[idx - 18] = _row(idx, 40, hp=96)
        store.on_install(idx, cells, route=route)
    assert store.fights[18].status == STATUS_DONE
    # New improve of fight successor (HP only — keep ammo flat) re-opens ripple.
    cells[1] = _row(19, 40, hp=99)
    store.on_install(19, cells, route=route)
    assert store.fights[18].status == STATUS_RIPPLING
    assert store.fights[18].ripple_tip == 19


def test_project_end_quality_shotgun_and_clip() -> None:
    start = (96, 56, 1, 10, 1, 0, 0)
    shotgun = project_end_quality(start, {"items_gained": ["shotgun"]})
    assert shotgun[0] == 96
    assert shotgun[1] == 56 + 50
    assert shotgun[3] == 11  # slots +1
    clip = project_end_quality(start, {"items_gained": ["handgun_bullets"]})
    assert clip[1] == 56 + 15


def test_hop_blocked_uses_projection_not_raw_ammo() -> None:
    # Equal HP: raw tip ammo loses, but shotgun gain can win.
    tip = (80, 60, 1, 10, 1, 0, 0)
    succ = (80, 100, 1, 11, 1, 0, 0)
    assert hop_blocked(tip, succ, {"items_gained": []})
    assert not hop_blocked(tip, succ, {"items_gained": ["shotgun"]})
    # Healthier tip beats richer ammo successor even without pickup.
    assert not hop_blocked((96, 56, 1, 10, 1, 0, 0), (80, 103, 2, 11, 1, 0, 0), None)


def test_hop_blocked_shotgun_leg_does_not_false_stop_at_cp22() -> None:
    """Incumbent cp23 already includes shotgun ammo; tip with more HG must proceed.

    Old +43 projection: 56+43=99 < 103 → false block. Strip/cushion fixes it.
    """
    tip = (80, 56, 1, 10, 1, 0, 0)
    succ = (80, 103, 2, 11, 1, 0, 73)
    step = {"items_gained": ["shotgun"]}
    assert not hop_blocked(tip, succ, step)
    # Richer tip (11 more HG-eq) clearly clears.
    assert not hop_blocked((80, 67, 1, 10, 1, 0, 0), succ, step)


def test_blocked_hop_reopens_when_tip_quality_improves(tmp_path: Path) -> None:
    cells = [_row(18, 70)] + [_row(i, 40, hp=40) for i in range(19, 24)]
    cells[5] = _row(23, 200, hp=40)  # index 23-18=5
    # flatten: cells[0]=18 ... cells[5]=23
    store = PayforwardRippleStore(tmp_path / "pf_reopen.json")
    route = [
        {"checkpoint_id": "cp19_id", "items_gained": []},
        {"checkpoint_id": "cp20_id", "items_gained": []},
        {"checkpoint_id": "cp21_id", "items_gained": []},
        {"checkpoint_id": "cp22_id", "items_gained": []},
        {"checkpoint_id": "cp23_id", "items_gained": ["shotgun"]},
    ]
    store.reconcile(cells, route=route)
    # Force tip at 22 blocked against rich 23.
    store.fights[18].status = STATUS_BLOCKED
    store.fights[18].blocked_at = 22
    store.fights[18].ripple_tip = None
    store.reconcile(cells, route=route)
    assert store.fights[18].status == STATUS_BLOCKED

    # Improve tip HP so projected/stripped check clears → reopen rippling.
    cells[4] = _row(22, 40, hp=96)
    store.reconcile(cells, route=route)
    assert store.fights[18].status == STATUS_RIPPLING
    assert store.fights[18].ripple_tip == 22


def test_ripple_store_on_install_and_block(tmp_path: Path) -> None:
    # Single fight 18→19. Ammo stays flat after the fight (no new cliffs);
    # beatability uses HP so equal-ammo tips can still quality_beats successors.
    cells = [_row(18, 70)] + [_row(i, 40, hp=80) for i in range(19, 26)]

    store = PayforwardRippleStore(tmp_path / "payforward_ripple.json")
    route = [
        {"checkpoint_id": "cp19_id", "items_gained": []},
        {"checkpoint_id": "cp20_id", "items_gained": []},
        {"checkpoint_id": "cp21_id", "items_gained": []},
        {"checkpoint_id": "cp22_id", "items_gained": []},
        {"checkpoint_id": "cp23_id", "items_gained": ["shotgun"]},
    ]
    store.reconcile(cells, route=route)
    assert list(store.fights) == [18]
    assert store.fights[18].status == STATUS_GRIND
    assert store.fights[18].stretch_end == 26

    def _advance(idx: int, *, hp: int = 96) -> None:
        cells[idx - 18] = _row(idx, 40, hp=hp)
        store.on_install(idx, cells, route=route)

    _advance(19)
    assert store.fights[18].status == STATUS_RIPPLING
    assert store.fights[18].ripple_tip == 19
    assert store.fights[18].sample_index() == 19

    _advance(20)
    _advance(21)
    assert store.fights[18].ripple_tip == 21
    assert list(store.fights) == [18]

    # Weak tip vs rich equal-HP plateau (ammo flat after 23 → still one fight).
    cells[4] = _row(22, 40, hp=40)
    for j, ci in enumerate(range(18, 26)):
        if ci >= 23:
            cells[j] = _row(ci, 200, hp=40)
    store.on_install(22, cells, route=route)
    assert store.fights[18].status == STATUS_BLOCKED
    assert store.fights[18].blocked_at == 22
    assert store.fights[18].sample_index() == 18  # budget back on fight


def test_sample_payforward_progression_mix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RE1_YAWN_PAYFORWARD_RIPPLE", "1")
    cells_dir = tmp_path / "states" / "yawn_rails" / "cells"
    cells = []
    # cp18 successor inefficient (overspend); frontier stays cp18.
    specs = [(0, 50), (5, 45), (18, 75), (19, 71), (20, 70)]
    for idx, ammo in specs:
        d = cells_dir / f"cp{idx:02d}"
        d.mkdir(parents=True)
        (d / "cell.State").write_bytes(b"x" * 100)
        (d / "cell.sidecar.json").write_text("{}", encoding="utf-8")
        extra = {}
        if idx == 18:
            extra["checkpoint_id"] = "l_passage_enter_108"
        cells.append(_row(idx, ammo, **extra))
    stage = {
        "route_id": "yawn_quest_v2",
        "route_path": "data/yawn_checkpoint_route.json",
        "route_steps": list(range(1, 40)),
        "legs_per_episode": 1,
        "cells_manifest": "states/yawn_rails/manifest.json",
    }
    by_idx = {int(r["checkpoint_index"]): r for r in cells}
    assert frontier_fight_index(by_idx, project_root=tmp_path) == 18
    counts: Counter[int] = Counter()
    rng = random.Random(0)
    for _ in range(6000):
        opts = sample_payforward_options(tmp_path, stage, cells, rng=rng)
        assert opts is not None
        assert opts["payforward_fight_progression"] is True
        counts[int(opts["route_start_index"]) - 1] += 1
    # 40% frontier cp18 + 60% uniform over 5 cells (no latest bias on cp20).
    assert counts[18] / 6000 > 0.30
    assert counts[0] / 6000 > 0.08
    assert counts[20] / 6000 < 0.25


def test_frontier_advances_when_fight_efficient(tmp_path: Path) -> None:
    target = fight_target_for_index(18)
    assert target is not None
    tip = _row(18, 75, checkpoint_id="l_passage_enter_108")
    good_succ = _row(19, 80)
    bad_succ = _row(19, 71)
    assert fight_efficiency_met(tip, good_succ, target, project_root=tmp_path)
    assert not fight_efficiency_met(tip, bad_succ, target, project_root=tmp_path)
    cells = [tip, good_succ, _row(26, 114, checkpoint_id="back_passage_10A")]
    by_idx = {int(r["checkpoint_index"]): r for r in cells}
    assert frontier_fight_index(by_idx, project_root=tmp_path) == 26


def test_sample_frontier_fight_options(tmp_path: Path) -> None:
    cells_dir = tmp_path / "states" / "yawn_rails" / "cells"
    cells = []
    for idx, ammo in ((18, 75), (19, 71), (26, 114)):
        d = cells_dir / f"cp{idx:02d}"
        d.mkdir(parents=True)
        (d / "cell.State").write_bytes(b"x" * 100)
        (d / "cell.sidecar.json").write_text("{}", encoding="utf-8")
        extra = {}
        if idx == 18:
            extra["checkpoint_id"] = "l_passage_enter_108"
        if idx == 26:
            extra["checkpoint_id"] = "back_passage_10A"
        cells.append(_row(idx, ammo, **extra))
    stage = {
        "route_steps": list(range(1, 60)),
        "legs_per_episode": 1,
    }
    from re1_rl.yawn_rails_payforward import sample_frontier_fight_options

    opts = sample_frontier_fight_options(tmp_path, stage, cells)
    assert opts is not None
    assert opts["reset_source"] == "route_cell_frontier_fight"
    assert opts["payforward_frontier_fight"] == 18
    assert opts["route_start_index"] == 19


def test_choose_progression_reset_index_bias(tmp_path: Path) -> None:
    cells = [_row(0, 40), _row(18, 75, checkpoint_id="l_passage_enter_108"), _row(19, 71)]
    counts: Counter[int] = Counter()
    rng = random.Random(1)
    for _ in range(5000):
        pick = choose_progression_reset_index(
            cells, rng, project_root=tmp_path, fight_bias=0.40
        )
        assert pick is not None
        counts[int(pick)] += 1
    assert counts[18] / 5000 > 0.25
    assert counts[0] / 5000 > 0.10


def test_new_fight_appears_on_reconcile(tmp_path: Path) -> None:
    store = PayforwardRippleStore(tmp_path / "pf.json")
    cells = [_row(18, 50), _row(19, 50), _row(20, 50)]
    store.reconcile(cells)
    assert store.fights == {}
    cells[1] = _row(19, 30)  # 18>19 fight appears
    store.reconcile(cells)
    assert list(store.fights) == [18]
    cells.append(_row(21, 10))  # 20>21 new fight
    cells[2] = _row(20, 40)
    store.reconcile(cells)
    assert sorted(store.fights) == [18, 20]


def test_quality_beats_sanity() -> None:
    assert quality_beats((96, 56, 1, 10, 1, 0, 0), (80, 103, 2, 11, 1, 0, 0))


def test_choose_cell_does_not_persist(tmp_path: Path) -> None:
    cells = [_row(18, 70), _row(19, 40), _row(20, 40)]
    path = tmp_path / "payforward_ripple.json"
    store = PayforwardRippleStore(path, project_root=tmp_path)
    pick = store.choose_cell_index(cells, random.Random(0))
    assert pick in {18, 19, 20}
    assert not path.is_file()  # sample path must not write


def test_save_swallows_replace_errors(tmp_path: Path, monkeypatch) -> None:
    import os

    from re1_rl.yawn_rails_payforward import FightRuntime

    path = tmp_path / "payforward_ripple.json"
    store = PayforwardRippleStore(path)
    store.fights[18] = FightRuntime(18, 20, STATUS_GRIND)

    def _boom(*_a, **_k):
        raise PermissionError("simulated")

    monkeypatch.setattr(os, "replace", _boom)
    store.save()  # must not raise
    assert not path.is_file()


def test_cp26_bogus_cliff_rejected_without_kills() -> None:
    tip = _row(
        26,
        114,
        checkpoint_id="back_passage_10A",
    )
    succ = _row(
        27,
        108,
        checkpoint_id="crow_gallery_enter_117",
    )
    assert not fight_leg_valid(tip, succ)
    assert [f.fight_index for f in discover_fights([tip, succ])] == []


def test_cp26_valid_with_two_zombie_spend() -> None:
    tip = _row(26, 114, checkpoint_id="back_passage_10A")
    succ = _row(27, 100, checkpoint_id="crow_gallery_enter_117")
    # Legacy sidecars without leg_kills_by_room: ammo floor only (2×7 beretta).
    assert fight_leg_valid(tip, succ)
    assert [f.fight_index for f in discover_fights([tip, succ])] == [26]


def test_cp40_one_zombie_fight() -> None:
    tip = _row(40, 60, checkpoint_id="east_stairs_101")
    succ = _row(41, 53, checkpoint_id="storeroom_enter_118")
    assert fight_leg_valid(tip, succ)
    assert [f.fight_index for f in discover_fights([tip, succ])] == [40]
    bogus = _row(41, 58, checkpoint_id="storeroom_enter_118")
    assert not fight_leg_valid(tip, bogus)


def test_cp40_requires_kill_when_sidecar_tracks_them(tmp_path: Path) -> None:
    sidecar = tmp_path / "cp41.sidecar.json"
    sidecar.write_text(
        '{"schema_version":1,"progress":{"leg_kills_by_room":{"10B":0}}}',
        encoding="utf-8",
    )
    tip = _row(40, 60, checkpoint_id="east_stairs_101")
    succ = _row(41, 53, checkpoint_id="storeroom_enter_118")
    succ["sidecar_path"] = "cp41.sidecar.json"
    assert not fight_leg_valid(tip, succ, project_root=tmp_path)


def test_cp40_valid_with_kill_in_sidecar(tmp_path: Path) -> None:
    sidecar = tmp_path / "cp41.sidecar.json"
    sidecar.write_text(
        '{"schema_version":1,"progress":{"leg_kills_by_room":{"10B":1}}}',
        encoding="utf-8",
    )
    tip = _row(40, 60, checkpoint_id="east_stairs_101")
    succ = _row(41, 53, checkpoint_id="storeroom_enter_118")
    succ["sidecar_path"] = "cp41.sidecar.json"
    assert fight_leg_valid(tip, succ, project_root=tmp_path)


def test_cp26_requires_kills_when_sidecar_tracks_them(tmp_path: Path) -> None:
    sidecar = tmp_path / "cp27.sidecar.json"
    sidecar.write_text(
        '{"schema_version":1,"progress":{"leg_kills_by_room":{"10A":1}}}',
        encoding="utf-8",
    )
    tip = _row(26, 114, checkpoint_id="back_passage_10A")
    succ = _row(27, 100, checkpoint_id="crow_gallery_enter_117")
    succ["sidecar_path"] = "cp27.sidecar.json"
    assert not fight_leg_valid(tip, succ, project_root=tmp_path)


def test_cp26_valid_with_knife_kills_in_sidecar(tmp_path: Path) -> None:
    sidecar = tmp_path / "cp27.sidecar.json"
    sidecar.write_text(
        '{"schema_version":1,"progress":{"leg_kills_by_room":{"10A":2}}}',
        encoding="utf-8",
    )
    tip = _row(26, 114, checkpoint_id="back_passage_10A")
    succ = _row(
        27,
        114,
        checkpoint_id="crow_gallery_enter_117",
    )
    succ["sidecar_path"] = "cp27.sidecar.json"
    assert fight_leg_valid(tip, succ, project_root=tmp_path)
    assert [f.fight_index for f in discover_fights([tip, succ], project_root=tmp_path)] == []


def test_navigate_only_10a_returns_never_fight() -> None:
    for cid in (
        "back_passage_return_10A",
        "back_passage_post_crest_10A",
        "east_stairs_101_post_storeroom",
    ):
        tip = _row(36, 100, checkpoint_id=cid)
        succ = _row(37, 86, checkpoint_id="next_room")
        assert not fight_leg_valid(tip, succ)
        assert discover_fights([tip, succ]) == []


def test_cp37_requires_dog_kill_when_sidecar_tracks_them(tmp_path: Path) -> None:
    sidecar = tmp_path / "cp38.sidecar.json"
    sidecar.write_text(
        '{"schema_version":1,"progress":{"leg_kills_by_room":{"11A":0}}}',
        encoding="utf-8",
    )
    tip = _row(37, 71, checkpoint_id="courtyard_enter_11A")
    succ = _row(38, 66, checkpoint_id="crest_gate_11A")
    succ["sidecar_path"] = "cp38.sidecar.json"
    assert not fight_leg_valid(tip, succ, project_root=tmp_path)


def test_cp37_valid_with_dog_kill_in_sidecar(tmp_path: Path) -> None:
    sidecar = tmp_path / "cp38.sidecar.json"
    sidecar.write_text(
        '{"schema_version":1,"progress":{"leg_kills_by_room":{"11A":1}}}',
        encoding="utf-8",
    )
    tip = _row(37, 71, checkpoint_id="courtyard_enter_11A")
    succ = _row(38, 66, checkpoint_id="crest_gate_11A")
    succ["sidecar_path"] = "cp38.sidecar.json"
    assert fight_leg_valid(tip, succ, project_root=tmp_path)


def test_cp45_requires_kills_when_sidecar_tracks_them(tmp_path: Path) -> None:
    sidecar = tmp_path / "cp46.sidecar.json"
    sidecar.write_text(
        '{"schema_version":1,"progress":{"leg_kills_by_room":{"204":1}}}',
        encoding="utf-8",
    )
    tip = _row(45, 114, checkpoint_id="c_passage_204")
    succ = _row(46, 100, checkpoint_id="upper_hall_enter_203")
    succ["sidecar_path"] = "cp46.sidecar.json"
    assert not fight_leg_valid(tip, succ, project_root=tmp_path)


def test_cp45_valid_with_two_kills_in_sidecar(tmp_path: Path) -> None:
    sidecar = tmp_path / "cp46.sidecar.json"
    sidecar.write_text(
        '{"schema_version":1,"progress":{"leg_kills_by_room":{"204":2}}}',
        encoding="utf-8",
    )
    tip = _row(45, 114, checkpoint_id="c_passage_204")
    succ = _row(46, 100, checkpoint_id="upper_hall_enter_203")
    succ["sidecar_path"] = "cp46.sidecar.json"
    assert fight_leg_valid(tip, succ, project_root=tmp_path)
