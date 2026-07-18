"""Cutscene exploration reward gating (per-episode unique keys).

Door / room-change skips use ``room:cam`` (blocks re-crossing the same door).

Same-room scripted beats (Barry talk, then Barry zombie kill on return) share a
camera — those use ``room:cam:sN`` so a later beat still pays once
(``MAX_SAME_ROOM_CUTSCENE_INDEX`` caps N; default allows s0 and s1 only).
"""

from __future__ import annotations

import math
from collections.abc import Collection
from typing import Any

from re1_rl.cutscene_ledger import _milestone_seen
from re1_rl.game_session import death_ui_from_ram, opening_phase_from_ram
from re1_rl.memory_map import PLAYER_HP_MAX, SCENE_FLAG_MASK
from re1_rl.ram_skip import in_game_menu_from_ram

# Emulated frames burned in skip_uncontrolled before a cutscene counts.
MIN_CUTSCENE_SKIP_FRAMES = 20

# Same-room scripted beats use ``room:cam:sN``. Cap N so scene_flag flicker /
# dialogue loops cannot mint unbounded +NEW_CUTSCENE_BONUS in 104/105.
# s0+s1 covers Barry talk then Barry-zombie return (see tests).
MAX_SAME_ROOM_CUTSCENE_INDEX = 1

# Boot / attract spans — never pay exploration cutscene bonus.
# In-mansion Barry/Wesker scenes (``mansion_intro_*``) are real gameplay cutscenes
# and pay once per room:cam like doors/Kenneth.
# Kenneth tea-room zombie script (``104:*:sN``). Sole hard gate: transitioning
# into Main Hall (106) before this beat has paid ends the episode (env).
KENNETH_CUTSCENE_MILESTONE = "104:0"
MAIN_HALL_ROOM = "106"
DINING_ROOM = "105"
TEA_ROOM = "104"
BARRY_DINING_CAM = 0
BARRY_DINING_CLUSTER_PREFIX = f"{DINING_ROOM}:{BARRY_DINING_CAM}:s"
# Telemetry / info key when illegal pre-Kenneth Main Hall entry terminates.
ILLEGAL_MAIN_HALL_FAILURE_REASON = "main_hall_before_kenneth"
# Empirical 105→106 west double door (data/doors_empirical.json). Hall-door
# pose used by examine-text idle-settle anti-farm (not a Kenneth reward gate).
DINING_HALL_DOOR_X = 30700
DINING_HALL_DOOR_Z = 7200
# Spawn (~31203,6892) sits inside this radius; Barry after walking into the
# room does not.
DINING_HALL_DOOR_RADIUS = 1800

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


def dining_tea_corridor_repeat_disqualified(
    *,
    key: str,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    rewarded_cutscenes: Collection[str] | None,
    visited_rooms: Collection[str] | None,
) -> bool:
    """Block dining↔tea-room cutscene farm (multi-cam doors + Kenneth sN replay).

    Door re-crosses are also split by harness/training mid-skip room-crossing
    credit (``new_room`` only, script segment reset) so settle frames do not
    inherit the door span as a same-room ``room:cam:sN`` cutscene.
    """
    visited = {str(r) for r in (visited_rooms or ())}
    if DINING_ROOM not in visited or TEA_ROOM not in visited:
        return False

    prev_r = str((prev_state or {}).get("room_id", "") or "")
    new_r = str((new_state or {}).get("room_id", "") or "")
    rewarded = {str(k) for k in (rewarded_cutscenes or ())}

    if prev_r == DINING_ROOM and new_r == TEA_ROOM:
        if any(k.startswith(f"{DINING_ROOM}:") for k in rewarded):
            return True
    if prev_r == TEA_ROOM and new_r == DINING_ROOM:
        if any(
            k.startswith(f"{TEA_ROOM}:") and ":s" not in k for k in rewarded
        ):
            return True

    if str(key).startswith(f"{TEA_ROOM}:") and ":s" in str(key):
        if any(k.startswith(f"{TEA_ROOM}:") and ":s" in k for k in rewarded):
            return True

    # After the corridor is known, block unbounded same-cam dining sN farm
    # (door settle re-triggers). First beat per cam (``:s0``) and Barry cluster
    # still pay; ``:s1+`` at non-Barry cams does not.
    if (
        prev_r == DINING_ROOM
        and new_r == DINING_ROOM
        and str(key).startswith(f"{DINING_ROOM}:")
        and ":s" in str(key)
        and not barry_dining_cluster_key(key)
        and not str(key).endswith(":s0")
    ):
        return True

    return False


def kenneth_tea_script_key(key: str) -> bool:
    """Tea-room Kenneth zombie script (``104:*:sN``)."""
    return str(key).startswith(f"{TEA_ROOM}:") and ":s" in str(key)


def same_room_script_key(key: str) -> bool:
    """Sequenced same-camera beat (``room:cam:sN``) — not a door ``room:cam`` key."""
    return ":s" in str(key)


def same_camera_sequel_script_key(
    key: str,
    rewarded_cutscenes: Collection[str] | None,
) -> bool:
    """Second beat at a camera (``:s1``) after ``:s0`` already paid — examine-safe."""
    k = str(key)
    if not k.endswith(":s1"):
        return False
    base = k[: -len(":s1")]
    prefix = base + ":s"
    return any(str(x).startswith(prefix) for x in (rewarded_cutscenes or ()))


def opening_corridor_dialogue_script_key(
    key: str,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
) -> bool:
    """Dining/tea same-room ``:sN`` with real dialogue/script evidence.

    Barry's walk-up talk often starts on cam 1/2 (not only cam 0) as msg-only
    idle scene — must be examine-exempt. Idle examine spam (no msg/scene
    movement) must stay blocked.
    """
    k = str(key)
    if ":s" not in k:
        return False
    if not (k.startswith(f"{DINING_ROOM}:") or k.startswith(f"{TEA_ROOM}:")):
        return False
    prev_sf = int((prev_state or {}).get("scene_flag", 0) or 0)
    new_sf = int((new_state or {}).get("scene_flag", 0) or 0)
    if (prev_sf & SCENE_FLAG_MASK) or (new_sf & SCENE_FLAG_MASK):
        return True
    if prev_sf != new_sf:
        return True
    prev_msg = int((prev_state or {}).get("msg_flag", 0) or 0)
    new_msg = int((new_state or {}).get("msg_flag", 0) or 0)
    return prev_msg != new_msg


def canonical_story_script_key(
    key: str,
    rewarded_cutscenes: Collection[str] | None = None,
    *,
    prev_state: dict[str, Any] | None = None,
    new_state: dict[str, Any] | None = None,
) -> bool:
    """Barry / Kenneth / dining-tea dialogue / same-camera sequel markers.

    Used for diagnostics / story classification. Examine-text skips are blocked
    outright in ``qualify_cutscene_reward`` (no cam-0 exemption) so interact
    spam cannot mint ``new_cutscene``.
    """
    return (
        barry_dining_cluster_key(key)
        or kenneth_tea_script_key(key)
        or opening_corridor_dialogue_script_key(key, prev_state, new_state)
        or same_camera_sequel_script_key(key, rewarded_cutscenes)
    )


def barry_dining_cluster_key(key: str) -> bool:
    """Barry talk + Barry zombie at dining cam 0 (``105:0:sN``)."""
    return str(key).startswith(BARRY_DINING_CLUSTER_PREFIX)


def near_dining_hall_door(state: dict[str, Any] | None) -> bool:
    """True when pose is at the dining→main-hall door (Wesker trigger zone)."""
    if not state:
        return False
    try:
        x = float(state.get("x", 0) or 0)
        z = float(state.get("z", 0) or 0)
    except (TypeError, ValueError):
        return False
    return math.hypot(x - DINING_HALL_DOOR_X, z - DINING_HALL_DOOR_Z) <= float(
        DINING_HALL_DOOR_RADIUS
    )


def room_change_cutscene_disqualified(
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
) -> bool:
    """Room A -> door skip -> room B is discovery (``new_room``), not a script beat."""
    if not prev_state or not new_state:
        return False
    prev_r = str(prev_state.get("room_id", "") or "")
    new_r = str(new_state.get("room_id", "") or "")
    return bool(prev_r and new_r and prev_r != new_r)


def story_use_menu_cutscene_exempt(new_state: dict[str, Any] | None) -> bool:
    """Successful story USE: exempt pause-menu and examine-text cutscene gates.

    Only when ``story_use_success`` is set — failed USE macros must not earn
    ``new_cutscene`` via this path.
    """
    return bool((new_state or {}).get("story_use_success"))


# Same-room dialogue with message-flag movement (Barry, …) — not examine spam.
SCRIPT_DIALOGUE_MIN_SKIP_FRAMES = 60
# Story scripts often settle with idle scene/msg at BOTH skip endpoints (live
# Barry walk-up: 1223 frames @ 105 cam2, both ends 0x80). Interact/examine
# farm is short (~34 frames). Existing examine unit cases use ~120 frames —
# keep the idle-settle floor above that band.
STORY_IDLE_SETTLE_MIN_SKIP_FRAMES = 300


def examine_text_skip_disqualified(
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    *,
    skip_frames: int = 0,
) -> bool:
    """Locked-door / examine message: same room, idle scene — not exploration."""
    if not prev_state or not new_state:
        return False
    room = str(prev_state.get("room_id", "") or "")
    if not room or room != str(new_state.get("room_id", "") or ""):
        return False
    prev_sf = int(prev_state.get("scene_flag", 0))
    new_sf = int(new_state.get("scene_flag", 0))
    # Scripted scenes (Barry, Kenneth, …) may move scene_flag mid-skip.
    if (prev_sf & SCENE_FLAG_MASK) or (new_sf & SCENE_FLAG_MASK):
        return False
    if prev_sf != new_sf:
        return False
    msg_before = int(prev_state.get("msg_flag", 0))
    msg_after = int(new_state.get("msg_flag", 0))
    if (
        msg_before != msg_after
        and int(skip_frames) >= SCRIPT_DIALOGUE_MIN_SKIP_FRAMES
    ):
        return False
    # Idle endpoints with no msg delta: short = examine/interact farm; long =
    # story beat that returned to mansion idle (do not require endpoint evidence).
    # Dining hall-door zone stays examine-blocked (Wesker). Barry walk-up is
    # away from that door (live: 1219f @ cam2 after walking, not interact spam).
    if int(skip_frames) >= STORY_IDLE_SETTLE_MIN_SKIP_FRAMES:
        if room == TEA_ROOM:
            pass  # keep tea idle-settle blocked; Kenneth moves scene_flag
        elif room == DINING_ROOM and (
            near_dining_hall_door(prev_state) or near_dining_hall_door(new_state)
        ):
            pass  # Wesker door farm
        else:
            return False
    # Typical in-room idle while walking / at a door.
    return (prev_sf & 0x7F) in (0, 0x80)


def qualify_cutscene_reward(
    *,
    skip_frames: int,
    prev_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
    episode_start_hp: int = 0,
    rewarded_cutscenes: Collection[str] | None = None,
    visited_rooms: Collection[str] | None = None,
) -> str | None:
    """Return cutscene key if this skip earns ``new_cutscene`` bonus, else None."""
    # Room A→B is door discovery (``new_room``), never a script beat — check
    # before the length gate so short patched doors are not mislabeled.
    if room_change_cutscene_disqualified(prev_state, new_state):
        return None
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
        if not story_use_menu_cutscene_exempt(new_state):
            return None
    if in_game_menu_from_ram(_ram_view_from_state(new_state or {})):
        if not story_use_menu_cutscene_exempt(new_state):
            return None

    if (
        examine_text_skip_disqualified(
            prev_state, new_state, skip_frames=int(skip_frames)
        )
        and not story_use_menu_cutscene_exempt(new_state)
    ):
        # Idle same-room examine / short msg: never pay. Cam-0 key exemptions
        # farmed interact→cutscene (live: +1.0 for 105:0:s0 at skip_frames=34).
        # Long idle-settle skips still clear via STORY_IDLE_SETTLE_MIN (Barry).
        return None

    if dining_tea_corridor_repeat_disqualified(
        key=key,
        prev_state=prev_state,
        new_state=new_state,
        rewarded_cutscenes=rewarded_cutscenes,
        visited_rooms=visited_rooms,
    ):
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
) -> str | None:
    """Human-readable reason when ``qualify_cutscene_reward`` returns None."""
    # Structural door vs script before the length gate (patched doors are short).
    if room_change_cutscene_disqualified(prev_state, new_state):
        return "room-change door skip (same-room scripts only)"
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
        if not story_use_menu_cutscene_exempt(new_state):
            return "pause menu at skip entry"
    if in_game_menu_from_ram(_ram_view_from_state(new_state or {})):
        if not story_use_menu_cutscene_exempt(new_state):
            return "pause menu at skip exit"
    if examine_text_skip_disqualified(
        prev_state, new_state, skip_frames=int(skip_frames)
    ):
        if not story_use_menu_cutscene_exempt(new_state):
            return "examine / locked text (same room, idle scene)"
    key = cutscene_key_from_state(
        prev_state, new_state, rewarded_cutscenes=rewarded_cutscenes
    )
    if key is not None and dining_tea_corridor_repeat_disqualified(
        key=key,
        prev_state=prev_state,
        new_state=new_state,
        rewarded_cutscenes=rewarded_cutscenes,
        visited_rooms=visited_rooms,
    ):
        return "dining<->tea room repeat (Kenneth / multi-cam door farm)"
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
        if why:
            lines.append(f"  unpaid_reason: {why}")
        elif proposed and str(proposed) in rewarded:
            lines.append(f"  unpaid_reason: duplicate key {proposed!r} this episode")
        elif qualified_key is None and not why:
            lines.append("  unpaid_reason: qualified key is None (no gate match)")
        else:
            lines.append("  unpaid_reason: (unknown)")
    return "\n".join(lines)
