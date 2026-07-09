"""One-shot: annotate data/room_items.json with gate metadata (Jill DC Standard)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "room_items.json"

# (room_id, item_name) -> gate dict
GATES: dict[tuple[str, str], dict] = {
    # --- Mansion 1F ---
    ("103", "green_herb"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Behind floor grate; push both statues onto grates before red button",
    },
    ("105", "shield_key"): {
        "type": "item",
        "requires": ["gold_emblem"],
        "notes": "Grandfather clock slides after gold emblem placed on fireplace",
    },
    ("106", "lockpick"): {
        "type": "event",
        "requires": [],
        "notes": "Barry gives lockpick after first zombie encounter in tea room",
    },
    ("106", "acid_rounds"): {
        "type": "event",
        "requires": [],
        "notes": "Barry hands acid rounds on 2F hall return if inventory has space",
    },
    ("107", "map_1f"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Push ladder behind statue and climb to bowl on statue head",
    },
    ("107", "ink_ribbon"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Push movable drawers aside in gallery side corridor",
    },
    ("10C", "armor_key"): {
        "type": "item",
        "requires": ["chemical"],
        "notes": "Pour chemical into greenhouse pump to kill vines on crest",
    },
    ("10D", "wind_crest"): {
        "type": "item",
        "requires": ["blue_jewel"],
        "notes": "Insert blue jewel in tiger eye; statue slides to reveal crest",
    },
    ("10F", "music_notes"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Push bookcase aside to reach shelf behind piano",
    },
    ("10F", "gold_emblem"): {
        "type": "item",
        "requires": ["music_notes", "emblem"],
        "notes": "Play piano with music notes; swap wooden emblem for gold in secret alcove",
    },
    ("102", "shotgun_shells"): {
        "type": "item",
        "requires": ["lockpick"],
        "notes": "Locked desk in vacant room",
    },
    ("111", "shotgun_shells"): {
        "type": "item",
        "requires": ["lockpick"],
        "notes": "Locked desk in dressing room",
    },
    ("115", "shotgun"): {
        "type": "trap",
        "requires": [],
        "notes": "Ceiling trap; Jill saved by Barry once (Chris needs broken_shotgun swap)",
    },
    ("116", "shotgun"): {
        "type": "trap",
        "requires": [],
        "notes": "Same wall rack as trap room; taking triggers ceiling trap (Jill: Barry rescue)",
    },
    ("117", "star_crest"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Crow painting buttons in life-cycle order (large gallery)",
    },
    ("11B", "square_crank"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Position stepladder at shelf and climb to top shelf",
    },
    # --- Mansion 2F ---
    ("202", "blue_jewel"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Push statue off 2F dining room balcony gap",
    },
    ("203", "acid_rounds"): {
        "type": "event",
        "requires": [],
        "notes": "Barry gives after Forest balcony scene if inventory has space",
    },
    ("205", "sun_crest"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Cover both floor grates with statues then press center button",
    },
    ("20A", "explosive_rounds"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Study room colored-bottle cabinet puzzle (push aquarium, bookcase)",
    },
    ("20B", "map_2f"): {
        "type": "item",
        "requires": ["lighter"],
        "notes": "Burn logs in lesson room entry fireplace",
    },
    ("20D", "clip"): {
        "type": "event",
        "requires": ["serum"],
        "notes": "Search Richard's body twice after giving him serum (pillar passage)",
    },
    ("20F", "acid_rounds"): {
        "type": "puzzle",
        "requires": ["lighter"],
        "notes": "Light candles, push left bookcase, enter secret cabinet room",
    },
    ("210", "moon_crest"): {
        "type": "event",
        "requires": [],
        "notes": "Appears after Yawn 1 fight or retreat in attic",
    },
    ("210", "shotgun_shells"): {
        "type": "event",
        "requires": [],
        "notes": "On barrel during Yawn 1 attic visit (fight or sneak past)",
    },
    ("212", "bazooka_acid"): {
        "type": "event",
        "requires": [],
        "notes": "Forest Speyer corpse cutscene on 2F terrace balcony",
    },
    ("215", "red_jewel"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Shut trophy room lights, stepladder under deer head, climb for jewel",
    },
    ("217", "mo_disc"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Library B: press wall button, push statue onto lit floor tile",
    },
    # --- Courtyard / Garden ---
    ("305", "doom_book_2"): {
        "type": "puzzle",
        "requires": ["hex_crank"],
        "notes": "Item chamber statue + hex crank puzzle (see room placement note in docs)",
    },
    ("306", "mo_disc"): {
        "type": "puzzle",
        "requires": ["hex_crank"],
        "notes": "Push statue five times, hex crank twice, align statue on tile",
    },
    # --- Underground ---
    ("30A", "hex_crank"): {
        "type": "event",
        "requires": [],
        "notes": "Sparkle pickup appears after Enrico death cutscene",
    },
    ("30A", "clip"): {
        "type": "event",
        "requires": [],
        "notes": "Search Enrico's body twice after cutscene",
    },
    ("30B", "mo_disc"): {
        "type": "item",
        "requires": ["hex_crank"],
        "notes": "Alcove behind first boulder trap after using hex crank on wall",
    },
    ("30B", "map_underground"): {
        "type": "item",
        "requires": ["hex_crank"],
        "notes": "On wall in boulder alcove (same gate as second MO disc)",
    },
    ("30D", "flame_rounds"): {
        "type": "event",
        "requires": [],
        "notes": "Hunter drop after triggering second boulder trap",
    },
    # --- Guardhouse ---
    ("401", "shotgun_shells"): {
        "type": "item",
        "requires": ["lockpick"],
        "notes": "Locked desk in room 001",
    },
    ("402", "control_room_key"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Pull bathtub drain chain in room 001 bathroom",
    },
    ("404", "pass_number"): {
        "type": "puzzle",
        "requires": [],
        "notes": "Jill reads pool-table code 345 in rec room (Chris: Barry gives pass)",
    },
    ("40A", "ink_ribbon"): {
        "type": "item",
        "requires": ["lockpick"],
        "notes": "Locked desk in room 003",
    },
    ("40C", "helmet_key"): {
        "type": "event",
        "requires": [],
        "notes": "Fireplace after Plant 42 boss (Barry helps if no V-Jolt)",
    },
    # --- Laboratory ---
    ("506", "wolf_medal"): {
        "type": "item",
        "requires": ["doom_book_2"],
        "notes": "Standard: inside Doom Book 2; open book at fountain (desk placement disputed)",
    },
    ("507", "eagle_medal"): {
        "type": "item",
        "requires": ["doom_book_1"],
        "notes": "Standard: inside Doom Book 1; open book at fountain (coffin note disputed)",
    },
    ("508", "passcode_a"): {
        "type": "item",
        "requires": ["mo_disc"],
        "notes": "Decoded at MO terminal, not a floor pickup",
    },
    ("508", "passcode_b"): {
        "type": "item",
        "requires": ["mo_disc"],
        "notes": "Decoded at MO terminal, not a floor pickup",
    },
    ("303", "rocket_launcher"): {
        "type": "event",
        "requires": [],
        "notes": "Brad drops launcher on helipad bad ending only",
    },
    ("302", "flare"): {
        "type": "event",
        "requires": [],
        "notes": "Barry gives flare at falls for good ending path",
    },
    ("50B", "flare"): {
        "type": "event",
        "requires": [],
        "notes": "Barry good-ending flare near lab escape elevator",
    },
    ("514", "rocket_launcher"): {
        "type": "event",
        "requires": [],
        "notes": "Barry drops rocket launcher before Tyrant fight (good ending)",
    },
}


def main() -> None:
    with PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    applied = 0
    for room_id, entry in data.items():
        if room_id.startswith("_"):
            continue
        for item in entry.get("items", []):
            key = (room_id, item["name"])
            if key in GATES:
                item["gate"] = dict(GATES[key])
                applied += 1

    with PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Applied {applied} gates to {PATH}")


if __name__ == "__main__":
    main()
