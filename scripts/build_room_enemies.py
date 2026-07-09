#!/usr/bin/env python3
"""Build data/room_enemies.json from Evil Resource Jill (standard) enemy locations."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "room_enemies.json"
ROOMS_JSON = ROOT / "data" / "rooms.json"

# Evil Resource display name -> rooms.json code (Jill standard / Director's Cut)
ER_TO_ROOM: dict[str, str] = {
  # Mansion 1F
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
  "East Stairway 1F": "101",
  "Mansion Storeroom": "11B",
  "Store Room": "11B",
  "Central Corridor": "103",
  "Greenhouse": "10C",
  "Keeper's Bedroom": "10E",
  "Keeper's Room": "10E",
  "Vacant Room": "102",
  "Mansion Save Room": "100",
  "West Stairway 1F": "10B",
  "Bar": "10F",
  "Piano Bar": "10F",
  "Dressing Room": "111",
  "Wardrobe": "112",
  "Bathroom": "113",
  "Isolated Passage": "118",
  "Elevator Stairway": "118",
  "Wardrobe Closet": "11C",
  "Outside Boiler": "114",
  # Mansion 2F
  "Main Hall 2F": "203",
  "East Stairway 2F": "201",
  "'C' Passage": "204",
  "Terrace Entry": "211",
  "Terrace": "212",
  "Dining Room 2F": "202",
  "West Stairway 2F": "207",
  "Armor Room": "205",
  "Pillar Passage": "20D",
  "Attic Entry": "20E",
  "Attic": "210",
  "Deer Room": "208",
  "Study": "20A",
  "Bedroom": "209",
  "Lesson Room Entry": "20B",
  "Lesson Room": "20C",
  "Lesson Space": "20C",
  "Small Dining Room": "20F",
  "Elevator 2F": "213",
  "Rough Passage": "214",
  "Trophy Room": "215",
  "Large Library": "216",
  "Library B": "217",
  "Private Library": "217",
  "Small Library": "218",
  "Hidden Library": "218",
  "Closet": "219",
  "Heliport Lookout": "212",
  # Mansion B1
  "Underground Passage 1": "21A",
  "Underground Passage 2": "21B",
  "Kitchen": "21C",
  # Courtyard
  "Courtyard Garden": "300",
  "Guardhouse Gate": "304",
  "Fountain": "305",
  "Heliport": "303",
  "Water Gate": "301",
  "Falls": "302",
  # Underground / caves
  "Generator Room": "30F",
  "Black Tiger Room": "30C",
  "Underground Save Room": "30E",
  "Item Chamber": "306",
  "Enrico Room": "30A",
  "Boulder Passage 1": "30D",
  "Boulder Passage 2": "30B",
  "Branched Passage": "308",
  "Underground Entry": "310",
  "Straight Passage": "30D",
  "Darkness Passage": "309",
  # Guardhouse
  "Guardhouse Entry": "400",
  "Guardhouse Save Room": "403",
  "Room 001": "401",
  "Room 001 Bathroom": "402",
  "001 Bathroom": "402",
  "Room 002": "406",
  "Room 002 Bathroom": "407",
  "Beehive Passage": "408",
  "Drug Storeroom": "409",
  "Rec Room": "404",
  "Plant 42 Room": "40C",
  "Plant Roots Room": "40E",
  "Room 003": "40A",
  "Room 003 Bathroom": "40B",
  "003 Bathroom": "40B",
  "Arms Storage": "410",
  "Arms Storehouse": "410",
  "Basement Storeroom": "410",
  "Control Room": "411",
  "Water Tank": "40E",
  "Water Tank Entry": "40D",
  "Meeting Room": "40F",
  "Security Room": "40F",
  "Center Passage": "405",
  # Lab
  "Laboratory Entry": "500",
  "Emergency Tunnel": "500",
  "Under Fountain": "500",
  "Ladder Room": "502",
  "Stairway": "503",
  "Visual Data Room": "504",
  "Small Lab": "506",
  "Small Laboratory": "506",
  "Morgue": "507",
  "Mortuary": "507",
  "Lab Save Room": "50E",
  "Power Room": "510",
  "Power Maze 1": "50F",
  "Power Maze 2": "510",
  "'O' Room": "505",
  "O Room": "505",
  "Private Room": "509",
  "Private Room A": "509",
  "Private Room B": "50A",
  "Private Corridor": "505",
  "Cell": "512",
  "Cell Entry": "50B",
  "Front of Cell": "50B",
  "Elevator Entry": "50C",
  "Front of Elevator": "50C",
  "Operating Room": "511",
  "Movie Room": "511",
  "X-Ray Room": "508",
  "Main Lab": "513",
  "Tyrant Room": "513",
  "Main Lab Entry": "514",
  "Front of Tyrant": "514",
  "Heliport Passage": "501",
  "Double Lock": "508",
  "Conference Room": "504",
}

ENEMY_TYPES = frozenset({
  "zombie", "cerberus", "crow", "hunter", "spider", "snake_yawn",
  "plant42", "wasp", "chimera", "tyrant", "shark", "black_tiger",
})

SPAWN_TRIGGERS = frozenset({"always", "event", "cutscene", "return_visit"})

# Evil Resource display / slug fragment -> canonical enemy_type
ENEMY_META: dict[str, str] = {
  "Zombie": "zombie",
  "Zombies": "zombie",
  "Cerberus": "cerberus",
  "Cerberuses": "cerberus",
  "Zombie Dog": "cerberus",
  "Zombie Dogs": "cerberus",
  "Crow": "crow",
  "Crows": "crow",
  "Hunter": "hunter",
  "Hunters": "hunter",
  "Web Spinner": "spider",
  "Web Spinners": "spider",
  "Spider": "spider",
  "Yawn": "snake_yawn",
  "Snake Yawn": "snake_yawn",
  "Plant 42": "plant42",
  "Plant42": "plant42",
  "Wasp": "wasp",
  "Wasps": "wasp",
  "Hornet": "wasp",
  "Hornets": "wasp",
  "Chimera": "chimera",
  "Chimeras": "chimera",
  "Tyrant": "tyrant",
  "Tyrant T-002": "tyrant",
  "Neptune": "shark",
  "Shark": "shark",
  "Black Tiger": "black_tiger",
}

ENEMY_SLUGS: list[tuple[str, str]] = [
  ("zombie", "Zombie"),
  ("cerberus", "Cerberus"),
  ("crow", "Crow"),
  ("hunter", "Hunter"),
  ("web-spinner", "Web Spinner"),
  ("yawn", "Yawn"),
  ("plant-42", "Plant 42"),
  ("hornet", "Hornet"),
  ("chimera", "Chimera"),
  ("tyrant", "Tyrant"),
  ("neptune", "Neptune"),
  ("black-tiger", "Black Tiger"),
]


def fetch(url: str, retries: int = 3) -> str:
  req = urllib.request.Request(url, headers={"User-Agent": "re1_rl/1.0"})
  for attempt in range(retries):
    try:
      with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
      if attempt + 1 == retries:
        raise
      time.sleep(2 ** attempt)
  raise RuntimeError("unreachable")


def parse_enemy_page(html: str, default_enemy: str) -> list[tuple[str, str, int, str, str]]:
  """Return list of (er_room, enemy_display, count, spawn_trigger, notes)."""
  results: list[tuple[str, str, int, str, str]] = []
  blocks = re.split(r"####\s+", html)
  for block in blocks[1:]:
    header = block.split("\n", 1)[0].strip()
    m = re.match(
      r"(.+?)\s+-\s+(Mansion 1F|Mansion 2F|Mansion B1|Courtyard|Underground|Guardhouse 1F|Guardhouse B1|Laboratory B1|Laboratory B2|Laboratory B3|Laboratory B4)",
      header,
    )
    if not m:
      continue
    er_room = m.group(1).strip()
    body = block
    jm = re.search(
      r"^Jill\s*\n\s*(.+?)(?=\n####|\nJill \(|\nChris|\nThere are no|\Z)",
      body,
      re.DOTALL | re.MULTILINE,
    )
    if not jm:
      continue
    desc = re.sub(r"<[^>]+>", "", jm.group(1))
    desc = re.sub(r"\s+", " ", desc).strip()
    desc = desc.split("View location")[0].strip()
    if not desc:
      continue
    count = 1
    enemy_name = default_enemy
    spawn_trigger = "always"
    cm = re.match(r"(\d+)\s+(.+?)\s*-\s*(.*)", desc)
    if cm:
      count = int(cm.group(1))
      enemy_name = cm.group(2).strip()
      desc = cm.group(3).strip()
    else:
      cm2 = re.match(r"(.+?)s?\s*\((\d+)×\d+\)\s*-\s*(.*)", desc)
      if cm2:
        enemy_name = cm2.group(1).strip()
        count = int(cm2.group(2))
        desc = cm2.group(3).strip()
    lower = desc.lower()
    if "cutscene" in lower or "scene" in lower:
      spawn_trigger = "cutscene"
    elif "return" in lower or "revisit" in lower or "second time" in lower:
      spawn_trigger = "return_visit"
    elif "event" in lower or "first time" in lower or "after" in lower:
      spawn_trigger = "event"
    results.append((er_room, enemy_name, count, spawn_trigger, desc))
  return results


def resolve_enemy_type(display: str) -> str | None:
  if display in ENEMY_META:
    return ENEMY_META[display]
  low = display.lower()
  for k, v in ENEMY_META.items():
    if k.lower() in low or low in k.lower():
      return v
  return None


def make_enemy_entry(
  display: str,
  count: int,
  spawn_trigger: str,
  notes: str,
  *,
  unverified: bool,
) -> dict | None:
  enemy_type = resolve_enemy_type(display)
  if not enemy_type or enemy_type not in ENEMY_TYPES:
    return None
  if spawn_trigger not in SPAWN_TRIGGERS:
    spawn_trigger = "always"
  entry: dict = {
    "enemy_type": enemy_type,
    "count": count,
    "spawn_trigger": spawn_trigger,
    "notes": notes,
  }
  if unverified:
    entry["unverified"] = True
  return entry


def add_enemy(room_data: dict, entry: dict) -> None:
  enemies = room_data["enemies"]
  for ex in enemies:
    if ex["enemy_type"] == entry["enemy_type"] and ex["spawn_trigger"] == entry["spawn_trigger"]:
      ex["count"] = max(ex["count"], entry["count"])
      if entry.get("notes") and entry["notes"] not in ex.get("notes", ""):
        ex["notes"] = (ex.get("notes", "") + "; " + entry["notes"]).strip("; ")
      if entry.get("unverified"):
        ex["unverified"] = True
      return
  enemies.append(entry)


def main() -> None:
  rooms_meta = json.loads(ROOMS_JSON.read_text(encoding="utf-8"))
  room_enemies: dict[str, dict] = {}
  for code, info in rooms_meta.items():
    room_enemies[code] = {"room_name": info["name"], "enemies": []}

  scraped: list[tuple[str, str, int, str, str, bool]] = []
  base = "https://www.evilresource.com/resident-evil"
  fetch_errors: list[str] = []
  scrape_ok = False

  for slug, display in ENEMY_SLUGS:
    url = f"{base}/enemies/{slug}"
    try:
      html = fetch(url)
      for row in parse_enemy_page(html, display):
        scraped.append((*row, False))
      scrape_ok = True
      time.sleep(0.3)
    except Exception as e:
      fetch_errors.append(f"{url}: {e}")

  # Manual seed: well-established Jill mansion / route-critical spawns only.
  manual: list[tuple[str, str, int, str, str]] = [
    # Mansion 1F — early route
    ("Tea Room", "Zombie", 1, "cutscene", "Kenneth Sullivan body; first zombie encounter"),
    ("Main Hall 1F", "Zombie", 2, "return_visit", "hallway zombies after mansion revisit"),
    ("Dining Room", "Zombie", 1, "return_visit", "zombie enters from kitchen door on revisit"),
    ("Art Room", "Zombie", 1, "return_visit", "zombie in statue alcove area"),
    ("'L' Passage", "Zombie", 1, "always", "zombie in L-shaped passage"),
    ("Winding Passage", "Zombie", 1, "always", "zombie en route to trap room"),
    ("Trap Room", "Zombie", 2, "always", "zombies; ceiling trap over shotgun rack"),
    ("Living Room", "Zombie", 2, "always", "zombies near wall shotgun rack"),
    ("Large Gallery", "Crow", 2, "always", "crows; star crest puzzle room"),
    ("Art Room", "Crow", 2, "always", "crows in gallery (107); adjacent to art room"),
    ("Central Corridor", "Zombie", 1, "return_visit", "zombie in F passage"),
    ("East Stairway 1F", "Zombie", 1, "return_visit", "zombie on east stairs landing"),
    ("Back Passage", "Zombie", 1, "return_visit", "zombie in back passage"),
    ("Greenhouse", "Zombie", 1, "return_visit", "zombie in greenhouse after crest puzzle"),
    ("Keeper's Room", "Zombie", 1, "return_visit", "zombie in employee/keeper bedroom"),
    ("Bar", "Zombie", 1, "return_visit", "zombie in piano bar"),
    ("Roofed Passage", "Zombie", 1, "always", "zombie in roofed outdoor passage"),
    # Courtyard — cerberus
    ("Courtyard Garden", "Cerberus", 2, "event", "dogs attack on first courtyard entry"),
    ("Guardhouse Gate", "Cerberus", 2, "always", "cerberus at guardhouse entrance"),
    # Mansion 2F
    ("Attic", "Yawn", 1, "event", "Yawn boss fight (first snake encounter)"),
    ("Terrace", "Zombie", 1, "cutscene", "Forest Speyer corpse event (not a combat spawn)"),
    ("Main Hall 2F", "Zombie", 2, "return_visit", "2F hall zombies after revisit"),
    ("Pillar Passage", "Zombie", 1, "return_visit", "zombie near Richard body area"),
    # Residence — post-power hunters & wasps
    ("Guardhouse Entry", "Hunter", 1, "return_visit", "hunter in entry passage after power returns"),
    ("Center Passage", "Hunter", 1, "return_visit", "hunter in dorm center corridor"),
    ("Room 002", "Hunter", 1, "return_visit", "hunter in room 002"),
    ("Rec Room", "Hunter", 1, "return_visit", "hunter in recreation room"),
    ("Room 003", "Hunter", 2, "return_visit", "hunters in room 003"),
    ("Beehive Passage", "Wasp", 1, "always", "wasp nest blocks passage to 002 key"),
    ("Plant 42 Room", "Plant 42", 1, "event", "Plant 42 boss"),
    # Underground
    ("Black Tiger Room", "Black Tiger", 1, "event", "Black Tiger boss in underground cavern"),
    ("Darkness Passage", "Hunter", 1, "always", "hunter in darkness passage (underground)"),
    # Lab
    ("Tyrant Room", "Tyrant", 1, "event", "Tyrant T-002 boss"),
    ("Morgue", "Chimera", 1, "return_visit", "chimera in morgue"),
    ("Power Maze 1", "Chimera", 1, "always", "chimera in power maze"),
  ]

  for er_room, display, count, trigger, notes in manual:
    scraped.append((er_room, display, count, trigger, notes, True))

  unmatched: list[dict] = []
  for er_room, display, count, trigger, notes, unverified in scraped:
    code = ER_TO_ROOM.get(er_room)
    entry = make_enemy_entry(display, count, trigger, notes, unverified=unverified)
    if not code:
      if entry:
        unmatched.append({
          "er_room": er_room,
          "enemy_type": entry["enemy_type"],
          "count": count,
          "notes": notes,
        })
      else:
        unmatched.append({"er_room": er_room, "enemy": display, "count": count, "notes": notes})
      continue
    if not entry:
      unmatched.append({"er_room": er_room, "enemy": display, "count": count, "notes": notes})
      continue
    add_enemy(room_enemies[code], entry)

  source = (
    "evilresource.com + manual Jill walkthrough transcription"
    if scrape_ok
    else "manual Jill walkthrough transcription (ER scrape failed)"
  )

  out: dict = {
    "_meta": {
      "source": source,
      "scenario": "jill",
      "game": "RE1 Director's Cut PS1 standard Jill",
      "date": "2026-07-04",
      "fetch_errors": fetch_errors[:20],
      "notes": "positions intentionally absent — come from RAM/RDT only",
      "scrape_ok": scrape_ok,
      "unmatched_count": len(unmatched),
    },
  }

  def sort_key(x: str) -> tuple[int, int]:
    return (int(x[:1], 16), int(x[1:], 16) if len(x) > 1 else 0)

  for code in sorted(room_enemies.keys(), key=sort_key):
    out[code] = room_enemies[code]

  OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
  n_enemies = sum(len(v["enemies"]) for k, v in out.items() if not k.startswith("_"))
  n_rooms_with = sum(
    1 for k, v in out.items() if not k.startswith("_") and v["enemies"]
  )
  print(f"Wrote {OUT}")
  print(f"rooms with enemies: {n_rooms_with}, total enemy entries: {n_enemies}")
  print(f"scrape_ok: {scrape_ok}, fetch_errors: {len(fetch_errors)}, unmatched: {len(unmatched)}")


if __name__ == "__main__":
  main()
