#!/usr/bin/env python3
"""Regenerate docs/yawn_checkpoint_cells.md from data/yawn_checkpoint_route.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROUTE_PATH = ROOT / "data" / "yawn_checkpoint_route.json"
ROOMS_PATH = ROOT / "data" / "rooms.json"
OUT_PATH = ROOT / "docs" / "yawn_checkpoint_cells.md"


def _fmt_list(items: list[str]) -> str:
    if not items:
        return "_(none)_"
    return ", ".join(f"`{x}`" for x in items)


def _render_condition(cond: Any, *, indent: int = 0) -> list[str]:
    pad = "  " * indent
    if isinstance(cond, str):
        return [f"{pad}- Enter room `{cond}`"] if cond.strip() else [f"{pad}- (room match)"]
    if not isinstance(cond, dict):
        return [f"{pad}- (invalid condition)"]
    ctype = str(cond.get("type", "room_enter"))
    if ctype == "all_of":
        lines = [f"{pad}- **all of:**"]
        for sub in cond.get("conditions", []):
            lines.extend(_render_condition(sub, indent=indent + 1))
        return lines
    if ctype == "any_of":
        lines = [f"{pad}- **any of:**"]
        for sub in cond.get("conditions", []):
            lines.extend(_render_condition(sub, indent=indent + 1))
        return lines
    if ctype == "room_enter":
        return [f"{pad}- Enter room `{cond.get('room_id', '?')}`"]
    if ctype == "room_enter_any":
        rooms = ", ".join(f"`{r}`" for r in cond.get("room_ids", []))
        return [f"{pad}- Enter any of: {rooms}"]
    if ctype == "room_enter_from":
        src = ", ".join(f"`{r}`" for r in cond.get("from_room_ids", []))
        return [f"{pad}- Enter room `{cond.get('room_id', '?')}` from {src}"]
    if ctype == "acquired_item":
        return [f"{pad}- Acquire item `{cond.get('item', '?')}`"]
    if ctype == "has_item":
        return [f"{pad}- Have item `{cond.get('item', '?')}` in inventory"]
    if ctype == "lacks_item":
        return [f"{pad}- Lack item `{cond.get('item', '?')}` in inventory"]
    if ctype == "story_use":
        return [f"{pad}- Story USE at `{cond.get('site_id', '?')}`"]
    if ctype == "observed_cutscene":
        return [f"{pad}- Observe cutscene with prefix `{cond.get('prefix', '?')}`"]
    if ctype == "in_control_steps_in_room":
        return [
            f"{pad}- Stay in-control in `{cond.get('room_id', '?')}` for "
            f"**{int(cond.get('min_steps', 0))}** steps"
        ]
    if ctype == "in_control_steps_since_cutscene":
        return [
            f"{pad}- Stay in-control in `{cond.get('room_id', '?')}` for "
            f"**{int(cond.get('min_steps', 0))}** steps **after** cutscene "
            f"`{cond.get('prefix', '?')}`"
        ]
    if ctype == "leg_kills_in_room":
        return [
            f"{pad}- Kill **{int(cond.get('min_kills', 1))}** enemy in room "
            f"`{cond.get('room_id', '?')}` this leg"
        ]
    if ctype == "typewriter_save":
        return [f"{pad}- Complete typewriter save"]
    if ctype == "state_flag":
        return [
            f"{pad}- State flag `{cond.get('field', '?')}` == `{cond.get('value', True)!r}`"
        ]
    if ctype == "gallery_progress":
        return [f"{pad}- Gallery puzzle progress >= {int(cond.get('min_step', 0))}"]
    if ctype == "visited_any":
        rooms = ", ".join(f"`{r}`" for r in cond.get("room_ids", []))
        return [
            f"{pad}- Visited any of {rooms} at route seq >= "
            f"{int(cond.get('min_route_seq', cond.get('min_waypoint_index', 0)))}"
        ]
    return [f"{pad}- ({ctype}) `{json.dumps(cond, sort_keys=True)}`"]


def _how_to_achieve(cp: dict[str, Any]) -> str:
    room = cp.get("room_id", "?")
    action = str(cp.get("action_type", "navigate"))
    req = list(cp.get("required_items") or [])
    gained = list(cp.get("items_gained") or [])
    consume = list(cp.get("consume_before_gain") or [])
    parts = [f"Be in / reach **{room}**."]
    if req:
        parts.append(f"Hold: {_fmt_list(req)}.")
    if action == "pickup" and gained:
        parts.append(f"Pick up {_fmt_list(gained)}.")
    elif action == "use_item":
        if consume:
            parts.append(f"Consume {_fmt_list(consume)} via story USE.")
        if gained:
            parts.append(f"Gains: {_fmt_list(gained)}.")
        else:
            parts.append("Perform the story USE (inventory USE at the site).")
    elif action == "fight":
        parts.append("Fight until the combat success condition clears.")
        if gained:
            parts.append(f"Gains: {_fmt_list(gained)}.")
    else:
        parts.append("Navigate until the success condition fires.")
    return " ".join(parts)


def _render_checkpoint(cp: dict[str, Any], rooms: dict[str, Any]) -> str:
    seq = int(cp["seq"])
    cell = f"cp{seq - 1:02d}"
    cid = cp.get("checkpoint_id", "")
    room = str(cp.get("room_id", ""))
    room_name = (rooms.get(room) or {}).get("name")
    room_line = f"`{room}`"
    if room_name:
        room_line += f" ({room_name})"
    lines = [
        f"### `{cell}` — `{cid}` (seq {seq})",
        "",
        f"- **Room:** {room_line}",
        f"- **Action:** `{cp.get('action_type', 'navigate')}`",
        f"- **Objective:** {cp.get('objective', '')}",
        f"- **Required items:** {_fmt_list(list(cp.get('required_items') or []))}",
        f"- **Items gained:** {_fmt_list(list(cp.get('items_gained') or []))}",
        f"- **How to achieve:** {_how_to_achieve(cp)}",
        "- **Success condition:**",
    ]
    lines.extend(_render_condition(cp.get("success_condition")))
    lines.append("")
    return "\n".join(lines)


def render_doc() -> str:
    route: list[dict[str, Any]] = json.loads(ROUTE_PATH.read_text(encoding="utf-8"))
    rooms: dict[str, Any] = json.loads(ROOMS_PATH.read_text(encoding="utf-8"))
    n = len(route)
    header = f"""# Yawn rails checkpoint cells (`cpNN`)

Generated from [`data/yawn_checkpoint_route.json`](../data/yawn_checkpoint_route.json) ({n} steps). Cell directory index is `seq - 1` (`cp00` = seq 1).

**Source of truth:** `data/yawn_checkpoint_route.json` (objectives and success conditions below are copied verbatim). Room names in parentheses come from [`data/rooms.json`](../data/rooms.json).

On success (yawn one-leg), the fleet captures/installs `states/yawn_rails/cells/cpNN/` for the completed index.

## Summary table

| Cell | Seq | Checkpoint ID | Room | Action | Objective |
|------|-----|---------------|------|--------|-----------|
"""
    rows: list[str] = []
    for cp in route:
        seq = int(cp["seq"])
        cell = f"cp{seq - 1:02d}"
        obj = str(cp.get("objective", "")).replace("|", "\\|")
        rows.append(
            f"| `{cell}` | {seq} | `{cp.get('checkpoint_id', '')}` | "
            f"`{cp.get('room_id', '')}` | {cp.get('action_type', 'navigate')} | {obj} |"
        )
    details = "## Details\n\n" + "\n".join(_render_checkpoint(cp, rooms) for cp in route)
    return header + "\n".join(rows) + "\n\n" + details


def main() -> int:
    OUT_PATH.write_text(render_doc(), encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(json.loads(ROUTE_PATH.read_text()))} checkpoints)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
