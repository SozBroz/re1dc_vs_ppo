"""Action masking for knife after run."""



from __future__ import annotations



import sys

from pathlib import Path



import numpy as np



sys.path.insert(0, str(Path(__file__).resolve().parents[1]))



from re1_rl.action_mask import KNIFE_SWING_ACTION, action_mask

from re1_rl.env import ACTION_NAMES, RE1Env





def test_mask_blocks_knife_during_recovery_latch() -> None:

    m = action_mask(11, None, player_anim=0, player_aux=0, player_recovery=2)

    assert not m[KNIFE_SWING_ACTION]





def test_mask_blocks_standing_recovery_latch() -> None:

    m = action_mask(11, None, player_anim=0x0D, player_aux=0x01, player_recovery=2)

    assert not m[KNIFE_SWING_ACTION]





def test_mask_blocks_knife_during_swing_recovery_anim() -> None:

    m = action_mask(11, None, player_anim=0x13, player_aux=0x04, player_recovery=8)

    assert not m[KNIFE_SWING_ACTION]





def test_mask_blocks_knife_from_unmapped_locomotion() -> None:

    m = action_mask(11, None, player_anim=0x06, player_aux=0x00, player_recovery=0)

    assert not m[KNIFE_SWING_ACTION]

    m2 = action_mask(11, None, player_anim=0x20, player_aux=0x00, player_recovery=0)

    assert not m2[KNIFE_SWING_ACTION]





def test_mask_allows_knife_from_standing_idle_hook() -> None:

    m = action_mask(11, None, player_anim=0x0D, player_aux=0x01, player_recovery=0)

    assert m[KNIFE_SWING_ACTION]





def test_mask_allows_knife_from_idle() -> None:

    m = action_mask(11, None)

    assert m[KNIFE_SWING_ACTION]





def test_mask_allows_knife_after_run_forward() -> None:

    run_forward = ACTION_NAMES.index("run_forward")

    m = action_mask(11, run_forward)

    assert m[KNIFE_SWING_ACTION]





def test_mask_allows_knife_after_walk() -> None:

    forward = ACTION_NAMES.index("forward")

    m = action_mask(11, forward)

    assert m[KNIFE_SWING_ACTION]





def test_env_action_masks_uses_ram_hooks() -> None:

    from gymnasium import spaces



    env = RE1Env.__new__(RE1Env)

    env.action_space = spaces.Discrete(len(ACTION_NAMES))

    env._prev_action = None

    env.bridge = None

    assert env.action_masks()[KNIFE_SWING_ACTION]





def test_action_mask_shape() -> None:

    m = action_mask(11, None)

    assert m.shape == (11,)

    assert m.dtype == np.bool_

