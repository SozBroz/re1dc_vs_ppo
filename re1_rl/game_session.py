"""Detect title menu / pause-options escapes from normal curriculum play."""

from __future__ import annotations

from re1_rl.memory_map import (
    DEATH_CONTINUE_GAME_MODE,
    DEATH_CONTINUE_GAME_STATE,
    DEATH_ROOM_OVERLAY_GAME_STATE,
    DEATH_UI_GAME_MODE,
    DEATH_UI_GAME_STATE,
    DEATH_UI_GAME_STATE_MASK,
    IN_CONTROL_GAMESTATE_MASK,
    IN_CONTROL_MASK,
    MAIN_MENU_GAME_MODE,
    MAIN_MENU_GAME_STATE,
    MENU_ROOM_ID,
    MAIN_MENU_ENTER_GAME_MODE,
    MAIN_MENU_ENTER_GAME_STATE,
    OPENING_FMV_GAME_STATE,
    OPENING_FMV_SCENE_FLAG,
    OPENING_NARRATION_GAME_MODE,
    PAUSE_MENU_GAME_MODE,
    PAUSE_MENU_GAME_STATE,
    PAUSE_MENU_GAME_STATE_MASK,
    OPTIONS_MENU_GAME_MODE,
    OPTIONS_MENU_GAME_STATE,
    PLAYSTATION_LOGO_GAME_MODE,
    PLAYSTATION_LOGO_GAME_STATE,
    PLAYER_HP_MAX,
    PRESS_ANY_BUTTON_GAME_STATE,
    OPENING_GAMEPLAY_TEASER_GAME_STATE,
    OPENING_GAMEPLAY_TEASER_GAME_MODE,
    OPENING_TEASER_ROOM_IDS,
    player_died,
)
from re1_rl.ram_skip import (
    in_control_from_ram,
    item_inventory_screen_from_ram,
    message_open_from_ram,
    pause_menu_tree_from_ram,
    scene_active_from_ram,
)

# RE1 mansion + lab stages used by the Jill any% curriculum.
_MAX_CURRICULUM_STAGE = 6

# Boot / attract screens that mean the agent left curriculum play (not mansion intro).
_BOOT_FAILURE_AT_ZERO_HP = frozenset({
    "playstation_logo",
    "opening_narration",
    "opening_fmv_cinematic",
    "press_any_button",
    "title_new_load_menu",
    "title_menu_enter",
    "title_mode_select",
})


def pause_menu_screen_id(game_state: int) -> int | None:
    """Screen/session id for the in-game pause / OPTIONS stack, if active."""
    gs = int(game_state)
    if gs == OPTIONS_MENU_GAME_STATE:
        return OPTIONS_MENU_GAME_STATE
    if (gs & PAUSE_MENU_GAME_STATE_MASK) == PAUSE_MENU_GAME_STATE:
        return gs
    top = gs & 0xF0000000
    if top in (0x40000000, 0x60000000) and (gs & 0x00FF0000) >= 0x00800000:
        return gs
    return None


def options_menu_from_ram(ram: dict[str, int | float]) -> bool:
    """True on the in-game OPTIONS / CONFIG screen (live ``gs=0x80808000``)."""
    if message_open_from_ram(ram) or scene_active_from_ram(ram):
        return False
    return (
        int(ram.get("game_state", 0)) == OPTIONS_MENU_GAME_STATE
        and int(ram.get("game_mode", 0)) == OPTIONS_MENU_GAME_MODE
    )


def opening_narration_from_ram(ram: dict[str, int | float]) -> bool:
    """True during FMV / text-crawl narration (``game_mode == 0x44``)."""
    return int(ram.get("game_mode", 0)) == OPENING_NARRATION_GAME_MODE


def opening_phase_from_ram(
    ram: dict[str, int | float],
    *,
    had_mansion_hp: bool = False,
) -> str | None:
    """Classify pre-control boot / intro spans for fresh-ROM observation.

    Phases (hunt 2026-07-07, play_human fresh boot + jill_start.State):
      - ``playstation_logo`` — Sony splash (``gs=0x40000000``, ``mode=0x40``)
      - ``title_new_load_menu`` — New Game / Load Game front-end
        (``room_id=27``, ``hp=0``; usually ``gs=0x80000000``, ``mode=0x80``)
      - ``opening_narration`` — engine narration mode (0x44), FMV / text crawl
      - ``opening_fmv_cinematic`` — pre-mansion helicopter FMV
        (``gs=0x80040000``, ``scene=0x04``, HP/room still 0)
      - ``press_any_button`` — Capcom logo / attract wait
        (``gs=0x80000000``, ``mode=0x80``, ``scene=0x00``, room/HP still 0)
      - ``opening_gameplay_teaser`` — in-engine Gallery/Main Hall action
        preview (``gs=0x40000000``, ``mode=0x40``, HP=140, room 6/7, no control)
      - ``mansion_intro_cutscene`` — Jill selected, HP live, mansion rooms 5/6,
        engine has not returned player control (Wesker/Barry hall intro)
    """
    room = int(ram.get("room_id", -1))
    stage = int(ram.get("stage_id", -1))
    hp = int(ram.get("player_hp", 0))
    char_id = int(ram.get("character_id", -1))
    mode = int(ram.get("game_mode", 0))
    gs = int(ram.get("game_state", 0))
    scene = int(ram.get("scene_flag", 0))

    if (
        not had_mansion_hp
        and hp == 0
        and room == 0
        and gs == PLAYSTATION_LOGO_GAME_STATE
        and mode == PLAYSTATION_LOGO_GAME_MODE
    ):
        return "playstation_logo"

    if opening_narration_from_ram(ram):
        return "opening_narration"

    if (
        hp > 0
        and stage == 0
        and room in OPENING_TEASER_ROOM_IDS
        and not in_control_from_ram(ram)
        and gs == OPENING_GAMEPLAY_TEASER_GAME_STATE
        and mode == OPENING_GAMEPLAY_TEASER_GAME_MODE
    ):
        return "opening_gameplay_teaser"

    if (
        not had_mansion_hp
        and hp == 0
        and room == 0
        and gs == OPENING_FMV_GAME_STATE
        and mode == IN_CONTROL_MASK
        and scene == OPENING_FMV_SCENE_FLAG
    ):
        return "opening_fmv_cinematic"

    if (
        not had_mansion_hp
        and hp == 0
        and room == 0
        and gs == PRESS_ANY_BUTTON_GAME_STATE
        and mode == IN_CONTROL_MASK
        and scene == 0
        and int(ram.get("msg_flag", 0)) == 0
    ):
        return "press_any_button"

    if (
        hp == 0
        and room == MENU_ROOM_ID
        and gs == MAIN_MENU_GAME_STATE
        and mode == MAIN_MENU_GAME_MODE
    ):
        return "title_new_load_menu"

    if (
        hp == 0
        and room in (0, MENU_ROOM_ID)
        and gs == MAIN_MENU_ENTER_GAME_STATE
        and mode == MAIN_MENU_ENTER_GAME_MODE
    ):
        return "title_menu_enter"

    if room == MENU_ROOM_ID and hp == 0:
        return "title_new_load_menu"

    if (
        had_mansion_hp
        and hp > 0
        and char_id == 1
        and stage == 0
        and room in (5, 6)
        and not in_control_from_ram(ram)
        and not message_open_from_ram(ram)
        and not scene_active_from_ram(ram)
        and not pause_menu_tree_from_ram(ram)
    ):
        return "mansion_intro_cutscene"

    if (
        had_mansion_hp
        and hp > 0
        and char_id == 1
        and stage == 0
        and room in (5, 6)
        and (message_open_from_ram(ram) or scene_active_from_ram(ram))
    ):
        return "mansion_intro_dialogue"

    return None


def death_ui_from_ram(ram: dict[str, int | float]) -> bool:
    """True on the Continue / Game Over UI after authentic damage."""
    gs = int(ram.get("game_state", 0))
    mode = int(ram.get("game_mode", 0))
    return (
        (gs & DEATH_UI_GAME_STATE_MASK) == (DEATH_UI_GAME_STATE & DEATH_UI_GAME_STATE_MASK)
        and mode == DEATH_UI_GAME_MODE
    )


def death_continue_from_ram(ram: dict[str, int | float]) -> bool:
    """True on the post-death Continue prompt (dog/hunter kill hunt)."""
    return (
        int(ram.get("game_state", 0)) == DEATH_CONTINUE_GAME_STATE
        and int(ram.get("game_mode", 0)) == DEATH_CONTINUE_GAME_MODE
    )


def death_room_overlay_from_ram(ram: dict[str, int | float]) -> bool:
    """True when the engine shows the death room view before the title escape."""
    return (
        int(ram.get("game_state", 0)) == DEATH_ROOM_OVERLAY_GAME_STATE
        and int(ram.get("game_mode", 0)) == IN_CONTROL_MASK
    )


def title_mode_select_from_ram(ram: dict[str, int | float]) -> bool:
    """Director's Cut STANDARD / TRAINING / ADVANCED select after death Continue."""
    return (
        int(ram.get("game_state", 0)) == MAIN_MENU_GAME_STATE
        and int(ram.get("game_mode", 0)) == MAIN_MENU_GAME_MODE
        and int(ram.get("player_hp", 0)) > int(PLAYER_HP_MAX)
    )


def scripted_death_hp_from_ram(
    ram: dict[str, int | float],
    *,
    episode_start_hp: int,
) -> bool:
    """HP sentinel (``0xFFFF``) during an active mansion episode."""
    if int(episode_start_hp) <= 0:
        return False
    hp = int(ram.get("player_hp", 0))
    if hp <= int(PLAYER_HP_MAX):
        return False
    if title_mode_select_from_ram(ram):
        return False
    return True


def episode_death_signal_from_ram(
    ram: dict[str, int | float],
    *,
    episode_start_hp: int = 0,
    prev_hp: int = 0,
) -> bool:
    """Fast aggregate for env ``dead`` flag and macro abort paths."""
    hp = int(ram.get("player_hp", 0))
    return (
        death_ui_from_ram(ram)
        or death_continue_from_ram(ram)
        or death_room_overlay_from_ram(ram)
        or title_mode_select_from_ram(ram)
        or scripted_death_hp_from_ram(ram, episode_start_hp=episode_start_hp)
        or player_died(hp, prev_hp=prev_hp, episode_start_hp=episode_start_hp)
    )


def confirm_midstep_death_abort(
    ram: dict[str, int | float],
    *,
    episode_start_hp: int = 0,
    prev_hp: int = 0,
) -> str | None:
    """Confirm a Lua/skip ``HP==0`` abort should end the episode.

    Mid-step HP can flicker to 0 for a frame (damage resolve, grab edge cases)
    while Jill is still alive — common when low HP near a downed Kenneth zombie.
    Only return a failure reason when post-abort RAM still says dead / death UI.
    """
    return episode_failure_reason(
        ram, episode_start_hp=episode_start_hp, prev_hp=prev_hp
    )


def episode_failure_reason(
    ram: dict[str, int | float],
    *,
    episode_start_hp: int = 0,
    prev_hp: int = 0,
) -> str | None:
    """Return a reason when training must treat the episode as death + hard reset.

    Covers in-mansion HP death, death UI, title/front-end escapes, pause/options
    menus, and boot/attract screens reached mid-curriculum.
    """
    hp = int(ram.get("player_hp", 0))

    if death_ui_from_ram(ram):
        return "death_screen_ui"

    if death_continue_from_ram(ram):
        return "death_continue_screen"

    if death_room_overlay_from_ram(ram):
        return "death_room_overlay"

    if episode_start_hp > 0 and title_mode_select_from_ram(ram):
        return "title_mode_select"

    if scripted_death_hp_from_ram(ram, episode_start_hp=episode_start_hp):
        return "scripted_death_hp"

    if episode_start_hp > 0 and hp == 0:
        boot = opening_phase_from_ram(ram, had_mansion_hp=False)
        if boot is not None:
            return boot
        boot = opening_phase_from_ram(ram, had_mansion_hp=True)
        if boot in _BOOT_FAILURE_AT_ZERO_HP:
            return boot

    outside = outside_gameplay_reason(ram, episode_start_hp=episode_start_hp)
    if outside:
        return outside

    if episode_start_hp > 0 and hp > 0:
        teaser = opening_phase_from_ram(ram, had_mansion_hp=True)
        if teaser == "opening_gameplay_teaser":
            return teaser

    if player_died(hp, prev_hp=prev_hp, episode_start_hp=episode_start_hp):
        return "hp_death"

    return None


def outside_gameplay_reason(
    ram: dict[str, int | float],
    *,
    episode_start_hp: int = 0,
) -> str | None:
    """Return a short reason if the engine left in-mansion play, else None.

  Signals (see docs/memory_hooks_and_observation_design.md § action gating):
    - ``room_id == MENU_ROOM_ID`` — title / load / options front-end (recon boot)
    - ``room_id == 0`` with ``hp == 0`` after episode had real HP — attract / boot
    - ``game_state & 0xFFFFFF00 == 0x40808000`` with ``game_mode == 0x40`` — START
      menu tree (ITEM, STATUS/ECG, MAP). Allowed for equip/use/combine macros.
    - ``game_state == 0x80808000`` with ``game_mode == 0x80`` — OPTIONS / CONFIG
      subtree (live hunt 2026-07-07, play_human :7780)
    - In-mansion HP but ``game_state & 0x90000000`` clear while modal flags clear
      — legacy CONFIG path when in-control byte stays 0x80
    """
    room = int(ram.get("room_id", -1))
    stage = int(ram.get("stage_id", -1))
    hp = int(ram.get("player_hp", 0))
    char_id = int(ram.get("character_id", -1))
    mode = int(ram.get("game_mode", 0))
    gs = int(ram.get("game_state", 0))

    if room == MENU_ROOM_ID:
        return "main_menu_room"

    if episode_start_hp > 0 and hp == 0 and room in (0, MENU_ROOM_ID):
        return "front_end_zero_hp"

    if episode_start_hp > 0 and hp == 0 and char_id == 0 and stage == 0 and room == 0:
        return "title_attract"

    if episode_start_hp > 0 and hp > 0 and stage <= _MAX_CURRICULUM_STAGE:
        if room in (0, MENU_ROOM_ID):
            return "menu_room_in_run"
        if item_inventory_screen_from_ram(ram):
            return None
        if options_menu_from_ram(ram):
            return "options_menu"
        if _is_pause_or_options_menu(ram, mode=mode, gs=gs):
            return "pause_or_options_menu"

    return None


def pause_or_options_menu_from_ram(ram: dict[str, int | float]) -> bool:
    """Legacy CONFIG trap: in-control byte set but active-play mask clear."""
    mode = int(ram.get("game_mode", 0))
    gs = int(ram.get("game_state", 0))
    return _is_pause_or_options_menu(ram, mode=mode, gs=gs)


def _is_pause_or_options_menu(
    ram: dict[str, int | float],
    *,
    mode: int,
    gs: int,
) -> bool:
    """In-game status / CONFIG / controller EDIT while HP and room still look valid."""
    if message_open_from_ram(ram) or scene_active_from_ram(ram):
        return False

    # Hunt-confirmed pause menu tree (ITEM, STATUS/ECG, MAP) — not OPTIONS/CONFIG.
    if mode == PAUSE_MENU_GAME_MODE and (gs & PAUSE_MENU_GAME_STATE_MASK) == PAUSE_MENU_GAME_STATE:
        return False

    # Legacy: in-control byte still 0x80 but active-play mask clear.
    if not (mode & IN_CONTROL_MASK):
        return False
    # Autosplitter uses (gs & 0x90000000) == 0x90000000, but knife-raised and
    # other in-mansion states can show 0x80800000 (bit 31 only). True pause /
    # options menus keep the high bit clear (e.g. 0x00000080).
    if (gs & 0x80000000) != 0:
        return False
    return True


def is_active_gameplay(ram: dict[str, int | float]) -> bool:
    """True when autosplitter-style active-play mask is set."""
    return (int(ram.get("game_state", 0)) & IN_CONTROL_GAMESTATE_MASK) == IN_CONTROL_GAMESTATE_MASK
