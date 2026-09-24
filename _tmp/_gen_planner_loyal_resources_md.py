"""Write docs/planner_loyal_resources.md from cell.pst RAM + meta.

Reads live ``states/planner_loyal/cells`` unless that tree is thinner than
the latest ``backups/planner_loyal_cells_pre_march_*/cells`` (tape-march
wipe). Override with ``--backup``, ``--live``, or ``--cells``.

Also diffs cell.pst mtimes vs the previous generate, scans fleet worker logs
for hop completions, and flags tips that have not succeeded recently.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import time
import zlib
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from re1_rl.go_explore_archive import LEG_FRAMES_SENTINEL  # noqa: E402
from re1_rl.memory_map import (  # noqa: E402
    ITEM_BOX_BASE,
    ITEM_IDS,
    INVENTORY_BASE,
    INVENTORY_SLOTS,
    PLAYER_HP,
    PS1_MAINRAM_BASE,
)
from re1_rl.planner_loyal_cells import PLANNER_LOYAL_QUALITY_UNKNOWN  # noqa: E402

LIVE_CELLS = ROOT / "states" / "planner_loyal" / "cells"
CELLS = LIVE_CELLS  # resolved in main() — backup vs live
OUT = ROOT / "docs" / "planner_loyal_resources.md"
STATE_PATH = ROOT / "data" / "planner_loyal_resources_gen_state.json"
PY = ROOT / "venv" / "Scripts" / "python.exe"
SCRIPT = ROOT / "_tmp" / "_gen_planner_loyal_resources_md.py"
BACKUP_DIR_GLOB = "planner_loyal_cells_pre_march_*"
MAGIC = 0x50535842
SEC_ZLIB = 1
BS_SEC_RAM = 0x02
BOX_SLOTS_LIVE = 48

# Tips with no hop success for this long (wall clock) are red-flagged.
STALE_SUCCESS_HOURS = 6.0
# Per-host worker log bytes scanned (newest recomp log).
LOG_TAIL_BYTES = 12_000_000

HOP_OK_RE = re.compile(
    r"hop_score_live\]\s+tip='(pl\d+)'\s+outcome='planner_step_success'"
)
MINTED_RE = re.compile(r"\[planner_loyal\]\s+minted\s+(pl\d+)\b")
REJECT_RE = re.compile(r"\[planner_loyal\]\s+reject quality\s+(pl\d+)\b")
TIP_RE = re.compile(r"\[planner_loyal\]\s+reset tip=(pl\d+)\b")

FLEET_LOGS: list[tuple[str, str | None, Path]] = [
    ("pking", None, ROOT / "data" / "logs" / "worker_pking-recomp.log"),
    ("wh1", "sshuser@192.168.0.203", Path(r"D:\re1_rl\data\logs\worker_wh1-recomp.log")),
    ("wh2", "sshuser@192.168.0.116", Path(r"C:\Users\sshuser\re1_rl\data\logs\worker_wh2-recomp.log")),
    ("wh3", "sshuser@192.168.0.229", Path(r"C:\Users\sshuser\re1_rl\data\logs\worker_wh3-recomp.log")),
]


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def _u64(b: bytes, off: int) -> int:
    return struct.unpack_from("<Q", b, off)[0]


def pst_ram(pst: Path) -> bytes:
    blob = pst.read_bytes()
    if len(blob) < 36 or _u32(blob, 0) != MAGIC:
        raise ValueError(f"not a PSXB pst: {pst}")
    nsec = _u32(blob, 28)
    off = 36
    for _ in range(nsec):
        tag = _u32(blob, off)
        pad = _u32(blob, off + 4)
        ln = _u64(blob, off + 8)
        payload = blob[off + 16 : off + 16 + ln]
        off += 16 + ln
        if tag != BS_SEC_RAM:
            continue
        raw = payload
        if pad & SEC_ZLIB:
            raw_len = _u32(payload, 0)
            raw = zlib.decompress(payload[4:])
            if len(raw) != raw_len:
                raise ValueError(f"zlib len {len(raw)} != {raw_len}")
        return raw
    raise ValueError(f"no RAM section: {pst}")


def slots_at(ram: bytes, bus: int, n: int) -> list[tuple[str, int]]:
    base = bus - PS1_MAINRAM_BASE
    out: list[tuple[str, int]] = []
    for i in range(n):
        iid = ram[base + i * 2]
        qty = ram[base + i * 2 + 1]
        if iid == 0:
            continue
        out.append((ITEM_IDS.get(iid, f"id_{iid:02X}"), int(qty)))
    return out


def ammo(slots: list[tuple[str, int]], weapon: str, pile: str) -> tuple[int, int]:
    loaded = reserve = 0
    for name, qty in slots:
        if name == weapon:
            loaded += max(0, int(qty))
        elif name == pile:
            reserve += max(0, int(qty))
    return loaded, reserve


BAZOOKA_AMMO_NAMES = frozenset(
    {
        "bazooka_acid",
        "bazooka_explosive",
        "bazooka_flame",
        "acid_rounds",
        "explosive_rounds",
        "flame_rounds",
    }
)
# Per-type buckets (weapon clip + matching reserve pile).
GL_ACID_NAMES = frozenset({"bazooka_acid", "acid_rounds"})
GL_EXPLOSIVE_NAMES = frozenset({"bazooka_explosive", "explosive_rounds"})
GL_FLAME_NAMES = frozenset({"bazooka_flame", "flame_rounds"})
MAGNUM_AMMO_NAMES = frozenset(
    {
        "colt_python",
        "colt_python_dumdum",
        "magnum_rounds",
        "dumdum_rounds",
    }
)


def sum_named(slots: list[tuple[str, int]], names: frozenset[str]) -> int:
    return sum(max(0, int(qty)) for name, qty in slots if name in names)


def beat_of(meta: dict) -> str:
    step = meta.get("planner_step") or {}
    return str(
        step.get("beat_id")
        or step.get("edge_id")
        or step.get("pickup_id")
        or step.get("op")
        or meta.get("checkpoint_id")
        or ""
    )


def leg_frames(meta: dict) -> int | None:
    """Elapsed hop frames from lexicographic `-frames`.

    11-dim planner-loyal quality stores ``-frames`` at index 8; the 8-dim
    go-explore tuple stores it at index 7. Unknown / sentinel values are blank.
    """
    q = list(meta.get("quality") or [])
    raw = None
    if len(q) >= 11:
        raw = q[8]
    elif len(q) >= 8:
        raw = q[7]
    else:
        return None
    try:
        neg = int(raw)
    except (TypeError, ValueError):
        return None
    if neg in (PLANNER_LOYAL_QUALITY_UNKNOWN, -int(LEG_FRAMES_SENTINEL)):
        return None
    frames = -neg
    if frames <= 0 or frames >= int(LEG_FRAMES_SENTINEL):
        return None
    return frames


def kill_total(meta: dict) -> int | None:
    """Cumulative almanac kills at this cell (`quality[1]` / `kills.almanac_total`)."""
    q = list(meta.get("quality") or [])
    if len(q) > 1:
        try:
            n = int(q[1])
        except (TypeError, ValueError):
            n = None
        else:
            # Unknown sentinel from 11-dim stitch — treat as missing.
            if n != -99999:
                return max(0, n)
    kills = meta.get("kills") if isinstance(meta.get("kills"), dict) else {}
    raw = kills.get("almanac_total")
    if raw is None:
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def hp_band(hp: int) -> str:
    if hp <= 0:
        return "dead"
    if hp <= 20:
        return "Danger"
    if hp <= 40:
        return "Caution"
    return "Fine"


def delta_cell(n: int | None) -> str:
    if n is None:
        return ""
    return f"{n:+d}"


# Floor ammo gained on acquire beats (pickup_id / beat_id item token).
FLOOR_AMMO_QTY: dict[str, int] = {
    "handgun_bullets": 15,
    "shotgun_shells": 7,
    "shotgun": 7,  # wall rack comes loaded
    # GL: weapon pickup is usually a loaded launcher (6); round piles are 6.
    "bazooka_acid": 6,
    "bazooka_explosive": 6,
    "bazooka_flame": 6,
    "acid_rounds": 6,
    "explosive_rounds": 6,
    "flame_rounds": 6,
}


def _beat_item_token(beat: str) -> str:
    """``108:handgun_bullets:1`` → ``handgun_bullets``; else ````."""
    parts = str(beat or "").split(":")
    if len(parts) >= 2 and parts[0] and parts[1]:
        return parts[1]
    return ""


def floor_ammo_gain(beat: str, *, kind: str) -> int:
    """Ammo rounds added from the floor on this cell's beat (0 if none)."""
    item = _beat_item_token(beat)
    if kind == "hg":
        return int(FLOOR_AMMO_QTY.get(item, 0)) if item == "handgun_bullets" else 0
    if kind == "sg":
        if item in {"shotgun_shells", "shotgun"}:
            return int(FLOOR_AMMO_QTY.get(item, 0))
        return 0
    if kind == "baz":
        if item in FLOOR_AMMO_QTY and item in BAZOOKA_AMMO_NAMES:
            return int(FLOOR_AMMO_QTY[item])
        return 0
    return 0


def parse_prev_kit(md_text: str) -> dict[str, dict[str, object]]:
    """Parse the Kit-by-PL table from a prior resources.md."""
    lines = md_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "## Kit by PL")
    except StopIteration:
        return {}
    out: dict[str, dict[str, object]] = {}
    for ln in lines[start + 1 :]:
        if ln.startswith("## "):
            break
        s = ln.strip()
        if not s.startswith("|") or s.startswith("| :") or s.startswith("| PL"):
            continue
        # | `plNN` | HP | Kills | HG | SG | Baz | Mag |
        parts = [p.strip() for p in s.strip("|").split("|")]
        if len(parts) < 6:
            continue
        name = parts[0].strip("`")
        if not name.startswith("pl"):
            continue
        if parts[1] == "missing pst":
            out[name] = {"missing": True}
            continue

        def _num(raw: str) -> int | None:
            raw = raw.strip()
            if raw == "":
                return None
            try:
                return int(raw)
            except ValueError:
                return None

        # Older docs: 6 cols (no Kills), 7 cols (no Frames), 8 cols (current).
        if len(parts) == 6:
            out[name] = {
                "missing": False,
                "hp": _num(parts[1]),
                "kills": None,
                "hg": _num(parts[2]),
                "sg": _num(parts[3]),
                "baz": _num(parts[4]),
                "mag": _num(parts[5]),
                "frames": None,
            }
        elif len(parts) == 7:
            out[name] = {
                "missing": False,
                "hp": _num(parts[1]),
                "kills": _num(parts[2]),
                "hg": _num(parts[3]),
                "sg": _num(parts[4]),
                "baz": _num(parts[5]),
                "mag": _num(parts[6]),
                "frames": None,
            }
        else:
            out[name] = {
                "missing": False,
                "hp": _num(parts[1]),
                "kills": _num(parts[2]),
                "hg": _num(parts[3]),
                "sg": _num(parts[4]),
                "baz": _num(parts[5]),
                "mag": _num(parts[6]),
                "frames": _num(parts[7]),
            }
    return out


def _kit_snapshot(row: dict) -> dict[str, object]:
    if row.get("missing"):
        return {"missing": True}
    return {
        "missing": False,
        "hp": row.get("hp"),
        "kills": row.get("kills"),
        "hg": row.get("hg"),
        "sg": row.get("sg"),
        "baz": row.get("baz"),
        "mag": row.get("mag"),
        "frames": row.get("frames"),
    }


def print_meaningful_kit_diffs(
    prev: dict[str, dict[str, object]],
    rows: list[dict],
) -> None:
    """Stdout cells whose kit columns changed vs the previous markdown."""
    cur = {r["name"]: _kit_snapshot(r) for r in rows}
    keys = ("hp", "kills", "hg", "sg", "baz", "mag", "frames")
    labels = {
        "hp": "HP",
        "kills": "Kills",
        "hg": "HG",
        "sg": "SG",
        "baz": "Baz",
        "mag": "Mag",
        "frames": "Frames",
    }
    added = sorted(set(cur) - set(prev), key=pl_sort_key)
    removed = sorted(set(prev) - set(cur), key=pl_sort_key)
    changed: list[str] = []
    for name in sorted(set(cur) & set(prev), key=pl_sort_key):
        a, b = prev[name], cur[name]
        if a.get("missing") and b.get("missing"):
            continue
        if a.get("missing") != b.get("missing"):
            if b.get("missing"):
                changed.append(f"{name}: now missing pst")
            else:
                changed.append(f"{name}: restored from missing")
            continue
        bits: list[str] = []
        for k in keys:
            if a.get(k) != b.get(k):
                bits.append(f"{labels[k]} {a.get(k)}->{b.get(k)}")
        if bits:
            changed.append(f"{name}: " + ", ".join(bits))

    if not prev:
        print("kit diff: no previous markdown (first generate)")
        return
    if not added and not removed and not changed:
        print("kit diff: no meaningful changes")
        return
    print("kit diff (vs previous markdown):")
    for name in added:
        snap = cur[name]
        if snap.get("missing"):
            print(f"  + {name} (missing pst)")
        else:
            print(
                f"  + {name} HP={snap['hp']} Kills={snap['kills']} "
                f"HG={snap['hg']} SG={snap['sg']} Baz={snap['baz']} Mag={snap['mag']} "
                f"Frames={snap['frames']}"
            )
    for name in removed:
        print(f"  - {name} (gone)")
    for line in changed:
        print(f"  ~ {line}")


def md_table(
    headers: list[str],
    rows: list[list[object]],
    *,
    aligns: list[str] | None = None,
) -> list[str]:
    """Column-padded GFM table so source MD lines up in the editor."""
    n = len(headers)
    if aligns is None:
        aligns = ["l"] * n
    if len(aligns) != n:
        raise ValueError("aligns length must match headers")
    grid: list[list[str]] = [[str(h) for h in headers]]
    for row in rows:
        if len(row) != n:
            raise ValueError(f"row width {len(row)} != header width {n}")
        grid.append([str(c) for c in row])
    widths = [max(len(grid[r][c]) for r in range(len(grid))) for c in range(n)]

    def pad(text: str, width: int, align: str) -> str:
        if align == "r":
            return text.rjust(width)
        if align == "c":
            return text.center(width)
        return text.ljust(width)

    def rule(align: str, width: int) -> str:
        w = max(width, 3)
        if align == "r":
            return "-" * (w - 1) + ":"
        if align == "c":
            return ":" + "-" * max(w - 2, 1) + ":"
        return ":" + "-" * (w - 1)

    lines = [
        "| "
        + " | ".join(pad(grid[0][i], widths[i], aligns[i]) for i in range(n))
        + " |",
        "| " + " | ".join(rule(aligns[i], widths[i]) for i in range(n)) + " |",
    ]
    for row in grid[1:]:
        lines.append(
            "| "
            + " | ".join(pad(row[i], widths[i], aligns[i]) for i in range(n))
            + " |"
        )
    return lines


def attach_deltas(rows: list[dict]) -> None:
    prev_hg = prev_hp = prev_sg = prev_baz = None
    for r in rows:
        if r.get("missing"):
            r["d_hg"] = r["d_hp"] = r["d_sg"] = r["d_baz"] = None
            r["u_hg"] = r["u_sg"] = r["u_baz"] = None
            r["pick_hg"] = r["pick_sg"] = r["pick_baz"] = 0
            continue
        r["pick_hg"] = floor_ammo_gain(r["beat"], kind="hg")
        r["pick_sg"] = floor_ammo_gain(r["beat"], kind="sg")
        r["pick_baz"] = floor_ammo_gain(r["beat"], kind="baz")
        if prev_hg is None:
            r["d_hg"] = r["d_hp"] = r["d_sg"] = r["d_baz"] = None
            r["u_hg"] = r["u_sg"] = r["u_baz"] = None
        else:
            r["d_hg"] = r["hg"] - prev_hg
            r["d_hp"] = r["hp"] - prev_hp
            r["d_sg"] = r["sg"] - prev_sg
            r["d_baz"] = r["baz"] - prev_baz
            # Spent rounds: prior on-person + floor pickup − current on-person.
            # Box reshuffles are ignored so deposits are not counted as "used".
            r["u_hg"] = int(prev_hg) + int(r["pick_hg"]) - int(r["hg"])
            r["u_sg"] = int(prev_sg) + int(r["pick_sg"]) - int(r["sg"])
            r["u_baz"] = int(prev_baz) + int(r["pick_baz"]) - int(r["baz"])
        prev_hg, prev_hp, prev_sg, prev_baz = r["hg"], r["hp"], r["sg"], r["baz"]


def worst_usage(rows: list[dict], key: str, n: int = 5) -> list[dict]:
    """Largest positive ammo spend (pickup-adjusted), else largest HP drop."""
    if key == "d_hp":
        scored = [r for r in rows if r.get(key) is not None and r[key] < 0]
        scored.sort(key=lambda r: (r[key], r["pl"]))
        return scored[:n]
    scored = [r for r in rows if r.get(key) is not None and r[key] > 0]
    scored.sort(key=lambda r: (-r[key], r["pl"]))
    return scored[:n]


def worst_md(title: str, unit: str, key: str, hops: list[dict]) -> list[str]:
    lines = [f"### {title}", ""]
    ammo = key in {"u_hg", "u_sg", "u_baz"}
    val_key = {"u_hg": "hg", "u_sg": "sg", "u_baz": "baz", "d_hp": "hp"}[key]
    dlt_key = {"u_hg": "d_hg", "u_sg": "d_sg", "u_baz": "d_baz", "d_hp": "d_hp"}[key]
    pick_key = {"u_hg": "pick_hg", "u_sg": "pick_sg", "u_baz": "pick_baz"}.get(key)
    if ammo:
        headers = ["Rank", "Cell", "Beat", "Room", unit, "Used", "Δ", "Pickup"]
        aligns = ["r", "l", "l", "l", "r", "r", "r", "r"]
    else:
        headers = ["Rank", "Cell", "Beat", "Room", unit, "Δ"]
        aligns = ["r", "l", "l", "l", "r", "r"]
    if not hops:
        empty = [""] * len(headers)
        empty[1] = "_(no hops)_"
        lines += md_table(headers, [empty], aligns=aligns)
        lines.append("")
        return lines
    body: list[list[object]] = []
    for i, r in enumerate(hops, 1):
        if ammo:
            pick = int(r.get(pick_key or "") or 0)
            body.append(
                [
                    i,
                    f"`{r['name']}`",
                    f"`{r['beat']}`",
                    f"`{r['room']}`",
                    r[val_key],
                    int(r[key]),
                    delta_cell(r.get(dlt_key)),
                    f"+{pick}" if pick else "",
                ]
            )
        else:
            body.append(
                [
                    i,
                    f"`{r['name']}`",
                    f"`{r['beat']}`",
                    f"`{r['room']}`",
                    r[val_key],
                    f"{r[key]:+d}",
                ]
            )
    lines += md_table(headers, body, aligns=aligns)
    if len(hops) < 5:
        lines.append("")
        lines.append(
            f"Only {len(hops)} hop(s) dump this resource on the champion path."
        )
    lines.append("")
    return lines


def wasteful_gl_md(rows: list[dict], n: int = 12) -> list[str]:
    """GL spends (combined acid+explosive+flame) + long carry with no spend."""
    lines = [
        "### Wasteful grenade-launcher PLs",
        "",
        "Combined GL ammo = acid + explosive + flame (launcher clip + round piles).",
        "Box deposits count as **Used** the same way (prior + pickup − current), so",
        "`use_box` banks show up here when explosive/acid leave the person.",
        "",
        "**Biggest GL spends** (combined types)",
        "",
    ]
    spends = worst_usage(rows, "u_baz", n=n)
    spend_body: list[list[object]] = []
    for i, r in enumerate(spends, 1):
        pick = int(r.get("pick_baz") or 0)
        spend_body.append(
            [
                i,
                f"`{r['name']}`",
                f"`{r['beat']}`",
                f"`{r['room']}`",
                r["baz"],
                int(r["u_baz"]),
                delta_cell(r.get("d_baz")),
                f"+{pick}" if pick else "",
                r.get("baz_acid", 0),
                r.get("baz_explosive", 0),
                r.get("baz_flame", 0),
            ]
        )
    if not spend_body:
        spend_body = [["", "_(no hops)_", "", "", "", "", "", "", "", "", ""]]
    lines += md_table(
        [
            "Rank",
            "Cell",
            "Beat",
            "Room",
            "Total GL",
            "Used",
            "Δ",
            "Pickup",
            "Acid",
            "Explosive",
            "Flame",
        ],
        spend_body,
        aligns=["r", "l", "l", "l", "r", "r", "r", "r", "r", "r", "r"],
    )
    lines.append("")

    # Corridor tax: holding GL ammo across hops with zero spend and no pickup.
    carry: list[dict] = []
    for r in rows:
        if r.get("missing"):
            continue
        if int(r.get("baz") or 0) <= 0:
            continue
        if int(r.get("u_baz") or 0) != 0:
            continue
        if int(r.get("pick_baz") or 0) != 0:
            continue
        if int(r.get("d_baz") or 0) != 0:
            continue
        fr = r.get("frames")
        if fr is None:
            continue
        carry.append(r)
    carry.sort(key=lambda r: (-int(r.get("frames") or 0), -int(r.get("baz") or 0), r["pl"]))
    carry = carry[:n]
    lines += [
        "**Longest hops while carrying unused GL ammo** (Δ=0, no floor pickup)",
        "",
    ]
    body: list[list[object]] = []
    for i, r in enumerate(carry, 1):
        body.append(
            [
                i,
                f"`{r['name']}`",
                f"`{r['beat']}`",
                f"`{r['room']}`",
                r["baz"],
                r.get("baz_acid", 0),
                r.get("baz_explosive", 0),
                r.get("baz_flame", 0),
                r.get("frames") or "",
            ]
        )
    if not body:
        body = [["", "_(none)_", "", "", "", "", "", "", ""]]
    lines += md_table(
        ["Rank", "Cell", "Beat", "Room", "Total GL", "Acid", "Explosive", "Flame", "Frames"],
        body,
        aligns=["r", "l", "l", "l", "r", "r", "r", "r", "r"],
    )
    lines.append("")
    return lines


def pl_sort_key(name: str) -> tuple[int, str]:
    """Numeric PL order so pl100 follows pl99, not pl10."""
    if name.startswith("pl"):
        tail = name[2:]
        if tail.isdigit():
            return (int(tail), name)
    return (10**9, name)


def pl_num(name: str) -> int:
    return int(name[2:])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_gen_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_gen_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def collect_pst_mtimes() -> dict[str, float]:
    out: dict[str, float] = {}
    if not CELLS.is_dir():
        return out
    for d in CELLS.iterdir():
        if not d.name.startswith("pl"):
            continue
        pst = d / "cell.pst"
        if not pst.is_file():
            continue
        try:
            pl_num(d.name)
        except ValueError:
            continue
        out[d.name] = float(pst.stat().st_mtime)
    return out


def new_savestates_since(
    prev_mtimes: dict[str, float],
    cur_mtimes: dict[str, float],
) -> list[tuple[str, float, str]]:
    """Return (pl, age_hours, kind) for pst newer than last generate snapshot."""
    if not prev_mtimes:
        # First generate: establish baseline only (don't list every installed cell).
        return []
    rows: list[tuple[str, float, str]] = []
    now = time.time()
    for name, mt in cur_mtimes.items():
        prev = prev_mtimes.get(name)
        if prev is None:
            kind = "new_cell"
        elif mt > prev + 0.5:
            kind = "updated"
        else:
            continue
        rows.append((name, (now - mt) / 3600.0, kind))
    rows.sort(key=lambda r: pl_sort_key(r[0]))
    return rows


def _newest_recomp_log(parent: Path, preferred: Path | None = None) -> Path | None:
    """Newest `worker*recomp*.log` by mtime (dated rotate beats stale undated)."""
    if not parent.is_dir():
        return None
    cands = sorted(
        parent.glob("worker*recomp*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if cands:
        return cands[0]
    if preferred is not None and preferred.is_file():
        return preferred
    return None


def _read_local_tail(path: Path, nbytes: int) -> tuple[str, float | None]:
    # Always prefer newest dated rotate; undated worker_*-recomp.log can be days stale.
    path = _newest_recomp_log(path.parent, preferred=path) or path
    if not path.is_file():
        return "", None
    mtime = path.stat().st_mtime
    size = path.stat().st_size
    with path.open("rb") as f:
        f.seek(max(0, size - nbytes))
        raw = f.read().decode("utf-8", "replace")
    if size > nbytes:
        raw = raw.split("\n", 1)[-1]
    return raw, mtime


def _ssh_log_tail(host: str, path: Path, nbytes: int) -> tuple[str, float | None]:
    """Read last nbytes of the newest recomp worker log on a remote host."""
    if host.endswith("203"):
        py = r"D:\re1_rl\venv\Scripts\python.exe"
    else:
        py = r"C:\Users\sshuser\re1_rl\venv\Scripts\python.exe"
    remote_py = f"""
import pathlib, sys
root = pathlib.Path(r'''{path.parent}''')
cands = sorted(root.glob('worker*recomp*.log'), key=lambda p: p.stat().st_mtime, reverse=True) if root.is_dir() else []
path = cands[0] if cands else None
if path is None:
    print('__NONE__')
    raise SystemExit(0)
data = path.read_bytes()
chunk = data[-{nbytes}:] if len(data) > {nbytes} else data
print(f'__MTIME__{{path.stat().st_mtime}}')
print(f'__NAME__{{path.name}}', file=sys.stderr)
sys.stdout.buffer.write(chunk)
"""
    try:
        r = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=20",
                "-o",
                "BatchMode=yes",
                host,
                py,
                "-u",
                "-",
            ],
            input=remote_py.encode("utf-8"),
            capture_output=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"fleet log {host}: ERR {exc}")
        return "", None
    raw = (r.stdout or b"").decode("utf-8", "replace")
    if not raw or raw.startswith("__NONE__"):
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        if err:
            print(f"fleet log {host}: empty ({err[:160]})")
        return "", None
    mtime = None
    if raw.startswith("__MTIME__"):
        first, _, rest = raw.partition("\n")
        try:
            mtime = float(first.replace("__MTIME__", "", 1))
        except ValueError:
            mtime = None
        raw = rest
    return raw, mtime


def scan_text_completions(text: str) -> dict[str, Counter[str]]:
    """Per-tip hop successes, tip starts; per-dest mint/reject."""
    tip_ok: Counter[str] = Counter()
    tip_start: Counter[str] = Counter()
    minted: Counter[str] = Counter()
    reject: Counter[str] = Counter()
    for line in text.splitlines():
        m = HOP_OK_RE.search(line)
        if m:
            tip_ok[m.group(1)] += 1
            continue
        m = TIP_RE.search(line)
        if m:
            tip_start[m.group(1)] += 1
            continue
        m = MINTED_RE.search(line)
        if m:
            minted[m.group(1)] += 1
            continue
        m = REJECT_RE.search(line)
        if m:
            reject[m.group(1)] += 1
    # If hop_score lines are missing, mint/reject on dest still imply tip success.
    for dest in set(minted) | set(reject):
        src_n = pl_num(dest) - 1
        if src_n < 0:
            continue
        src = f"pl{src_n:02d}"
        if tip_ok.get(src, 0) > 0:
            continue
        tip_ok[src] += int(minted.get(dest, 0)) + int(reject.get(dest, 0))
    return {
        "tip_ok": tip_ok,
        "tip_start": tip_start,
        "minted": minted,
        "reject": reject,
    }


def scan_fleet_completions() -> dict:
    tip_ok: Counter[str] = Counter()
    tip_start: Counter[str] = Counter()
    minted: Counter[str] = Counter()
    reject: Counter[str] = Counter()
    host_rows: list[dict] = []
    for name, ssh_host, path in FLEET_LOGS:
        if ssh_host is None:
            text, mtime = _read_local_tail(path, LOG_TAIL_BYTES)
        else:
            text, mtime = _ssh_log_tail(ssh_host, path, LOG_TAIL_BYTES)
        if not text:
            host_rows.append({"host": name, "ok": False, "chars": 0})
            continue
        part = scan_text_completions(text)
        tip_ok.update(part["tip_ok"])
        tip_start.update(part["tip_start"])
        minted.update(part["minted"])
        reject.update(part["reject"])
        host_rows.append(
            {
                "host": name,
                "ok": True,
                "chars": len(text),
                "tip_ok": int(sum(part["tip_ok"].values())),
                "tip_start": int(sum(part["tip_start"].values())),
                "minted": int(sum(part["minted"].values())),
                "log_mtime": mtime,
            }
        )
        print(
            f"fleet log {name}: chars={len(text)} tip_ok={sum(part['tip_ok'].values())} "
            f"starts={sum(part['tip_start'].values())} minted={sum(part['minted'].values())}"
        )
    return {
        "tip_ok": tip_ok,
        "tip_start": tip_start,
        "minted": minted,
        "reject": reject,
        "hosts": host_rows,
    }


def merge_last_success(
    prev: dict[str, str],
    tip_ok: Counter[str],
    *,
    now: datetime,
) -> dict[str, str]:
    out = dict(prev)
    stamp = _iso(now)
    for tip, n in tip_ok.items():
        if n > 0:
            out[tip] = stamp
    return out


def format_age_hours(hours: float | None) -> str:
    if hours is None:
        return "never"
    if hours < 1:
        return f"{hours * 60:.0f}m"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def collect_stale_tips(
    *,
    installed: list[str],
    tip_ok: Counter[str],
    tip_start: Counter[str],
    last_success: dict[str, str],
    now: datetime,
) -> list[dict[str, object]]:
    """Installed tips with no recent hop success (red flags)."""
    rows: list[dict[str, object]] = []
    for tip in installed:
        ls = _parse_iso(last_success.get(tip))
        age_h = (now - ls).total_seconds() / 3600.0 if ls else None
        starts = int(tip_start.get(tip, 0))
        oks = int(tip_ok.get(tip, 0))
        flag = None
        if age_h is not None and age_h >= STALE_SUCCESS_HOURS:
            flag = "stale"
        elif age_h is None and starts > 0 and oks == 0:
            flag = "window_fail"
        else:
            continue
        rows.append(
            {
                "tip": tip,
                "age_h": age_h,
                "starts": starts,
                "oks": oks,
                "flag": flag,
            }
        )
    # Worst first: window_fail with most starts, then stale by age.
    def _rank(r: dict[str, object]) -> tuple:
        flag = str(r["flag"])
        age = r["age_h"]
        starts = int(r["starts"])
        fail_rank = 0 if flag == "window_fail" else 1
        age_key = -(float(age) if isinstance(age, (int, float)) else 1e9)
        return (fail_rank, -starts, age_key, pl_sort_key(str(r["tip"])))

    rows.sort(key=_rank)
    return rows


def stagnant_top5_md(stale_rows: list[dict[str, object]], n: int = 5) -> list[str]:
    lines = [
        "### Stagnant tips (top 5)",
        "",
        "Most worrying tips from the fleet log sweep: `window_fail` (starts, "
        "zero hop success in window) then `stale` (sticky last-ok too old).",
        "",
    ]
    top = stale_rows[:n]
    if not top:
        empty = ["", "_(none)_", "", "", ""]
        lines += md_table(
            ["Rank", "Tip", "Flag", "Starts", "Last ok age"],
            [empty],
            aligns=["r", "l", "l", "r", "r"],
        )
        lines.append("")
        return lines
    body: list[list[object]] = []
    for i, r in enumerate(top, 1):
        body.append(
            [
                i,
                f"`{r['tip']}`",
                r["flag"],
                r["starts"],
                format_age_hours(
                    float(r["age_h"]) if isinstance(r["age_h"], (int, float)) else None
                ),
            ]
        )
    lines += md_table(
        ["Rank", "Tip", "Flag", "Starts", "Last ok age"],
        body,
        aligns=["r", "l", "l", "r", "r"],
    )
    lines.append("")
    return lines


def print_stagnant_stdout(stale_rows: list[dict[str, object]]) -> None:
    """Stdout for stagnant tips only (full fleet dump stays in the markdown)."""
    print("--- stagnant tips top 5 ---")
    top = stale_rows[:5]
    if not top:
        print("  (none)")
        return
    for i, r in enumerate(top, 1):
        age = r["age_h"] if isinstance(r["age_h"], (int, float)) else None
        print(
            f"  {i}. {r['tip']} flag={r['flag']} starts={r['starts']} "
            f"tip_ok={r['oks']} last_ok={format_age_hours(age)}"
        )


def fleet_sections(
    *,
    new_pst: list[tuple[str, float, str]],
    fleet: dict,
    last_success: dict[str, str],
    stale_rows: list[dict[str, object]],
    now: datetime,
    baseline_pst: bool,
) -> list[str]:
    tip_ok: Counter[str] = fleet["tip_ok"]
    tip_start: Counter[str] = fleet["tip_start"]
    minted: Counter[str] = fleet["minted"]
    lines: list[str] = [
        "## New savestates since last generate",
        "",
    ]
    if not new_pst:
        if baseline_pst:
            lines += [
                "_(baseline — first generate with state file; next run diffs PST mtimes)_",
                "",
            ]
        else:
            lines += [
                "_(none — no `cell.pst` newer than the previous generate snapshot)_",
                "",
            ]
    else:
        body: list[list[object]] = []
        for name, age_h, kind in new_pst:
            body.append(
                [
                    f"`{name}`",
                    kind,
                    format_age_hours(age_h),
                ]
            )
        lines += md_table(
            ["PL", "Change", "PST age"],
            body,
            aligns=["l", "l", "r"],
        )
        lines.append("")

    lines += [
        "## Fleet hop completions (log window)",
        "",
        "Scanned newest `worker*-recomp*.log` on pking + WH1/2/3 "
        f"(last ~{LOG_TAIL_BYTES // 1_000_000}MB each).",
        "`tip_ok` = `hop_score_live` `planner_step_success` for that tip "
        "(mint/reject on the next PL only fills tips with zero hop_score hits). "
        "Rate can exceed 100% when the log window contains successes whose "
        "`reset tip=` lines have already scrolled out.",
        "",
    ]
    hosts_ok = [h for h in fleet["hosts"] if h.get("ok")]
    if not hosts_ok:
        lines += ["_(no fleet logs readable)_", ""]
    else:
        host_body = [
            [
                h["host"],
                h.get("tip_ok", 0),
                h.get("tip_start", 0),
                h.get("minted", 0),
            ]
            for h in fleet["hosts"]
            if h.get("ok")
        ]
        lines += md_table(
            ["Host", "tip_ok", "tip starts", "mints"],
            host_body,
            aligns=["l", "r", "r", "r"],
        )
        lines.append("")

    all_tips = sorted(
        set(tip_ok) | set(tip_start) | {f"pl{pl_num(m) - 1:02d}" for m in minted if pl_num(m) > 0},
        key=pl_sort_key,
    )
    if all_tips:
        comp_body: list[list[object]] = []
        for tip in all_tips:
            dest = f"pl{pl_num(tip) + 1:02d}"
            starts = tip_start.get(tip, 0)
            oks = tip_ok.get(tip, 0)
            rate = f"{100.0 * oks / starts:.1f}%" if starts else ""
            ls = _parse_iso(last_success.get(tip))
            age_h = (now - ls).total_seconds() / 3600.0 if ls else None
            comp_body.append(
                [
                    f"`{tip}`",
                    f"`{dest}`",
                    starts,
                    oks,
                    rate,
                    format_age_hours(age_h),
                    minted.get(dest, 0),
                ]
            )
        lines += [
            "### Completions by tip (this log window)",
            "",
        ]
        lines += md_table(
            ["Tip", "Hunts", "Starts", "tip_ok", "Rate", "Last ok age", "Mints@dest"],
            comp_body,
            aligns=["l", "l", "r", "r", "r", "r", "r"],
        )
        lines.append("")

    lines += [
        f"## Stale tips (no hop success ≥ {STALE_SUCCESS_HOURS:g}h)",
        "",
        "Red flag: installed tip with no recent `planner_step_success`.",
        "`stale` = sticky last-ok older than the threshold across generates.",
        "`window_fail` = tip starts in this log window but zero tip_ok.",
        "Tips never seen in logs are omitted until the fleet attempts them.",
        "",
    ]
    if not stale_rows:
        lines += ["_(none)_", ""]
    else:
        stale_body = [
            [
                f"`{r['tip']}`",
                format_age_hours(
                    float(r["age_h"]) if isinstance(r["age_h"], (int, float)) else None
                ),
                r["starts"],
                r["oks"],
                r["flag"],
            ]
            for r in sorted(stale_rows, key=lambda r: pl_sort_key(str(r["tip"])))
        ]
        lines += md_table(
            ["Tip", "Last ok age", "Starts (window)", "tip_ok (window)", "Flag"],
            stale_body,
            aligns=["l", "r", "r", "r", "l"],
        )
        lines.append("")

    return lines


def regen_cmd(*, extra: str = "") -> str:
    return f"& '{PY}' -u '{SCRIPT}'{extra}"


def _pl_dirs(cells: Path) -> list[Path]:
    if not cells.is_dir():
        return []
    return [d for d in cells.iterdir() if d.name.startswith("pl") and d.is_dir()]


def latest_pre_march_backup() -> Path | None:
    roots = sorted(
        (ROOT / "backups").glob(BACKUP_DIR_GLOB),
        key=lambda p: p.name,
    )
    return roots[-1] if roots else None


def cells_from_backup_root(root: Path) -> Path:
    nested = root / "cells"
    if nested.is_dir() and _pl_dirs(nested):
        return nested
    return root


def rel_cells(cells: Path) -> str:
    try:
        return cells.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(cells)


def resolve_cells_dir(
    *,
    cells: Path | None,
    backup: bool,
    live: bool,
) -> tuple[Path, str]:
    """Return (cells_dir, source) where source is cli/backup/backup-auto/live."""
    picked = sum(bool(x) for x in (cells is not None, backup, live))
    if picked > 1:
        raise SystemExit("use only one of --cells / --backup / --live")
    if cells is not None:
        p = cells if cells.is_absolute() else ROOT / cells
        if (p / "cells").is_dir() and not _pl_dirs(p):
            p = p / "cells"
        if not p.is_dir():
            raise SystemExit(f"cells dir not found: {p}")
        return p.resolve(), "cli"
    if live:
        return LIVE_CELLS, "live"
    bak_root = latest_pre_march_backup()
    if backup:
        if bak_root is None:
            raise SystemExit(f"no backups/{BACKUP_DIR_GLOB} found")
        return cells_from_backup_root(bak_root), "backup"
    if bak_root is not None:
        bak = cells_from_backup_root(bak_root)
        live_n = len(_pl_dirs(LIVE_CELLS))
        bak_n = len(_pl_dirs(bak))
        if bak_n > live_n:
            return bak, "backup-auto"
    return LIVE_CELLS, "live"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cells",
        type=Path,
        default=None,
        help="plNN directory (or a backup root that contains cells/)",
    )
    ap.add_argument(
        "--backup",
        action="store_true",
        help=f"latest backups/{BACKUP_DIR_GLOB}/cells",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="states/planner_loyal/cells (skip backup auto-pick)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="markdown path (default: docs/planner_loyal_resources.md)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    global CELLS, OUT
    args = parse_args(argv)
    cells_dir, cells_source = resolve_cells_dir(
        cells=args.cells, backup=args.backup, live=args.live
    )
    CELLS = cells_dir
    if args.out is not None:
        OUT = args.out if args.out.is_absolute() else ROOT / args.out
    using_backup = CELLS.resolve() != LIVE_CELLS.resolve()
    live_n = len(_pl_dirs(LIVE_CELLS))
    cells_n = len(_pl_dirs(CELLS))
    print(
        f"cells {rel_cells(CELLS)} ({cells_source}, {cells_n} pl dirs"
        + (f"; live has {live_n}" if cells_source != "live" else "")
        + ")"
    )

    prev_state = load_gen_state()
    prev_mtimes = {
        str(k): float(v)
        for k, v in (prev_state.get("pst_mtimes") or {}).items()
        if isinstance(v, (int, float))
    }
    prev_last_success = {
        str(k): str(v)
        for k, v in (prev_state.get("last_success_utc") or {}).items()
        if isinstance(v, str)
    }

    if not CELLS.is_dir():
        raise SystemExit(f"cells dir not found: {CELLS}")

    rows: list[dict] = []
    for d in sorted(CELLS.iterdir(), key=lambda p: pl_sort_key(p.name)):
        if not d.name.startswith("pl"):
            continue
        try:
            pl = int(d.name[2:])
        except ValueError:
            continue
        meta: dict = {}
        mp = d / "meta.json"
        if mp.is_file():
            meta = json.loads(mp.read_text(encoding="utf-8"))
        pst = d / "cell.pst"
        if not pst.is_file():
            rows.append(
                {
                    "pl": pl,
                    "name": d.name,
                    "beat": beat_of(meta),
                    "room": str(meta.get("room_id") or ""),
                    "kills": kill_total(meta),
                    "frames": leg_frames(meta),
                    "missing": True,
                }
            )
            continue
        ram = pst_ram(pst)
        hp_off = PLAYER_HP - PS1_MAINRAM_BASE
        hp = int.from_bytes(ram[hp_off : hp_off + 2], "little")
        q = list(meta.get("quality") or [])
        qhp = int(q[0]) if q else None
        kills = kill_total(meta)
        frames = leg_frames(meta)
        inv = slots_at(ram, INVENTORY_BASE, INVENTORY_SLOTS)
        box = slots_at(ram, ITEM_BOX_BASE, BOX_SLOTS_LIVE)
        hg_l, hg_r = ammo(inv, "beretta", "handgun_bullets")
        sg_l, sg_r = ammo(inv, "shotgun", "shotgun_shells")
        box_hg_l, box_hg_r = ammo(box, "beretta", "handgun_bullets")
        box_sg_l, box_sg_r = ammo(box, "shotgun", "shotgun_shells")
        baz_acid = sum_named(inv, GL_ACID_NAMES)
        baz_explosive = sum_named(inv, GL_EXPLOSIVE_NAMES)
        baz_flame = sum_named(inv, GL_FLAME_NAMES)
        baz = baz_acid + baz_explosive + baz_flame
        box_baz = sum_named(box, BAZOOKA_AMMO_NAMES)
        rows.append(
            {
                "pl": pl,
                "name": d.name,
                "beat": beat_of(meta),
                "room": str(meta.get("room_id") or ""),
                "hp": hp,
                "qhp": qhp,
                "kills": kills,
                "frames": frames,
                "hg_l": hg_l,
                "hg_r": hg_r,
                "hg": hg_l + hg_r,
                "sg_l": sg_l,
                "sg_r": sg_r,
                "sg": sg_l + sg_r,
                "baz_acid": baz_acid,
                "baz_explosive": baz_explosive,
                "baz_flame": baz_flame,
                "baz": baz,
                "mag": sum_named(inv, MAGNUM_AMMO_NAMES),
                "box_hg": box_hg_l + box_hg_r,
                "box_sg": box_sg_l + box_sg_r,
                "box_baz": box_baz,
                "missing": False,
            }
        )

    attach_deltas(rows)
    now = _utc_now()
    installed = [r["name"] for r in rows if not r.get("missing")]
    if using_backup:
        # Backup mtimes must not replace the live generate snapshot.
        cur_mtimes = prev_mtimes
        new_pst: list[tuple[str, float, str]] = []
        baseline_pst = False
    else:
        cur_mtimes = collect_pst_mtimes()
        new_pst = new_savestates_since(prev_mtimes, cur_mtimes)
        baseline_pst = not bool(prev_mtimes)

    # Kit diff first so the familiar output appears before the slow fleet sweep.
    prev_kit: dict[str, dict[str, object]] = {}
    if OUT.is_file():
        try:
            prev_kit = parse_prev_kit(OUT.read_text(encoding="utf-8"))
        except OSError:
            prev_kit = {}
    print_meaningful_kit_diffs(prev_kit, rows)

    today = date.today().isoformat()
    tip = max(
        (r for r in rows if not r.get("missing")),
        key=lambda r: int(r["pl"]),
        default=None,
    )
    tip_name = tip["name"] if tip else "?"
    cells_rel = rel_cells(CELLS)
    if using_backup:
        kit_src = (
            f"Champion-path kit on pre-march backup `{cells_rel}/plNN/cell.pst`."
        )
        kit_src2 = (
            "Live `states/planner_loyal/cells` is the tape-march tree (not used here)."
        )
    else:
        kit_src = (
            "Champion-path kit on installed `states/planner_loyal/cells/plNN/cell.pst`."
        )
        kit_src2 = None
    lines: list[str] = [
        "# Planner-loyal resources by PL",
        "",
        kit_src,
    ]
    if kit_src2:
        lines.append(kit_src2)
    lines += [
        "HP and ammo are read from the C-RE1 RAM section (not `quality[1]` HG-eq).",
        "Companion to [`planner_loyal_cells.md`](planner_loyal_cells.md),",
        "[`planner_loyal_ammo.md`](planner_loyal_ammo.md), and",
        "[`planner_loyal_kills.md`](planner_loyal_kills.md).",
        "",
        f"Generated **{today}**. Highest minted cell is `{tip_name}`.",
        "",
        "Consecutive rows are **independent champion mints**, not one episode.",
        "`Δ` is vs the previous installed cell. Box columns are the 100/118 item",
        "box (on-person totals ignore boxed ammo).",
        "",
        "Handgun total = beretta loaded + `handgun_bullets` reserve.",
        "Shotgun total = shotgun loaded + `shotgun_shells` reserve.",
        "GL ammo (combined) = acid + explosive + flame (launcher clips + round piles).",
        "Magnum total = both Colts + `magnum_rounds` / `dumdum_rounds` (on-person).",
        "Kills = cumulative almanac total (`quality[1]` / `kills.almanac_total`).",
        "Frames = hop elapsed (`-quality[8]`; lexicographic `-frames`).",
        "",
        "## Kit by PL",
        "",
        "On-person totals only (boxed ammo ignored). GL column is **all ammo types combined**.",
        "",
    ]
    kit_body: list[list[object]] = []
    for r in rows:
        kills_cell: object = "" if r.get("kills") is None else r["kills"]
        frames_cell: object = "" if r.get("frames") is None else r["frames"]
        if r.get("missing"):
            kit_body.append(
                [
                    f"`{r['name']}`",
                    "missing pst",
                    kills_cell,
                    "",
                    "",
                    "",
                    "",
                    frames_cell,
                ]
            )
            continue
        kit_body.append(
            [
                f"`{r['name']}`",
                r["hp"],
                kills_cell,
                r["hg"],
                r["sg"],
                r["baz"],
                r["mag"],
                frames_cell,
            ]
        )
    lines += md_table(
        [
            "PL",
            "HP",
            "Kills",
            "Pistol bullets",
            "Shotgun bullets",
            "GL ammo (all)",
            "Magnum bullets",
            "Frames",
        ],
        kit_body,
        aligns=["l", "r", "r", "r", "r", "r", "r", "r"],
    )
    lines += [
        "",
        "## Worst hops (top 5)",
        "",
        "Largest resource *spend* vs the previous champion cell.",
        "Ammo **Used** = prior on-person total + floor pickup − current total",
        "(so L-hallway `108:handgun_bullets` that nets −5 after a +15 clip still",
        "counts as **20** spent). Box deposits/withdrawals are ignored for Used.",
        "HP still ranks by raw Δ. `Δ` is the on-person net change.",
        "GL **Used** combines acid + explosive + flame.",
        "",
    ]
    lines += worst_md(
        "Handgun ammo", "Total HG", "u_hg", worst_usage(rows, "u_hg")
    )
    lines += worst_md("HP", "HP", "d_hp", worst_usage(rows, "d_hp"))
    lines += worst_md(
        "Shotgun ammo", "Total SG", "u_sg", worst_usage(rows, "u_sg")
    )
    lines += worst_md(
        "Grenade launcher (combined)",
        "Total GL",
        "u_baz",
        worst_usage(rows, "u_baz"),
    )
    lines += wasteful_gl_md(rows)
    # Placeholder index: fleet sweep fills stagnant top-5 after GL waste block.
    stagnant_insert_at = len(lines)

    hg_body: list[list[object]] = []
    prev_hg = None
    for r in rows:
        if r.get("missing"):
            hg_body.append(
                [
                    f"`{r['name']}`",
                    f"`{r['beat']}`" if r["beat"] else "",
                    f"`{r['room']}`" if r["room"] else "",
                    "",
                    "",
                    "",
                    "",
                    "missing pst",
                ]
            )
            continue
        dlt = delta_cell(None if prev_hg is None else r["hg"] - prev_hg)
        hg_body.append(
            [
                f"`{r['name']}`",
                f"`{r['beat']}`",
                f"`{r['room']}`",
                r["hg_l"],
                r["hg_r"],
                f"**{r['hg']}**",
                dlt,
                r["box_hg"],
            ]
        )
        prev_hg = r["hg"]
    lines += [
        "## Handgun ammo by PL",
        "",
    ]
    lines += md_table(
        ["Cell", "Beat", "Room", "Loaded", "Reserve", "Total", "Δ", "Box HG"],
        hg_body,
        aligns=["l", "l", "l", "r", "r", "r", "r", "r"],
    )

    hp_body: list[list[object]] = []
    prev_hp = None
    for r in rows:
        if r.get("missing"):
            hp_body.append(
                [
                    f"`{r['name']}`",
                    f"`{r['beat']}`" if r["beat"] else "",
                    f"`{r['room']}`" if r["room"] else "",
                    "",
                    "",
                    "missing pst",
                ]
            )
            continue
        dlt = delta_cell(None if prev_hp is None else r["hp"] - prev_hp)
        note = hp_band(r["hp"])
        if r.get("qhp") is not None and r["qhp"] != r["hp"]:
            note += f" (meta {r['qhp']})"
        hp_body.append(
            [
                f"`{r['name']}`",
                f"`{r['beat']}`",
                f"`{r['room']}`",
                f"**{r['hp']}**",
                dlt,
                note,
            ]
        )
        prev_hp = r["hp"]
    lines += [
        "",
        "## HP by PL",
        "",
    ]
    lines += md_table(
        ["Cell", "Beat", "Room", "HP", "Δ", "Band"],
        hp_body,
        aligns=["l", "l", "l", "r", "r", "l"],
    )

    sg_body: list[list[object]] = []
    prev_sg = None
    for r in rows:
        if r.get("missing"):
            sg_body.append(
                [
                    f"`{r['name']}`",
                    f"`{r['beat']}`" if r["beat"] else "",
                    f"`{r['room']}`" if r["room"] else "",
                    "",
                    "",
                    "",
                    "",
                    "missing pst",
                ]
            )
            continue
        dlt = delta_cell(None if prev_sg is None else r["sg"] - prev_sg)
        sg_body.append(
            [
                f"`{r['name']}`",
                f"`{r['beat']}`",
                f"`{r['room']}`",
                r["sg_l"],
                r["sg_r"],
                f"**{r['sg']}**",
                dlt,
                r["box_sg"],
            ]
        )
        prev_sg = r["sg"]
    lines += [
        "",
        "## Shotgun ammo by PL",
        "",
    ]
    lines += md_table(
        ["Cell", "Beat", "Room", "Loaded", "Reserve", "Total", "Δ", "Box SG"],
        sg_body,
        aligns=["l", "l", "l", "r", "r", "r", "r", "r"],
    )

    gl_body: list[list[object]] = []
    prev_baz = None
    for r in rows:
        if r.get("missing"):
            gl_body.append(
                [
                    f"`{r['name']}`",
                    f"`{r['beat']}`" if r["beat"] else "",
                    f"`{r['room']}`" if r["room"] else "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "missing pst",
                ]
            )
            continue
        dlt = delta_cell(None if prev_baz is None else r["baz"] - prev_baz)
        used = r.get("u_baz")
        gl_body.append(
            [
                f"`{r['name']}`",
                f"`{r['beat']}`",
                f"`{r['room']}`",
                r.get("baz_acid", 0),
                r.get("baz_explosive", 0),
                r.get("baz_flame", 0),
                f"**{r['baz']}**",
                dlt,
                "" if used is None else used,
                r.get("box_baz", 0),
            ]
        )
        prev_baz = r["baz"]
    lines += [
        "",
        "## Grenade launcher ammo by PL",
        "",
        "Acid / explosive / flame are separate columns; **Total** combines them.",
        "`Used` is pickup-adjusted spend vs the previous cell (box banks count).",
        "",
    ]
    lines += md_table(
        [
            "Cell",
            "Beat",
            "Room",
            "Acid",
            "Explosive",
            "Flame",
            "Total",
            "Δ",
            "Used",
            "Box GL",
        ],
        gl_body,
        aligns=["l", "l", "l", "r", "r", "r", "r", "r", "r", "r"],
    )

    regen_extra = ""
    if cells_source == "backup" or cells_source == "backup-auto":
        regen_extra = " --backup"
    elif args.cells is not None:
        regen_extra = f" --cells '{rel_cells(CELLS)}'"
    elif cells_source == "live" and args.live:
        regen_extra = " --live"
    lines += [
        "",
        "## How to regenerate",
        "",
        "```powershell",
        regen_cmd(extra=regen_extra),
        "```",
        "",
        "Unadorned command auto-picks the latest pre-march backup when live",
        "`states/planner_loyal/cells` has fewer `plNN` dirs. Force a tree with",
        "`--backup`, `--live`, or `--cells PATH`.",
        "",
    ]

    # Slow path last: multi-machine log sweep.
    print("scanning fleet logs (pking + WH1/2/3)...")
    fleet = scan_fleet_completions()
    last_success = merge_last_success(prev_last_success, fleet["tip_ok"], now=now)
    stale_rows = collect_stale_tips(
        installed=installed,
        tip_ok=fleet["tip_ok"],
        tip_start=fleet["tip_start"],
        last_success=last_success,
        now=now,
    )
    print_stagnant_stdout(stale_rows)

    stagnant_block = stagnant_top5_md(stale_rows)
    lines[stagnant_insert_at:stagnant_insert_at] = stagnant_block
    lines += fleet_sections(
        new_pst=new_pst,
        fleet=fleet,
        last_success=last_success,
        stale_rows=stale_rows,
        now=now,
        baseline_pst=baseline_pst,
    )

    OUT.write_text("\n".join(lines), encoding="utf-8")
    save_gen_state(
        {
            "generated_at_utc": _iso(now),
            "pst_mtimes": cur_mtimes,
            "last_success_utc": last_success,
            "stale_success_hours": STALE_SUCCESS_HOURS,
            "fleet_hosts": fleet.get("hosts"),
            "cells": rel_cells(CELLS),
            "cells_source": cells_source,
        }
    )
    print(f"wrote {OUT} rows={len(rows)}")
    print(f"wrote state {STATE_PATH} last_success_tips={len(last_success)}")
    for r in rows:
        if r.get("missing"):
            continue
        if r.get("qhp") is not None and r["qhp"] != r["hp"]:
            print(f"HP mismatch {r['name']} ram={r['hp']} meta={r['qhp']}")


if __name__ == "__main__":
    main()
