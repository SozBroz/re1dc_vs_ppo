"""Story inventory USE at position-gated sites."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import SELECT_SLOT_BASE, USE_ACTION, action_mask
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    GOLD_EMBLEM_RETURN_PENALTY,
    STORY_ITEM_USE_BONUS,
    compute_reward,
)
from re1_rl.story_item_use import (
    ALCOVE_EMBLEM_SITE_ID,
    any_legal_story_use_slot,
    gold_emblem_return_detected,
    legal_story_use_slots,
    load_story_use_sites,
    matching_story_sites,
    slot_legal_for_story_use,
    story_use_macro_resolved,
    story_use_succeeded,
    annotate_story_use_success,
)

N_ACTIONS = 46  # attack_up@6, attack_down@45; no quickturn
MUSIC_NOTES_ID = 0x23
EMBLEM_ID = 0x1F
GOLD_EMBLEM_ID = 0x20
ALCOVE_X = 9750
ALCOVE_Z = 8600


def _inv(*slots: tuple[int, int]) -> list[tuple[int, int]]:
    out = list(slots)
    while len(out) < 8:
        out.append((0, 0))
    return out


def test_load_music_notes_piano_site() -> None:
    load_story_use_sites.cache_clear()
    sites = load_story_use_sites()
    piano = next(s for s in sites if s["id"] == "music_notes@10F_piano")
    assert piano["room"] == "10F"
    assert piano["item"] == "music_notes"
    assert piano["x"] == 9737
    assert piano["z"] == 8020


def test_story_use_masked_away_from_piano() -> None:
    inv = _inv((MUSIC_NOTES_ID, 1))
    assert not any_legal_story_use_slot(
        inv, room="10F", x=14000, z=3000, rewarded_site_ids=set()
    )
    m = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        current_hp=96,
        episode_start_hp=96,
        room_id="10F",
        player_x=14000,
        player_z=3000,
        rewarded_story_uses=set(),
    )
    assert not m[USE_ACTION]


def test_story_use_legal_at_piano() -> None:
    inv = _inv((0, 0), (0, 0), (0, 0), (MUSIC_NOTES_ID, 0))
    assert any_legal_story_use_slot(
        inv, room="10F", x=9737, z=8020, rewarded_site_ids=set()
    )
    m0 = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        current_hp=96,
        episode_start_hp=96,
        room_id="10F",
        player_x=9737,
        player_z=8020,
        rewarded_story_uses=set(),
    )
    assert m0[USE_ACTION]
    assert not m0[SELECT_SLOT_BASE]
    m1 = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        use_phase=1,
        current_hp=96,
        episode_start_hp=96,
        room_id="10F",
        player_x=9737,
        player_z=8020,
        rewarded_story_uses=set(),
    )
    assert m1[SELECT_SLOT_BASE + 3]


def test_story_use_requires_positive_qty() -> None:
    """Empty slot (no item id) must not afford USE."""
    inv = _inv((0, 0), (0, 0), (0, 0), (0, 0))
    assert not any_legal_story_use_slot(
        inv, room="10F", x=9737, z=8020, rewarded_site_ids=set()
    )


def test_story_use_not_repeatable_after_reward() -> None:
    inv = _inv((MUSIC_NOTES_ID, 1))
    rewarded = {"music_notes@10F_piano"}
    assert not any_legal_story_use_slot(
        inv, room="10F", x=9737, z=8020, rewarded_site_ids=rewarded
    )
    assert not matching_story_sites(
        room="10F",
        x=9737,
        z=8020,
        inventory=inv,
        rewarded_site_ids=rewarded,
    )


def test_story_use_success_consumed_item() -> None:
    site = {"item": "music_notes", "consumes": True}
    ok = story_use_succeeded(
        before={"scene_flag": 0x80, "msg_flag": 0, "in_control": True},
        after={"scene_flag": 0x80, "msg_flag": 0, "in_control": True},
        site=site,
        slot=0,
        inventory_before=[(MUSIC_NOTES_ID, 1)],
        inventory_after=[(0, 0)],
    )
    assert ok


def test_story_use_success_scene_change() -> None:
    site = {"item": "emblem", "consumes": False}
    ok = story_use_succeeded(
        before={"scene_flag": 0x80, "msg_flag": 0, "in_control": True},
        after={"scene_flag": 0x90, "msg_flag": 0, "in_control": False},
        site=site,
        slot=1,
        inventory_before=[(0, 0), (0x1F, 1)],
        inventory_after=[(0, 0), (0x1F, 1)],
    )
    assert ok


def test_story_use_macro_ignores_menu_msg_only() -> None:
    site = {"item": "music_notes", "consumes": True}
    assert not story_use_macro_resolved(
        before={"scene_flag": 0x80, "msg_flag": 0, "in_control": True, "in_item_menu": False},
        after={
            "scene_flag": 0x80,
            "msg_flag": 0x80,
            "in_control": False,
            "in_item_menu": True,
        },
        site=site,
        slot=0,
        inventory_before=[(MUSIC_NOTES_ID, 1)],
        inventory_after=[(MUSIC_NOTES_ID, 1)],
    )


def test_annotate_story_use_piano_consumed() -> None:
    prev = {
        "room_id": "10F",
        "x": 9737,
        "z": 8020,
        "scene_flag": 0x80,
        "msg_flag": 0,
        "in_control": True,
    }
    after = dict(prev)
    after["scene_flag"] = 0x81
    after["in_control"] = False
    inv_before = _inv((MUSIC_NOTES_ID, 1))
    inv_after = _inv((0, 0))
    out = annotate_story_use_success(
        after,
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
        rewarded_site_ids=set(),
    )
    assert out["story_use_success"] == "music_notes@10F_piano"


def test_story_use_reward_and_stagnation_reset() -> None:
    progress = ProgressTracker()
    progress._stagnation_frames = 50000
    prev = {"room_id": "10F", "hp": 96}
    cur = {
        "room_id": "10F",
        "hp": 96,
        "story_use_success": "music_notes@10F_piano",
        "in_control": True,
        "step_emulated_frames": 8,
        "reference_step_frames": 8,
    }
    rew, bd = compute_reward(prev, cur, planner=None, progress=progress, return_breakdown=True)
    assert bd["story_use"] == STORY_ITEM_USE_BONUS
    assert rew > 0.9
    assert progress.stagnation_frames == 0
    assert not progress.claim_story_use_bonus("music_notes@10F_piano")
    rew2, bd2 = compute_reward(prev, cur, planner=None, progress=progress, return_breakdown=True)
    assert bd2["story_use"] == 0.0


def test_story_use_mask_allows_movement_while_pending() -> None:
    inv = _inv((MUSIC_NOTES_ID, 1))
    m = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        use_phase=1,
        current_hp=96,
        episode_start_hp=96,
        room_id="10F",
        player_x=9737,
        player_z=8020,
        rewarded_story_uses=set(),
    )
    assert m[1]  # forward — reorient before slot pick
    assert m[3]  # turn_left
    assert m[SELECT_SLOT_BASE]


def test_story_use_legal_without_anim_ready() -> None:
    inv = _inv((MUSIC_NOTES_ID, 1))
    m = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        current_hp=96,
        episode_start_hp=96,
        room_id="10F",
        player_x=9737,
        player_z=8020,
        rewarded_story_uses=set(),
        player_anim=0x13,
        player_aux=0x04,
        player_recovery=8,
    )
    assert m[USE_ACTION]


def test_failed_story_use_stays_affordant() -> None:
    inv = _inv((MUSIC_NOTES_ID, 1))
    assert any_legal_story_use_slot(
        inv, room="10F", x=9737, z=8020, rewarded_site_ids=set()
    )
    site = {"item": "music_notes", "consumes": True}
    failed = not story_use_succeeded(
        before={"scene_flag": 0x80, "msg_flag": 0, "in_control": True},
        after={"scene_flag": 0x80, "msg_flag": 0, "in_control": True},
        site=site,
        slot=0,
        inventory_before=[(MUSIC_NOTES_ID, 1)],
        inventory_after=[(MUSIC_NOTES_ID, 1)],
    )
    assert failed
    assert any_legal_story_use_slot(
        inv, room="10F", x=9737, z=8020, rewarded_site_ids=set()
    )

    inv = _inv((MUSIC_NOTES_ID, 1))
    assert slot_legal_for_story_use(
        inv, 0, room="10F", x=9737, z=8020, rewarded_site_ids=set()
    )
    assert not slot_legal_for_story_use(
        inv, 0, room="10F", x=10000, z=10000, rewarded_site_ids=set()
    )


def test_env_action_masks_enables_story_use_at_piano() -> None:
    """RE1Env.action_masks must pass room/pos into story-use gating."""
    from unittest.mock import MagicMock, patch

    from gymnasium import spaces

    from re1_rl.action_mask import SELECT_SLOT_BASE, USE_ACTION
    from re1_rl.env import ACTION_NAMES, RE1Env
    from re1_rl.progress import ProgressTracker

    env = RE1Env.__new__(RE1Env)
    env.action_space = spaces.Discrete(len(ACTION_NAMES))
    env._prev_action = None
    env._progress = ProgressTracker()
    env._async_cutscene_skip = False
    env._skipping_flag = False
    env._use_phase = 0
    env._equip_phase = 0
    env._combine_phase = 0
    env._combine_slot_a = None
    env._episode_start_hp = 96
    env._box_cache = None
    env.bridge = MagicMock()
    env.bridge.read_ram.return_value = {"equipped_slot_1based": 0}

    inv = _inv((0, 0), (0, 0), (0, 0), (MUSIC_NOTES_ID, 0))
    pose = {
        "room_id": "10F",
        "x": 9737,
        "z": 8020,
        "hp": 96,
        "in_control": True,
        "enemies": [],
    }
    env._prev_state = dict(pose)

    with (
        patch("re1_rl.env.read_knife_hooks", return_value=(0x0D, 0x01, 0)),
        patch("re1_rl.attack_macro.read_equipped_weapon", return_value=0),
        patch("re1_rl.item_box.read_inventory", return_value=inv),
        patch("re1_rl.item_box.is_box_room", return_value=False),
        patch("re1_rl.weapon_equip.policy_inventory", side_effect=lambda raw: raw),
    ):
        mask = env.action_masks(pose)
        assert mask[USE_ACTION]
        assert not mask[SELECT_SLOT_BASE]

        far = dict(pose)
        far["x"] = 10000
        far["z"] = 10000
        mask_far = env.action_masks(far)
        assert not mask_far[USE_ACTION]


def test_alcove_site_matches_gold_emblem_pickup_coords() -> None:
    load_story_use_sites.cache_clear()
    sites = load_story_use_sites()
    alcove = next(s for s in sites if s["id"] == ALCOVE_EMBLEM_SITE_ID)
    assert alcove["room"] == "10F"
    assert alcove["item"] == "emblem"
    assert alcove["x"] == ALCOVE_X
    assert alcove["z"] == ALCOVE_Z
    # Pickup table (data/rdt_item_positions.json 10F:gold_emblem) uses same stand.
    assert alcove["x"] == 9750 and alcove["z"] == 8600


def test_emblem_use_legal_at_alcove() -> None:
    inv = _inv((EMBLEM_ID, 0), (GOLD_EMBLEM_ID, 0))
    assert any_legal_story_use_slot(
        inv, room="10F", x=ALCOVE_X, z=ALCOVE_Z, rewarded_site_ids=set()
    )
    assert slot_legal_for_story_use(
        inv, 0, room="10F", x=ALCOVE_X, z=ALCOVE_Z, rewarded_site_ids=set()
    )
    m = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        current_hp=96,
        episode_start_hp=96,
        room_id="10F",
        player_x=ALCOVE_X,
        player_z=ALCOVE_Z,
        rewarded_story_uses=set(),
    )
    assert m[USE_ACTION]


def test_annotate_emblem_alcove_pays_story_use() -> None:
    prev = {
        "room_id": "10F",
        "x": ALCOVE_X,
        "z": ALCOVE_Z,
        "scene_flag": 0x80,
        "msg_flag": 0,
        "in_control": True,
    }
    after = dict(prev)
    after["scene_flag"] = 0x91
    after["in_control"] = False
    # Swap: both held before; wooden placed / scene resolves; gold stays.
    inv_before = _inv((EMBLEM_ID, 0), (GOLD_EMBLEM_ID, 0))
    inv_after = _inv((EMBLEM_ID, 0), (GOLD_EMBLEM_ID, 0))
    out = annotate_story_use_success(
        after,
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
        rewarded_site_ids=set(),
    )
    assert out.get("story_use_success") == ALCOVE_EMBLEM_SITE_ID
    assert not out.get("gold_emblem_return")

    progress = ProgressTracker()
    rew, bd = compute_reward(
        {"room_id": "10F", "hp": 96},
        {
            "room_id": "10F",
            "hp": 96,
            "story_use_success": ALCOVE_EMBLEM_SITE_ID,
            "in_control": True,
            "step_emulated_frames": 8,
            "reference_step_frames": 8,
        },
        planner=None,
        progress=progress,
        return_breakdown=True,
    )
    assert bd["story_use"] == STORY_ITEM_USE_BONUS
    assert bd["gold_emblem_return"] == 0.0
    assert rew > 0.9


def test_gold_only_put_back_pays_penalty() -> None:
    prev = {
        "room_id": "10F",
        "x": ALCOVE_X,
        "z": ALCOVE_Z,
        "scene_flag": 0x80,
        "msg_flag": 0,
        "in_control": True,
    }
    after = dict(prev)
    after["scene_flag"] = 0x81
    after["in_control"] = False
    inv_before = _inv((GOLD_EMBLEM_ID, 0))
    inv_after = _inv((0, 0))
    assert gold_emblem_return_detected(
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
    )
    out = annotate_story_use_success(
        after,
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
        rewarded_site_ids=set(),
    )
    assert out.get("gold_emblem_return") is True
    assert not out.get("story_use_success")

    progress = ProgressTracker()
    rew, bd = compute_reward(
        {"room_id": "10F", "hp": 96},
        {
            "room_id": "10F",
            "hp": 96,
            "gold_emblem_return": True,
            "in_control": True,
            "step_emulated_frames": 8,
            "reference_step_frames": 8,
        },
        planner=None,
        progress=progress,
        return_breakdown=True,
    )
    assert bd["gold_emblem_return"] == GOLD_EMBLEM_RETURN_PENALTY == -12.0
    assert bd["story_use"] == 0.0
    assert rew < -2.9


def test_swap_path_no_gold_return_penalty() -> None:
    """Having both + wooden USE (gold stays) must not pay gold put-back penalty."""
    prev = {
        "room_id": "10F",
        "x": ALCOVE_X,
        "z": ALCOVE_Z,
        "scene_flag": 0x80,
        "msg_flag": 0,
        "in_control": True,
    }
    after = dict(prev)
    after["scene_flag"] = 0x92
    after["in_control"] = False
    inv_before = _inv((EMBLEM_ID, 0), (GOLD_EMBLEM_ID, 0))
    inv_after = _inv((0, 0), (GOLD_EMBLEM_ID, 0))  # wooden consumed, gold kept
    assert not gold_emblem_return_detected(
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
    )
    out = annotate_story_use_success(
        after,
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
        rewarded_site_ids=set(),
    )
    assert out.get("story_use_success") == ALCOVE_EMBLEM_SITE_ID
    assert not out.get("gold_emblem_return")


def test_putting_gold_back_while_holding_emblem_is_return_not_story_use() -> None:
    """Wrong item USE: gold leaves at alcove → −3, not emblem story_use."""
    prev = {
        "room_id": "10F",
        "x": ALCOVE_X,
        "z": ALCOVE_Z,
        "scene_flag": 0x80,
        "msg_flag": 0,
        "in_control": True,
    }
    after = dict(prev)
    after["scene_flag"] = 0x93
    after["in_control"] = False
    inv_before = _inv((EMBLEM_ID, 0), (GOLD_EMBLEM_ID, 0))
    inv_after = _inv((EMBLEM_ID, 0), (0, 0))  # gold put back; wooden still held
    out = annotate_story_use_success(
        after,
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
        rewarded_site_ids=set(),
    )
    assert out.get("gold_emblem_return") is True
    assert not out.get("story_use_success")


def test_fireplace_gold_emblem_site_still_works() -> None:
    load_story_use_sites.cache_clear()
    sites = load_story_use_sites()
    fireplace = next(s for s in sites if s["id"] == "gold_emblem@105_fireplace")
    assert fireplace["room"] == "105"
    assert fireplace["item"] == "gold_emblem"

    inv = _inv((GOLD_EMBLEM_ID, 0))
    assert any_legal_story_use_slot(
        inv, room="105", x=2900, z=8100, rewarded_site_ids=set()
    )
    # Alcove put-back must not fire in the dining room.
    assert not gold_emblem_return_detected(
        prev_state={"room_id": "105", "x": 2900, "z": 8100},
        inventory_before=inv,
        inventory_after=_inv((0, 0)),
    )
    prev = {
        "room_id": "105",
        "x": 2900,
        "z": 8100,
        "scene_flag": 0x80,
        "msg_flag": 0,
        "in_control": True,
    }
    after = dict(prev)
    after["scene_flag"] = 0x88
    after["in_control"] = False
    out = annotate_story_use_success(
        after,
        prev_state=prev,
        inventory_before=inv,
        inventory_after=inv,  # consumes=false fireplace
        rewarded_site_ids=set(),
    )
    assert out.get("story_use_success") == "gold_emblem@105_fireplace"
    assert not out.get("gold_emblem_return")


def test_fireplace_site_covers_latest_quicksave4_pose() -> None:
    """Latest QS4 2026-07-17 22:03:09.497: 105 (3746,8186), gold in slot 3."""
    load_story_use_sites.cache_clear()
    inv = _inv(
        (0x01, 0),  # knife
        (0x02, 15),  # beretta
        (0x41, 1),  # first_aid_spray_alt
        (GOLD_EMBLEM_ID, 1),
        (0x0B, 15),  # handgun_bullets
    )
    px, pz = 3746, 8186
    sites = matching_story_sites(
        room="105", x=px, z=pz, inventory=inv, rewarded_site_ids=set()
    )
    assert [s["id"] for s in sites] == ["gold_emblem@105_fireplace"]
    assert legal_story_use_slots(
        inv, room="105", x=px, z=pz, rewarded_site_ids=set()
    ) == [3]
    assert any_legal_story_use_slot(
        inv, room="105", x=px, z=pz, rewarded_site_ids=set()
    )
    mask = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        current_hp=96,
        poisoned=False,
        episode_start_hp=96,
        in_control=True,
        room_id="105",
        player_x=px,
        player_z=pz,
        rewarded_story_uses=set(),
        use_phase=0,
    )
    assert bool(mask[USE_ACTION])
    submenu_mask = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        current_hp=96,
        poisoned=False,
        episode_start_hp=96,
        in_control=True,
        room_id="105",
        player_x=px,
        player_z=pz,
        rewarded_story_uses=set(),
        use_phase=1,
    )
    assert bool(submenu_mask[SELECT_SLOT_BASE + 3])
    assert not bool(submenu_mask[SELECT_SLOT_BASE + 2])
    # Once rewarded, site stays masked off.
    assert not any_legal_story_use_slot(
        inv,
        room="105",
        x=px,
        z=pz,
        rewarded_site_ids={"gold_emblem@105_fireplace"},
    )


def test_music_notes_piano_unaffected_by_alcove_logic() -> None:
    prev = {
        "room_id": "10F",
        "x": 9737,
        "z": 8020,
        "scene_flag": 0x80,
        "msg_flag": 0,
        "in_control": True,
    }
    after = dict(prev)
    after["scene_flag"] = 0x81
    after["in_control"] = False
    inv_before = _inv((MUSIC_NOTES_ID, 1), (GOLD_EMBLEM_ID, 0))
    inv_after = _inv((0, 0), (GOLD_EMBLEM_ID, 0))
    # Gold still held — not a put-back; piano USE still annotates.
    assert not gold_emblem_return_detected(
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
    )
    out = annotate_story_use_success(
        after,
        prev_state=prev,
        inventory_before=inv_before,
        inventory_after=inv_after,
        rewarded_site_ids=set(),
    )
    assert out.get("story_use_success") == "music_notes@10F_piano"
    assert not out.get("gold_emblem_return")
