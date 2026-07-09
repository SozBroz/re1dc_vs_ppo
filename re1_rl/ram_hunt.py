"""Shared RAM snapshot + diff helpers for capture_session and hunt scripts."""

from __future__ import annotations

from collections import defaultdict

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import MESSAGE_FLAG, PS1_MAINRAM_BASE

MAINRAM_SIZE = 0x200000
CHUNK = 0x10000

SCD_FLAG_LO = 0x800C8600
SCD_FLAG_HI = 0x800C8800

PROMPT_FAST_LO = 0x800C0000
PROMPT_FAST_HI = 0x800D0000

PROMPT_EXCLUDE = [
    (0x800C5100, 0x800C5200),
    (0x800C8670, 0x800C8680),
]

ENEMY_CANDIDATE_BASE = 0x801141FC
ENEMY_SLOT_STRIDE = 0x18C
ENEMY_NUM_SLOTS = 6


def read_range(client: BizHawkClient, lo: int, hi: int) -> list[int]:
    size = hi - lo
    out: list[int] = []
    for off in range(0, size, CHUNK):
        out.extend(client.read_block(lo + off, min(CHUNK, size - off)))
    return out


def read_mainram(client: BizHawkClient) -> list[int]:
    return read_range(client, PS1_MAINRAM_BASE, PS1_MAINRAM_BASE + MAINRAM_SIZE)


def diff_bytes(
    before: list[int],
    after: list[int],
    lo: int = PS1_MAINRAM_BASE,
) -> list[tuple[int, int, int]]:
    n = min(len(before), len(after))
    return [(lo + i, before[i], after[i]) for i in range(n) if before[i] != after[i]]


def bit_flips(before: list[int], after: list[int], lo: int) -> list[dict]:
    n = min(len(before), len(after))
    flips: list[dict] = []
    for i in range(n):
        xor = before[i] ^ after[i]
        if not xor:
            continue
        addr = lo + i
        for bit in range(8):
            if xor & (1 << bit):
                old_bit = (before[i] >> bit) & 1
                new_bit = (after[i] >> bit) & 1
                flips.append({
                    "address": f"0x{addr:08X}",
                    "bit": bit,
                    "old": old_bit,
                    "new": new_bit,
                    "transition": f"{old_bit}->{new_bit}",
                })
    return flips


def message_flag_byte(client: BizHawkClient) -> int:
    return int(client.read_ram([("msg", MESSAGE_FLAG, "u8")])["msg"])


def prompt_snapshot(client: BizHawkClient, lo: int, hi: int, tag: str) -> list[int]:
    msg = message_flag_byte(client)
    print(f"  [{tag}] MESSAGE_FLAG=0x{msg:02X} (bit7={'1' if msg & 0x80 else '0'})",
          flush=True)
    return read_range(client, lo, hi)


def in_prompt_exclude(addr: int) -> bool:
    return any(lo <= addr < hi for lo, hi in PROMPT_EXCLUDE)


def consistent_prompt_diffs(
    away_snaps: list[list[int]],
    at_snaps: list[list[int]],
    lo: int,
) -> list[tuple[int, set[int], set[int]]]:
    n = min(len(s) for s in away_snaps + at_snaps)
    away_vals: dict[int, set[int]] = defaultdict(set)
    at_vals: dict[int, set[int]] = defaultdict(set)
    for snap in away_snaps:
        for i in range(n):
            away_vals[i].add(snap[i])
    for snap in at_snaps:
        for i in range(n):
            at_vals[i].add(snap[i])
    hits: list[tuple[int, set[int], set[int]]] = []
    for i in range(n):
        addr = lo + i
        if in_prompt_exclude(addr):
            continue
        av, tv = away_vals[i], at_vals[i]
        if av and tv and av.isdisjoint(tv):
            hits.append((addr, av, tv))
    return sorted(hits, key=lambda t: t[0])


def fmt_byte_set(s: set[int]) -> str:
    return "{" + ",".join(f"0x{v:02X}" for v in sorted(s)) + "}"


def cluster_changes(
    changes: list[tuple[int, int, int]],
) -> list[dict]:
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
        clusters.append({
            "start": lo_addr,
            "end": hi_addr,
            "size": hi_addr - lo_addr + 1,
            "changes": [{"addr": f"0x{a:08X}", "old": o, "new": n} for a, o, n in run],
        })
    return clusters


def _u16_at(data: list[int], index: int) -> int:
    if index + 1 >= len(data):
        return 0
    return data[index] | (data[index + 1] << 8)


def _cluster_min_distance(cluster: dict, target: int) -> int:
    start, end = int(cluster["start"]), int(cluster["end"])
    if start <= target <= end:
        return 0
    return start - target if target < start else target - end


def rank_enemy_clusters(
    clusters: list[dict],
    before: list[int],
    after: list[int],
    lo: int = PS1_MAINRAM_BASE,
) -> list[dict]:
    ranked: list[dict] = []
    for c in clusters:
        dist = _cluster_min_distance(c, ENEMY_CANDIDATE_BASE)
        tags: list[str] = []
        for entry in c["changes"]:
            idx = int(entry["addr"], 16) - lo
            old_b, new_b = int(entry["old"]), int(entry["new"])
            if old_b != 0 and new_b == 0:
                tags.append("byte_to_zero")
            if idx >= 0 and idx + 1 < len(before):
                if _u16_at(before, idx) != 0 and _u16_at(after, idx) == 0:
                    tags.append("u16_to_zero")
        if dist <= ENEMY_SLOT_STRIDE:
            tags.append("near_candidate")
        row = dict(c)
        row["score"] = dist
        row["dist_to_candidate"] = dist
        row["tags"] = sorted(set(tags))
        ranked.append(row)
    ranked.sort(key=lambda r: (r["score"], r["start"]))
    return ranked
