"""Attack-from-entry-state test harness (any equippable weapon).

Used by ``tests/test_attack_from_entry.py`` (mocked hooks) and
``scripts/run_attack_entry_suite.py`` (live EmuHawk on a probe port).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from re1_rl.attack_macro import KNIFE_WEAPON_ID, execute_attack_macro, equipped_weapon_name
from re1_rl.knife_macro import read_knife_hooks
from re1_rl.memory_map import (
    EQUIPPED_SLOT_INDEX,
    EQUIPPED_SLOT_INDEX_1BASED,
    EQUIPPED_WEAPON_ID,
    INVENTORY_BASE,
    ITEM_IDS,
)
from re1_rl.weapon_equip import EQUIPPABLE_WEAPON_IDS

# Outcomes that mean the macro never got a real shot off.
HARD_FAIL_OUTCOMES = frozenset(
    {
        "settle_interrupt",
        "settle_timeout",
        "aim_interrupt",
        "aim_timeout",
        "recovery_interrupt",
        "recovery_timeout",
        "slash_timeout",
        "ammo_timeout",
        "no_weapon",
        "aborted_interrupt",
    }
)


@dataclass(frozen=True)
class EntryScenario:
    """One pre-attack movement / interaction setup."""

    name: str
    setup: tuple[tuple[str, int], ...] = ()
    # First anim hook when ``attack`` begins (live reads override when present).
    entry_hook: tuple[int, int, int] = (0, 0, 0)
    # Extra hooks burned during ranged settle (after entry_hook).
    settle_prefix: tuple[tuple[int, int, int], ...] = ()


ENTRY_SCENARIOS: tuple[EntryScenario, ...] = (
    EntryScenario("neutral_idle", setup=(("noop", 12),)),
    EntryScenario(
        "after_run",
        setup=(("run_forward", 8), ("noop", 12)),
        entry_hook=(0x0D, 0x01, 0),
        settle_prefix=((0x0D, 0x01, 2), (0x0D, 0x01, 0)),
    ),
    EntryScenario(
        "after_walk",
        setup=(("forward", 8), ("noop", 12)),
        entry_hook=(0x0D, 0x01, 0),
    ),
    EntryScenario(
        "after_turn_left",
        setup=(("turn_left", 6), ("noop", 12)),
        entry_hook=(0x02, 0x00, 0),
    ),
    EntryScenario(
        "after_turn_right",
        setup=(("turn_right", 6), ("noop", 12)),
        entry_hook=(0x02, 0x00, 0),
    ),
    EntryScenario(
        "after_interact_tap",
        setup=(("interact", 1), ("noop", 16)),
        entry_hook=(0, 0, 0),
    ),
    EntryScenario(
        "locomotion_mid_run",
        setup=(("run_forward", 4),),
        entry_hook=(0x06, 0x00, 0),
        settle_prefix=((0x06, 0x00, 0), (0x06, 0x00, 0), (0, 0, 0), (0, 0, 0)),
    ),
    EntryScenario(
        "standing_recovery_latch",
        setup=(("run_forward", 4), ("noop", 4)),
        entry_hook=(0x0D, 0x01, 2),
        settle_prefix=((0x0D, 0x01, 1), (0x0D, 0x01, 0), (0x0D, 0x01, 0)),
    ),
)

ENTRY_SCENARIO_BY_NAME: dict[str, EntryScenario] = {s.name: s for s in ENTRY_SCENARIOS}


def weapon_id_from_name(name: str) -> int:
    key = name.strip().lower().replace("-", "_")
    for wid, label in ITEM_IDS.items():
        if label == key:
            return int(wid)
    raise KeyError(f"unknown weapon name: {name!r}")


def parse_weapon_list(spec: str) -> list[int]:
    """Comma-separated weapon names or ``0x02`` ids."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if part.lower().startswith("0x"):
            out.append(int(part, 16))
        elif part.isdigit():
            out.append(int(part))
        else:
            out.append(weapon_id_from_name(part))
    return out


def default_ammo_for_weapon(weapon_id: int) -> int:
    if int(weapon_id) == KNIFE_WEAPON_ID:
        return 0
    if int(weapon_id) == 0x06:
        return 100
    return 30


def ram_equip_weapon(bridge: Any, weapon_id: int, *, ammo: int | None = None) -> None:
    """Inject weapon + ammo into slot 0 and equip (works for undiscovered guns)."""
    qty = default_ammo_for_weapon(weapon_id) if ammo is None else int(ammo)
    bridge.write_ram(
        [
            ("inv0", INVENTORY_BASE, "u16", (qty << 8) | int(weapon_id)),
            ("eq_p", EQUIPPED_WEAPON_ID, "u8", int(weapon_id)),
            ("eq_s", EQUIPPED_SLOT_INDEX_1BASED, "u8", 1),
            ("eq_slot", EQUIPPED_SLOT_INDEX, "u8", 0),
        ]
    )
    bridge.frameadvance(5)


def _ranged_success_tail() -> list[tuple[int, int, int]]:
    return [
        (0x12, 0x03, 0),
        (0x12, 0x03, 0),
        (0x13, 0x03, 0),
        (0x13, 0x03, 0),
        (0x14, 0x03, 0),
        (0x14, 0x03, 0),
        (0x14, 0x03, 0),
        (0x13, 0x03, 0),
        (0x13, 0x03, 0),
        (0, 0, 0),
        (0, 0, 0),
    ]


def mock_hook_sequence_for_scenario(
    scenario: EntryScenario | str,
    weapon_id: int,
) -> list[tuple[int, int, int]]:
    """Synthetic hook timeline for mocked ``execute_attack_macro`` tests."""
    sc = ENTRY_SCENARIO_BY_NAME[scenario] if isinstance(scenario, str) else scenario
    seq: list[tuple[int, int, int]] = [sc.entry_hook]
    seq.extend(sc.settle_prefix)
    if int(weapon_id) == KNIFE_WEAPON_ID:
        # Standing-neutral knife observes once per aim frame, then Cross.
        # Align slash hooks to begin exactly at the first Cross observe.
        from re1_rl.attack_macro import KNIFE_UP_AIM_FRAMES

        # Production standing knife aims max(KNIFE_UP_AIM_FRAMES, 32) frames.
        aim_frames = max(int(KNIFE_UP_AIM_FRAMES), 32)
        pad = max(0, aim_frames - len(seq))
        seq.extend([(0, 0, 0)] * pad)
        seq.extend([(0x14, 0x04, 0)] * 10)
        seq.extend([(0, 0, 0)] * 20)
        return seq
    seq.extend(_ranged_success_tail())
    return seq


def format_hooks(anim: int, aux: int, recovery: int) -> str:
    return f"anim=0x{anim:02X} aux=0x{aux:02X} rec={recovery}"


def read_entry_snapshot(bridge: Any) -> dict[str, Any]:
    anim, aux, rec = read_knife_hooks(bridge)
    return {
        "hooks": format_hooks(anim, aux, rec),
        "anim": anim,
        "aux": aux,
        "recovery": rec,
    }


def attack_succeeded(
    weapon_id: int,
    report: dict[str, Any],
    *,
    ammo_before: int,
    ammo_after: int,
) -> bool:
    outcome = str(report.get("outcome", ""))
    if outcome in HARD_FAIL_OUTCOMES:
        return False
    if int(weapon_id) == KNIFE_WEAPON_ID:
        return outcome == "ok"
    if int(report.get("ammo_spent", 0)) > 0:
        return True
    if report.get("saw_fire_anim"):
        return True
    return ammo_after < ammo_before and int(weapon_id) != KNIFE_WEAPON_ID


def run_setup(env: Any, setup: Sequence[tuple[str, int]], action_index: dict[str, int]) -> None:
    for action_name, count in setup:
        if action_name == "_r1_pulse":
            for _ in range(int(count)):
                env.bridge.step(n=1, sticky={}, pulse={"r1": True})
            continue
        if action_name == "noop":
            for _ in range(int(count)):
                env.bridge.step(n=1, sticky={}, pulse={})
            continue
        action = action_index[action_name]
        for _ in range(int(count)):
            env.step(action)


def slot0_ammo(bridge: Any, weapon_id: int) -> int:
    raw = int(bridge.read_ram([("inv0", INVENTORY_BASE, "u16")])["inv0"])
    if (raw & 0xFF) != int(weapon_id):
        return 0
    return raw >> 8


@dataclass
class EntryAttackResult:
    weapon_id: int
    weapon_name: str | None
    scenario: str
    pre: dict[str, Any]
    report: dict[str, Any]
    ammo_before: int
    ammo_after: int
    succeeded: bool
    live: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def evaluate_mock_attack(
    bridge: Any,
    *,
    scenario: EntryScenario | str,
    weapon_id: int,
    hook_sequence: Iterable[tuple[int, int, int]] | None = None,
) -> EntryAttackResult:
    sc = ENTRY_SCENARIO_BY_NAME[scenario] if isinstance(scenario, str) else scenario
    weapon = equipped_weapon_name(weapon_id)
    hooks = list(hook_sequence or mock_hook_sequence_for_scenario(sc, weapon_id))
    hook_iter = iter(hooks)

    def read_ram(fields):
        names = {f[0] for f in fields}
        if "equipped_weapon_id" in names:
            return {"equipped_weapon_id": int(weapon_id)}
        if "player_hp" in names:
            return {"player_hp": 96}
        if any(n.startswith("inv_slot_") for n in names):
            qty = default_ammo_for_weapon(weapon_id)
            return {
                n: ((qty << 8) | weapon_id) if n == "inv_slot_0" else 0
                for n in names
            }
        if "inv0" in names:
            qty = default_ammo_for_weapon(weapon_id)
            return {"inv0": (qty << 8) | weapon_id}
        try:
            a, x, r = next(hook_iter)
        except StopIteration:
            a, x, r = (0, 0, 0)
        out: dict[str, Any] = {}
        if "player_anim" in names:
            out["player_anim"] = a
        if "player_action_aux" in names:
            out["player_action_aux"] = x
        if "player_recovery_timer" in names:
            out["player_recovery_timer"] = r
        return out

    bridge.read_ram.side_effect = read_ram
    bridge.step.return_value = (0, False)
    # MagicMock truthiness would fake active attack_pins / frame_ring and
    # double-consume hook reads inside _step_one_frame.
    bridge.attack_pins = None
    bridge.frame_ring = None
    pre = {"hooks": format_hooks(*sc.entry_hook), "scenario": sc.name}
    ammo_before = default_ammo_for_weapon(weapon_id)
    empty = {k: False for k in ("up", "down", "left", "right", "square", "cross", "r1")}
    died, frames, report = execute_attack_macro(
        bridge, empty_sticky=empty, prev_hp=96, episode_start_hp=96,
    )
    ammo_after = ammo_before - int(report.get("ammo_spent", 0))
    ok = (not died) and attack_succeeded(
        weapon_id, report, ammo_before=ammo_before, ammo_after=ammo_after,
    )
    return EntryAttackResult(
        weapon_id=int(weapon_id),
        weapon_name=weapon,
        scenario=sc.name,
        pre=pre,
        report=dict(report),
        ammo_before=ammo_before,
        ammo_after=ammo_after,
        succeeded=ok,
        live=False,
    )


def evaluate_live_attack(
    env: Any,
    *,
    scenario: EntryScenario | str,
    weapon_id: int,
    attack_action: int,
    action_index: dict[str, int],
) -> EntryAttackResult:
    sc = ENTRY_SCENARIO_BY_NAME[scenario] if isinstance(scenario, str) else scenario
    weapon = equipped_weapon_name(weapon_id)
    ram_equip_weapon(env.bridge, weapon_id)
    env._sticky_input.reset()
    run_setup(env, sc.setup, action_index)
    pre = read_entry_snapshot(env.bridge)
    pre["scenario"] = sc.name
    ammo_before = slot0_ammo(env.bridge, weapon_id)
    _, _rew, _term, _trunc, info = env.step(attack_action)
    report = dict(info.get("attack_report") or {})
    if not report and int(weapon_id) == KNIFE_WEAPON_ID:
        report = dict(getattr(env.bridge, "last_knife_anim_report", None) or {})
        report.setdefault("weapon", "knife")
        report.setdefault("macro_path", "knife_neutral")
    ammo_after = slot0_ammo(env.bridge, weapon_id)
    ok = attack_succeeded(
        weapon_id, report, ammo_before=ammo_before, ammo_after=ammo_after,
    )
    return EntryAttackResult(
        weapon_id=int(weapon_id),
        weapon_name=weapon,
        scenario=sc.name,
        pre=pre,
        report=report,
        ammo_before=ammo_before,
        ammo_after=ammo_after,
        succeeded=ok,
        live=True,
        extra={"reward": float(info.get("reward", 0.0))},
    )


def all_equippable_weapon_ids() -> tuple[int, ...]:
    return EQUIPPABLE_WEAPON_IDS
