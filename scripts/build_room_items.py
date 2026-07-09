#!/usr/bin/env python3
"""Build data/room_items.json from Evil Resource Jill (standard) item locations."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "room_items.json"
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
    "Tiger Statue Room": "10D",
    "Outside Boiler": "114",
    "Bathroom": "113",
    "Isolated Passage": "118",
    # "Courtyard Study" intentionally unmapped: no confident debug code in
    # rooms.json (10A is BACK PASSAGE, the corridor it connects to). Items
    # there flow to _unmatched for human verification.
    "Elevator Stairway": "118",
    "Wardrobe Closet": "11C",
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
    "Boulder Room 1": "30D",
    "Boulder Room 2": "30B",
    "Branched Passage": "308",
    "Underground Entry": "310",
    "Straight Passage": "30D",
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

# Evil Resource / display name -> (snake_name, item_id or None, key_item, in_inventory_table)
ITEM_META: dict[str, tuple[str, int | None, bool, bool]] = {
    "Emblem": ("emblem", 0x1F, True, True),
    "Gold Emblem": ("gold_emblem", 0x20, True, True),
    "Shield Key": ("shield_key", 0x35, True, True),
    "Armor Key": ("armor_key", 0x34, True, True),
    "Sword Key": ("sword_key", 0x33, True, True),
    "Helmet Key": ("helmet_key", 0x36, True, True),
    "Blue Jewel": ("blue_jewel", 0x21, True, True),
    "Red Jewel": ("red_jewel", 0x22, True, True),
    "Wind Crest": ("wind_crest", 0x29, True, True),
    "Star Crest": ("star_crest", 0x2D, True, True),
    "Sun Crest": ("sun_crest", 0x2E, True, True),
    "Moon Crest": ("moon_crest", 0x2C, True, True),
    "Square Crank": ("square_crank", 0x1D, True, True),
    "Hex Crank": ("hex_crank", 0x1E, True, True),
    "Hexagonal Crank": ("hex_crank", 0x1E, True, True),
    "Music Notes": ("music_notes", 0x23, True, True),
    "Lighter": ("lighter", 0x30, True, True),
    "Lockpick": ("lockpick", 0x31, True, True),
    "Herbicide": ("chemical", 0x26, True, True),
    "Chemical": ("chemical", 0x26, True, True),
    "Battery": ("battery", 0x27, True, True),
    "MO Disk": ("mo_disc", 0x28, True, True),
    "Doom Book 1": ("doom_book_1", 0x40, True, True),
    "Doom Book 2": ("doom_book_2", 0x3F, True, True),
    "Red Book": ("red_book", 0x3E, True, True),
    "Wolf Medal": ("wolf_medal", 0x24, True, True),
    "Eagle Medal": ("eagle_medal", 0x25, True, True),
    "002 Key": ("dorm_key_002", 0x39, True, True),
    "002 Dormitory Key": ("dorm_key_002", 0x39, True, True),
    "003 Key": ("dorm_key_003", 0x3A, True, True),
    "003 Dormitory Key": ("dorm_key_003", 0x3A, True, True),
    "Control Room Key": ("control_room_key", 0x3B, True, True),
    "C. Room Key": ("control_room_key", 0x3B, True, True),
    "Power Room Key": ("power_room_key", 0x37, True, True),
    "Passcode A": ("passcode_a", None, True, False),
    "Passcode B": ("passcode_b", None, True, False),
    "Empty Bottle": ("empty_bottle", 0x13, True, True),
    "Water": ("water", 0x14, True, True),
    "UMB No.2": ("umb_no2", 0x15, True, True),
    "UMB No.4": ("umb_no4", 0x16, True, True),
    "UMB No.7": ("umb_no7", 0x17, True, True),
    "UMB No.13": ("umb_no13", 0x18, True, True),
    "NP-003": ("n_p003", 0x1A, True, True),
    "Yellow-6": ("yellow_6", 0x19, True, True),
    "V-JOLT": ("v_jolt", 0x1B, True, True),
    "Flare": ("flare", 0x2A, True, True),
    "Slides": ("slides", 0x2B, True, True),
    "Serum": ("serum", 0x42, True, True),
    "Broken Shotgun": ("broken_shotgun", 0x1C, False, True),
    "Green Herb": ("green_herb", 0x44, False, True),
    "Red Herb": ("red_herb", 0x43, False, True),
    "Blue Herb": ("blue_herb", 0x45, False, True),
    "First Aid Spray": ("first_aid_spray", 0x0B, False, True),
    "Ink Ribbon": ("ink_ribbon", 0x2F, False, True),
    "Clip": ("clip", None, False, False),
    "Shells": ("shotgun_shells", 0x0C, False, True),
    "Shotgun Shells": ("shotgun_shells", 0x0C, False, True),
    "Acid Rounds": ("acid_rounds", 0x11, False, True),
    "Explosive Rounds": ("explosive_rounds", 0x10, False, True),
    "Flame Rounds": ("flame_rounds", 0x12, False, True),
    "Magnum Rounds": ("magnum_rounds", 0x0E, False, True),
    "Remington M870": ("shotgun", 0x03, False, True),
    "Shotgun": ("shotgun", 0x03, False, True),
    "Bazooka": ("bazooka_acid", 0x07, False, True),
    "Colt Python": ("colt_python", 0x05, False, True),
    "Rocket Launcher": ("rocket_launcher", 0x0A, False, True),
    "Combat Knife": ("knife", 0x01, False, True),
    "Map of the First Floor": ("map_1f", None, False, False),
    "Map of the Second Floor": ("map_2f", None, False, False),
    "Map of the Garden": ("map_garden", None, False, False),
    "Map of the Underground": ("map_underground", None, False, False),
    "Garden Map": ("map_garden", None, False, False),
    "Dormitory Map": ("map_dormitory", None, False, False),
    "Botany Book": ("botany_book", None, False, False),
    "Keeper's Diary": ("keepers_diary", None, False, False),
    "Researcher's Will": ("researchers_will", None, False, False),
    "Orders": ("orders", None, False, False),
    "Scrapbook": ("scrapbook", None, False, False),
    "Pass Number": ("pass_number", None, True, False),
    "Plant 42 Report": ("plant_42_report", None, False, False),
    "V-JOLT Report": ("v_jolt_report", None, False, False),
}

ITEM_SLUGS = [
    "emblem", "gold-emblem", "shield-key", "armor-key", "sword-key", "helmet-key",
    "blue-jewel", "red-jewel", "wind-crest", "star-crest", "sun-crest", "moon-crest",
    "square-crank", "hex-crank", "music-notes", "lighter", "lockpick", "herbicide",
    "battery", "mo-disk", "doom-book-1", "doom-book-2", "wolf-medal",
    "eagle-medal", "002-key", "control-room-key", "003-key", "power-room-key",
    "empty-bottle", "water", "umb-no2", "umb-no4", "umb-no7", "umb-no13", "np-003",
    "yellow-6", "v-jolt", "flare", "slides", "serum", "broken-shotgun",
    "green-herb", "red-herb", "blue-herb", "first-aid-spray", "ink-ribbon",
]

WEAPON_SLUGS = [
    ("remington-m870", "Remington M870"),
    ("bazooka", "Bazooka"),
    ("colt-python", "Colt Python"),
    ("rocket-launcher", "Rocket Launcher"),
    ("combat-knife", "Combat Knife"),
    ("clip", "Clip"),
    ("shells", "Shells"),
    ("acid-rounds", "Acid Rounds"),
    ("explosive-rounds", "Explosive Rounds"),
    ("flame-rounds", "Flame Rounds"),
    ("magnum-rounds", "Magnum Rounds"),
]

MISC_SLUGS = [
    ("map-of-the-first-floor", "Map of the First Floor"),
    ("map-of-the-second-floor", "Map of the Second Floor"),
    ("map-of-the-garden", "Map of the Garden"),
    ("map-of-the-underground", "Map of the Underground"),
]

FILE_SLUGS = [
    ("botany-book", "Botany Book"),
    ("keepers-diary", "Keeper's Diary"),
    ("researchers-will", "Researcher's Will"),
    ("orders", "Orders"),
    ("scrapbook", "Scrapbook"),
    ("pass-number", "Pass Number"),
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


def parse_jill_locations(html: str) -> list[tuple[str, str, int, str]]:
    """Return list of (er_room, item_display, count, notes)."""
    results: list[tuple[str, str, int, str]] = []
    # Split on location headers: #### Room - Area
    blocks = re.split(r"####\s+", html)
    for block in blocks[1:]:
        header_line = block.split("\n", 1)[0].strip()
        m = re.match(r"(.+?)\s+-\s+(Mansion 1F|Mansion 2F|Mansion B1|Courtyard|Underground|Guardhouse 1F|Guardhouse B1|Laboratory B1|Laboratory B2|Laboratory B3|Laboratory B4)", header_line)
        if not m:
            continue
        er_room = m.group(1).strip()
        # Find Jill standard (not Arranged, not Chris, not Deadly Silence)
        jill_pat = re.compile(
            r"####\s+(.+?)\s*\n\s*Jill\s*\n\s*(.+?)(?=\n####|\nThere are no|\Z)",
            re.DOTALL,
        )
        # Within this block, look for item sub-headers
        item_blocks = re.split(r"####\s+", block)
        for ib in item_blocks:
            lines = ib.strip().split("\n")
            if len(lines) < 2:
                continue
            item_name = lines[0].strip()
            if item_name == er_room or " - " in item_name:
                continue
            body = "\n".join(lines[1:])
            if not re.search(r"^Jill\s*$", body, re.MULTILINE):
                continue
            if re.search(r"Jill \(Arranged", body) and not re.match(r"^Jill\s*$", body.split("Jill (Arranged")[0].strip(), re.MULTILINE):
                # only standard Jill paragraph
                jm = re.search(r"^Jill\s*\n\s*(.+?)(?=\n####|\nJill \(|\nChris|\Z)", body, re.DOTALL | re.MULTILINE)
            else:
                jm = re.search(r"^Jill\s*\n\s*(.+?)(?=\n####|\nJill \(|\nChris|\Z)", body, re.DOTALL | re.MULTILINE)
            if not jm:
                continue
            desc = re.sub(r"<[^>]+>", "", jm.group(1))
            desc = re.sub(r"\s+", " ", desc).strip()
            if not desc or desc.startswith("View "):
                continue
            count = 1
            cm = re.match(r"(\d+)\s+(.+?)\s*-\s*(.*)", desc)
            if cm:
                count = int(cm.group(1))
                item_name = cm.group(2).strip()
                desc = cm.group(3).strip()
            else:
                cm2 = re.match(r"(.+?)\s*\(1×(\d+)\)", item_name)
                if cm2:
                    item_name = cm2.group(1).strip()
                    count = int(cm2.group(2))
            results.append((er_room, item_name, count, desc))
    return results


def parse_item_page(html: str, default_item: str) -> list[tuple[str, str, int, str]]:
    results: list[tuple[str, str, int, str]] = []
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
        jm = re.search(r"^Jill\s*\n\s*(.+?)(?=\n####|\nJill \(|\nChris|\nThere are no|\Z)", body, re.DOTALL | re.MULTILINE)
        if not jm:
            continue
        desc = re.sub(r"<[^>]+>", "", jm.group(1))
        desc = re.sub(r"\s+", " ", desc).strip()
        # Page boilerplate follows the description; cut it off instead of
        # discarding the whole entry.
        desc = desc.split("View location")[0].strip()
        if not desc:
            continue
        count = 1
        item_name = default_item
        cm = re.match(r"(\d+)\s+(.+?)\s*-\s*(.*)", desc)
        if cm:
            count = int(cm.group(1))
            item_name = cm.group(2).strip()
            desc = cm.group(3).strip()
        else:
            cm2 = re.match(r"(.+?)s?\s*\((\d+)×\d+\)\s*-\s*(.*)", desc)
            if cm2:
                item_name = cm2.group(1).strip()
                count = int(cm2.group(2))
                desc = cm2.group(3).strip()
        results.append((er_room, item_name, count, desc))
    return results


def make_item_entry(display: str, count: int, notes: str) -> dict | None:
    meta = ITEM_META.get(display)
    if not meta:
        # try partial
        for k, v in ITEM_META.items():
            if k.lower() in display.lower() or display.lower() in k.lower():
                meta = v
                break
    if not meta:
        return None
    name, iid, key_item, in_inv = meta
    return {
        "name": name,
        "item_id": iid,
        "count": count,
        "key_item": key_item,
        "in_inventory_table": in_inv,
        "notes": notes,
    }


def add_item(room_data: dict, entry: dict) -> None:
    items = room_data["items"]
    for ex in items:
        if ex["name"] == entry["name"]:
            ex["count"] = max(ex["count"], entry["count"])
            if entry.get("notes") and entry["notes"] not in ex.get("notes", ""):
                ex["notes"] = (ex.get("notes", "") + "; " + entry["notes"]).strip("; ")
            return
    items.append(entry)


def main() -> None:
    rooms_meta = json.loads(ROOMS_JSON.read_text(encoding="utf-8"))
    room_items: dict[str, dict] = {}
    for code, info in rooms_meta.items():
        room_items[code] = {"room_name": info["name"], "items": []}

    unmatched: list[dict] = []
    scraped: list[tuple[str, str, int, str]] = []

    base = "https://www.evilresource.com/resident-evil"
    urls: list[tuple[str, str]] = []
    for slug in ITEM_SLUGS:
        display = slug.replace("-", " ").title().replace("No2", "No.2").replace("No4", "No.4")
        display = display.replace("Np 003", "NP-003").replace("V Jolt", "V-JOLT")
        display = display.replace("002 Key", "002 Key").replace("003 Key", "003 Key")
        urls.append((f"{base}/items/{slug}", display))
    for slug, display in WEAPON_SLUGS:
        urls.append((f"{base}/weaponry/{slug}", display))
    for slug, display in MISC_SLUGS:
        urls.append((f"{base}/miscellaneous-objects/{slug}", display))
    for slug, display in FILE_SLUGS:
        urls.append((f"{base}/files/{slug}", display))

    fetch_errors: list[str] = []
    for url, display in urls:
        try:
            html = fetch(url)
            scraped.extend(parse_item_page(html, display))
            time.sleep(0.3)
        except Exception as e:
            fetch_errors.append(f"{url}: {e}")

    # Manual supplements from Evil Resource room pages + GameFAQs walkthrough (Jill DC standard)
    manual: list[tuple[str, str, int, str]] = [
        # route-critical pickups verified on room pages / walkthrough
        ("Dining Room", "Emblem", 1, "above fireplace; wooden emblem"),
        ("Dining Room", "Shield Key", 1, "behind grandfather clock after gold emblem"),
        ("Main Hall 1F", "Lockpick", 1, "Barry after first zombie"),
        ("Main Hall 1F", "Ink Ribbon", 2, "on typewriter"),
        ("Main Hall 1F", "Map of the First Floor", 1, "SE stairs alcove / art room map"),
        ("Main Hall 1F", "Acid Rounds", 6, "Barry gives if inventory space (2F return)"),
        ("Art Room", "Map of the First Floor", 1, "on statue after pushing ladder"),
        ("Art Room", "Ink Ribbon", 1, "behind movable drawers"),
        ("'L' Passage", "Clip", 1, "under display case"),
        ("Trap Room", "Shotgun", 1, "living room rack; Jill Barry rescue"),
        ("Large Gallery", "Star Crest", 1, "crow painting puzzle"),
        ("Mansion Storeroom", "Chemical", 1, "herbicide chemicals"),
        ("Mansion Storeroom", "Green Herb", 1, "on shelf"),
        ("Greenhouse", "Armor Key", 1, "after chemical on plant; on crest"),
        ("Greenhouse", "Green Herb", 4, "benches by fountain"),
        ("Keeper's Room", "Clip", 1, "on bed"),
        ("Keeper's Room", "Shotgun Shells", 1, "in closet"),
        ("Keeper's Room", "Keeper's Diary", 1, "on desk"),
        ("Vacant Room", "Clip", 1, "on shelf"),
        ("Vacant Room", "Shotgun Shells", 1, "desk drawer with lockpick"),
        ("Vacant Room", "Broken Shotgun", 1, "on wall"),
        ("Vacant Room", "Serum", 1, "save room shelf route uses serum from 100; also 102"),
        ("Mansion Save Room", "Serum", 1, "on shelf"),
        ("Bar", "Music Notes", 1, "behind movable bookcase"),
        ("Bar", "Gold Emblem", 1, "secret room after piano"),
        ("Dressing Room", "Clip", 1, "on shelf"),
        ("Dressing Room", "Shotgun Shells", 1, "locked desk"),
        ("Wardrobe", "Green Herb", 2, "SE corner"),
        ("Tiger Statue Room", "Wind Crest", 1, "blue jewel on tiger eye"),
        ("Armor Room", "Sun Crest", 1, "statue puzzle cabinet"),
        ("Pillar Passage", "Green Herb", 2, "behind pillar"),
        ("Attic", "Moon Crest", 1, "after Yawn 1"),
        ("Attic", "Shotgun Shells", 1, "barrel if avoiding Yawn"),
        ("Dining Room 2F", "Blue Jewel", 1, "push statue off balcony"),
        ("Terrace", "Bazooka", 1, "Forest Speyer corpse event"),
        ("Bedroom", "Red Herb", 1, "behind right bed"),
        ("Bedroom", "Clip", 1, "behind right bed"),
        ("Bedroom", "Lighter", 1, "on shelf"),
        ("Bedroom", "Shotgun Shells", 1, "optional"),
        ("Study", "Explosive Rounds", 1, "secret cabinet puzzle"),
        ("Study", "Ink Ribbon", 1, "lab coat rack"),
        ("Study", "Researcher's Will", 1, "on desk"),
        ("Lesson Room Entry", "Map of the Second Floor", 1, "fireplace with lighter"),
        ("Lesson Room Entry", "Green Herb", 1, "corner"),
        ("Small Dining Room", "Ink Ribbon", 1, "on table"),
        ("Small Dining Room", "Clip", 1, "left cabinet"),
        ("Small Dining Room", "Acid Rounds", 1, "secret bookcase room"),
        ("Trophy Room", "Shotgun Shells", 1, "on shelf"),
        ("Trophy Room", "Magnum Rounds", 1, "on shelf"),
        ("Trophy Room", "Red Jewel", 1, "deer head with lights off"),
        ("Trophy Room", "Orders", 1, "on table"),
        ("Closet", "Battery", 1, "on chair"),
        ("Closet", "Explosive Rounds", 2, "on shelf"),
        ("Large Library", "Scrapbook", 1, "on desk"),
        ("Library B", "MO Disk", 1, "desk after statue puzzle"),
        ("Library B", "MO Disk", 1, "first MO disk (Any% alt: security room)"),
        ("Elevator 2F", "Green Herb", 1, "alcove near elevator"),
        ("Courtyard Study", "Doom Book 1", 1, "bookshelf next to door; switch on lamp first (contains eagle_medal)"),
        ("Courtyard Study", "Magnum Rounds", 1, "optional desk"),
        ("Store Room", "Square Crank", 1, "top shelf via stepladder"),
        ("Courtyard Garden", "Green Herb", 3, "near SE double doors"),
        ("Courtyard Garden", "Red Herb", 2, "garden"),
        ("Courtyard Garden", "Blue Herb", 3, "garden"),
        ("Courtyard Garden", "Map of the Garden", 1, "near broken elevator"),
        ("Guardhouse Gate", "Green Herb", 2, "near gate"),
        ("Guardhouse Gate", "Blue Herb", 2, "near gate"),
        ("Fountain", "Green Herb", 2, "near welded doors"),
        ("Fountain", "Doom Book 2", 1, "fountain puzzle reward"),
        ("Falls", "Flare", 1, "Barry ending item"),
        ("Room 001", "Red Book", 1, "on bed"),
        ("Room 001", "Shotgun Shells", 1, "locked desk"),
        ("Room 001 Bathroom", "Control Room Key", 1, "drain bathtub"),
        ("Beehive Passage", "002 Dormitory Key", 1, "on table near wasp nest"),
        ("Central Corridor", "Green Herb", 3, "behind pushed statue"),
        ("Room 002", "Shotgun Shells", 1, "in desk"),
        ("Room 002", "Dormitory Map", 1, "on wall"),
        ("Room 002 Bathroom", "Clip", 1, "bathroom"),
        ("Water Tank Entry", "Green Herb", 2, "opposite water tank door"),
        ("Arms Storage", "003 Dormitory Key", 1, "sparkle on shelf"),
        ("Arms Storage", "Clip", 2, "on shelves (2x15)"),
        ("Arms Storage", "Shotgun Shells", 2, "on shelves"),
        ("'L' Passage", "Clip", 1, "under second cabinet (15 rounds)"),
        ("Tea Room", "Clip", 2, "on Kenneth body (2x15)"),
        ("East Stairway 1F", "Green Herb", 1, "next to stairs"),
        ("Winding Passage", "Green Herb", 1, "opposite outside boiler entrance"),
        ("Living Room", "Shotgun", 1, "wall rack; ceiling trap in adjacent 115"),
        ("Pillar Passage", "Clip", 1, "search Richard body after serum"),
        ("Main Hall 2F", "Acid Rounds", 6, "Barry after Forest scene if space"),
        ("Boulder Passage 2", "MO Disk", 1, "alcove after boulder"),
        ("Boulder Passage 2", "Map of the Underground", 1, "wall after boulder"),
        ("Guardhouse Entry", "Blue Herb", 1, "near entrance plants"),
        ("Elevator 2F", "MO Disk", 1, "security room alt; Library B is primary"),
        ("Double Lock", "Passcode A", 1, "aka pass_number; decoded on MO disc reader, not a physical pickup"),
        ("Double Lock", "Passcode B", 1, "aka pass_number; decoded on MO disc reader, not a physical pickup"),
        ("Emergency Tunnel", "Battery", 1, "second battery, floor next to heliport elevator"),
        ("X-Ray Room", "Clip", 2, "wooden box (2x15)"),
        ("X-Ray Room", "Green Herb", 1, "west side floor"),
        ("Drug Storeroom", "Empty Bottle", 3, "V-Jolt prep"),
        ("Drug Storeroom", "Water", 1, "sink"),
        ("Drug Storeroom", "UMB No.2", 1, "shelves"),
        ("Drug Storeroom", "UMB No.4", 1, "shelves"),
        ("Room 003 Bathroom", "Flame Rounds", 1, "zombie drop"),
        ("Room 003", "Ink Ribbon", 1, "locked desk"),
        ("Plant 42 Room", "Helmet Key", 1, "fireplace after boss"),
        ("Guardhouse Save Room", "First Aid Spray", 1, "on shelf"),
        ("Guardhouse Save Room", "Explosive Rounds", 1, "on shelf"),
        ("Rec Room", "Pass Number", 1, "pool table code 345 (Jill)"),
        ("Enrico Room", "Hex Crank", 1, "on body after scene"),
        ("Enrico Room", "Clip", 1, "search body twice"),
        ("Generator Room", "First Aid Spray", 1, "near generator"),
        ("Generator Room", "Explosive Rounds", 1, "near generator"),
        ("Boulder Passage 1", "Flame Rounds", 1, "after boulder trap"),
        ("Item Chamber", "MO Disk", 1, "second MO disk"),
        ("Item Chamber", "Explosive Rounds", 1, "on shelf"),
        ("Item Chamber", "First Aid Spray", 1, "on shelf"),
        ("Underground Save Room", "Ink Ribbon", 2, "save room"),
        ("Underground Save Room", "Green Herb", 1, "floor"),
        ("Lab Save Room", "Power Room Key", 1, "on shelf; ITEM_IDS 0x37 names this lab_key_1"),
        ("Lab Save Room", "Green Herb", 1, "next to bed"),
        ("Small Lab", "Wolf Medal", 1, "on desk"),
        ("Morgue", "Eagle Medal", 1, "inside coffin"),
        ("Power Maze 2", "Slides", 1, "projector room"),
        ("Cell Entry", "Flare", 1, "Barry good ending"),
        ("Main Lab Entry", "Rocket Launcher", 1, "Barry drops before Tyrant"),
        ("Heliport", "Rocket Launcher", 1, "Brad drop bad ending"),
    ]
    scraped.extend(manual)

    for er_room, display, count, notes in scraped:
        code = ER_TO_ROOM.get(er_room)
        entry = make_item_entry(display, count, notes)
        if not code:
            if entry:
                unmatched.append({"er_room": er_room, "item": entry["name"], "count": count, "notes": notes})
            else:
                unmatched.append({"er_room": er_room, "item": display, "count": count, "notes": notes})
            continue
        if not entry:
            unmatched.append({"er_room": er_room, "item": display, "count": count, "notes": notes})
            continue
        add_item(room_items[code], entry)

    for code in ("216", "217", "213"):
        for it in room_items[code]["items"]:
            if it["name"] == "mo_disc":
                it["notes"] = "first MO disc; Any% may use 213 security room instead"

    # Route (route_jill_anypct.json) waypoints whose room codes disagree with
    # source-verified placements. The route file is left untouched; a human
    # should fix the route or confirm the room hex in-game.
    unmatched.extend([
        {"er_room": "Large Gallery", "item": "star_crest", "count": 1,
         "notes": "ROUTE CONFLICT wp08: route says 107 GALLERY; Evil Resource places star_crest in Large Gallery (crow paintings) = debug 117 LARGE GALLERY. Kept in 117."},
        {"er_room": "n/a (Jill has no sword key)", "item": "sword_key", "count": 0,
         "notes": "ROUTE CONFLICT wp17: sword_key is Chris-only per Evil Resource mansion contents (Jill uses lockpick). Route wp17 items_gained is wrong; serum@102 also dubious (canonical serum is 100 Mansion Save Room)."},
        {"er_room": "Room 001 Bathroom", "item": "control_room_key", "count": 1,
         "notes": "ROUTE CONFLICT wp22: route says 401 ROOM 001; key is in the bathtub of the separate bathroom room = 402. Kept in 402."},
        {"er_room": "Beehive Passage", "item": "dorm_key_002", "count": 1,
         "notes": "ROUTE CONFLICT wp23: route says 406 ROOM 002; 002 key is on the table near the wasp nest in Beehive Passage = 408 (StrategyWiki + GameFAQs agree). Kept in 408."},
        {"er_room": "Room 001", "item": "red_book", "count": 1,
         "notes": "ROUTE CONFLICT wp23: route says 406 ROOM 002; Red Book is on the bed in Room 001 = 401 (GameFAQs Jill DC walkthrough). Kept in 401."},
        {"er_room": "Private Library / Library B", "item": "mo_disc", "count": 1,
         "notes": "ROUTE CONFLICT wp34: route says 216 LIBRARY A; the MO disc statue-puzzle room is LIBRARY B = 217 (walkthrough names it Library B literally). Kept in 217; alt copy noted in 213."},
        {"er_room": "Closet", "item": "battery", "count": 1,
         "notes": "ROUTE CONFLICT wp35: route says 213 FRONT ELEVATOR; Evil Resource places battery in the Closet adjacent to Elevator 2F, best debug match 219 SHED (stage-2 = 2F). Kept in 219; needs in-game hex confirmation."},
        {"er_room": "Courtyard Study", "item": "doom_book_1", "count": 1,
         "notes": "ROUTE CONFLICT wp36: route says 20B FRONT LESSON ROOM; Evil Resource places Doom Book 1 in Courtyard Study (1F, helmet key door off Back Passage). No confident debug code in rooms.json - left unmapped."},
        {"er_room": "Store Room", "item": "square_crank", "count": 1,
         "notes": "ROUTE CONFLICT wp37: route says 305 FOUNTAIN; Evil Resource places square_crank on the Store Room top shelf (shed to courtyard) = 11B. Kept in 11B."},
    ])

    out = {
        "_meta": {
            "source": "evilresource.com + GameFAQs Jill walkthrough (KrystalCelest)",
            "scenario": "jill",
            "game": "RE1 Director's Cut PS1 standard Jill",
            "date": "2026-07-02",
            "fetch_errors": fetch_errors[:20],
            "scrape_count": len(scraped),
        },
        "_unmatched": unmatched,
    }
    # Sort room keys, skip empty-only if desired — include all rooms from rooms.json
    for code in sorted(room_items.keys(), key=lambda x: (int(x[:1], 16), int(x[1:], 16) if len(x) > 1 else 0)):
        out[code] = room_items[code]

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n_items = sum(len(v["items"]) for k, v in out.items() if k not in ("_meta", "_unmatched"))
    n_key = sum(
        1
        for k, v in out.items()
        if k not in ("_meta", "_unmatched")
        for it in v["items"]
        if it.get("key_item")
    )
    n_rooms_with = sum(1 for k, v in out.items() if k not in ("_meta", "_unmatched") and v["items"])
    print(f"Wrote {OUT}")
    print(f"rooms with items: {n_rooms_with}, total item entries: {n_items}, key items: {n_key}")
    print(f"unmatched: {len(unmatched)}, fetch_errors: {len(fetch_errors)}")


if __name__ == "__main__":
    main()
