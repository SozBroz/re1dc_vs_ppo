"""Leg replay tape + yawn quality speed sentinel."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from re1_rl.go_explore_archive import (
    LEG_FRAMES_SENTINEL,
    attach_leg_frames,
    normalize_quality,
    quality_beats,
)
from re1_rl.go_explore_capture import quality_replace_significant
from re1_rl.go_explore_merge import CELL_REPLAY_NAME
from re1_rl.leg_replay import (
    LegReplayBuffer,
    build_leg_replay_payload,
    new_leg_replay_buffer,
    should_write_leg_replay,
    write_leg_replay_json,
)
from re1_rl.yawn_cell_quality import seed_cell_leg_frames_sentinel, seed_leg_frames_sentinel
from re1_rl.yawn_rails_sync import (
    CELL_SIDECAR_NAME,
    CELL_STATE_NAME,
    try_install_yawn_cell,
    yawn_rails_root,
)


def test_buffer_leg_frames_sum() -> None:
    buf = new_leg_replay_buffer()
    buf.append(7, 18)
    buf.append(1, 8)
    buf.append(7, 54)
    assert len(buf) == 3
    assert buf.leg_frames == 80
    actions, frames = buf.as_lists()
    assert actions == [7, 1, 7]
    assert frames == [18, 8, 54]


def test_single_leg_gate() -> None:
    buf = LegReplayBuffer()
    buf.append(1, 8)
    env = SimpleNamespace(_route_start_index=18, _leg_replay=buf)
    assert should_write_leg_replay(env, 18)
    env._route_start_index = 10
    assert not should_write_leg_replay(env, 18)
    env._route_start_index = 18
    env._leg_replay = LegReplayBuffer()
    assert not should_write_leg_replay(env, 18)


def test_payload_schema_and_write(tmp_path: Path) -> None:
    buf = LegReplayBuffer()
    buf.append(7, 18)
    buf.append(1, 8)
    env = SimpleNamespace(
        _route_start_index=18,
        _leg_replay=buf,
        _async_cutscene_skip=True,
        frame_skip=8,
        project_root=tmp_path,
        action_space=SimpleNamespace(n=45),
        _progress=SimpleNamespace(leg_kills_for_capture=lambda: {"108": 2}),
        _reset_options={"pb_bundle": {}},
    )
    quality = attach_leg_frames((96, 40, 0, 8, 1, 0, 0), 26)
    payload = build_leg_replay_payload(
        env,
        completed_index=18,
        completed_id="l_passage_enter_108",
        settled=True,
        live_state={"room_id": "108", "x": 1, "z": 2, "facing": 3, "hp": 96, "in_control": True},
        quality=quality,
        to_state_sha256="abc",
    )
    assert payload is not None
    assert payload["actions"] == [7, 1]
    assert payload["emu_frames_per_step"] == [18, 8]
    assert payload["leg_frames"] == 26
    assert payload["contract"]["frame_skip"] == 8
    assert payload["contract"]["async_cutscene_skip"] is True
    assert payload["contract"]["joypad_tape"] is False
    assert "joypad_bits" not in payload
    assert payload["end"]["quality"][7] == -26
    dest = tmp_path / CELL_REPLAY_NAME
    write_leg_replay_json(dest, payload)
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded["leg_steps"] == 2


def test_try_install_copies_replay_tape(tmp_path: Path) -> None:
    yr = yawn_rails_root(tmp_path)
    staging = yr / ".staging" / "cp18"
    staging.mkdir(parents=True)
    (staging / CELL_STATE_NAME).write_bytes(b"STATE")
    (staging / CELL_SIDECAR_NAME).write_text("{}", encoding="utf-8")
    (staging / CELL_REPLAY_NAME).write_text(
        json.dumps({"schema_version": 1, "actions": [1]}) + "\n",
        encoding="utf-8",
    )
    q = attach_leg_frames((96, 40, 0, 8, 1, 0, 0), 80)
    ok = try_install_yawn_cell(
        tmp_path,
        checkpoint_index=18,
        staged_dir=staging,
        quality=q,
        row={
            "checkpoint_index": 18,
            "checkpoint_id": "l_passage_enter_108",
            "room_id": "108",
            "quality": list(q),
        },
        holder="test",
    )
    assert ok
    installed = yr / "cells" / "cp18" / CELL_REPLAY_NAME
    assert installed.is_file()
    assert json.loads(installed.read_text(encoding="utf-8"))["actions"] == [1]


def test_seed_leg_frames_sentinel_overwrites_speed(tmp_path: Path) -> None:
    yr = yawn_rails_root(tmp_path)
    cell = yr / "cells" / "cp18"
    cell.mkdir(parents=True)
    (cell / "meta.json").write_text(
        json.dumps({"checkpoint_index": 18, "quality": [96, 40, 0, 8, 1, 0, 0, -12]}),
        encoding="utf-8",
    )
    man = {
        "schema_version": 1,
        "cells": [
            {"checkpoint_index": 18, "quality": [96, 40, 0, 8, 1, 0, 0]},
        ],
    }
    (yr / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    n = seed_leg_frames_sentinel(tmp_path)
    assert n == 1
    row = json.loads((yr / "manifest.json").read_text(encoding="utf-8"))["cells"][0]
    assert row["quality"][7] == -LEG_FRAMES_SENTINEL
    meta = json.loads((cell / "meta.json").read_text(encoding="utf-8"))
    assert meta["quality"][7] == -LEG_FRAMES_SENTINEL
    new_q = attach_leg_frames(row["quality"][:7], 400)
    assert quality_beats(new_q, row["quality"])
    assert quality_replace_significant(new_q, row["quality"])


def test_seed_cell_leg_frames_sentinel_only_touches_that_index(tmp_path: Path) -> None:
    yr = yawn_rails_root(tmp_path)
    for idx, speed in ((18, -12), (19, -7656)):
        cell = yr / "cells" / f"cp{idx:02d}"
        cell.mkdir(parents=True)
        (cell / "meta.json").write_text(
            json.dumps({"checkpoint_index": idx, "quality": [96, 79, 0, 9, 1, 0, -30, speed]}),
            encoding="utf-8",
        )
    man = {
        "schema_version": 1,
        "cells": [
            {"checkpoint_index": 18, "quality": [96, 79, 0, 9, 1, 0, -30, -12]},
            {"checkpoint_index": 19, "quality": [96, 79, 0, 9, 1, 0, -30, -7656]},
        ],
    }
    (yr / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    assert seed_cell_leg_frames_sentinel(tmp_path, 19) is True
    rows = json.loads((yr / "manifest.json").read_text(encoding="utf-8"))["cells"]
    assert rows[0]["quality"][7] == -12
    assert rows[1]["quality"][7] == -LEG_FRAMES_SENTINEL
    meta19 = json.loads((yr / "cells" / "cp19" / "meta.json").read_text(encoding="utf-8"))
    assert meta19["quality"][7] == -LEG_FRAMES_SENTINEL
    meta18 = json.loads((yr / "cells" / "cp18" / "meta.json").read_text(encoding="utf-8"))
    assert meta18["quality"][7] == -12


def test_payload_includes_joypad_bits_when_bridge_dumps() -> None:
    buf = LegReplayBuffer()
    buf.append(1, 8)
    env = SimpleNamespace(
        _route_start_index=19,
        _leg_replay=buf,
        _async_cutscene_skip=False,
        frame_skip=8,
        project_root=".",
        action_space=SimpleNamespace(n=45),
        _progress=SimpleNamespace(leg_kills_for_capture=lambda: {}),
        _reset_options={"pb_bundle": {}},
        bridge=SimpleNamespace(tape_dump=lambda: [1, 0, 4]),
    )
    quality = attach_leg_frames((96, 40, 0, 8, 1, 0, 0), 8)
    payload = build_leg_replay_payload(
        env,
        completed_index=19,
        completed_id="ammo_108",
        settled=False,
        live_state={"room_id": "108", "x": 1, "z": 2, "facing": 3, "hp": 96, "in_control": True},
        quality=quality,
        to_state_sha256="abc",
    )
    assert payload is not None
    assert payload["contract"]["joypad_tape"] is True
    assert payload["joypad_bits"] == [1, 0, 4]
    assert payload["joypad_frames"] == 3


def test_normalize_pads_missing_speed_with_sentinel() -> None:
    q = normalize_quality((96, 40, 0, 8, 1, 0, 0))
    assert len(q) == 8
    assert q[7] == -LEG_FRAMES_SENTINEL
    assert normalize_quality(None)[7] == -LEG_FRAMES_SENTINEL
