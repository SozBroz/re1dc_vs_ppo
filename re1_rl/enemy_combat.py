"""Enemy HP deltas from live RAM table reads."""

from __future__ import annotations

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
) -> int:
    """Enemies near enough to justify knife/attack.

    Default: ``combat_near`` (gun band, ``ENEMY_COMBAT_NEAR_DIST``).
    ``knife=True`` uses ``knife_near`` (``ENEMY_KNIFE_COMBAT_NEAR_DIST``).
    ``max_dist`` overrides both and requires ``in_room`` + stored ``dist``.
    """
    n = 0
    for ent in enemies or []:
        if int(ent.get("hp", 0)) <= 0:
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
    for ent in enemies or []:
        if int(ent.get("hp", 0)) <= 0:
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
        if after <= 0:
            events.append({
                "slot": slot,
                "hp_before": before,
                "hp_after": 0,
                "damage": before,
                "killed": True,
                "reward_denied": denied,
                "is_crow": is_crow,
                **({"type_id": meta["type_id"]} if "type_id" in meta else {}),
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
                **({"type_id": meta["type_id"]} if "type_id" in meta else {}),
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

    pending_left = int(prev_state.get("pending_combat_frames") or 0)
    pending_ammo = int(prev_state.get("pending_combat_ammo") or 0)
    pending_wid = int(prev_state.get("pending_combat_weapon_id") or 0)
    pending_knife = bool(prev_state.get("pending_combat_knife"))

    hit = int(out.get("enemy_damage") or 0) > 0 or int(out.get("enemy_kills") or 0) > 0
    if room_changed:
        _clear_pending_fields(out)
        return out

    if hit:
        out["credited_from_pending"] = bool(
            pending_left > 0 and not knife and not attack
        )
        _clear_pending_fields(out)
        return out

    outcome = str(attack_outcome or "")
    failed_macro = outcome in ("dry_fire", "illegal_attack") or bool(
        out.get("attack_macro_failure")
    )

    if knife or attack:
        if failed_macro:
            # Keep immediate miss flags from apply_combat_step_fields.
            _clear_pending_fields(out)
            return out
        # Defer miss: strip same-step miss flags and wait for HP to post.
        out.pop("attack_missed", None)
        out.pop("knife_swing_missed", None)
        wid = int(weapon_id or out.get("equipped_weapon_id") or 0)
        out["pending_combat_frames"] = pending_combat_window_frames(wid)
        out["pending_combat_ammo"] = int(ammo_spent)
        out["pending_combat_weapon_id"] = wid
        out["pending_combat_knife"] = bool(knife)
        out["combat_damage_credit"] = True
        return out

    if pending_left <= 0:
        _clear_pending_fields(out)
        return out

    tick = max(1, int(step_emulated_frames))
    left = max(0, pending_left - tick)
    if left > 0:
        out["pending_combat_frames"] = left
        out["pending_combat_ammo"] = pending_ammo
        out["pending_combat_weapon_id"] = pending_wid
        out["pending_combat_knife"] = pending_knife
        out["combat_damage_credit"] = True
        return out

    # Window expired with no hit — apply deferred miss once.
    _clear_pending_fields(out)
    out["pending_combat_expired"] = True
    if pending_knife:
        out["knife_swing_missed"] = True
    elif pending_ammo > 0:
        out["attack_missed"] = True
        out["ammo_spent"] = pending_ammo
        out["pending_miss_weapon_id"] = pending_wid
    return out
