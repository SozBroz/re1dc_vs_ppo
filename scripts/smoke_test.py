#!/usr/bin/env python3
"""Import and plumbing smoke test — no emulator required."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOG_SAVE_DIR = Path(r"C:\Program Files (x86)\GOG Galaxy\Games\Resident Evil\SAVE")


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append((name, True, ""))
        except Exception as exc:  # noqa: BLE001 — smoke test aggregates failures
            results.append((name, False, str(exc)))

    # Imports
    def _imports() -> None:
        import re1_rl  # noqa: F401
        import re1_rl.bizhawk_bridge  # noqa: F401
        import re1_rl.env  # noqa: F401
        import re1_rl.memory_map  # noqa: F401
        import re1_rl.planner  # noqa: F401
        import re1_rl.reward  # noqa: F401
        import re1_rl.save_parser  # noqa: F401
        import re1_rl.pc_track  # noqa: F401
        import re1_rl.pc_track.capture  # noqa: F401
        import re1_rl.pc_track.input  # noqa: F401
        import re1_rl.pc_track.process_memory  # noqa: F401

    check("imports", _imports)

    # Planner + reward with dummy data
    def _planner_reward() -> None:
        from re1_rl.planner import WaypointPlanner
        from re1_rl.reward import compute_reward

        planner = WaypointPlanner(ROOT / "data" / "route_jill_anypct.json", waypoints=["105", "106"])
        assert planner.next_waypoint_room() == "105"
        prev = {"room_id": "104", "hp": 100, "inventory": [], "step": 1}
        state = {"room_id": "105", "hp": 95, "inventory": [], "step": 2}
        r = compute_reward(prev, state, planner)
        assert isinstance(r, float)

    check("planner_reward", _planner_reward)

    # BizHawk client instantiate (no connection)
    def _bridge() -> None:
        from re1_rl.bizhawk_bridge import BizHawkClient

        client = BizHawkClient(port=5556)
        assert client.port == 5556

    check("bizhawk_bridge", _bridge)

    # PC track stubs
    def _pc_track() -> None:
        from re1_rl.pc_track.process_memory import ProcessMemory

        pm = ProcessMemory()
        assert not pm.is_attached()

    check("pc_track", _pc_track)

    # Save parser against real GOG saves if present
    def _save_parser() -> None:
        from re1_rl.save_parser import parse_save_file

        save_path = GOG_SAVE_DIR / "savedat1.dat"
        if not save_path.is_file():
            print("  [skip] no GOG save at", save_path)
            return
        inv = parse_save_file(save_path)
        print(f"  savedat1.dat inventory ({len(inv)} items):", inv)
        assert isinstance(inv, list)

    check("save_parser", _save_parser)

    # Curriculum JSON load
    def _curriculum() -> None:
        import json

        stage_path = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
        data = json.loads(stage_path.read_text(encoding="utf-8"))
        assert data["stage"] == "m0_dining_to_main_hall"

    check("curriculum", _curriculum)

    # Summary
    print("\n=== smoke_test summary ===")
    failed = 0
    for name, ok, err in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}" + (f" — {err}" if err else ""))
        if not ok:
            failed += 1

    if failed:
        print(f"\n{failed} check(s) FAILED")
        return 1
    print("\nAll checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
