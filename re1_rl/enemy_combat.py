"""Enemy HP deltas from live RAM table reads."""

from __future__ import annotations

import os
from typing import Any

# Imperator-validated 2026-07-23: infinite-respawn pests and the aquarium
# shark (Neptune) must never mint #7/#8 combat pay. Kind filter prefers
# type_id once H1.4 lands; room deny is a bridge for exclusive pest rooms.
# kind@0x05 from hub-door classify (wasp/adder/shark). Confirm before adding more.
NO_COMBAT_REWARD_TYPE_IDS: frozenset[int] = frozenset({0x0A, 0x0B, 0x0D})
NO_COMBAT_REWARD_TYPE_NAMES: frozenset[str] = frozenset(
    {"wasp", "bee", "hornet", "adder", "snake_adder", "shark", "neptune"}
)
# Exclusive rooms confirmed by hub-door classify (2026-07-23) until type_id
# is wired. Expand only when a room is pest/shark-only (no paid fauna).
# 408 honeycomb wasps; 301 water-gate adder swarm; 40E water-tank Neptunes.
NO_COMBAT_REWARD_ROOMS: frozenset[str] = frozenset({"408", "301", "40E"})

# Crow gallery pests: active_byte from live QS0 / room_enemies notes.
CROW_ACTIVE_BYTES: frozenset[int] = frozenset({0x04, 0x1C})
CROW_IDLE_ACTIVE_BYTE = 0x04
CROW_FLYING_ACTIVE_BYTE = 0x1C
# Live cp27 / room 117: kind@0x05=0x0D with active_byte 0x00 (not 0x04/0x1C).
GALLERY_CROW_RAM_TYPE_ID = 0x0D
GALLERY_CROW_ROOMS: frozenset[str] = frozenset({"107", "117", "212"})
# Exclusive crow combat rooms (no paid fauna spawns in almanac).
CROW_ONLY_COMBAT_ROOMS: frozenset[str] = frozenset({"117"})

# Cerberus / zombie-dog (hub-door classify + dog_attack_ram_trace 2026-07).
# kind@0x05 is shared with Tyrant/Yawn — pair with HP band + active_byte.
CERBERUS_RAM_TYPE_ID = 0x0F
CERBERUS_HP_MAX = 120  # live dogs ~100; Tyrant 220+, Yawn 3050+
CERBERUS_ACTIVE_BYTES: frozenset[int] = frozenset({0x2C, 0x44, 0x90})
CERBERUS_TYPE_NAMES: frozenset[str] = frozenset({"cerberus", "zombie_dog"})

# Ordinary mansion zombies (test_enemy_combat + RDT model map).
ZOMBIE_RAM_TYPE_IDS: frozenset[int] = frozenset({0x01, 0x02})
ZOMBIE_TYPE_NAMES: frozenset[str] = frozenset({"zombie"})

# Parked enemy slots keep hp>0 at fixed sentinel coords across unrelated rooms.
# Exact (x, z) matches only — attack-mask path; does not affect combat pay/obs.
# See docs/post_statue_campaign.plan.md (payforward fight ammo audit).
POOL_GHOST_FINGERPRINTS: frozenset[tuple[int, int]] = frozenset(
    {
        (6456, 5245),  # hub-door classify ghost (many rooms)
        (24725, 6252),  # live fleet s4 bleed (202/204/207/11A)
        (24717, 6060),  # fleet bleed stack (202/204/207/107)
        (24941, 5299),  # live fleet s5 bleed
        (2710, 10875),  # cross-room bleed parked zombie row
        (30000, 30000),  # RDT pool park (e.g. cp18 / room 108)
    }
)
# Quantize world coords when detecting corpse piles for attack-mask only.
_ATTACK_MASK_STACK_QUANT = 32


def mask_attack_pool_ghosts_enabled() -> bool:
    return os.environ.get("MASK_ATTACK_POOL_GHOSTS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def is_pool_ghost_coordinate(x: int | float, z: int | float) -> bool:
    """True when coords match a known off-map / parked slot fingerprint."""
    return (int(x), int(z)) in POOL_GHOST_FINGERPRINTS


def is_pool_ghost_enemy(ent: dict[str, Any]) -> bool:
    if "x" not in ent or "z" not in ent:
        return False
    return is_pool_ghost_coordinate(ent["x"], ent["z"])


def mask_attack_corpse_stacks_enabled() -> bool:
    """Skip stacked inactive corpse piles for attack-mask counts only."""
    return os.environ.get("MASK_ATTACK_CORPSE_STACKS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _attack_mask_coord_key(x: int | float, z: int | float) -> tuple[int, int]:
    q = int(_ATTACK_MASK_STACK_QUANT)
    return (int(x) // q, int(z) // q)


def _attack_mask_protected_entity(ent: dict[str, Any]) -> bool:
    """Entities we never hide from attack mask when uncertain."""
    ab = int(ent.get("active_byte", 0))
    if ab != 0:
        return True
    meta = {
        "type_id": ent.get("type_id"),
        "active_byte": ab,
        "hp_before": int(ent.get("hp", 0)),
    }
    return is_cerberus_combat_entity(meta, hp_before=int(ent.get("hp", 0)))


def _stacked_inactive_corpse_coords(
    enemies: list[dict[str, Any]] | None,
) -> frozenset[tuple[int, int]]:
    """Coords where 2+ combat_near rows share a tile and all are inactive."""
    from collections import defaultdict

    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for ent in enemies or []:
        if int(ent.get("hp", 0)) <= 0:
            continue
        if not int(ent.get("combat_near", 0)):
            continue
        if "x" not in ent or "z" not in ent:
            continue
        groups[_attack_mask_coord_key(ent["x"], ent["z"])].append(ent)
    corpse: set[tuple[int, int]] = set()
    for coord, group in groups.items():
        if len(group) < 3:
            continue
        if any(_attack_mask_protected_entity(e) for e in group):
            continue
        if all(int(e.get("active_byte", 0)) == 0 for e in group):
            corpse.add(coord)
    return frozenset(corpse)


def _skip_for_attack_mask(
    ent: dict[str, Any],
    *,
    all_enemies: list[dict[str, Any]] | None,
    corpse_coords: frozenset[tuple[int, int]] | None = None,
) -> bool:
    if mask_attack_pool_ghosts_enabled() and is_pool_ghost_enemy(ent):
        return True
    if not mask_attack_corpse_stacks_enabled():
        return False
    if _attack_mask_protected_entity(ent):
        return False
    if int(ent.get("active_byte", 0)) != 0:
        return False
    if corpse_coords is None:
        corpse_coords = _stacked_inactive_corpse_coords(all_enemies)
    if "x" not in ent or "z" not in ent:
        return False
    return _attack_mask_coord_key(ent["x"], ent["z"]) in corpse_coords


def is_crow_combat_entity(meta: dict[str, Any]) -> bool:
    """True when RAM meta identifies a crow (gallery pests)."""
    name = str(meta.get("type_name") or meta.get("enemy_type") or "").lower()
    if name == "crow":
        return True
    ab = meta.get("active_byte")
    return ab is not None and int(ab) in CROW_ACTIVE_BYTES


def _crow_meta_from_enemy(ent: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    name = ent.get("type_name") or ent.get("enemy_type")
    if name is not None:
        meta["type_name"] = str(name)
    if "active_byte" in ent:
        meta["active_byte"] = int(ent["active_byte"])
    return meta


def is_crow_enemy(
    ent: dict[str, Any],
    *,
    room_id: str | None = None,
) -> bool:
    """True for gallery crows (zero combat pay; attack macros should stay masked)."""
    if int(ent.get("hp", 0)) <= 0:
        return False
    if is_crow_combat_entity(_crow_meta_from_enemy(ent)):
        return True
    rid = str(room_id or "").upper()
    if rid not in GALLERY_CROW_ROOMS:
        return False
    tid = ent.get("type_id")
    if tid is None or int(tid) != GALLERY_CROW_RAM_TYPE_ID:
        return False
    return bool(int(ent.get("in_room", ent.get("alive", 0))))


def is_cerberus_combat_entity(
    meta: dict[str, Any],
    *,
    hp_before: int | None = None,
) -> bool:
    """True for cerberus / zombie-dog hits (not Tyrant/Yawn at kind 0x0F)."""
    name = str(meta.get("type_name") or meta.get("enemy_type") or "").lower()
    if name in CERBERUS_TYPE_NAMES:
        return True
    tid = meta.get("type_id")
    if tid is None or int(tid) != CERBERUS_RAM_TYPE_ID:
        return False
    hp = int(hp_before if hp_before is not None else meta.get("hp_before", 0))
    if hp <= 0 or hp > CERBERUS_HP_MAX:
        return False
    ab = meta.get("active_byte")
    if ab is None:
        return False
    return int(ab) in CERBERUS_ACTIVE_BYTES


def is_zombie_combat_entity(
    meta: dict[str, Any],
    *,
    room_id: str | None = None,
    slot: int | None = None,
    hp_before: int | None = None,
) -> bool:
    """True for ordinary zombies (not dogs / crows / denied pests)."""
    name = str(meta.get("type_name") or meta.get("enemy_type") or "").lower()
    if name in ZOMBIE_TYPE_NAMES:
        return True
    if name in CERBERUS_TYPE_NAMES or name == "crow":
        return False
    if name and name in NO_COMBAT_REWARD_TYPE_NAMES:
        return False
    hp = int(
        hp_before
        if hp_before is not None
        else meta.get("hp_before", meta.get("hp", 0)) or 0
    )
    tid = meta.get("type_id")
    if tid is not None and int(tid) == CERBERUS_RAM_TYPE_ID:
        # kind 0x0F is shared with cerberus/Tyrant/Yawn; only dogs match active_byte.
        if is_cerberus_combat_entity(meta, hp_before=hp):
            return False
        if 0 < hp <= CERBERUS_HP_MAX:
            return True
    if tid is not None and int(tid) in ZOMBIE_RAM_TYPE_IDS:
        return True
    # Live type_id is often 0 — fall back to static room roster by slot.
    if room_id is not None and slot is not None and (
        tid is None or int(tid) == 0
    ):
        from re1_rl.attack_log_context import infer_enemy_type_for_slot

        return infer_enemy_type_for_slot(str(room_id), int(slot)) == "zombie"
    return False


def is_passive_crow_enemy(
    ent: dict[str, Any],
    *,
    room_id: str | None = None,
) -> bool:
    """True for idle gallery crows (active_byte 0x04 or type-only crow tag)."""
    if not is_crow_enemy(ent, room_id=room_id):
        return False
    meta = _crow_meta_from_enemy(ent)
    ab = meta.get("active_byte")
    if ab is None:
        return True
    return int(ab) in (0, CROW_IDLE_ACTIVE_BYTE)


def combat_reward_denied(
    *,
    room_id: str | None = None,
    type_id: int | None = None,
    type_name: str | None = None,
) -> bool:
    """True when damage/kill on this entity/room must not pay."""
    if room_id is not None and str(room_id).upper() in NO_COMBAT_REWARD_ROOMS:
        return True
    if type_id is not None and int(type_id) in NO_COMBAT_REWARD_TYPE_IDS:
        return True
    if type_name is not None and str(type_name).lower() in NO_COMBAT_REWARD_TYPE_NAMES:
        return True
    return False


def alive_enemy_count(enemies: list[dict[str, Any]] | None) -> int:
    """Enemies with in-room coordinates (excludes off-map pool ghosts)."""
    n = 0
    for ent in enemies or []:
        if not ent.get("alive", True):
            continue
        if int(ent.get("hp", 0)) > 0:
            n += 1
    return n


def combat_enemy_count(
    enemies: list[dict[str, Any]] | None,
    *,
    max_dist: float | None = None,
    knife: bool = False,
    for_attack_mask: bool = False,
) -> int:
    """Enemies near enough to justify knife/attack.

    Default: ``combat_near`` (gun band, ``ENEMY_COMBAT_NEAR_DIST``).
    ``knife=True`` uses ``knife_near`` (``ENEMY_KNIFE_COMBAT_NEAR_DIST``).
    ``max_dist`` overrides both and requires ``in_room`` + stored ``dist``.
    """
    n = 0
    corpse_coords = (
        _stacked_inactive_corpse_coords(enemies) if for_attack_mask else frozenset()
    )
    for ent in enemies or []:
        if int(ent.get("hp", 0)) <= 0:
            continue
        if for_attack_mask and _skip_for_attack_mask(
            ent, all_enemies=enemies, corpse_coords=corpse_coords
        ):
            continue
        if max_dist is not None:
            if not int(ent.get("in_room", ent.get("alive", 0))):
                continue
            if float(ent.get("dist", 1e18)) >= float(max_dist):
                continue
            n += 1
            continue
        flag = "knife_near" if knife else "combat_near"
        if int(ent.get(flag, 0)):
            n += 1
    return n


def paid_combat_enemy_count(
    enemies: list[dict[str, Any]] | None,
    *,
    knife: bool = False,
    room_id: str | None = None,
    for_attack_mask: bool = False,
) -> int:
    """Near-band enemies that justify attack macros.

    Gallery crows are excluded (zero combat pay). Crow-only rooms (117) mask
    attack even when RAM active_byte/type tags are missing.
    """
    rid = str(room_id or "").upper()
    if rid in CROW_ONLY_COMBAT_ROOMS:
        if combat_enemy_count(enemies, knife=knife) > 0:
            return 0
    n = 0
    corpse_coords = (
        _stacked_inactive_corpse_coords(enemies) if for_attack_mask else frozenset()
    )
    for ent in enemies or []:
        if int(ent.get("hp", 0)) <= 0:
            continue
        if for_attack_mask and _skip_for_attack_mask(
            ent, all_enemies=enemies, corpse_coords=corpse_coords
        ):
            continue
        if is_crow_enemy(ent, room_id=room_id):
            continue
        flag = "knife_near" if knife else "combat_near"
        if int(ent.get(flag, 0)):
            n += 1
    return n


def enemy_hp_by_slot(enemies: list[dict[str, Any]]) -> dict[int, int]:
    """Map enemy table slot index -> HP (living enemies only)."""
    out: dict[int, int] = {}
    for ent in enemies:
        slot = int(ent.get("slot", -1))
        hp = int(ent.get("hp", 0))
        if slot >= 0 and hp > 0:
            out[slot] = hp
    return out


def enemy_combat_delta(
    prev: dict[int, int], curr: dict[int, int]
) -> tuple[int, int]:
    """Return (total_damage_dealt, kill_count) across enemy slots."""
    damage = 0
    kills = 0
    for slot in set(prev) | set(curr):
        before = int(prev.get(slot, 0))
        after = int(curr.get(slot, 0))
        if before <= 0:
            continue
        if after <= 0:
            kills += 1
            damage += before
        elif after < before:
            damage += before - after
    return damage, kills


def _type_meta_by_slot(
    enemies: list[dict[str, Any]] | None,
) -> dict[int, dict[str, Any]]:
    """slot -> {type_id?, type_name?/enemy_type?} from whichever side has it."""
    out: dict[int, dict[str, Any]] = {}
    for ent in enemies or []:
        slot = int(ent.get("slot", -1))
        if slot < 0:
            continue
        meta: dict[str, Any] = {}
        if "type_id" in ent:
            meta["type_id"] = int(ent["type_id"])
        name = ent.get("type_name") or ent.get("enemy_type")
        if name is not None:
            meta["type_name"] = str(name)
        if "active_byte" in ent:
            meta["active_byte"] = int(ent["active_byte"])
        if "hp" in ent:
            meta["hp"] = int(ent["hp"])
        if meta:
            out[slot] = meta
    return out


def enemy_combat_events(
    prev_enemies: list[dict[str, Any]] | None,
    curr_enemies: list[dict[str, Any]] | None,
    *,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    """Per-slot HP changes: slot, hp_before, hp_after, damage, killed."""
    prev_hps = enemy_hp_by_slot(prev_enemies or [])
    curr_hps = enemy_hp_by_slot(curr_enemies or [])
    types = _type_meta_by_slot(prev_enemies)
    for slot, meta in _type_meta_by_slot(curr_enemies).items():
        types.setdefault(slot, {}).update(meta)
    events: list[dict[str, Any]] = []
    for slot in sorted(set(prev_hps) | set(curr_hps)):
        before = int(prev_hps.get(slot, 0))
        after = int(curr_hps.get(slot, 0))
        if before <= 0:
            continue
        meta = types.get(slot, {})
        denied = combat_reward_denied(
            room_id=room_id,
            type_id=meta.get("type_id"),
            type_name=meta.get("type_name"),
        )
        is_crow = is_crow_combat_entity(meta)
        is_cerberus = is_cerberus_combat_entity(meta, hp_before=before)
        is_zombie = (not is_cerberus) and is_zombie_combat_entity(
            meta, room_id=room_id, slot=slot, hp_before=before,
        )
        extra: dict[str, Any] = {}
        if "type_id" in meta:
            extra["type_id"] = meta["type_id"]
        if "type_name" in meta:
            extra["type_name"] = meta["type_name"]
        if "active_byte" in meta:
            extra["active_byte"] = meta["active_byte"]
        if after <= 0:
            events.append({
                "slot": slot,
                "hp_before": before,
                "hp_after": 0,
                "damage": before,
                "killed": True,
                "reward_denied": denied,
                "is_crow": is_crow,
                "is_cerberus": is_cerberus,
                "is_zombie": is_zombie,
                **extra,
            })
        elif after < before:
            events.append({
                "slot": slot,
                "hp_before": before,
                "hp_after": after,
                "damage": before - after,
                "killed": False,
                "reward_denied": denied,
                "is_crow": is_crow,
                "is_cerberus": is_cerberus,
                "is_zombie": is_zombie,
                **extra,
            })
    return events


def format_enemy_table(enemies: list[dict[str, Any]] | None) -> str:
    """Compact RAM enemy table: ``s0:hp61 s1:hp48``."""
    if not enemies:
        return "-"
    parts: list[str] = []
    for ent in sorted(enemies, key=lambda e: int(e.get("slot", 99))):
        slot = int(ent.get("slot", -1))
        hp = int(ent.get("hp", 0))
        if slot < 0 or hp <= 0:
            continue
        extra = ""
        if "x" in ent and "z" in ent:
            extra = f"@{int(ent['x'])},{int(ent['z'])}"
        if "type_id" in ent:
            extra += f",t{int(ent['type_id'])}"
        parts.append(f"s{slot}:hp{hp}{extra}")
    return " ".join(parts) if parts else "-"


def apply_combat_step_fields(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    *,
    knife: bool = False,
    attack: bool = False,
    credit_damage: bool = False,
) -> dict[str, Any]:
    """Attach ``enemy_damage`` / ``enemy_kills`` (and miss flags) like ``env.step``.

    Room changes unload the previous room's enemy table — that must not count as
    kills (door-loop farm: exit tea room → Kenneth slot vanishes → +damage/+kill).

    HP flicker / despawn during interact / door / cutscene (no knife or attack
    this step) must not pay either — live dining door interact minted +0.06.

    ``credit_damage`` pays HP deltas while a pending shot window is open (dog
    lag / grenade flight). It must not set miss flags. Arm via
    ``tick_pending_combat_credit`` after a fire that has not yet resolved.
    """
    out = dict(state)
    prev_room = str(prev_state.get("room_id", "") or "")
    curr_room = str(out.get("room_id", "") or "")
    if prev_room and curr_room and prev_room != curr_room:
        out["enemy_damage"] = 0
        out["enemy_kills"] = 0
        out["combat_events"] = []
        return out

    if not knife and not attack and not credit_damage:
        out["enemy_damage"] = 0
        out["enemy_kills"] = 0
        out["combat_events"] = []
        return out

    prev_enemies = list(prev_state.get("enemies", []) or [])
    curr_enemies = list(out.get("enemies", []) or [])
    # Room-wide deny (exclusive wasp/adder/shark halls) zeroes combat pay even
    # before type_id is mapped — still record events with reward_denied.
    if combat_reward_denied(room_id=curr_room):
        combat_events = enemy_combat_events(
            prev_enemies, curr_enemies, room_id=curr_room
        )
        out["enemy_damage"] = 0
        out["enemy_kills"] = 0
        out["combat_events"] = combat_events
        out["combat_reward_denied"] = True
        if not combat_events:
            out["knife_swing_missed"] = knife
            out["attack_missed"] = attack
        return out

    combat_events = enemy_combat_events(
        prev_enemies, curr_enemies, room_id=curr_room
    )
    enemy_damage = 0
    enemy_kills = 0
    for ev in combat_events:
        if ev.get("reward_denied"):
            continue
        enemy_damage += int(ev.get("damage", 0))
        if ev.get("killed"):
            enemy_kills += 1
    out["enemy_damage"] = enemy_damage
    out["enemy_kills"] = enemy_kills
    out["combat_events"] = combat_events
    if enemy_damage == 0 and enemy_kills == 0:
        out["knife_swing_missed"] = knife
        out["attack_missed"] = attack
    return out


# Emulated-frame windows (60fps). Training turbo must not change these.
HITSCAN_PENDING_FRAMES = 30  # 0.5s — beretta/shotgun/knife settle lag
PROJECTILE_PENDING_FRAMES = 120  # 2.0s — grenade / bazooka flight


def pending_combat_window_frames(weapon_id: int) -> int:
    """How long to wait for HP to post after a fire."""
    from re1_rl.attack_macro import BAZOOKA_WEAPON_IDS

    if int(weapon_id) in BAZOOKA_WEAPON_IDS:
        return PROJECTILE_PENDING_FRAMES
    return HITSCAN_PENDING_FRAMES


def _clear_pending_fields(out: dict[str, Any]) -> None:
    out["pending_combat_frames"] = 0
    out["pending_combat_ammo"] = 0
    out["pending_combat_weapon_id"] = 0
    out["pending_combat_knife"] = False
    out["pending_combat_shots"] = []
    out["combat_damage_credit"] = False


def has_pending_combat(state: dict[str, Any] | None) -> bool:
    """True when at least one attack is still awaiting HP credit."""
    if not state:
        return False
    shots = state.get("pending_combat_shots")
    if isinstance(shots, list) and shots:
        return True
    return int(state.get("pending_combat_frames") or 0) > 0


def _pending_shots_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize legacy scalar pending fields into a per-shot queue."""
    shots = state.get("pending_combat_shots")
    if isinstance(shots, list) and shots:
        return [
            {
                "frames_left": int(s.get("frames_left") or 0),
                "ammo": int(s.get("ammo") or 0),
                "weapon_id": int(s.get("weapon_id") or 0),
                "knife": bool(s.get("knife")),
            }
            for s in shots
        ]
    left = int(state.get("pending_combat_frames") or 0)
    if left <= 0:
        return []
    return [
        {
            "frames_left": left,
            "ammo": int(state.get("pending_combat_ammo") or 0),
            "weapon_id": int(state.get("pending_combat_weapon_id") or 0),
            "knife": bool(state.get("pending_combat_knife")),
        }
    ]


def _sync_pending_scalars(out: dict[str, Any], shots: list[dict[str, Any]]) -> None:
    if not shots:
        _clear_pending_fields(out)
        return
    front = shots[0]
    out["pending_combat_shots"] = shots
    out["pending_combat_frames"] = max(int(s["frames_left"]) for s in shots)
    out["pending_combat_ammo"] = int(front["ammo"])
    out["pending_combat_weapon_id"] = int(front["weapon_id"])
    out["pending_combat_knife"] = bool(front["knife"])
    out["combat_damage_credit"] = True


def _age_pending_shots(
    pending_shots: list[dict[str, Any]],
    tick: int,
) -> tuple[list[dict[str, Any]], int, int, int]:
    still_pending: list[dict[str, Any]] = []
    expired_knife = 0
    expired_gun_ammo = 0
    expired_wid = 0
    for shot in pending_shots:
        left = int(shot["frames_left"]) - tick
        if left <= 0:
            if shot["knife"]:
                expired_knife += 1
            elif int(shot["ammo"]) > 0:
                expired_gun_ammo += int(shot["ammo"])
                expired_wid = int(shot["weapon_id"])
        else:
            still_pending.append({**shot, "frames_left": left})
    return still_pending, expired_knife, expired_gun_ammo, expired_wid


def _apply_expired_miss(
    out: dict[str, Any],
    *,
    expired_knife: int,
    expired_gun_ammo: int,
    expired_wid: int,
) -> None:
    if not expired_knife and not expired_gun_ammo:
        return
    out["pending_combat_expired"] = True
    if expired_knife:
        out["knife_swing_missed"] = True
    if expired_gun_ammo:
        out["attack_missed"] = True
        out["ammo_spent"] = int(expired_gun_ammo)
        out["pending_miss_weapon_id"] = int(expired_wid)


def _finalize_pending(
    out: dict[str, Any],
    pending_shots: list[dict[str, Any]],
    *,
    expired_knife: int = 0,
    expired_gun_ammo: int = 0,
) -> None:
    if pending_shots:
        _sync_pending_scalars(out, pending_shots)
    elif not (expired_knife or expired_gun_ammo):
        _clear_pending_fields(out)
    else:
        out["pending_combat_shots"] = []
        out["pending_combat_frames"] = 0
        out["pending_combat_ammo"] = 0
        out["pending_combat_weapon_id"] = 0
        out["pending_combat_knife"] = False
        out["combat_damage_credit"] = False


def tick_pending_combat_credit(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    *,
    knife: bool = False,
    attack: bool = False,
    step_emulated_frames: int = 8,
    ammo_spent: int = 0,
    weapon_id: int = 0,
    attack_outcome: str = "",
) -> dict[str, Any]:
    """Arm / tick / expire delayed combat credit after ``apply_combat_step_fields``.

    While ``pending_combat_frames`` remain, the next step may pass
    ``credit_damage=True``. Miss / ammo taxes are deferred until the window
    expires with no HP drop (grenade flight, dog HP lag).
    """
    out = state
    prev_room = str(prev_state.get("room_id", "") or "")
    curr_room = str(out.get("room_id", "") or "")
    room_changed = bool(prev_room and curr_room and prev_room != curr_room)

    pending_shots = _pending_shots_from_state(prev_state)

    hit = int(out.get("enemy_damage") or 0) > 0 or int(out.get("enemy_kills") or 0) > 0
    if room_changed:
        _clear_pending_fields(out)
        return out

    outcome = str(attack_outcome or "")
    failed_macro = outcome in ("dry_fire", "illegal_attack") or bool(
        out.get("attack_macro_failure")
    )

    if hit:
        out["credited_from_pending"] = bool(
            pending_shots and not knife and not attack
        )
        if pending_shots:
            pending_shots = pending_shots[1:]

    if knife or attack:
        if failed_macro:
            # Keep in-flight projectiles; failed fire does not arm a new window.
            _finalize_pending(out, pending_shots)
            return out
        # Defer miss: strip same-step macro miss flags and wait for HP to post.
        out.pop("attack_missed", None)
        out.pop("knife_swing_missed", None)
        tick = max(1, int(step_emulated_frames))
        pending_shots, expired_knife, expired_gun_ammo, expired_wid = _age_pending_shots(
            pending_shots, tick
        )
        new_rounds = int(ammo_spent)
        if expired_gun_ammo > 0:
            if new_rounds > 0:
                # Same step: old round(s) missed while firing another.
                out["deferred_waste_rounds"] = int(expired_gun_ammo)
                out["attack_missed"] = True
                out["pending_miss_weapon_id"] = int(expired_wid)
            else:
                _apply_expired_miss(
                    out,
                    expired_knife=expired_knife,
                    expired_gun_ammo=expired_gun_ammo,
                    expired_wid=expired_wid,
                )
        elif expired_knife:
            _apply_expired_miss(
                out,
                expired_knife=expired_knife,
                expired_gun_ammo=0,
                expired_wid=0,
            )
        wid = int(weapon_id or out.get("equipped_weapon_id") or 0)
        pending_shots.append(
            {
                "frames_left": pending_combat_window_frames(wid),
                "ammo": new_rounds,
                "weapon_id": wid,
                "knife": bool(knife),
            }
        )
        _finalize_pending(
            out,
            pending_shots,
            expired_knife=expired_knife,
            expired_gun_ammo=expired_gun_ammo if new_rounds <= 0 else 0,
        )
        return out

    if hit:
        _finalize_pending(out, pending_shots)
        return out

    if not pending_shots:
        _clear_pending_fields(out)
        return out

    tick = max(1, int(step_emulated_frames))
    pending_shots, expired_knife, expired_gun_ammo, expired_wid = _age_pending_shots(
        pending_shots, tick
    )
    _apply_expired_miss(
        out,
        expired_knife=expired_knife,
        expired_gun_ammo=expired_gun_ammo,
        expired_wid=expired_wid,
    )
    _finalize_pending(
        out,
        pending_shots,
        expired_knife=expired_knife,
        expired_gun_ammo=expired_gun_ammo,
    )
    return out
