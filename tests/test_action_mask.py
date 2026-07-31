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

    m = action_mask(N_ACTIONS, None, player_anim=0x0D, player_aux=0x01, player_recovery=0)

    assert m[ATTACK_ACTION]





def test_mask_allows_knife_from_idle() -> None:

    m = action_mask(N_ACTIONS, None)

    assert m[ATTACK_ACTION]





def test_mask_allows_knife_after_run_forward() -> None:

    run_forward = ACTION_NAMES.index("run_forward")

    m = action_mask(N_ACTIONS, run_forward)

    assert m[ATTACK_ACTION]





def test_mask_allows_knife_after_walk() -> None:

    forward = ACTION_NAMES.index("forward")

    m = action_mask(N_ACTIONS, forward)

    assert m[ATTACK_ACTION]





def test_env_action_masks_uses_ram_hooks() -> None:

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

    assert env.action_masks()[ATTACK_ACTION]





def test_action_mask_shape() -> None:

    m = action_mask(N_ACTIONS, None)

    assert m.shape == (N_ACTIONS,)

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

