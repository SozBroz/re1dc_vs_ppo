"""Write docs/planner_loyal_cells.md from the live chunk + rooms.json."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rooms = json.loads((ROOT / "data" / "rooms.json").read_text(encoding="utf-8"))
chunk = json.loads(
    (ROOT / "data" / "planner_chunks" / "cp05_shield_key.json").read_text(
        encoding="utf-8"
    )
)


def rname(rid: str) -> str:
    row = rooms.get(rid) or {}
    return str(row.get("name") or rid)


def dest_of(edge: str) -> str:
    return edge.split("->", 1)[1] if edge and "->" in edge else ""


SEED = [
    (0, "fresh_start_105", "105", "tip", "Dining fresh start, no emblem (installed, never minted)", None, None),
    (1, "emblem_105", "105", "acquire", "Pick up the wooden emblem", "emblem", None),
    (2, "kenneth_104", "104", "traverse", "Enter the Tea Room (Kenneth)", None, "105->104"),
    (3, "barry_return_105", "105", "traverse", "Return to Dining after Kenneth", None, "104->105"),
    (4, "main_hall_106", "106", "traverse", "Reach Main Hall after Kenneth", None, "105->106"),
    (5, "upper_hall_203", "203", "traverse", "Climb to Main Hall 2F", None, "106->203"),
    (6, "barry_hall_return_106", "106", "traverse", "Return from 203 to Main Hall (lockpick tip)", None, "203->106"),
]
TIP_SLOT = 6


def step_room(step: dict) -> str:
    if step.get("op") == "use_box":
        return str(step.get("room_id") or "118")
    room = str(step.get("room_id") or dest_of(str(step.get("edge_id") or "")))
    if room:
        return room
    pickup = str(step.get("pickup_id") or "")
    if ":" in pickup:
        return pickup.split(":", 1)[0]
    return ""


def step_cid(step: dict) -> str:
    return str(
        step.get("beat_id")
        or step.get("pickup_id")
        or step.get("site_id")
        or step.get("edge_id")
        or step.get("op")
    )


def objective_for(step: dict) -> str:
    op = str(step.get("op") or "")
    edge = str(step.get("edge_id") or "")
    pickup = str(step.get("pickup_id") or "")
    site = str(step.get("site_id") or "")
    beat = str(step.get("beat_id") or "")
    note = str(step.get("note") or "")
    room = step_room(step)
    if op == "traverse":
        to = dest_of(edge)
        return f"Walk `{edge}` into `{to}` ({rname(to)})"
    if op == "acquire":
        extra = f" ({note})" if note else ""
        return f"Take `{pickup}`{extra}"
    if op == "use_box":
        leave = "leave_100" if room == "100" else "leave_118"
        return f"Rearrange the {room} box to the {leave} loadout, then close the box"
    if op in {"objective", "do_puzzle", "trigger_cutscene"}:
        label = beat or site
        extra = f" — {note}" if note else ""
        return f"`{label}` at `{site or room}`{extra}"
    return op


def success_for(step: dict) -> str:
    op = str(step.get("op") or "")
    edge = str(step.get("edge_id") or "")
    pickup = str(step.get("pickup_id") or "")
    site = str(step.get("site_id") or "")
    room = step_room(step)
    beat = str(step.get("beat_id") or "")
    if op == "traverse":
        to = dest_of(edge)
        return (
            f"Enter room `{to}` via `{edge}` (already-there counts after cinema "
            f"dump). Any other door is `wrong_traverse:{edge} got <room>` (−4)."
        )
    if op == "acquire":
        return f"Inventory gains `{pickup}` while this step is current"
    if op == "use_box":
        if step.get("held_on_exit"):
            return "Box closes and inventory matches this step's `held_on_exit`"
        leave = "leave_100" if room == "100" else "leave_118"
        return f"Box closes and inventory matches `{leave}.held_on_exit`"
    if op == "do_puzzle" and beat.startswith("gallery_portrait_"):
        idx = beat.rsplit("_", 1)[-1]
        return f"Room `117` and gallery completed-steps >= {idx}"
    if op == "do_puzzle" and beat in {"armor_vent_door", "armor_vent_far"}:
        east = "east OM-object target `(14035, 7340)` within ±8 in all three mirrors"
        if beat.endswith("door"):
            return f"Room `205` and {east}"
        return (
            f"Room `205`, {east}, and west OM-object seat inside validated AABB "
            "X `4845..5195` / Z `7086..7336` (covers QS `(4895, 7186)`); "
            "both are mandatory "
            "(one shove-grid cell still covers the west vent AOT)"
        )
    if op == "do_puzzle" and (
        beat == "push_statue_2f" or site == "dining_statue_knocked"
    ):
        return (
            "Room `202` and dining balcony statue knocked "
            "(`dining_statue_flag` bit 0x10 / `dining_statue_knocked`)"
        )
    if op == "trigger_cutscene" and (
        beat == "richard_bleedout" or site == "20D:richard"
    ):
        return (
            "Mint `20D:richard` via long scripted skip in Pillar Passage "
            "(or confirmed 20D→204 dump). Starts Richard's ~6 min death timer. "
            "``capture:false`` — no pl cell; cinema already dumps to C passage."
        )
    if op in {"objective", "do_puzzle"}:
        return f"`story_use_success` == `{site}` in room `{room}`"
    return op


def item_gained(step: dict) -> str:
    pickup = str(step.get("pickup_id") or "")
    if not pickup:
        return "_(none)_"
    parts = pickup.split(":")
    if len(parts) >= 2:
        return f"`{parts[1]}`"
    return f"`{pickup}`"


def main() -> None:
    lines: list[str] = []
    a = lines.append
    a("# Planner-loyal cells (`plNN`)")
    a("")
    n_steps = len(chunk["steps"])
    n_capturing = sum(1 for s in chunk["steps"] if s.get("capture") is not False)
    last_slot = TIP_SLOT + n_capturing
    end_anchor = str(chunk.get("end_anchor_beat_id") or "")
    a(
        "Generated from [`data/planner_chunks/cp05_shield_key.json`]"
        f"(../data/planner_chunks/cp05_shield_key.json) ({n_steps} authored steps after "
        "the lockpick tip). Room names in parentheses come from "
        "[`data/rooms.json`](../data/rooms.json)."
    )
    a("")
    a(
        "**C-RE1 numbering (Sep 2026):** `pl00` is the dining fresh start "
        "(no emblem). Opening remint is `pl01` emblem … `pl06` lockpick "
        "(`opening_to_lockpick.json`). Live shield-key step 0 (`106->105`) "
        "mints `pl07`. Watch `states/planner_loyal/cells/plNN/cell.pst` — "
        "BizHawk `cell.State` is backed up under "
        "`backups/planner_loyal_bizhawk_20260904/`."
    )
    a("")
    a(
        "On step success the fleet installs `states/planner_loyal/cells/plNN/` "
        "for the completed index."
    )
    a("")
    a(
        "- Slot formula: capturing steps only — `capture:false` (Richard) does "
        "not consume a `plNN`. After `pl86` (`204->20D`), next mint is `pl87` "
        "(`204->207`)."
    )
    a(
        "- Training starts: every minted `pl06+` plus `pl00` when it has "
        "`cell.pst` (pin file `data/planner_loyal_reset_pin.env`; blank = "
        "uniform over loadable C-RE1 cells, **not** pinned to `pl00`)."
    )
    a(
        "- After reset from a cell, the live step is `planner_step_index + 1` "
        "(or first chunk step from `pl06`)."
    )
    a(
        "- `wrong_traverse:A->B got C` means the **wanted** hop was `A->B`; "
        "they entered `C` instead (−4 divert). Completing `A->B` mints the "
        "cell and does **not** log `wrong_traverse`."
    )
    a(
        "- Tea-room lock: `104->103` stays locked until `103->104` is done "
        "once (this chunk never opens it). `103->10C` / `103->10D` are open. "
        "Do not walk `116->106` after the shotgun. Vacant `102` clip+shells "
        "are taken on the armor-key return; skip re-loot."
    )
    a(
        f"- Chunk end-anchor: `{end_anchor}` (`pl{last_slot:02d}`). "
        "Mid-chunk success keeps the episode open."
    )
    a("")
    a("## What's minted / what it's stuck on")
    a("")
    cells_root = ROOT / "states" / "planner_loyal" / "cells"
    minted: list[tuple[int, str]] = []
    if cells_root.is_dir():
        for path in sorted(cells_root.iterdir()):
            if not path.is_dir() or not path.name.startswith("pl"):
                continue
            if not (path / "cell.pst").is_file():
                continue
            try:
                idx = int(path.name[2:])
            except ValueError:
                continue
            cid = path.name
            meta_p = path / "meta.json"
            if meta_p.is_file():
                try:
                    meta = json.loads(meta_p.read_text(encoding="utf-8"))
                    cid = str(meta.get("checkpoint_id") or path.name)
                except (OSError, json.JSONDecodeError):
                    pass
            minted.append((idx, cid))
    if minted:
        a(
            "Live C-RE1 `cell.pst` on this machine: "
            + ", ".join(f"`pl{i:02d}` ({cid})" for i, cid in minted)
            + "."
        )
        nxt = minted[-1][0] + 1
        a(
            f"Highest minted is `pl{minted[-1][0]:02d}`. The fleet is **stuck "
            f"trying to mint `pl{nxt:02d}`** (see the summary row for that "
            "slot — that is the current objective)."
        )
    else:
        a("No live `cell.pst` cells found under `states/planner_loyal/cells`.")
    a("")
    a(
        "Logs (pking): `reset tip=` is the start cell; `queue_seek ... want=` "
        "is the step they must finish; `fail='planner_divert' target=` / "
        "`divert=` is why they died before minting. `minted pl` / "
        "`reject quality pl` is a completed cell."
    )
    a("")
    a("```powershell")
    a("Get-ChildItem D:\\re1_rl\\states\\planner_loyal\\cells -Directory | % Name")
    a(
        "findstr /C:\"[planner_loyal] minted pl\" /C:\"reset tip=\" "
        "/C:\"queue_seek\" /C:\"planner_divert\" "
        "D:\\re1_rl\\data\\logs\\worker_pking-recomp.log"
    )
    a("```")
    a("")
    a("## Summary table")
    a("")
    a("| Cell | Step n | Checkpoint ID | Room | Op | Objective |")
    a("|------|--------|---------------|------|----|-----------|")

    for idx, cid, room, op, obj, _gained, _edge in SEED:
        tip = " (training tip)" if idx == TIP_SLOT else ""
        a(
            f"| `pl{idx:02d}` | seed | `{cid}` | `{room}` ({rname(room)}) | "
            f"{op} | {obj}{tip} |"
        )

    cap_n = 0
    for step in chunk["steps"]:
        n = int(step["n"])
        room = step_room(step)
        if step.get("capture") is False:
            a(
                f"| _(none)_ | {n} | `{step_cid(step)}` | `{room}` "
                f"({rname(room)}) | {step['op']} | {objective_for(step)} |"
            )
            continue
        cap_n += 1
        slot = TIP_SLOT + cap_n
        a(
            f"| `pl{slot:02d}` | {n} | `{step_cid(step)}` | `{room}` "
            f"({rname(room)}) | {step['op']} | {objective_for(step)} |"
        )

    a("")
    a("## Details")
    a("")
    a("### Seed cells (not from this chunk)")
    a("")
    for idx, cid, room, op, obj, gained, edge in SEED:
        a(f"### `pl{idx:02d}` — `{cid}` (seed)")
        a("")
        a(f"- **Room:** `{room}` ({rname(room)})")
        a(f"- **Op:** `{op}`")
        a(f"- **Objective:** {obj}")
        a(f"- **Items gained:** {('`' + gained + '`') if gained else '_(none)_'}")
        if edge:
            a(f"- **Success:** enter `{dest_of(edge)}` via `{edge}`")
        elif gained:
            a(f"- **Success:** acquire `{gained}` in `{room}`")
        a("")

    a(f"### Chunk cells (`pl07`–`pl{last_slot:02d}`)")
    a("")
    # Slot numbers skip capture:false steps (same rule as runtime).
    cap_n = 0
    for step in chunk["steps"]:
        n = int(step["n"])
        capture = step.get("capture") is not False
        if capture:
            cap_n += 1
            slot = TIP_SLOT + cap_n
            title = f"`pl{slot:02d}` — `{step_cid(step)}` (step {n})"
        else:
            title = f"`(no cell)` — `{step_cid(step)}` (step {n}, capture:false)"
        op = str(step.get("op") or "")
        edge = str(step.get("edge_id") or "")
        pickup = str(step.get("pickup_id") or "")
        site = str(step.get("site_id") or "")
        beat = str(step.get("beat_id") or "")
        note = str(step.get("note") or "")
        room = step_room(step)
        a(f"### {title}")
        a("")
        a(f"- **Room:** `{room}` ({rname(room)})")
        a(f"- **Op:** `{op}`")
        if edge:
            a(f"- **Edge:** `{edge}`")
        if pickup:
            a(f"- **Pickup:** `{pickup}`")
        if site:
            a(f"- **Site:** `{site}`")
        if beat:
            a(f"- **Beat:** `{beat}`")
        if step.get("capture") is False:
            a("- **Capture:** `false` (queue advance only; no `plNN` cell)")
        if note:
            a(f"- **Note:** {note}")
        a(f"- **Objective:** {objective_for(step)}")
        a(f"- **Items gained:** {item_gained(step)}")
        a(f"- **How to achieve:** {objective_for(step)}.")
        a(f"- **Success condition:** {success_for(step)}")
        a("")

    out = ROOT / "docs" / "planner_loyal_cells.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} lines={len(lines)}")


if __name__ == "__main__":
    main()
