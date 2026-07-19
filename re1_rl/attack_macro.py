"""Per-weapon attack macros for height-synced discrete combat actions.

Discrete actions (env):
  - ``attack`` (9)      — **neutral** aim for every weapon
  - ``attack_up`` (6)  — **high** (R1+Up) for every weapon (old quickturn slot)
  - ``attack_down`` (45) — **crouch / floor** (R1+Down) for every weapon

Weapon paths:
  - **knife** — standing R1 (neutral), R1+Up (high), crouch R1+Down (down)
  - **guns**  — standing R1 (neutral), R1+Up (high), R1+Down floor-aim (down)

``knife_swing`` (index 8) still calls crouch ``knife_macro`` directly from ``env``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from re1_rl.frame_ring import FrameRingBuffer
from re1_rl.knife_macro import (
    CROUCH_KNIFE_ACTIVE_AUX,
    MIN_BUTTON_PHASE_FRAMES,
    _step_one_frame,
    is_idle_recovery_latch,
    is_knife_animation_idle,
    is_knife_slash_anim,
    is_knife_swing_recovery_tail,
    is_standing_pre_knife_idle,
    is_standing_recovery_latch,
    read_knife_hooks,
)
from re1_rl.memory_map import (
    EQUIPPED_WEAPON_ID,
    INVENTORY_BASE,
    INVENTORY_SLOTS,
    ITEM_IDS,
    WEAPON_ITEM_IDS,
)
from re1_rl.sticky_input import STICKY_KEYS

KNIFE_WEAPON_ID = 0x01
SHOTGUN_WEAPON_ID = 0x03

AIM_ANIM_RAISING = 0x12
AIM_ANIM_STABLE = 0x13
FIRE_ANIM = 0x14
GUN_AUX_TRACK = 0x03

# Standing gun pad only — never up/down/left/right (RE1: R1+Down = floor aim).
RANGED_FACE_KEYS = frozenset({"r1", "cross"})

AIM_BUTTONS = {"r1": True}
FIRE_BUTTONS = {"r1": True, "cross": True}


def standing_gun_buttons(buttons: dict[str, bool] | None) -> dict[str, bool]:
    """Keep only standing-gun face buttons; strip aim-up / aim-down / strafe."""
    if not buttons:
        return {}
    return {k: True for k in RANGED_FACE_KEYS if buttons.get(k)}


def cleared_movement_sticky(sticky: dict[str, bool] | None = None) -> dict[str, bool]:
    """All direction/run latches off — ranged macros must not inherit walk/aim-down."""
    out = {k: False for k in STICKY_KEYS}
    if sticky:
        for k, v in sticky.items():
            if k not in STICKY_KEYS:
                out[k] = bool(v) and k in RANGED_FACE_KEYS
    return out

MAX_SETTLE_FRAMES = 20
MAX_AIM_FRAMES = 120
MIN_FIRE_HOLD_FRAMES = 6
SHOTGUN_RECOVERY_PAD_FRAMES = MIN_BUTTON_PHASE_FRAMES
MAX_FIRE_RECOVERY_FRAMES = 240
UP_AIM_MAX_FRAMES = 80
UP_FIRE_MAX_FRAMES = 12
UP_RECOVERY_MAX_FRAMES = 120
KNIFE_UP_CROSS_FRAMES = 5
KNIFE_UP_RELEASE_STAGE_FRAMES = 4
KNIFE_UP_AIM_FRAMES = 24

_FRAME_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "weapon_frame_data.json"
_frame_data_cache: dict[str, Any] | None = None


def weapon_frame_data() -> dict[str, Any]:
    global _frame_data_cache
    if _frame_data_cache is None:
        if _FRAME_DATA_PATH.is_file():
            with _FRAME_DATA_PATH.open(encoding="utf-8") as f:
                raw = json.load(f)
            _frame_data_cache = {k: v for k, v in raw.items() if not k.startswith("_")}
        else:
            _frame_data_cache = {}
    return _frame_data_cache


def frame_budget(weapon_name: str) -> tuple[int, int]:
    """(max_aim_frames, max_recovery_frames) for a ranged weapon, padded 2x."""
    data = weapon_frame_data().get(weapon_name)
    if not data:
        return MAX_AIM_FRAMES, MAX_FIRE_RECOVERY_FRAMES
    aim = data.get("frames_to_stable_aim")
    rec = data.get("fire_recovery_frames")
    max_aim = max(int(aim) * 2, 40) if aim else MAX_AIM_FRAMES
    max_rec = max(int(rec) * 2, 60) if rec else MAX_FIRE_RECOVERY_FRAMES
    return max_aim, max_rec


def read_equipped_weapon(bridge: Any) -> int:
    raw = bridge.read_ram([("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8")])
    return int(raw["equipped_weapon_id"])


def equipped_weapon_name(weapon_id: int) -> str | None:
    if weapon_id in WEAPON_ITEM_IDS:
        return ITEM_IDS.get(weapon_id)
    return None


def attack_possible(weapon_id: int) -> bool:
    return weapon_id in WEAPON_ITEM_IDS


def can_attack_with_ammo(
    inventory: list[tuple[int, int]],
    weapon_id: int,
    equipped_slot_0based: int | None = None,
) -> bool:
    from re1_rl.ammo_accounting import can_fire_from_equipped_slot

    if int(weapon_id) == KNIFE_WEAPON_ID:
        return True
    return can_fire_from_equipped_slot(
        inventory, weapon_id, equipped_slot_0based
    )


def _ammo_count(bridge: Any, weapon_id: int) -> int:
    fields = [
        (f"inv_slot_{i}", INVENTORY_BASE + 2 * i, "u16")
        for i in range(INVENTORY_SLOTS)
    ]
    ram = bridge.read_ram(fields)
    total = 0
    for i in range(INVENTORY_SLOTS):
        raw = int(ram.get(f"inv_slot_{i}", 0))
        if raw & 0xFF == weapon_id:
            total += raw >> 8
    return total


def is_gun_aim_stable(anim: int, aux: int, recovery: int) -> bool:
    """Stable standing fire window for ranged weapons (aux 0x03 track)."""
    return recovery == 0 and aux == GUN_AUX_TRACK and anim == AIM_ANIM_STABLE


def is_gun_link_ready(anim: int, aux: int, recovery: int) -> bool:
    """Post-shot recovery is over; a following macro may buffer its next input."""
    return recovery == 0 and aux == GUN_AUX_TRACK and anim in (
        AIM_ANIM_STABLE,
        FIRE_ANIM,
        0x16,
    )


def is_aim_stable(anim: int, aux: int, recovery: int) -> bool:
    """Public helper: gun stable aim OR knife crouch-aim ready (tests / masks)."""
    if is_gun_aim_stable(anim, aux, recovery):
        return True
    return recovery == 0 and aux == 0x04 and anim == AIM_ANIM_RAISING


def is_gun_attack_track(anim: int, aux: int) -> bool:
    if anim == 0 and aux == 0:
        return True
    return anim in (AIM_ANIM_RAISING, AIM_ANIM_STABLE, FIRE_ANIM, 0x15, 0x16, 0x17) and aux in (
        0,
        GUN_AUX_TRACK,
    )


def is_ranged_settle_complete(anim: int, aux: int, recovery: int) -> bool:
    if is_knife_animation_idle(anim, aux, recovery):
        return True
    if is_standing_pre_knife_idle(anim, aux, recovery):
        return True
    if is_gun_aim_stable(anim, aux, recovery):
        return True
    return anim == AIM_ANIM_RAISING and aux == GUN_AUX_TRACK and recovery == 0


def is_attack_settle_complete(anim: int, aux: int, recovery: int) -> bool:
    return is_ranged_settle_complete(anim, aux, recovery)


def is_ranged_settle_wait_state(anim: int, aux: int, recovery: int) -> bool:
    if is_ranged_settle_complete(anim, aux, recovery):
        return True
    if is_idle_recovery_latch(anim, aux, recovery):
        return True
    if is_standing_recovery_latch(anim, aux, recovery):
        return True
    if anim == 0x06 and aux == 0:
        return True
    return is_gun_attack_track(anim, aux)


def is_attack_settle_wait_state(anim: int, aux: int, recovery: int) -> bool:
    return is_ranged_settle_wait_state(anim, aux, recovery)


def is_attack_track(anim: int, aux: int) -> bool:
    return is_gun_attack_track(anim, aux)


def _empty_report(weapon_id: int, weapon: str | None) -> dict[str, Any]:
    return {
        "outcome": "ok",
        "weapon": weapon,
        "weapon_id": weapon_id,
        "ammo_spent": 0,
        "frames": 0,
        "saw_fire_anim": False,
        "trail": [],
        "macro_path": None,
        "link_aim_held": False,
    }


def _execute_knife_attack_crouch_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
) -> tuple[bool, int, dict[str, Any]]:
    """Crouch knife (R1+Down) with buffered Cross pulses for linked attacks."""
    return _execute_standing_knife_height_macro(
        bridge,
        empty_sticky=empty_sticky,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        weapon_id=weapon_id,
        weapon=weapon,
        aim_buttons={"r1": True, "down": True},
        swing_buttons={"r1": True, "down": True, "cross": True},
        macro_path="knife_crouch",
        aim_mode="down",
    )


def _execute_standing_knife_height_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
    aim_buttons: dict[str, bool],
    swing_buttons: dict[str, bool],
    macro_path: str,
    aim_mode: str,
) -> tuple[bool, int, dict[str, Any]]:
    """Standing knife at a height: aim hold, RAM-gated Cross until 0x14 slash."""
    report = _empty_report(weapon_id, weapon)
    report["macro_path"] = macro_path
    report["aim_mode"] = aim_mode
    total = 0
    trail: list[str] = []
    max_aim = max(KNIFE_UP_AIM_FRAMES, 32)
    max_swing = max(KNIFE_UP_CROSS_FRAMES * 24, 96)
    entry_hooks = read_knife_hooks(bridge)
    entry_slash = is_knife_slash_anim(*entry_hooks)
    linked_entry = entry_hooks[1] == CROUCH_KNIFE_ACTIVE_AUX and entry_hooks[0] in (
        0x13,
        FIRE_ANIM,
        0x15,
    )
    old_slash_cleared = not entry_slash

    def _observe() -> tuple[int, int, int]:
        anim, aux, rec = read_knife_hooks(bridge)
        trail.append(f"f{total}:anim=0x{anim:02X} aux=0x{aux:02X} rec={rec}")
        if len(trail) > 16:
            trail.pop(0)
        return anim, aux, rec

    def _step(buttons: dict[str, bool]) -> bool:
        nonlocal total
        died = _step_one_frame(
            bridge,
            buttons,
            empty_sticky=empty_sticky,
            echo_joypad=False,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        total += 1
        return died

    def _finish(outcome: str, died: bool) -> tuple[bool, int, dict[str, Any]]:
        report["frames"] = total
        report["trail"] = list(trail)
        report["outcome"] = outcome
        return died, total, report

    aim_frames = MIN_BUTTON_PHASE_FRAMES if linked_entry and not entry_slash else max_aim
    for _ in range(aim_frames):
        if _step(aim_buttons):
            return _finish("death", True)
        anim, aux, rec = _observe()
        if not is_knife_slash_anim(anim, aux, rec):
            old_slash_cleared = True
            if linked_entry:
                break

    saw_slash = False
    saw_positive_recovery = False
    cross_on_frames = MIN_FIRE_HOLD_FRAMES
    cross_off_frames = MIN_BUTTON_PHASE_FRAMES * 2
    pulse_period = cross_on_frames + cross_off_frames
    for pulse_frame in range(max_swing):
        buttons = (
            swing_buttons
            if pulse_frame % pulse_period < cross_on_frames
            else aim_buttons
        )
        if _step(buttons):
            return _finish("death", True)
        anim, aux, rec = _observe()
        slash = is_knife_slash_anim(anim, aux, rec)
        if not slash:
            old_slash_cleared = True
        if old_slash_cleared and slash:
            report["saw_fire_anim"] = True
            saw_slash = True
            saw_positive_recovery = rec > 0
            break
    if not saw_slash:
        return _finish("slash_timeout", False)

    # Release Cross and height, but never drop R1 between linked knife attacks.
    for _ in range(KNIFE_UP_RELEASE_STAGE_FRAMES):
        if _step(aim_buttons):
            return _finish("death", True)
        _observe()

    stable = 0
    for _ in range(UP_RECOVERY_MAX_FRAMES):
        if _step({"r1": True}):
            return _finish("death", True)
        anim, aux, rec = _observe()
        saw_positive_recovery = saw_positive_recovery or rec > 0
        if saw_positive_recovery and rec == 0:
            break
        ready_fallback = rec == 0 and not is_knife_slash_anim(anim, aux, rec)
        stable = stable + 1 if ready_fallback else 0
        if stable >= MIN_BUTTON_PHASE_FRAMES:
            break
    else:
        return _finish("recovery_timeout", False)

    report["link_aim_held"] = True
    return _finish("ok", False)


def _execute_knife_attack_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
) -> tuple[bool, int, dict[str, Any]]:
    """Standing-neutral knife: R1 aim + R1+Cross slash (no up/down)."""
    return _execute_standing_knife_height_macro(
        bridge,
        empty_sticky=empty_sticky,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        weapon_id=weapon_id,
        weapon=weapon,
        aim_buttons={"r1": True},
        swing_buttons={"r1": True, "cross": True},
        macro_path="knife_neutral",
        aim_mode="neutral",
    )


def _bridge_uses_frame_ring(bridge: Any) -> bool:
    return isinstance(getattr(bridge, "frame_ring", None), FrameRingBuffer)


def is_macro_swing_anim(anim: int, aux: int, recovery: int = 0) -> bool:
    """True when knife slash or gun fire anim bytes are active."""
    from re1_rl.knife_macro import is_knife_slash_anim

    if is_knife_slash_anim(anim, aux, recovery):
        return True
    return anim == FIRE_ANIM and aux == GUN_AUX_TRACK


def macro_swing_frame(bridge: Any) -> bool:
    """True on the emulated frame where knife slash or gun fire anim is active."""
    return is_macro_swing_anim(*read_knife_hooks(bridge))


def _execute_ranged_attack_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
    recovery_padding_frames: int = 0,
    macro_path: str | None = None,
) -> tuple[bool, int, dict[str, Any]]:
    """Standing R1 aim + fire for beretta, shotgun, magnum, etc.

    Robust to aim-down / sticky movement: directions are stripped from every
    pad frame and movement sticky is force-cleared so R1+Down cannot floor-aim.
    """
    report = _empty_report(weapon_id, weapon)
    report["macro_path"] = macro_path or f"ranged:{weapon or weapon_id}"

    # Never inherit walk / aim-down latch from the prior env step.
    empty_sticky = cleared_movement_sticky(empty_sticky)
    aim_pad = standing_gun_buttons(AIM_BUTTONS)
    fire_pad = standing_gun_buttons(FIRE_BUTTONS)

    max_aim, max_recovery = frame_budget(weapon or "")
    max_recovery += max(int(recovery_padding_frames), 0)
    ammo_before = _ammo_count(bridge, weapon_id)
    neutral: dict[str, bool] = {}
    total = 0
    trail: list[str] = []

    def _observe() -> tuple[int, int, int]:
        anim, aux, rec = read_knife_hooks(bridge)
        trail.append(f"f{total}:anim=0x{anim:02X} aux=0x{aux:02X} rec={rec}")
        if len(trail) > 16:
            trail.pop(0)
        return anim, aux, rec

    def _finish(outcome: str, died: bool) -> tuple[bool, int, dict[str, Any]]:
        report["frames"] = total
        report["trail"] = list(trail)
        spent = 0
        try:
            spent = max(0, ammo_before - _ammo_count(bridge, weapon_id))
        except (OSError, RuntimeError, KeyError, TypeError):
            pass
        report["ammo_spent"] = spent
        # Floor-aim / jammed pad can flash fire anim (0x14) without spending a
        # round — never grade that as a successful gun attack.
        if outcome == "ok" and spent <= 0:
            report["outcome"] = "dry_fire"
        else:
            report["outcome"] = outcome
        return died, total, report

    def _step(buttons: dict[str, bool]) -> bool:
        nonlocal total
        died = _step_one_frame(
            bridge,
            standing_gun_buttons(buttons),
            empty_sticky=empty_sticky,
            echo_joypad=False,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        total += 1
        return died

    settle_run = 0
    early_aim_run = 0
    settle_wait = 0
    aim_precooked = False
    entry_anim, entry_aux, entry_recovery = _observe()
    if is_gun_link_ready(entry_anim, entry_aux, entry_recovery):
        aim_precooked = True
        settle_run = MIN_BUTTON_PHASE_FRAMES
    while settle_run < MIN_BUTTON_PHASE_FRAMES and early_aim_run < MIN_BUTTON_PHASE_FRAMES:
        if settle_wait >= MAX_SETTLE_FRAMES:
            return _finish("settle_timeout", False)
        if _step(neutral):
            return _finish("death", True)
        settle_wait += 1
        anim, aux, rec = _observe()
        if not is_ranged_settle_wait_state(anim, aux, rec):
            return _finish("settle_interrupt", False)
        if is_gun_aim_stable(anim, aux, rec):
            early_aim_run += 1
            settle_run = 0
            if early_aim_run >= MIN_BUTTON_PHASE_FRAMES:
                aim_precooked = True
        elif is_ranged_settle_complete(anim, aux, rec):
            settle_run += 1
            early_aim_run = 0
        else:
            settle_run = 0
            early_aim_run = 0

    if aim_precooked:
        stable_run = MIN_BUTTON_PHASE_FRAMES
        aim_wait = 0
    else:
        stable_run = 0
        aim_wait = 0
    while stable_run < MIN_BUTTON_PHASE_FRAMES:
        if aim_wait >= max_aim:
            return _finish("aim_timeout", False)
        if _step(aim_pad):
            return _finish("death", True)
        aim_wait += 1
        anim, aux, rec = _observe()
        if not is_gun_attack_track(anim, aux):
            return _finish("aim_interrupt", False)
        stable_run = stable_run + 1 if is_gun_aim_stable(anim, aux, rec) else 0

    for _ in range(MAX_AIM_FRAMES):
        if _step(fire_pad):
            return _finish("death", True)
        anim, aux, _rec = _observe()
        if anim == FIRE_ANIM and aux == GUN_AUX_TRACK:
            report["saw_fire_anim"] = True
        if _ammo_count(bridge, weapon_id) < ammo_before:
            report["saw_fire_anim"] = True
            break
    else:
        return _finish("ok", False)

    rec_wait = 0
    saw_positive_recovery = False
    while rec_wait < max_recovery:
        anim, aux, rec = _observe()
        if anim == FIRE_ANIM and aux == GUN_AUX_TRACK:
            report["saw_fire_anim"] = True
        saw_positive_recovery = saw_positive_recovery or rec > 0
        if (
            report["saw_fire_anim"]
            and saw_positive_recovery
            and is_gun_link_ready(anim, aux, rec)
        ):
            break
        if is_gun_aim_stable(anim, aux, rec) and report["saw_fire_anim"]:
            break
        if anim == 0 and aux == 0 and rec == 0 and rec_wait > MIN_BUTTON_PHASE_FRAMES:
            break
        if not is_gun_attack_track(anim, aux):
            return _finish("recovery_interrupt", False)
        if _step(aim_pad):
            return _finish("death", True)
        rec_wait += 1
    else:
        return _finish("recovery_timeout", False)

    report["link_aim_held"] = True
    return _finish("ok", False)


def _execute_shotgun_attack_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
) -> tuple[bool, int, dict[str, Any]]:
    """Shotgun recovery reaches stable aim at the generic 2x budget boundary."""
    return _execute_ranged_attack_macro(
        bridge,
        empty_sticky=empty_sticky,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        weapon_id=weapon_id,
        weapon=weapon,
        recovery_padding_frames=SHOTGUN_RECOVERY_PAD_FRAMES,
        macro_path="shotgun_ranged",
    )


def _execute_ranged_attack_up_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
) -> tuple[bool, int, dict[str, Any]]:
    """R1+Up shot with staged Cross, Up, then R1 release."""
    report = _empty_report(weapon_id, weapon)
    report["macro_path"] = f"ranged_up:{weapon or weapon_id}"
    report["aim_mode"] = "up"
    ammo_before = _ammo_count(bridge, weapon_id)
    total = 0
    trail: list[str] = []
    up_aim = {"r1": True, "up": True}
    up_fire = {"r1": True, "up": True, "cross": True}

    def _observe() -> tuple[int, int, int]:
        anim, aux, rec = read_knife_hooks(bridge)
        trail.append(f"f{total}:anim=0x{anim:02X} aux=0x{aux:02X} rec={rec}")
        if len(trail) > 16:
            trail.pop(0)
        return anim, aux, rec

    def _step(buttons: dict[str, bool]) -> bool:
        nonlocal total
        died = _step_one_frame(
            bridge,
            buttons,
            empty_sticky=empty_sticky,
            echo_joypad=False,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        total += 1
        return died

    def _finish(outcome: str, died: bool) -> tuple[bool, int, dict[str, Any]]:
        spent = max(0, ammo_before - _ammo_count(bridge, weapon_id))
        report["frames"] = total
        report["trail"] = list(trail)
        report["ammo_spent"] = spent
        report["outcome"] = "dry_fire" if outcome == "ok" and spent <= 0 else outcome
        return died, total, report

    stable = 0
    for _ in range(UP_AIM_MAX_FRAMES):
        if _step(up_aim):
            return _finish("death", True)
        anim, aux, rec = _observe()
        stable = stable + 1 if is_gun_aim_stable(anim, aux, rec) else 0
        if stable >= MIN_BUTTON_PHASE_FRAMES:
            break
    else:
        return _finish("aim_timeout", False)

    for _ in range(UP_FIRE_MAX_FRAMES):
        if _step(up_fire):
            return _finish("death", True)
        anim, aux, _rec = _observe()
        if anim == FIRE_ANIM and aux == GUN_AUX_TRACK:
            report["saw_fire_anim"] = True
        if _ammo_count(bridge, weapon_id) < ammo_before:
            break
    else:
        return _finish("ammo_timeout", False)

    stable = 0
    saw_positive_recovery = False
    for _ in range(UP_RECOVERY_MAX_FRAMES):
        if _step(up_aim):
            return _finish("death", True)
        anim, aux, rec = _observe()
        if anim == FIRE_ANIM and aux == GUN_AUX_TRACK:
            report["saw_fire_anim"] = True
        saw_positive_recovery = saw_positive_recovery or rec > 0
        if (
            report["saw_fire_anim"]
            and saw_positive_recovery
            and is_gun_link_ready(anim, aux, rec)
        ):
            break
        stable = stable + 1 if is_gun_aim_stable(anim, aux, rec) else 0
        if report["saw_fire_anim"] and stable >= MIN_BUTTON_PHASE_FRAMES:
            break
    else:
        return _finish("recovery_timeout", False)

    # Release Up immediately at the recovery-zero boundary; keep aim for linking.
    if _step({"r1": True}):
        return _finish("death", True)
    _observe()

    report["link_aim_held"] = True
    return _finish("ok", False)


def _execute_knife_attack_up_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
) -> tuple[bool, int, dict[str, Any]]:
    """R1+Up high knife slash (RAM-gated Cross until 0x14)."""
    return _execute_standing_knife_height_macro(
        bridge,
        empty_sticky=empty_sticky,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        weapon_id=weapon_id,
        weapon=weapon,
        aim_buttons={"r1": True, "up": True},
        swing_buttons={"r1": True, "up": True, "cross": True},
        macro_path="knife_up",
        aim_mode="up",
    )


def _execute_ranged_attack_down_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
) -> tuple[bool, int, dict[str, Any]]:
    """R1+Down floor-aim shot with staged Cross, Down, then R1 release."""
    report = _empty_report(weapon_id, weapon)
    report["macro_path"] = f"ranged_down:{weapon or weapon_id}"
    report["aim_mode"] = "down"
    ammo_before = _ammo_count(bridge, weapon_id)
    total = 0
    trail: list[str] = []
    down_aim = {"r1": True, "down": True}
    down_fire = {"r1": True, "down": True, "cross": True}

    def _observe() -> tuple[int, int, int]:
        anim, aux, rec = read_knife_hooks(bridge)
        trail.append(f"f{total}:anim=0x{anim:02X} aux=0x{aux:02X} rec={rec}")
        if len(trail) > 16:
            trail.pop(0)
        return anim, aux, rec

    def _step(buttons: dict[str, bool]) -> bool:
        nonlocal total
        died = _step_one_frame(
            bridge,
            buttons,
            empty_sticky=empty_sticky,
            echo_joypad=False,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        total += 1
        return died

    def _finish(outcome: str, died: bool) -> tuple[bool, int, dict[str, Any]]:
        spent = max(0, ammo_before - _ammo_count(bridge, weapon_id))
        report["frames"] = total
        report["trail"] = list(trail)
        report["ammo_spent"] = spent
        report["outcome"] = "dry_fire" if outcome == "ok" and spent <= 0 else outcome
        return died, total, report

    # R1 preamble then add Down — matches live crouch-entry probe (r1_first).
    for _ in range(MIN_BUTTON_PHASE_FRAMES * 2):
        if _step({"r1": True}):
            return _finish("death", True)
        _observe()

    stable = 0
    for _ in range(UP_AIM_MAX_FRAMES):
        if _step(down_aim):
            return _finish("death", True)
        anim, aux, rec = _observe()
        stable = stable + 1 if is_gun_aim_stable(anim, aux, rec) else 0
        if stable >= MIN_BUTTON_PHASE_FRAMES:
            break
    else:
        # Floor-aim may stay on raising (0x12) — still allow fire after hold.
        pass

    for _ in range(max(UP_FIRE_MAX_FRAMES * 4, MIN_FIRE_HOLD_FRAMES * 8)):
        if _step(down_fire):
            return _finish("death", True)
        anim, aux, _rec = _observe()
        if anim == FIRE_ANIM and aux == GUN_AUX_TRACK:
            report["saw_fire_anim"] = True
        if _ammo_count(bridge, weapon_id) < ammo_before:
            report["saw_fire_anim"] = True
            break
    else:
        return _finish("ammo_timeout", False)

    saw_positive_recovery = False
    for _ in range(UP_RECOVERY_MAX_FRAMES):
        if _step(down_aim):
            return _finish("death", True)
        anim, aux, rec = _observe()
        if anim == FIRE_ANIM and aux == GUN_AUX_TRACK:
            report["saw_fire_anim"] = True
        saw_positive_recovery = saw_positive_recovery or rec > 0
        if (
            report["saw_fire_anim"]
            and saw_positive_recovery
            and is_gun_link_ready(anim, aux, rec)
        ):
            break
        if is_gun_aim_stable(anim, aux, rec) or (anim == 0 and aux == 0 and rec == 0):
            if report["saw_fire_anim"]:
                break

    # Release Down immediately at the recovery-zero boundary; keep aim for linking.
    if _step({"r1": True}):
        return _finish("death", True)
    _observe()

    report["link_aim_held"] = True
    return _finish("ok", False)


# Per-weapon dispatch for ``attack`` (neutral). Knife is standing; guns stand.
_WEAPON_ATTACK_HANDLERS: dict[int, Callable[..., tuple[bool, int, dict[str, Any]]]] = {
    KNIFE_WEAPON_ID: _execute_knife_attack_macro,
    SHOTGUN_WEAPON_ID: _execute_shotgun_attack_macro,
}
for _wid in WEAPON_ITEM_IDS:
    if _wid not in (KNIFE_WEAPON_ID, SHOTGUN_WEAPON_ID):
        _WEAPON_ATTACK_HANDLERS[_wid] = _execute_ranged_attack_macro


def execute_attack_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int = 0,
    episode_start_hp: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Dispatch neutral (standing) attack for the equipped weapon."""
    empty_sticky = cleared_movement_sticky(empty_sticky)
    pins = getattr(bridge, "attack_pins", None)
    if pins is not None and _bridge_uses_frame_ring(bridge):
        pins.begin(bridge)
    try:
        weapon_id = read_equipped_weapon(bridge)
        weapon = equipped_weapon_name(weapon_id)
        if weapon is None or weapon_id not in WEAPON_ITEM_IDS:
            report = _empty_report(weapon_id, weapon)
            report["outcome"] = "no_weapon"
            report["aim_mode"] = "neutral"
            return False, 0, report

        handler = _WEAPON_ATTACK_HANDLERS.get(weapon_id, _execute_ranged_attack_macro)
        died, frames, report = handler(
            bridge,
            empty_sticky=empty_sticky,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            weapon_id=weapon_id,
            weapon=weapon,
        )
        report.setdefault("aim_mode", "neutral")
        return died, frames, report
    finally:
        if pins is not None and pins.active:
            pins.finish(bridge)


def execute_attack_up_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int = 0,
    episode_start_hp: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Dispatch the directional high attack for the equipped weapon."""
    empty_sticky = cleared_movement_sticky(empty_sticky)
    pins = getattr(bridge, "attack_pins", None)
    if pins is not None and _bridge_uses_frame_ring(bridge):
        pins.begin(bridge)
    try:
        weapon_id = read_equipped_weapon(bridge)
        weapon = equipped_weapon_name(weapon_id)
        if weapon is None or weapon_id not in WEAPON_ITEM_IDS:
            report = _empty_report(weapon_id, weapon)
            report["outcome"] = "no_weapon"
            report["aim_mode"] = "up"
            return False, 0, report
        handler = (
            _execute_knife_attack_up_macro
            if weapon_id == KNIFE_WEAPON_ID
            else _execute_ranged_attack_up_macro
        )
        return handler(
            bridge,
            empty_sticky=empty_sticky,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            weapon_id=weapon_id,
            weapon=weapon,
        )
    finally:
        if pins is not None and pins.active:
            pins.finish(bridge)


def execute_attack_down_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int = 0,
    episode_start_hp: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Dispatch crouch / floor-aim attack for the equipped weapon."""
    empty_sticky = cleared_movement_sticky(empty_sticky)
    pins = getattr(bridge, "attack_pins", None)
    if pins is not None and _bridge_uses_frame_ring(bridge):
        pins.begin(bridge)
    try:
        weapon_id = read_equipped_weapon(bridge)
        weapon = equipped_weapon_name(weapon_id)
        if weapon is None or weapon_id not in WEAPON_ITEM_IDS:
            report = _empty_report(weapon_id, weapon)
            report["outcome"] = "no_weapon"
            report["aim_mode"] = "down"
            return False, 0, report
        handler = (
            _execute_knife_attack_crouch_macro
            if weapon_id == KNIFE_WEAPON_ID
            else _execute_ranged_attack_down_macro
        )
        return handler(
            bridge,
            empty_sticky=empty_sticky,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            weapon_id=weapon_id,
            weapon=weapon,
        )
    finally:
        if pins is not None and pins.active:
            pins.finish(bridge)
