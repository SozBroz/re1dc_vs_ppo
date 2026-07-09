"""Full-RAM diff hunt for the PS1 enemy entity table.

Operator drives EmuHawk; script snapshots on Enter between phases.
Kill-one-enemy diffs cluster changed bytes and rank near the ASL linear-map
candidate (0x801141FC). Optional struct probe watches 6 slots x 0x18C stride.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\hunt_enemy_ram.py --port 5555
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\hunt_enemy_ram.py --base 0x801141FC
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import CAM_ID, PLAYER_X, PLAYER_Z, PS1_MAINRAM_BASE, ROOM_ID

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / "states" / "jill_control_fresh.State"

MAINRAM_SIZE = 0x200000
CHUNK = 0x10000
CANDIDATE_BASE = 0x801141FC
SLOT_STRIDE = 0x18C
NUM_SLOTS = 6

# MediaKite ASL enemy HP offsets (GOG exe); linear-mapped to PS1 for reference only.
ASL_ENEMY_HP_OFFSETS = [
    0x8353BC,
    0x835548,
    0x8356D4,
    0x835860,
    0x8359EC,
    0x835B78,
]


def _pause(msg: str) -> None:
    print(msg, flush=True)
    input()


def read_mainram(
    client: BizHawkClient,
    lo: int = PS1_MAINRAM_BASE,
    size: int = MAINRAM_SIZE,
    chunk: int = CHUNK,
) -> list[int]:
    out: list[int] = []
    for off in range(0, size, chunk):
        out.extend(client.read_block(lo + off, min(chunk, size - off)))
    return out


def addr_of_index(lo: int, index: int) -> int:
    return lo + index


def diff_bytes(
    before: list[int],
    after: list[int],
    lo: int = PS1_MAINRAM_BASE,
) -> list[tuple[int, int, int]]:
    """(ps1_addr, old_byte, new_byte) for every changed index."""
    n = min(len(before), len(after))
    return [(lo + i, before[i], after[i]) for i in range(n) if before[i] != after[i]]


def cluster_changes(
    changes: list[tuple[int, int, int]],
) -> list[dict[str, int | list[dict[str, int | str]]]]:
    if not changes:
        return []
    changes = sorted(changes, key=lambda t: t[0])
    runs: list[list[tuple[int, int, int]]] = []
    for ch in changes:
        if runs and ch[0] == runs[-1][-1][0] + 1:
            runs[-1].append(ch)
        else:
            runs.append([ch])
    clusters: list[dict] = []
    for run in runs:
        lo_addr = run[0][0]
        hi_addr = run[-1][0]
        clusters.append(
            {
                "start": lo_addr,
                "end": hi_addr,
                "size": hi_addr - lo_addr + 1,
                "changes": [
                    {"addr": f"0x{a:08X}", "old": o, "new": n} for a, o, n in run
                ],
            }
        )
    return clusters


def cluster_min_distance(cluster: dict, target: int) -> int:
    start = int(cluster["start"])
    end = int(cluster["end"])
    if start <= target <= end:
        return 0
    if target < start:
        return start - target
    return target - end


def u16_at(data: list[int], index: int) -> int:
    if index + 1 >= len(data):
        return 0
    return data[index] | (data[index + 1] << 8)


def score_cluster(
    cluster: dict,
    before: list[int],
    after: list[int],
    lo: int,
) -> tuple[int, list[str]]:
    """Lower score = better. Tags explain ranking signals."""
    tags: list[str] = []
    dist = cluster_min_distance(cluster, CANDIDATE_BASE)
    score = dist

    for entry in cluster["changes"]:
        addr = int(entry["addr"], 16)
        idx = addr - lo
        old_b = int(entry["old"])
        new_b = int(entry["new"])
        if old_b != 0 and new_b == 0:
            tags.append("byte_to_zero")
        if old_b == 0 and new_b != 0:
            tags.append("byte_from_zero")
        if idx >= 0 and idx + 1 < len(before):
            old_u = u16_at(before, idx)
            new_u = u16_at(after, idx)
            if old_u != 0 and new_u == 0:
                tags.append("u16_to_zero")
            if 0 < old_u <= 0xFF and new_u == 0:
                tags.append("hp_like_zero")

    if dist <= SLOT_STRIDE:
        tags.append("near_asl_candidate")
    if dist % SLOT_STRIDE == 0 and dist < SLOT_STRIDE * NUM_SLOTS:
        tags.append("stride_aligned")

    return score, sorted(set(tags))


def rank_clusters(
    clusters: list[dict],
    before: list[int],
    after: list[int],
    lo: int,
) -> list[dict]:
    ranked: list[dict] = []
    for c in clusters:
        score, tags = score_cluster(c, before, after, lo)
        row = dict(c)
        row["score"] = score
        row["dist_to_candidate"] = cluster_min_distance(c, CANDIDATE_BASE)
        row["tags"] = tags
        ranked.append(row)
    ranked.sort(key=lambda r: (r["score"], r["start"]))
    return ranked


def intersect_cluster_sets(sets: list[list[dict]]) -> list[dict]:
    if not sets:
        return []
    keys = {(c["start"], c["end"]) for c in sets[0]}
    for s in sets[1:]:
        keys &= {(c["start"], c["end"]) for c in s}
    out: list[dict] = []
    for s in sets[0]:
        k = (s["start"], s["end"])
        if k in keys:
            out.append(s)
    out.sort(key=lambda c: c["start"])
    return out


def print_cluster_report(
    title: str,
    ranked: list[dict],
    *,
    limit: int = 25,
) -> None:
    print(f"\n=== {title} (top {limit}) ===", flush=True)
    for i, c in enumerate(ranked[:limit]):
        tags = ",".join(c.get("tags", [])) or "-"
        print(
            f"  #{i + 1} 0x{c['start']:08X}-0x{c['end']:08X} "
            f"({c['size']}B) dist={c['dist_to_candidate']} score={c['score']} [{tags}]",
            flush=True,
        )


def plausible_coord(v: int) -> bool:
    if v < 0:
        v = -v
    return 1000 <= v <= 33000


def read_s16(data: list[int], off: int) -> int:
    if off + 1 >= len(data):
        return 0
    raw = data[off] | (data[off + 1] << 8)
    return raw - 0x10000 if raw & 0x8000 else raw


def probe_slot_bytes(slot: list[int], player_x: int, player_z: int) -> dict:
    """Heuristic field guesses inside one 0x18C-byte slot."""
    hints: list[str] = []
    for off in range(0, min(len(slot) - 1, 0x40), 2):
        sx = read_s16(slot, off)
        if plausible_coord(sx):
            hints.append(f"+0x{off:02X}s16={sx}")
    for off in range(0, min(len(slot) - 1, 0x80), 2):
        hp = u16_at(slot, off)
        if 1 <= hp <= 0xFF:
            hints.append(f"+0x{off:02X}u16={hp}")
    return {
        "coord_hints": hints[:6],
        "player_delta": (abs(player_x), abs(player_z)),
    }


def dump_struct_table(
    client: BizHawkClient,
    base: int,
    *,
    label: str = "probe",
) -> dict:
    poll = [
        ("px", PLAYER_X, "s16"),
        ("pz", PLAYER_Z, "s16"),
        ("room", ROOM_ID, "u8"),
        ("cam", CAM_ID, "u8"),
    ]
    meta = client.read_ram(poll)
    px, pz = int(meta["px"]), int(meta["pz"])
    room, cam = int(meta["room"]), int(meta["cam"])

    print(f"\n--- {label} room={room} cam={cam} player=({px},{pz}) ---", flush=True)
    slots_out: list[dict] = []
    for i in range(NUM_SLOTS):
        addr = base + i * SLOT_STRIDE
        raw = client.read_block(addr, SLOT_STRIDE)
        hints = probe_slot_bytes(raw, px, pz)
        # Fixed hypothesis offsets (operator refines in RESULTS doc).
        row = {
            "slot": i,
            "base": f"0x{addr:08X}",
            "off0_s16": read_s16(raw, 0),
            "off2_s16": read_s16(raw, 2),
            "off4_s16": read_s16(raw, 4),
            "off6_s16": read_s16(raw, 6),
            "off8_u16": u16_at(raw, 8),
            "off0a_u8": raw[0x0A] if len(raw) > 0x0A else 0,
            "off0b_u8": raw[0x0B] if len(raw) > 0x0B else 0,
            "hints": hints["coord_hints"],
        }
        slots_out.append(row)
        hint_s = " ".join(hints["coord_hints"]) if hints["coord_hints"] else "-"
        print(
            f"  slot{i} @0x{addr:08X} "
            f"s16@0/2/4/6=({row['off0_s16']},{row['off2_s16']},{row['off4_s16']},{row['off6_s16']}) "
            f"u16@8={row['off8_u16']} u8@A/B={row['off0a_u8']:02X}/{row['off0b_u8']:02X}  [{hint_s}]",
            flush=True,
        )
    return {"room": room, "cam": cam, "player": {"x": px, "z": pz}, "slots": slots_out}


def run_kill_diff_session(
    client: BizHawkClient,
    state_path: str,
    session_idx: int,
) -> dict:
    client.load_savestate(state_path)
    client.frameadvance(3)
    _pause(
        f"[session {session_idx}] Enemy ALIVE — stand in single-enemy room, then Enter for snapshot A"
    )
    snap_a = read_mainram(client)
    _pause(f"[session {session_idx}] Kill the enemy, then Enter for snapshot B")
    snap_b = read_mainram(client)
    changes = diff_bytes(snap_a, snap_b)
    clusters = cluster_changes(changes)
    ranked = rank_clusters(clusters, snap_a, snap_b, PS1_MAINRAM_BASE)
    print_cluster_report(f"session {session_idx} kill diff", ranked)
    return {
        "session": session_idx,
        "changed_bytes": len(changes),
        "clusters": ranked,
        "snapshots": {"alive_bytes": len(snap_a), "dead_bytes": len(snap_b)},
    }


def run_struct_probe(client: BizHawkClient, base: int) -> list[dict]:
    print(f"\nStruct probe mode: base=0x{base:08X}, stride=0x{SLOT_STRIDE:X}, slots={NUM_SLOTS}",
          flush=True)
    print("Press Enter to refresh table; empty line + Enter to exit probe.", flush=True)
    last_cam: int | None = None
    readings: list[dict] = []
    while True:
        row = dump_struct_table(client, base, label="refresh")
        readings.append(row)
        cam = int(row["cam"])
        if last_cam is not None and cam != last_cam:
            print(f"  ** CAM_ID changed {last_cam} -> {cam} (same room={row['room']}) **",
                  flush=True)
        last_cam = cam
        line = input()
        if not line.strip():
            break
    return readings


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt PS1 enemy RAM via kill diffs.")
    ap.add_argument("--savestate", type=str, default=str(DEFAULT_STATE))
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument(
        "--base",
        type=lambda s: int(s, 0),
        default=None,
        help="Struct probe base address (0x...); skips kill-diff if set alone with --probe-only",
    )
    ap.add_argument(
        "--probe-only",
        action="store_true",
        help="Skip kill-diff phases; only run struct probe (requires --base).",
    )
    args = ap.parse_args()

    client = BizHawkClient(port=args.port, timeout=600.0)
    client.start_server()
    print(f"listening on port {args.port}; launch EmuHawk + re1_client.lua", flush=True)
    client.wait_for_client()
    client.set_speed(100)

    findings: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "savestate": args.savestate,
        "candidate_base": f"0x{CANDIDATE_BASE:08X}",
        "slot_stride": SLOT_STRIDE,
        "asl_enemy_hp_gog": [f"0x{o:08X}" for o in ASL_ENEMY_HP_OFFSETS],
        "sessions": [],
        "intersection": [],
        "chosen_base": None,
        "struct_probe": [],
    }

    if args.probe_only:
        if args.base is None:
            print("--probe-only requires --base", flush=True)
            return 2
        findings["struct_probe"] = run_struct_probe(client, args.base)
        findings["chosen_base"] = f"0x{args.base:08X}"
    else:
        client.load_savestate(args.savestate)
        client.frameadvance(2)
        print("Loaded savestate. Recommended rooms: 104 (tea/Kenneth), 115 (trap).", flush=True)
        print(f"ASL linear-map candidate (LOW confidence): 0x{CANDIDATE_BASE:08X}", flush=True)

        s1 = run_kill_diff_session(client, args.savestate, 1)
        findings["sessions"].append(s1)

        if input("Run second kill-diff session in another room? [y/N]: ").strip().lower() == "y":
            s2 = run_kill_diff_session(client, args.savestate, 2)
            findings["sessions"].append(s2)
            inter = intersect_cluster_sets(
                [s["clusters"] for s in findings["sessions"]]
            )
            findings["intersection"] = inter
            print_cluster_report("intersection across sessions", inter, limit=15)

        base_in = args.base
        if base_in is None:
            raw = input(
                f"Struct probe base [Enter=0x{CANDIDATE_BASE:08X}, or 0xADDR, or skip]: "
            ).strip()
            if raw.lower() in ("skip", "n", "no"):
                base_in = None
            elif raw:
                base_in = int(raw, 0)
            else:
                base_in = CANDIDATE_BASE
        if base_in is not None:
            findings["chosen_base"] = f"0x{base_in:08X}"
            findings["struct_probe"] = run_struct_probe(client, base_in)

    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"enemy_ram_hunt_{stamp}.json"
    out_path.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)

    client.quit()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
