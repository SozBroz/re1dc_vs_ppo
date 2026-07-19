"""Four-second cutscene duration gate with per-episode unique keys.

Room-changing sessions use ``room:cam`` at entry. Same-room sessions use
``room:cam:sN``; ``MAX_SAME_ROOM_CUTSCENE_INDEX`` caps repeats per camera.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from re1_rl.cutscene_ledger import _milestone_seen
from re1_rl.game_session import death_ui_from_ram, opening_phase_from_ram
from re1_rl.memory_map import PLAYER_HP_MAX, SCENE_FLAG_MASK
from re1_rl.ram_skip import in_game_menu_from_ram

# Emulated frames burned in one uninterrupted skip session before a cutscene
# counts. 450 frames is 7.5 seconds at PS1 NTSC 60fps: above the observed
# 384–424 frame stair transitions while preserving 508-frame Kenneth.
MIN_CUTSCENE_SKIP_FRAMES = 450

# Same-room freezes use ``room:cam:sN``. Cap N so repeated long freezes at one
# camera cannot mint unbounded +NEW_CUTSCENE_BONUS.
MAX_SAME_ROOM_CUTSCENE_INDEX = 1

# Boot / attract spans — never pay exploration cutscene bonus.
# Kenneth tea-room freeze (``104:*:sN``) unlocks legal Main Hall entry.
KENNETH_CUTSCENE_MILESTONE = "104:0"
MAIN_HALL_ROOM = "106"
TEA_ROOM = "104"
# Telemetry key for soft pre-Kenneth Main Hall entry penalty (no episode end).
ILLEGAL_MAIN_HALL_FAILURE_REASON = "main_hall_before_kenneth"

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
    if n > MAX_SAME_ROOM_CUTSCENE_INDEX:
        return None
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


def kenneth_cutscene_seen(
    rewarded_cutscenes: Collection[str] | None,
    *,
    visited_rooms: Collection[str] | None = None,
) -> bool:
    """True once the tea-room Kenneth zombie script beat paid this episode."""
    del visited_rooms  # visit alone is not the canonical Kenneth ledger mark
    seen = set(rewarded_cutscenes or ())
    if any(str(k).startswith(f"{TEA_ROOM}:") and ":s" in str(k) for k in seen):
        return True
    return False


def illegal_main_hall_before_kenneth_transition(
    prev_room: str,
    room: str,
    *,
    rewarded_cutscenes: Collection[str] | None,
    visited_rooms: Collection[str] | None = None,
) -> bool:
    """True on a transition *into* Main Hall (106) before Kenneth paid.

    Does not fire for starting/resetting in 106, remaining in 106, or entering
    106 after the canonical tea-room Kenneth beat (``104:*:sN``) has paid.
    """
    del visited_rooms
    if str(room) != MAIN_HALL_ROOM:
        return False
    prev = str(prev_room or "")
    if not prev or prev == MAIN_HALL_ROOM:
        return False
    return not kenneth_cutscene_seen(rewarded_cutscenes)


def room_change_cutscene_disqualified(
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
) -> bool:
    """Classify a room-changing skip for diagnostics; it is not a pay denial."""
    if not prev_state or not new_state:
        return False
    prev_r = str(prev_state.get("room_id", "") or "")
    new_r = str(new_state.get("room_id", "") or "")
    return bool(prev_r and new_r and prev_r != new_r)


def story_use_menu_cutscene_exempt(new_state: dict[str, Any] | None) -> bool:
    """Successful story USE: exempt the pause-menu cutscene gate.

    Only when ``story_use_success`` is set — failed USE macros must not earn
    ``new_cutscene`` via this path.
    """
    return bool((new_state or {}).get("story_use_success"))


def scene_flag_shows_script(scene_flag: int) -> bool:
    """True when scene_flag is not mansion idle (matches ram_skip scene_active)."""
    sf = int(scene_flag) & 0xFF
    if sf & SCENE_FLAG_MASK:
        return True
    return (sf & 0x7F) != 0


def apply_skip_script_evidence(
    entry: dict[str, Any] | None,
    *,
    peak_scene_flag: int | None = None,
    peak_msg_flag: int | None = None,
) -> dict[str, Any] | None:
    """Fold mid-skip scene/msg peaks into an entry state for telemetry.

    Reward qualification no longer depends on these peaks; duration owns pay.
    """
    if not entry:
        return entry
    out = dict(entry)
    if peak_scene_flag is not None:
        peak_sf = int(peak_scene_flag) & 0xFF
        entry_sf = int(out.get("scene_flag", 0) or 0) & 0xFF
        if scene_flag_shows_script(peak_sf) and not scene_flag_shows_script(entry_sf):
            out["scene_flag"] = peak_sf
    if peak_msg_flag is not None:
        peak_msg = int(peak_msg_flag) & 0xFF
        entry_msg = int(out.get("msg_flag", 0) or 0) & 0xFF
        if peak_msg != entry_msg and peak_msg != 0:
            out["msg_flag"] = peak_msg
    return out


def _inventory_name_set(state: dict[str, Any] | None) -> set[str]:
    from re1_rl.item_todo import canonical_item

    names: set[str] = set()
    for raw in (state or {}).get("inventory") or ():
        name = canonical_item(str(raw))
        if name:
            names.add(name)
    return names


def inventory_acquired(
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
) -> set[str]:
    """Item names gained between skip entry and settle (canonical)."""
    return _inventory_name_set(new_state) - _inventory_name_set(prev_state)


def pickup_cutscene_disqualified(
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    cutscene_blocked_after_pickup_room: str | None = None,
) -> bool:
    """True when cutscene must yield to the item/weapon pickup channel.

    Skill (a): pickup has its own pay (#3/#5/#6) — never also ``new_cutscene``.
    Covers (1) inventory growth on this skip settle and (2) same-room fragment
    skips after a key/weapon pickup until Jill leaves that room.
    """
    if inventory_acquired(prev_state, new_state):
        return True
    blocked = str(cutscene_blocked_after_pickup_room or "")
    if not blocked:
        return False
    room = str((new_state or {}).get("room_id", "") or "")
    return bool(room) and room == blocked


def qualify_cutscene_reward(
    *,
    skip_frames: int,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    episode_start_hp: int = 0,
    rewarded_cutscenes: Collection[str] | None = None,
    visited_rooms: Collection[str] | None = None,
    cutscene_blocked_after_pickup_room: str | None = None,
) -> str | None:
    """Return a key when a non-exempt freeze lasts at least 450 frames."""
    del visited_rooms
    if int(skip_frames) < MIN_CUTSCENE_SKIP_FRAMES:
        return None
    key = cutscene_key_from_state(
        prev_state,
        new_state,
        rewarded_cutscenes=rewarded_cutscenes,
    )
    if key is None:
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
        if not story_use_menu_cutscene_exempt(new_state):
            return None
    if in_game_menu_from_ram(_ram_view_from_state(new_state or {})):
        if not story_use_menu_cutscene_exempt(new_state):
            return None

    if pickup_cutscene_disqualified(
        prev_state,
        new_state,
        cutscene_blocked_after_pickup_room=cutscene_blocked_after_pickup_room,
    ):
        return None

    # Pre-Kenneth Main Hall scripts (Wesker talk, etc.): never pay cutscene.
    # Illegal hall entry already applies the soft -1.6 gate in compute_reward.
    hall_room = str((new_state or {}).get("room_id", "") or "") or str(
        (prev_state or {}).get("room_id", "") or ""
    )
    if hall_room == MAIN_HALL_ROOM and not kenneth_cutscene_seen(rewarded_cutscenes):
        return None

    return key


def cutscene_disqualify_reason(
    *,
    skip_frames: int,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    episode_start_hp: int = 0,
    rewarded_cutscenes: Collection[str] | None = None,
    visited_rooms: Collection[str] | None = None,
    cutscene_blocked_after_pickup_room: str | None = None,
) -> str | None:
    """Human-readable reason when ``qualify_cutscene_reward`` returns None."""
    del visited_rooms
    if int(skip_frames) < MIN_CUTSCENE_SKIP_FRAMES:
        return (
            f"skip_frames={int(skip_frames)} < {MIN_CUTSCENE_SKIP_FRAMES}"
        )
    if cutscene_key_from_state(
        prev_state, new_state, rewarded_cutscenes=rewarded_cutscenes
    ) is None:
        base = cutscene_room_cam(prev_state)
        if base is not None:
            room, cam = base
            new_room = str((new_state or {}).get("room_id", "") or "")
            if not new_room or new_room == room:
                n = same_room_cutscene_index(room, cam, rewarded_cutscenes)
                if n > MAX_SAME_ROOM_CUTSCENE_INDEX:
                    return (
                        f"same-room cutscene index {n} > "
                        f"MAX_SAME_ROOM_CUTSCENE_INDEX="
                        f"{MAX_SAME_ROOM_CUTSCENE_INDEX}"
                    )
        return "no room:cam key at skip entry"
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
        if not story_use_menu_cutscene_exempt(new_state):
            return "pause menu at skip entry"
    if in_game_menu_from_ram(_ram_view_from_state(new_state or {})):
        if not story_use_menu_cutscene_exempt(new_state):
            return "pause menu at skip exit"
    if pickup_cutscene_disqualified(
        prev_state,
        new_state,
        cutscene_blocked_after_pickup_room=cutscene_blocked_after_pickup_room,
    ):
        if inventory_acquired(prev_state, new_state):
            return "item pickup (own channel; not cutscene)"
        return (
            "post-pickup same-room suppress "
            f"(blocked_room={cutscene_blocked_after_pickup_room!r})"
        )
    hall_room = str((new_state or {}).get("room_id", "") or "") or str(
        (prev_state or {}).get("room_id", "") or ""
    )
    if hall_room == MAIN_HALL_ROOM and not kenneth_cutscene_seen(rewarded_cutscenes):
        return "pre-Kenneth Main Hall cutscene (Wesker/hall; poisoned gate, no pay)"
    return None


def skip_session_kind(
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
) -> str:
    """Classify a settled skip: door room-change vs same-room script/examine."""
    if room_change_cutscene_disqualified(prev_state, new_state):
        return "door_room_change"
    return "same_room_script"


def format_cutscene_gate_panel(
    *,
    skip_frames: int,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    episode_start_hp: int = 0,
    rewarded_cutscenes: Collection[str] | None = None,
    visited_rooms: Collection[str] | None = None,
    cutscene_blocked_after_pickup_room: str | None = None,
    positive_rewards_disabled: bool = False,
    qualified_key: str | None = None,
    breakdown: dict[str, float] | None = None,
) -> str:
    """Terminal panel for cutscene reward gating (human monitor harness)."""
    proposed = cutscene_key_from_state(
        prev_state, new_state, rewarded_cutscenes=rewarded_cutscenes
    )
    kind = skip_session_kind(prev_state, new_state)
    if qualified_key is None:
        qualified_key = qualify_cutscene_reward(
            skip_frames=skip_frames,
            prev_state=prev_state,
            new_state=new_state,
            episode_start_hp=episode_start_hp,
            rewarded_cutscenes=rewarded_cutscenes,
            visited_rooms=visited_rooms,
            cutscene_blocked_after_pickup_room=cutscene_blocked_after_pickup_room,
        )
    kenneth = kenneth_cutscene_seen(
        rewarded_cutscenes, visited_rooms=visited_rooms
    )
    visited = sorted({str(r) for r in (visited_rooms or ())})
    rewarded = sorted({str(k) for k in (rewarded_cutscenes or ())})
    prev_r = str((prev_state or {}).get("room_id", "") or "")
    new_r = str((new_state or {}).get("room_id", "") or "")
    prev_cam = int((prev_state or {}).get("cam_id", 0) or 0)
    new_cam = int((new_state or {}).get("cam_id", 0) or 0)
    illegal_hall = illegal_main_hall_before_kenneth_transition(
        prev_r,
        new_r,
        rewarded_cutscenes=rewarded_cutscenes,
        visited_rooms=visited_rooms,
    )
    paid = float((breakdown or {}).get("new_cutscene", 0.0))
    why = cutscene_disqualify_reason(
        skip_frames=skip_frames,
        prev_state=prev_state,
        new_state=new_state,
        episode_start_hp=episode_start_hp,
        rewarded_cutscenes=rewarded_cutscenes,
        visited_rooms=visited_rooms,
    )
    lines = [
        "[cutscene-gate]",
        (
            f"  kind={kind}  skip_frames={int(skip_frames)}  "
            f"prev={prev_r}:cam{prev_cam}  new={new_r}:cam{new_cam}"
        ),
        (
            f"  proposed_key={proposed!r}  qualified={qualified_key!r}  "
            f"paid new_cutscene={paid:+.5f}"
        ),
        (
            f"  kenneth_seen={kenneth}  "
            f"illegal_main_hall_entry={'FAIL' if illegal_hall else 'ok'}"
        ),
        f"  visited_rooms={visited}",
        f"  rewarded_cutscenes={rewarded}",
    ]
    if paid <= 0.0:
        if positive_rewards_disabled:
            lines.append(
                "  unpaid_reason: positive rewards disabled after Kenneth gate breach"
            )
        elif why:
            lines.append(f"  unpaid_reason: {why}")
        elif proposed and str(proposed) in rewarded:
            lines.append(f"  unpaid_reason: duplicate key {proposed!r} this episode")
        elif qualified_key is None and not why:
            lines.append("  unpaid_reason: qualified key is None (no gate match)")
        else:
            lines.append("  unpaid_reason: (unknown)")
    return "\n".join(lines)
