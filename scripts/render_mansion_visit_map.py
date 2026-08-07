#!/usr/bin/env python3
"""Render every Evil Resource RE1 map with visit + pickup hover tooltips.

Layer stack (ER-identical):
  bottom — per-room highlight GIFs, red=visited / green=unvisited
  top    — map.gif (walls + orange door icons)

Hover a room → items picked up there (fleet logs) vs never picked (room_items).
"""

from __future__ import annotations

import html as html_lib
import json
import shutil
import sys
import webbrowser
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_room_items import ER_TO_ROOM  # noqa: E402

sys.path.insert(0, str(ROOT))
from re1_rl.item_todo import canonical_item  # noqa: E402

VISITED_PATH = ROOT / "data" / "logs" / "_compiled_visited_rooms.json"
PICKUPS_PATH = ROOT / "data" / "logs" / "_compiled_room_pickups.json"
ROOM_ITEMS_PATH = ROOT / "data" / "room_items.json"
ASSET_SRC = ROOT / "data" / "logs" / "_er_map_assets"
ASSET_DST = ROOT / "data" / "logs" / "er_maps"
OUT = ROOT / "data" / "logs" / "mansion_visit_map.html"
CATALOG = ASSET_SRC / "catalog.json"

GREEN = (0, 153, 0, 255)
RED = (198, 40, 40, 255)

FLOOR_ORDER = [
    "re1_mansion1f",
    "re1_mansion2f",
    "re1_mansionb1",
    "re1_courtyard",
    "re1_underground",
    "re1_guardhouse1f",
    "re1_guardhouseb1",
    "re1_laboratoryb1",
    "re1_laboratoryb2",
    "re1_laboratoryb3",
    "re1_laboratoryb4",
]

FLOOR_TITLES = {
    "re1_mansion1f": "Mansion 1F",
    "re1_mansion2f": "Mansion 2F",
    "re1_mansionb1": "Mansion B1",
    "re1_courtyard": "Courtyard",
    "re1_underground": "Underground",
    "re1_guardhouse1f": "Guardhouse 1F",
    "re1_guardhouseb1": "Guardhouse B1",
    "re1_laboratoryb1": "Laboratory B1",
    "re1_laboratoryb2": "Laboratory B2",
    "re1_laboratoryb3": "Laboratory B3",
    "re1_laboratoryb4": "Laboratory B4",
}

SECTION_ORDER = [
    ("Mansion", ["re1_mansion1f", "re1_mansion2f", "re1_mansionb1"]),
    ("Courtyard", ["re1_courtyard", "re1_underground"]),
    ("Guardhouse Residence", ["re1_guardhouse1f", "re1_guardhouseb1"]),
    (
        "Underground Laboratory",
        [
            "re1_laboratoryb1",
            "re1_laboratoryb2",
            "re1_laboratoryb3",
            "re1_laboratoryb4",
        ],
    ),
]

# Prefer explicit codes when ER_TO_ROOM has collisions / aliases.
_PREFERRED_CODE = {
    "Main Hall 1F": "106",
    "Dining Room": "105",
    "Tea Room": "104",
    "Art Room": "107",
    "'L' Passage": "108",
    "Winding Passage": "109",
    "Trap Room": "115",
    "Living Room": "116",
    "Back Passage": "10A",
    "Large Gallery": "117",
    "Roofed Passage": "11A",
    "East Stairway 1F": "10B",
    "West Stairway 1F": "101",
    "Mansion Storeroom": "118",
    "Store Room": "11B",
    "Central Corridor": "103",
    "Greenhouse": "10C",
    "Keeper's Bedroom": "10E",
    "Vacant Room": "102",
    "Mansion Save Room": "100",
    "Bar": "10F",
    "Dressing Room": "111",
    "Wardrobe": "112",
    "Tiger Statue Room": "10D",
    "Outside Boiler": "114",
    "Bathroom": "113",
    "Wardrobe Closet": "11C",
    "Courtyard Study": None,
    "Main Hall 2F": "203",
    "East Stairway 2F": "207",
    "'C' Passage": "204",
    "Terrace Entry": "211",
    "Terrace": "212",
    "Heliport Lookout": "212",
    "Dining Room 2F": "202",
    "West Stairway 2F": "201",
    "Armor Room": "205",
    "Pillar Passage": "20D",
    "Attic Entry": "20E",
    "Attic": "210",
    "Deer Room": "208",
    "Study": "20A",
    "Bedroom": "209",
    "Lesson Room Entry": "20B",
    "Lesson Room": "20C",
    "Small Dining Room": "20F",
    "Elevator 2F": "213",
    "Rough Passage": "214",
    "Trophy Room": "215",
    "Large Library": "216",
    "Private Library": "217",
    "Small Library": "218",
    "Closet": "219",
    "Underground Passage 1": "21A",
    "Underground Passage 2": "21B",
    "Kitchen": "21C",
    "Courtyard Garden": "300",
    "Water Gate": "301",
    "Falls": "302",
    "Heliport": "303",
    "Guardhouse Gate": "304",
    "Fountain": "305",
}


def er_name_to_code(name: str) -> str | None:
    if name in _PREFERRED_CODE:
        return _PREFERRED_CODE[name]
    return ER_TO_ROOM.get(name)


def load_visited() -> set[str]:
    if not VISITED_PATH.exists():
        return set()
    data = json.loads(VISITED_PATH.read_text(encoding="utf-8"))
    return {str(r["room_id"]) for r in data.get("rooms", [])}


def load_pickups() -> dict[str, set[str]]:
    if not PICKUPS_PATH.exists():
        return {}
    data = json.loads(PICKUPS_PATH.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for rid, block in (data.get("rooms") or {}).items():
        out[str(rid).upper()] = {
            canonical_item(x) for x in (block.get("picked") or [])
        }
    return out


def load_room_catalog() -> dict[str, list[dict]]:
    raw = json.loads(ROOM_ITEMS_PATH.read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {}
    for rid, block in raw.items():
        if rid.startswith("_") or not isinstance(block, dict):
            continue
        items = []
        for it in block.get("items") or []:
            name = canonical_item(str(it.get("name") or ""))
            if not name:
                continue
            items.append(
                {
                    "name": name,
                    "count": it.get("count"),
                    "key_item": bool(it.get("key_item")),
                    "notes": it.get("notes") or "",
                }
            )
        out[str(rid).upper()] = items
    return out


def pretty_item(name: str) -> str:
    return str(name).replace("_", " ").strip().title()


def room_item_breakdown(
    code: str | None,
    *,
    pickups: dict[str, set[str]],
    catalog: dict[str, list[dict]],
) -> tuple[list[str], list[str], list[str]]:
    """Return (picked_labels, never_labels, extra_logged_labels)."""
    if not code:
        return [], [], []
    code_u = code.upper()
    picked_set = set(pickups.get(code_u) or ())
    cat = catalog.get(code_u) or []
    cat_names = [c["name"] for c in cat]
    cat_canon = {canonical_item(n): n for n in cat_names}

    picked_in_catalog: list[str] = []
    never: list[str] = []
    for entry in cat:
        label = pretty_item(entry["name"])
        if entry.get("count") and int(entry["count"] or 0) > 1:
            label = f"{label} ×{entry['count']}"
        if entry.get("key_item"):
            label = f"[key] {label}"
        if canonical_item(entry["name"]) in picked_set:
            picked_in_catalog.append(label)
        else:
            never.append(label)

    extras = []
    for p in sorted(picked_set):
        if p not in cat_canon:
            extras.append(pretty_item(p))
    return picked_in_catalog, never, extras


def coords_to_svg_points(shape: str, coords: str) -> str | None:
    nums = [float(x) for x in coords.split(",") if x.strip() != ""]
    if shape == "rect" and len(nums) == 4:
        x1, y1, x2, y2 = nums
        return f"{x1},{y1} {x2},{y1} {x2},{y2} {x1},{y2}"
    if shape == "poly" and len(nums) >= 6 and len(nums) % 2 == 0:
        return " ".join(f"{nums[i]},{nums[i+1]}" for i in range(0, len(nums), 2))
    return None


def recolor_highlight(im: Image.Image, rgba: tuple[int, int, int, int]) -> Image.Image:
    src = im.convert("RGBA")
    pixels = list(src.getdata())
    tr, tg, tb, ta = rgba
    out_px = []
    for r, g, b, a in pixels:
        if a == 0:
            out_px.append((0, 0, 0, 0))
        elif g >= 100 and g > r + 40 and g > b + 40:
            out_px.append((tr, tg, tb, ta))
        elif (r, g, b) != (0, 0, 0):
            out_px.append((r, g, b, a))
        else:
            out_px.append((0, 0, 0, 0))
    out = Image.new("RGBA", src.size, (0, 0, 0, 0))
    out.putdata(out_px)
    return out


def build_fill_layer(floor_key: str, meta: dict, visited: set[str], dest: Path) -> None:
    w, h = int(meta["width"]), int(meta["height"])
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    highlights = meta["highlights"]
    src_dir = ASSET_SRC / floor_key
    for area in meta["areas"]:
        ref = area["ref"]
        rel = highlights.get(ref)
        if not rel:
            continue
        gif_path = src_dir / Path(rel).name
        if not gif_path.exists():
            print(f"missing highlight {gif_path}")
            continue
        code = er_name_to_code(area["name"])
        color = RED if (code and code in visited) else GREEN
        canvas.alpha_composite(recolor_highlight(Image.open(gif_path), color))
    dest.mkdir(parents=True, exist_ok=True)
    canvas.save(dest / "fills.png")
    shutil.copy2(src_dir / "map.gif", dest / "map.gif")
    stacked = Image.alpha_composite(canvas, Image.open(dest / "map.gif").convert("RGBA"))
    stacked.save(dest / "preview_stacked.png")


def render_floor(
    key: str,
    meta: dict,
    *,
    visited: set[str],
    pickups: dict[str, set[str]],
    catalog: dict[str, list[dict]],
) -> str:
    w, h = int(meta["width"]), int(meta["height"])
    floor_title = FLOOR_TITLES.get(key, meta.get("title") or key)
    rel_fills = f"er_maps/{key}/fills.png"
    rel_map = f"er_maps/{key}/map.gif"
    map_name = meta["map_id"]

    hit_polys = []
    visited_names: list[str] = []
    unvisited_names: list[str] = []

    for area in meta["areas"]:
        name = area["name"]
        code = er_name_to_code(name)
        is_visited = bool(code and code in visited)
        picked, never, extras = room_item_breakdown(
            code, pickups=pickups, catalog=catalog
        )
        if code is None:
            status = "unmapped"
            unvisited_names.append(f"{name} [?]")
        elif is_visited:
            status = "visited"
            visited_names.append(f"{name} [{code}]")
        else:
            status = "unvisited"
            unvisited_names.append(f"{name} [{code}]")

        tip = {
            "name": name,
            "code": code or "",
            "status": status,
            "picked": picked,
            "never": never,
            "extras": extras,
        }
        tip_attr = html_lib.escape(json.dumps(tip, ensure_ascii=False), quote=True)

        pts = coords_to_svg_points(area["shape"], area["coords"])
        href = area.get("href") or meta.get("page_url") or "/resident-evil/maps/mansion"
        if href.startswith("/"):
            href = "https://www.evilresource.com" + href
        if pts:
            hit_polys.append(
                f'<a href="{html_lib.escape(href)}" target="_blank" rel="noopener" '
                f'class="room-link" data-tip="{tip_attr}">'
                f'<polygon points="{pts}" fill="transparent" '
                f'style="pointer-events:auto;cursor:pointer"/>'
                f"</a>"
            )

    page_url = meta.get("page_url") or "https://www.evilresource.com/resident-evil/maps"
    return f"""
  <section class="floor" id="{key}">
    <h3>{html_lib.escape(floor_title)}
      <a class="er-link" href="{html_lib.escape(page_url)}" target="_blank" rel="noopener">ER</a>
    </h3>
    <div class="mapbox">
      <div class="map-wrap" style="width:{w}px;height:{h}px">
        <img class="fills" src="{rel_fills}" width="{w}" height="{h}" alt="" draggable="false">
        <img class="base-map" src="{rel_map}" width="{w}" height="{h}"
             usemap="#{map_name}" alt="{html_lib.escape(floor_title)}" draggable="false">
        <svg class="overlay" viewBox="0 0 {w} {h}" width="{w}" height="{h}"
             xmlns="http://www.w3.org/2000/svg" style="pointer-events:none">
          {"".join(hit_polys)}
        </svg>
      </div>
    </div>
    <div class="stats">
      <div><strong class="red">Visited ({len(visited_names)}):</strong> {html_lib.escape(", ".join(visited_names) or "—")}</div>
      <div><strong class="green">Unvisited ({len(unvisited_names)}):</strong> {html_lib.escape(", ".join(unvisited_names) or "—")}</div>
    </div>
  </section>
"""


def main() -> None:
    if not CATALOG.exists():
        raise SystemExit(
            f"Missing {CATALOG}. Run: python D:/re1_rl/scripts/download_er_maps.py"
        )
    catalog_maps = json.loads(CATALOG.read_text(encoding="utf-8"))
    visited = load_visited()
    pickups = load_pickups()
    room_catalog = load_room_catalog()
    ASSET_DST.mkdir(parents=True, exist_ok=True)

    mapped_codes: set[str] = set()
    sections_html: list[str] = []
    for section_title, keys in SECTION_ORDER:
        blocks = []
        for key in keys:
            if key not in catalog_maps:
                print("missing floor in catalog", key)
                continue
            meta = catalog_maps[key]
            for area in meta["areas"]:
                code = er_name_to_code(area["name"])
                if code:
                    mapped_codes.add(code)
            print("compositing", key, "...")
            build_fill_layer(key, meta, visited, ASSET_DST / key)
            blocks.append(
                render_floor(
                    key,
                    meta,
                    visited=visited,
                    pickups=pickups,
                    catalog=room_catalog,
                )
            )
        if blocks:
            sections_html.append(
                f'<section class="area"><h2>{html_lib.escape(section_title)}</h2>'
                + "".join(blocks)
                + "</section>"
            )

    orphan = sorted(visited - mapped_codes)
    orphan_html = ""
    if orphan:
        orphan_html = (
            "<p class='orphan'><strong>Visited in logs but no ER polygon:</strong> "
            + ", ".join(orphan)
            + "</p>"
        )

    n_floors = sum(1 for _, keys in SECTION_ORDER for k in keys if k in catalog_maps)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>RE1 visit map — all Evil Resource floors</title>
<style>
  :root {{
    --bg: #0a0a0a;
    --panel: #121212;
    --text: #f3f3f3;
    --muted: #9a9a9a;
    --red: #c62828;
    --green: #009900;
    --tip: #1e1e1e;
  }}
  body {{
    margin: 0; padding: 20px 24px 48px;
    background: var(--bg); color: var(--text);
    font-family: Tahoma, "Segoe UI", sans-serif;
  }}
  h1 {{ margin: 0 0 6px; font-size: 1.45rem; }}
  h2 {{
    margin: 28px 0 12px; font-size: 1.25rem;
    border-bottom: 1px solid #333; padding-bottom: 6px; color: #eee;
  }}
  h3 {{ margin: 0 0 10px; font-size: 1.05rem; color: #ddd; }}
  .er-link {{
    font-size: 0.75rem; margin-left: 8px; color: #8ab4ff;
    text-decoration: none; border: 1px solid #456; padding: 1px 6px; border-radius: 3px;
  }}
  .sub {{ color: var(--muted); max-width: 80ch; line-height: 1.45; margin: 0 0 14px; }}
  .sub a {{ color: #8ab4ff; }}
  .legend {{
    display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
    margin: 0 0 18px; font-size: 0.95rem;
  }}
  .swatch i {{
    display: inline-block; width: 16px; height: 16px; border-radius: 2px;
    vertical-align: -3px; margin-right: 6px;
  }}
  .toc {{
    display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 20px;
  }}
  .toc a {{
    color: #cde; text-decoration: none; font-size: 0.85rem;
    border: 1px solid #333; padding: 4px 8px; border-radius: 4px; background: #181818;
  }}
  .toc a:hover {{ border-color: #666; }}
  .floor {{
    background: var(--panel);
    border: 1px solid #2c2c2c;
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 16px;
    overflow-x: auto;
  }}
  .mapbox {{
    position: relative;
    display: inline-block;
    border: 2px solid #505050;
    background: #000;
    padding: 10px;
  }}
  .map-wrap {{
    position: relative;
    image-rendering: pixelated;
    background: #000;
  }}
  .fills, .base-map {{
    position: absolute; left: 0; top: 0;
    display: block; image-rendering: pixelated;
  }}
  .fills {{ z-index: 1; }}
  .base-map {{ z-index: 2; }}
  .overlay {{ position: absolute; left: 0; top: 0; z-index: 3; }}
  .stats {{
    margin-top: 12px; font-size: 0.85rem; color: var(--muted);
    line-height: 1.4; display: grid; gap: 6px;
  }}
  .red {{ color: #ff8a80; }}
  .green {{ color: #69f0ae; }}
  .orphan {{ color: #ffcc80; font-size: 0.9rem; }}
  code {{ color: #ffd27a; }}
  #tip {{
    position: fixed; z-index: 1000; display: none;
    min-width: 220px; max-width: 340px;
    background: var(--tip); color: #f5f5f5;
    border: 1px solid #555; border-radius: 8px;
    padding: 10px 12px; box-shadow: 0 8px 24px rgba(0,0,0,.55);
    pointer-events: none; font-size: 0.86rem; line-height: 1.35;
  }}
  #tip h4 {{ margin: 0 0 6px; font-size: 0.95rem; }}
  #tip .status {{ font-size: 0.8rem; margin-bottom: 8px; }}
  #tip .status.visited {{ color: #ff8a80; }}
  #tip .status.unvisited, #tip .status.unmapped {{ color: #69f0ae; }}
  #tip .block {{ margin-top: 8px; }}
  #tip .block strong {{ display: block; margin-bottom: 3px; color: #bbb; font-size: 0.78rem; text-transform: uppercase; letter-spacing: .03em; }}
  #tip ul {{ margin: 0; padding-left: 1.1em; }}
  #tip li {{ margin: 1px 0; }}
  #tip .empty {{ color: #777; font-style: italic; }}
</style>
</head>
<body>
  <h1>Resident Evil — all Evil Resource maps</h1>
  <p class="sub">
    All {n_floors} interactive floors from
    <a href="https://www.evilresource.com/resident-evil/maps" target="_blank" rel="noopener">Evil Resource</a>
    (authentic <code>map.gif</code> + room fills).
    <strong class="red">Red = visited</strong>,
    <strong class="green">green = unvisited</strong>.
    Hover a room for pickups logged there vs items still never taken (from <code>room_items.json</code>).
  </p>
  <div class="legend">
    <span class="swatch"><i style="background:var(--red)"></i>Visited</span>
    <span class="swatch"><i style="background:var(--green)"></i>Unvisited / untouched</span>
    <span>Visited rooms: <strong>{len(visited)}</strong></span>
    <span>Rooms with pickups logged: <strong>{len(pickups)}</strong></span>
  </div>
  <nav class="toc">
    {"".join(f'<a href="#{k}">{FLOOR_TITLES.get(k, k)}</a>' for _, keys in SECTION_ORDER for k in keys if k in catalog_maps)}
  </nav>
  {orphan_html}
  {"".join(sections_html)}
  <div id="tip"></div>
<script>
(function () {{
  const tip = document.getElementById('tip');
  function renderList(items) {{
    if (!items || !items.length) return '<div class="empty">none</div>';
    return '<ul>' + items.map(i => '<li>' + i + '</li>').join('') + '</ul>';
  }}
  function show(e, data) {{
    const statusCls = data.status || '';
    const code = data.code ? ' [' + data.code + ']' : '';
    let extras = '';
    if (data.extras && data.extras.length) {{
      extras = '<div class="block"><strong>Also logged (not in room catalog)</strong>'
        + renderList(data.extras) + '</div>';
    }}
    tip.innerHTML =
      '<h4>' + data.name + code + '</h4>' +
      '<div class="status ' + statusCls + '">' + statusCls + '</div>' +
      '<div class="block"><strong>Picked up in this room</strong>' + renderList(data.picked) + '</div>' +
      '<div class="block"><strong>Never picked up from this room</strong>' + renderList(data.never) + '</div>' +
      extras;
    tip.style.display = 'block';
    move(e);
  }}
  function move(e) {{
    const pad = 14;
    let x = e.clientX + pad;
    let y = e.clientY + pad;
    const rect = tip.getBoundingClientRect();
    if (x + rect.width > window.innerWidth - 8) x = e.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 8) y = e.clientY - rect.height - pad;
    tip.style.left = Math.max(8, x) + 'px';
    tip.style.top = Math.max(8, y) + 'px';
  }}
  function hide() {{ tip.style.display = 'none'; }}
  document.querySelectorAll('a.room-link').forEach(a => {{
    a.addEventListener('mouseenter', e => {{
      try {{ show(e, JSON.parse(a.dataset.tip)); }} catch (err) {{}}
    }});
    a.addEventListener('mousemove', move);
    a.addEventListener('mouseleave', hide);
  }});
}})();
</script>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"floors={n_floors} visited={len(visited)} pickup_rooms={len(pickups)}")
    if orphan:
        print("orphan", orphan)


if __name__ == "__main__":
    main()
    try:
        webbrowser.open(OUT.as_uri())
    except Exception:
        pass
