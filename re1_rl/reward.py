"""Shaped reward for hierarchical RE1 control.

Exploration mode (checkpoint path disabled):
  - +NEW_ROOM_BONUS once per new room entered per episode
  - +NEW_DOCUMENT_EXAMINE_BONUS once per room on first document/file examine UI edge
  - +NEW_CUTSCENE_BONUS once per same-room scripted cutscene (room:cam:sN) per episode
  - Room-change door skips do not pay new_cutscene (discovery is new_room only)
  - Goal-vector checkpoint compass is zeroed in obs (see obs_encoder.encode_goal)
  - No waypoint / PBRS / wrong-room / retreat / success_room shaping
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from re1_rl.item_todo import canonical_item
from re1_rl.key_items import KEY_ITEM_NAMES
from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS

if TYPE_CHECKING:
    from re1_rl.planner import WaypointPlanner
    from re1_rl.progress import ProgressTracker
    from re1_rl.room_graph import RoomGraph

_KEY_ITEM_NAME_SET: frozenset[str] = frozenset(KEY_ITEM_NAMES)
_WEAPON_NAME_SET: frozenset[str] = frozenset(
    ITEM_IDS[i] for i in WEAPON_ITEM_IDS if i in ITEM_IDS
)

# Human-scale reward units: one route checkpoint = +1.2; step = 1/5000 of that.
CHECKPOINT_REWARD = 1.2
STEPS_PER_CHECKPOINT = 5000

STEP_PENALTY = -CHECKPOINT_REWARD / STEPS_PER_CHECKPOINT  # -0.00024
REFERENCE_STEP_FRAMES = 8

# Large progress payouts (imperator 2026-07-20: ×4 vs prior 3.0 / 1.5 scale).
# Absolute human-scale (not × CHECKPOINT); living cost / γ use CHECKPOINT_REWARD.
NEW_ROOM_BONUS = 12.0
NEW_CUTSCENE_BONUS = 6.0
# Document/file examine UI (gs=0x40808100): same +12 / 12m floor as new room.
NEW_DOCUMENT_EXAMINE_BONUS = 12.0

# Legacy aliases kept for tests / telemetry that import old names.
WAYPOINT_ROOM_BONUS = NEW_ROOM_BONUS

# Junk / ammo / herbs: modest crumb (not ×4 with large progress).
ITEM_PICKUP_BONUS = 0.15
# Keys / emblems / crests (room_items.json key_item=true).
KEY_ITEM_PICKUP_BONUS = 12.0
# Story inventory USE at a curated site (piano, fireplace, …).
STORY_ITEM_USE_BONUS = 12.0
# 10F alcove: put gold_emblem back without leaving the wooden emblem (anti-hack).
# Exact inverse of key-item pickup (+12); intended path is USE wooden emblem → +12.
GOLD_EMBLEM_RETURN_PENALTY = -KEY_ITEM_PICKUP_BONUS
# Every physical pickup of a gun/knife-class weapon (not ammo). Same scale as keys.
NEW_WEAPON_PICKUP_BONUS = 12.0
# The wall rack can toggle forever: taking the shotgun pays; replacing it
# removes exactly that reward. Repeating the loop is net zero before step cost.
# Re-takes after a return still claw ±NEW_WEAPON but do not re-extend idle.
SHOTGUN_RETURN_PENALTY = -NEW_WEAPON_PICKUP_BONUS
SHOTGUN_RACK_ROOMS: frozenset[str] = frozenset({"115", "116"})
# Idle contempt: no new room / document / cutscene / key / weapon / story / gallery.
# Start budget and all progress extensions: 12 min. Grace 3 min then 3→12 ramp.
# Frames @ 60 emulated fps (PS1 NTSC / BizHawk).
SOFTLOCK_PRE_KENNETH_FRAMES = 12 * 60 * 60
SOFTLOCK_POST_KENNETH_FRAMES = 12 * 60 * 60
# New room / document / key pickup / key use / first weapon: at least this idle cap.
SOFTLOCK_EXTENSION_FRAMES = 12 * 60 * 60
# Alias: max episode idle cap (tests of the full ramp).
SOFTLOCK_FRAME_THRESHOLD = SOFTLOCK_POST_KENNETH_FRAMES
# First 3 min of no-progress: no extra idle tax (living step cost only).
CONTEMPT_GRACE_FRAMES = 3 * 60 * 60

# Fine condition HP for Jill (real max band; not PLAYER_HP_MAX=140 RAM ceiling).
JILL_FINE_HP = 96
# Health punishment bucket: independent of CHECKPOINT_REWARD (progress stays 1.2).
# Full Fine→1 chip + death terminal = −1.0 total. Same chip/death ratio as before:
# 2/3 dense HP loss, 1/3 episode-end death.
HEALTH_PUNISHMENT_BUDGET = 1.0
SURVIVAL_BUDGET_SCALED = HEALTH_PUNISHMENT_BUDGET
NEAR_DEATH_DAMAGE_SCALED = (2.0 / 3.0) * SURVIVAL_BUDGET_SCALED  # ≈0.6667
DEATH_PENALTY_SCALED = (1.0 / 3.0) * SURVIVAL_BUDGET_SCALED  # ≈0.3333
DEATH_PENALTY = -DEATH_PENALTY_SCALED
# Sole Kenneth gate: illegal pre-Kenneth transition into Main Hall room 106.
# Fixed −0.05 (not scaled with large-progress ×4); terminates the episode.
MAIN_HALL_BEFORE_KENNETH_PENALTY = -0.05
# Doing-nothing contempt must not exceed death (else suicide beats softlock).
# Stepwise / ramp potency is 1/5 of the death budget.
CONTEMPT_BUDGET_SCALED = DEATH_PENALTY_SCALED / 5.0
SOFTLOCK_TIMEOUT_PENALTY = -CONTEMPT_BUDGET_SCALED

ENEMY_DAMAGE_REWARD = CHECKPOINT_REWARD / 200
ENEMY_KILL_REWARD = 0.2 * CHECKPOINT_REWARD
# Flat legacy miss flags (unused); ammo waste uses per-weapon clip table below.
ATTACK_MISS_PENALTY = 0.0
KNIFE_MISS_PENALTY = 0.0
AMMO_WASTE_PENALTY = 0.0

# Miss / ammo-waste tax: per missed round =
#   -0.5 * ITEM_PICKUP_BONUS / clip_size
# clip_size = magazine / typical ammo pack amortized against one junk pickup.
# Knife and flamethrower omitted (no discrete clip pack for this tax).
# Bazooka chamber capacity is 1 (WEAPON_CLIP_CAPACITY); miss tax uses pack size 6
# (room_items acid_rounds count=6; DC / Evil Resource).
MISS_TAX_CLIP_SIZE: dict[int, int] = {
    0x02: 15,  # beretta / handgun
    0x03: 7,   # shotgun
    0x04: 6,   # colt python dumdum
    0x05: 6,   # colt python magnum
    0x07: 6,   # bazooka acid
    0x08: 6,   # bazooka explosive
    0x09: 6,   # bazooka flame
    0x0A: 6,   # rocket launcher
}

REWARD_SCALE = 1.0

# Dense softlock ramp is already in the scalar reward (bd["softlock"]); one γ.
# Half-life ≈ 45s emulated time incl. living cost: γ_eff := γ + c with
# c = STEP_PENALTY (−0.00024), n = 45 / (8/60) = 337.5 ref steps →
# γ = 0.5^(1/n) − c ≈ 0.998188. (Delayed-+1 PV solve differs slightly; ship γ_eff.)
RL_GAMMA = 0.998188

# Per-HP chip from health bucket / (Fine→1 span). Independent of CHECKPOINT_REWARD.
HP_LOSS_SCALE = NEAR_DEATH_DAMAGE_SCALED / (JILL_FINE_HP - 1)
# Heal is the exact inverse of damage (same scale, opposite sign).
HP_GAIN_SCALE = HP_LOSS_SCALE
# Legacy export; heal is linear now (kept so old imports do not break).
HEAL_LOG_CURVE_EXPONENT = 1.0


def hp_heal_reward(hp_delta: int) -> float:
    """Heal reward: inverse of the per-HP damage penalty (linear)."""
    if hp_delta <= 0:
        return 0.0
    return HP_GAIN_SCALE * float(hp_delta)


def ammo_waste_per_missed_round(weapon_id: int) -> float:
    """Per-round miss tax for ``weapon_id`` (0 if knife / unknown / no clip)."""
    clip = MISS_TAX_CLIP_SIZE.get(int(weapon_id) & 0xFF)
    if clip is None or clip <= 0:
        return 0.0
    return -0.5 * ITEM_PICKUP_BONUS / float(clip)


def ammo_waste_penalty(weapon_id: int, rounds_spent: int) -> float:
    """Total ammo-waste penalty for a missed attack that spent ``rounds_spent``."""
    rounds = int(rounds_spent)
    if rounds <= 0:
        return 0.0
    return ammo_waste_per_missed_round(weapon_id) * float(rounds)

# Disabled checkpoint-path terms (exported for tests that assert they stay off).
WRONG_ROOM_PENALTY = -0.5 * CHECKPOINT_REWARD
RETREAT_PENALTY = -0.5 * CHECKPOINT_REWARD
SUCCESS_ROOM_BONUS = 100.0 * CHECKPOINT_REWARD
PBRS_GRAPH_WEIGHT = CHECKPOINT_REWARD / 20
PBRS_DOOR_WEIGHT = 0.5 * CHECKPOINT_REWARD
SHAPING_GAMMA = 1.0
UNKNOWN_HOPS = 8.0
DIST_NORM = 4096.0

# When False, compute_reward ignores checkpoint-path shaping and planner advances.
ENABLE_CHECKPOINT_PATH = False


def softlock_frame_threshold(progress: ProgressTracker | None) -> int:
    """Idle truncate cap: 12 min from start; ≥12 min after room/key/weapon/use."""
    if progress is None:
        return SOFTLOCK_PRE_KENNETH_FRAMES
    if progress.kenneth_gate_breached:
        return SOFTLOCK_PRE_KENNETH_FRAMES
    from re1_rl.cutscene_reward import kenneth_cutscene_seen

    if kenneth_cutscene_seen(progress.rewarded_cutscenes):
        base = SOFTLOCK_POST_KENNETH_FRAMES
    else:
        base = SOFTLOCK_PRE_KENNETH_FRAMES
    extended = int(getattr(progress, "softlock_cap_frames", 0) or 0)
    if extended > 0:
        return max(base, extended)
    return base


def stagnation_episode_timeout(
    progress: ProgressTracker | None,
    *,
    threshold: int | None = None,
) -> bool:
    """True when idle frames hit the stagnation episode cap (caller sets truncated)."""
    if progress is None:
        return False
    thr = softlock_frame_threshold(progress) if threshold is None else int(threshold)
    return progress.stagnation_timed_out(threshold=thr)


def contempt_spent_at(
    frames: int,
    *,
    grace: int = CONTEMPT_GRACE_FRAMES,
    threshold: int = SOFTLOCK_FRAME_THRESHOLD,
    budget: float = CONTEMPT_BUDGET_SCALED,
) -> float:
    """Cumulative idle contempt spent after ``frames`` of no progress.

    Grace is free. From grace→threshold a linear per-frame rate integrates to
    ``budget`` (quadratic spent curve). If threshold≤grace (short test caps),
    the full budget applies as a single step when frames reach threshold.
    """
    frames = max(0, int(frames))
    threshold = max(0, int(threshold))
    grace = min(max(0, int(grace)), threshold)
    budget = float(budget)
    if budget <= 0.0:
        return 0.0
    ramp = threshold - grace
    # No ramp room (short test caps): full budget on the timeout step.
    if ramp <= 0:
        return budget if frames >= threshold else 0.0
    if frames <= grace:
        return 0.0
    if frames >= threshold:
        return budget
    x = float(frames - grace)
    return budget * (x / float(ramp)) ** 2


def contempt_penalty_delta(
    frames_before: int,
    frames_after: int,
    *,
    grace: int = CONTEMPT_GRACE_FRAMES,
    threshold: int = SOFTLOCK_FRAME_THRESHOLD,
    budget: float = CONTEMPT_BUDGET_SCALED,
) -> float:
    """Negative reward for idle-frame advance; 0 if the clock did not increase."""
    before = max(0, int(frames_before))
    after = max(0, int(frames_after))
    if after <= before:
        return 0.0
    spent = contempt_spent_at(
        after, grace=grace, threshold=threshold, budget=budget
    ) - contempt_spent_at(
        before, grace=grace, threshold=threshold, budget=budget
    )
    return -float(spent)


def potential(
    state: dict[str, Any],
    planner: WaypointPlanner,
    graph: RoomGraph | None,
) -> tuple[float, float]:
    """(phi_graph, phi_door) for a state. Higher = closer to objective."""
    if not ENABLE_CHECKPOINT_PATH or graph is None:
        return 0.0, 0.0
    room = str(state.get("room_id", ""))
    goal = planner.next_waypoint_room()
    if goal is None:
        return 0.0, 0.0

    hops = graph.hop_distance(room, str(goal))
    if hops is None:
        phi_g = -max(UNKNOWN_HOPS, float(graph.diameter) + 2.0)
    else:
        phi_g = -float(hops)

    phi_d = 0.0
    door = graph.exit_toward(room, str(goal))
    if door is not None and "x" in state and "z" in state:
        dist = math.hypot(door.x - state["x"], door.z - state["z"])
        phi_d = -min(dist / DIST_NORM, 1.0)

    return PBRS_GRAPH_WEIGHT * phi_g, PBRS_DOOR_WEIGHT * phi_d


def compute_reward(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    planner: WaypointPlanner,
    *,
    progress: ProgressTracker | None = None,
    graph: RoomGraph | None = None,
    softlock_threshold: int | None = None,
    success_room: str | None = None,
    return_breakdown: bool = False,
) -> float | tuple[float, dict[str, float]]:
    """Compute scalar reward from symbolic state dicts."""
    del success_room  # checkpoint success_room bonus disabled
    if softlock_threshold is None:
        softlock_threshold = softlock_frame_threshold(progress)

    step_frames = int(state.get("step_emulated_frames", REFERENCE_STEP_FRAMES))
    ref_frames = int(state.get("reference_step_frames", REFERENCE_STEP_FRAMES))
    step_scale = max(step_frames, 0) / max(ref_frames, 1)

    bd: dict[str, float] = {
        "step": STEP_PENALTY * step_scale,
        "pbrs_graph": 0.0,
        "pbrs_door": 0.0,
        "waypoint": 0.0,
        "new_room": 0.0,
        "document_examine": 0.0,
        "new_cutscene": 0.0,
        "retreat": 0.0,
        "wrong_room": 0.0,
        "item": 0.0,
        "key_item": 0.0,
        "story_use": 0.0,
        "gallery": 0.0,
        "gold_emblem_return": 0.0,
        "shotgun_return": 0.0,
        "new_weapon": 0.0,
        "success_room": 0.0,
        "hp": 0.0,
        "death": 0.0,
        "main_hall_before_kenneth": 0.0,
        "softlock": 0.0,
        "enemy_damage": 0.0,
        "enemy_kill": 0.0,
        "attack_miss": 0.0,
        "ammo_waste": 0.0,
    }

    prev_room = str(prev_state.get("room_id", ""))
    room = str(state.get("room_id", ""))
    room_changed = room != prev_room

    # Kenneth gate: the first illegal pre-Kenneth entry into 106 pays -0.05 and
    # irreversibly disables positive rewards/extensions for this episode.
    # Never mark 106 visited on an illegal transition.
    illegal_main_hall = False
    new_kenneth_gate_breach = False
    if progress is not None:
        from re1_rl.cutscene_reward import (
            illegal_main_hall_before_kenneth_transition,
        )

        illegal_main_hall = illegal_main_hall_before_kenneth_transition(
            prev_room,
            room,
            rewarded_cutscenes=progress.rewarded_cutscenes,
            visited_rooms=progress.visited_rooms,
        )
        if illegal_main_hall and not state.get("dead"):
            new_kenneth_gate_breach = progress.breach_kenneth_gate()
            softlock_threshold = softlock_frame_threshold(progress)

    is_new_room = False
    if progress is not None and not illegal_main_hall:
        is_new_room = progress.first_visit(
            room,
            at_waypoint=0,
            at_route_seq=None,
        )

    if ENABLE_CHECKPOINT_PATH and graph is not None:
        pg_prev, pd_prev = potential(prev_state, planner, graph)
        pg_now, pd_now = potential(state, planner, graph)
        bd["pbrs_graph"] = SHAPING_GAMMA * pg_now - pg_prev
        bd["pbrs_door"] = SHAPING_GAMMA * pd_now - pd_prev

        while True:
            completed_idx = planner.waypoint_index
            if not planner.advance_if_success(
                state, progress=progress, prev_state=prev_state
            ):
                break
            if progress is not None:
                progress.on_waypoint_advanced()
            claimed = progress.claim_waypoint_bonus(completed_idx) \
                if progress is not None else True
            if claimed:
                bd["waypoint"] += WAYPOINT_ROOM_BONUS

        target = planner.next_waypoint_room()
        if room_changed and target is not None:
            if prev_room == str(target) and room != str(target) \
                    and planner.next_waypoint_room() == str(target):
                bd["retreat"] = RETREAT_PENALTY
            elif room != str(target):
                off_route = graph.hop_distance(room, str(target)) is None
                if off_route and graph.knows_room(str(target)):
                    claimed = progress.claim_offroute_penalty(room) \
                        if progress is not None else True
                    if claimed:
                        bd["wrong_room"] = WRONG_ROOM_PENALTY

    if room_changed and is_new_room:
        bd["new_room"] += NEW_ROOM_BONUS
    # Spawn room (dining 105 in m0): visited at reset; pay +new_room once on the
    # first compute_reward of the episode so Wesker/door settles cannot steal
    # credit for "first time in dining".
    if progress is not None and progress.claim_spawn_room_bonus():
        bd["new_room"] += NEW_ROOM_BONUS

    # Document/file examine overlay: rising edge into mode=0x40 / gs=0x40808100.
    # Assumption: all books share that signature (no stable document ID hunted
    # yet). Anti-farm: once per room per episode — reopen in the same room does
    # not re-pay; a first open in a different room can.
    if progress is not None and not progress.kenneth_gate_breached:
        from re1_rl.ram_skip import document_examine_ui_from_ram

        entered_document = (
            document_examine_ui_from_ram(state)
            and not document_examine_ui_from_ram(prev_state)
        )
        if entered_document and progress.claim_document_examine_bonus(room):
            bd["document_examine"] = NEW_DOCUMENT_EXAMINE_BONUS

    if "new_items" in state:
        new_items = set(state["new_items"])
    else:
        new_items = set(state.get("inventory", [])) - set(prev_state.get("inventory", []))
    acquired_key_or_weapon = False
    # First acquire of a weapon type this episode: 12m idle floor + stagnation reset.
    # Shotgun rack re-takes still pay NEW_WEAPON (clawed back on return) but do not
    # count as exploration progress — blocks idle-clock / extension farms.
    weapon_progress = False
    for raw in new_items:
        name = canonical_item(str(raw))
        if name in _KEY_ITEM_NAME_SET:
            bd["key_item"] += KEY_ITEM_PICKUP_BONUS
            acquired_key_or_weapon = True
        elif name in _WEAPON_NAME_SET:
            bd["new_weapon"] += NEW_WEAPON_PICKUP_BONUS
            acquired_key_or_weapon = True
            if progress is not None and progress.claim_weapon_progress(name):
                weapon_progress = True
        else:
            bd["item"] += ITEM_PICKUP_BONUS

    # Pickup owns its channel (skill a): never also claim new_cutscene this step.
    cutscene_key = state.get("cutscene_key") if not new_items else None
    if (
        cutscene_key
        and progress is not None
        and not progress.kenneth_gate_breached
    ):
        if progress.claim_cutscene_bonus(str(cutscene_key)):
            bd["new_cutscene"] = NEW_CUTSCENE_BONUS

    if progress is not None:
        room_now = str(state.get("room_id", "") or "")
        progress.clear_pickup_cutscene_block_if_left(room_now)
        if acquired_key_or_weapon:
            progress.note_pickup_cutscene_block(room_now)

    prev_inventory = {
        canonical_item(str(name)) for name in prev_state.get("inventory", [])
    }
    inventory = {
        canonical_item(str(name)) for name in state.get("inventory", [])
    }
    if progress is not None:
        bd["gallery"] = progress.gallery_step_reward(
            prev_room=prev_room,
            room=room,
            prev_raw=int(prev_state.get("gallery_progress", 0) or 0),
            raw=int(state.get("gallery_progress", 0) or 0),
            prev_confirm=int(prev_state.get("gallery_confirm", 0) or 0),
            confirm=int(state.get("gallery_confirm", 0) or 0),
            star_crest_held="star_crest" in inventory,
        )
    room = str(state.get("room_id", "") or "")
    shotgun_removed_at_rack = (
        room in SHOTGUN_RACK_ROOMS
        and "shotgun" in prev_inventory
        and "shotgun" not in inventory
        and not state.get("dead")
        and int(state.get("hp", 0) or 0) > 0
    )
    if progress is not None:
        if progress._shotgun_return_armed is None:
            progress._shotgun_return_armed = "shotgun" in prev_inventory
        if "shotgun" in inventory:
            progress._shotgun_return_armed = True
        shotgun_removed_at_rack = (
            shotgun_removed_at_rack and progress._shotgun_return_armed
        )
    if shotgun_removed_at_rack:
        bd["shotgun_return"] = SHOTGUN_RETURN_PENALTY
        if progress is not None:
            progress._shotgun_return_armed = False

    story_use_site = state.get("story_use_success")
    if story_use_site and progress is not None:
        if progress.claim_story_use_bonus(str(story_use_site)):
            bd["story_use"] = STORY_ITEM_USE_BONUS

    if state.get("gold_emblem_return"):
        bd["gold_emblem_return"] = GOLD_EMBLEM_RETURN_PENALTY

    prev_hp = int(prev_state.get("hp", 0))
    hp = int(state.get("hp", 0))
    hp_delta = hp - prev_hp
    if hp_delta < 0:
        bd["hp"] = HP_LOSS_SCALE * hp_delta
    elif hp_delta > 0 and prev_hp > 0:
        # Ignore bogus HP jumps from menu/cutscene init (prev_hp==0).
        bd["hp"] = hp_heal_reward(hp_delta)

    # Actual death owns the ordinary death channel. Otherwise the first Kenneth
    # gate breach contributes once under its explicit telemetry key.
    if state.get("dead"):
        bd["death"] = DEATH_PENALTY
    elif new_kenneth_gate_breach:
        bd["main_hall_before_kenneth"] = MAIN_HALL_BEFORE_KENNETH_PENALTY

    enemy_damage = int(state.get("enemy_damage", 0) or 0)
    if enemy_damage > 0:
        bd["enemy_damage"] = ENEMY_DAMAGE_REWARD * enemy_damage
    enemy_kills = int(state.get("enemy_kills", 0) or 0)
    if enemy_kills > 0:
        bd["enemy_kill"] = ENEMY_KILL_REWARD * enemy_kills

    # Miss / ammo waste: only on attack_missed with ammo spent. Hits pay no tax.
    # Knife uses knife_swing_missed (no clip) — never taxed here.
    if state.get("attack_missed"):
        rounds = int(state.get("ammo_spent", 0) or 0)
        if rounds > 0:
            wid = int(state.get("equipped_weapon_id", 0) or 0)
            bd["ammo_waste"] = ammo_waste_penalty(wid, rounds)

    if progress is not None and progress.kenneth_gate_breached:
        for term, value in bd.items():
            if value > 0.0:
                bd[term] = 0.0

    if progress is not None and not state.get("dead"):
        # Room / document / key get / key use / first weapon → 12 min idle floor.
        if (
            bd["new_room"] != 0.0
            or bd["document_examine"] != 0.0
            or bd["key_item"] != 0.0
            or bd["story_use"] != 0.0
            or weapon_progress
        ):
            progress.note_softlock_extension(SOFTLOCK_EXTENSION_FRAMES)
            softlock_threshold = softlock_frame_threshold(progress)
        made_progress = (
            bd["new_room"] != 0.0
            or bd["document_examine"] != 0.0
            or bd["new_cutscene"] != 0.0
            or bd["key_item"] != 0.0
            or bd["story_use"] != 0.0
            or bd["gallery"] > 0.0
            or weapon_progress
        )
        # Pause idle clock during cutscenes / doors (not in_control).
        frames_before = progress.stagnation_frames
        if made_progress or bool(state.get("in_control", True)):
            progress.note_stagnation_step(
                made_progress=made_progress,
                step_frames=step_frames,
            )
            if not made_progress:
                bd["softlock"] = contempt_penalty_delta(
                    frames_before,
                    progress.stagnation_frames,
                    threshold=softlock_threshold,
                )

    reward = float(sum(bd.values())) * REWARD_SCALE
    if return_breakdown:
        return reward, bd
    return reward
