"""Action masking for knife after run."""



from __future__ import annotations



import sys

from pathlib import Path



import numpy as np



sys.path.insert(0, str(Path(__file__).resolve().parents[1]))



from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION, ATTACK_UP_ACTION, action_mask

from re1_rl.env import ACTION_NAMES, RE1Env

N_ACTIONS = len(ACTION_NAMES)





def test_mask_blocks_knife_during_recovery_latch() -> None:

    m = action_mask(N_ACTIONS, None, player_anim=0, player_aux=0, player_recovery=2)

    assert not m[ATTACK_ACTION]





def test_mask_blocks_standing_recovery_latch() -> None:

    m = action_mask(N_ACTIONS, None, player_anim=0x0D, player_aux=0x01, player_recovery=2)

    assert not m[ATTACK_ACTION]





def test_mask_blocks_knife_during_swing_recovery_anim() -> None:

    m = action_mask(N_ACTIONS, None, player_anim=0x13, player_aux=0x04, player_recovery=8)

    assert not m[ATTACK_ACTION]





def test_mask_blocks_knife_from_unmapped_locomotion() -> None:

    m = action_mask(N_ACTIONS, None, player_anim=0x06, player_aux=0x00, player_recovery=0)

    assert not m[ATTACK_ACTION]

    m2 = action_mask(N_ACTIONS, None, player_anim=0x20, player_aux=0x00, player_recovery=0)

    assert not m2[ATTACK_ACTION]





def test_mask_allows_knife_from_standing_idle_hook() -> None:

    m = action_mask(
        N_ACTIONS, None, player_anim=0x0D, player_aux=0x01,
        player_recovery=0, equipped_weapon_id=0x01,
    )

    assert m[ATTACK_ACTION]





def test_mask_allows_knife_from_idle() -> None:

    m = action_mask(N_ACTIONS, None, equipped_weapon_id=0x01)

    assert m[ATTACK_ACTION]





def test_mask_allows_knife_after_run_forward() -> None:

    run_forward = ACTION_NAMES.index("run_forward")

    m = action_mask(N_ACTIONS, run_forward, equipped_weapon_id=0x01)

    assert m[ATTACK_ACTION]





def test_mask_allows_knife_after_walk() -> None:

    forward = ACTION_NAMES.index("forward")

    m = action_mask(N_ACTIONS, forward, equipped_weapon_id=0x01)

    assert m[ATTACK_ACTION]





def test_env_action_masks_fails_closed_without_ram_bridge() -> None:

    from gymnasium import spaces



    env = RE1Env.__new__(RE1Env)

    env.action_space = spaces.Discrete(len(ACTION_NAMES))

    env._prev_action = None

    env.bridge = None

    env._async_cutscene_skip = False

    env._skipping_flag = False

    # Near enemy so combat mask does not zero knife (default MASK_ATTACK_WITHOUT_ENEMIES).

    env._prev_state = {

        "in_control": True,

        "enemies": [

            {

                "slot": 0,

                "hp": 80,

                "in_room": 1,

                "combat_near": 1,

                "knife_near": 1,

                "dist": 800,

            }

        ],

    }

    assert not env.action_masks()[ATTACK_ACTION]





def test_action_mask_shape() -> None:

    m = action_mask(N_ACTIONS, None)

    assert N_ACTIONS == 45
    assert m.shape == (45,)

    assert m.dtype == np.bool_


def test_knife_vs_gun_near_bands_mask() -> None:
    """Knife masked out beyond knife band; gun still legal in gun band."""
    from re1_rl.action_mask import ATTACK_ACTION

    idle = dict(player_anim=0x0D, player_aux=0x01, player_recovery=0)
    # Mid-range: knife band empty, gun band armed.
    m_knife = action_mask(
        N_ACTIONS,
        None,
        **idle,
        equipped_weapon_id=0x01,
        knife_enemies_near=0,
        gun_enemies_near=1,
        mask_combat_without_enemies=True,
    )
    assert not m_knife[ATTACK_DOWN_ACTION]
    m_gun = action_mask(
        N_ACTIONS,
        None,
        **idle,
        equipped_weapon_id=0x02,  # beretta
        inventory=[(0x02, 15)],
        knife_enemies_near=0,
        gun_enemies_near=1,
        mask_combat_without_enemies=True,
    )
    assert m_gun[ATTACK_ACTION]
    # Close range: both armed.
    m_close = action_mask(
        N_ACTIONS,
        None,
        **idle,
        equipped_weapon_id=0x01,
        knife_enemies_near=1,
        gun_enemies_near=1,
        mask_combat_without_enemies=True,
    )
    assert m_close[ATTACK_DOWN_ACTION]


def test_mask_blocks_equip_during_gun_stable_aim() -> None:
    from re1_rl.action_mask import EQUIP_ACTION, USE_ACTION

    inv = [(0x01, 1), (0x02, 15)]
    m = action_mask(
        N_ACTIONS,
        None,
        player_anim=0x13,
        player_aux=0x03,
        player_recovery=0,
        equipped_weapon_id=0x02,
        inventory=inv,
    )
    assert m[ATTACK_ACTION]
    assert not m[EQUIP_ACTION]
    assert not m[USE_ACTION]


def test_mask_blocks_equip_during_gun_raise() -> None:
    from re1_rl.action_mask import EQUIP_ACTION

    inv = [(0x01, 1), (0x02, 15)]
    m = action_mask(
        N_ACTIONS,
        None,
        player_anim=0x12,
        player_aux=0x03,
        player_recovery=0,
        equipped_weapon_id=0x02,
        inventory=inv,
    )
    assert not m[EQUIP_ACTION]


def test_mask_allows_equip_from_standing_gun_idle() -> None:
    from re1_rl.action_mask import EQUIP_ACTION

    inv = [(0x01, 1), (0x02, 15)]
    m = action_mask(
        N_ACTIONS,
        None,
        player_anim=0x0D,
        player_aux=0x01,
        player_recovery=0,
        equipped_weapon_id=0x02,
        inventory=inv,
    )
    assert m[EQUIP_ACTION]

