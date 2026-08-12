"""Legal action masks for RE1 discrete control.

Action layout (env.ACTION_NAMES) — fixed size/names; do not reshape PPO:
  0-5   movement (curved run via sticky: run_forward + turn_*)
  6-8   attack_up / attack / attack_down
  9     interact
  10    use                — open USE menu; then select_slot_N (2-step)
  11    equip              — open EQUIP menu; then select_slot_N (2-step)
  12-19 deposit_slot_N     — while box UI open: 0=withdraw mode, 1=deposit mode,
                             2=close; 3-7 unused (deposit item pick uses select_slot)
  20-35 withdraw_box_N     — pick box source slot (box UI withdraw phase)
  36    combine            — open COMBINE menu; select_slot x2 (3-step)
  37-44 select_slot_N      — shared slot pick (use / equip / combine / box deposit)
"""

from __future__ import annotations

import numpy as np

from re1_rl.ammo_accounting import can_fire_from_equipped_slot
from re1_rl.item_box import is_typewriter_or_box_room
from re1_rl.item_use import any_legal_use_slot, slot_legal_for_use
from re1_rl.story_item_use import (
    any_legal_story_use_slot,
    legal_story_use_slots,
    slot_legal_for_story_use,
)
from re1_rl.attack_macro import (
    AIM_ANIM_RAISING,
    AIM_ANIM_STABLE,
    FIRE_ANIM,
    GUN_AUX_TRACK,
)
from re1_rl.knife_macro import (
    CROUCH_KNIFE_ACTIVE_AUX,
    CROUCH_KNIFE_AIM_ANIM,
    knife_action_ready,
    knife_crouch_action_ready,
    is_crouch_knife_aim_ready,
    is_knife_mid_swing_state,
)
from re1_rl.weapon_equip import (
    EQUIPPABLE_WEAPON_IDS,
    any_legal_equip_slot,
    slot_legal_for_equip,
)

ATTACK_UP_ACTION = 6
ATTACK_ACTION = 7
ATTACK_DOWN_ACTION = 8
INTERACT_ACTION = 9
USE_ACTION = 10
EQUIP_ACTION = 11
DEPOSIT_ACTION_BASE = EQUIP_ACTION + 1  # 12
N_DEPOSIT_ACTIONS = 8
WITHDRAW_ACTION_BASE = DEPOSIT_ACTION_BASE + N_DEPOSIT_ACTIONS  # 20
N_WITHDRAW_ACTIONS = 16
COMBINE_ACTION = WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS  # 36
SELECT_SLOT_BASE = COMBINE_ACTION + 1  # 37
N_SELECT_SLOT = 8

# Main Hall 1F (106) and 2F upper hall (203): attack macros always illegal.
ALWAYS_ILLEGAL_ATTACK_ROOMS: frozenset[str] = frozenset({"106", "203"})


def is_always_illegal_attack_room(room_id: str | None) -> bool:
    if room_id is None:
        return False
    return str(room_id).strip().upper() in ALWAYS_ILLEGAL_ATTACK_ROOMS


# Box-UI mode verbs reuse never-trained deposit_slot indices (names unchanged).
BOX_WITHDRAW_ACTION = DEPOSIT_ACTION_BASE + 0  # deposit_slot_0
BOX_DEPOSIT_ACTION = DEPOSIT_ACTION_BASE + 1  # deposit_slot_1
BOX_CLOSE_ACTION = DEPOSIT_ACTION_BASE + 2  # deposit_slot_2
BOX_BANK_BOSS_ACTION = DEPOSIT_ACTION_BASE + 3  # deposit_slot_3 — room-100 boss bank preset

# box_phase: 0 = choose withdraw/deposit/close; 1 = pick box slot; 2 = pick inv slot
BOX_PHASE_CHOOSE = 0
BOX_PHASE_WITHDRAW_SLOT = 1
BOX_PHASE_DEPOSIT_SLOT = 2

KNIFE_ID = 0x01

DEPOSIT_ACTION_NAMES = [f"deposit_slot_{i}" for i in range(N_DEPOSIT_ACTIONS)]
WITHDRAW_ACTION_NAMES = [f"withdraw_box_{i}" for i in range(N_WITHDRAW_ACTIONS)]
MENU_ACTION_NAMES = ["combine"] + [
    f"select_slot_{i}" for i in range(N_SELECT_SLOT)
]

# Tank controls, runs, and interact are allowed while story USE is pending so
# Jill can turn toward the interact point after opening the USE submenu.
# Combat macros are deliberately excluded.
_MOVEMENT_ACTION_COUNT = 6
_STORY_USE_RECOVERY_ACTIONS = frozenset((*range(_MOVEMENT_ACTION_COUNT), INTERACT_ACTION))

# Document / file examine overlay (doom books, botany book): mash directions + Cross.
_DOCUMENT_EXAMINE_ACTIONS = frozenset({0, 1, 2, 3, INTERACT_ACTION})


def _height_attack_legal(
    *,
    anim_ready: bool,
    equipped_weapon_id: int | None,
    equipped_slot_0based: int | None,
    inventory: list[tuple[int, int]] | None,
    mask_combat_without_enemies: bool,
    knife_enemies: int | None,
    gun_enemies: int | None,
    alive_enemies_in_room: int | None,
) -> bool:
    """Shared legality for attack / attack_up / attack_down (weapon-dispatched macros)."""
    # Missing equipped-weapon RAM is not an invitation to attack.
    legal = anim_ready and equipped_weapon_id is not None
    wid: int | None = None
    if equipped_weapon_id is not None:
        wid = int(equipped_weapon_id)
        legal = legal and wid in EQUIPPABLE_WEAPON_IDS
        if legal and wid != KNIFE_ID and inventory is not None:
            legal = can_fire_from_equipped_slot(
                inventory, wid, equipped_slot_0based
            )
    if legal and mask_combat_without_enemies:
        if wid == KNIFE_ID:
            if knife_enemies is not None:
                legal = knife_enemies > 0
        elif gun_enemies is not None:
            legal = gun_enemies > 0
        elif alive_enemies_in_room is not None:
            legal = int(alive_enemies_in_room) > 0
    return legal


def _in_ranged_combat_pose(anim: int, aux: int, recovery: int) -> bool:
    """True when Jill is actively aiming or firing a gun (not neutral idle)."""
    if anim in (AIM_ANIM_RAISING, AIM_ANIM_STABLE, FIRE_ANIM, 0x15, 0x16, 0x17) and aux in (
        0,
        GUN_AUX_TRACK,
    ):
        return True
    return recovery > 0 and anim in (
        AIM_ANIM_RAISING,
        AIM_ANIM_STABLE,
        FIRE_ANIM,
        0x15,
        0x16,
        0x17,
    ) and aux in (0, GUN_AUX_TRACK)


def _in_knife_combat_pose(anim: int, aux: int, recovery: int) -> bool:
    """True when Jill is mid knife slash or crouch-knife aim."""
    if is_knife_mid_swing_state(anim, aux, recovery):
        return True
    if is_crouch_knife_aim_ready(anim, aux, recovery):
        return True
    return anim == CROUCH_KNIFE_AIM_ANIM and aux == CROUCH_KNIFE_ACTIVE_AUX


def menu_action_ready(
    anim: int,
    aux: int,
    recovery: int,
    *,
    equipped_weapon_id: int | None,
) -> bool:
    """Stricter than knife_action_ready — block ITEM/EQUIP during combat poses.

    Gun stable aim (0x13/0x03) is knife_action_ready for linked attacks but must
    not open the pause menu before the shot/recovery finishes.
    """
    if not knife_action_ready(anim, aux, recovery):
        return False
    if equipped_weapon_id is None:
        return True
    wid = int(equipped_weapon_id)
    if wid == KNIFE_ID:
        return not _in_knife_combat_pose(anim, aux, recovery)
    return not _in_ranged_combat_pose(anim, aux, recovery)


def _submenu_active(
    *,
    use_phase: int,
    equip_phase: int,
    combine_phase: int,
) -> bool:
    return int(use_phase) == 1 or int(equip_phase) == 1 or int(combine_phase) in (1, 2)


def _mask_box_ui_session(
    mask: np.ndarray,
    n_actions: int,
    *,
    box_phase: int,
    inventory: list[tuple[int, int]] | None,
    box: list[tuple[int, int]] | None,
    room_id: str | None = None,
) -> np.ndarray:
    """Legal actions while the item-box UI is open (in_control is false)."""
    from re1_rl.item_box import (
        BOX_DEPOSIT_POLICY_ENABLED,
        BOX_DEPOSIT_ROOMS,
        can_deposit,
        can_withdraw,
    )
    from re1_rl.item_box_ui_macro import first_empty_inventory_slot

    mask[:] = False
    phase = int(box_phase)
    # UI withdraw requires an empty inv slot (Cross on empty → box list).
    has_empty_inv = (
        inventory is not None and first_empty_inventory_slot(inventory) is not None
    )
    deposit_room_ok = (
        room_id is None or str(room_id) in BOX_DEPOSIT_ROOMS
    )
    deposit_enabled = bool(BOX_DEPOSIT_POLICY_ENABLED) and deposit_room_ok

    if phase == BOX_PHASE_WITHDRAW_SLOT:
        if inventory is not None and box is not None and has_empty_inv:
            for i in range(N_WITHDRAW_ACTIONS):
                idx = WITHDRAW_ACTION_BASE + i
                if idx < n_actions:
                    ok, _ = can_withdraw(inventory, box, i)
                    mask[idx] = bool(ok)
        # Allow backing out to close without completing a withdraw.
        if BOX_CLOSE_ACTION < n_actions:
            mask[BOX_CLOSE_ACTION] = True
        return mask

    if phase == BOX_PHASE_DEPOSIT_SLOT:
        if deposit_enabled and inventory is not None and box is not None:
            for i in range(N_SELECT_SLOT):
                idx = SELECT_SLOT_BASE + i
                if idx < n_actions:
                    ok, _ = can_deposit(inventory, box, i, room_id=room_id)
                    mask[idx] = bool(ok)
        if BOX_CLOSE_ACTION < n_actions:
            mask[BOX_CLOSE_ACTION] = True
        return mask

    # BOX_PHASE_CHOOSE: withdraw / close (deposit wired but policy-gated).
    from re1_rl.boss_prep_macro import room100_boss_bank_preflight

    if BOX_BANK_BOSS_ACTION < n_actions:
        ok_bank = False
        if (
            deposit_enabled
            and inventory is not None
            and box is not None
            and str(room_id or "").strip().upper() == "100"
        ):
            ok_bank, _ = room100_boss_bank_preflight(
                inventory, box, room_id=room_id
            )
        mask[BOX_BANK_BOSS_ACTION] = bool(ok_bank)
    if BOX_WITHDRAW_ACTION < n_actions:
        any_withdraw = False
        if inventory is not None and box is not None and has_empty_inv:
            for i in range(min(N_WITHDRAW_ACTIONS, len(box))):
                ok, _ = can_withdraw(inventory, box, i)
                if ok:
                    any_withdraw = True
                    break
        mask[BOX_WITHDRAW_ACTION] = any_withdraw
    if BOX_DEPOSIT_ACTION < n_actions:
        any_deposit = False
        if deposit_enabled and inventory is not None and box is not None:
            for i in range(min(N_SELECT_SLOT, len(inventory))):
                ok, _ = can_deposit(inventory, box, i, room_id=room_id)
                if ok:
                    any_deposit = True
                    break
        mask[BOX_DEPOSIT_ACTION] = any_deposit
    if BOX_CLOSE_ACTION < n_actions:
        mask[BOX_CLOSE_ACTION] = True
    return mask


def action_mask(
    n_actions: int,
    prev_action: int | None,
    *,
    player_anim: int | None = None,
    player_aux: int | None = None,
    player_recovery: int | None = None,
    equipped_weapon_id: int | None = None,
    equipped_slot_0based: int | None = None,
    inventory: list[tuple[int, int]] | None = None,
    box: list[tuple[int, int]] | None = None,
    in_box_room: bool = False,
    box_ui_open: bool = False,
    box_phase: int = 0,
    use_phase: int = 0,
    equip_phase: int = 0,
    combine_phase: int = 0,
    combine_slot_a: int | None = None,
    current_hp: int | None = None,
    poisoned: bool = False,
    episode_start_hp: int | None = None,
    in_control: bool = True,
    grab_escape_pending: bool = False,
    alive_enemies_in_room: int | None = None,
    knife_enemies_near: int | None = None,
    gun_enemies_near: int | None = None,
    mask_combat_without_enemies: bool = True,
    room_id: str | None = None,
    player_x: float | int | None = None,
    player_z: float | int | None = None,
    rewarded_story_uses: set[str] | frozenset[str] | None = None,
    document_examine_open: bool = False,
    equip_switch_cooldown: int = 0,
) -> np.ndarray:
    """Return bool mask (True = legal) for MaskablePPO / ActionMasker."""
    del prev_action

    mask = np.ones(n_actions, dtype=bool)
    if document_examine_open:
        mask[:] = False
        for idx in _DOCUMENT_EXAMINE_ACTIONS:
            if idx < n_actions:
                mask[idx] = True
        return mask
    if grab_escape_pending:
        mask[:] = False
        if n_actions > 0:
            mask[0] = True
        return mask
    # Item-box UI clears in_control; expose withdraw/deposit/close instead of noop.
    if box_ui_open:
        return _mask_box_ui_session(
            mask,
            n_actions,
            box_phase=box_phase,
            inventory=inventory,
            box=box,
            room_id=room_id,
        )
    if not in_control:
        mask[:] = False
        if n_actions > 0:
            mask[0] = True
        return mask
    use_ph = int(use_phase)
    equip_ph = int(equip_phase)
    combine_ph = int(combine_phase)
    in_submenu = _submenu_active(
        use_phase=use_ph, equip_phase=equip_ph, combine_phase=combine_ph
    )

    if in_submenu:
        mask[:] = False

    anim_ready = True
    crouch_anim_ready = True
    menu_ready = True
    if (
        player_anim is not None
        and player_aux is not None
        and player_recovery is not None
    ):
        anim = int(player_anim)
        aux = int(player_aux)
        rec = int(player_recovery)
        anim_ready = knife_action_ready(anim, aux, rec)
        crouch_anim_ready = knife_crouch_action_ready(anim, aux, rec)
        menu_ready = menu_action_ready(
            anim, aux, rec, equipped_weapon_id=equipped_weapon_id
        )

    # Prefer weapon-specific near counts when provided (generous knife band < gun).
    knife_enemies = (
        int(knife_enemies_near)
        if knife_enemies_near is not None
        else (int(alive_enemies_in_room) if alive_enemies_in_room is not None else None)
    )
    gun_enemies = (
        int(gun_enemies_near)
        if gun_enemies_near is not None
        else (int(alive_enemies_in_room) if alive_enemies_in_room is not None else None)
    )

    # Typewriter / box rooms and Main Hall F1/F2: attack macros always illegal.
    attacks_banned = (
        is_typewriter_or_box_room(room_id)
        or is_always_illegal_attack_room(room_id)
    )

    if not in_submenu:
        if attacks_banned:
            height_legal = False
        else:
            height_legal = _height_attack_legal(
                anim_ready=anim_ready,
                equipped_weapon_id=equipped_weapon_id,
                equipped_slot_0based=equipped_slot_0based,
                inventory=inventory,
                mask_combat_without_enemies=mask_combat_without_enemies,
                knife_enemies=knife_enemies,
                gun_enemies=gun_enemies,
                alive_enemies_in_room=alive_enemies_in_room,
            )
        for idx in (ATTACK_UP_ACTION, ATTACK_ACTION, ATTACK_DOWN_ACTION):
            if idx < n_actions:
                mask[idx] = height_legal
        if (
            not attacks_banned
            and ATTACK_DOWN_ACTION < n_actions
            and mask[ATTACK_DOWN_ACTION]
        ):
            down_ready = (
                crouch_anim_ready
                if equipped_weapon_id == KNIFE_ID
                else anim_ready
            )
            mask[ATTACK_DOWN_ACTION] = _height_attack_legal(
                anim_ready=down_ready,
                equipped_weapon_id=equipped_weapon_id,
                equipped_slot_0based=equipped_slot_0based,
                inventory=inventory,
                mask_combat_without_enemies=mask_combat_without_enemies,
                knife_enemies=knife_enemies,
                gun_enemies=gun_enemies,
                alive_enemies_in_room=alive_enemies_in_room,
            )

    if not in_submenu:
        # Box transfers only while the box UI is open (handled above).
        for i in range(N_DEPOSIT_ACTIONS):
            idx = DEPOSIT_ACTION_BASE + i
            if idx < n_actions:
                mask[idx] = False
        for idx in range(
            WITHDRAW_ACTION_BASE, WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS
        ):
            if idx < n_actions:
                mask[idx] = False
        _ = in_box_room  # retained for call-site compat; UI session gates box acts

    if USE_ACTION < n_actions:
        mask[USE_ACTION] = False
    if EQUIP_ACTION < n_actions:
        mask[EQUIP_ACTION] = False
    if COMBINE_ACTION < n_actions:
        mask[COMBINE_ACTION] = False
    for i in range(N_SELECT_SLOT):
        idx = SELECT_SLOT_BASE + i
        if idx < n_actions:
            mask[idx] = False

    if inventory is not None:
        story_kwargs = {
            "room": room_id,
            "x": player_x,
            "z": player_z,
            "rewarded_site_ids": rewarded_story_uses,
        }
        story_legal = any_legal_story_use_slot(inventory, **story_kwargs)
        story_slots = legal_story_use_slots(inventory, **story_kwargs)
        if not in_submenu:
            if USE_ACTION < n_actions:
                heal_legal = any_legal_use_slot(
                    inventory,
                    current_hp=current_hp,
                    poisoned=poisoned,
                    episode_start_hp=episode_start_hp,
                )
                # Story USE: key item + stand position only (no anim_ready gate).
                mask[USE_ACTION] = (menu_ready and heal_legal) or story_legal
            if EQUIP_ACTION < n_actions:
                mask[EQUIP_ACTION] = menu_ready and any_legal_equip_slot(
                    inventory,
                    equipped_weapon_id=equipped_weapon_id,
                    equipped_slot_0based=equipped_slot_0based,
                )
                # One-step holdout after a successful equip swap (anti thrash).
                if int(equip_switch_cooldown) > 0:
                    mask[EQUIP_ACTION] = False
            if COMBINE_ACTION < n_actions:
                from re1_rl.inventory_combine import any_valid_combine

                mask[COMBINE_ACTION] = menu_ready and any_valid_combine(inventory)
        elif use_ph == 1:
            if story_legal:
                for idx in _STORY_USE_RECOVERY_ACTIONS:
                    if idx < n_actions:
                        mask[idx] = True
            for i in range(N_SELECT_SLOT):
                idx = SELECT_SLOT_BASE + i
                if idx < n_actions:
                    heal_slot = slot_legal_for_use(
                        inventory,
                        i,
                        current_hp=current_hp,
                        poisoned=poisoned,
                        episode_start_hp=episode_start_hp,
                    )
                    story_slot = i in story_slots
                    mask[idx] = heal_slot or story_slot
        elif equip_ph == 1:
            for i in range(N_SELECT_SLOT):
                idx = SELECT_SLOT_BASE + i
                if idx < n_actions:
                    mask[idx] = slot_legal_for_equip(
                        inventory,
                        i,
                        equipped_weapon_id=equipped_weapon_id,
                        equipped_slot_0based=equipped_slot_0based,
                    )
        elif combine_ph == 1:
            from re1_rl.inventory_combine import slot_legal_as_first

            for i in range(N_SELECT_SLOT):
                idx = SELECT_SLOT_BASE + i
                if idx < n_actions:
                    mask[idx] = slot_legal_as_first(inventory, i)
        elif combine_ph == 2 and combine_slot_a is not None:
            from re1_rl.inventory_combine import slot_legal_as_second

            slot_a = int(combine_slot_a)
            for i in range(N_SELECT_SLOT):
                idx = SELECT_SLOT_BASE + i
                if idx < n_actions:
                    mask[idx] = slot_legal_as_second(inventory, slot_a, i)

    return mask
