"""Legal action masks for RE1 discrete control.

Action layout (env.ACTION_NAMES):
  0-5   movement
  6     attack_up          — R1+Up high attack (old quickturn slot; DC has no QT)
  7     interact
  8     knife_swing
  9     attack
  10    use               — open USE menu; then select_slot_N (2-step)
  11    equip             — open EQUIP menu; then select_slot_N (2-step)
  12-19 deposit_slot_N
  20-35 withdraw_box_N
  36    combine            — open COMBINE menu; select_slot x2 (3-step)
  37-44 select_slot_N      — shared slot pick (use / equip / combine)
  45    attack_down        — R1+Down crouch / floor-aim attack macro
"""

from __future__ import annotations

import numpy as np

from re1_rl.ammo_accounting import can_fire_from_equipped_slot
from re1_rl.item_use import any_legal_use_slot, slot_legal_for_use
from re1_rl.story_item_use import (
    any_legal_story_use_slot,
    legal_story_use_slots,
    slot_legal_for_story_use,
)
from re1_rl.knife_macro import knife_action_ready
from re1_rl.weapon_equip import (
    EQUIPPABLE_WEAPON_IDS,
    any_legal_equip_slot,
    slot_legal_for_equip,
)

ATTACK_UP_ACTION = 6  # reuses former quickturn index
KNIFE_SWING_ACTION = 8
ATTACK_ACTION = 9
USE_ACTION = 10
EQUIP_ACTION = 11
DEPOSIT_ACTION_BASE = EQUIP_ACTION + 1  # 12
N_DEPOSIT_ACTIONS = 8
WITHDRAW_ACTION_BASE = DEPOSIT_ACTION_BASE + N_DEPOSIT_ACTIONS  # 20
N_WITHDRAW_ACTIONS = 16
COMBINE_ACTION = WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS  # 36
SELECT_SLOT_BASE = COMBINE_ACTION + 1  # 37
N_SELECT_SLOT = 8
ATTACK_DOWN_ACTION = SELECT_SLOT_BASE + N_SELECT_SLOT  # 45

KNIFE_ID = 0x01

DEPOSIT_ACTION_NAMES = [f"deposit_slot_{i}" for i in range(N_DEPOSIT_ACTIONS)]
WITHDRAW_ACTION_NAMES = [f"withdraw_box_{i}" for i in range(N_WITHDRAW_ACTIONS)]
MENU_ACTION_NAMES = ["combine"] + [
    f"select_slot_{i}" for i in range(N_SELECT_SLOT)
]

# Tank controls + interact: allowed while story USE is pending so Jill can turn
# toward the interact point after opening the USE submenu. Combat macros excluded.
_STORY_USE_RECOVERY_ACTIONS = frozenset(
    i for i in range(8) if i != ATTACK_UP_ACTION
)

# Document / file examine overlay (doom books, botany book): mash directions + Cross.
_DOCUMENT_EXAMINE_ACTIONS = frozenset({0, 1, 2, 3, 7})


def _submenu_active(
    *,
    use_phase: int,
    equip_phase: int,
    combine_phase: int,
) -> bool:
    return int(use_phase) == 1 or int(equip_phase) == 1 or int(combine_phase) in (1, 2)


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
    if (
        player_anim is not None
        and player_aux is not None
        and player_recovery is not None
    ):
        anim_ready = knife_action_ready(
            int(player_anim), int(player_aux), int(player_recovery)
        )

    enemies_present = True
    if mask_combat_without_enemies and alive_enemies_in_room is not None:
        enemies_present = int(alive_enemies_in_room) > 0
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

    if not in_submenu and KNIFE_SWING_ACTION < n_actions:
        legal = anim_ready
        if mask_combat_without_enemies and knife_enemies is not None:
            legal = legal and knife_enemies > 0
        if equipped_weapon_id is not None:
            legal = legal and int(equipped_weapon_id) == KNIFE_ID
        mask[KNIFE_SWING_ACTION] = legal

    if not in_submenu and ATTACK_ACTION < n_actions:
        legal = anim_ready
        if equipped_weapon_id is not None:
            wid = int(equipped_weapon_id)
            legal = legal and wid in EQUIPPABLE_WEAPON_IDS
            if legal and wid != KNIFE_ID and inventory is not None:
                legal = can_fire_from_equipped_slot(
                    inventory, wid, equipped_slot_0based
                )
        else:
            wid = None
        if legal and mask_combat_without_enemies:
            if wid == KNIFE_ID:
                if knife_enemies is not None:
                    legal = knife_enemies > 0
            elif gun_enemies is not None:
                legal = gun_enemies > 0
            elif alive_enemies_in_room is not None:
                legal = int(alive_enemies_in_room) > 0
        mask[ATTACK_ACTION] = legal
        if ATTACK_UP_ACTION < n_actions:
            mask[ATTACK_UP_ACTION] = legal
        if ATTACK_DOWN_ACTION < n_actions:
            mask[ATTACK_DOWN_ACTION] = legal

    if not in_submenu:
        if inventory is not None and box is not None:
            from re1_rl.item_box import can_deposit, can_withdraw

            for i in range(N_DEPOSIT_ACTIONS):
                idx = DEPOSIT_ACTION_BASE + i
                if idx < n_actions:
                    ok, _ = can_deposit(inventory, box, i)
                    mask[idx] = in_box_room and ok
            for i in range(N_WITHDRAW_ACTIONS):
                idx = WITHDRAW_ACTION_BASE + i
                if idx < n_actions:
                    ok, _ = can_withdraw(inventory, box, i)
                    mask[idx] = in_box_room and ok
        elif not in_box_room:
            for idx in range(
                DEPOSIT_ACTION_BASE, WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS
            ):
                if idx < n_actions:
                    mask[idx] = False

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
                mask[USE_ACTION] = (anim_ready and heal_legal) or story_legal
            if EQUIP_ACTION < n_actions:
                mask[EQUIP_ACTION] = anim_ready and any_legal_equip_slot(
                    inventory,
                    equipped_weapon_id=equipped_weapon_id,
                    equipped_slot_0based=equipped_slot_0based,
                )
            if COMBINE_ACTION < n_actions:
                from re1_rl.inventory_combine import any_valid_combine

                mask[COMBINE_ACTION] = any_valid_combine(inventory)
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
