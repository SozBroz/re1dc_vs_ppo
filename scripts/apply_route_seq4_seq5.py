"""One-shot: seq 4 accepts 201/203/207 upstairs; seq 5 return to 106.

Updates route JSON, scaffolding tests, and checkpoints doc in one pass.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "data" / "route_jill_anypct.json"
TESTS = ROOT / "tests" / "test_scaffolding.py"
DOC = ROOT / "docs" / "checkpoints_and_route_guide.md"

SEQ4 = {
    "seq": 4,
    "room_id": "203",
    "objective": "explore mansion 2F (main hall / east or west stairs)",
    "action_type": "navigate",
    "required_items": [],
    "items_gained": [],
    "success_condition": {
        "type": "room_enter_any",
        "room_ids": ["201", "203", "207"],
    },
    "notes": "203 HALL 2F is compass goal; RAM may report 201 (east stair 2F) or 207 (west stair 2F) while climbing.",
    "source": "alexfung|in-game",
}

SEQ5 = {
    "seq": 5,
    "room_id": "106",
    "objective": "return to main hall after 2F explore (Barry dialogue)",
    "action_type": "scripted_macro",
    "required_items": [],
    "items_gained": [],
    "success_condition": {
        "type": "room_enter",
        "room_id": "106",
    },
    "notes": "Re-enter 106 after coming back down from 2F; Barry scene follows in-room.",
    "source": "alexfung|in-game",
}

TEST_BLOCK = '''def test_explore_2f_waypoint_after_barry_and_wesker():
    """Route step 4: 201/203/207 pays only after Barry + Wesker complete."""
    g = RoomGraph(DOORS)
    planner = make_planner(route_steps=[2, 3, 4])
    progress = ProgressTracker()
    in_106 = make_state(room="106", step=1)
    in_203 = make_state(room="203", step=3)

    _, bd0 = compute_reward(in_106, in_203, planner, progress=progress,
                            graph=g, return_breakdown=True)
    assert bd0["waypoint"] == 0.0
    assert planner.waypoint_index == 0

    for _ in range(60):
        progress.record_in_control_step("106", True)
    settled = make_state(room="106", step=2)
    compute_reward(in_106, settled, planner, progress=progress, graph=g)
    assert planner.waypoint_index == 1

    progress.on_waypoint_advanced()
    for _ in range(60):
        progress.record_in_control_step("106", True)
    settled2 = make_state(room="106", step=3)
    compute_reward(settled, settled2, planner, progress=progress, graph=g)
    assert planner.waypoint_index == 2

    _, bd1 = compute_reward(settled2, in_203, planner, progress=progress,
                            graph=g, return_breakdown=True)
    assert bd1["waypoint"] > 0
    assert planner.waypoint_index == 3


def test_explore_2f_waypoint_accepts_stair_rooms():
    for room in ("201", "207"):
        g = RoomGraph(DOORS)
        planner = make_planner(route_steps=[2, 3, 4])
        progress = ProgressTracker()
        in_106 = make_state(room="106", step=1)

        for _ in range(60):
            progress.record_in_control_step("106", True)
        settled = make_state(room="106", step=2)
        compute_reward(in_106, settled, planner, progress=progress, graph=g)
        progress.on_waypoint_advanced()
        for _ in range(60):
            progress.record_in_control_step("106", True)
        settled2 = make_state(room="106", step=3)
        compute_reward(settled, settled2, planner, progress=progress, graph=g)

        _, bd = compute_reward(settled2, make_state(room=room, step=3), planner,
                               progress=progress, graph=g, return_breakdown=True)
        assert bd["waypoint"] > 0
        assert planner.waypoint_index == 3


def test_return_to_hall_after_2f_explore():
    """Route step 5: re-enter 106 after seq 4 complete."""
    g = RoomGraph(DOORS)
    planner = make_planner(route_steps=[2, 3, 4, 5])
    progress = ProgressTracker()

    for seq in (2, 3):
        for _ in range(60):
            progress.record_in_control_step("106", True)
        compute_reward(make_state(room="106"), make_state(room="106"), planner,
                       progress=progress, graph=g)
        progress.on_waypoint_advanced()

    compute_reward(make_state(room="106"), make_state(room="203"), planner,
                   progress=progress, graph=g)
    assert planner.waypoint_index == 3

    _, bd = compute_reward(make_state(room="203"), make_state(room="106"), planner,
                           progress=progress, graph=g, return_breakdown=True)
    assert bd["waypoint"] > 0
    assert planner.waypoint_index == 4
'''


def patch_route() -> None:
    route = json.loads(ROUTE.read_text(encoding="utf-8"))
    by_seq = {int(s["seq"]): s for s in route}
    by_seq[4] = SEQ4
    by_seq[5] = SEQ5
    route = [by_seq[int(s["seq"])] for s in route]
    ROUTE.write_text(json.dumps(route, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"patched {ROUTE}")


def patch_tests() -> None:
    text = TESTS.read_text(encoding="utf-8")
    pattern = re.compile(
        r"def test_explore_2f_waypoint_after_barry_and_wesker\(\):.*?"
        r"(?=\ndef test_pbrs_zero_on_closed_loop)",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit("test block anchor not found")
    text = pattern.sub(TEST_BLOCK + "\n\n", text)
    TESTS.write_text(text, encoding="utf-8")
    print(f"patched {TESTS}")


def patch_doc() -> None:
    text = DOC.read_text(encoding="utf-8")
    text = text.replace(
        "| **4** | 203 Hall 2F (or **201** / **213**) | **Explore top floor** | Enter **203**, **201**, or **213** (`room_enter_any`) |",
        "| **4** | 203 Hall 2F (or **201** / **207**) | **Explore top floor** | Enter **203**, **201**, or **207** (`room_enter_any`) |",
    )
    text = text.replace(
        "| **5** | 106 Main Hall | Talk to **Barry** again (after 2F) | 60+ in-control steps in hall |",
        "| **5** | 106 Main Hall | **Return to hall** after 2F (Barry dialogue) | Enter room **106** (`room_enter`) |",
    )
    text = text.replace(
        "| 4 | 203 | Hall 2F (alt **201** east stairs, **213** elevator) | **Explore top floor** |",
        "| 4 | 203 | Hall 2F (alt **201** east stairs, **207** west stairs) | **Explore top floor** |",
    )
    text = text.replace(
        "| 5 | 106 | Main Hall | **Talk to Barry** (after exploring) |",
        "| 5 | 106 | Main Hall | **Return to hall** after 2F explore |",
    )
    DOC.write_text(text, encoding="utf-8")
    print(f"patched {DOC}")


def main() -> None:
    patch_route()
    patch_tests()
    patch_doc()
    print("APPLY_ROUTE_SEQ4_SEQ5_OK")


if __name__ == "__main__":
    main()
