"""Human-readable HUD overlay: annotate a game frame with what the agent
knows (obs fields), wants (goal compass), and feels (reward breakdown).

Used by scripts/watch_env.py for live debugging and by the YouTube capture
pipeline. Pure cv2 drawing; no game/bridge dependencies.
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

PANEL_W = 360
FONT = cv2.FONT_HERSHEY_SIMPLEX

GREEN = (80, 220, 80)
RED = (60, 60, 230)
WHITE = (230, 230, 230)
GRAY = (150, 150, 150)
YELLOW = (60, 200, 240)
CYAN = (220, 200, 60)


def _put(img, text, x, y, color=WHITE, scale=0.42, thick=1):
    cv2.putText(img, text, (x, y), FONT, scale, color, thick, cv2.LINE_AA)


def _draw_compass(img, cx, cy, r, bearing_sin, bearing_cos, has_door):
    """Egocentric compass: needle up = door dead ahead of the player."""
    cv2.circle(img, (cx, cy), r, GRAY, 1, cv2.LINE_AA)
    if not has_door:
        _put(img, "?", cx - 5, cy + 5, GRAY, 0.5)
        return
    ang = math.atan2(bearing_sin, bearing_cos)  # 0 = ahead
    tip = (int(cx + r * 0.85 * math.sin(ang)), int(cy - r * 0.85 * math.cos(ang)))
    cv2.arrowedLine(img, (cx, cy), tip, CYAN, 2, cv2.LINE_AA, tipLength=0.35)


def annotate_frame(
    rgb: np.ndarray,
    obs: dict[str, np.ndarray],
    info: dict[str, Any],
    reward: float | None = None,
) -> np.ndarray:
    """Return BGR image: game frame + side panel of agent internals."""
    from re1_rl.obs_encoder import GOAL_FIELDS, PROPRIO_FIELDS

    frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = frame.shape[:2]
    canvas = np.zeros((max(h, 560), w + PANEL_W, 3), dtype=np.uint8)
    canvas[:h, :w] = frame

    x0, y = w + 10, 20
    goal = obs.get("goal")
    proprio = obs.get("proprio")
    g = {name: float(goal[i]) for i, (name, _) in enumerate(GOAL_FIELDS)} if goal is not None else {}
    p = {name: float(proprio[i]) for i, (name, _) in enumerate(PROPRIO_FIELDS)} if proprio is not None else {}

    # header: where / where to
    _put(canvas, f"room {info.get('room_id', '?')}  ->  wp {info.get('waypoint', '?')} "
         f"[{info.get('waypoint_index', '?')}]", x0, y, WHITE, 0.5, 1)
    y += 22
    hp = info.get("hp", 0)
    _put(canvas, f"hp {hp}", x0, y, GREEN if hp > 48 else RED, 0.5)
    obj = [name[4:] for name, v in g.items() if name.startswith("obj_") and v > 0.5]
    _put(canvas, f"objective: {obj[0] if obj else '?'}", x0 + 80, y, YELLOW, 0.5)
    y += 14

    # compass to active exit door
    _draw_compass(canvas, x0 + 40, y + 45, 38,
                  g.get("door_bearing_sin", 0.0), g.get("door_bearing_cos", 1.0),
                  g.get("doors_available", 0.0) > 0.5)
    _put(canvas, f"door dist {g.get('door_distance', 0):.2f}", x0 + 95, y + 35, CYAN)
    _put(canvas, f"hops {g.get('route_hop_distance', 0) * 20:.0f}", x0 + 95, y + 55, CYAN)
    wrong = g.get("wrong_room_flag", 0.0) > 0.5
    _put(canvas, "OFF ROUTE" if wrong else "on route", x0 + 95, y + 75,
         RED if wrong else GREEN)
    y += 105

    # reward breakdown bars
    bd: dict[str, float] = info.get("reward_breakdown") or {}
    total_txt = f"reward {reward:+.4f}" if reward is not None else "reward"
    _put(canvas, total_txt, x0, y, WHITE, 0.5, 1)
    y += 8
    for name, val in bd.items():
        y += 16
        color = GREEN if val > 0 else (RED if val < 0 else GRAY)
        _put(canvas, f"{name:<12}{val:+8.3f}", x0, y, color)
        bar = int(min(abs(val), 2.0) / 2.0 * 120)
        if bar:
            cv2.rectangle(canvas, (x0 + 170, y - 8), (x0 + 170 + bar, y - 2), color, -1)
    y += 24

    # proprio essentials
    _put(canvas, f"pos ({info.get('state', {}).get('x', '?')}, "
         f"{info.get('state', {}).get('z', '?')})  facing "
         f"{info.get('state', {}).get('facing', '?')}", x0, y, GRAY)
    y += 16
    _put(canvas, f"in_control {p.get('in_control', 0):.0f}   "
         f"cam {info.get('state', {}).get('cam_id', '?')}   "
         f"skip {info.get('frames_skipped', 0)}f", x0, y, GRAY)
    y += 16
    _put(canvas, f"progress: wp_index {info.get('waypoint_index', 0)} / "
         f"max {info.get('max_waypoint', 0)}", x0, y, GRAY)
    y += 22

    # items: inventory, TODO progress, pickups left in this room
    todo = info.get("item_todo")
    if todo:
        _put(canvas, f"item TODO {todo[0]}/{todo[1]}   next: "
             f"{info.get('next_item') or 'done'}", x0, y, YELLOW, 0.45)
        y += 16
    left = info.get("items_left_here")
    if left is not None:
        gated = info.get("gated_items_here") or 0
        color = CYAN if left else GRAY
        txt = f"pickups left in room: {left}"
        if gated:
            txt += f"  (+{gated} locked, later)"
        _put(canvas, txt, x0, y, color, 0.45)
        y += 16

    # spatial: nearest-item arrow, enemy summary, visited coverage
    spatial = obs.get("spatial")
    if spatial is not None:
        from re1_rl.spatial_encoder import SPATIAL_FIELDS

        sp = {name: float(spatial[i]) for i, (name, _) in enumerate(SPATIAL_FIELDS)}
        if sp.get("item0_dist", 0.0) > 0.0:
            _draw_compass(canvas, x0 + 20, y + 18, 16,
                          sp["item0_bearing_sin"], sp["item0_bearing_cos"], True)
            _put(canvas, f"nearest item d={sp['item0_dist']:.2f}"
                 + ("  KEY" if sp.get("item0_key_item", 0) > 0.5 else ""),
                 x0 + 45, y + 22, CYAN, 0.45)
            y += 40
        n_enemies = round(sp.get("enemy_count", 0.0) * 10)
        if n_enemies:
            _put(canvas, f"enemies alive: {n_enemies}  nearest d={sp.get('enemy0_dist', 0):.2f}",
                 x0, y, RED, 0.45)
            y += 16
    visited = obs.get("visited")
    if visited is not None:
        _put(canvas, f"room cells seen: {int(visited.sum())}", x0, y, GRAY, 0.45)
        y += 16
    inv = info.get("inventory") or []
    _put(canvas, "inventory:", x0, y, WHITE, 0.45)
    y += 14
    if not inv:
        _put(canvas, "  (empty)", x0, y, GRAY)
        y += 14
    for name, qty in inv:
        new = name in set(info.get("new_items") or [])
        _put(canvas, f"  {name} x{qty}" + ("  NEW" if new else ""), x0, y,
             GREEN if new else WHITE)
        y += 14

    return canvas
