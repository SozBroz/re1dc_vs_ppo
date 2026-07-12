"""Cutscene exploration reward gating (per-episode unique keys).

Door / room-change skips use ``room:cam`` (blocks re-crossing the same door).

Same-room scripted beats (Barry talk, then Barry zombie kill on return) share a
camera — those use ``room:cam:sN`` so a later beat still pays once.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from re1_rl.game_session import death_ui_from_ram, opening_phase_from_ram
from re1_rl.memory_map import PLAYER_HP_MAX
from re1_rl.ram_skip import in_game_menu_from_ram

# Emulated frames burned in skip_uncontrolled before a cutscene counts.
MIN_CUTSCENE_SKIP_FRAMES = 20

# Boot / attract spans — never pay exploration cutscene bonus.
# In-mansion Barry/Wesker scenes (``mansion_intro_*``) are real gameplay cutscenes
# and pay once per room:cam like doors/Kenneth.
OPENING_PHASES_NO_REWARD: frozenset[str] = frozenset(
    {
        "playstation_logo",
        "title_new_load_menu",
        "title_menu_enter",
        "opening_narration",
        "opening_fmv_cinematic",
        "press_any_button",
        "opening_gameplay_teaser",
    }
)


def cutscene_room_cam(state: dict[str, Any] | None) -> tuple[str, int] | None:
    """``(room_id, cam_id)`` at skip entry, or None if unusable."""
    if not state:
        return None
    room = str(state.get("room_id", "") or "")
    if not room:
        return None
    return room, int(state.get("cam_id", 0))


def same_room_cutscene_index(
    room: str,
    cam: int,
    rewarded_cutscenes: Collection[str] | None,
) -> int:
    """Next ``sN`` index for same-room beats already claimed this episode."""
    prefix = f"{room}:{int(cam)}:s"
    best = -1
    for key in rewarded_cutscenes or ():
        if not str(key).startswith(prefix):
            continue
        suffix = str(key)[len(prefix) :]
        if suffix.isdigit():
            best = max(best, int(suffix))
    return best + 1


def cutscene_key_from_state(
    state: dict[str, Any] | None,
    new_state: dict[str, Any] | None = None,
    *,
    rewarded_cutscenes: Collection[str] | None = None,
) -> str | None:
    """Stable per-episode id at skip entry.

    Room-changing skips → ``room:cam``.
    Same-room skips → ``room:cam:sN`` (N = next unused index in ``rewarded_cutscenes``).
    """
    base = cutscene_room_cam(state)
    if base is None:
        return None
    room, cam = base
    door_key = f"{room}:{cam}"
    if new_state is None:
        return door_key
    new_room = str(new_state.get("room_id", "") or "")
    if new_room and new_room != room:
        return door_key
    n = same_room_cutscene_index(room, cam, rewarded_cutscenes)
    return f"{room}:{cam}:s{n}"


def _ram_view_from_state(state: dict[str, Any]) -> dict[str, int | float]:
    room_byte = state.get("room_byte")
    if room_byte is None and state.get("room_id"):
        # Fallback for tests / sparse dicts: "105" -> room byte 5 on stage 0.
        code = str(state["room_id"])
        if len(code) >= 3 and code[0].isdigit():
            room_byte = int(code[2:], 16)
    return {
        "room_id": int(room_byte or 0),
        "stage_id": int(state.get("stage_id", 0)),
        "player_hp": int(state.get("hp", 0)),
        "character_id": int(state.get("character_id", 1)),
        "game_mode": int(state.get("game_mode", 0)),
        "game_state": int(state.get("game_state", 0)),
        "scene_flag": int(state.get("scene_flag", 0)),
        "msg_flag": int(state.get("msg_flag", 0)),
    }


def opening_phase_for_state(
    state: dict[str, Any] | None,
    *,
    episode_start_hp: int,
) -> str | None:
    if not state:
        return None
    had_mansion_hp = int(episode_start_hp) > 0
    return opening_phase_from_ram(
        _ram_view_from_state(state),
        had_mansion_hp=had_mansion_hp,
    )


def cutscene_death_disqualified_from_state(
    state: dict[str, Any] | None,
    *,
    episode_start_hp: int,
) -> bool:
    """Hunter/dog kill + white fade: HP already 0 — never pay exploration cutscene."""
    if not state:
        return False
    ram = _ram_view_from_state(state)
    hp = int(state.get("hp", ram.get("player_hp", 0)))
    if death_ui_from_ram(ram):
        return True
    if int(episode_start_hp) <= 0:
        return False
    # Dog/hunter kill: HP RAM reads 0 or 0xFFFF during scripted death / white fade.
    return hp <= 0 or hp > int(PLAYER_HP_MAX)


def qualify_cutscene_reward(
    *,
    skip_frames: int,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    episode_start_hp: int = 0,
    rewarded_cutscenes: Collection[str] | None = None,
) -> str | None:
    """Return cutscene key if this skip earns ``new_cutscene`` bonus, else None."""
    if int(skip_frames) < MIN_CUTSCENE_SKIP_FRAMES:
        return None
    key = cutscene_key_from_state(
        prev_state,
        new_state,
        rewarded_cutscenes=rewarded_cutscenes,
    )
    if key is None:
        return None

    hp_before = int((prev_state or {}).get("hp", 0))
    hp_after = int((new_state or {}).get("hp", 0))
    if hp_before > 0 and hp_after < hp_before:
        return None
    if cutscene_death_disqualified_from_state(
        prev_state, episode_start_hp=episode_start_hp
    ):
        return None
    if cutscene_death_disqualified_from_state(
        new_state, episode_start_hp=episode_start_hp
    ):
        return None

    phase = opening_phase_for_state(prev_state, episode_start_hp=episode_start_hp)
    if phase in OPENING_PHASES_NO_REWARD:
        return None
    phase_after = opening_phase_for_state(new_state, episode_start_hp=episode_start_hp)
    if phase_after in OPENING_PHASES_NO_REWARD:
        return None

    if in_game_menu_from_ram(_ram_view_from_state(prev_state or {})):
        return None
    if in_game_menu_from_ram(_ram_view_from_state(new_state or {})):
        return None

    return key


def cutscene_disqualify_reason(
    *,
    skip_frames: int,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    episode_start_hp: int = 0,
    rewarded_cutscenes: Collection[str] | None = None,
) -> str | None:
    """Human-readable reason when ``qualify_cutscene_reward`` returns None."""
    if int(skip_frames) < MIN_CUTSCENE_SKIP_FRAMES:
        return (
            f"skip_frames={int(skip_frames)} < {MIN_CUTSCENE_SKIP_FRAMES}"
        )
    if cutscene_key_from_state(
        prev_state, new_state, rewarded_cutscenes=rewarded_cutscenes
    ) is None:
        return "no room:cam key at skip entry"
    hp_before = int((prev_state or {}).get("hp", 0))
    hp_after = int((new_state or {}).get("hp", 0))
    if hp_before > 0 and hp_after < hp_before:
        return "hp loss during skip"
    if cutscene_death_disqualified_from_state(
        prev_state, episode_start_hp=episode_start_hp
    ):
        return "death span at skip entry"
    if cutscene_death_disqualified_from_state(
        new_state, episode_start_hp=episode_start_hp
    ):
        return "death span at skip exit"
    phase = opening_phase_for_state(prev_state, episode_start_hp=episode_start_hp)
    if phase in OPENING_PHASES_NO_REWARD:
        return f"opening phase {phase!r}"
    phase_after = opening_phase_for_state(new_state, episode_start_hp=episode_start_hp)
    if phase_after in OPENING_PHASES_NO_REWARD:
        return f"opening phase after {phase_after!r}"
    if in_game_menu_from_ram(_ram_view_from_state(prev_state or {})):
        return "pause menu at skip entry"
    if in_game_menu_from_ram(_ram_view_from_state(new_state or {})):
        return "pause menu at skip exit"
    return None
