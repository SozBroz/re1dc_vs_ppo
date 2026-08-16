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
    HEADING_RESTORE_VERSION,
    LegReplayBuffer,
    build_leg_replay_payload,
    new_leg_replay_buffer,
    policy_leg_frames_from_tape,
    reclassify_contaminated_async_skip_tape,
    should_write_leg_replay,
    tape_is_combat,
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
    assert buf.policy_leg_frames == 80
    assert buf.skip_leg_frames == 0
    actions, frames = buf.as_lists()
    assert actions == [7, 1, 7]
    assert frames == [18, 8, 54]


def test_buffer_frame_channels_split_policy_from_skip() -> None:
    buf = new_leg_replay_buffer()
    buf.append(9, policy_frames=18)
    buf.append(0, policy_frames=0, skip_frames=1200, reward_only_frames=0)
    buf.append(0, policy_frames=0, skip_frames=0, reward_only_frames=8)
    assert buf.policy_leg_frames == 18
    assert buf.skip_leg_frames == 1200
    assert buf.reward_only_leg_frames == 8
    assert buf.leg_frames == 1218
    actions, policy, skip, reward_only = buf.as_channel_lists()
    assert actions == [9, 0, 0]
    assert policy == [18, 0, 0]
    assert skip == [0, 1200, 0]
    assert reward_only == [0, 0, 8]


def test_buffer_append_reward_pads_and_ignores_extra() -> None:
    buf = new_leg_replay_buffer()
    buf.append(9, 18)
    buf.append(1, 8)
    buf.append(5, 8)
    buf.append_reward(4.0, {"new_room": 4.0, "step": 0.0})
    rewards, events = buf.aligned_rewards()
    assert rewards == [0.0, 0.0, 4.0]
    assert events == [{}, {}, {"new_room": 4.0}]
    buf.append_reward(1.2, {"new_cutscene": 1.2})
    rewards, events = buf.aligned_rewards()
    assert rewards == [0.0, 0.0, 4.0]
    assert events[2] == {"new_room": 4.0}


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
    env._route_start_index = 0
    env._leg_replay = buf
    assert should_write_leg_replay(env, 0)
    env._route_start_index = 1
    assert not should_write_leg_replay(env, 0)


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
    assert payload["policy_frames_per_step"] == [18, 8]
    assert payload["skip_frames_per_step"] == [0, 0]
    assert payload["leg_frames"] == 26
    assert payload["policy_leg_frames"] == 26
    assert payload["contract"]["frame_skip"] == 8
    assert payload["contract"]["async_cutscene_skip"] is True
    assert payload["contract"]["frame_channels"] is True
    assert payload["contract"]["joypad_tape"] is False
    assert payload["contract"]["heading_restore"] == HEADING_RESTORE_VERSION
    assert payload["contract"]["combat_leg"] is True
    assert "joypad_bits" not in payload
    assert payload["end"]["quality"][7] == -26
    assert "rewards" not in payload
    dest = tmp_path / CELL_REPLAY_NAME
    write_leg_replay_json(dest, payload)
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded["leg_steps"] == 2


def test_payload_uses_policy_frames_for_quality_speed(tmp_path: Path) -> None:
    buf = LegReplayBuffer()
    buf.append(9, policy_frames=18)
    buf.append(0, policy_frames=0, skip_frames=1200)
    buf.append(0, reward_only_frames=8)
    env = SimpleNamespace(
        _route_start_index=0,
        _leg_replay=buf,
        _async_cutscene_skip=True,
        frame_skip=8,
        project_root=tmp_path,
        action_space=SimpleNamespace(n=45),
        _progress=None,
        _reset_options={},
        _stage={"init_savestate": "missing.State"},
    )
    quality = attach_leg_frames((96, 45, 100, 4, 1, 0, -30), buf.policy_leg_frames)
    payload = build_leg_replay_payload(
        env,
        completed_index=0,
        completed_id="emblem_105",
        settled=False,
        live_state={"room_id": "105", "x": 1, "z": 2, "facing": 3, "hp": 96},
        quality=quality,
        to_state_sha256="to",
    )
    assert payload is not None
    assert payload["policy_leg_frames"] == 18
    assert payload["skip_leg_frames"] == 1200
    assert payload["reward_only_leg_frames"] == 8
    assert payload["leg_frames"] == 1218
    assert payload["end"]["quality"][7] == -18


def test_reclassify_contaminated_async_skip_tape_splits_chunk_and_min_bills() -> None:
    tape = {
        "schema_version": 1,
        "actions": [9, 5, 0, 0, 0, 1],
        "emu_frames_per_step": [18, 8, 1200, 8, 25, 8],
        "leg_frames": 1267,
        "contract": {"frame_skip": 8, "async_cutscene_skip": True},
        "end": {"quality": [96, 45, 100, 4, 1, 0, -30, -1267]},
    }
    fixed = reclassify_contaminated_async_skip_tape(tape, frame_skip=8, skip_chunk=600)
    assert fixed["policy_frames_per_step"] == [18, 8, 0, 0, 0, 8]
    assert fixed["skip_frames_per_step"] == [0, 0, 1200, 0, 25, 0]
    assert fixed["reward_only_frames_per_step"] == [0, 0, 0, 8, 0, 0]
    assert fixed["policy_leg_frames"] == 34
    assert fixed["skip_leg_frames"] == 1225
    assert fixed["reward_only_leg_frames"] == 8
    assert fixed["leg_frames"] == 1259
    assert fixed["end"]["quality"][7] == -34
    assert policy_leg_frames_from_tape(fixed) == 34
    assert policy_leg_frames_from_tape(tape) == 1267


def test_fresh_cp00_tape_uses_init_savestate(tmp_path: Path) -> None:
    import hashlib

    init = tmp_path / "states" / "jill_control_fresh.State"
    init.parent.mkdir(parents=True)
    init.write_bytes(b"FRESH-INIT")
    buf = LegReplayBuffer()
    buf.append(9, 8)
    buf.append(1, 8)
    env = SimpleNamespace(
        _route_start_index=0,
        _leg_replay=buf,
        _async_cutscene_skip=True,
        frame_skip=8,
        project_root=tmp_path,
        action_space=SimpleNamespace(n=45),
        _progress=None,
        _reset_options={},
        _stage={"init_savestate": "states/jill_control_fresh.State"},
    )
    quality = attach_leg_frames((96, 45, 100, 4, 1, 0, -30), 16)
    payload = build_leg_replay_payload(
        env,
        completed_index=0,
        completed_id="emblem_105",
        settled=True,
        live_state={
            "room_id": "105",
            "x": 1,
            "z": 2,
            "facing": 3,
            "hp": 96,
            "in_control": True,
        },
        quality=quality,
        to_state_sha256="to-sha",
    )
    assert payload is not None
    assert payload["from_checkpoint_index"] == -1
    assert payload["from_checkpoint_id"] == "route_initial"
    assert payload["from_state_sha256"] == hashlib.sha256(b"FRESH-INIT").hexdigest()
    assert payload["to_checkpoint_index"] == 0
    assert payload["to_checkpoint_id"] == "emblem_105"
    assert payload["end"]["quality"][7] == -16
    assert payload["actions"] == [9, 1]


def test_tape_is_combat_from_kills_or_attacks() -> None:
    assert tape_is_combat({"end": {"leg_kills_by_room": {"11A": 1}}})
    assert tape_is_combat({"actions": [1, 7, 1]})
    assert tape_is_combat({"contract": {"combat_leg": True}})
    assert not tape_is_combat({"actions": [1, 2, 9], "end": {"leg_kills_by_room": {}}})


def test_payload_from_sha_hashes_predecessor_state(tmp_path: Path) -> None:
    import hashlib

    pred = tmp_path / "pred"
    pred.mkdir()
    state = pred / CELL_STATE_NAME
    state.write_bytes(b"PRED-STATE")
    buf = LegReplayBuffer()
    buf.append(1, 8)
    env = SimpleNamespace(
        _route_start_index=18,
        _leg_replay=buf,
        _async_cutscene_skip=True,
        frame_skip=8,
        project_root=tmp_path,
        action_space=SimpleNamespace(n=45),
        _progress=None,
        _reset_options={
            "pb_bundle": {
                "state_path": str(state),
                "sidecar_path": str(pred / "sidecar.json"),
            }
        },
    )
    payload = build_leg_replay_payload(
        env,
        completed_index=18,
        completed_id="l_passage_enter_108",
        settled=True,
        live_state={"room_id": "108", "x": 1, "z": 2, "facing": 3, "hp": 96},
        quality=(96, 40, 0, 8, 1, 0, 0, -8),
        to_state_sha256="abc",
    )
    assert payload is not None
    assert payload["from_state_sha256"] == hashlib.sha256(b"PRED-STATE").hexdigest()
    assert payload["contract"]["combat_leg"] is False


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
    store = json.loads((yr / "store.json").read_text(encoding="utf-8"))
    assert "18" in store["cells"]
    assert store["cells"]["18"]["checkpoint_id"] == "l_passage_enter_108"


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


def test_payload_includes_stepwise_rewards(tmp_path: Path) -> None:
    buf = LegReplayBuffer()
    buf.append(9, 18)
    buf.append_reward(0.0, {})
    buf.append(1, 8)
    buf.append_reward(4.0, {"new_room": 4.0})
    buf.append(5, 8)
    buf.append_reward(13.2, {"new_cutscene": 1.2, "checkpoint_success": 12.0})
    env = SimpleNamespace(
        _route_start_index=0,
        _leg_replay=buf,
        _async_cutscene_skip=True,
        frame_skip=8,
        project_root=tmp_path,
        action_space=SimpleNamespace(n=45),
        _progress=None,
        _reset_options={},
        _stage={"init_savestate": "missing.State"},
    )
    payload = build_leg_replay_payload(
        env,
        completed_index=0,
        completed_id="emblem_105",
        settled=False,
        live_state={"room_id": "105", "x": 1, "z": 2, "facing": 3, "hp": 96},
        quality=(96, 45, 100, 4, 1, 0, -30, -34),
        to_state_sha256="to",
    )
    assert payload is not None
    assert payload["rewards"] == [0.0, 4.0, 13.2]
    assert payload["reward_total"] == 17.2
    assert payload["reward_events"] == [
        [1, {"new_room": 4.0}],
        [2, {"new_cutscene": 1.2, "checkpoint_success": 12.0}],
    ]
    assert payload["reward_by_channel"] == {
        "new_room": 4.0,
        "new_cutscene": 1.2,
        "checkpoint_success": 12.0,
    }


def test_joypad_replay_keeps_engine_patches_and_skip_turbo() -> None:
    """cp13 stayed in 10F when joypad replay omitted door-skip/turbo writes."""
    root = Path(__file__).resolve().parents[1]
    lua = (root / "lua" / "re1_client.lua").read_text(encoding="utf-8")
    replay = (root / "scripts" / "replay_leg.py").read_text(encoding="utf-8")
    assert "apply_patches(tape_skip_force_turbo())" in lua
    assert "TAPE_SCENE_FLAG = 0x800C3002" in lua
    assert "turbo_patches=True" in replay


def test_normalize_pads_missing_speed_with_sentinel() -> None:
    q = normalize_quality((96, 40, 0, 8, 1, 0, 0))
    assert len(q) == 8
    assert q[7] == -LEG_FRAMES_SENTINEL
    assert normalize_quality(None)[7] == -LEG_FRAMES_SENTINEL
