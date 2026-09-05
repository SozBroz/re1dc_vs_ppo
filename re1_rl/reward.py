"""Shaped reward for hierarchical RE1 control and one-leg rails training."""

from __future__ import annotations

import json
import math
from pathlib import Path
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

# Legacy unit label only (docs / old telemetry). Live magnitudes below are
# independent statics — do not derive progress / survival / step / combat from this.
CHECKPOINT_REWARD = 1.2
STEPS_PER_CHECKPOINT = 5000  # legacy label; STEP_PENALTY is an independent static

# Living cost: independent static (historically −1.2/5000).
STEP_PENALTY = -0.00024
REFERENCE_STEP_FRAMES = 8

# Progress payouts (imperator 2026-07-20 delinked table).
# Each signal owns its float; not scaled from CHECKPOINT_REWARD.
# Disabled 2026-08-16: CP already pays the cell.
NEW_ROOM_BONUS = 0.0
# Disabled 2026-08-16: interact/cutscene farm. Observation + claim still run
# (Kenneth / ledger). Pay and idle-extend / stagnation-reset are off.
NEW_CUTSCENE_BONUS = 0.0
# Disabled 2026-08-16: interact examine farm (+4 / +12m was the live spam).
NEW_DOCUMENT_EXAMINE_BONUS = 0.0

# Legacy aliases kept for tests / telemetry that import old names.
WAYPOINT_ROOM_BONUS = 4.0

# Junk / herbs: modest crumb.
ITEM_PICKUP_BONUS = 0.15
# Ammunition stacks (handgun bullets, shells, launcher packs, …).
AMMO_PICKUP_BONUS = 2.0
# Box withdraw/deposit: no generic transfer reward and no pickup-channel pay
# (imperator 2026-08-13). Exception: yawn_box_prep_118 banks wind crest +
# armor key for +YAWN_BOX_KEY_DEPOSIT_BONUS each (imperator 2026-08-21).
BOX_WITHDRAW_BONUS = 0.0
YAWN_BOX_KEY_DEPOSIT_BONUS = 2.0
# Completed typewriter save (ink-ribbon consume + save cinema + stable control).
TYPEWRITER_SAVE_BONUS = 0.3
# Keys / emblems / crests — disabled 2026-08-16 (CP already pays the cell).
KEY_ITEM_PICKUP_BONUS = 0.0
# Leaving inventory after a paid key pickup (not story USE / box deposit).
KEY_ITEM_RETURN_PENALTY = -KEY_ITEM_PICKUP_BONUS
# Story inventory USE — disabled 2026-08-16 (CP already pays the cell).
STORY_ITEM_USE_BONUS = 0.0
# Dining 2F balcony statue knocked — disabled 2026-08-16 (CP already pays).
DINING_STATUE_BONUS = 0.0
# Dense statue→drop/final distance shaping (clipped ±0.5/step, ~+10 full shove).
DINING_STATUE_PROGRESS_STEP = 0.5
DINING_STATUE_PROGRESS_BUDGET = 10.0
# 10F alcove: put gold_emblem back without leaving the wooden emblem (anti-hack).
# Intended path is USE wooden emblem → +4 story use.
GOLD_EMBLEM_RETURN_PENALTY = -4.0
# Weapon pickup — disabled 2026-08-16 (CP already pays). Claim still tracks.
NEW_WEAPON_PICKUP_BONUS = 0.0
# The wall rack can toggle forever off-rails: taking the shotgun pays; replacing
# it removes exactly that reward. On yawn rails, put-back is a cell-fail
# terminal (−4, zero same-step positives, end episode) so a later CP cannot
# file without the gun. Re-takes after a return still claw ±NEW_WEAPON but do
# not re-extend idle.
SHOTGUN_RETURN_PENALTY = -4.0
SHOTGUN_RACK_ROOMS: frozenset[str] = frozenset({"115", "116"})
# Idle contempt: ammo / gallery / statue-progress / rails checkpoint still
# reset or extend. Room / weapon / statue-knock / cutscene / document / key /
# story-use do not (2026-08-16).
# Start budget and progress extensions: 12 min. Grace 3 min then ramp to cap.
# Frames @ 60 emulated fps (PS1 NTSC / BizHawk).
SOFTLOCK_PRE_KENNETH_FRAMES = 12 * 60 * 60
SOFTLOCK_POST_KENNETH_FRAMES = 12 * 60 * 60
# New room / document / key / story use / weapon / rails checkpoint: idle floor.
SOFTLOCK_EXTENSION_FRAMES = 12 * 60 * 60
# Alias: max episode idle cap (tests of the full ramp).
SOFTLOCK_FRAME_THRESHOLD = SOFTLOCK_POST_KENNETH_FRAMES
# Hard episode wall extension when a rails cell/checkpoint completes (8 f/step).
CHECKPOINT_MAX_STEPS_EXTENSION = SOFTLOCK_EXTENSION_FRAMES // 8  # 5400 steps / 12 min
# First 3 min of no-progress: no extra idle tax (living step cost only).
CONTEMPT_GRACE_FRAMES = 3 * 60 * 60

JILL_FINE_HP = 96  # Jill max HP (Chris uses PLAYER_HP_MAX=140)
# Survival budget 4.0 (4× prior); same chip/death ratio (2/3 dense Fine→1, 1/3 death).
# Literals below are precomputed; not derived from CHECKPOINT_REWARD.
SURVIVAL_BUDGET_SCALED = 4.0
NEAR_DEATH_DAMAGE_SCALED = 2.6666666666666665  # 8/3
DEATH_PENALTY_SCALED = 1.3333333333333333  # 4/3
DEATH_PENALTY = -1.3333333333333333
# Sole Kenneth gate: illegal pre-Kenneth transition into Main Hall room 106.
MAIN_HALL_BEFORE_KENNETH_PENALTY = -0.05
# Idle contempt budget: death/5 → static literal under new death.
CONTEMPT_BUDGET_SCALED = 0.26666666666666666  # |DEATH|/5
SOFTLOCK_TIMEOUT_PENALTY = -0.26666666666666666

ENEMY_DAMAGE_REWARD = 0.014
ENEMY_KILL_REWARD = 2.0
# Imperator 2026-08-19: steer combat toward the Beretta on fodder.
# Imperator 2026-08-22: bosses (Yawn / Tiger / Plant 42 / Tyrant) pay 0.1× so
# a 15-HP chip (+0.084) stays above the 0.04 ammo spend tax.
BERETTA_WEAPON_ID = 0x02
BERETTA_DAMAGE_SCALE = 1.1
BERETTA_BOSS_DAMAGE_SCALE = 0.1
# COMBINE reload when the weapon slot is at or below 1/3 combine capacity
# (beretta 5/15, shotgun 2/7, bazooka/magnum 2/6).
# 0.5 stays under the cheapest repeat dump (10 beretta misses to go 15→5:
# spend 0.40 + base miss-waste ~0.267). Hits are not this farm — they are combat.
WEAPON_RELOAD_REWARD = 0.5
# Shotgun vs cerberus is brutally ammo-inefficient in RE1 DC — steer to handgun.
SHOTGUN_DOG_HIT_PENALTY = -1.4
# Magnum / bazooka on fodder (dog or zombie) — keep heavy ammo for bosses.
HEAVY_WEAPON_FODDER_HIT_PENALTY = -2.0
HEAVY_WEAPON_FODDER_IDS: frozenset[int] = frozenset(
    {
        0x04,  # colt python dumdum
        0x05,  # colt python magnum
        0x07,  # bazooka acid
        0x08,  # bazooka explosive
        0x09,  # bazooka flame
    }
)
# Gallery crows: pest combat pays nothing (#7/#8).
CROW_COMBAT_REWARD_SCALE = 0.0
# Named bosses (Yawn / Black Tiger / Plant 42 / Tyrant): 4× hit and kill pay.
BOSS_COMBAT_REWARD_SCALE = 4.0
# Conservative ammo-waste tax.  A full inverse pickup tax (1.0) made scarce
# weapons too expensive to explore; 0.10 keeps successful damage comfortably
# more valuable while giving confirmed misses a small, immediate signal.
ATTACK_MISS_TAX_SCALE = 0.20
KNIFE_MISS_PENALTY = -0.01 * ATTACK_MISS_TAX_SCALE
ATTACK_DRY_FIRE_PENALTY = -0.005
ATTACK_MACRO_FAILURE_PENALTY = -0.01
# Max per-round miss tax when wasting the last round in inventory.
AMMO_WASTE_MAX_PENALTY = 0.25
# Flat legacy miss flag (unused); live knife tax uses KNIFE_MISS_PENALTY above.
ATTACK_MISS_PENALTY = 0.0
AMMO_WASTE_PENALTY = 0.0  # legacy stub; not read by compute_reward

# Per-round ammo expenditure (hit or miss). Keeps kills +EV while making SG/RL
# spray costly vs nav crumbs. Knife / flamethrower omitted (no discrete round).
# Deferred misses re-post ``ammo_spent`` on expiry — skip spend there via
# ``pending_combat_expired`` so fire-step already paid once.
AMMO_SPEND_TAX_PER_ROUND: dict[int, float] = {
    0x02: 0.04,  # beretta / handgun
    0x03: 0.25,  # shotgun
    0x04: 0.40,  # colt python dumdum
    0x05: 0.40,  # colt python magnum
    0x07: 0.40,  # grenade launcher / bazooka acid
    0x08: 0.40,  # bazooka explosive
    0x09: 0.40,  # bazooka flame
    0x0A: 0.75,  # rocket launcher
}

# Miss / ammo-waste tax: per missed round =
#   −AMMO_PICKUP_BONUS / clip_size
# Full inverse of one ammo pickup, amortized over the magazine / pack.
# (Prior half-pickup factor removed 2026-07-20 — imperator: clip-adjusted inverse, not 0.5×.)
# Knife and flamethrower omitted (no discrete clip pack for this tax).
# Bazooka chamber capacity is 1 (WEAPON_CLIP_CAPACITY); miss tax uses pack size 6
# (room_items acid_rounds count=6; DC / Evil Resource).
MISS_TAX_CLIP_SIZE: dict[int, int] = {
    0x02: 15,  # beretta / handgun
    0x03: 7,   # shotgun
    0x04: 6,   # colt python dumdum
    0x05: 6,   # colt python magnum
    0x07: 6,   # grenade launcher / bazooka acid
    0x08: 6,   # bazooka explosive
    0x09: 6,   # bazooka flame
    0x0A: 6,   # rocket launcher
}

REWARD_SCALE = 1.0


def step_penalty_for_frames(
    frames: int,
    *,
    ref_frames: int = REFERENCE_STEP_FRAMES,
) -> float:
    """Living-cost step contempt scaled by emulated frames (pre-REWARD_SCALE)."""
    step_frames = max(int(frames), 0)
    ref = max(int(ref_frames), 1)
    return STEP_PENALTY * (step_frames / ref)


def _load_ammo_item_names() -> frozenset[str]:
    names: set[str] = set()
    try:
        cat_path = Path(__file__).resolve().parents[1] / "data" / "item_categories.json"
        data = json.loads(cat_path.read_text(encoding="utf-8"))
        names.update(str(k) for k, v in data.items() if v == "ammo")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    for item_id in range(0x0B, 0x13):
        if item_id in ITEM_IDS:
            names.add(ITEM_IDS[item_id])
    return frozenset(names)


AMMO_ITEM_NAMES: frozenset[str] = _load_ammo_item_names()

HP_HEAL_RESOURCE_ITEMS: frozenset[str] = frozenset({
    "first_aid_spray",
    "first_aid_spray_alt",
    "green_herb",
    "red_herb",
    "mixed_herbs_gr",
    "mixed_herbs_gg",
    "mixed_herbs_gb",
    "mixed_herbs_grb",
    "mixed_herbs_ggg",
    "mixed_herbs_ggb",
})
POISON_CURE_RESOURCE_ITEMS: frozenset[str] = frozenset({
    "blue_herb",
    "mixed_herbs_gb",
    "mixed_herbs_grb",
    "mixed_herbs_ggb",
})
HEALTH_RESOURCE_ITEMS = HP_HEAL_RESOURCE_ITEMS | POISON_CURE_RESOURCE_ITEMS


def _inventory_names(state: dict[str, Any]) -> set[str]:
    raw_slots = state.get("inventory_slots")
    if raw_slots is None:
        return {
            canonical_item(str(name))
            for name in (state.get("inventory") or [])
            if name
        }
    names: set[str] = set()
    for entry in raw_slots:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("item")
        elif isinstance(entry, (list, tuple)) and entry:
            name = entry[0]
        else:
            name = None
        if name:
            names.add(canonical_item(str(name)))
    return names


def _free_inventory_slots(state: dict[str, Any]) -> int:
    raw_slots = state.get("inventory_slots")
    if raw_slots is None:
        occupied = len(state.get("inventory") or [])
    else:
        occupied = 0
        for entry in raw_slots:
            if isinstance(entry, dict):
                occupied += bool(entry.get("name") or entry.get("item"))
            elif isinstance(entry, (list, tuple)) and entry:
                occupied += bool(entry[0])
    return max(0, 8 - int(occupied))


def _health_pickup_adds_resource(
    name: str,
    *,
    prev_state: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    if name not in HEALTH_RESOURCE_ITEMS or _free_inventory_slots(state) < 3:
        return False
    held = _inventory_names(prev_state)
    has_hp = bool(held & HP_HEAL_RESOURCE_ITEMS)
    has_cure = bool(held & POISON_CURE_RESOURCE_ITEMS)
    return (
        (name in HP_HEAL_RESOURCE_ITEMS and not has_hp)
        or (name in POISON_CURE_RESOURCE_ITEMS and not has_cure)
    )


def gamma_for_emulated_half_life(
    half_life_s: float,
    *,
    step_frames: int = REFERENCE_STEP_FRAMES,
) -> float:
    """Pure discount γ so a reward halves every ``half_life_s`` emulated seconds."""
    if half_life_s <= 0:
        raise ValueError("half_life_s must be positive")
    steps = half_life_s / (step_frames / 60.0)
    return 0.5 ** (1.0 / steps)


# Dense softlock ramp is already in the scalar reward (bd["softlock"]); one γ.
# Yawn rails MC credit: ~25s emulated half-life (20–30s band).  Longer reach
# for navigation chains comes from full-size nav crumbs + dominant checkpoint,
# not an extreme γ.
RAILS_CREDIT_HALF_LIFE_S = 25.0
RL_GAMMA = gamma_for_emulated_half_life(RAILS_CREDIT_HALF_LIFE_S)

# Per-HP damage / heal: (8/3) / (JILL_FINE_HP - 1) = (8/3)/95. Exact inverse heal.
HP_LOSS_SCALE = 0.02807017543859649
HP_GAIN_SCALE = 0.02807017543859649
# Legacy export; heal is linear now (kept so old imports do not break).
HEAL_LOG_CURVE_EXPONENT = 1.0


def hp_heal_reward(hp_delta: int) -> float:
    """Heal reward: inverse of the per-HP damage penalty (linear)."""
    if hp_delta <= 0:
        return 0.0
    return HP_GAIN_SCALE * float(hp_delta)


def _player_hp_in_reward_band(hp: int) -> bool:
    """True for live Jill HP readings (1..96). 0 is death/init; >96 is garbage/Chris-scale."""
    return 1 <= int(hp) <= int(JILL_FINE_HP)


def ammo_waste_per_missed_round(
    weapon_id: int,
    *,
    ammo_before: int | None = None,
) -> float:
    """Per-round miss tax for ``weapon_id`` (0 if knife / unknown / no clip).

    When ``ammo_before`` is set and total fireable ammo drops below 2× chamber
    clip, scale linearly up to ``AMMO_WASTE_MAX_PENALTY`` on the last round.
    """
    from re1_rl.ammo_accounting import WEAPON_CLIP_CAPACITY

    wid = int(weapon_id) & 0xFF
    tax_clip = MISS_TAX_CLIP_SIZE.get(wid)
    if tax_clip is None or tax_clip <= 0:
        return 0.0
    base = AMMO_PICKUP_BONUS / float(tax_clip) * ATTACK_MISS_TAX_SCALE
    if ammo_before is None:
        return -base
    chamber = int(WEAPON_CLIP_CAPACITY.get(wid, tax_clip))
    threshold = 2 * chamber
    ammo = max(1, int(ammo_before))
    if ammo >= threshold:
        return -base
    if ammo <= 1:
        return -AMMO_WASTE_MAX_PENALTY
    t = (threshold - ammo) / float(threshold - 1)
    mult = 1.0 + t * (AMMO_WASTE_MAX_PENALTY / base - 1.0)
    return -base * mult


def ammo_waste_penalty(
    weapon_id: int,
    rounds_spent: int,
    *,
    ammo_before: int | None = None,
) -> float:
    """Total ammo-waste penalty for a missed attack that spent ``rounds_spent``."""
    rounds = int(rounds_spent)
    if rounds <= 0:
        return 0.0
    return ammo_waste_per_missed_round(
        weapon_id, ammo_before=ammo_before,
    ) * float(rounds)


def ammo_spend_per_round(weapon_id: int) -> float:
    """Per-round expenditure tax for ``weapon_id`` (0 if knife / unknown)."""
    return float(AMMO_SPEND_TAX_PER_ROUND.get(int(weapon_id) & 0xFF, 0.0))


def ammo_spend_penalty(weapon_id: int, rounds_spent: int) -> float:
    """Total ammo expenditure tax for ``rounds_spent`` (hit or miss)."""
    rounds = int(rounds_spent)
    if rounds <= 0:
        return 0.0
    per = ammo_spend_per_round(weapon_id)
    if per <= 0.0:
        return 0.0
    return -per * float(rounds)


def _combat_ammo_weapon_id(state: dict[str, Any]) -> int:
    """Weapon id for ammo taxes (pending fire / deferred miss / equipped)."""
    return int(
        state.get("pending_combat_weapon_id")
        or state.get("pending_miss_weapon_id")
        or state.get("equipped_weapon_id")
        or 0
    )


def _nominal_weapon_damage_max(weapon_id: int) -> int:
    from re1_rl.weapon_damage import WEAPON_NOMINAL_DAMAGE

    pair = WEAPON_NOMINAL_DAMAGE.get(int(weapon_id) & 0xFF)
    if not pair:
        return 0
    return int(pair[1])


def combat_overkill_penalty(state: dict[str, Any]) -> float:
    """Penalty for wasted nominal damage on kills (scales like miss tax)."""
    events = state.get("combat_events")
    if not events:
        return 0.0
    wid = int(
        state.get("pending_miss_weapon_id")
        or state.get("equipped_weapon_id")
        or 0
    )
    if wid <= 0:
        return 0.0
    nominal = _nominal_weapon_damage_max(wid)
    if nominal <= 0:
        return 0.0
    ammo_spent = int(state.get("ammo_spent", 0) or 0)
    total = 0.0
    if wid == 0x01:
        per_miss = KNIFE_MISS_PENALTY
        for ev in events:
            if ev.get("reward_denied") or not ev.get("killed"):
                continue
            damage = int(ev.get("damage", 0))
            wasted = max(0, nominal - damage)
            if wasted <= 0:
                continue
            total += (wasted / float(nominal)) * per_miss
        return total
    from re1_rl.ammo_accounting import fireable_ammo_before_miss

    ammo_before = fireable_ammo_before_miss(state, wid, rounds_spent=ammo_spent)
    per_round = ammo_waste_per_missed_round(wid, ammo_before=ammo_before)
    if per_round == 0.0:
        return 0.0
    for ev in events:
        if ev.get("reward_denied") or not ev.get("killed"):
            continue
        damage = int(ev.get("damage", 0))
        wasted = max(0, nominal - damage)
        if wasted <= 0:
            continue
        total += (wasted / float(nominal)) * per_round
    return total


def shotgun_dog_hit_penalty(state: dict[str, Any]) -> float:
    """Flat tax when a shotgun shell damages a cerberus (per combat event)."""
    from re1_rl.attack_macro import SHOTGUN_WEAPON_ID

    wid = _combat_ammo_weapon_id(state)
    if int(wid) != int(SHOTGUN_WEAPON_ID):
        return 0.0
    events = state.get("combat_events")
    if not events:
        return 0.0
    hits = 0
    for ev in events:
        if ev.get("reward_denied"):
            continue
        if int(ev.get("damage", 0) or 0) <= 0:
            continue
        if ev.get("is_cerberus"):
            hits += 1
    if hits <= 0:
        return 0.0
    return SHOTGUN_DOG_HIT_PENALTY * float(hits)


def heavy_weapon_fodder_hit_penalty(state: dict[str, Any]) -> float:
    """−2 per magnum/bazooka hit on a dog or zombie."""
    wid = _combat_ammo_weapon_id(state)
    if int(wid) not in HEAVY_WEAPON_FODDER_IDS:
        return 0.0
    events = state.get("combat_events")
    if not events:
        return 0.0
    hits = 0
    for ev in events:
        if ev.get("reward_denied") or ev.get("is_yawn") or ev.get("is_boss"):
            continue
        if int(ev.get("damage", 0) or 0) <= 0:
            continue
        if ev.get("is_cerberus") or ev.get("is_zombie"):
            hits += 1
    if hits <= 0:
        return 0.0
    return HEAVY_WEAPON_FODDER_HIT_PENALTY * float(hits)


# Legacy aliases (all rails off-path / leave-target is now terminal -4).
WRONG_ROOM_PENALTY = -4.0
# Room detour / leave-target: -4, zeros same-step positives, ends episode.
# In-room pickup order is never gated — only graph hops toward the checkpoint.
WRONG_ROOM_TERMINAL_PENALTY = -4.0
WRONG_ROOM_TERMINAL_FROM_CHECKPOINT_ID = "l_passage_enter_108"
# Jill Standard Yawn route never needs broken_shotgun (Barry trap rescue).
FORBIDDEN_ITEM_TERMINAL_PENALTY = -4.0
YAWN_FORBIDDEN_ITEMS = frozenset({"broken_shotgun"})
RETREAT_PENALTY = -4.0
SUCCESS_ROOM_BONUS = CHECKPOINT_REWARD
PBRS_GRAPH_WEIGHT = 0.02
PBRS_DOOR_WEIGHT = 0.05
SHAPING_GAMMA = 1.0
UNKNOWN_HOPS = 8.0
DIST_NORM = 4096.0
# Dominant terminal pulse on one-leg rails (unscaled; exploration uses CHECKPOINT_REWARD).
# Timed cells pay leftover-budget scale: last-frame complete still gets the floor.
RAILS_CHECKPOINT_REWARD = 8.0
RAILS_CHECKPOINT_REWARD_MIN = 4.0


def rails_checkpoint_success_reward(
    progress: "ProgressTracker | None",
    *,
    extra_frames: int = 0,
) -> float:
    """Scale the +8 cell pulse by leftover timeout; untimed cells keep +8.

    Timed cells pay ``MIN + (8-MIN) * leftover_frac`` so a last-frame
    complete is still +4 (timeout remains -4). Incumbent PB is not used.
    """
    if progress is None or int(progress.cell_timeout_frames) <= 0:
        return RAILS_CHECKPOINT_REWARD
    frac = progress.cell_timeout_remaining_frac(extra_frames)
    span = RAILS_CHECKPOINT_REWARD - RAILS_CHECKPOINT_REWARD_MIN
    return RAILS_CHECKPOINT_REWARD_MIN + span * frac


# Hard capture gate failure (inventory headroom, leg kills, unsettled state, etc.).
# Distinct from quality compare (LOSE_TO_INCUMBENT) — not worth filing at all.
RAILS_CAPTURE_INELIGIBLE_PENALTY = -4.0
# Per-CP emulated-frame wall expired without satisfying the cell.
RAILS_CELL_TIMEOUT_PENALTY = -4.0
# Navigation milestones keep full exploration magnitudes on rails (+4 / +2 ammo).
RAILS_NAV_POSITIVE_SCALE = 1.0
RAILS_NAV_POSITIVE_TERMS: frozenset[str] = frozenset({
    "new_room",
    "new_cutscene",
    "document_examine",
    "key_item",
    "story_use",
    "dining_statue",
    "dining_statue_progress",
    "new_weapon",
    "ammo_pickup",
    "gallery",
    "weapon_reload",
    "yawn_box_key_deposit",
})
# PBRS, junk pickups, typewriter, etc.
RAILS_MINOR_POSITIVE_SCALE = 0.05
RAILS_AUX_POSITIVE_SCALE = RAILS_MINOR_POSITIVE_SCALE
# Combat must remain locally learnable on rails: miss taxes are unscaled, so
# scaling only hit/kill positives makes correct shooting systematically worse.
RAILS_UNSCALED_COMBAT_TERMS: frozenset[str] = frozenset({
    "enemy_damage",
    "enemy_kill",
})
# Rails clawbacks mirror nav pickup crumbs (+4 room/key/story/gallery → −4 returns).
RAILS_SCALED_CLAWBACK_TERMS: frozenset[str] = frozenset({
    "gallery",
    "gold_emblem_return",
    "key_item_return",
})

# Kept as a public capability flag; each curriculum still opts in via rails_mode.
ENABLE_CHECKPOINT_PATH = True


def _wrong_room_terminal_active(planner: Any) -> bool:
    """True when the active rails checkpoint is at/after L Passage enter."""
    route = getattr(planner, "route", None) or []
    gate_seq: int | None = None
    for step in route:
        if not isinstance(step, dict):
            continue
        if str(step.get("checkpoint_id") or "") == WRONG_ROOM_TERMINAL_FROM_CHECKPOINT_ID:
            try:
                gate_seq = int(step.get("seq", 0))
            except (TypeError, ValueError):
                gate_seq = None
            break
    if gate_seq is None:
        return False
    cur = planner.current_route_seq()
    if cur is None:
        return False
    try:
        return int(cur) >= int(gate_seq)
    except (TypeError, ValueError):
        return False


def _box_inventory_unpaid(state: dict[str, Any]) -> bool:
    """True when inventory deltas this step are a box withdraw/deposit."""
    transfer = str(state.get("box_transfer") or "").strip().lower()
    return bool(
        state.get("box_withdraw_success")
        or state.get("box_deposit_success")
        or state.get("box_ui_step")
        or transfer in {"withdraw", "deposit"}
    )


def _key_item_return_blocked(
    *,
    state: dict[str, Any],
    room: str,
) -> bool:
    """True when a key leaving inventory is not a put-back farm."""
    if state.get("story_use_success") or state.get("gold_emblem_return"):
        return True
    from re1_rl.item_box import is_box_room

    return is_box_room(room)


def softlock_frame_threshold(progress: ProgressTracker | None) -> int:
    """Idle truncate cap: 12 min from start and after room/key/weapon/use/cell."""
    if progress is None:
        return SOFTLOCK_PRE_KENNETH_FRAMES
    if progress.kenneth_gate_breached:
        return SOFTLOCK_PRE_KENNETH_FRAMES
    from re1_rl.cutscene_reward import kenneth_cutscene_seen

    if kenneth_cutscene_seen(
        progress.observed_cutscenes | progress.rewarded_cutscenes
    ):
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
    if graph is None:
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


def _combat_event_scale(ev: dict[str, Any], *, room_id: str) -> float:
    """Crow / boss multiplier for one HP-delta event.

    Live pending-credit hits sometimes arrive without ``is_yawn`` / ``is_boss``
    flags (or with only the scalar ``enemy_damage`` fallback). Reclassify from
    type_id / room / slot so attic Yawn cannot pay 1× by omission.
    """
    if ev.get("is_crow"):
        return CROW_COMBAT_REWARD_SCALE
    if ev.get("is_boss") or ev.get("is_yawn"):
        return BOSS_COMBAT_REWARD_SCALE
    from re1_rl.enemy_combat import is_boss_combat_entity, is_yawn_combat_entity

    slot = ev.get("slot")
    hp_before = ev.get("hp_before")
    if is_yawn_combat_entity(ev, room_id=room_id, slot=slot):
        return BOSS_COMBAT_REWARD_SCALE
    if is_boss_combat_entity(
        ev, room_id=room_id, slot=slot, hp_before=hp_before
    ):
        return BOSS_COMBAT_REWARD_SCALE
    return 1.0


def _state_boss_combat_scale(state: dict[str, Any]) -> float:
    """Boss multiplier when paying the scalar ``enemy_damage`` fallback."""
    from re1_rl.enemy_combat import is_boss_combat_entity, is_yawn_combat_entity
    from re1_rl.yawn_hp import YAWN_ROOM

    room = str(state.get("room_id") or "").strip().upper()
    if room == YAWN_ROOM:
        return BOSS_COMBAT_REWARD_SCALE
    for ent in state.get("enemies") or []:
        if not isinstance(ent, dict):
            continue
        slot = ent.get("slot")
        if is_yawn_combat_entity(ent, room_id=room, slot=slot):
            return BOSS_COMBAT_REWARD_SCALE
        if is_boss_combat_entity(
            ent, room_id=room, slot=slot, hp_before=ent.get("hp")
        ):
            return BOSS_COMBAT_REWARD_SCALE
    return 1.0


def _beretta_damage_scale(state: dict[str, Any], *, vs_boss: bool = False) -> float:
    if int(_combat_ammo_weapon_id(state)) != BERETTA_WEAPON_ID:
        return 1.0
    if vs_boss:
        return BERETTA_BOSS_DAMAGE_SCALE
    return BERETTA_DAMAGE_SCALE


def _inventory_id_qty_slots(state: dict[str, Any] | None) -> list[tuple[int, int]]:
    from re1_rl.ammo_accounting import inventory_slots_to_id_qty

    return inventory_slots_to_id_qty((state or {}).get("inventory_slots"))


def reload_low_ammo_threshold(weapon_id: int) -> int:
    """Max loaded qty that still qualifies a COMBINE reload crumb (floor of 1/3)."""
    from re1_rl.ammo_accounting import combine_clip_capacity

    return combine_clip_capacity(int(weapon_id) & 0xFF) // 3


def low_ammo_reload_reward(prev_state: dict[str, Any], state: dict[str, Any]) -> float:
    """+0.1 per weapon slot COMBINE-reloaded from at or below 1/3 capacity."""
    from re1_rl.ammo_accounting import WEAPON_AMMO_ITEM
    from re1_rl.memory_map import WEAPON_ITEM_IDS

    prev_slots = _inventory_id_qty_slots(prev_state)
    cur_slots = _inventory_id_qty_slots(state)
    if not prev_slots or not cur_slots:
        return 0.0
    n = min(len(prev_slots), len(cur_slots))
    total = 0.0
    for i in range(n):
        pid, pq = prev_slots[i]
        cid, cq = cur_slots[i]
        wid = int(pid) & 0xFF
        if wid == 0 or wid != (int(cid) & 0xFF):
            continue
        if wid not in WEAPON_ITEM_IDS or wid == 0x01:
            continue
        ammo_id = WEAPON_AMMO_ITEM.get(wid)
        if ammo_id is None:
            continue
        if int(pq) > reload_low_ammo_threshold(wid):
            continue
        if int(cq) <= int(pq):
            continue
        prev_ammo = sum(
            int(q) for iid, q in prev_slots if (int(iid) & 0xFF) == int(ammo_id)
        )
        cur_ammo = sum(
            int(q) for iid, q in cur_slots if (int(iid) & 0xFF) == int(ammo_id)
        )
        if cur_ammo >= prev_ammo:
            continue
        total += WEAPON_RELOAD_REWARD
    return total


def enemy_combat_rewards(state: dict[str, Any]) -> tuple[float, float]:
    """Return ``(damage_pay, kill_pay)`` honoring per-event crow / boss scaling."""
    room_id = str(state.get("room_id") or "")
    events = state.get("combat_events")
    if events:
        damage_pay = 0.0
        kill_pay = 0.0
        for ev in events:
            if ev.get("reward_denied"):
                continue
            scale = _combat_event_scale(ev, room_id=room_id)
            beretta = _beretta_damage_scale(
                state, vs_boss=scale == BOSS_COMBAT_REWARD_SCALE
            )
            damage_pay += ENEMY_DAMAGE_REWARD * int(ev.get("damage", 0)) * scale * beretta
            if ev.get("killed"):
                kill_pay += ENEMY_KILL_REWARD * scale
        return damage_pay, kill_pay
    scale = _state_boss_combat_scale(state)
    beretta = _beretta_damage_scale(
        state, vs_boss=scale == BOSS_COMBAT_REWARD_SCALE
    )
    enemy_damage = int(state.get("enemy_damage", 0) or 0)
    enemy_kills = int(state.get("enemy_kills", 0) or 0)
    return (
        ENEMY_DAMAGE_REWARD * enemy_damage * scale * beretta,
        ENEMY_KILL_REWARD * enemy_kills * scale,
    )


def scalarize_reward(
    breakdown: dict[str, float],
    *,
    planner_loyal: bool = False,
) -> float:
    """Mode-aware reward scalarizer used by env step paths."""
    if planner_loyal:
        from re1_rl.planner_loyal import scalarize_planner_loyal_reward

        return scalarize_planner_loyal_reward(breakdown)
    return float(sum(breakdown.values())) * REWARD_SCALE


def _planner_loyal_breakdown_template() -> dict[str, float]:
    from re1_rl.planner_loyal import (
        PLANNER_LOYAL_SCALAR_KEYS,
        PLANNER_LOYAL_TELEMETRY_KEYS,
    )

    bd: dict[str, float] = {
        key: 0.0 for key in PLANNER_LOYAL_SCALAR_KEYS | PLANNER_LOYAL_TELEMETRY_KEYS
    }
    return bd


def _compute_planner_loyal_reward(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    planner_loyal_queue: Any,
    *,
    progress: ProgressTracker | None = None,
    typewriter_save_complete: bool = False,
    box_opened: bool = False,
    box_closed: bool = False,
    return_breakdown: bool = False,
) -> float | tuple[float, dict[str, float]]:
    """Strict planner-loyal reward path — no legacy crumbs or progress side effects."""
    from re1_rl.planner_loyal import (
        PLANNER_DIVERT_PENALTY,
        PLANNER_STEP_SUCCESS_REWARD,
        PLANNER_TIMEOUT_PENALTY,
        scalarize_planner_loyal_reward,
    )
    from re1_rl.yawn_cell_timeout import FLAT_CELL_TIMEOUT_FRAMES

    softlock_threshold = softlock_frame_threshold(progress)
    step_frames = int(state.get("step_emulated_frames", REFERENCE_STEP_FRAMES))
    ref_frames = int(state.get("reference_step_frames", REFERENCE_STEP_FRAMES))
    step_scale = max(step_frames, 0) / max(ref_frames, 1)

    bd = _planner_loyal_breakdown_template()
    bd["step"] = STEP_PENALTY * step_scale
    from re1_rl.armor_room_puzzle import (
        ARMOR_GAS_DAMAGE_PENALTY,
        ARMOR_INPLACE_STATUE_PUSH_PENALTY,
        armor_approach_progress_reward,
        armor_approach_reference,
        armor_far_leg_active,
        armor_gas_damage_detected,
        armor_inplace_statue_push_detected,
        armor_statue_progress_reward,
    )

    bd["armor_statue_progress"] = armor_statue_progress_reward(
        prev_state,
        state,
        planner_loyal_queue,
        progress,
    )
    from re1_rl.dining_statue_puzzle import dining_statue_progress_reward

    bd["dining_statue_progress"] = dining_statue_progress_reward(
        prev_state,
        state,
        queue=planner_loyal_queue,
    )
    if progress is not None and armor_far_leg_active(planner_loyal_queue, state):
        reference = progress.baseline_armor_far_approach(
            armor_approach_reference(prev_state)
        )
        bd["armor_approach"] = armor_approach_progress_reward(
            prev_state, state, planner_loyal_queue, reference
        )
    if progress is not None and armor_inplace_statue_push_detected(
        prev_state, state, planner_loyal_queue
    ):
        if progress.breach_armor_inplace_statue_push():
            bd["armor_inplace_statue_push"] = -float(ARMOR_INPLACE_STATUE_PUSH_PENALTY)
    if progress is not None and armor_gas_damage_detected(prev_state, state):
        if progress.breach_armor_gas():
            bd["armor_gas"] = -float(ARMOR_GAS_DAMAGE_PENALTY)

    prev_room = str(prev_state.get("room_id", "") or "")
    room = str(state.get("room_id", "") or "")
    new_kenneth_gate_breach = False
    if progress is not None and not state.get("dead"):
        from re1_rl.barry_return_checkpoint import note_kenneth_live_scene
        from re1_rl.cutscene_reward import (
            illegal_main_hall_before_kenneth_transition,
        )

        # Mark 104:*:sN before the hall check so a legal post-Kenneth
        # 105→106 (opening step 4) is not false-killed.
        note_kenneth_live_scene(progress, state)
        if illegal_main_hall_before_kenneth_transition(
            prev_room,
            room,
            rewarded_cutscenes=(
                progress.observed_cutscenes | progress.rewarded_cutscenes
            ),
            peak_room=state.get("_skip_peak_room"),
        ):
            new_kenneth_gate_breach = progress.breach_kenneth_gate()
            if new_kenneth_gate_breach:
                bd["main_hall_before_kenneth"] = MAIN_HALL_BEFORE_KENNETH_PENALTY

    loyal = planner_loyal_queue.evaluate_transition(
        prev_state=prev_state,
        state=state,
        box_opened=box_opened,
        box_closed=box_closed,
        typewriter_save_complete=bool(typewriter_save_complete),
        progress=progress,
    )
    bd["heal_use_tax"] = float(loyal.get("heal_use_tax") or 0.0)

    if new_kenneth_gate_breach:
        # Kenneth owns this death. Do not also stack planner_divert −4.
        for key in (
            "planner_step_success",
            "checkpoint_success",
            "planner_divert",
            "wrong_room",
            "enemy_damage",
            "enemy_kill",
            "hp",
            "weapon_reload",
        ):
            bd[key] = 0.0
    elif loyal.get("divert"):
        bd["planner_divert"] = PLANNER_DIVERT_PENALTY
        bd["wrong_room"] = PLANNER_DIVERT_PENALTY
        if progress is not None and hasattr(progress, "breach_wrong_room"):
            progress.breach_wrong_room()
        for key in (
            "planner_step_success",
            "checkpoint_success",
            "enemy_damage",
            "enemy_kill",
            "hp",
            "weapon_reload",
        ):
            bd[key] = 0.0
    elif loyal.get("step_success"):
        extra = int(state.get("step_emulated_frames") or 0)
        if progress is not None and int(progress.cell_timeout_frames) <= 0:
            progress.arm_cell_timeout(int(FLAT_CELL_TIMEOUT_FRAMES))
        pay = float(PLANNER_STEP_SUCCESS_REWARD)
        if progress is not None and int(progress.cell_timeout_frames) > 0:
            pay = float(PLANNER_STEP_SUCCESS_REWARD) * float(
                progress.cell_timeout_remaining_frac(extra)
            )
        bd["planner_step_success"] = pay
        bd["checkpoint_success"] = pay
        if progress is not None and planner_loyal_queue.done:
            if hasattr(progress, "claim_checkpoint_success"):
                progress.claim_checkpoint_success()
        elif progress is not None:
            # Mid-chunk: keep playing with a fresh 12m idle / max_steps budget.
            progress.note_softlock_extension(SOFTLOCK_EXTENSION_FRAMES)
            progress.note_max_steps_extension(CHECKPOINT_MAX_STEPS_EXTENSION)
            if hasattr(progress, "arm_cell_timeout"):
                progress.arm_cell_timeout(int(FLAT_CELL_TIMEOUT_FRAMES))
                progress.leg_emulated_frames = 0

    if progress is not None:
        from re1_rl.weapon_equip import inventory_entries_to_names

        inventory = set(inventory_entries_to_names(state.get("inventory")))
        _, gallery_wrong = progress.gallery_step_reward(
            prev_room=str(prev_state.get("room_id", "") or ""),
            room=str(state.get("room_id", "") or ""),
            prev_raw=int(prev_state.get("gallery_progress", 0) or 0),
            raw=int(state.get("gallery_progress", 0) or 0),
            prev_confirm=int(prev_state.get("gallery_confirm", 0) or 0),
            confirm=int(state.get("gallery_confirm", 0) or 0),
            star_crest_held="star_crest" in inventory,
            x=float(state.get("x", 0) or 0),
            z=float(state.get("z", 0) or 0),
            prev_x=float(prev_state.get("x", 0) or 0),
            prev_z=float(prev_state.get("z", 0) or 0),
        )
        # Leave-117 is already planner_divert. Same-room wrong Yes uses
        # gallery_wrong like yawn cells (−4, episode end).
        if gallery_wrong != 0.0 and not loyal.get("divert"):
            bd["gallery_wrong"] = float(gallery_wrong)

    if (
        progress is not None
        and "step_emulated_frames" in state
        and not bd["planner_step_success"]
        and not progress.wrong_room_breached
        and not progress.gallery_wrong_breached
        and not progress.armor_inplace_statue_push_breached
        and not progress.armor_gas_breached
        and not progress.kenneth_gate_breached
    ):
        progress.note_leg_frames(int(state.get("step_emulated_frames") or 0))
        if (
            progress.cell_timeout_frames > 0
            and progress.leg_emulated_frames >= progress.cell_timeout_frames
            and progress.breach_cell_timeout()
        ):
            bd["planner_timeout"] = PLANNER_TIMEOUT_PENALTY
            bd["checkpoint_timeout"] = PLANNER_TIMEOUT_PENALTY

    prev_hp = int(prev_state.get("hp", 0))
    hp = int(state.get("hp", 0))
    hp_delta = hp - prev_hp
    if hp_delta < 0 and _player_hp_in_reward_band(prev_hp) and hp <= JILL_FINE_HP:
        bd["hp"] = HP_LOSS_SCALE * hp_delta
    elif (
        hp_delta > 0
        and _player_hp_in_reward_band(prev_hp)
        and _player_hp_in_reward_band(hp)
    ):
        bd["hp"] = hp_heal_reward(hp_delta)

    if state.get("dead"):
        bd["death"] = DEATH_PENALTY

    enemy_damage_pay, enemy_kill_pay = enemy_combat_rewards(state)
    if enemy_damage_pay > 0.0:
        bd["enemy_damage"] = enemy_damage_pay
    if enemy_kill_pay > 0.0:
        bd["enemy_kill"] = enemy_kill_pay

    reload_pay = low_ammo_reload_reward(prev_state, state)
    if reload_pay > 0.0:
        bd["weapon_reload"] = reload_pay

    overkill = combat_overkill_penalty(state)
    if overkill < 0.0:
        bd["combat_overkill"] = overkill

    dog_sg = shotgun_dog_hit_penalty(state)
    if dog_sg < 0.0:
        bd["shotgun_dog_hit"] = dog_sg

    heavy_fodder = heavy_weapon_fodder_hit_penalty(state)
    if heavy_fodder < 0.0:
        bd["heavy_weapon_fodder_hit"] = heavy_fodder

    rounds_spent = int(state.get("ammo_spent", 0) or 0)
    if rounds_spent > 0 and not state.get("pending_combat_expired"):
        bd["ammo_spend"] = ammo_spend_penalty(
            _combat_ammo_weapon_id(state), rounds_spent,
        )

    if state.get("knife_swing_missed"):
        bd["attack_miss"] = KNIFE_MISS_PENALTY
    elif state.get("attack_missed"):
        waste_rounds = int(state.get("deferred_waste_rounds") or 0)
        if waste_rounds <= 0:
            waste_rounds = rounds_spent
        if waste_rounds > 0:
            from re1_rl.ammo_accounting import fireable_ammo_before_miss

            wid = _combat_ammo_weapon_id(state)
            ammo_before = fireable_ammo_before_miss(
                state, wid, rounds_spent=waste_rounds,
            )
            bd["ammo_waste"] = ammo_waste_penalty(
                wid,
                waste_rounds,
                ammo_before=ammo_before,
            )
    if state.get("attack_dry_fire"):
        bd["attack_dry_fire"] = ATTACK_DRY_FIRE_PENALTY
    elif state.get("attack_macro_failure"):
        bd["attack_macro_failure"] = ATTACK_MACRO_FAILURE_PENALTY

    if progress is not None and (
        progress.wrong_room_breached
        or progress.cell_timeout_breached
        or progress.gallery_wrong_breached
        or progress.armor_inplace_statue_push_breached
        or progress.armor_gas_breached
        or progress.kenneth_gate_breached
    ):
        for term, value in tuple(bd.items()):
            if value > 0.0:
                bd[term] = 0.0

    if progress is not None and not state.get("dead"):
        frames_before = progress.stagnation_frames
        # Only a statue shove counts as progress. The approach potential is
        # symmetric, so pacing toward/away would otherwise reset the idle clock
        # for free.
        armor_progress = float(bd.get("armor_statue_progress") or 0.0) > 0.0
        dining_progress = float(bd.get("dining_statue_progress") or 0.0) > 0.0
        if bd.get("planner_step_success", 0.0) == 0.0:
            progress.note_stagnation_step(
                made_progress=armor_progress or dining_progress,
                step_frames=step_frames,
            )
        bd["softlock"] = contempt_penalty_delta(
            frames_before,
            progress.stagnation_frames,
            threshold=softlock_threshold,
        )

    reward = scalarize_planner_loyal_reward(bd)
    if return_breakdown:
        return reward, bd
    return reward


def compute_reward(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    planner: WaypointPlanner,
    *,
    progress: ProgressTracker | None = None,
    graph: RoomGraph | None = None,
    softlock_threshold: int | None = None,
    success_room: str | None = None,
    rails_mode: bool = False,
    typewriter_save_complete: bool = False,
    return_breakdown: bool = False,
    planner_loyal_queue: Any | None = None,
    box_opened: bool = False,
    box_closed: bool = False,
) -> float | tuple[float, dict[str, float]]:
    """Compute scalar reward from symbolic state dicts."""
    del success_room
    if planner_loyal_queue is not None:
        return _compute_planner_loyal_reward(
            prev_state,
            state,
            planner_loyal_queue,
            progress=progress,
            typewriter_save_complete=typewriter_save_complete,
            box_opened=box_opened,
            box_closed=box_closed,
            return_breakdown=return_breakdown,
        )
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
        "typewriter_save": 0.0,
        "retreat": 0.0,
        "wrong_room": 0.0,
        "forbidden_item": 0.0,
        "item": 0.0,
        "ammo_pickup": 0.0,
        "box_withdraw": 0.0,
        "yawn_box_key_deposit": 0.0,
        "key_item": 0.0,
        "story_use": 0.0,
        "gallery": 0.0,
        "gallery_wrong": 0.0,
        "dining_statue": 0.0,
        "dining_statue_progress": 0.0,
        "armor_statue_progress": 0.0,
        "gold_emblem_return": 0.0,
        "key_item_return": 0.0,
        "shotgun_return": 0.0,
        "new_weapon": 0.0,
        "checkpoint_success": 0.0,
        "checkpoint_capture_ineligible": 0.0,
        "checkpoint_timeout": 0.0,
        "success_room": 0.0,
        "hp": 0.0,
        "death": 0.0,
        "main_hall_before_kenneth": 0.0,
        "softlock": 0.0,
        "enemy_damage": 0.0,
        "enemy_kill": 0.0,
        "attack_miss": 0.0,
        "ammo_spend": 0.0,
        "ammo_waste": 0.0,
        "combat_overkill": 0.0,
        "shotgun_dog_hit": 0.0,
        "heavy_weapon_fodder_hit": 0.0,
        "weapon_reload": 0.0,
        "attack_dry_fire": 0.0,
        "attack_macro_failure": 0.0,
        "planner_step_success": 0.0,
        "planner_divert": 0.0,
        "heal_use_tax": 0.0,
    }

    planner_loyal = planner_loyal_queue is not None
    if planner_loyal:
        from re1_rl.planner_loyal import (
            PLANNER_DIVERT_PENALTY,
            PLANNER_STEP_SUCCESS_REWARD,
        )

        loyal = planner_loyal_queue.evaluate_transition(
            prev_state=prev_state,
            state=state,
            box_opened=box_opened,
            box_closed=box_closed,
            typewriter_save_complete=bool(typewriter_save_complete),
            progress=progress,
        )
        bd["heal_use_tax"] = float(loyal.get("heal_use_tax") or 0.0)
        if loyal.get("divert"):
            bd["planner_divert"] = PLANNER_DIVERT_PENALTY
            bd["wrong_room"] = PLANNER_DIVERT_PENALTY  # episode terminal path
            if progress is not None and hasattr(progress, "breach_wrong_room"):
                progress.breach_wrong_room()
            # Zero same-step positives under divert.
            for key in (
                "checkpoint_success",
                "planner_step_success",
                "enemy_damage",
                "enemy_kill",
                "hp",
                "ammo_pickup",
                "item",
                "key_item",
                "story_use",
                "gallery",
                "new_room",
                "new_weapon",
                "armor_statue_progress",
            ):
                bd[key] = 0.0
        elif loyal.get("step_success"):
            from re1_rl.yawn_cell_timeout import FLAT_CELL_TIMEOUT_FRAMES

            extra = int(state.get("step_emulated_frames") or 0)
            if progress is not None and int(progress.cell_timeout_frames) <= 0:
                # Ensure a 12m wall so leftover scaling is defined.
                progress.arm_cell_timeout(int(FLAT_CELL_TIMEOUT_FRAMES))
            pay = float(PLANNER_STEP_SUCCESS_REWARD)
            if progress is not None and int(progress.cell_timeout_frames) > 0:
                # Full +8 at fresh budget; drops linearly with time used.
                pay = float(PLANNER_STEP_SUCCESS_REWARD) * float(
                    progress.cell_timeout_remaining_frac(extra)
                )
            bd["planner_step_success"] = pay
            bd["checkpoint_success"] = pay
            if progress is not None and hasattr(progress, "claim_checkpoint_success"):
                progress.claim_checkpoint_success()
            # Mid-chunk: reset a fresh 12m wall for the next planner step.
            if (
                progress is not None
                and not planner_loyal_queue.done
                and hasattr(progress, "arm_cell_timeout")
            ):
                progress.arm_cell_timeout(int(FLAT_CELL_TIMEOUT_FRAMES))

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
            rewarded_cutscenes=(
                progress.observed_cutscenes | progress.rewarded_cutscenes
            ),
            visited_rooms=progress.visited_rooms,
            peak_room=state.get("_skip_peak_room"),
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

    if (
        rails_mode
        and ENABLE_CHECKPOINT_PATH
        and graph is not None
        and not planner_loyal
    ):
        pg_prev, pd_prev = potential(prev_state, planner, graph)
        pg_now, pd_now = potential(state, planner, graph)
        bd["pbrs_graph"] = SHAPING_GAMMA * pg_now - pg_prev
        bd["pbrs_door"] = SHAPING_GAMMA * pd_now - pd_prev

        target = planner.next_waypoint_room()
        if room_changed and target is not None:
            from re1_rl.barry_rescue_checkpoint import (
                should_suppress_wrong_room as barry_suppress_wrong_room,
            )
            from re1_rl.richard_cutscene_checkpoint import (
                note_richard_cutscene_room_transition,
                should_suppress_wrong_room as richard_suppress_wrong_room,
            )
            from re1_rl.yawn_box_prep_checkpoint import (
                should_suppress_wrong_room as yawn_box_prep_suppress_wrong_room,
            )

            # Mint 20D:richard on the scripted dump so cp84 cannot auto-fire
            # from a bare observed_cutscene prefix (see planner gate).
            if progress is not None:
                note_richard_cutscene_room_transition(
                    planner, progress, prev_room, room, state
                )

            if (
                barry_suppress_wrong_room(planner, prev_room, room, state)
                or richard_suppress_wrong_room(planner, prev_room, room, state)
                or yawn_box_prep_suppress_wrong_room(planner, prev_room, room, state)
            ):
                pass
            else:
                left_target = (
                    prev_room == str(target)
                    and room != str(target)
                    and planner.next_waypoint_room() == str(target)
                )
                off_rails = False
                if left_target:
                    off_rails = True
                elif room != str(target):
                    prev_hops = graph.hop_distance(prev_room, str(target))
                    now_hops = graph.hop_distance(room, str(target))
                    off_rails = now_hops is None or (
                        prev_hops is not None and now_hops >= prev_hops
                    )
                    if off_rails and not graph.knows_room(str(target)):
                        off_rails = False
                if off_rails:
                    claimed = True
                    if progress is not None and not left_target:
                        claimed = progress.claim_offroute_penalty(room)
                    if claimed:
                        # All rails wrong-way: -4 and end the episode (no soft
                        # retreat / sparse -3). Leave-target always claims.
                        bd["wrong_room"] = WRONG_ROOM_TERMINAL_PENALTY
                        if progress is not None:
                            progress.breach_wrong_room()

    if room_changed and is_new_room:
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
    # First acquire still claims weapons_progressed (anti-farm / planner).
    # Pay and idle-extend are off (2026-08-16). Shotgun re-takes still claw return.
    ammo_progress = False
    box_unpaid = _box_inventory_unpaid(state)
    for raw in new_items:
        name = canonical_item(str(raw))
        if (
            rails_mode
            and name in YAWN_FORBIDDEN_ITEMS
            and progress is not None
            and progress.breach_forbidden_item()
        ):
            bd["forbidden_item"] = FORBIDDEN_ITEM_TERMINAL_PENALTY
        if progress is not None:
            progress.note_leg_acquired(name)
        if name in _KEY_ITEM_NAME_SET:
            if progress is None or progress.claim_key_item_bonus(name):
                if not box_unpaid:
                    bd["key_item"] += KEY_ITEM_PICKUP_BONUS
                    acquired_key_or_weapon = True
        elif name in _WEAPON_NAME_SET:
            first_weapon = (
                progress is not None and progress.claim_weapon_progress(name)
            )
            shotgun_retake = (
                name == "shotgun"
                and progress is not None
                and "shotgun" in progress.weapons_progressed
                and not first_weapon
            )
            if progress is None or first_weapon or shotgun_retake:
                if not box_unpaid:
                    bd["new_weapon"] += NEW_WEAPON_PICKUP_BONUS
                    if first_weapon:
                        acquired_key_or_weapon = True
        elif name in AMMO_ITEM_NAMES:
            if not box_unpaid:
                bd["ammo_pickup"] += AMMO_PICKUP_BONUS
                ammo_progress = True
        else:
            if (
                not box_unpaid
                and (
                    name not in HEALTH_RESOURCE_ITEMS
                    or _health_pickup_adds_resource(
                        name, prev_state=prev_state, state=state
                    )
                )
            ):
                bd["item"] += ITEM_PICKUP_BONUS

    if state.get("box_withdraw_success"):
        bd["box_withdraw"] = BOX_WITHDRAW_BONUS

    # cp89: closing the box before prep-ready is an invalid-cell terminal (−4).
    if (
        rails_mode
        and state.get("yawn_box_prep_early_close")
        and progress is not None
    ):
        progress.breach_capture_ineligible()
        for term, value in tuple(bd.items()):
            if value > 0.0:
                bd[term] = 0.0
        bd["checkpoint_capture_ineligible"] = RAILS_CAPTURE_INELIGIBLE_PENALTY

    # cp89 only: pay once per wind crest / armor key successfully banked.
    if (
        rails_mode
        and state.get("box_deposit_success")
        and progress is not None
        and not progress.kenneth_gate_breached
        and YAWN_BOX_KEY_DEPOSIT_BONUS > 0.0
    ):
        from re1_rl.yawn_box_prep_checkpoint import (
            YAWN_BOX_PREP_BANKED_KEYS,
            YAWN_BOX_PREP_CHECKPOINT_ID,
            deposited_yawn_box_key_names,
        )

        obj = planner.current_objective() if planner is not None else None
        cid = str((obj or {}).get("checkpoint_id") or "")
        if cid == YAWN_BOX_PREP_CHECKPOINT_ID:
            for name in deposited_yawn_box_key_names(prev_state, state):
                if name in YAWN_BOX_PREP_BANKED_KEYS and progress.claim_yawn_box_key_deposit(
                    name
                ):
                    bd["yawn_box_key_deposit"] += YAWN_BOX_KEY_DEPOSIT_BONUS

    # Observation and payout are separate ledgers. Every qualified key is
    # observed; payment still requires a new-room pairing on this transition.
    cutscene_key = state.get("cutscene_key") if not new_items else None
    if (
        cutscene_key
        and progress is not None
        and not progress.kenneth_gate_breached
    ):
        progress.observe_cutscene(str(cutscene_key))
        room_paired = (room_changed and is_new_room) or bool(
            state.get("cutscene_paired_new_room")
        )
        if room_paired and progress.claim_cutscene_bonus(str(cutscene_key)):
            bd["new_cutscene"] = NEW_CUTSCENE_BONUS

    if progress is not None and not progress.kenneth_gate_breached:
        from re1_rl.barry_return_checkpoint import note_kenneth_live_scene

        note_kenneth_live_scene(progress, state)

    # Same edge as PB typewriter capture (detector complete). Modest crumb;
    # does not extend the 12 min idle floor. Sidecar episode starts suppress
    # the detector until control+ribbon count are stable (see TypewriterSaveDetector).
    if typewriter_save_complete and not (
        progress is not None and progress.kenneth_gate_breached
    ):
        bd["typewriter_save"] = TYPEWRITER_SAVE_BONUS

    if progress is not None:
        room_now = str(state.get("room_id", "") or "")
        progress.clear_pickup_cutscene_block_if_left(room_now)
        if acquired_key_or_weapon:
            progress.note_pickup_cutscene_block(room_now)

    from re1_rl.weapon_equip import inventory_entries_to_names

    prev_inventory = set(inventory_entries_to_names(prev_state.get("inventory")))
    inventory = set(inventory_entries_to_names(state.get("inventory")))
    if progress is not None:
        gallery_pay, gallery_wrong = progress.gallery_step_reward(
            prev_room=prev_room,
            room=room,
            prev_raw=int(prev_state.get("gallery_progress", 0) or 0),
            raw=int(state.get("gallery_progress", 0) or 0),
            prev_confirm=int(prev_state.get("gallery_confirm", 0) or 0),
            confirm=int(state.get("gallery_confirm", 0) or 0),
            star_crest_held="star_crest" in inventory,
            x=float(state.get("x", 0) or 0),
            z=float(state.get("z", 0) or 0),
            prev_x=float(prev_state.get("x", 0) or 0),
            prev_z=float(prev_state.get("z", 0) or 0),
        )
        bd["gallery"] = gallery_pay
        bd["gallery_wrong"] = gallery_wrong
        from re1_rl.dining_statue_puzzle import (
            dining_statue_knocked_from_state,
            dining_statue_progress_reward,
        )

        bd["dining_statue_progress"] = dining_statue_progress_reward(
            prev_state, state, planner
        )
        if bool(state.get("in_control")) and progress.claim_dining_statue_bonus(
            knocked=dining_statue_knocked_from_state(state),
            prev_knocked=dining_statue_knocked_from_state(prev_state),
            room_id=room,
        ):
            bd["dining_statue"] = DINING_STATUE_BONUS
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
            if rails_mode:
                progress.breach_shotgun_return()

    if (
        progress is not None
        and not state.get("dead")
        and int(state.get("hp", 0) or 0) > 0
        and not _key_item_return_blocked(state=state, room=room)
    ):
        for name in sorted(prev_inventory - inventory):
            if name not in _KEY_ITEM_NAME_SET:
                continue
            if name not in progress.key_items_rewarded:
                continue
            bd["key_item_return"] += KEY_ITEM_RETURN_PENALTY
            progress.release_key_item_reward(name)

    story_use_site = state.get("story_use_success")
    if story_use_site and progress is not None:
        if progress.claim_story_use_bonus(str(story_use_site)):
            bd["story_use"] = STORY_ITEM_USE_BONUS

    if rails_mode:
        state["typewriter_save_complete"] = bool(typewriter_save_complete)
        if progress is not None:
            state["gallery_puzzle_solved"] = bool(progress.gallery_puzzle_solved)
        # While a CP freeze is waiting to capture, do not advance further
        # (room_enter already-true after Richard 20D→204 dump).
        freeze_pending = False
        # planner is the env's planner; freeze flag lives on env when available.
        # compute_reward does not get env — use progress attribute set by env.
        if progress is not None and getattr(progress, "checkpoint_freeze_pending", False):
            freeze_pending = True
        advanced = False
        if not freeze_pending and not planner_loyal:
            advanced = bool(
                planner.advance_if_success(
                    state, progress=progress, prev_state=prev_state
                )
            )
        if advanced:
            if progress is not None and progress.shotgun_return_breached:
                claimed = False
            elif progress is not None:
                progress.on_waypoint_advanced()
                claimed = progress.claim_checkpoint_success()
            else:
                claimed = True
            if claimed:
                extra = int(state.get("step_emulated_frames") or 0)
                bd["checkpoint_success"] = (
                    rails_checkpoint_success_reward(progress, extra_frames=extra)
                    if rails_mode
                    else CHECKPOINT_REWARD
                )
                # Legacy telemetry alias remains zero; checkpoint_success is
                # intentionally explicit in rollout accounting.
                if progress is not None and not progress.checkpoint_success:
                    from re1_rl.yawn_cell_timeout import cell_timeout_frames_for_planner

                    progress.arm_cell_timeout(
                        cell_timeout_frames_for_planner(
                            planner, progress.timeout_table_root
                        )
                    )
        if progress is not None and not bd["checkpoint_success"]:
            from re1_rl.barry_return_checkpoint import fail_barry_return_if_unmet

            fail_barry_return_if_unmet(
                planner,
                progress,
                bd,
                RAILS_CAPTURE_INELIGIBLE_PENALTY,
                room_id=room,
                state=state,
            )

    if (
        rails_mode
        and progress is not None
        and "step_emulated_frames" in state
        and not bd["checkpoint_success"]
        and not progress.kenneth_gate_breached
        and not progress.wrong_room_breached
        and not progress.forbidden_item_breached
        and not progress.cell_timeout_breached
        and not progress.shotgun_return_breached
    ):
        progress.note_leg_frames(int(state.get("step_emulated_frames") or 0))
        if (
            progress.cell_timeout_frames > 0
            and progress.leg_emulated_frames >= progress.cell_timeout_frames
            and progress.breach_cell_timeout()
        ):
            bd["checkpoint_timeout"] = RAILS_CELL_TIMEOUT_PENALTY

    if state.get("gold_emblem_return"):
        bd["gold_emblem_return"] = GOLD_EMBLEM_RETURN_PENALTY

    prev_hp = int(prev_state.get("hp", 0))
    hp = int(state.get("hp", 0))
    hp_delta = hp - prev_hp
    # Ignore menu/cutscene init (prev_hp==0) and impossible slingshots above
    # Jill max (96). Death chip still allows hp==0.
    if hp_delta < 0 and _player_hp_in_reward_band(prev_hp) and hp <= JILL_FINE_HP:
        bd["hp"] = HP_LOSS_SCALE * hp_delta
    elif (
        hp_delta > 0
        and _player_hp_in_reward_band(prev_hp)
        and _player_hp_in_reward_band(hp)
    ):
        bd["hp"] = hp_heal_reward(hp_delta)

    # Actual death owns the ordinary death channel. Otherwise the first Kenneth
    # gate breach contributes once under its explicit telemetry key.
    if state.get("dead"):
        bd["death"] = DEATH_PENALTY
    elif new_kenneth_gate_breach:
        bd["main_hall_before_kenneth"] = MAIN_HALL_BEFORE_KENNETH_PENALTY

    enemy_damage_pay, enemy_kill_pay = enemy_combat_rewards(state)
    if enemy_damage_pay > 0.0:
        bd["enemy_damage"] = enemy_damage_pay
    if enemy_kill_pay > 0.0:
        bd["enemy_kill"] = enemy_kill_pay

    reload_pay = low_ammo_reload_reward(prev_state, state)
    if reload_pay > 0.0:
        bd["weapon_reload"] = reload_pay

    overkill = combat_overkill_penalty(state)
    if overkill < 0.0:
        bd["combat_overkill"] = overkill

    dog_sg = shotgun_dog_hit_penalty(state)
    if dog_sg < 0.0:
        bd["shotgun_dog_hit"] = dog_sg

    heavy_fodder = heavy_weapon_fodder_hit_penalty(state)
    if heavy_fodder < 0.0:
        bd["heavy_weapon_fodder_hit"] = heavy_fodder

    # Ammo expenditure: every spent round (hit or miss). Deferred miss expiry
    # re-posts ammo_spent after the fire step already paid — skip that replay.
    rounds_spent = int(state.get("ammo_spent", 0) or 0)
    if rounds_spent > 0 and not state.get("pending_combat_expired"):
        bd["ammo_spend"] = ammo_spend_penalty(
            _combat_ammo_weapon_id(state), rounds_spent,
        )

    # Miss taxes: gun ammo waste on attack_missed; knife whiff on knife_swing_missed
    # (any knife-equipped macro height). Extra inefficiency on top of spend tax.
    if state.get("knife_swing_missed"):
        bd["attack_miss"] = KNIFE_MISS_PENALTY
    elif state.get("attack_missed"):
        waste_rounds = int(state.get("deferred_waste_rounds") or 0)
        if waste_rounds <= 0:
            waste_rounds = rounds_spent
        if waste_rounds > 0:
            from re1_rl.ammo_accounting import fireable_ammo_before_miss

            # Deferred projectile miss may resolve after a weapon swap.
            wid = _combat_ammo_weapon_id(state)
            ammo_before = fireable_ammo_before_miss(
                state, wid, rounds_spent=waste_rounds,
            )
            bd["ammo_waste"] = ammo_waste_penalty(
                wid,
                waste_rounds,
                ammo_before=ammo_before,
            )
    if state.get("attack_dry_fire"):
        bd["attack_dry_fire"] = ATTACK_DRY_FIRE_PENALTY
    elif state.get("attack_macro_failure"):
        bd["attack_macro_failure"] = ATTACK_MACRO_FAILURE_PENALTY

    if progress is not None and (
        progress.kenneth_gate_breached
        or progress.wrong_room_breached
        or progress.forbidden_item_breached
        or progress.cell_timeout_breached
        or progress.capture_ineligible_breached
        or progress.shotgun_return_breached
    ):
        for term, value in bd.items():
            if value > 0.0:
                bd[term] = 0.0

    if rails_mode and not planner_loyal:
        for term, value in tuple(bd.items()):
            if term == "checkpoint_success":
                continue
            if value > 0.0 and term not in RAILS_UNSCALED_COMBAT_TERMS:
                scale = (
                    RAILS_NAV_POSITIVE_SCALE
                    if term in RAILS_NAV_POSITIVE_TERMS
                    else RAILS_MINOR_POSITIVE_SCALE
                )
                bd[term] = value * scale
            elif value < 0.0 and term in RAILS_SCALED_CLAWBACK_TERMS:
                bd[term] = value * RAILS_NAV_POSITIVE_SCALE

    if progress is not None and not state.get("dead"):
        # Ammo / rails checkpoint → 12 min idle. Room / weapon / statue-knock
        # / cutscene / document / key / story-use do not extend (2026-08-16).
        if (
            bd["checkpoint_success"] != 0.0
            or ammo_progress
            or bd["yawn_box_key_deposit"] != 0.0
        ):
            progress.note_softlock_extension(SOFTLOCK_EXTENSION_FRAMES)
            softlock_threshold = softlock_frame_threshold(progress)
        if bd["checkpoint_success"] != 0.0:
            progress.note_max_steps_extension(CHECKPOINT_MAX_STEPS_EXTENSION)
        made_progress = (
            bd["gallery"] > 0.0
            or bd["dining_statue_progress"] > 0.0
            or bd["armor_statue_progress"] > 0.0
            or bd["yawn_box_key_deposit"] > 0.0
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

    if planner_loyal:
        # Strip legacy crumbs / rails pulses; keep contempt, combat, HP heal,
        # heal-use tax, and planner step/divert channels.
        for key in (
            "pbrs_graph",
            "pbrs_door",
            "waypoint",
            "new_room",
            "document_examine",
            "new_cutscene",
            "typewriter_save",
            "item",
            "ammo_pickup",
            "box_withdraw",
            "yawn_box_key_deposit",
            "key_item",
            "story_use",
            "gallery",
            "dining_statue",
            "dining_statue_progress",
            "new_weapon",
            "weapon_reload",
            "checkpoint_capture_ineligible",
            "checkpoint_timeout",
            "success_room",
        ):
            bd[key] = 0.0
        # Alias channels stay populated for capture / telemetry; do not
        # double-count them in the scalar reward. Divert scores planner_divert
        # once (−4); wrong_room is a terminal-path alias only.
        skip_alias = 0.0
        if bd["planner_divert"] != 0.0:
            bd["wrong_room"] = bd["planner_divert"]
            skip_alias += bd["planner_divert"]
        elif bd["planner_step_success"] != 0.0:
            bd["checkpoint_success"] = bd["planner_step_success"]
            bd["wrong_room"] = 0.0
            skip_alias += bd["checkpoint_success"]
        reward = float(sum(bd.values()) - skip_alias) * REWARD_SCALE
    else:
        reward = float(sum(bd.values())) * REWARD_SCALE
    if return_breakdown:
        return reward, bd
    return reward
