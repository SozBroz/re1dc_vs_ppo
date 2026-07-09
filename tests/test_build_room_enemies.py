"""Offline tests for data/room_enemies.json schema and mansion anchors."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

ROOM_ENEMIES = PROJECT_ROOT / "data" / "room_enemies.json"
ROOMS = PROJECT_ROOT / "data" / "rooms.json"

ENEMY_TYPES = frozenset({
  "zombie", "cerberus", "crow", "hunter", "spider", "snake_yawn",
  "plant42", "wasp", "chimera", "tyrant", "shark", "black_tiger",
})

SPAWN_TRIGGERS = frozenset({"always", "event", "cutscene", "return_visit"})


@pytest.fixture(scope="module")
def room_enemies() -> dict:
  assert ROOM_ENEMIES.is_file(), f"missing {ROOM_ENEMIES}; run scripts/build_room_enemies.py"
  return json.loads(ROOM_ENEMIES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rooms() -> dict:
  return json.loads(ROOMS.read_text(encoding="utf-8"))


def test_room_enemies_loads_and_meta(room_enemies: dict) -> None:
  assert "_meta" in room_enemies
  meta = room_enemies["_meta"]
  assert meta["scenario"] == "jill"
  assert meta["game"] == "RE1 Director's Cut PS1 standard Jill"
  assert "fetch_errors" in meta
  assert "notes" in meta


def test_every_room_key_in_rooms_json(room_enemies: dict, rooms: dict) -> None:
  for key in room_enemies:
    if key.startswith("_"):
      continue
    assert key in rooms, f"room_enemies key {key!r} not in rooms.json"


def test_all_rooms_present(room_enemies: dict, rooms: dict) -> None:
  for code in rooms:
    assert code in room_enemies, f"rooms.json code {code!r} missing from room_enemies.json"
    assert room_enemies[code]["room_name"] == rooms[code]["name"]
    assert "enemies" in room_enemies[code]


def test_enemy_entry_schema(room_enemies: dict) -> None:
  for key, block in room_enemies.items():
    if key.startswith("_"):
      continue
    for enemy in block["enemies"]:
      assert enemy["enemy_type"] in ENEMY_TYPES, enemy
      assert enemy["spawn_trigger"] in SPAWN_TRIGGERS, enemy
      assert isinstance(enemy["count"], int) and enemy["count"] >= 1
      assert "notes" in enemy


def test_tea_room_has_zombie(room_enemies: dict) -> None:
  enemies = room_enemies["104"]["enemies"]
  assert any(e["enemy_type"] == "zombie" for e in enemies), enemies
