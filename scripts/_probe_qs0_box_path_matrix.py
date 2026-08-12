"""Live QA: every QS0 goal-preserving box path from path enumeration.

Tests D/W/CO sequences against QuickSave0 (mansion save room 100) and
asserts the expected end layout:
  box: knife
  inv: ink_ribbon, acid_rounds, bazooka_acid (+ fixed loadout)

Usage:
    python scripts/_probe_qs0_box_path_matrix.py
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    probe_box_ui_open,
    _wait,
)
from re1_rl.memory_map import (  # noqa: E402
    INVENTORY_BASE,
    ITEM_BOX_BASE,
    ITEM_IDS,
    PLAYER_HP,
)

STATE = next(
    (ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State").glob("*QuickSave0.State")
)
PORT = 5652
ROOM_ID = "100"  # mansion save room — room-100 baz/acid/rocket allowlist

KNIFE = KNIFE_ITEM_ID
INK = 0x2F
ACID = 0x11
BAZ = 0x07
BERETTA = 0x02
BULLETS = 0x0B
SHIELD = 0x35
SHOTGUN = 0x03
CHEMICAL = 0x26

FIXED_INV = frozenset({BERETTA, BULLETS, SHIELD, SHOTGUN, CHEMICAL})
GOAL_BOX = frozenset({KNIFE})
GOAL_INV_MOVABLE = frozenset({INK, ACID, BAZ})

ALIASES = {
    "K": KNIFE,
    "I": INK,
    "A": ACID,
    "B": BAZ,
}
NAMES = {v: k for k, v in {
    "knife": KNIFE,
    "ink": INK,
    "acid": ACID,
    "baz": BAZ,
}.items()}
# op tokens: CO | W:K | D:I etc.


def _name(iid: int) -> str:
    return ITEM_IDS.get(int(iid), f"0x{int(iid):02x}")


def co_placements(base: list[str]) -> list[list[str]]:
    """All CO insertions in gaps around ``base`` (no consecutive CO)."""
    if not base:
        return [[], ["CO"]]
    n = len(base)
    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for mask in range(1 << (n + 1)):
        seq: list[str] = []
        for g in range(n + 1):
            if mask & (1 << g):
                if not seq or seq[-1] != "CO":
                    seq.append("CO")
            if g < n:
                seq.append(base[g])
        while seq and seq[-1] == "CO":
            seq.pop()
        key = tuple(seq)
        if key not in seen:
            seen.add(key)
            out.append(seq)
    return out


def all_sequences() -> list[tuple[str, list[str], str | None]]:
    """Return (name, ops, expected_block_reason).

    ``expected_block_reason`` is a substring matched against failure text when
    the path cannot run on QS0 geometry (full 8/8 inv, odd-column empties).
    """
    cases: list[tuple[str, list[str], str | None]] = []
    cases.append(("noop", [], None))
    cases.append(("co_only", ["CO"], None))

    for key, tag in (("I", "ink"), ("A", "acid"), ("B", "baz")):
        base = [f"D:{key}", f"W:{key}"]
        for i, seq in enumerate(co_placements(base)):
            block = (
                "inv_slot_unreachable"
                if key in ("I", "A")
                else "empty_slot_unreachable"
            )
            cases.append((f"waste_{tag}_{i}", seq, block))

    for temp, tname in (("I", "ink"), ("A", "acid"), ("B", "baz")):
        base = [f"D:{temp}", "W:K", "D:K", f"W:{temp}"]
        block = (
            "inv_slot_unreachable"
            if temp in ("I", "A")
            else "empty_slot_unreachable"
        )
        cases.append((f"knife_cycle_{tname}_bare", base, block))
        cases.append((f"knife_cycle_{tname}_co_mid", ["CO"] + base, block))
        cases.append(
            (
                f"knife_cycle_{tname}_co_spread",
                [f"D:{temp}", "CO", "W:K", "CO", "D:K", "CO", f"W:{temp}"],
                block,
            )
        )

    for wrong, wname in (("I", "ink"), ("A", "acid"), ("B", "baz")):
        base = ["D:K", f"W:{wrong}"]
        block = (
            "inv_slot_unreachable"
            if wrong in ("I", "A")
            else "empty_slot_unreachable"
        )
        for i, seq in enumerate(co_placements(base)):
            cases.append((f"fix_{wname}_in_box_{i}", seq, block))

    return cases


@dataclass
class CursorTracker:
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
class Results:
    passed: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _inv_slot(inv: list[tuple[int, int]], item_id: int) -> int:
    for i, (iid, _q) in enumerate(inv):
        if int(iid) == int(item_id):
            return i
    raise KeyError(f"inv missing {_name(item_id)}")


def _box_slot(box: list[tuple[int, int]], item_id: int) -> int:
    for i, (iid, _q) in enumerate(box[:16]):
        if int(iid) == int(item_id):
            return i
    raise KeyError(f"box missing {_name(item_id)}")


def _assert_goal(c: BizHawkClient, *, label: str) -> None:
    inv = read_inventory(c)
    box = read_box_live(c)
    inv_ids = {int(iid) for iid, _q in inv if iid}
    box_ids = {int(iid) for iid, _q in box[:16] if iid}

    if KNIFE not in box_ids:
        raise AssertionError(f"{label}: knife not in box; box={_fmt_box(box)}")
    if inv_ids & GOAL_INV_MOVABLE != GOAL_INV_MOVABLE:
        raise AssertionError(f"{label}: missing movable on inv; inv={_fmt_inv(inv)}")
    if KNIFE in inv_ids:
        raise AssertionError(f"{label}: knife on person; inv={_fmt_inv(inv)}")
    if not FIXED_INV.issubset(inv_ids):
        raise AssertionError(f"{label}: fixed item missing; inv={_fmt_inv(inv)}")
    if SHIELD not in inv_ids or CHEMICAL not in inv_ids:
        raise AssertionError(f"{label}: key item left person")
    if BERETTA in box_ids:
        raise AssertionError(f"{label}: beretta in box")
    if SHIELD in box_ids or CHEMICAL in box_ids:
        raise AssertionError(f"{label}: key in box")
    pol = box_pollution_reason(box)
    if pol:
        raise AssertionError(f"{label}: pollution {pol!r}")


def _fmt_inv(inv: list[tuple[int, int]]) -> str:
    return ", ".join(f"{i}:{_name(iid)}" for i, (iid, _) in enumerate(inv) if iid)


def _fmt_box(box: list[tuple[int, int]]) -> str:
    return ", ".join(f"{i}:{_name(iid)}" for i, (iid, _) in enumerate(box[:8]) if iid)


def _open_box(c: BizHawkClient, *, hp: int) -> None:
    if probe_box_ui_open(c):
        return
    for _ in range(5):
        c.step(buttons={"cross": True}, n=12, abort_on_zero_hp=False)
        _wait(c, frames=POST_OPEN_SETTLE_FRAMES, prev_hp=hp, episode_start_hp=hp)
        if probe_box_ui_open(c):
            return
        c.step(buttons={}, n=20, abort_on_zero_hp=False)
    raise RuntimeError("box UI did not open")


def _prepare_fresh(c: BizHawkClient, *, hp: int) -> None:
    if probe_box_ui_open(c):
        close_box_ui(c, prev_hp=hp, episode_start_hp=hp)
        _wait(c, frames=30, prev_hp=hp, episode_start_hp=hp)
    _open_box(c, hp=hp)


def _setup_wrong_box(c: BizHawkClient, wrong: str) -> None:
    """Only ``wrong`` allowlisted item in box; knife on inv (QS0-derived)."""
    inv = read_inventory(c)
    box = [(0, 0)] * 48
    wrong_id = ALIASES[wrong]
    box[0] = (wrong_id, inv[_inv_slot(inv, wrong_id)][1])
    # drop wrong from inv, add knife to inv at freed slot
    wslot = _inv_slot(inv, wrong_id)
    new_inv = list(inv)
    new_inv[wslot] = (KNIFE, 0)
    fields: list[tuple[str, int, str, int]] = []
    for i in range(48):
        fields.append((f"bx{i}", ITEM_BOX_BASE + i * 2, "u16", 0))
    for i in range(8):
        fields.append((f"iv{i}", INVENTORY_BASE + i * 2, "u16", 0))
    for i, (iid, qty) in enumerate(box):
        if iid:
            fields.append((f"b{i}", ITEM_BOX_BASE + i * 2, "u16", _encode_slot(iid, qty)))
    for i, (iid, qty) in enumerate(new_inv):
        if iid:
            fields.append(
                (f"i{i}", INVENTORY_BASE + i * 2, "u16", _encode_slot(iid, qty))
            )
    c.write_ram(fields)
    c.frameadvance(4)


def _run_op(
    c: BizHawkClient,
    *,
    hp: int,
    cur: CursorTracker,
    op: str,
) -> None:
    if op == "CO":
        close_box_ui(c, prev_hp=hp, episode_start_hp=hp)
        _wait(c, frames=30, prev_hp=hp, episode_start_hp=hp)
        _open_box(c, hp=hp)
        cur.inv, cur.box = 0, 0
        return

    kind, key = op.split(":", 1)
    item_id = ALIASES[key]
    if kind == "W":
        box = read_box(c)
        slot = _box_slot(box, item_id)
        ok, reason = can_withdraw(read_inventory(c), box, slot)
        if not ok:
            raise AssertionError(f"{op} blocked: {reason}")
        _d, _f, report = execute_box_withdraw_ui(
            c,
            slot,
            prev_hp=hp,
            episode_start_hp=hp,
            inv_cursor=cur.inv,
            box_cursor=cur.box,
        )
        if not report.get("ok"):
            raise AssertionError(f"{op} failed: {report}")
        cur.apply(report)
        return

    if kind == "D":
        inv = read_inventory(c)
        slot = _inv_slot(inv, item_id)
        ok, reason = can_deposit(inv, read_box(c), slot, room_id=ROOM_ID)
        if not ok:
            raise AssertionError(f"{op} blocked: {reason}")
        _d, _f, report = execute_box_deposit_ui(
            c,
            slot,
            prev_hp=hp,
            episode_start_hp=hp,
            inv_cursor=cur.inv,
            box_cursor=cur.box,
            room_id=ROOM_ID,
        )
        if not report.get("ok"):
            raise AssertionError(f"{op} failed: {report}")
        cur.apply(report)
        return

    raise ValueError(op)


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

    def reload(self) -> None:
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


def _fmt_seq(seq: list[str]) -> str:
    def one(op: str) -> str:
        if op == "CO":
            return "CO"
        k, key = op.split(":", 1)
        nm = {"K": "knife", "I": "ink", "A": "acid", "B": "baz"}[key]
        return f"{'W' if k=='W' else 'D'}({nm})"

    return " → ".join(one(o) for o in seq) if seq else "(noop)"


def main() -> int:
    if not STATE.is_file():
        print(f"ERROR: missing {STATE}")
        return 1

    cases = all_sequences()
    print(f"STATE={STATE.name}  cases={len(cases)}")

    results = Results()
    emu = Emulator()
    try:
        emu.start()
        for name, seq, expected_block in cases:
            try:
                emu.reload()
                _prepare_fresh(emu.client, hp=emu.hp)
                cur = CursorTracker()
                if name.startswith("fix_"):
                    if "ink" in name:
                        key = "I"
                    elif "acid" in name:
                        key = "A"
                    else:
                        key = "B"
                    _setup_wrong_box(emu.client, key)
                    _prepare_fresh(emu.client, hp=emu.hp)
                    cur = CursorTracker()
                blocked_at: str | None = None
                for op in seq:
                    try:
                        _run_op(emu.client, hp=emu.hp, cur=cur, op=op)
                    except Exception as step_exc:
                        if expected_block and expected_block in str(step_exc):
                            blocked_at = str(step_exc)
                            break
                        raise
                if blocked_at is not None:
                    results.blocked.append(name)
                    print(f"  BLOCKED (expected) {name}: {blocked_at[:80]}")
                    continue
                _assert_goal(emu.client, label=name)
                results.passed.append(name)
                print(f"  OK  {name}: {_fmt_seq(seq)}")
            except Exception as exc:
                if expected_block and expected_block in str(exc):
                    results.blocked.append(name)
                    print(f"  BLOCKED (expected) {name}: {exc}")
                    continue
                results.failed.append((name, str(exc)))
                print(f"  FAIL {name}: {exc}")
                try:
                    print(f"       inv: {_fmt_inv(read_inventory(emu.client))}")
                    print(f"       box: {_fmt_box(read_box_live(emu.client))}")
                    pol = box_pollution_reason(read_box_live(emu.client))
                    if pol:
                        print(f"       pollution: {pol}")
                except Exception:
                    pass
    finally:
        emu.stop()

    print(
        f"\nSUMMARY: {len(results.passed)} passed, "
        f"{len(results.blocked)} blocked (expected), "
        f"{len(results.failed)} failed"
    )
    for name in results.failed:
        print(f"  FAIL {name[0]}: {name[1]}")
    return 0 if not results.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
