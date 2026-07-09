"""Training episode failure detection (death UI, menus, boot screens)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.game_session import (
    death_continue_from_ram,
    death_room_overlay_from_ram,
    death_ui_from_ram,
    episode_failure_reason,
    opening_phase_from_ram,
    title_mode_select_from_ram,
)
from re1_rl.memory_map import (
    DEATH_CONTINUE_GAME_MODE,
    DEATH_CONTINUE_GAME_STATE,
    DEATH_ROOM_OVERLAY_GAME_STATE,
    DEATH_UI_GAME_MODE,
    DEATH_UI_GAME_STATE,
    IN_CONTROL_MASK,
    MAIN_MENU_GAME_MODE,
    MAIN_MENU_GAME_STATE,
    MENU_ROOM_ID,
    OPENING_FMV_GAME_STATE,
    OPENING_FMV_SCENE_FLAG,
    PAUSE_MENU_GAME_MODE,
    PAUSE_MENU_GAME_STATE,
    SCRIPTED_DEATH_HP,
)

PROBE = Path(__file__).resolve().parents[1] / "data" / "new_death_screen_probe.json"


def _ram(**kwargs):
    base = {
        "player_hp": 96,
        "stage_id": 0,
        "room_id": 5,
        "character_id": 1,
        "game_mode": IN_CONTROL_MASK,
        "game_state": 0x80800004,
        "msg_flag": 0,
        "scene_flag": 0x8080,
    }
    base.update(kwargs)
    return base


def test_death_ui_screen() -> None:
    ram = _ram(
        player_hp=65524,
        game_state=DEATH_UI_GAME_STATE,
        game_mode=DEATH_UI_GAME_MODE,
    )
    assert death_ui_from_ram(ram) is True
    assert episode_failure_reason(ram, episode_start_hp=96, prev_hp=96) == "death_screen_ui"


def test_death_ui_dog_fade_variant() -> None:
    ram = _ram(
        player_hp=SCRIPTED_DEATH_HP,
        game_state=0x81800000,
        game_mode=DEATH_UI_GAME_MODE,
        room_id=8,
        scene_flag=0x80,
    )
    assert death_ui_from_ram(ram) is True
    assert episode_failure_reason(ram, episode_start_hp=96, prev_hp=16) == "death_screen_ui"


def test_death_continue_screen() -> None:
    ram = _ram(
        player_hp=SCRIPTED_DEATH_HP,
        game_state=DEATH_CONTINUE_GAME_STATE,
        game_mode=DEATH_CONTINUE_GAME_MODE,
        room_id=8,
        scene_flag=0x80,
    )
    assert death_continue_from_ram(ram) is True
    assert (
        episode_failure_reason(ram, episode_start_hp=96, prev_hp=16)
        == "death_continue_screen"
    )


def test_title_mode_select_after_death() -> None:
    ram = _ram(
        player_hp=SCRIPTED_DEATH_HP,
        game_state=MAIN_MENU_GAME_STATE,
        game_mode=MAIN_MENU_GAME_MODE,
        room_id=8,
        character_id=1,
    )
    assert title_mode_select_from_ram(ram) is True
    assert (
        episode_failure_reason(ram, episode_start_hp=96, prev_hp=96)
        == "title_mode_select"
    )


def test_death_room_overlay_quick_save3() -> None:
    ram = _ram(
        player_hp=96,
        game_state=DEATH_ROOM_OVERLAY_GAME_STATE,
        game_mode=IN_CONTROL_MASK,
        room_id=19,
        scene_flag=0x80,
    )
    assert death_room_overlay_from_ram(ram) is True
    assert (
        episode_failure_reason(ram, episode_start_hp=96, prev_hp=96)
        == "death_room_overlay"
    )


def test_live_quicksave_probe_signatures() -> None:
    if not PROBE.is_file():
        return
    rows = json.loads(PROBE.read_text(encoding="utf-8"))
    by_file = {row["file"]: row for row in rows}
    qs0 = by_file[
        "Resident Evil - Director's Cut (USA).Nymashock.QuickSave0.State"
    ]
    qs3 = by_file[
        "Resident Evil - Director's Cut (USA).Nymashock.QuickSave3.State"
    ]
    ram0 = {
        "player_hp": qs0["player_hp"],
        "stage_id": qs0["stage_id"],
        "room_id": qs0["room_id"],
        "character_id": qs0["character_id"],
        "game_mode": qs0["game_mode"],
        "game_state": qs0["game_state"],
        "scene_flag": qs0["scene_flag"],
        "msg_flag": qs0["msg_flag"],
    }
    ram3 = {
        "player_hp": qs3["player_hp"],
        "stage_id": qs3["stage_id"],
        "room_id": qs3["room_id"],
        "character_id": qs3["character_id"],
        "game_mode": qs3["game_mode"],
        "game_state": qs3["game_state"],
        "scene_flag": qs3["scene_flag"],
        "msg_flag": qs3["msg_flag"],
    }
    assert (
        episode_failure_reason(ram0, episode_start_hp=96, prev_hp=96)
        == "title_mode_select"
    )
    assert (
        episode_failure_reason(ram3, episode_start_hp=96, prev_hp=96)
        == "death_room_overlay"
    )


def test_hp_death() -> None:
    ram = _ram(player_hp=0)
    assert episode_failure_reason(ram, episode_start_hp=96, prev_hp=48) == "hp_death"


def test_main_menu_is_episode_failure() -> None:
    ram = _ram(room_id=MENU_ROOM_ID, player_hp=0, game_state=0x80000000)
    assert episode_failure_reason(ram, episode_start_hp=96, prev_hp=96) == "title_new_load_menu"


def test_opening_screen_mid_curriculum() -> None:
    ram = _ram(
        player_hp=0,
        room_id=0,
        game_state=OPENING_FMV_GAME_STATE,
        game_mode=IN_CONTROL_MASK,
        scene_flag=OPENING_FMV_SCENE_FLAG,
    )
    assert (
        episode_failure_reason(ram, episode_start_hp=96, prev_hp=96)
        == "opening_fmv_cinematic"
    )


def test_item_inventory_screen_not_episode_failure() -> None:
    """ITEM screen (ECG + inventory) must not terminate training mid equip/use."""
    ram = _ram(game_state=PAUSE_MENU_GAME_STATE, game_mode=PAUSE_MENU_GAME_MODE)
    assert episode_failure_reason(ram, episode_start_hp=96, prev_hp=96) is None
    ram_status = _ram(game_state=0x40808004, game_mode=PAUSE_MENU_GAME_MODE)
    assert episode_failure_reason(ram_status, episode_start_hp=96, prev_hp=96) is None


def test_options_menu_live_signature_is_episode_failure() -> None:
    from re1_rl.memory_map import OPTIONS_MENU_GAME_MODE, OPTIONS_MENU_GAME_STATE

    ram = _ram(game_state=OPTIONS_MENU_GAME_STATE, game_mode=OPTIONS_MENU_GAME_MODE)
    assert (
        episode_failure_reason(ram, episode_start_hp=96, prev_hp=96) == "options_menu"
    )


def test_options_menu_does_not_false_positive_dining() -> None:
    assert episode_failure_reason(
        _ram(game_state=0x80800000, game_mode=IN_CONTROL_MASK),
        episode_start_hp=96,
        prev_hp=96,
    ) is None


def test_normal_play_not_failure() -> None:
    assert episode_failure_reason(_ram(), episode_start_hp=96, prev_hp=96) is None
