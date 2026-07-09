"""Per-weapon attack macros for the unified ``attack`` action (index 9).

Dispatch:
  - **knife (0x01)** → crouch ``knife_macro`` (R1+down aim/swing/recovery)
  - **all other weapons** → standing ranged macro (R1 aim, R1+cross fire)

``knife_swing`` (index 8) still calls ``knife_macro`` directly from ``env``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from re1_rl.knife_macro import (
    KNIFE_AIM_GAME_FRAMES,
    KNIFE_FRAME_SCALE,
    KNIFE_RECOVERY_GAME_FRAMES,
    KNIFE_SWING_GAME_FRAMES,
    MIN_BUTTON_PHASE_FRAMES,
    _step_one_frame,
    execute_knife_macro,
    is_idle_recovery_latch,
    is_knife_animation_idle,
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

KNIFE_WEAPON_ID = 0x01

AIM_ANIM_RAISING = 0x12
AIM_ANIM_STABLE = 0x13
FIRE_ANIM = 0x14
GUN_AUX_TRACK = 0x03

AIM_BUTTONS = {"r1": True}
FIRE_BUTTONS = {"r1": True, "cross": True}

MAX_SETTLE_FRAMES = 20
MAX_AIM_FRAMES = 120
MIN_FIRE_HOLD_FRAMES = 6
MAX_FIRE_RECOVERY_FRAMES = 240
MAX_TAIL_FRAMES = 60

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
) -> bool:
    from re1_rl.ammo_accounting import can_fire_weapon

    if int(weapon_id) == KNIFE_WEAPON_ID:
        return True
    return can_fire_weapon(inventory, weapon_id)


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


def is_aim_stable(anim: int, aux: int, recovery: int) -> bool:
    """Public helper: gun stable aim OR knife crouch-aim ready (tests / masks)."""
    if is_gun_aim_stable(anim, aux, recovery):
        return True
    return recovery == 0 and aux == 0x04 and anim == AIM_ANIM_RAISING


def is_gun_attack_track(anim: int, aux: int) -> bool:
    if anim == 0 and aux == 0:
        return True
    return anim in (AIM_ANIM_RAISING, AIM_ANIM_STABLE, FIRE_ANIM, 0x15, 0x17) and aux in (
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
    }


def _execute_knife_attack_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
) -> tuple[bool, int, dict[str, Any]]:
    """Crouch knife path — never use standing R1-only gun logic on knife."""
    died, frames = execute_knife_macro(
        bridge,
        empty_sticky=empty_sticky,
        phases=(
            KNIFE_AIM_GAME_FRAMES,
            KNIFE_SWING_GAME_FRAMES,
            KNIFE_RECOVERY_GAME_FRAMES,
        ),
        scale=KNIFE_FRAME_SCALE,
        use_ram_gates=True,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    knife_report = getattr(bridge, "last_knife_anim_report", None) or {}
    report = _empty_report(weapon_id, weapon)
    report["macro_path"] = "knife_crouch"
    report["outcome"] = str(knife_report.get("outcome", "ok"))
    report["frames"] = int(frames)
    report["saw_fire_anim"] = report["outcome"] == "ok"
    report["knife_report"] = dict(knife_report)
    return died, int(frames), report


def _execute_ranged_attack_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int,
    episode_start_hp: int,
    weapon_id: int,
    weapon: str | None,
) -> tuple[bool, int, dict[str, Any]]:
    """Standing R1 aim + fire for beretta, shotgun, magnum, etc."""
    report = _empty_report(weapon_id, weapon)
    report["macro_path"] = f"ranged:{weapon or weapon_id}"

    max_aim, max_recovery = frame_budget(weapon or "")
    ammo_before = _ammo_count(bridge, weapon_id)
    neutral = {k: False for k in empty_sticky}
    total = 0
    trail: list[str] = []

    def _observe() -> tuple[int, int, int]:
        anim, aux, rec = read_knife_hooks(bridge)
        trail.append(f"f{total}:anim=0x{anim:02X} aux=0x{aux:02X} rec={rec}")
        if len(trail) > 16:
            trail.pop(0)
        return anim, aux, rec

    def _finish(outcome: str, died: bool) -> tuple[bool, int, dict[str, Any]]:
        report["outcome"] = outcome
        report["frames"] = total
        report["trail"] = list(trail)
        try:
            report["ammo_spent"] = max(0, ammo_before - _ammo_count(bridge, weapon_id))
        except (OSError, RuntimeError, KeyError, TypeError):
            pass
        return died, total, report

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

    settle_run = 0
    early_aim_run = 0
    settle_wait = 0
    aim_precooked = False
    entry_anim, entry_aux, entry_recovery = _observe()
    if is_gun_aim_stable(entry_anim, entry_aux, entry_recovery):
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
        if _step(AIM_BUTTONS):
            return _finish("death", True)
        aim_wait += 1
        anim, aux, rec = _observe()
        if not is_gun_attack_track(anim, aux):
            return _finish("aim_interrupt", False)
        stable_run = stable_run + 1 if is_gun_aim_stable(anim, aux, rec) else 0

    for _ in range(MIN_FIRE_HOLD_FRAMES):
        if _step(FIRE_BUTTONS):
            return _finish("death", True)
        anim, aux, _rec = _observe()
        if anim == FIRE_ANIM and aux == GUN_AUX_TRACK:
            report["saw_fire_anim"] = True

    rec_wait = 0
    while rec_wait < max_recovery:
        anim, aux, rec = _observe()
        if anim == FIRE_ANIM and aux == GUN_AUX_TRACK:
            report["saw_fire_anim"] = True
        if is_gun_aim_stable(anim, aux, rec) and report["saw_fire_anim"]:
            break
        if anim == 0 and aux == 0 and rec == 0 and rec_wait > MIN_BUTTON_PHASE_FRAMES:
            break
        if not is_gun_attack_track(anim, aux):
            return _finish("recovery_interrupt", False)
        if _step(AIM_BUTTONS):
            return _finish("death", True)
        rec_wait += 1
    else:
        return _finish("recovery_timeout", False)

    tail = 0
    while tail < MAX_TAIL_FRAMES:
        anim, aux, rec = _observe()
        if anim == 0 and aux == 0 and rec == 0:
            break
        if _step(neutral):
            return _finish("death", True)
        tail += 1

    return _finish("ok", False)


# Per-weapon dispatch for the ``attack`` action. Knife is crouch; all PS1
# ranged weapons share standing gun logic with per-name frame budgets.
_WEAPON_ATTACK_HANDLERS: dict[int, Callable[..., tuple[bool, int, dict[str, Any]]]] = {
    KNIFE_WEAPON_ID: _execute_knife_attack_macro,
}
for _wid in WEAPON_ITEM_IDS:
    if _wid != KNIFE_WEAPON_ID:
        _WEAPON_ATTACK_HANDLERS[_wid] = _execute_ranged_attack_macro


def execute_attack_macro(
    bridge: Any,
    *,
    empty_sticky: dict[str, bool],
    prev_hp: int = 0,
    episode_start_hp: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Dispatch to the weapon-specific attack macro."""
    weapon_id = read_equipped_weapon(bridge)
    weapon = equipped_weapon_name(weapon_id)
    if weapon is None or weapon_id not in WEAPON_ITEM_IDS:
        report = _empty_report(weapon_id, weapon)
        report["outcome"] = "no_weapon"
        return False, 0, report

    handler = _WEAPON_ATTACK_HANDLERS.get(weapon_id, _execute_ranged_attack_macro)
    return handler(
        bridge,
        empty_sticky=empty_sticky,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        weapon_id=weapon_id,
        weapon=weapon,
    )
