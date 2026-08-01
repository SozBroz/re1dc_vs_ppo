from __future__ import annotations

import base64
import json
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import re1_rl.memlog_dashboard.server as dashboard_server
from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM
from re1_rl.episode_history import ACQUISITION_LOG_DIM, ROOM_HISTORY_DIM
from re1_rl.item_affordances import AFFORDANCES_DIM
from re1_rl.key_items import KEYS_HELD_DIM
from re1_rl.maps_files import MAPS_FILES_DIM
from re1_rl.memlog_dashboard.server import (
    DashboardConfig,
    DashboardHTTPServer,
    DashboardService,
    OwnedMemlogProcess,
    owned_tree_kill_command,
)
from re1_rl.milestone_features import MILESTONE_DIM
from re1_rl.obs_encoder import (
    BOX_DIM,
    GOAL_DIM,
    INVENTORY_OBS_DIM,
    PROPRIO_DIM,
    ROOM_VISITED_DIM,
)
from re1_rl.obs_explain import (
    action_presentation,
    decode_frame_planes,
    explain_observation,
)
from re1_rl.room_signature import ENEMY_ROSTER_DIM
from re1_rl.spatial_encoder import SPATIAL_DIM
from re1_rl.weapon_damage import LAST_ATTACK_DIM, WEAPON_CARD_DIM
from re1_rl.world_state_encoder import WORLD_STATE_DIM


ACTION_COUNT = 45
CANONICAL_MOVEMENT_ATTACK_SEQUENCE = [
    "run_forward",
    "attack_up",
    "attack",
    "attack_down",
]


def _canonical_action_names() -> list[str]:
    names = [f"action_{i}" for i in range(ACTION_COUNT)]
    names[5:9] = CANONICAL_MOVEMENT_ATTACK_SEQUENCE
    return names


def _frame(value: int = 0) -> dict:
    raw = bytes([value]) * (4 * 63 * 84)
    return {
        "base64": base64.b64encode(raw).decode("ascii"),
        "shape": [4, 63, 84],
        "dtype": "uint8",
    }


def _all_obs() -> dict:
    return {
        "frame": _frame(),
        "proprio": [0.0] * PROPRIO_DIM,
        "goal": [0.0] * GOAL_DIM,
        "spatial": [0.0] * SPATIAL_DIM,
        "visited": [[[0.0] for _ in range(16)] for _ in range(16)],
        "rooms_visited": [0.0] * ROOM_VISITED_DIM,
        "box": [0.0] * BOX_DIM,
        "inventory": [0.0] * INVENTORY_OBS_DIM,
        "weapon_card": [0.0] * WEAPON_CARD_DIM,
        "last_attack": [0.0] * LAST_ATTACK_DIM,
        "history": [0.0] * ROOM_HISTORY_DIM,
        "acquisitions": [0.0] * ACQUISITION_LOG_DIM,
        "room_enemies": [0.0] * ENEMY_ROSTER_DIM,
        "keys_held": [0.0] * KEYS_HELD_DIM,
        "affordances": [0.0] * AFFORDANCES_DIM,
        "world_state": [0.0] * WORLD_STATE_DIM,
        "cutscene_ledger": [0.0] * CUTSCENE_LEDGER_DIM,
        "milestones": [0.0] * MILESTONE_DIM,
        "maps_files": [0.0] * MAPS_FILES_DIM,
    }


def _write_snapshot(root: Path, **updates) -> dict:
    payload = {
        "run_id": "run-a",
        "seq": 7,
        "time": 100.0,
        "obs": _all_obs(),
        "legal_mask": [True] * ACTION_COUNT,
        "action": {
            "index": 2,
            "name": "back",
            "raw_logits": list(range(ACTION_COUNT)),
            "masked_probs": [0.0] * (ACTION_COUNT - 1) + [1.0],
            "value": 1.25,
            "logprob": -2.0,
        },
        "reward": 0.5,
        "reward_breakdown": {"step": -0.001, "new_room": 0.5, "softlock": -0.2},
    }
    payload.update(updates)
    path = root / "data" / "memlog" / "latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_every_current_observation_key_is_represented() -> None:
    obs = _all_obs()
    obs["future_sensor"] = [4, 5]
    explained = explain_observation(obs)
    assert set(explained) == set(obs)
    assert explained["goal"]["note"].startswith("Route/compass fields are zeroed")
    assert "active Doc04 extractor omits it" in explained["affordances"]["note"]
    assert explained["future_sensor"]["rows"][1]["name"] == "future_sensor[1]"
    assert len(explained["world_state"]["rows"]) == WORLD_STATE_DIM


def test_frame_decode_validates_shape_dtype_and_bytes() -> None:
    decoded = decode_frame_planes(_frame(127))
    assert decoded["plane_count"] == 4
    assert decoded["plane_shape"] == [63, 84]
    assert decoded["labels"] == ["oldest", "older", "newer", "newest"]
    with pytest.raises(ValueError, match="shape must be"):
        decode_frame_planes({**_frame(), "shape": [4, 84, 63]})
    with pytest.raises(ValueError, match="expected"):
        decode_frame_planes({
            "base64": base64.b64encode(b"short").decode("ascii"),
            "shape": [4, 63, 84],
        })


def test_control_is_atomic_preserves_fields_and_rejects_wrong_run(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    service = DashboardService(DashboardConfig(root=tmp_path), clock=lambda: 101.0)
    first = service.control("pause", {"run_id": "run-a"})
    assert first["paused"] is True
    second = service.control("speed", {"run_id": "run-a", "speed_pct": 1600})
    assert second["paused"] is True
    assert second["speed_pct"] == 1600
    assert json.loads(service.control_path.read_text())["run_id"] == "run-a"
    assert not list(service.control_path.parent.glob("*.tmp"))
    with pytest.raises(ValueError, match="run_id mismatch"):
        service.control("resume", {"run_id": "old-run"})
    with pytest.raises(ValueError, match="run_id is required"):
        service.control("pause", {})


def test_reward_events_filter_step_softlock_zero_and_bad_lines(tmp_path: Path) -> None:
    events_path = tmp_path / "data" / "memlog" / "reward_events.jsonl"
    events_path.parent.mkdir(parents=True)
    lines = [
        {"name": "step", "reward": -0.001},
        {"name": "softlock", "reward": -0.2},
        {"name": "new_room", "reward": 0.5},
        {"name": "zero", "reward": 0},
        {"reward_breakdown": {"step": -0.01, "pickup": 0.25, "contempt": -1}},
    ]
    events_path.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\nnot-json\n",
        encoding="utf-8",
    )
    result = DashboardService(DashboardConfig(root=tmp_path)).events()
    assert len(result["events"]) == 2
    assert result["cumulative_reward"] == pytest.approx(0.75)
    assert [e["display_reward"] for e in result["events"]] == [0.5, 0.25]


def test_latest_marks_stale_and_builds_presentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dashboard_server, "ACTION_NAMES", _canonical_action_names())
    _write_snapshot(tmp_path, time=90.0)
    service = DashboardService(
        DashboardConfig(root=tmp_path, stale_after_s=5.0),
        clock=lambda: 100.0,
    )
    latest = service.latest()
    assert latest["stale"] is True
    assert latest["age_s"] == 10.0
    assert latest["snapshot"]["reward_events_filtered"] == {"new_room": 0.5}
    rows = latest["snapshot"]["action_presentation"]["rows"]
    assert len(rows) == ACTION_COUNT
    assert [row["name"] for row in rows[5:9]] == CANONICAL_MOVEMENT_ATTACK_SEQUENCE


def test_producer_nested_schema_is_decoded(tmp_path: Path) -> None:
    encoded = lambda values: {"encoding": "json", "dtype": "float32",
                              "shape": [len(values)], "data": values}
    payload = {
        "run_id": "nested-run",
        "heartbeat_unix_s": 100.0,
        "horizon_step": 3,
        "n_steps": 128,
        "control": {"run_id": "nested-run", "speed_pct": 6400, "paused": False},
        "pre_step": {
            "observation": {
                "frame": {**_frame(), "encoding": "base64"},
                "proprio": encoded([0.0] * PROPRIO_DIM),
            },
            "action_mask": encoded([1.0] * ACTION_COUNT),
            "action": 4,
            "raw_logits": encoded([float(i) for i in range(ACTION_COUNT)]),
            "masked_probs": encoded([1 / ACTION_COUNT] * ACTION_COUNT),
            "value": 2.0,
            "logprob": -1.0,
            "policy_version": 9,
        },
        "post_step": {
            "reward": 0.25,
            "reward_breakdown": {"new_room": 0.25},
            "done": False,
            "info": {},
        },
    }
    path = tmp_path / "data" / "memlog" / "latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    latest = DashboardService(
        DashboardConfig(root=tmp_path), clock=lambda: 101.0
    ).latest()["snapshot"]
    assert latest["observation_explained"]["frame"]["frame"]["plane_count"] == 4
    assert len(latest["observation_explained"]["proprio"]["rows"]) == PROPRIO_DIM
    assert len(latest["action_presentation"]["rows"]) == ACTION_COUNT
    assert latest["action_presentation"]["chosen_index"] == 4
    assert latest["action_presentation"]["policy_version"] == 9


def test_action_rows_include_mask_choice_entropy_and_rank() -> None:
    mask = [True] * ACTION_COUNT
    mask[3] = False
    probs = [0.0] * ACTION_COUNT
    probs[4], probs[2] = 0.7, 0.3
    result = action_presentation(
        {
            "policy_version": 12,
            "legal_mask": mask,
            "action": {
                "index": 2,
                "raw_logits": [float(i) for i in range(ACTION_COUNT)],
                "masked_probs": probs,
            },
        },
        _canonical_action_names(),
    )
    assert len(result["rows"]) == ACTION_COUNT
    assert [row["name"] for row in result["rows"][5:9]] == (
        CANONICAL_MOVEMENT_ATTACK_SEQUENCE
    )
    assert result["rows"][2]["chosen"] is True
    assert result["rows"][3]["masked"] is True
    assert result["chosen_rank"] == 2
    assert result["entropy"] > 0


class _FakeProcess:
    pid = 4321

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


def test_process_ownership_helpers_do_not_name_kill(tmp_path: Path) -> None:
    launcher = tmp_path / "run_memlog_agent.cmd"
    launcher.write_text("@exit /b 0\n", encoding="utf-8")
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return _FakeProcess()

    owned = OwnedMemlogProcess(
        DashboardConfig(root=tmp_path, launcher=launcher),
        popen=fake_popen,
    )
    status = owned.start("owned-run")
    assert status["pid"] == 4321
    assert status["run_id"] == "owned-run"
    assert calls[0][1]["env"]["RE1_MEMLOG_RUN_ID"] == "owned-run"
    assert owned_tree_kill_command(4321) == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert all("EmuHawk" not in token and "python" not in token
               for token in owned_tree_kill_command(4321))


def test_dashboard_keeps_all_fields_open_and_uses_page_scroll_only() -> None:
    static = PROJECT_ROOT / "re1_rl" / "memlog_dashboard" / "static"
    app = (static / "app.js").read_text(encoding="utf-8")
    css = (static / "style.css").read_text(encoding="utf-8")
    assert 'document.createElement("details")' not in app
    assert 'document.createElement("section")' in app
    assert "window.scrollTo(scrollX, scrollY)" in app
    assert ".table-wrap { max-height:" not in css
    assert ".obs-rows { max-height:" not in css
    assert ".event-list { max-height:" not in css
    assert ".obs-row.padding { display: none" not in css
    assert '$("show-all")' not in app
    assert '"episode_return"' in app
    assert '"episode_reset_frames_left"' in app


def test_http_control_endpoint_enforces_run_id(tmp_path: Path) -> None:
    _write_snapshot(tmp_path)
    server = DashboardHTTPServer(
        ("127.0.0.1", 0),
        DashboardService(DashboardConfig(root=tmp_path)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        request = Request(
            base + "/api/control/pause",
            data=json.dumps({"run_id": "run-a"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            assert json.loads(response.read())["ok"] is True
        bad = Request(
            base + "/api/control/resume",
            data=json.dumps({"run_id": "wrong"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as exc:
            urlopen(bad, timeout=2)
        assert exc.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
