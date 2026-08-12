"""Live box-UI QA harness (plan docs/box_ui_macro_qa.plan.md §5).

L1 C1–C16 cursor physics, L2 D1–D10 transfers, L3 G0/G1 golden path.
Does not use MAGIC_BOX_RAM_WRITES / apply_deposit / apply_withdraw / write_ram.
Call execute_box_deposit_ui / execute_box_withdraw_ui directly (no PPO env).

  python scripts/_probe_box_ui_exhaustive.py --port 5691 --cases all --g0-repeats 1
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import re1_rl.item_box_ui_macro as box_macro  # noqa: E402
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import (  # noqa: E402
    EMUHAWK,
    assert_rom_present,
    emuhawk_argv,
    newest_quicksave,
)
from re1_rl.inventory_menu_macro import slot_nav_moves  # noqa: E402
from re1_rl.item_box import (  # noqa: E402
    BOX_SLOTS,
    BOX_SLOTS_LIVE,
    INVENTORY_SLOTS,
    is_deposit_allowed_item,
    is_key_item_id,
    read_box,
    read_box_live,
    read_inventory,
)
from re1_rl.item_box_ui_macro import (  # noqa: E402
    POST_OPEN_SETTLE_FRAMES,
    _confirm_cross,
    _home_inventory,
    _move,
    _wait,
    close_box_ui,
    execute_box_deposit_ui,
    execute_box_withdraw_ui,
    first_empty_inventory_slot,
    probe_box_ui_open,
)
from re1_rl.memory_map import (  # noqa: E402
    GAME_MODE,
    GAME_STATE,
    ITEM_IDS,
    PLAYER_HP,
    ROOM_ID,
    STAGE_ID,
)
from re1_rl.yawn_box_prep_checkpoint import (  # noqa: E402
    WIND_CREST_ITEM_ID,
    yawn_box_forbidden_weapon_ammo_ids,
    yawn_box_prep_capture_ready,
    yawn_box_weapon_ammo_clear,
)

DEFAULT_PORT = 5691
OUT_DIR = ROOT / "data" / "box_ui_qa"
ROOM_118 = "118"
KNIFE_ID = 0x01
BERETTA_ID = 0x02
SHOTGUN_SHELLS_ID = 0x0C
SHIELD_KEY_ID = 0x35
BAZOOKA_IDS = frozenset({0x07, 0x08, 0x09, 0x0A})

L1_IDS = [f"C{i}" for i in range(1, 17)]
L2_IDS = ["D1", "D2", "D3", "D4", "D5", "D5b", "D6", "D7", "D8", "D9", "D10"]
L3_IDS = ["G0", "G1"]
ALL_IDS = L1_IDS + L2_IDS + L3_IDS

_HARNESS: "Harness | None" = None
_ORIG_TAP = box_macro._tap


# ---------------------------------------------------------------------------
# Identity helper (pure; no emulator)
# ---------------------------------------------------------------------------


def _norm_slots(slots: Sequence[tuple[int, int] | list[int]] | None) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for entry in slots or ():
        out.append((int(entry[0]), int(entry[1])))
    return out


def _first_empty(slots: Sequence[tuple[int, int]], *, limit: int | None = None) -> int | None:
    cap = len(slots) if limit is None else min(len(slots), int(limit))
    for i in range(cap):
        if int(slots[i][0]) == 0:
            return i
    return None


def _changed(before: Sequence[tuple[int, int]], after: Sequence[tuple[int, int]]) -> list[int]:
    n = max(len(before), len(after))
    hits: list[int] = []
    for i in range(n):
        b = before[i] if i < len(before) else (0, 0)
        a = after[i] if i < len(after) else (0, 0)
        if (int(b[0]), int(b[1])) != (int(a[0]), int(a[1])):
            hits.append(i)
    return hits


def _slot(slots: Sequence[tuple[int, int]], i: int) -> tuple[int, int]:
    if i < 0 or i >= len(slots):
        return (0, 0)
    return (int(slots[i][0]), int(slots[i][1]))


def assert_one_item_transfer(
    inv_before: Sequence[tuple[int, int] | list[int]],
    inv_after: Sequence[tuple[int, int] | list[int]],
    box_before: Sequence[tuple[int, int] | list[int]],
    box_after: Sequence[tuple[int, int] | list[int]],
    source: int,
    dest: int,
    direction: str,
) -> str:
    """Return '' if RAM shows one item into the first empty hole; else a reason.

    Reasons: ``swap``, ``extra_delta``, ``dest_not_first_empty``.
    ``direction`` is ``deposit`` (inv source → box dest) or ``withdraw``.
    """
    inv_b = _norm_slots(inv_before)
    inv_a = _norm_slots(inv_after)
    box_b = _norm_slots(box_before)
    box_a = _norm_slots(box_after)
    src = int(source)
    dst = int(dest)
    dep = str(direction).strip().lower() == "deposit"

    if dep:
        first = _first_empty(box_b, limit=BOX_SLOTS)
        if first is None or dst != int(first):
            return "dest_not_first_empty"
        if _slot(box_b, dst)[0] != 0:
            return "swap"
        src_side_b, src_side_a = inv_b, inv_a
        dst_side_b, dst_side_a = box_b, box_a
    else:
        first = _first_empty(inv_b, limit=INVENTORY_SLOTS)
        if first is None or dst != int(first):
            return "dest_not_first_empty"
        if _slot(inv_b, dst)[0] != 0:
            return "swap"
        src_side_b, src_side_a = box_b, box_a
        dst_side_b, dst_side_a = inv_b, inv_a

    src_changed = _changed(src_side_b, src_side_a)
    dst_changed = _changed(dst_side_b, dst_side_a)
    if any(i != src for i in src_changed):
        # Occupied dest plus another slot is an exchange, not a hole fill.
        if dst in dst_changed and _slot(dst_side_b, dst)[0] != 0:
            return "swap"
        return "extra_delta"
    if any(i != dst for i in dst_changed):
        return "extra_delta"
    if src not in src_changed or dst not in dst_changed:
        return "extra_delta"

    moved = _slot(src_side_b, src)
    if moved[0] == 0:
        return "extra_delta"
    if _slot(src_side_a, src)[0] not in (0, moved[0]):
        return "swap"
    got = _slot(dst_side_a, dst)
    if got[0] != moved[0]:
        return "swap"
    if _slot(src_side_a, src)[0] != 0 and _slot(src_side_b, src) == _slot(src_side_a, src):
        return "extra_delta"
    return ""


def assert_ram_unchanged(
    inv_before: Sequence[tuple[int, int] | list[int]],
    inv_after: Sequence[tuple[int, int] | list[int]],
    box_before: Sequence[tuple[int, int] | list[int]],
    box_after: Sequence[tuple[int, int] | list[int]],
) -> str:
    """Return '' if inv + box bytes match; else ``extra_delta``."""
    if _changed(_norm_slots(inv_before), _norm_slots(inv_after)):
        return "extra_delta"
    if _changed(_norm_slots(box_before), _norm_slots(box_after)):
        return "extra_delta"
    return ""


def _identity_self_check() -> None:
    inv_b = [
        (0x02, 1),
        (0x0B, 30),
        (0x35, 1),
        (0x03, 1),
        (0x11, 1),
        (0x34, 1),
        (0x0C, 1),
        (0x29, 1),
    ]
    box_b = [(0x01, 1), (0x07, 1)] + [(0, 0)] * 14
    inv_ok = inv_b[:-1] + [(0, 0)]
    box_ok = [(0x01, 1), (0x07, 1), (0x29, 1)] + [(0, 0)] * 13
    assert assert_one_item_transfer(inv_b, inv_ok, box_b, box_ok, 7, 2, "deposit") == ""

    box_wrong = [(0x01, 1), (0x07, 1), (0, 0), (0x29, 1)] + [(0, 0)] * 12
    assert (
        assert_one_item_transfer(inv_b, inv_ok, box_b, box_wrong, 7, 3, "deposit")
        == "dest_not_first_empty"
    )

    inv_extra = [(0, 0)] + inv_b[1:-1] + [(0, 0)]
    assert (
        assert_one_item_transfer(inv_b, inv_extra, box_b, box_ok, 7, 2, "deposit")
        == "extra_delta"
    )

    # shield_key @2 ↔ crest @2 while slot 7 also emptied
    inv_swap = [
        (0x02, 1),
        (0x0B, 30),
        (0x29, 1),
        (0x03, 1),
        (0x11, 1),
        (0x34, 1),
        (0x0C, 1),
        (0, 0),
    ]
    box_swap = [(0x01, 1), (0x07, 1), (0x35, 1)] + [(0, 0)] * 13
    assert assert_one_item_transfer(inv_b, inv_swap, box_b, box_swap, 7, 2, "deposit") in (
        "swap",
        "extra_delta",
    )

    inv_w = inv_ok
    box_w = box_ok
    inv_w_after = inv_ok[:-1] + [(0x07, 1)]
    box_w_after = [(0x01, 1), (0, 0), (0x29, 1)] + [(0, 0)] * 13
    assert (
        assert_one_item_transfer(inv_w, inv_w_after, box_w, box_w_after, 1, 7, "withdraw")
        == ""
    )
    assert assert_ram_unchanged(inv_b, inv_b, box_b, box_b) == ""
    assert assert_ram_unchanged(inv_b, inv_ok, box_b, box_b) == "extra_delta"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _fmt(slots: Sequence[tuple[int, int]], *, limit: int | None = None) -> str:
    cap = len(slots) if limit is None else min(len(slots), int(limit))
    parts: list[str] = []
    for i in range(cap):
        iid, qty = int(slots[i][0]), int(slots[i][1])
        if not iid:
            continue
        parts.append(f"{i}:{ITEM_IDS.get(iid, hex(iid))}x{qty}")
    return "[" + ", ".join(parts) + "]"


def _pairs(slots: Sequence[tuple[int, int]], *, limit: int | None = None) -> list[list[int]]:
    cap = len(slots) if limit is None else min(len(slots), int(limit))
    return [[int(slots[i][0]), int(slots[i][1])] for i in range(cap)]


def _live_delta(
    before: Sequence[tuple[int, int]],
    after: Sequence[tuple[int, int]],
) -> list[dict[str, Any]]:
    n = max(len(before), len(after))
    out: list[dict[str, Any]] = []
    for i in range(n):
        b = before[i] if i < len(before) else (0, 0)
        a = after[i] if i < len(after) else (0, 0)
        if (int(b[0]), int(b[1])) != (int(a[0]), int(a[1])):
            out.append(
                {
                    "slot": i,
                    "before": [int(b[0]), int(b[1])],
                    "after": [int(a[0]), int(a[1])],
                }
            )
    return out


def _find_id(slots: Sequence[tuple[int, int]], item_id: int, *, limit: int | None = None) -> int | None:
    cap = len(slots) if limit is None else min(len(slots), int(limit))
    want = int(item_id)
    for i in range(cap):
        if int(slots[i][0]) == want:
            return i
    return None


def _find_any(
    slots: Sequence[tuple[int, int]],
    ids: frozenset[int] | set[int],
    *,
    limit: int | None = None,
) -> int | None:
    cap = len(slots) if limit is None else min(len(slots), int(limit))
    for i in range(cap):
        if int(slots[i][0]) in ids:
            return i
    return None


def _inv_names(inv: Sequence[tuple[int, int]]) -> list[str]:
    names: list[str] = []
    for iid, _q in inv:
        if int(iid):
            names.append(ITEM_IDS.get(int(iid), "") or "")
    return names


def _new_keys_in_box(
    before: Sequence[tuple[int, int]],
    after: Sequence[tuple[int, int]],
) -> list[str]:
    """Key items that appeared in the box (ignore pre-existing)."""
    before_ids = {int(s[0]) for s in before if int(s[0]) and is_key_item_id(int(s[0]))}
    hits: list[str] = []
    for i, (iid, _q) in enumerate(after):
        iid = int(iid)
        if not iid or iid in before_ids or not is_key_item_id(iid):
            continue
        hits.append(f"{ITEM_IDS.get(iid, hex(iid))}@{i}")
    return hits


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class Harness:
    def __init__(
        self,
        client: BizHawkClient,
        qs: Path,
        out: Path,
        jsonl_fp: Any,
        *,
        skip_screens: bool,
    ) -> None:
        self.client = client
        self.qs = qs
        self.out = out
        self.jsonl_fp = jsonl_fp
        self.skip_screens = skip_screens
        self.case_id = ""
        self.step = 0
        self.assumed_inv = 0
        self.assumed_box = 0
        self.pane = "inv"
        self.hp = 0
        self.room_id = ROOM_118
        self.inv_cursor = 0
        self.box_cursor = 0
        self.cross_count = 0
        self.last_shot = ""
        self.rows: list[dict[str, Any]] = []

    def begin(self, case_id: str, *, reset_cursors: bool = True) -> None:
        self.case_id = case_id
        self.step = 0
        self.cross_count = 0
        self.last_shot = ""
        if reset_cursors:
            self.assumed_inv = 0
            self.assumed_box = 0
            self.pane = "inv"
            self.inv_cursor = 0
            self.box_cursor = 0
        (self.out / case_id).mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        ram = self.client.read_ram(
            [
                ("gm", GAME_MODE, "u8"),
                ("gs", GAME_STATE, "u32"),
                ("hp", PLAYER_HP, "u16"),
            ]
        )
        inv = read_inventory(self.client)
        box16 = read_box(self.client)
        live = read_box_live(self.client)
        return {
            "inv": inv,
            "box_16": box16,
            "box_live": live,
            "gm": int(ram["gm"]),
            "gs": int(ram["gs"]),
            "hp": int(ram["hp"]),
            "open": probe_box_ui_open(self.client),
        }

    def shot(self, tag: str) -> str:
        self.step += 1
        rel = f"{self.case_id}/{self.step:02d}_{tag}.png"
        path = self.out / rel
        if not self.skip_screens:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.client._request({"cmd": "screenshot", "path": str(path)})
        self.last_shot = str(path) if not self.skip_screens else ""
        return self.last_shot

    def log_status(self, tag: str) -> None:
        snap = self.snapshot()
        print(
            f"{self.case_id} {tag}: mode=0x{snap['gm']:02X} gs=0x{snap['gs']:08X} "
            f"open={snap['open']} assumed_inv={self.assumed_inv} "
            f"assumed_box={self.assumed_box} pane={self.pane} "
            f"inv={_fmt(snap['inv'])} box={_fmt(snap['box_16'])}",
            flush=True,
        )

    def record(
        self,
        *,
        ok: bool,
        reason: str,
        snap: dict[str, Any] | None = None,
        before_live: Sequence[tuple[int, int]] | None = None,
        skipped: bool = False,
    ) -> dict[str, Any]:
        snap = snap or self.snapshot()
        live = snap["box_live"]
        delta = _live_delta(before_live, live) if before_live is not None else []
        row = {
            "id": self.case_id,
            "ok": True if skipped else bool(ok),
            "skipped": bool(skipped),
            "assumed_inv": self.assumed_inv,
            "assumed_box": self.assumed_box,
            "inv": _pairs(snap["inv"]),
            "box_16": _pairs(snap["box_16"], limit=BOX_SLOTS),
            "box_live_delta": delta,
            "reason": reason,
            "screenshot": self.last_shot,
        }
        self.jsonl_fp.write(json.dumps(row) + "\n")
        self.jsonl_fp.flush()
        self.rows.append(row)
        flag = "SKIP" if skipped else ("PASS" if ok else "FAIL")
        print(f"RESULT {self.case_id} {flag} {reason}", flush=True)
        return row

    def read_room_hp(self) -> tuple[int, str]:
        ram = self.client.read_ram(
            [
                ("hp", PLAYER_HP, "u16"),
                ("stage", STAGE_ID, "u8"),
                ("room", ROOM_ID, "u8"),
            ]
        )
        self.hp = int(ram["hp"])
        self.room_id = f"{int(ram['stage']) + 1}{int(ram['room']):02X}"
        return self.hp, self.room_id

    def open_box(self) -> bool:
        self.client.step(buttons={"cross": True}, n=12, abort_on_zero_hp=False)
        self.client.step(buttons={}, n=50, abort_on_zero_hp=False)
        if probe_box_ui_open(self.client):
            _wait(
                self.client,
                frames=POST_OPEN_SETTLE_FRAMES,
                prev_hp=self.hp,
                episode_start_hp=self.hp,
            )
            return True
        for _ in range(4):
            self.client.step(buttons={"cross": True}, n=8, abort_on_zero_hp=False)
            self.client.step(buttons={}, n=40, abort_on_zero_hp=False)
            if probe_box_ui_open(self.client):
                _wait(
                    self.client,
                    frames=POST_OPEN_SETTLE_FRAMES,
                    prev_hp=self.hp,
                    episode_start_hp=self.hp,
                )
                return True
        return False

    def reload_open(self) -> None:
        self.client.load_savestate(str(self.qs.resolve()))
        self.client.frameadvance(8)
        self.read_room_hp()
        self.inv_cursor = 0
        self.box_cursor = 0
        self.assumed_inv = 0
        self.assumed_box = 0
        self.pane = "inv"
        self.cross_count = 0
        if not self.open_box():
            raise RuntimeError("box UI did not open")
        self.shot("open_home")
        self.log_status("open_home")

    def dpad(self, direction: str, taps: int = 1) -> None:
        for _ in range(max(0, int(taps))):
            _move(
                self.client,
                direction,
                prev_hp=self.hp,
                episode_start_hp=self.hp,
                taps=1,
            )
            self._assume_dpad(direction)
            if _HARNESS is not self:
                self.shot(direction)

    def _assume_dpad(self, direction: str) -> None:
        if self.pane == "inv":
            slot = int(self.assumed_inv)
            row, col = divmod(slot, 2)
            if direction == "down":
                row = min(3, row + 1)
            elif direction == "up":
                row = max(0, row - 1)
            elif direction == "right":
                if col == 0:
                    col = 1
                else:
                    self.pane = "box"
                    return
            elif direction == "left":
                if col == 1:
                    col = 0
                else:
                    self.pane = "box"
                    return
            self.assumed_inv = row * 2 + col
            return
        slot = int(self.assumed_box)
        if direction == "down":
            self.assumed_box = min(BOX_SLOTS_LIVE - 1, slot + 1)
        elif direction == "up":
            self.assumed_box = (slot - 1) % BOX_SLOTS_LIVE

    def cross(self) -> None:
        _confirm_cross(self.client, prev_hp=self.hp, episode_start_hp=self.hp)
        if self.pane == "inv":
            self.pane = "box"
        if _HARNESS is not self:
            self.shot("cross")

    def nav_inv(self, to_slot: int) -> None:
        src = int(self.assumed_inv) if self.pane == "inv" else 0
        if self.pane != "inv":
            raise RuntimeError("nav_inv while pane is box list")
        for direction in slot_nav_moves(src, int(to_slot)):
            self.dpad(direction, taps=1)

    def close(self) -> dict[str, Any]:
        _died, _f, report = close_box_ui(
            self.client,
            prev_hp=self.hp,
            episode_start_hp=self.hp,
            inv_cursor=int(self.inv_cursor),
        )
        self.inv_cursor = 0
        self.box_cursor = 0
        self.assumed_inv = 0
        self.assumed_box = 0
        self.pane = "inv"
        return report


def _hook_tap(
    client: Any,
    buttons: dict[str, bool],
    *,
    frames: int,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    died, used = _ORIG_TAP(
        client,
        buttons,
        frames=frames,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    h = _HARNESS
    if h is None or not buttons:
        return died, used
    keys = [k for k, v in buttons.items() if v]
    if not keys:
        return died, used
    if "cross" in keys:
        h.cross_count += 1
    h.shot("+".join(keys))
    return died, used


# ---------------------------------------------------------------------------
# Transfer wrappers
# ---------------------------------------------------------------------------


def _run_deposit(h: Harness, inv_slot: int, **kwargs: Any) -> tuple[dict[str, Any], str]:
    before = h.snapshot()
    crosses0 = h.cross_count
    died, _frames, report = execute_box_deposit_ui(
        h.client,
        int(inv_slot),
        prev_hp=h.hp,
        episode_start_hp=h.hp,
        inv_cursor=h.inv_cursor,
        box_cursor=h.box_cursor,
        room_id=h.room_id,
        **kwargs,
    )
    after = h.snapshot()
    report = dict(report)
    report["died"] = bool(died)
    report["crosses"] = h.cross_count - crosses0
    ident = ""
    if report.get("ok"):
        dest = int(report.get("dest_slot", report.get("expected_dest", -1)))
        ident = assert_one_item_transfer(
            before["inv"],
            after["inv"],
            before["box_live"],
            after["box_live"],
            int(inv_slot),
            dest,
            "deposit",
        )
        if ident:
            report["ok"] = False
            report["reason"] = ident
        else:
            h.inv_cursor = int(report.get("inv_cursor", h.inv_cursor))
            h.box_cursor = int(report.get("box_cursor", h.box_cursor))
            h.assumed_inv = h.inv_cursor
            h.assumed_box = h.box_cursor
            h.pane = "inv"
    report["identity"] = ident
    report["_before"] = before
    report["_after"] = after
    h.shot("after_deposit")
    h.log_status("after_deposit")
    return report, ident


def _run_withdraw(h: Harness, box_slot: int) -> tuple[dict[str, Any], str]:
    before = h.snapshot()
    crosses0 = h.cross_count
    died, _frames, report = execute_box_withdraw_ui(
        h.client,
        int(box_slot),
        prev_hp=h.hp,
        episode_start_hp=h.hp,
        inv_cursor=h.inv_cursor,
        box_cursor=h.box_cursor,
        room_id=h.room_id,
    )
    after = h.snapshot()
    report = dict(report)
    report["died"] = bool(died)
    report["crosses"] = h.cross_count - crosses0
    ident = ""
    if report.get("ok"):
        dest = int(report.get("dest_slot", -1))
        ident = assert_one_item_transfer(
            before["inv"],
            after["inv"],
            before["box_live"],
            after["box_live"],
            int(box_slot),
            dest,
            "withdraw",
        )
        if ident:
            report["ok"] = False
            report["reason"] = ident
        else:
            h.inv_cursor = int(report.get("inv_cursor", h.inv_cursor))
            h.box_cursor = int(report.get("box_cursor", h.box_cursor))
            h.assumed_inv = h.inv_cursor
            h.assumed_box = h.box_cursor
            h.pane = "inv"
    report["identity"] = ident
    report["_before"] = before
    report["_after"] = after
    h.shot("after_withdraw")
    h.log_status("after_withdraw")
    return report, ident


def _pollution_delta_reason(
    before_live: Sequence[tuple[int, int]],
    after_live: Sequence[tuple[int, int]],
) -> str:
    keys = _new_keys_in_box(before_live, after_live)
    if keys:
        return "key_item_in_box:" + ",".join(keys)
    deep = [d for d in _live_delta(before_live, after_live) if int(d["slot"]) >= BOX_SLOTS]
    if deep:
        return f"deep_box_write:{deep}"
    return ""


def _crest_slot(inv: Sequence[tuple[int, int]]) -> int | None:
    return _find_id(inv, WIND_CREST_ITEM_ID, limit=INVENTORY_SLOTS)


def _bazooka_slot(box: Sequence[tuple[int, int]]) -> int | None:
    return _find_any(box, BAZOOKA_IDS, limit=BOX_SLOTS)


def _deposit_crest_or_reason(h: Harness) -> tuple[dict[str, Any] | None, str]:
    inv = read_inventory(h.client)
    slot = _crest_slot(inv)
    if slot is None:
        return None, "crest_not_in_inventory"
    report, ident = _run_deposit(h, slot)
    if not report.get("ok"):
        return report, report.get("reason") or ident or "deposit_failed"
    return report, ""


# ---------------------------------------------------------------------------
# L1 — cursor physics
# ---------------------------------------------------------------------------


def case_c1(h: Harness) -> None:
    """Open-box home cell. Human scores red highlight vs assumed slot 0."""
    h.reload_open()
    h.assumed_inv = 0
    h.record(
        ok=True,
        reason="human_score:open_home assumed_inv=0",
        before_live=h.snapshot()["box_live"],
    )


def case_c2(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    h.dpad("up", taps=1)
    h.shot("up_from_0")
    h.log_status("up_from_0")
    h.reload_open()
    h.nav_inv(6)
    h.dpad("down", taps=1)
    h.shot("down_from_6")
    h.log_status("down_from_6")
    h.reload_open()
    h.nav_inv(7)
    h.dpad("down", taps=1)
    h.shot("down_from_7")
    h.log_status("down_from_7")
    after = h.snapshot()
    ram = assert_ram_unchanged(
        before["inv"], after["inv"], before["box_live"], after["box_live"]
    )
    # Reloads reset RAM; last segment only. Visual wrap is human-scored.
    h.record(
        ok=True,
        reason="human_score:vertical_wrap assumed_inv=" + str(h.assumed_inv),
        before_live=after["box_live"],
    )
    _ = ram


def case_c3(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    h.dpad("right", taps=1)
    h.shot("right_from_occ_0")
    h.log_status("right_from_occ_0")
    after = h.snapshot()
    ram = assert_ram_unchanged(
        before["inv"], after["inv"], before["box_live"], after["box_live"]
    )
    reason = "human_score:right_from_occupied_0 assumed_inv=" + str(h.assumed_inv)
    if h.pane != "inv":
        reason += " PANE_SWITCH"
    if ram:
        h.record(ok=False, reason=ram, snap=after, before_live=before["box_live"])
        return
    h.record(ok=True, reason=reason, snap=after, before_live=before["box_live"])


def case_c4(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    h.nav_inv(6)
    h.dpad("right", taps=1)
    h.shot("right_from_occ_6")
    h.log_status("right_from_occ_6")
    occ_pane = h.pane
    occ_assumed = h.assumed_inv
    # Cursor is on slot 7 after Right from 6. Reload before the crest
    # deposit or execute_box_deposit_ui navigates from a guessed 0.
    h.reload_open()
    setup, err = _deposit_crest_or_reason(h)
    if err:
        h.record(
            ok=False,
            reason=f"hole_setup_failed:{err} occ6_pane={occ_pane} assumed={occ_assumed}",
            before_live=before["box_live"],
        )
        return
    inv = read_inventory(h.client)
    hole = first_empty_inventory_slot(inv)
    if hole is None:
        h.record(ok=False, reason="no_hole_after_deposit", before_live=before["box_live"])
        return
    # After deposit the red cursor is unknown; home then nav to the hole.
    _home_inventory(h.client, prev_hp=h.hp, episode_start_hp=h.hp)
    h.pane = "inv"
    h.assumed_inv = 0
    h.nav_inv(int(hole))
    h.dpad("right", taps=1)
    h.shot("right_from_empty_hole")
    h.log_status("right_from_empty_hole")
    h.record(
        ok=True,
        reason=(
            f"human_score:right_occ6_pane={occ_pane} assumed={occ_assumed} "
            f"vs empty_slot={hole} pane={h.pane} assumed_inv={h.assumed_inv} "
            f"(QS1 hole is slot {hole}, not 6)"
        ),
        before_live=before["box_live"],
    )
    _ = setup


def case_c5(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    h.dpad("right", taps=1)
    h.dpad("left", taps=1)
    h.shot("left_from_1")
    h.log_status("left_from_1")
    after = h.snapshot()
    ram = assert_ram_unchanged(
        before["inv"], after["inv"], before["box_live"], after["box_live"]
    )
    if ram:
        h.record(ok=False, reason=ram, snap=after, before_live=before["box_live"])
        return
    h.record(
        ok=True,
        reason=f"human_score:left_from_1 assumed_inv={h.assumed_inv} pane={h.pane}",
        snap=after,
        before_live=before["box_live"],
    )


def case_c6(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    h.dpad("left", taps=1)
    h.shot("left_from_0")
    h.log_status("left_from_0")
    left_pane = h.pane
    h.reload_open()
    h.dpad("right", taps=1)
    h.dpad("right", taps=1)
    h.shot("right_from_1")
    h.log_status("right_from_1")
    h.record(
        ok=True,
        reason=(
            f"human_score:left0_pane={left_pane} right1_pane={h.pane} "
            f"(macro must never emit these)"
        ),
        before_live=before["box_live"],
    )


def case_c7(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    for direction in slot_nav_moves(0, 7):
        h.dpad(direction, taps=1)
    h.shot("path_0_to_7")
    h.log_status("path_0_to_7")
    after = h.snapshot()
    ram = assert_ram_unchanged(
        before["inv"], after["inv"], before["box_live"], after["box_live"]
    )
    crest = _crest_slot(after["inv"])
    reason = f"human_score:path_0_to_7 assumed_inv={h.assumed_inv} crest_slot={crest}"
    if ram:
        h.record(ok=False, reason=ram, snap=after, before_live=before["box_live"])
        return
    h.record(ok=True, reason=reason, snap=after, before_live=before["box_live"])


def case_c8(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    for direction in slot_nav_moves(0, 7):
        h.dpad(direction, taps=1)
    for direction in slot_nav_moves(7, 0):
        h.dpad(direction, taps=1)
    h.shot("path_7_to_0")
    h.log_status("path_7_to_0")
    after = h.snapshot()
    ram = assert_ram_unchanged(
        before["inv"], after["inv"], before["box_live"], after["box_live"]
    )
    if ram:
        h.record(ok=False, reason=ram, snap=after, before_live=before["box_live"])
        return
    h.record(
        ok=True,
        reason=f"human_score:path_7_to_0 assumed_inv={h.assumed_inv} pane={h.pane}",
        snap=after,
        before_live=before["box_live"],
    )


def case_c9(h: Harness) -> None:
    notes: list[str] = []
    for start in range(INVENTORY_SLOTS):
        h.reload_open()
        if start:
            h.nav_inv(start)
        h.shot(f"before_home_from_{start}")
        _home_inventory(h.client, prev_hp=h.hp, episode_start_hp=h.hp)
        h.shot(f"after_home_from_{start}")
        h.log_status(f"home_from_{start}")
        notes.append(f"{start}->assumed_unknown")
    h.record(
        ok=True,
        reason="human_score:home_inventory from 0..7 (odd col may miss slot 0) "
        + ",".join(notes),
    )


def case_c10(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    h.cross()
    h.shot("cross_occupied_inv")
    h.log_status("cross_occupied_inv")
    after = h.snapshot()
    ram = assert_ram_unchanged(
        before["inv"], after["inv"], before["box_live"], after["box_live"]
    )
    if ram:
        h.record(ok=False, reason=ram, snap=after, before_live=before["box_live"])
        return
    h.record(
        ok=True,
        reason=f"human_score:cross_occ_inv pane={h.pane} assumed_box={h.assumed_box}",
        snap=after,
        before_live=before["box_live"],
    )


def case_c11(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    _rep, err = _deposit_crest_or_reason(h)
    if err:
        h.record(ok=False, reason=f"hole_setup_failed:{err}", before_live=before["box_live"])
        return
    inv = read_inventory(h.client)
    hole = first_empty_inventory_slot(inv)
    if hole is None:
        h.record(ok=False, reason="no_hole_after_deposit", before_live=before["box_live"])
        return
    _home_inventory(h.client, prev_hp=h.hp, episode_start_hp=h.hp)
    h.pane = "inv"
    h.assumed_inv = 0
    h.nav_inv(int(hole))
    pre = h.snapshot()
    h.cross()
    h.shot("cross_empty_inv")
    h.log_status("cross_empty_inv")
    after = h.snapshot()
    ram = assert_ram_unchanged(
        pre["inv"], after["inv"], pre["box_live"], after["box_live"]
    )
    if ram:
        h.record(ok=False, reason=ram, snap=after, before_live=pre["box_live"])
        return
    h.record(
        ok=True,
        reason=f"human_score:cross_empty_slot={hole} pane={h.pane} assumed_box={h.assumed_box}",
        snap=after,
        before_live=pre["box_live"],
    )


def case_c12(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    h.cross()
    h.pane = "box"
    h.assumed_box = 0
    h.dpad("down", taps=1)
    h.shot("box_down_from_0")
    h.log_status("box_down_from_0")
    down_assumed = h.assumed_box
    h.reload_open()
    h.cross()
    h.pane = "box"
    h.assumed_box = 0
    h.dpad("up", taps=1)
    h.shot("box_up_from_0")
    h.log_status("box_up_from_0")
    h.record(
        ok=True,
        reason=(
            f"human_score:box_down_from_0 assumed={down_assumed} "
            f"box_up_from_0 assumed={h.assumed_box} (wrap_mod48={(0 - 1) % BOX_SLOTS_LIVE})"
        ),
        before_live=before["box_live"],
    )


def case_c13(h: Harness) -> None:
    notes: list[str] = []
    for n in (1, 8, 15, 16):
        h.reload_open()
        h.cross()
        h.pane = "box"
        h.assumed_box = 0
        h.dpad("up", taps=n)
        h.shot(f"up_x{n}_from_box0")
        h.log_status(f"up_x{n}")
        wrap = (0 - n) % BOX_SLOTS_LIVE
        notes.append(f"N={n} assumed_wrap={wrap} assumed_tracked={h.assumed_box}")
    h.record(
        ok=True,
        reason="human_score:box_up_xN " + "; ".join(notes) + " (15->33 if wrap)",
    )


def case_c14(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    dest = _first_empty(before["box_16"], limit=BOX_SLOTS)
    if dest is None:
        h.record(ok=False, reason="box_full", snap=before, before_live=before["box_live"])
        return
    occupied = [
        i
        for i in range(BOX_SLOTS)
        if i != int(dest) and _slot(before["box_16"], i)[0] != 0
    ]
    report, ident = _deposit_crest_or_reason(h)
    after = h.snapshot()
    if ident or (report and not report.get("ok")):
        h.record(
            ok=False,
            reason=(report or {}).get("reason") or ident or "deposit_failed",
            snap=after,
            before_live=before["box_live"],
        )
        return
    if _slot(after["box_16"], int(dest))[0] != WIND_CREST_ITEM_ID:
        h.record(
            ok=False,
            reason=f"crest_not_in_first_empty dest={dest}",
            snap=after,
            before_live=before["box_live"],
        )
        return
    for i in occupied:
        if _slot(after["box_16"], i) != _slot(before["box_16"], i):
            h.record(
                ok=False,
                reason=f"swap:box{i}_changed dest={dest}",
                snap=after,
                before_live=before["box_live"],
            )
            return
    h.record(
        ok=True,
        reason=f"dest={dest} first_empty occupied_untouched={occupied}",
        snap=after,
        before_live=before["box_live"],
    )


def case_c15(h: Harness) -> None:
    """Occupied inv × occupied box Cross must exchange. That is never a legal transfer."""
    h.reload_open()
    before = h.snapshot()
    if _slot(before["inv"], 0)[0] == 0:
        h.record(ok=False, reason="fixture_inv0_empty", snap=before, before_live=before["box_live"])
        return
    occ_box = next(
        (i for i in range(BOX_SLOTS) if _slot(before["box_16"], i)[0] != 0),
        None,
    )
    if occ_box is None:
        h.record(ok=False, reason="fixture_no_occupied_box_slot", snap=before, before_live=before["box_live"])
        return
    h.cross()
    h.pane = "box"
    h.assumed_box = 0
    if int(occ_box) > 0:
        h.dpad("down", taps=int(occ_box))
    h.cross()
    h.shot("exchange_cross")
    after = h.snapshot()
    delta = _live_delta(before["box_live"], after["box_live"])
    inv_delta = _changed(before["inv"], after["inv"])
    swapped = bool(delta) and bool(inv_delta)
    if not swapped:
        h.record(
            ok=False,
            reason="expected_game_exchange_not_observed",
            snap=after,
            before_live=before["box_live"],
        )
        return
    h.record(
        ok=True,
        reason=f"game_exchanges occupied_inv_x_occupied_box inv_delta={inv_delta} box_delta={delta}",
        snap=after,
        before_live=before["box_live"],
    )


def case_c16(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    report, err = _deposit_crest_or_reason(h)
    after = h.snapshot()
    if err:
        h.record(ok=False, reason=err, snap=after, before_live=before["box_live"])
        return
    cursor = (report or {}).get("cursor_out")
    h.shot("after_ok_deposit")
    h.record(
        ok=True,
        reason=f"human_score:post_deposit_red_cursor cursor_out={cursor}",
        snap=after,
        before_live=before["box_live"],
    )


# ---------------------------------------------------------------------------
# L2 — single transfers
# ---------------------------------------------------------------------------


def _refuse_deposit(h: Harness, inv_slot: int, *, expect_sub: str) -> None:
    h.reload_open()
    before = h.snapshot()
    crosses0 = h.cross_count
    report, _ident = _run_deposit(h, inv_slot)
    after = report["_after"]
    ram = assert_ram_unchanged(
        before["inv"], after["inv"], before["box_live"], after["box_live"]
    )
    crosses = h.cross_count - crosses0
    reason = str(report.get("reason") or "")
    if report.get("ok"):
        h.record(ok=False, reason=f"illegal_deposit_succeeded:{reason}", snap=after, before_live=before["box_live"])
        return
    if crosses:
        h.record(
            ok=False,
            reason=f"crossed_before_refuse:{reason} crosses={crosses}",
            snap=after,
            before_live=before["box_live"],
        )
        return
    if ram:
        h.record(ok=False, reason=f"ram_changed:{ram} macro={reason}", snap=after, before_live=before["box_live"])
        return
    if expect_sub not in reason and reason not in ("key_item", "not_allowlisted"):
        h.record(
            ok=False,
            reason=f"unexpected_refuse_reason:{reason} expected_contains={expect_sub}",
            snap=after,
            before_live=before["box_live"],
        )
        return
    h.record(ok=True, reason=f"refused_before_cross:{reason}", snap=after, before_live=before["box_live"])


def case_d1(h: Harness) -> dict[str, Any] | None:
    h.reload_open()
    before = h.snapshot()
    slot = _crest_slot(before["inv"])
    if slot is None:
        h.record(ok=False, reason="crest_not_in_inventory", snap=before, before_live=before["box_live"])
        return None
    dest = _first_empty(before["box_16"], limit=BOX_SLOTS)
    report, ident = _run_deposit(h, slot)
    after = report["_after"]
    poll = _pollution_delta_reason(before["box_live"], after["box_live"])
    if poll and "wind_crest" not in poll:
        h.record(ok=False, reason=poll, snap=after, before_live=before["box_live"])
        return report
    if not report.get("ok"):
        h.record(
            ok=False,
            reason=report.get("reason") or ident or "deposit_failed",
            snap=after,
            before_live=before["box_live"],
        )
        return report
    if report.get("reason") == "exchange_detected" or report.get("exchange_detected"):
        h.record(ok=False, reason="exchange_detected", snap=after, before_live=before["box_live"])
        return report
    if _slot(after["inv"], int(slot))[0] != 0:
        h.record(ok=False, reason="source_not_empty", snap=after, before_live=before["box_live"])
        return report
    if dest is None or _slot(after["box_16"], int(dest))[0] != WIND_CREST_ITEM_ID:
        h.record(ok=False, reason=f"crest_not_in_first_empty dest={dest}", snap=after, before_live=before["box_live"])
        return report
    for iid in (SHIELD_KEY_ID, 0x34):
        if _find_id(after["inv"], iid) is None:
            h.record(ok=False, reason=f"key_left_person:{ITEM_IDS.get(iid)}", snap=after, before_live=before["box_live"])
            return report
    h.record(
        ok=True,
        reason=f"ok dest={dest} cursor_out={report.get('cursor_out')}",
        snap=after,
        before_live=before["box_live"],
    )
    return report


def case_d2(h: Harness) -> None:
    h.reload_open()
    inv = read_inventory(h.client)
    slot = _find_id(inv, SHIELD_KEY_ID)
    if slot is None:
        h.record(ok=False, reason="shield_key_not_in_inventory")
        return
    _refuse_deposit(h, slot, expect_sub="key_item")


def case_d3(h: Harness) -> None:
    h.reload_open()
    inv = read_inventory(h.client)
    slot = _find_id(inv, SHOTGUN_SHELLS_ID)
    if slot is None:
        h.record(ok=False, reason="shells_not_in_inventory")
        return
    _refuse_deposit(h, slot, expect_sub="not_allowlisted")


def case_d4(h: Harness) -> None:
    h.reload_open()
    inv = read_inventory(h.client)
    slot = _find_id(inv, BERETTA_ID)
    if slot is None:
        h.record(ok=False, reason="beretta_not_in_inventory")
        return
    _refuse_deposit(h, slot, expect_sub="not_allowlisted")


def _setup_crest_deposited(h: Harness, *, reuse_open: bool) -> str:
    """Deposit crest; reload+open unless ``reuse_open`` (session after D1)."""
    if reuse_open:
        inv = read_inventory(h.client)
        box = read_box(h.client)
        if _crest_slot(inv) is None and _find_id(box, WIND_CREST_ITEM_ID) is not None:
            return ""
    else:
        h.reload_open()
    _rep, err = _deposit_crest_or_reason(h)
    return err


def case_d5(h: Harness, *, reuse_open: bool = False) -> None:
    err = _setup_crest_deposited(h, reuse_open=reuse_open)
    if err:
        h.record(ok=False, reason=f"d1_setup_failed:{err}")
        return
    before = h.snapshot()
    slot = _bazooka_slot(before["box_16"])
    if slot is None:
        h.record(ok=False, reason="bazooka_not_in_box", snap=before, before_live=before["box_live"])
        return
    dest = first_empty_inventory_slot(before["inv"])
    report, ident = _run_withdraw(h, slot)
    after = report["_after"]
    if not report.get("ok"):
        h.record(
            ok=False,
            reason=report.get("reason") or ident or "withdraw_failed",
            snap=after,
            before_live=before["box_live"],
        )
        return
    if dest is None or _slot(after["inv"], int(dest))[0] not in BAZOOKA_IDS:
        h.record(ok=False, reason=f"bazooka_not_in_first_empty_inv dest={dest}", snap=after, before_live=before["box_live"])
        return
    if _slot(after["box_16"], int(slot))[0] != 0:
        h.record(ok=False, reason="box_source_not_empty", snap=after, before_live=before["box_live"])
        return
    if _find_id(after["box_live"], WIND_CREST_ITEM_ID) is None:
        h.record(ok=False, reason="crest_left_box", snap=after, before_live=before["box_live"])
        return
    poll = _pollution_delta_reason(before["box_live"], after["box_live"])
    if poll:
        h.record(ok=False, reason=poll, snap=after, before_live=before["box_live"])
        return
    h.record(
        ok=True,
        reason=f"ok dest={dest} cursor_out={report.get('cursor_out')}",
        snap=after,
        before_live=before["box_live"],
    )


def case_d5b(h: Harness) -> None:
    err = _setup_crest_deposited(h, reuse_open=False)
    if err:
        h.record(ok=False, reason=f"d1_setup_failed:{err}")
        return
    before = h.snapshot()
    slot = _find_id(before["box_16"], KNIFE_ID)
    if slot is None:
        h.record(ok=False, reason="knife_not_in_box", snap=before, before_live=before["box_live"])
        return
    dest = first_empty_inventory_slot(before["inv"])
    report, ident = _run_withdraw(h, slot)
    after = report["_after"]
    if not report.get("ok"):
        h.record(
            ok=False,
            reason=report.get("reason") or ident or "withdraw_failed",
            snap=after,
            before_live=before["box_live"],
        )
        return
    if dest is None or _slot(after["inv"], int(dest))[0] != KNIFE_ID:
        h.record(ok=False, reason=f"knife_not_in_first_empty_inv dest={dest}", snap=after, before_live=before["box_live"])
        return
    h.record(
        ok=True,
        reason=f"ok dest={dest} (legal; CP still needs guns out before leave)",
        snap=after,
        before_live=before["box_live"],
    )


def case_d6(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    if first_empty_inventory_slot(before["inv"]) is not None:
        h.record(
            ok=False,
            reason="fixture_not_full_pack",
            snap=before,
            before_live=before["box_live"],
        )
        return
    slot = _find_id(before["box_16"], KNIFE_ID)
    if slot is None:
        slot = 0
    crosses0 = h.cross_count
    report, _ident = _run_withdraw(h, slot)
    after = report["_after"]
    ram = assert_ram_unchanged(
        before["inv"], after["inv"], before["box_live"], after["box_live"]
    )
    crosses = h.cross_count - crosses0
    if report.get("ok"):
        h.record(ok=False, reason="withdraw_on_full_pack_succeeded", snap=after, before_live=before["box_live"])
        return
    if crosses:
        h.record(ok=False, reason=f"crossed_on_full_pack crosses={crosses}", snap=after, before_live=before["box_live"])
        return
    if ram:
        h.record(ok=False, reason=f"ram_changed:{ram}", snap=after, before_live=before["box_live"])
        return
    if report.get("reason") != "inventory_full":
        h.record(
            ok=False,
            reason=f"unexpected_reason:{report.get('reason')}",
            snap=after,
            before_live=before["box_live"],
        )
        return
    h.record(ok=True, reason="inventory_full no_cross", snap=after, before_live=before["box_live"])


def case_d7(h: Harness) -> None:
    err = _setup_crest_deposited(h, reuse_open=False)
    if err:
        h.record(ok=False, reason=f"d1_setup_failed:{err}")
        return
    before = h.snapshot()
    if _slot(before["inv"], 2)[0] == 0:
        h.record(ok=False, reason="inv2_not_occupied_cannot_force_dest", snap=before, before_live=before["box_live"])
        return
    slot = _bazooka_slot(before["box_16"])
    if slot is None:
        slot = _find_id(before["box_16"], KNIFE_ID)
    if slot is None:
        h.record(ok=False, reason="no_box_source", snap=before, before_live=before["box_live"])
        return
    orig = box_macro.first_reachable_empty_inventory_slot

    def _force_dest(_inv: list[tuple[int, int]], *, from_slot: int = 0) -> int:
        _ = from_slot
        return 2

    box_macro.first_reachable_empty_inventory_slot = _force_dest  # type: ignore[assignment]
    try:
        crosses0 = h.cross_count
        report, _ident = _run_withdraw(h, slot)
    finally:
        box_macro.first_reachable_empty_inventory_slot = orig
    after = report["_after"]
    ram = assert_ram_unchanged(
        before["inv"], after["inv"], before["box_live"], after["box_live"]
    )
    crosses = h.cross_count - crosses0
    if report.get("ok"):
        h.record(ok=False, reason="forced_occupied_dest_succeeded_swap", snap=after, before_live=before["box_live"])
        return
    if ram:
        h.record(ok=False, reason=f"ram_changed:{ram} reason={report.get('reason')}", snap=after, before_live=before["box_live"])
        return
    if crosses:
        h.record(
            ok=False,
            reason=f"crossed_occupied_dest crosses={crosses} reason={report.get('reason')}",
            snap=after,
            before_live=before["box_live"],
        )
        return
    h.record(
        ok=True,
        reason=f"aborted dest=2 occupied reason={report.get('reason')}",
        snap=after,
        before_live=before["box_live"],
    )


def case_d8(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    dest = _first_empty(before["box_16"], limit=BOX_SLOTS)
    if dest != 0:
        h.record(
            ok=True,
            reason=f"skip:first_empty={dest} (QS1 box is knife+bazooka, not empty)",
            snap=before,
            before_live=before["box_live"],
            skipped=True,
        )
        return
    slot = _crest_slot(before["inv"])
    if slot is None:
        h.record(ok=False, reason="crest_not_in_inventory", snap=before, before_live=before["box_live"])
        return
    report, ident = _run_deposit(h, slot)
    after = report["_after"]
    if not report.get("ok"):
        h.record(ok=False, reason=report.get("reason") or ident, snap=after, before_live=before["box_live"])
        return
    if _slot(after["box_16"], 0)[0] != WIND_CREST_ITEM_ID:
        h.record(ok=False, reason="crest_not_in_box0", snap=after, before_live=before["box_live"])
        return
    h.record(ok=True, reason="landed_box0", snap=after, before_live=before["box_live"])


def case_d9(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    legal = [
        i
        for i, (iid, _q) in enumerate(before["inv"][:INVENTORY_SLOTS])
        if int(iid) and is_deposit_allowed_item(int(iid), h.room_id)
    ]
    if len(legal) < 2:
        h.record(
            ok=True,
            reason=(
                f"skip:qs1_only_{len(legal)}_legal_deposit slots={legal} "
                "(need knife/herb on person for two deposits; knife is in the box)"
            ),
            snap=before,
            before_live=before["box_live"],
            skipped=True,
        )
        return
    dests: list[int] = []
    for slot in legal[:2]:
        snap = h.snapshot()
        expect = _first_empty(snap["box_16"], limit=BOX_SLOTS)
        report, ident = _run_deposit(h, slot)
        after = report["_after"]
        if not report.get("ok"):
            h.record(
                ok=False,
                reason=report.get("reason") or ident,
                snap=after,
                before_live=before["box_live"],
            )
            return
        got = int(report.get("dest_slot", -1))
        if expect is None or got != int(expect):
            h.record(
                ok=False,
                reason=f"dest_not_first_empty_at_moment expect={expect} got={got}",
                snap=after,
                before_live=before["box_live"],
            )
            return
        dests.append(got)
    h.record(
        ok=True,
        reason=f"two_deposits dests={dests} cursors_chained",
        before_live=before["box_live"],
    )


def case_d10(h: Harness) -> None:
    h.reload_open()
    before = h.snapshot()
    d1_report, err = _deposit_crest_or_reason(h)
    if err:
        h.record(ok=False, reason=f"deposit_failed:{err}", before_live=before["box_live"])
        return
    c1 = dict((d1_report or {}).get("cursor_out") or {})
    mid = h.snapshot()
    bslot = _bazooka_slot(mid["box_16"])
    if bslot is None:
        h.record(ok=False, reason="bazooka_not_in_box", snap=mid, before_live=before["box_live"])
        return
    w_report, ident = _run_withdraw(h, bslot)
    if not w_report.get("ok"):
        h.record(
            ok=False,
            reason=w_report.get("reason") or ident,
            snap=w_report["_after"],
            before_live=before["box_live"],
        )
        return
    after_w = w_report["_after"]
    legal = [
        i
        for i, (iid, _q) in enumerate(after_w["inv"][:INVENTORY_SLOTS])
        if int(iid) and is_deposit_allowed_item(int(iid), h.room_id)
    ]
    hole = first_empty_inventory_slot(after_w["inv"])
    if hole is None or not legal:
        h.record(
            ok=True,
            reason=(
                f"session_ok_third_deposit_skipped:inventory_full "
                f"cursor_chain d1={c1} d_withdraw={w_report.get('cursor_out')}"
            ),
            snap=after_w,
            before_live=before["box_live"],
        )
        return
    report, ident = _run_deposit(h, legal[0])
    after = report["_after"]
    if not report.get("ok"):
        h.record(ok=False, reason=report.get("reason") or ident, snap=after, before_live=before["box_live"])
        return
    h.record(
        ok=True,
        reason=f"deposit_withdraw_deposit cursors d1={c1} w={w_report.get('cursor_out')} d2={report.get('cursor_out')}",
        snap=after,
        before_live=before["box_live"],
    )


# ---------------------------------------------------------------------------
# L3 — golden path
# ---------------------------------------------------------------------------


def _withdraw_guns_ammo(h: Harness) -> str:
    forbidden = yawn_box_forbidden_weapon_ammo_ids()
    for _ in range(BOX_SLOTS):
        live = read_box_live(h.client)
        if yawn_box_weapon_ammo_clear(live):
            return ""
        slot = _find_any(live, forbidden, limit=BOX_SLOTS)
        if slot is None:
            return f"gun_ammo_in_unmodeled_box live={_fmt(live, limit=BOX_SLOTS_LIVE)}"
        before = h.snapshot()
        report, ident = _run_withdraw(h, slot)
        after = report["_after"]
        if not report.get("ok"):
            return str(report.get("reason") or ident or "withdraw_failed")
        poll = _pollution_delta_reason(before["box_live"], after["box_live"])
        if poll:
            return poll
        if ident:
            return ident
    live = read_box_live(h.client)
    if not yawn_box_weapon_ammo_clear(live):
        return f"guns_ammo_remain box={_fmt(live)}"
    return ""


def _g0_body(h: Harness, *, reopen_after_deposit: bool) -> tuple[bool, str]:
    h.reload_open()
    before = h.snapshot()
    _rep, err = _deposit_crest_or_reason(h)
    if err:
        return False, f"deposit_failed:{err}"
    after_d = h.snapshot()
    poll = _pollution_delta_reason(before["box_live"], after_d["box_live"])
    if poll and "wind_crest" not in poll:
        return False, poll
    if reopen_after_deposit:
        close_rep = h.close()
        h.shot("closed")
        if not h.open_box():
            return False, f"reopen_failed close={close_rep}"
        h.shot("reopen_home")
        h.inv_cursor = 0
        h.box_cursor = 0
        h.assumed_inv = 0
        h.assumed_box = 0
        h.pane = "inv"
        h.log_status("reopen")
    err = _withdraw_guns_ammo(h)
    if err:
        return False, err
    close_rep = h.close()
    h.shot("closed")
    if probe_box_ui_open(h.client):
        return False, f"box_still_open close={close_rep}"
    inv = read_inventory(h.client)
    live = read_box_live(h.client)
    ready = yawn_box_prep_capture_ready(live, _inv_names(inv))
    if ready:
        return False, f"capture_not_ready:{ready}"
    return True, "yawn_box_prep_capture_ready"


def case_g0(h: Harness, *, repeats: int) -> None:
    n = max(1, int(repeats))
    for i in range(1, n + 1):
        cid = "G0" if n == 1 else f"G0_r{i}"
        h.begin(cid)
        try:
            ok, reason = _g0_body(h, reopen_after_deposit=False)
        except Exception as exc:  # noqa: BLE001 — live harness must record the row
            h.record(ok=False, reason=f"exception:{exc}")
            continue
        h.record(ok=ok, reason=reason)


def case_g1(h: Harness) -> None:
    h.begin("G1")
    try:
        ok, reason = _g0_body(h, reopen_after_deposit=True)
    except Exception as exc:  # noqa: BLE001
        h.record(ok=False, reason=f"exception:{exc}")
        return
    h.record(ok=ok, reason=reason)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


CASES: dict[str, Callable[..., None]] = {
    "C1": case_c1,
    "C2": case_c2,
    "C3": case_c3,
    "C4": case_c4,
    "C5": case_c5,
    "C6": case_c6,
    "C7": case_c7,
    "C8": case_c8,
    "C9": case_c9,
    "C10": case_c10,
    "C11": case_c11,
    "C12": case_c12,
    "C13": case_c13,
    "C14": case_c14,
    "C15": case_c15,
    "C16": case_c16,
    "D1": case_d1,
    "D2": case_d2,
    "D3": case_d3,
    "D4": case_d4,
    "D5": case_d5,
    "D5b": case_d5b,
    "D6": case_d6,
    "D7": case_d7,
    "D8": case_d8,
    "D9": case_d9,
    "D10": case_d10,
    "G0": case_g0,
    "G1": case_g1,
}


def parse_cases(raw: str) -> list[str]:
    text = (raw or "all").strip()
    if not text or text.lower() == "all":
        return list(ALL_IDS)
    out: list[str] = []
    for part in text.split(","):
        p = part.strip()
        if not p:
            continue
        pl = p.lower()
        if pl == "l1":
            out.extend(L1_IDS)
        elif pl == "l2":
            out.extend(L2_IDS)
        elif pl == "l3":
            out.extend(L3_IDS)
        elif pl == "g0":
            out.append("G0")
        elif pl == "g1":
            out.append("G1")
        elif p in CASES:
            out.append(p)
        elif p.upper() in CASES:
            out.append(p.upper())
        elif pl == "d5b":
            out.append("D5b")
        else:
            raise SystemExit(f"unknown --cases token: {p}")
    seen: set[str] = set()
    uniq: list[str] = []
    for cid in out:
        if cid in seen:
            continue
        seen.add(cid)
        uniq.append(cid)
    return uniq


def _print_table(rows: list[dict[str, Any]]) -> None:
    print("\n=== box UI QA ===", flush=True)
    print(f"{'id':<8} {'result':<6} reason", flush=True)
    for row in rows:
        if row.get("skipped"):
            flag = "SKIP"
        else:
            flag = "PASS" if row.get("ok") else "FAIL"
        print(f"{row['id']:<8} {flag:<6} {row.get('reason', '')}", flush=True)


def _kill(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    _identity_self_check()
    parser = argparse.ArgumentParser(description="Box UI exhaustive QA (QS1, no magic RAM writes)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--cases", default="all", help="comma list, or l1/l2/g0/g1/l3/all")
    parser.add_argument("--g0-repeats", type=int, default=1)
    parser.add_argument("--skip-screens", action="store_true")
    args = parser.parse_args()
    wanted = parse_cases(args.cases)

    assert_rom_present()
    qs = newest_quicksave()
    print(f"quicksave={qs}", flush=True)
    print(f"cases={wanted}", flush=True)

    out = OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    jsonl_path = out / "report.jsonl"
    client = BizHawkClient(port=int(args.port), timeout=180.0, connect_timeout=90.0)
    client.start_server()
    proc: subprocess.Popen[Any] | None = None
    global _HARNESS
    try:
        proc = subprocess.Popen(
            emuhawk_argv(port=int(args.port)),
            cwd=str(EMUHAWK.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        client.wait_for_client()
        client.set_speed(100)
        box_macro._tap = _hook_tap
        with jsonl_path.open("w", encoding="utf-8") as fp:
            h = Harness(client, qs, out, fp, skip_screens=bool(args.skip_screens))
            _HARNESS = h
            skip_d5 = False
            for cid in wanted:
                if cid == "D5" and skip_d5:
                    continue
                if cid == "D1" and "D5" in wanted:
                    h.begin("D1")
                    d1 = case_d1(h)
                    # Keep D1 cursor_out; begin() would zero them and the
                    # next withdraw would Cross from a guessed 0 (shield_key
                    # exchange). Same-session D5 must chain the live cursors.
                    h.begin("D5", reset_cursors=False)
                    if not d1 or not d1.get("ok"):
                        h.record(ok=False, reason="d1_failed_no_session_withdraw")
                    else:
                        case_d5(h, reuse_open=True)
                    skip_d5 = True
                    continue
                h.begin(cid)
                if cid == "G0":
                    case_g0(h, repeats=int(args.g0_repeats))
                elif cid == "D5":
                    case_d5(h, reuse_open=False)
                else:
                    CASES[cid](h)
            _print_table(h.rows)
            failed = [r for r in h.rows if not r.get("ok") and not r.get("skipped")]
            return 1 if failed else 0
    finally:
        _HARNESS = None
        box_macro._tap = _ORIG_TAP
        try:
            client.quit()
        except Exception:
            pass
        _kill(proc)


if __name__ == "__main__":
    raise SystemExit(main())
