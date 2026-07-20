"""Wide RAM hunt: botany-book examine UI vs START/ITEM vs gameplay (QS1).

Loads QuickSave1 (book open), dumps regions, Triangle-closes to gameplay,
opens Start/ITEM, dumps again, then reports bytes unique to book vs ITEM.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.bizhawk_paths import EMUHAWK, LUA, ROM
from re1_rl.inventory_menu_macro import open_item_screen
from re1_rl.memory_map import (
    GAME_MODE,
    GAME_STATE,
    MESSAGE_FLAG,
    PLAYER_HP,
    SCENE_FLAG,
)
from re1_rl.ram_skip import (
    in_control_from_ram,
    item_inventory_screen_from_ram,
    message_open_from_ram,
    options_menu_from_ram,
    pause_menu_tree_from_ram,
)

STATE = (
    ROOT
    / "tools"
    / "BizHawk-2.11.1"
    / "PSX"
    / "State"
    / "Resident Evil - Director's Cut (USA).Nymashock.QuickSave1.State"
)
PORT = 7833
OUT = ROOT / "data" / "_document_examine_signifier_hunt.json"

# Menu / UI / session work RAM (proven useful in prior hunts).
REGIONS: list[tuple[int, int]] = [
    (0x800B7000, 0x2000),  # ITEM submenu / cursor / selected item
    (0x800C2800, 0x1800),  # near game_state / gallery / scene
    (0x800C5000, 0x1000),  # player entity + nearby
    (0x800C8000, 0x1000),  # stage/room/msg/timer/maps/files/inv
    (0x800CA800, 0x1000),  # poly / UI sprite work
    (0x80180000, 0x4000),  # misc work RAM used in submenu hunts
    (0x801C0000, 0x4000),
]

# Extra named spots for the summary table.
NAMED = [
    ("game_state", GAME_STATE, "u32"),
    ("game_mode", GAME_MODE, "u8"),
    ("scene_flag", SCENE_FLAG, "u8"),
    ("msg_flag", MESSAGE_FLAG, "u8"),
    ("submenu_n", 0x800B7FE9, "u8"),
    ("submenu_item", 0x800B7FEB, "u8"),
    ("submenu_flags", 0x800B7FF3, "u8"),
    ("submenu_cursor", 0x800B7FF4, "u8"),
    ("submenu_qty", 0x800B7FF6, "u8"),
    ("maps_files", 0x800C8714, "u16"),
]


def _snap_meta(client: BizHawkClient) -> dict:
    ram = client.read_ram(NAMED)
    out = {k: int(v) for k, v in ram.items()}
    out["in_control"] = bool(in_control_from_ram(ram))
    out["pause_menu"] = bool(pause_menu_tree_from_ram(ram))
    out["item_screen"] = bool(item_inventory_screen_from_ram(ram))
    out["message_open"] = bool(message_open_from_ram(ram))
    out["options_menu"] = bool(options_menu_from_ram(ram))
    return out


def _dump_regions(client: BizHawkClient) -> dict[str, list[int]]:
    blobs: dict[str, list[int]] = {}
    for base, count in REGIONS:
        blobs[f"0x{base:08X}"] = [int(b) for b in client.read_block(base, count)]
    return blobs


def _triangle_close(client: BizHawkClient, *, max_pulses: int = 45) -> int:
    for i in range(max_pulses):
        meta = _snap_meta(client)
        if meta["in_control"] and not meta["pause_menu"] and not meta["message_open"]:
            return i
        client.step({"triangle": True}, n=2)
        client.step({}, n=4)
    return max_pulses


def _diff_bytes(
    a: dict[str, list[int]],
    b: dict[str, list[int]],
) -> list[tuple[int, int, int]]:
    """Return (addr, a_val, b_val) for differing bytes."""
    hits: list[tuple[int, int, int]] = []
    for key, base_hex in [(k, int(k, 16)) for k in a]:
        aa = a[key]
        bb = b[key]
        n = min(len(aa), len(bb))
        for i in range(n):
            if aa[i] != bb[i]:
                hits.append((base_hex + i, aa[i], bb[i]))
    return hits


def _stable_book_unique(
    book: dict[str, list[int]],
    item: dict[str, list[int]],
    play: dict[str, list[int]],
) -> list[dict]:
    """Bytes where book != item, and book != play (book-specific vs both)."""
    out: list[dict] = []
    for key, base in [(k, int(k, 16)) for k in book]:
        bb = book[key]
        ii = item[key]
        pp = play[key]
        n = min(len(bb), len(ii), len(pp))
        for i in range(n):
            if bb[i] != ii[i] and bb[i] != pp[i]:
                out.append(
                    {
                        "addr": f"0x{base + i:08X}",
                        "book": bb[i],
                        "item": ii[i],
                        "play": pp[i],
                    }
                )
    return out


def main() -> int:
    if not STATE.is_file():
        raise FileNotFoundError(STATE)

    client = BizHawkClient(
        port=PORT,
        timeout=180.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / "_document_examine_hunt.png"),
        screenshot_mmf=True,
    )
    client.start_server()
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
            "--gdi",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(8)
        client.wait_for_client()
        client.load_savestate(str(STATE))
        client.frameadvance(4)

        book_meta = _snap_meta(client)
        book_blob = _dump_regions(client)
        print("BOOK", json.dumps(book_meta, indent=2), flush=True)
        client.screenshot()

        pulses = _triangle_close(client)
        play_meta = _snap_meta(client)
        play_blob = _dump_regions(client)
        print(f"PLAY after triangle pulses={pulses}", json.dumps(play_meta, indent=2), flush=True)

        if not play_meta["in_control"]:
            raise RuntimeError("failed to reach gameplay after Triangle")

        hp = max(int(client.read_ram([("player_hp", PLAYER_HP, "u16")])["player_hp"]), 1)
        died, frames, _, opened = open_item_screen(
            client, prev_hp=hp, episode_start_hp=hp
        )
        client.frameadvance(4)
        item_meta = _snap_meta(client)
        item_blob = _dump_regions(client)
        print(
            f"ITEM died={died} frames={frames} opened={opened}",
            json.dumps(item_meta, indent=2),
            flush=True,
        )

        book_vs_item = _diff_bytes(book_blob, item_blob)
        book_vs_play = _diff_bytes(book_blob, play_blob)
        item_vs_play = _diff_bytes(item_blob, play_blob)
        unique = _stable_book_unique(book_blob, item_blob, play_blob)

        # Prefer small scalars / near known menu bases for candidates.
        def _score(row: dict) -> tuple[int, int]:
            addr = int(row["addr"], 16)
            near_menu = 0 if 0x800B7E00 <= addr < 0x800B8100 else 1
            near_gs = 0 if 0x800C3000 <= addr < 0x800C3100 else 1
            near_msg = 0 if 0x800C8600 <= addr < 0x800C8800 else 1
            near_ui = 0 if 0x800CA800 <= addr < 0x800CB800 else 1
            near_player = 0 if 0x800C5000 <= addr < 0x800C5300 else 1
            tier = min(near_menu, near_gs, near_msg, near_ui, near_player)
            return (tier, addr)

        unique_sorted = sorted(unique, key=_score)
        # Collapse to first 80 priority + count
        priority = unique_sorted[:80]

        # Also: same value in book, different in item, even if equals play
        # (less useful but sometimes a sticky flag).
        book_ne_item_only = []
        for key, base in [(k, int(k, 16)) for k in book_blob]:
            bb, ii = book_blob[key], item_blob[key]
            for i in range(min(len(bb), len(ii))):
                if bb[i] != ii[i]:
                    book_ne_item_only.append(
                        {
                            "addr": f"0x{base + i:08X}",
                            "book": bb[i],
                            "item": ii[i],
                            "play": play_blob[key][i],
                        }
                    )
        book_ne_item_prio = sorted(book_ne_item_only, key=_score)[:80]

        # Named byte table across three states
        named_table = []
        for name, addr, dtype in NAMED:
            named_table.append(
                {
                    "name": name,
                    "addr": f"0x{addr:08X}",
                    "dtype": dtype,
                    "book": book_meta.get(name),
                    "item": item_meta.get(name),
                    "play": play_meta.get(name),
                }
            )

        summary = {
            "state": str(STATE),
            "regions": [{"base": f"0x{b:08X}", "count": c} for b, c in REGIONS],
            "book_meta": book_meta,
            "play_meta": play_meta,
            "item_meta": item_meta,
            "named_table": named_table,
            "diff_counts": {
                "book_vs_item": len(book_vs_item),
                "book_vs_play": len(book_vs_play),
                "item_vs_play": len(item_vs_play),
                "book_unique_vs_item_and_play": len(unique),
            },
            "book_unique_priority": priority,
            "book_ne_item_priority": book_ne_item_prio,
        }
        OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print("DIFF COUNTS", json.dumps(summary["diff_counts"], indent=2), flush=True)
        print("NAMED TABLE", flush=True)
        for row in named_table:
            print(
                f"  {row['name']:16} {row['addr']}  "
                f"book={row['book']} item={row['item']} play={row['play']}",
                flush=True,
            )
        print("TOP book-unique (book!=item and book!=play):", flush=True)
        for row in priority[:40]:
            print(
                f"  {row['addr']}  book={row['book']:3} item={row['item']:3} play={row['play']:3}",
                flush=True,
            )
        print("wrote", OUT, flush=True)
        return 0
    finally:
        try:
            client.quit()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
