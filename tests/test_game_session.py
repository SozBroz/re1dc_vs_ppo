"""Unit tests for title menu / pause-options escape detection."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.game_session import (
    opening_narration_from_ram,
    opening_phase_from_ram,
    outside_gameplay_reason,
    pause_menu_screen_id,
)
from re1_rl.memory_map import (
    IN_CONTROL_MASK,
    MENU_ROOM_ID,
    PAUSE_MENU_GAME_STATE,
    PAUSE_MENU_GAME_MODE,
    STATUS_ECG_GAME_STATE,
)


def _ram(**kwargs):
    base = {
        "player_hp": 96,
        "stage_id": 0,
        "room_id": 5,
        "character_id": 1,
        "game_mode": IN_CONTROL_MASK,
        "game_state": 0x90000080,
        "msg_flag": 0,
        "scene_flag": 0x8080,
    }
    base.update(kwargs)
    return base


def test_main_menu_room_triggers_restart() -> None:
    reason = outside_gameplay_reason(
        _ram(room_id=MENU_ROOM_ID, player_hp=0), episode_start_hp=96
    )
    assert reason == "main_menu_room"


def test_normal_dining_play_is_ok() -> None:
    assert outside_gameplay_reason(_ram(), episode_start_hp=96) is None


def test_knife_ready_dining_not_flagged_as_pause() -> None:
    """Knife-raised savestate uses game_state 0x80800000, not full 0x90000000."""
    assert outside_gameplay_reason(_ram(game_state=0x80800000), episode_start_hp=96) is None


def test_pause_options_without_active_play_mask() -> None:
    reason = outside_gameplay_reason(
        _ram(game_state=0x00000080), episode_start_hp=96
    )
    assert reason == "pause_or_options_menu"


def test_options_menu_live_signature() -> None:
    from re1_rl.game_session import options_menu_from_ram
    from re1_rl.memory_map import OPTIONS_MENU_GAME_MODE, OPTIONS_MENU_GAME_STATE

    ram = _ram(
        game_state=OPTIONS_MENU_GAME_STATE,
        game_mode=OPTIONS_MENU_GAME_MODE,
    )
    assert options_menu_from_ram(ram) is True
    reason = outside_gameplay_reason(ram, episode_start_hp=96)
    assert reason == "options_menu"
    assert pause_menu_screen_id(OPTIONS_MENU_GAME_STATE) == OPTIONS_MENU_GAME_STATE


def test_pause_menu_screen_id_hunt_signature() -> None:
    """ITEM inventory (START menu) is allowed during equip/use macros."""
    ram = _ram(
        game_state=PAUSE_MENU_GAME_STATE,
        game_mode=PAUSE_MENU_GAME_MODE,
    )
    reason = outside_gameplay_reason(ram, episode_start_hp=96)
    assert reason is None
    assert pause_menu_screen_id(PAUSE_MENU_GAME_STATE) == PAUSE_MENU_GAME_STATE
    assert pause_menu_screen_id(0x40808004) == 0x40808004
    assert pause_menu_screen_id(STATUS_ECG_GAME_STATE) == STATUS_ECG_GAME_STATE
    assert pause_menu_screen_id(0x80800000) is None


def test_cutscene_dialogue_not_flagged_as_pause() -> None:
    assert (
        outside_gameplay_reason(
            _ram(game_state=0x00000080, msg_flag=0x80), episode_start_hp=96
        )
        is None
    )


def test_opening_narration_mode() -> None:
    assert opening_narration_from_ram(_ram(game_mode=0x44, room_id=27)) is True
    phase = opening_phase_from_ram(_ram(game_mode=0x44, room_id=27), had_mansion_hp=False)
    assert phase == "opening_narration"


def test_title_main_menu_before_hp() -> None:
    phase = opening_phase_from_ram(
        _ram(room_id=MENU_ROOM_ID, player_hp=0, game_mode=0x80),
        had_mansion_hp=False,
    )
    assert phase == "title_new_load_menu"


def test_title_new_load_menu_hunt_signature() -> None:
    from re1_rl.memory_map import MAIN_MENU_GAME_MODE, MAIN_MENU_GAME_STATE

    phase = opening_phase_from_ram(
        _ram(
            room_id=MENU_ROOM_ID,
            player_hp=0,
            game_state=MAIN_MENU_GAME_STATE,
            game_mode=MAIN_MENU_GAME_MODE,
        ),
        had_mansion_hp=True,
    )
    assert phase == "title_new_load_menu"
    assert (
        outside_gameplay_reason(
            _ram(room_id=MENU_ROOM_ID, player_hp=0), episode_start_hp=0
        )
        == "main_menu_room"
    )


def test_opening_fmv_cinematic() -> None:
    from re1_rl.memory_map import (
        OPENING_FMV_GAME_STATE,
        OPENING_FMV_SCENE_FLAG,
        IN_CONTROL_MASK,
    )

    phase = opening_phase_from_ram(
        _ram(
            player_hp=0,
            room_id=0,
            game_state=OPENING_FMV_GAME_STATE,
            game_mode=IN_CONTROL_MASK,
            scene_flag=OPENING_FMV_SCENE_FLAG,
        ),
        had_mansion_hp=False,
    )
    assert phase == "opening_fmv_cinematic"


def test_opening_gameplay_teaser() -> None:
    from re1_rl.memory_map import (
        OPENING_GAMEPLAY_TEASER_GAME_MODE,
        OPENING_GAMEPLAY_TEASER_GAME_STATE,
    )

    phase = opening_phase_from_ram(
        _ram(
            player_hp=140,
            room_id=7,
            character_id=1,
            game_state=OPENING_GAMEPLAY_TEASER_GAME_STATE,
            game_mode=OPENING_GAMEPLAY_TEASER_GAME_MODE,
            scene_flag=0,
            msg_flag=0,
        ),
        had_mansion_hp=True,
    )
    assert phase == "opening_gameplay_teaser"


def test_press_any_button_phase() -> None:
    from re1_rl.memory_map import IN_CONTROL_MASK, PRESS_ANY_BUTTON_GAME_STATE

    phase = opening_phase_from_ram(
        _ram(
            player_hp=0,
            room_id=0,
            game_state=PRESS_ANY_BUTTON_GAME_STATE,
            game_mode=IN_CONTROL_MASK,
            scene_flag=0,
            msg_flag=0,
        ),
        had_mansion_hp=False,
    )
    assert phase == "press_any_button"


def test_playstation_logo_phase() -> None:
    from re1_rl.memory_map import PLAYSTATION_LOGO_GAME_MODE, PLAYSTATION_LOGO_GAME_STATE

    phase = opening_phase_from_ram(
        _ram(
            player_hp=0,
            room_id=0,
            game_state=PLAYSTATION_LOGO_GAME_STATE,
            game_mode=PLAYSTATION_LOGO_GAME_MODE,
        ),
        had_mansion_hp=False,
    )
    assert phase == "playstation_logo"


def test_mansion_intro_cutscene_jill_start_shape() -> None:
    phase = opening_phase_from_ram(
        _ram(
            player_hp=140,
            room_id=6,
            character_id=1,
            game_mode=0x42,
            msg_flag=0,
            scene_flag=0,
        ),
        had_mansion_hp=True,
    )
    assert phase == "mansion_intro_cutscene"


def test_front_end_after_episode_started() -> None:
    reason = outside_gameplay_reason(
        _ram(room_id=0, player_hp=0, character_id=0), episode_start_hp=96
    )
    assert reason == "front_end_zero_hp"
