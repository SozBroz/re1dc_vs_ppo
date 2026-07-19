"""Expanded action space: attack macro gating, equip menu, box masks."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import (
    ATTACK_ACTION,
    ATTACK_DOWN_ACTION,
    ATTACK_UP_ACTION,
    COMBINE_ACTION,
    DEPOSIT_ACTION_BASE,
    EQUIP_ACTION,
    KNIFE_SWING_ACTION,
    N_SELECT_SLOT,
    N_WITHDRAW_ACTIONS,
    SELECT_SLOT_BASE,
    USE_ACTION,
    WITHDRAW_ACTION_BASE,
    action_mask,
)
from re1_rl.attack_macro import (
    attack_possible,
    can_attack_with_ammo,
    execute_attack_down_macro,
    execute_attack_macro,
    frame_budget,
    is_aim_stable,
    is_attack_settle_wait_state,
)
from re1_rl.weapon_equip import (
    EQUIPPABLE_WEAPON_IDS,
    can_equip,
    magic_equip,
    magic_equip_slot,
)

N_ACTIONS = ATTACK_DOWN_ACTION + 1  # 46


def test_action_layout_matches_env_names() -> None:
    from re1_rl.env import ACTION_NAMES

    assert len(ACTION_NAMES) == N_ACTIONS
    assert ACTION_NAMES[KNIFE_SWING_ACTION] == "knife_swing"
    assert ACTION_NAMES[ATTACK_ACTION] == "attack"
    assert ACTION_NAMES[ATTACK_UP_ACTION] == "attack_up"
    assert ACTION_NAMES[ATTACK_DOWN_ACTION] == "attack_down"
    assert ACTION_NAMES[USE_ACTION] == "use"
    assert ACTION_NAMES[EQUIP_ACTION] == "equip"
    assert ACTION_NAMES[DEPOSIT_ACTION_BASE] == "deposit_slot_0"
    assert ACTION_NAMES[WITHDRAW_ACTION_BASE] == "withdraw_box_0"
    assert ACTION_NAMES[COMBINE_ACTION] == "combine"
    assert ACTION_NAMES[SELECT_SLOT_BASE] == "select_slot_0"


def test_attack_masked_without_ammo() -> None:
    inv = [(0x02, 0)] + [(0, 0)] * 7
    m = action_mask(
        N_ACTIONS, None, equipped_weapon_id=0x02, inventory=inv,
        player_anim=0, player_aux=0, player_recovery=0,
    )
    assert not m[ATTACK_ACTION]
    assert not can_attack_with_ammo(inv, 0x02)


def test_attack_legal_with_beretta_ammo() -> None:
    inv = [(0x02, 5)] + [(0, 0)] * 7
    m = action_mask(
        N_ACTIONS, None, equipped_weapon_id=0x02, inventory=inv,
        player_anim=0, player_aux=0, player_recovery=0,
    )
    assert m[ATTACK_ACTION]
    assert m[ATTACK_UP_ACTION]
    assert m[ATTACK_DOWN_ACTION]


def test_attack_height_masks_always_match_attack_mask() -> None:
    cases = [
        {},
        {"equipped_weapon_id": 0},
        {
            "equipped_weapon_id": 0x02,
            "inventory": [(0x02, 5)] + [(0, 0)] * 7,
            "gun_enemies_near": 1,
        },
        {
            "equipped_weapon_id": 0x02,
            "inventory": [(0x02, 0)] + [(0, 0)] * 7,
            "gun_enemies_near": 1,
        },
        {
            "equipped_weapon_id": 0x01,
            "knife_enemies_near": 0,
        },
        {
            "equipped_weapon_id": 0x01,
            "knife_enemies_near": 1,
        },
        {
            "equipped_weapon_id": 0x03,
            "player_recovery": 5,
            "gun_enemies_near": 1,
        },
        {"equipped_weapon_id": 0x03, "in_control": False},
        {"equipped_weapon_id": 0x03, "use_phase": 1},
    ]
    for overrides in cases:
        kwargs = {
            "player_anim": 0,
            "player_aux": 0,
            "player_recovery": 0,
            **overrides,
        }
        mask = action_mask(N_ACTIONS, None, **kwargs)
        assert mask[ATTACK_UP_ACTION] == mask[ATTACK_ACTION], overrides
        assert mask[ATTACK_DOWN_ACTION] == mask[ATTACK_ACTION], overrides


def test_knife_attack_legal_without_ammo_items() -> None:
    inv = [(0x01, 0)] + [(0, 0)] * 7
    m = action_mask(
        N_ACTIONS, None, equipped_weapon_id=0x01, inventory=inv,
        player_anim=0, player_aux=0, player_recovery=0,
    )
    assert m[ATTACK_ACTION]
    assert can_attack_with_ammo(inv, 0x01)


def test_attack_masked_without_weapon() -> None:
    m = action_mask(N_ACTIONS, None, equipped_weapon_id=0)
    assert not m[ATTACK_ACTION]
    assert not m[KNIFE_SWING_ACTION]


def test_attack_legal_with_gun_but_knife_swing_blocked() -> None:
    m = action_mask(N_ACTIONS, None, equipped_weapon_id=0x02)
    assert m[ATTACK_ACTION]
    assert not m[KNIFE_SWING_ACTION]


def test_knife_swing_legal_with_knife() -> None:
    m = action_mask(N_ACTIONS, None, equipped_weapon_id=0x01)
    assert m[KNIFE_SWING_ACTION]
    assert m[ATTACK_ACTION]


def test_attack_masked_during_recovery() -> None:
    m = action_mask(
        N_ACTIONS, None,
        player_anim=0, player_aux=0, player_recovery=5,
        equipped_weapon_id=0x02,
        alive_enemies_in_room=1,
    )
    assert not m[ATTACK_ACTION]


def test_attack_link_boundaries_are_legal_next_frame() -> None:
    cases = (
        # Live link-matrix boundaries: aimed knife, knife recovery aim, gun aim.
        (0x01, 0x12, 0x04),
        (0x01, 0x13, 0x04),
        (0x02, 0x13, 0x03),
        (0x03, 0x13, 0x03),
    )
    for weapon_id, anim, aux in cases:
        inventory = (
            [(weapon_id, 5)] + [(0, 0)] * 7
            if weapon_id != 0x01
            else [(0x01, 0)] + [(0, 0)] * 7
        )
        mask = action_mask(
            N_ACTIONS,
            None,
            equipped_weapon_id=weapon_id,
            inventory=inventory,
            player_anim=anim,
            player_aux=aux,
            player_recovery=0,
            alive_enemies_in_room=1,
        )
        assert mask[ATTACK_ACTION], (weapon_id, anim, aux)
        assert mask[ATTACK_UP_ACTION], (weapon_id, anim, aux)
        assert mask[ATTACK_DOWN_ACTION], (weapon_id, anim, aux)


def test_combat_masked_without_enemies_in_room() -> None:
    m = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=0x01,
        player_anim=0,
        player_aux=0,
        player_recovery=0,
        alive_enemies_in_room=0,
    )
    assert not m[KNIFE_SWING_ACTION]
    assert not m[ATTACK_ACTION]


def test_combat_mask_disabled_for_debug() -> None:
    m = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=0x01,
        player_anim=0,
        player_aux=0,
        player_recovery=0,
        alive_enemies_in_room=0,
        mask_combat_without_enemies=False,
    )
    assert m[KNIFE_SWING_ACTION]
    assert m[ATTACK_ACTION]


def test_equip_mask_two_step() -> None:
    inv = [(0x01, 0), (0x02, 15), (0x41, 1)] + [(0, 0)] * 5
    m0 = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=0x01,
        equipped_slot_0based=0,
        inventory=inv,
        player_anim=0,
        player_aux=0,
        player_recovery=0,
    )
    assert m0[EQUIP_ACTION]
    assert not m0[SELECT_SLOT_BASE:SELECT_SLOT_BASE + N_SELECT_SLOT].any()

    m_unarmed = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=0,
        equipped_slot_0based=None,
        inventory=inv,
        player_anim=0,
        player_aux=0,
        player_recovery=0,
    )
    assert m_unarmed[EQUIP_ACTION]

    m1 = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=0,
        equipped_slot_0based=None,
        inventory=inv,
        equip_phase=1,
    )
    assert not m1[EQUIP_ACTION]
    assert m1[SELECT_SLOT_BASE]  # knife: RAM qty 0, policy treats as owned
    assert m1[SELECT_SLOT_BASE + 1]

    m_switch = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=0x01,
        equipped_slot_0based=0,
        inventory=inv,
        equip_phase=1,
    )
    assert not m_switch[EQUIP_ACTION]
    assert not m_switch[SELECT_SLOT_BASE]  # already holding knife
    assert m_switch[SELECT_SLOT_BASE + 1]


def test_equip_masked_when_equipped_unknown() -> None:
    inv = [(0, 0)] * 8
    m = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=None,
        inventory=inv,
    )
    assert not m[EQUIP_ACTION]


def test_equip_legal_knife_only_when_equipped_unknown() -> None:
    inv = [(0x01, 0)] + [(0, 0)] * 7
    m = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=None,
        inventory=inv,
        player_anim=0,
        player_aux=0,
        player_recovery=0,
    )
    assert m[EQUIP_ACTION]


def test_equip_masked_when_only_equipped_weapon() -> None:
    inv = [(0x01, 1)] + [(0, 0)] * 7
    m = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=0x01,
        equipped_slot_0based=0,
        inventory=inv,
        player_anim=0,
        player_aux=0,
        player_recovery=0,
    )
    assert not m[EQUIP_ACTION]


def test_equip_switch_pistol_to_knife() -> None:
    inv = [(0x01, 0), (0x02, 14)] + [(0, 0)] * 6
    m = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=0x02,
        equipped_slot_0based=1,
        inventory=inv,
        player_anim=0,
        player_aux=0,
        player_recovery=0,
    )
    assert m[EQUIP_ACTION]
    m_ph = action_mask(
        N_ACTIONS,
        None,
        equipped_weapon_id=0x02,
        equipped_slot_0based=1,
        inventory=inv,
        equip_phase=1,
        player_anim=0,
        player_aux=0,
        player_recovery=0,
    )
    assert not m_ph[EQUIP_ACTION]
    assert m_ph[SELECT_SLOT_BASE]
    assert not m_ph[SELECT_SLOT_BASE + 1]


def test_box_actions_masked_outside_box_room() -> None:
    inv = [(0x01, 0)] + [(0, 0)] * 7
    box = [(0x0B, 15)] + [(0, 0)] * 15
    m = action_mask(
        N_ACTIONS, None, equipped_weapon_id=0x01,
        inventory=inv, box=box, in_box_room=False,
    )
    assert not m[DEPOSIT_ACTION_BASE:WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS].any()


def test_box_actions_unknown_state_masked() -> None:
    m = action_mask(N_ACTIONS, None, in_box_room=False)
    assert not m[DEPOSIT_ACTION_BASE:WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS].any()


def test_can_equip_rules() -> None:
    assert can_equip(0x02, equipped_id=0x01, inventory_ids=[0x01, 0x02])
    assert not can_equip(0x02, equipped_id=0x02, inventory_ids=[0x01, 0x02])
    assert not can_equip(0x03, equipped_id=0x01, inventory_ids=[0x01, 0x02])
    assert not can_equip(0x44, equipped_id=0x01, inventory_ids=[0x44])


class _FakeBridge:
    def __init__(self, inv_ids: list[int]) -> None:
        self._inv = inv_ids
        self.writes: list[list] = []

    def read_ram(self, fields):
        out = {}
        for name, _addr, _dtype in fields:
            if name.startswith("inv_slot_"):
                idx = int(name.split("_")[-1])
                out[name] = self._inv[idx] if idx < len(self._inv) else 0
        return out

    def write_ram(self, fields):
        self.writes.append(fields)


def test_magic_equip_writes_mirrors() -> None:
    bridge = _FakeBridge([0x01, 0x02, 0, 0, 0, 0, 0, 0])
    result = magic_equip(bridge, 0x02)
    assert result["ok"]
    assert result["slot"] == 1
    values = [f[3] for f in bridge.writes[0]]
    assert values == [0x02, 2, 1]


def test_magic_equip_slot_writes_mirrors() -> None:
    bridge = _FakeBridge([0x01, 0x02, 0x03, 0, 0, 0, 0, 0])
    result = magic_equip_slot(bridge, 2)
    assert result["ok"]
    assert result["slot"] == 2
    values = [f[3] for f in bridge.writes[0]]
    assert values == [0x03, 3, 2]


def test_magic_equip_refuses_missing_weapon() -> None:
    bridge = _FakeBridge([0x01, 0, 0, 0, 0, 0, 0, 0])
    result = magic_equip(bridge, 0x03)
    assert not result["ok"]
    assert result["reason"] == "not_in_inventory"
    assert bridge.writes == []


def test_attack_possible_only_for_weapons() -> None:
    assert attack_possible(0x01)
    assert attack_possible(0x0A)
    assert not attack_possible(0x00)
    assert not attack_possible(0x44)


def test_aim_stable_signature() -> None:
    assert is_aim_stable(0x13, 0x03, 0)
    assert is_aim_stable(0x12, 0x04, 0)
    assert not is_aim_stable(0x12, 0x03, 0)
    assert not is_aim_stable(0x13, 0x04, 0)
    assert not is_aim_stable(0x13, 0x03, 5)


def test_frame_budget_defaults() -> None:
    max_aim, max_rec = frame_budget("nonexistent_weapon")
    assert max_aim >= 40 and max_rec >= 60


def test_shotgun_handler_is_isolated_from_beretta() -> None:
    from re1_rl.attack_macro import (
        _WEAPON_ATTACK_HANDLERS,
        _execute_ranged_attack_macro,
        _execute_shotgun_attack_macro,
    )

    assert _WEAPON_ATTACK_HANDLERS[0x03] is _execute_shotgun_attack_macro
    assert _WEAPON_ATTACK_HANDLERS[0x02] is _execute_ranged_attack_macro


def test_equippable_ids_are_ten_ps1_weapons() -> None:
    assert len(EQUIPPABLE_WEAPON_IDS) == 10
    assert 0x6F not in EQUIPPABLE_WEAPON_IDS


def test_attack_settle_wait_state_covers_locomotion() -> None:
    assert is_attack_settle_wait_state(0x06, 0x00, 0)
    assert is_attack_settle_wait_state(0, 0, 2)
    assert is_attack_settle_wait_state(0x0D, 0x01, 0)
    assert is_attack_settle_wait_state(0x12, 0x03, 0)
    assert not is_attack_settle_wait_state(0x20, 0x00, 0)


def test_attack_macro_settles_after_locomotion() -> None:
    from unittest.mock import MagicMock

    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0x06, 0x00, 0),
        (0x06, 0x00, 0),
        (0, 0, 0),
        (0, 0, 0),
        (0x12, 0x03, 0),
        (0x12, 0x03, 0),
        (0x13, 0x03, 0),
        (0x13, 0x03, 0),
        (0x14, 0x03, 0),
        (0x14, 0x03, 0),
        (0x13, 0x03, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)
    inv_reads = {"n": 0}

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "equipped_weapon_id" in names:
            return {"equipped_weapon_id": 0x02}
        if "player_hp" in names:
            return {"player_hp": 96}
        if any(n.startswith("inv_slot_") for n in names):
            inv_reads["n"] += 1
            # First _ammo_count is pre-shot; later reads show one round spent.
            # Packing: high byte = qty, low byte = item id (beretta 0x02).
            raw = 0x0F02 if inv_reads["n"] <= 1 else 0x0E02
            return {n: raw if n == "inv_slot_0" else 0 for n in names}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square", "cross", "r1")}
    died, frames, report = execute_attack_macro(
        bridge,
        empty_sticky=empty,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert not died
    assert report["outcome"] == "ok"
    assert report["ammo_spent"] == 1
    assert report["macro_path"] == "ranged:beretta"
    assert report["link_aim_held"] is True
    assert bridge.step.call_args_list[-1].kwargs["frame_buttons"][-1] == {"r1": True}
    assert frames > 0


def test_standing_gun_buttons_strip_aim_down() -> None:
    from re1_rl.attack_macro import standing_gun_buttons

    assert standing_gun_buttons({"r1": True, "down": True, "up": True}) == {"r1": True}
    assert standing_gun_buttons(
        {"r1": True, "cross": True, "down": True, "left": True, "square": True}
    ) == {"r1": True, "cross": True}
    assert standing_gun_buttons({"down": True}) == {}


def test_ranged_macro_strips_down_from_pad_and_sticky() -> None:
    from unittest.mock import MagicMock

    import re1_rl.attack_macro as am

    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0, 0, 0),
        (0, 0, 0),
        (0x12, 0x03, 0),
        (0x13, 0x03, 0),
        (0x13, 0x03, 0),
        (0x14, 0x03, 0),
        (0x14, 0x03, 0),
        (0x13, 0x03, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)
    inv_reads = {"n": 0}

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "equipped_weapon_id" in names:
            return {"equipped_weapon_id": 0x02}
        if "player_hp" in names:
            return {"player_hp": 96}
        if any(n.startswith("inv_slot_") for n in names):
            inv_reads["n"] += 1
            raw = 0x0F02 if inv_reads["n"] <= 1 else 0x0E02
            return {n: raw if n == "inv_slot_0" else 0 for n in names}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    # Contaminate module pads + sticky the way a buggy caller / watch script might.
    am.AIM_BUTTONS.clear()
    am.AIM_BUTTONS.update({"r1": True, "down": True, "up": True})
    am.FIRE_BUTTONS.clear()
    am.FIRE_BUTTONS.update({"r1": True, "cross": True, "down": True, "left": True})
    try:
        died, _frames, report = execute_attack_macro(
            bridge,
            empty_sticky={
                "up": False,
                "down": True,
                "left": True,
                "right": False,
                "square": True,
            },
            prev_hp=96,
            episode_start_hp=96,
        )
    finally:
        am.AIM_BUTTONS.clear()
        am.AIM_BUTTONS.update({"r1": True})
        am.FIRE_BUTTONS.clear()
        am.FIRE_BUTTONS.update({"r1": True, "cross": True})

    assert not died
    assert report["outcome"] == "ok"
    assert report["ammo_spent"] == 1
    for call in bridge.step.call_args_list:
        kwargs = call.kwargs
        sticky = kwargs.get("sticky") or {}
        assert sticky.get("down") is not True
        assert sticky.get("up") is not True
        assert sticky.get("left") is not True
        assert sticky.get("right") is not True
        assert sticky.get("square") is not True
        for btn in kwargs.get("frame_buttons") or []:
            assert "down" not in btn or not btn["down"]
            assert "up" not in btn or not btn["up"]
            assert "left" not in btn or not btn["left"]
            assert "right" not in btn or not btn["right"]
            assert "square" not in btn or not btn["square"]


def test_ranged_dry_fire_not_ok() -> None:
    """Fire anim without ammo decrement (floor-aim style) must not report ok."""
    from unittest.mock import MagicMock

    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    hook_seq = [
        (0, 0, 0),
        (0, 0, 0),
        (0x12, 0x03, 0),
        (0x13, 0x03, 0),
        (0x13, 0x03, 0),
        (0x14, 0x03, 0),
        (0x14, 0x03, 0),
        (0x13, 0x03, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "equipped_weapon_id" in names:
            return {"equipped_weapon_id": 0x02}
        if "player_hp" in names:
            return {"player_hp": 96}
        if any(n.startswith("inv_slot_") for n in names):
            # Ammo never drops — simulates R1+Down floor aim.
            return {n: 0x0F02 if n == "inv_slot_0" else 0 for n in names}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    _died, _frames, report = execute_attack_macro(
        bridge, empty_sticky=empty, prev_hp=96, episode_start_hp=96,
    )
    assert report["saw_fire_anim"] is True
    assert report["ammo_spent"] == 0
    assert report["outcome"] == "dry_fire"


def test_attack_knife_uses_neutral_macro_path(monkeypatch) -> None:
    from unittest.mock import MagicMock

    bridge = MagicMock()
    bridge.attack_pins = None
    bridge.frame_ring = None
    bridge.step.return_value = (0, False)
    # Aim hold is >=32 observes; slash must start after that window.
    hooks = iter(
        [(0, 0, 0)] * 32
        + [(0x14, 0x04, 0)] * 8
        + [(0, 0, 0)] * 24
    )

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "equipped_weapon_id" in names:
            return {"equipped_weapon_id": 0x01}
        if "player_hp" in names:
            return {"player_hp": 96}
        try:
            a, x, r = next(hooks)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    bridge.read_ram.side_effect = read_ram
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames, report = execute_attack_macro(
        bridge, empty_sticky=empty, prev_hp=96, episode_start_hp=96,
    )
    assert not died
    assert report["macro_path"] == "knife_neutral"
    assert report["aim_mode"] == "neutral"
    assert report["weapon"] == "knife"
    assert report["outcome"] == "ok"
    assert report["saw_fire_anim"] is True


def test_attack_down_knife_uses_crouch_macro_path(monkeypatch) -> None:
    from unittest.mock import MagicMock

    bridge = MagicMock()
    def read_ram(fields):
        values = {
            "equipped_weapon_id": 0x01,
            "player_hp": 96,
            "player_anim": 0,
            "player_action_aux": 0,
            "player_recovery_timer": 0,
        }
        return {name: values[name] for name, _addr, _dtype in fields}

    bridge.read_ram.side_effect = read_ram
    bridge.step.return_value = (0, False)

    def fake_height(*_a, **kwargs):
        return False, 42, {
            "macro_path": kwargs["macro_path"],
            "aim_mode": kwargs["aim_mode"],
            "weapon": kwargs["weapon"],
            "outcome": "ok",
            "link_aim_held": True,
        }

    monkeypatch.setattr(
        "re1_rl.attack_macro._execute_standing_knife_height_macro", fake_height
    )
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, frames, report = execute_attack_down_macro(
        bridge, empty_sticky=empty, prev_hp=96, episode_start_hp=96,
    )
    assert not died
    assert frames == 42
    assert report["macro_path"] == "knife_crouch"
    assert report["aim_mode"] == "down"
    assert report["weapon"] == "knife"
    assert report["outcome"] == "ok"
    assert report["link_aim_held"] is True


def test_attack_down_beretta_holds_down_and_spends_ammo() -> None:
    from unittest.mock import MagicMock

    bridge = MagicMock()
    bridge.step.return_value = (0, False)
    ammo = {"qty": 0x0F02}  # beretta packed qty
    hook_seq = [
        (0x12, 0x03, 0),
        (0x13, 0x03, 0),
        (0x13, 0x03, 0),
        (0x14, 0x03, 0),
        (0x14, 0x03, 0),
        (0x13, 0x03, 0),
        (0, 0, 0),
        (0, 0, 0),
    ]
    hook_iter = iter(hook_seq * 8)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "equipped_weapon_id" in names:
            return {"equipped_weapon_id": 0x02}
        if "player_hp" in names:
            return {"player_hp": 96}
        if any(n.startswith("inv_slot_") for n in names):
            return {n: ammo["qty"] if n == "inv_slot_0" else 0 for n in names}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        return {
            "player_anim": a,
            "player_action_aux": x,
            "player_recovery_timer": r,
        }

    def step(**kwargs):
        for btn in kwargs.get("frame_buttons") or []:
            if btn.get("cross") and btn.get("down") and btn.get("r1"):
                # Spend one round after fire pad.
                ammo["qty"] = 0x0E02
        return (0, False)

    bridge.read_ram.side_effect = read_ram
    bridge.step.side_effect = step
    empty = {k: False for k in ("up", "down", "left", "right", "square")}
    died, _frames, report = execute_attack_down_macro(
        bridge, empty_sticky=empty, prev_hp=96, episode_start_hp=96,
    )
    assert not died
    assert report["aim_mode"] == "down"
    assert report["outcome"] == "ok"
    assert report["ammo_spent"] == 1
    assert report["link_aim_held"] is True
    assert bridge.step.call_args_list[-1].kwargs["frame_buttons"][-1] == {"r1": True}
    saw_down_fire = False
    for call in bridge.step.call_args_list:
        for btn in call.kwargs.get("frame_buttons") or []:
            if btn.get("r1") and btn.get("down") and btn.get("cross"):
                saw_down_fire = True
    assert saw_down_fire
