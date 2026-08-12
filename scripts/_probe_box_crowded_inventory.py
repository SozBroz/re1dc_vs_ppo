"""Live box UI regression: crowded-inventory withdraw/deposit + cursor tracking.

Loads cp40 storeroom, patches RAM for reproducible layouts, opens the box UI,
and runs a battery of withdraw/deposit scenarios using env-style cursor tracking
(only advance cursors after ok=True).

Usage:
    python scripts/_probe_box_crowded_inventory.py
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import EMUHAWK, LUA, assert_rom_present  # noqa: E402
from re1_rl.item_box import (  # noqa: E402
    KNIFE_ITEM_ID,
    _encode_slot,
    box_pollution_reason,
    can_deposit,
    can_withdraw,
    read_box,
    read_box_live,
    read_inventory,
)
from re1_rl.item_box_ui_macro import (  # noqa: E402
    POST_OPEN_SETTLE_FRAMES,
    close_box_ui,
    execute_box_deposit_ui,
    execute_box_withdraw_ui,
    first_empty_inventory_slot,
    first_reachable_empty_inventory_slot,
    probe_box_ui_open,
    _wait,
)
from re1_rl.memory_map import (  # noqa: E402
    INVENTORY_BASE,
    ITEM_BOX_BASE,
    ITEM_IDS,
    PLAYER_HP,
)

STATE_CP40 = ROOT / "states" / "yawn_rails" / "cells" / "cp40" / "cell.State"
STATE_QS0 = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State" / (
    "Resident Evil - Director's Cut (USA).Nymashock.QuickSave0.State"
)
STATE = STATE_QS0 if STATE_QS0.is_file() else STATE_CP40
PORT = 5649

BERETTA = 0x02
CLIP = 0x0B
GREEN_HERB = 0x44
SHOTGUN = 0x03
SHIELD_KEY = 0x35


def _name(iid: int) -> str:
    return ITEM_IDS.get(int(iid), f"0x{int(iid):02x}")


def _fmt_inv(inv: list[tuple[int, int]]) -> str:
    return ", ".join(
        f"{i}:{_name(iid)}x{q}" for i, (iid, q) in enumerate(inv) if iid
    ) or "(empty)"


def _fmt_box(box: list[tuple[int, int]], *, n: int = 8) -> str:
    return ", ".join(
        f"{i}:{_name(iid)}x{q}" for i, (iid, q) in enumerate(box[:n]) if iid
    ) or "(empty)"


@dataclass
class CursorTracker:
    """Mirror env: only advance cursors after successful transfers."""

    inv: int = 0
    box: int = 0

    def apply(self, report: dict[str, Any]) -> None:
        if not report.get("ok"):
            return
        if report.get("inv_cursor") is not None:
            self.inv = int(report["inv_cursor"])
        if report.get("box_cursor") is not None:
            self.box = int(report["box_cursor"])


@dataclass
class Case:
    name: str
    setup: Callable[[BizHawkClient], None]
    run: Callable[[BizHawkClient, int, CursorTracker], None]


@dataclass
class Results:
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _write_slots(
    c: BizHawkClient,
    *,
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
) -> None:
    fields: list[tuple[str, int, str, int]] = []
    for i in range(48):
        fields.append((f"bx{i}", ITEM_BOX_BASE + i * 2, "u16", 0))
    for i in range(8):
        fields.append((f"iv{i}", INVENTORY_BASE + i * 2, "u16", 0))
    for i, (iid, qty) in enumerate(box):
        fields.append((f"b{i}", ITEM_BOX_BASE + i * 2, "u16", _encode_slot(iid, qty)))
    for i, (iid, qty) in enumerate(inventory):
        fields.append(
            (f"i{i}", INVENTORY_BASE + i * 2, "u16", _encode_slot(iid, qty))
        )
    c.write_ram(fields)
    c.frameadvance(4)


def _open_box(c: BizHawkClient, *, hp: int) -> None:
    if probe_box_ui_open(c):
        return
    for _ in range(5):
        c.step(buttons={"cross": True}, n=12, abort_on_zero_hp=False)
        _wait(c, frames=POST_OPEN_SETTLE_FRAMES, prev_hp=hp, episode_start_hp=hp)
        if probe_box_ui_open(c):
            return
        c.step(buttons={}, n=20, abort_on_zero_hp=False)
    if not probe_box_ui_open(c):
        raise RuntimeError("box UI did not open")


def _prepare_box_session(c: BizHawkClient, *, hp: int) -> None:
    """Close any open box UI, then interact to open fresh (cursor 0,0)."""
    if probe_box_ui_open(c):
        close_box_ui(c, prev_hp=hp, episode_start_hp=hp)
        _wait(c, frames=30, prev_hp=hp, episode_start_hp=hp)
    _open_box(c, hp=hp)


def _setup_layout(
    c: BizHawkClient,
    *,
    hp: int,
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
) -> None:
    _write_slots(c, inventory=inventory, box=box)
    _prepare_box_session(c, hp=hp)


def _assert_clean_box(box_live: list[tuple[int, int]], *, label: str) -> None:
    reason = box_pollution_reason(box_live)
    if reason:
        raise AssertionError(f"{label}: box pollution {reason!r}")


def _assert_no_beretta(box_live: list[tuple[int, int]], *, label: str) -> None:
    slots = [i for i, (iid, _) in enumerate(box_live) if iid == BERETTA]
    if slots:
        raise AssertionError(f"{label}: beretta in box slots {slots}")


def _withdraw(
    c: BizHawkClient,
    *,
    hp: int,
    cur: CursorTracker,
    box_slot: int,
    expect_ok: bool = True,
) -> dict[str, Any]:
    _d, _f, report = execute_box_withdraw_ui(
        c,
        box_slot,
        prev_hp=hp,
        episode_start_hp=hp,
        inv_cursor=cur.inv,
        box_cursor=cur.box,
    )
    if expect_ok and not report.get("ok"):
        raise AssertionError(f"withdraw box[{box_slot}] failed: {report}")
    if not expect_ok and report.get("ok"):
        raise AssertionError(f"withdraw box[{box_slot}] unexpectedly ok: {report}")
    cur.apply(report)
    return report


def _deposit(
    c: BizHawkClient,
    *,
    hp: int,
    cur: CursorTracker,
    inv_slot: int,
    expect_ok: bool = True,
) -> dict[str, Any]:
    inv = read_inventory(c)
    box = read_box(c)
    ok, reason = can_deposit(inv, box, inv_slot)
    if expect_ok and not ok:
        raise AssertionError(f"can_deposit slot {inv_slot} blocked: {reason}")
    _d, _f, report = execute_box_deposit_ui(
        c,
        inv_slot,
        prev_hp=hp,
        episode_start_hp=hp,
        inv_cursor=cur.inv,
        box_cursor=cur.box,
    )
    if expect_ok and not report.get("ok"):
        raise AssertionError(f"deposit inv[{inv_slot}] failed: {report}")
    if not expect_ok and report.get("ok"):
        raise AssertionError(f"deposit inv[{inv_slot}] unexpectedly ok: {report}")
    cur.apply(report)
    return report


# --- Layout presets ---------------------------------------------------------

def _cp40_two_empty_slots() -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """6/8 inv slots used — room for two box withdraws."""
    inv = [
        (KNIFE_ITEM_ID, 0),
        (BERETTA, 14),
        (CLIP, 16),
        (SHIELD_KEY, 1),
        (GREEN_HERB, 1),
        (SHOTGUN, 4),
        (0, 0),
        (0, 0),
    ]
    box = [(CLIP, 15), (CLIP, 15)] + [(0, 0)] * 46
    return inv, box


def _cp40_crowded() -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """7/8 inv slots used — empty at even slot 6 (box-UI reachable).

    Left/Right only move from empty cells. An empty at slot 7 with slot 6
    occupied is unreachable from slot 0 — use slot 6 for positive cases.
    """
    inv = [
        (KNIFE_ITEM_ID, 0),
        (BERETTA, 14),
        (CLIP, 16),
        (SHIELD_KEY, 1),
        (GREEN_HERB, 1),
        (SHOTGUN, 4),
        (0, 0),
        (GREEN_HERB, 1),
    ]
    box = [(CLIP, 15), (CLIP, 15)] + [(0, 0)] * 46
    return inv, box


def _unreachable_odd_empty() -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Only empty is odd-col slot 7 with slot 6 occupied — withdraw must fail."""
    inv = [
        (KNIFE_ITEM_ID, 0),
        (BERETTA, 14),
        (CLIP, 16),
        (SHIELD_KEY, 1),
        (GREEN_HERB, 1),
        (SHOTGUN, 4),
        (GREEN_HERB, 1),
        (0, 0),
    ]
    box = [(CLIP, 15), (CLIP, 15)] + [(0, 0)] * 46
    return inv, box


def _full_inventory() -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    inv = [
        (KNIFE_ITEM_ID, 0),
        (BERETTA, 14),
        (CLIP, 16),
        (SHIELD_KEY, 1),
        (GREEN_HERB, 1),
        (SHOTGUN, 4),
        (GREEN_HERB, 1),
        (0x41, 1),  # first_aid_spray_alt fills slot 7
    ]
    box = [(CLIP, 15), (CLIP, 15), (CLIP, 15)] + [(0, 0)] * 45
    return inv, box


def _sparse_box_crowded_inv() -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    inv, _ = _cp40_crowded()
    box = [(CLIP, 15)] + [(0, 0)] * 47
    return inv, box


# --- Test cases -------------------------------------------------------------


def _assert_keys_on_person(inv: list[tuple[int, int]], *, label: str) -> None:
    if not any(iid == SHIELD_KEY for iid, _ in inv):
        raise AssertionError(f"{label}: shield_key missing from inventory")


def _assert_no_keys_in_box(box_live: list[tuple[int, int]], *, label: str) -> None:
    slots = [i for i, (iid, _) in enumerate(box_live) if iid == SHIELD_KEY]
    if slots:
        raise AssertionError(f"{label}: shield_key in box slots {slots}")


def _case_cp40_withdraw_knife_herb(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    inv, box = _cp40_crowded()
    _setup_layout(c, hp=hp, inventory=inv, box=box)
    empty = first_empty_inventory_slot(read_inventory(c))
    assert empty == 6, f"expected empty inv slot 6, got {empty}"

    r0 = _withdraw(c, hp=hp, cur=cur, box_slot=0)
    assert r0["moved"] == (CLIP, 15)
    assert cur.inv == 6, f"cursor should rest on inv slot 6, got {cur.inv}"
    _assert_keys_on_person(read_inventory(c), label="after_withdraw")

    # Deposit even-col herb before knife so packing does not trap us without
    # a reachable empty bridge for odd-col targets.
    inv_now = read_inventory(c)
    herb_slot = next(i for i, (iid, _) in enumerate(inv_now) if iid == GREEN_HERB and i % 2 == 0)
    r1 = _deposit(c, hp=hp, cur=cur, inv_slot=herb_slot)
    assert r1["moved"][0] == GREEN_HERB
    _assert_keys_on_person(read_inventory(c), label="after_herb")

    knife_slot = next(i for i, (iid, _) in enumerate(read_inventory(c)) if iid == KNIFE_ITEM_ID)
    r2 = _deposit(c, hp=hp, cur=cur, inv_slot=knife_slot)
    assert r2["moved"][0] == KNIFE_ITEM_ID

    box_live = read_box_live(c)
    _assert_clean_box(box_live, label="cp40_session")
    _assert_no_beretta(box_live, label="cp40_session")
    _assert_no_keys_in_box(box_live, label="cp40_session")
    _assert_keys_on_person(read_inventory(c), label="cp40_session")
    assert any(iid == KNIFE_ITEM_ID for iid, _ in box_live[:16])
    assert sum(1 for iid, _ in box_live[:16] if iid == GREEN_HERB) >= 1
    assert not any(iid == KNIFE_ITEM_ID for iid, _ in read_inventory(c))


def _case_double_withdraw(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    """Two empties in different columns: deposit between withdraws (box-UI rule)."""
    inv, box = _cp40_two_empty_slots()
    _setup_layout(c, hp=hp, inventory=inv, box=box)
    assert first_empty_inventory_slot(read_inventory(c)) == 6

    _withdraw(c, hp=hp, cur=cur, box_slot=0)
    inv_after_1 = read_inventory(c)
    assert any(iid == CLIP and q > 0 for iid, q in inv_after_1)
    # Slot 6 filled; remaining empty at 7 is odd-col — unreachable until we
    # free a bridge. Bank knife (even col) so the next withdraw can proceed.
    ks = next(i for i, (iid, _) in enumerate(inv_after_1) if iid == KNIFE_ITEM_ID)
    _deposit(c, hp=hp, cur=cur, inv_slot=ks)
    _assert_keys_on_person(read_inventory(c), label="double_mid")

    _withdraw(c, hp=hp, cur=cur, box_slot=1)
    inv_after_2 = read_inventory(c)
    clip_count = sum(q for iid, q in inv_after_2 if iid == CLIP)
    assert clip_count >= 30, f"expected ~30 bullets on person, got {clip_count}"

    box16 = read_box(c)
    assert box16[1][0] == 0, "box slot 1 should be empty after second withdraw"
    _assert_clean_box(read_box_live(c), label="double_withdraw")
    _assert_no_keys_in_box(read_box_live(c), label="double_withdraw")


def _case_withdraw_deposit_rewithdraw(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    inv, box = _sparse_box_crowded_inv()
    _setup_layout(c, hp=hp, inventory=inv, box=box)

    _withdraw(c, hp=hp, cur=cur, box_slot=0)
    hs = next(
        i
        for i, (iid, _) in enumerate(read_inventory(c))
        if iid == GREEN_HERB and i % 2 == 0
    )
    _deposit(c, hp=hp, cur=cur, inv_slot=hs)
    knife_slot = next(i for i, (iid, _) in enumerate(read_inventory(c)) if iid == KNIFE_ITEM_ID)
    _deposit(c, hp=hp, cur=cur, inv_slot=knife_slot)
    _assert_keys_on_person(read_inventory(c), label="rewithdraw_mid")

    # Box has knife + reopen ammo via RAM for second withdraw
    c.write_ram([("b1", ITEM_BOX_BASE + 2, "u16", _encode_slot(CLIP, 15))])
    c.frameadvance(2)

    _withdraw(c, hp=hp, cur=cur, box_slot=1)
    inv_final = read_inventory(c)
    assert sum(q for iid, q in inv_final if iid == CLIP) >= 15
    _assert_clean_box(read_box_live(c), label="rewithdraw")
    _assert_no_beretta(read_box_live(c), label="rewithdraw")
    _assert_keys_on_person(inv_final, label="rewithdraw")
    _assert_no_keys_in_box(read_box_live(c), label="rewithdraw")


def _case_single_withdraw_on_full_inv(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    """7/8 slots used: only one withdraw fits; second is correctly blocked."""
    inv, box = _cp40_crowded()
    _setup_layout(c, hp=hp, inventory=inv, box=box)

    _withdraw(c, hp=hp, cur=cur, box_slot=0)
    report = _withdraw(c, hp=hp, cur=cur, box_slot=1, expect_ok=False)
    assert report.get("reason") == "inventory_full"
    _assert_clean_box(read_box_live(c), label="single_withdraw_full")
    _assert_keys_on_person(read_inventory(c), label="single_withdraw_full")


def _case_full_inventory_withdraw_blocked(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    inv, box = _full_inventory()
    _setup_layout(c, hp=hp, inventory=inv, box=box)
    assert first_empty_inventory_slot(read_inventory(c)) is None

    inv_before = list(read_inventory(c))
    box_before = list(read_box(c))
    report = _withdraw(c, hp=hp, cur=cur, box_slot=0, expect_ok=False)
    assert report.get("reason") == "inventory_full"
    assert cur.inv == 0 and cur.box == 0, "cursors must not advance on failure"
    assert read_inventory(c) == inv_before
    assert read_box(c) == box_before


def _case_unreachable_odd_empty_withdraw(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    """Empty only at slot 7 with 6 occupied — cannot Left/Right onto it."""
    inv, box = _unreachable_odd_empty()
    _setup_layout(c, hp=hp, inventory=inv, box=box)
    assert first_empty_inventory_slot(read_inventory(c)) == 7

    inv_before = list(read_inventory(c))
    report = _withdraw(c, hp=hp, cur=cur, box_slot=0, expect_ok=False)
    assert report.get("reason") == "empty_slot_unreachable", report
    assert cur.inv == 0 and cur.box == 0
    assert read_inventory(c) == inv_before
    _assert_keys_on_person(read_inventory(c), label="unreachable_odd")
    _assert_no_keys_in_box(read_box_live(c), label="unreachable_odd")


def _case_deposit_both_herbs(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    inv, box = _cp40_crowded()
    _setup_layout(c, hp=hp, inventory=inv, box=box)

    # Bank both herbs while an empty bridge still exists (before filling inv).
    _withdraw(c, hp=hp, cur=cur, box_slot=0)
    hs_even = next(
        i
        for i, (iid, _) in enumerate(read_inventory(c))
        if iid == GREEN_HERB and i % 2 == 0
    )
    _deposit(c, hp=hp, cur=cur, inv_slot=hs_even)
    # After even deposit, park should leave a reachable empty for odd herb.
    inv_now = read_inventory(c)
    hs_odd = next(i for i, (iid, _) in enumerate(inv_now) if iid == GREEN_HERB)
    _deposit(c, hp=hp, cur=cur, inv_slot=hs_odd)
    ks = next(i for i, (iid, _) in enumerate(read_inventory(c)) if iid == KNIFE_ITEM_ID)
    _deposit(c, hp=hp, cur=cur, inv_slot=ks)
    _assert_keys_on_person(read_inventory(c), label="both_herbs_mid")
    _withdraw(c, hp=hp, cur=cur, box_slot=1)

    box_live = read_box_live(c)
    herb_in_box = sum(1 for iid, _ in box_live[:16] if iid == GREEN_HERB)
    assert herb_in_box >= 2, f"expected 2 herbs in box, got {herb_in_box}"
    assert not any(iid == GREEN_HERB for iid, _ in read_inventory(c))
    _assert_clean_box(box_live, label="both_herbs")
    _assert_no_beretta(box_live, label="both_herbs")
    _assert_no_keys_in_box(box_live, label="both_herbs")
    _assert_keys_on_person(read_inventory(c), label="both_herbs")


def _case_triple_session(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    """Multi-transfer open-box session without close; keys never bank."""
    inv, box = _cp40_crowded()
    box[2] = (CLIP, 15)
    _setup_layout(c, hp=hp, inventory=inv, box=box)

    _withdraw(c, hp=hp, cur=cur, box_slot=0)
    hs = next(
        i
        for i, (iid, _) in enumerate(read_inventory(c))
        if iid == GREEN_HERB and i % 2 == 0
    )
    _deposit(c, hp=hp, cur=cur, inv_slot=hs)
    _assert_keys_on_person(read_inventory(c), label="triple_mid1")
    # Second herb while empty bridge still available.
    hs2 = next(i for i, (iid, _) in enumerate(read_inventory(c)) if iid == GREEN_HERB)
    _deposit(c, hp=hp, cur=cur, inv_slot=hs2)
    ks = next(i for i, (iid, _) in enumerate(read_inventory(c)) if iid == KNIFE_ITEM_ID)
    _deposit(c, hp=hp, cur=cur, inv_slot=ks)
    _withdraw(c, hp=hp, cur=cur, box_slot=1)
    _withdraw(c, hp=hp, cur=cur, box_slot=2)

    live = read_box_live(c)
    _assert_clean_box(live, label="triple_session")
    _assert_no_beretta(live, label="triple_session")
    _assert_no_keys_in_box(live, label="triple_session")
    _assert_keys_on_person(read_inventory(c), label="triple_session")
    assert any(iid == KNIFE_ITEM_ID for iid, _ in live[:16])
    assert sum(1 for iid, _ in live[:16] if iid == GREEN_HERB) >= 2


def _case_extended_session_no_close(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    """Long open-box session: many withdraw/deposit cycles; keys never bank."""
    inv = [
        (KNIFE_ITEM_ID, 0),
        (BERETTA, 14),
        (CLIP, 16),
        (SHIELD_KEY, 1),
        (GREEN_HERB, 1),
        (SHOTGUN, 4),
        (0, 0),
        (GREEN_HERB, 1),
    ]
    box = [
        (CLIP, 15),
        (CLIP, 15),
        (GREEN_HERB, 1),
        (CLIP, 15),
    ] + [(0, 0)] * 44
    _setup_layout(c, hp=hp, inventory=inv, box=box)

    transfers = 0
    # Wave 1: withdraw + bank even herb + knife
    _withdraw(c, hp=hp, cur=cur, box_slot=0)
    transfers += 1
    _assert_keys_on_person(read_inventory(c), label="ext1")
    hs = next(
        i
        for i, (iid, _) in enumerate(read_inventory(c))
        if iid == GREEN_HERB and i % 2 == 0
    )
    _deposit(c, hp=hp, cur=cur, inv_slot=hs)
    transfers += 1
    hs2 = next(i for i, (iid, _) in enumerate(read_inventory(c)) if iid == GREEN_HERB)
    _deposit(c, hp=hp, cur=cur, inv_slot=hs2)
    transfers += 1
    ks = next(i for i, (iid, _) in enumerate(read_inventory(c)) if iid == KNIFE_ITEM_ID)
    _deposit(c, hp=hp, cur=cur, inv_slot=ks)
    transfers += 1
    _assert_keys_on_person(read_inventory(c), label="ext2")
    _assert_no_keys_in_box(read_box_live(c), label="ext2")

    # Wave 2: more withdraws into freed slots, bank box herb
    _withdraw(c, hp=hp, cur=cur, box_slot=1)
    transfers += 1
    _withdraw(c, hp=hp, cur=cur, box_slot=2)
    transfers += 1
    inv_now = read_inventory(c)
    assert sum(1 for iid, _ in inv_now if iid == GREEN_HERB) >= 1
    hs = next(i for i, (iid, _) in enumerate(inv_now) if iid == GREEN_HERB)
    _deposit(c, hp=hp, cur=cur, inv_slot=hs)
    transfers += 1
    _withdraw(c, hp=hp, cur=cur, box_slot=3)
    transfers += 1

    assert transfers >= 7, transfers
    shield_slot = next(i for i, (iid, _) in enumerate(read_inventory(c)) if iid == SHIELD_KEY)
    ok, reason = can_deposit(read_inventory(c), read_box(c), shield_slot)
    assert not ok and reason == "key_item", (ok, reason)
    _d, _f, blocked = execute_box_deposit_ui(
        c,
        shield_slot,
        prev_hp=hp,
        episode_start_hp=hp,
        inv_cursor=cur.inv,
        box_cursor=cur.box,
    )
    assert not blocked.get("ok")
    assert blocked.get("reason") == "key_item"
    _assert_keys_on_person(read_inventory(c), label="ext_final")
    live = read_box_live(c)
    _assert_no_keys_in_box(live, label="ext_final")
    _assert_clean_box(live, label="ext_final")
    _assert_no_beretta(live, label="ext_final")


def _case_stale_cursor_rejected(c: BizHawkClient, hp: int, cur: CursorTracker) -> None:
    inv, box = _cp40_crowded()
    _setup_layout(c, hp=hp, inventory=inv, box=box)

    _withdraw(c, hp=hp, cur=cur, box_slot=0)
    assert cur.inv == 6
    saved_inv, saved_box = cur.inv, cur.box

    knife_slot = next(i for i, (iid, _) in enumerate(read_inventory(c)) if iid == KNIFE_ITEM_ID)
    # Deliberately stale inv cursor (0 instead of 6) while UI rests on filled dest.
    _d, _f, bad = execute_box_deposit_ui(
        c,
        knife_slot,
        prev_hp=hp,
        episode_start_hp=hp,
        inv_cursor=0,
        box_cursor=cur.box,
    )
    assert not bad.get("ok"), bad
    reason = str(bad.get("reason", ""))
    assert (
        bad.get("exchange_detected")
        or bad.get("ram_changed")
        or "disallowed" in reason
        or reason
        in (
            "transfer_no_effect",
            "wrong_item_deposited",
            "key_item_deposited",
            "inv_slot_drift",
        )
        or reason.startswith("inv_slot_unreachable")
        or reason.startswith("key_item_in_box")
    ), bad
    cur.inv, cur.box = saved_inv, saved_box  # env would not apply failed report
    _assert_keys_on_person(read_inventory(c), label="stale_after_bad")
    _assert_no_keys_in_box(read_box_live(c), label="stale_after_bad")

    # Recovery: correct cursors should still work
    _deposit(c, hp=hp, cur=cur, inv_slot=knife_slot)
    _assert_no_beretta(read_box_live(c), label="after_stale_recovery")
    _assert_keys_on_person(read_inventory(c), label="after_stale_recovery")


CASES: list[Case] = [
    Case("cp40_withdraw_knife_herb", lambda c: None, _case_cp40_withdraw_knife_herb),
    Case("double_withdraw_two_empty_slots", lambda c: None, _case_double_withdraw),
    Case("single_withdraw_then_blocked", lambda c: None, _case_single_withdraw_on_full_inv),
    Case("unreachable_odd_empty_withdraw", lambda c: None, _case_unreachable_odd_empty_withdraw),
    Case("full_inventory_withdraw_blocked", lambda c: None, _case_full_inventory_withdraw_blocked),
    Case("withdraw_deposit_rewithdraw", lambda c: None, _case_withdraw_deposit_rewithdraw),
    Case("deposit_both_herbs", lambda c: None, _case_deposit_both_herbs),
    Case("stale_cursor_rejected", lambda c: None, _case_stale_cursor_rejected),
    Case("triple_session_cp41_style", lambda c: None, _case_triple_session),
    Case("extended_session_keys_stay", lambda c: None, _case_extended_session_no_close),
]


class Emulator:
    def __init__(self) -> None:
        self.rom = assert_rom_present()
        self.client = BizHawkClient(port=PORT, timeout=240.0, connect_timeout=90.0)
        self.proc: subprocess.Popen[bytes] | None = None
        self.hp = 0

    def start(self) -> None:
        self.client.start_server()
        self.proc = subprocess.Popen(
            [
                str(EMUHAWK),
                str(self.rom),
                f"--lua={LUA}",
                "--socket_ip=127.0.0.1",
                f"--socket_port={PORT}",
            ],
            cwd=str(EMUHAWK.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.client.wait_for_client()
        self.client.set_speed(0)

    def reload_box(self) -> None:
        if not STATE.is_file():
            raise FileNotFoundError(STATE)
        self.client.load_savestate(str(STATE.resolve()))
        self.client.frameadvance(4)
        self.hp = int(self.client.read_ram([("hp", PLAYER_HP, "u16")])["hp"])

    def stop(self) -> None:
        try:
            self.client.quit()
        except Exception:
            pass
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass


def main() -> int:
    if not STATE.is_file():
        print(f"ERROR: missing save state (tried {STATE})")
        return 1
    print(f"Using savestate: {STATE.name}")

    results = Results()
    emu = Emulator()
    try:
        emu.start()
        for case in CASES:
            print(f"\n{'=' * 60}\nCASE: {case.name}\n{'=' * 60}")
            try:
                emu.reload_box()
                cur = CursorTracker()
                case.setup(emu.client)
                case.run(emu.client, emu.hp, cur)
                inv = read_inventory(emu.client)
                box = read_box(emu.client)
                print(f"  PASS — inv: {_fmt_inv(inv)}")
                print(f"         box: {_fmt_box(box)}")
                print(f"         cursors: inv={cur.inv} box={cur.box}")
                results.passed.append(case.name)
            except Exception as exc:
                print(f"  FAIL — {exc}")
                try:
                    inv = read_inventory(emu.client)
                    box_live = read_box_live(emu.client)
                    print(f"         inv: {_fmt_inv(inv)}")
                    print(f"         box: {_fmt_box(box_live)}")
                    pol = box_pollution_reason(box_live)
                    if pol:
                        print(f"         pollution: {pol}")
                except Exception:
                    pass
                results.failed.append((case.name, str(exc)))
    finally:
        emu.stop()

    print(f"\n{'=' * 60}\nSUMMARY: {len(results.passed)} passed, {len(results.failed)} failed")
    for name in results.passed:
        print(f"  OK  {name}")
    for name, err in results.failed:
        print(f"  FAIL {name}: {err}")

    return 0 if not results.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
