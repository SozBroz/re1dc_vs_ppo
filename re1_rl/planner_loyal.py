"""Planner-loyal PPO: follow LLM-authored atomic steps; divert = terminal.

Reward contract (imperator 2026-08-25):
- Keep: stepwise contempt, damage taken, damage dealt, heal-from-HP rewards.
- Heal-use tax: any consumed heal item -0.10.
- Low-ammo COMBINE reload (weapon at or below 1/3 clip): +0.50.
- Completing the current planner step: +8 scaled by leftover 12m cell budget
  (``+8 * leftover_frac``); rearm a fresh 12m cell wall, extend idle +
  ``max_steps`` by 12m, and keep playing. The last authored chunk step ends
  the episode.
- Divert (wrong room / unplanned pickup / unplanned box / typewriter save): -4, episode end.
  COMBINE reshuffles (reload / herb mix / ammo merge), scripted ``event``
  grants (Barry acid, Speyer bazooka, …), and already-held *weapon* chamber
  bumps are not pickups. Floor piles always divert unless the current step is
  that ``acquire``.
- Cell timer: flat 12 minutes only (no custom yawn_cell_timeouts.json times).
- Armor room 205: ``armor_room_enter`` (pl78), exact east vent (pl79),
  exact east+west vents (pl80), then ``sun_crest`` acquire (pl81).
- After sun crest: Richard bleedout first (starts ~6 min death timer), place
  sun at 11A, dining 2F enter, then ``push_statue_2f`` end-anchor.
- Dining 2F (pl95): same ±0.5 shove crumb + ``pushables`` / goal compass as
  yawn ``statue_202`` (gated on ``push_statue_2f`` / ``dining_statue_knocked``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from re1_rl.item_todo import canonical_item

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHUNK = ROOT / "data" / "planner_chunks" / "cp05_shield_key.json"

PLANNER_MAX_STEPS = 20
PLANNER_STEP_SUCCESS_REWARD = 8.0
PLANNER_DIVERT_PENALTY = -4.0
PLANNER_TIMEOUT_PENALTY = -4.0
HEAL_USE_TAX_LIGHT = -0.10  # green / blue herb
HEAL_USE_TAX_STRONG = -0.10  # sprays, red mixes, multi-herb mixes

# Scalar reward keys under strict planner-loyal (telemetry aliases excluded).
PLANNER_LOYAL_SCALAR_KEYS: frozenset[str] = frozenset(
    {
        "step",
        "softlock",
        "hp",
        "death",
        "enemy_damage",
        "enemy_kill",
        "attack_miss",
        "ammo_spend",
        "ammo_waste",
        "combat_overkill",
        "shotgun_dog_hit",
        "heavy_weapon_fodder_hit",
        "attack_dry_fire",
        "attack_macro_failure",
        "heal_use_tax",
        "weapon_reload",
        "planner_step_success",
        "planner_divert",
        "planner_timeout",
        "gallery_wrong",
        "armor_statue_progress",
        "armor_inplace_statue_push",
        "armor_approach",
        "armor_gas",
        "dining_statue_progress",
    }
)

# Populated for capture / terminal paths; never summed into the scalar reward.
PLANNER_LOYAL_TELEMETRY_KEYS: frozenset[str] = frozenset(
    {
        "wrong_room",
        "checkpoint_success",
        "checkpoint_timeout",
    }
)

# Strategy-memory / almanac keys removed from the obs Dict under planner-loyal
# (not zeroed). World tower is omitted when ``world_state`` is absent.
PLANNER_LOYAL_OMIT_OBS_KEYS = frozenset(
    {
        "history",
        "acquisitions",
        "rooms_visited",
        "cutscene_ledger",
        "milestones",
        "maps_files",
        "affordances",
        "world_state",
    }
)

LIGHT_HEAL_USE_ITEMS = frozenset({"green_herb", "blue_herb"})
STRONG_HEAL_USE_ITEMS = frozenset(
    {
        "first_aid_spray",
        "first_aid_spray_alt",
        "red_herb",
        "mixed_herbs_gr",
        "mixed_herbs_gg",
        "mixed_herbs_gb",
        "mixed_herbs_grb",
        "mixed_herbs_ggg",
        "mixed_herbs_ggb",
    }
)

# Yawn place_emblem_10F accepted either bar wooden-emblem USE site.
_ALCOVE_SWAP_SITES = frozenset({"emblem@10F_alcove", "emblem@10F_wall"})
_ALCOVE_SWAP_BEATS = frozenset({"emblem_swap_alcove"})

# Already holding this type must not skip a later room's pile (108 clip after 104).
_ON_PATH_PILE_ITEMS = frozenset(
    {
        "handgun_bullets",
        "shotgun_shells",
        "green_herb",
        "red_herb",
        "blue_herb",
        "first_aid_spray",
        "first_aid_spray_alt",
    }
)

# Ops encoded for the policy (one-hot order). Do not add types — obs is 182-d.
PLANNER_OP_TYPES = (
    "traverse",
    "acquire",
    "objective",
    "do_puzzle",
    "trigger_cutscene",
    "boss",
    "use_box",
)
DEFAULT_BOX_ROOM = "118"
GO_TO_BOX_OP = "go_to_box"


def _encode_queue_op(op: str) -> str:
    """``go_to_box`` shares the ``use_box`` one-hot so planner_steps stay 182-d."""
    raw = str(op or "")
    if raw == GO_TO_BOX_OP:
        return "use_box"
    return raw


def _box_dest_room(step: dict[str, Any] | None) -> str:
    if not step:
        return DEFAULT_BOX_ROOM
    return str(step.get("room_id") or "").strip().upper() or DEFAULT_BOX_ROOM


def load_chunk(path: str | Path | None = None) -> dict[str, Any]:
    chunk_path = Path(path) if path else DEFAULT_CHUNK
    with chunk_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _chunk_file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PlannerLoyalQueue:
    """Pop-front queue of LLM-authored steps.

    The chunk may be longer than ``PLANNER_MAX_STEPS``; obs encodes at most
    that many *remaining* orders. Pass ``chunk_path`` so each reset re-reads
    the file (content hash). A git pull is enough; do not bounce workers.
    """

    def __init__(
        self,
        chunk: dict[str, Any] | None = None,
        *,
        chunk_path: Path | str | None = None,
    ) -> None:
        self._chunk_path: Path | None = None
        self._chunk_digest: str | None = None
        if chunk is not None and chunk_path is None:
            self._apply_chunk(chunk)
        else:
            path = Path(chunk_path) if chunk_path is not None else resolve_chunk_path()
            self._apply_chunk(chunk if chunk is not None else load_chunk(path), path)

    def _apply_chunk(
        self, data: dict[str, Any], path: Path | None = None
    ) -> None:
        raw_steps = list(data.get("steps") or [])
        self.chunk_id = str(data.get("chunk_id") or "unknown")
        self.end_anchor = str(data.get("end_anchor_beat_id") or "")
        leave = data.get("leave_118")
        self.leave_118 = leave if isinstance(leave, dict) else None
        leave_100 = data.get("leave_100")
        self.leave_100 = leave_100 if isinstance(leave_100, dict) else None
        self._steps = []
        for index, step in enumerate(raw_steps):
            row = dict(step)
            row["n"] = int(row.get("n") or index + 1)
            op = str(row.get("op") or "")
            if op == "use_key_item":
                row["op"] = "objective"
            self._steps.append(row)
        if path is not None:
            self._chunk_path = Path(path)
            try:
                self._chunk_digest = _chunk_file_digest(self._chunk_path)
            except OSError:
                self._chunk_digest = None
        self._index = 0
        self.step_success_pending = False
        self.divert_reason = None
        self._start_held = set()
        self._start_qty_totals = {}
        self._start_room = ""
        self._start_gallery_progress = 0
        self._start_gallery_solved = False
        self._satisfied_pickups: set[str] = set()

    def reload_if_stale(self, project_root: Path | str | None = None) -> bool:
        """Re-read the chunk file. True when bytes or path changed."""
        if self._chunk_path is None:
            return False
        path = resolve_chunk_path(project_root)
        try:
            digest = _chunk_file_digest(path)
        except OSError:
            return False
        if (
            path.resolve() == self._chunk_path.resolve()
            and self._chunk_digest is not None
            and digest == self._chunk_digest
        ):
            return False
        try:
            data = load_chunk(path)
            validate_planner_loyal_chunk(data)
        except (OSError, json.JSONDecodeError, ValueError):
            return False
        self._apply_chunk(data, path)
        return True

    @property
    def remaining(self) -> list[dict[str, Any]]:
        return list(self._steps[self._index :])

    @property
    def current(self) -> dict[str, Any] | None:
        if self._index >= len(self._steps):
            return None
        return self._steps[self._index]

    @property
    def done(self) -> bool:
        return self._index >= len(self._steps)

    @property
    def index(self) -> int:
        return self._index

    def reset(self) -> None:
        self._index = 0
        self.step_success_pending = False
        self.divert_reason = None
        self._start_held = set()
        self._start_qty_totals = {}
        self._start_room = ""
        self._start_gallery_progress = 0
        self._start_gallery_solved = False
        self._satisfied_pickups = set()

    def note_start_inventory(self, state: dict[str, Any]) -> None:
        """Snapshot episode-start inventory (sidecar / live RAM after reset)."""
        self._start_qty_totals = _inventory_qty_totals(state)
        # Presence, not qty: files/notes often occupy a slot with qty 0.
        self._start_held = set(_inventory_held_names(state))
        self._start_room = str(state.get("room_id") or "").strip().upper()
        self._start_gallery_progress = int(state.get("gallery_progress", 0) or 0)
        self._start_gallery_solved = bool(state.get("gallery_puzzle_solved", False))

    def seek(self, index: int) -> None:
        """Jump queue to ``index`` (0 = first step; len(steps) = done)."""
        n = len(self._steps)
        self._index = max(0, min(int(index), n))
        self.step_success_pending = False
        self.divert_reason = None
        self._rebuild_satisfied_pickups()

    def _rebuild_satisfied_pickups(self) -> None:
        satisfied: set[str] = set()
        for step in self._steps[: self._index]:
            if str(step.get("op") or "") != "acquire":
                continue
            pickup_id = str(step.get("pickup_id") or "")
            if pickup_id:
                satisfied.add(pickup_id)
        self._satisfied_pickups = satisfied

    def _completed_acquire_names(self, room: str) -> set[str]:
        """Item names already minted in this room — leftover cinema is not a pickup."""
        names: set[str] = set()
        room = str(room or "")
        for pickup_id in self._satisfied_pickups:
            step_room, item, _pile = _pickup_id_parts(pickup_id)
            if item and (not step_room or step_room == room):
                names.add(item)
        return names

    def _acquire_sibling_pile_ids(self, room: str, item: str) -> list[str]:
        siblings: list[str] = []
        for step in self._steps:
            if str(step.get("op") or "") != "acquire":
                continue
            pickup_id = str(step.get("pickup_id") or "")
            step_room, step_item, pile = _pickup_id_parts(pickup_id)
            if step_room == room and step_item == item and pile is not None:
                siblings.append(pickup_id)
        return siblings

    def _should_skip_acquire_at_start(self, pickup_id: str) -> bool:
        if pickup_id in self._satisfied_pickups:
            return True
        room, item, pile = _pickup_id_parts(pickup_id)
        if not item:
            return False
        if item in _ON_PATH_PILE_ITEMS:
            return False
        siblings = self._acquire_sibling_pile_ids(room, item)
        if len(siblings) > 1:
            # Numbered piles (e.g. 104:handgun_bullets:1 vs :2): never skip from
            # generic "already holding ammo"; only exact pickup_ids satisfied.
            return False
        return item in self._start_held

    def _alcove_swap_complete(
        self, prev_state: dict[str, Any], state: dict[str, Any]
    ) -> bool:
        """Wooden emblem left inventory in 10F while gold_emblem is still held.

        Yawn ``place_emblem_10F`` used story_use (alcove or wall) plus
        ``lacks_item`` emblem. Planner-loyal used to require an exact
        ``emblem@10F_alcove`` story id, so a real swap often never minted.
        """
        step = self.current or {}
        site = str(step.get("site_id") or "")
        beat = str(step.get("beat_id") or "")
        if site not in _ALCOVE_SWAP_SITES and beat not in _ALCOVE_SWAP_BEATS:
            return False
        if str(state.get("room_id") or "") != "10F":
            return False
        held_now = _inventory_held_names(state)
        if "gold_emblem" not in held_now or "emblem" in held_now:
            return False
        if "emblem" in _inventory_held_names(prev_state):
            return True
        return "emblem" in self._start_held

    def _unique_acquire_now_held(self, state: dict[str, Any]) -> bool:
        """True when the current unique acquire's item is in inventory now."""
        step = self.current or {}
        if str(step.get("op") or "") != "acquire":
            return False
        pickup_id = str(step.get("pickup_id") or "")
        room, item, _pile = _pickup_id_parts(pickup_id)
        if not item or pickup_id in self._satisfied_pickups:
            return False
        if len(self._acquire_sibling_pile_ids(room, item)) > 1:
            return False
        if item in self._start_held:
            return False
        return item in _inventory_held_names(state)

    def _skip_satisfied_acquires(self) -> None:
        """Skip acquire steps already satisfied at episode start."""
        while True:
            step = self.current
            if not step or str(step.get("op") or "") != "acquire":
                return
            pickup_id = str(step.get("pickup_id") or "")
            if self._should_skip_acquire_at_start(pickup_id):
                self._index += 1
                self._rebuild_satisfied_pickups()
                continue
            return

    def _skip_satisfied_box_nav(self) -> None:
        """Skip go_to_box when the episode already starts in that box room."""
        while True:
            step = self.current
            if not step or str(step.get("op") or "") != GO_TO_BOX_OP:
                return
            dest = _box_dest_room(step)
            if self._start_room and self._start_room == dest:
                self._index += 1
                continue
            return

    def _skip_satisfied_gallery_portraits(self) -> None:
        """Skip portrait / end-of-life steps already true at episode start."""
        from re1_rl.gallery_puzzle import completed_steps

        done = completed_steps(self._start_gallery_progress)
        while True:
            step = self.current
            if not step or str(step.get("op") or "") != "do_puzzle":
                return
            if _is_gallery_end_of_life(step):
                if self._start_gallery_solved:
                    self._index += 1
                    continue
                return
            portrait = _gallery_portrait_index(step)
            if portrait is None or done < portrait:
                return
            self._index += 1

    def target_room(self) -> str | None:
        step = self.current
        if not step:
            return None
        op = str(step.get("op") or "")
        if op == "traverse":
            edge = str(step.get("edge_id") or "")
            if "->" in edge:
                return edge.split("->", 1)[1]
            return None
        if op == "acquire":
            pickup_id = str(step.get("pickup_id") or "")
            room, _item, _pile = _pickup_id_parts(pickup_id)
            if room:
                return room
        if op == GO_TO_BOX_OP:
            return _box_dest_room(step)
        return str(step.get("room_id") or "") or None

    def box_target_held(self) -> list[Any] | None:
        """``held_on_exit`` for the current ``use_box`` step, else None."""
        step = self.current
        if not step or str(step.get("op") or "") != "use_box":
            return None
        held = step.get("held_on_exit")
        nested = step.get("leave_100") or step.get("leave_118")
        if not held and isinstance(nested, dict):
            held = nested.get("held_on_exit")
        room = str(step.get("room_id") or "")
        if not held and room == "100" and isinstance(self.leave_100, dict):
            held = self.leave_100.get("held_on_exit")
        if not held and isinstance(self.leave_118, dict):
            held = self.leave_118.get("held_on_exit")
        if not isinstance(held, list) or not held:
            return None
        return list(held)

    def allowed_banked_key_names(self) -> frozenset[str]:
        """Story keys Muse authored into ``leave_118`` / ``leave_100`` banks."""
        from re1_rl.item_todo import canonical_item
        from re1_rl.key_items import KEY_ITEM_NAMES

        names: set[str] = set()
        leaves = [
            self.leave_118 if isinstance(self.leave_118, dict) else {},
            getattr(self, "leave_100", None) if isinstance(getattr(self, "leave_100", None), dict) else {},
        ]
        step = self.current or {}
        for key in ("leave_100", "leave_118"):
            nested = step.get(key)
            if isinstance(nested, dict):
                leaves.append(nested)
        for leave in leaves:
            for row in (leave or {}).get("banked_in_box") or []:
                if not isinstance(row, dict):
                    continue
                name = canonical_item(str(row.get("item") or ""))
                if name and name in KEY_ITEM_NAMES:
                    names.add(name)
        return frozenset(names)

    def allowed_banked_key_ids(self) -> frozenset[int]:
        from re1_rl.box_target import item_name_to_id

        ids: set[int] = set()
        for name in self.allowed_banked_key_names():
            iid = item_name_to_id(name)
            if iid is not None:
                ids.add(int(iid))
        return frozenset(ids)

    def evaluate_transition(
        self,
        *,
        prev_state: dict[str, Any],
        state: dict[str, Any],
        box_opened: bool = False,
        box_closed: bool = False,
        typewriter_save_complete: bool = False,
        progress: Any = None,
    ) -> dict[str, Any]:
        """Return reward flags for this env step under planner loyalty."""
        result = {
            "step_success": False,
            "divert": False,
            "divert_reason": None,
            "heal_use_tax": 0.0,
        }
        if self.done:
            return result

        self._skip_satisfied_acquires()
        self._skip_satisfied_box_nav()
        self._skip_satisfied_gallery_portraits()
        if self.done:
            return result

        step = self.current or {}
        op = str(step.get("op") or "")
        prev_room = str(prev_state.get("room_id") or "")
        room = str(state.get("room_id") or "")

        # Heal-use tax from inventory disappearance of heal items (not box).
        tax = _heal_use_tax(prev_state, state)
        result["heal_use_tax"] = tax

        if typewriter_save_complete or _ink_ribbon_consumed(prev_state, state):
            result["divert"] = True
            result["divert_reason"] = (
                "unplanned_typewriter_save"
                if typewriter_save_complete
                else "unplanned_ink_ribbon_use"
            )
            self.divert_reason = result["divert_reason"]
            return result

        # Divert: unplanned box open.
        if box_opened and op != "use_box":
            result["divert"] = True
            result["divert_reason"] = "unplanned_box"
            self.divert_reason = result["divert_reason"]
            return result

        # Unique key acquire (music_notes, gold_emblem, …): complete if the
        # item is held now and was not held at episode start. File/document
        # cinema often starts skip *after* RAM already has the item, so the
        # inventory rising edge is gone. Numbered ammo piles still need a qty
        # edge (same sibling rule as reset skip).
        if self._unique_acquire_now_held(state):
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
            self._rebuild_satisfied_pickups()
            print(
                f"[planner_loyal] unique_acquire {step.get('pickup_id')} "
                f"room={room}",
                flush=True,
            )
            return result

        # Mint Richard ledger on scripted 20D→204 dump (planner-loyal legs).
        if progress is not None and room and prev_room and room != prev_room:
            from re1_rl.richard_cutscene_checkpoint import (
                note_richard_cutscene_room_transition,
            )

            note_richard_cutscene_room_transition(
                None,
                progress,
                prev_room,
                room,
                state,
                planner_loyal_queue=self,
            )

        # RAM-flag puzzle completions (yawn ``state_flag`` path) must win over
        # unplanned_room / pickup gates — knock often shares a step with a
        # cinema room dump or inventory flicker.
        from re1_rl.armor_room_puzzle import armor_vent_step_complete

        if armor_vent_step_complete(step, state):
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
            return result

        if _dining_statue_step_complete(step, state):
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
            print(
                f"[planner_loyal] push_statue_2f knocked room={room}",
                flush=True,
            )
            return result

        # Divert: unplanned room change. go_to_box may hop any door until dest.
        if room and prev_room and room != prev_room:
            if op == GO_TO_BOX_OP:
                dest = _box_dest_room(step)
                if room == dest:
                    result["step_success"] = True
                    self._index += 1
                    self.step_success_pending = True
                    print(
                        f"[planner_loyal] go_to_box arrived {dest} from {prev_room}",
                        flush=True,
                    )
                return result
            if op != "traverse":
                # Richard cinema often dumps Pillar Passage → C passage.
                if _richard_bleedout_complete(step, state, progress):
                    result["step_success"] = True
                    self._index += 1
                    self.step_success_pending = True
                    print(
                        f"[planner_loyal] richard_bleedout "
                        f"{prev_room}->{room}",
                        flush=True,
                    )
                    return result
                result["divert"] = True
                result["divert_reason"] = f"unplanned_room:{prev_room}->{room}"
                self.divert_reason = result["divert_reason"]
                return result
            edge = str(step.get("edge_id") or "")
            expected = edge.split("->", 1)[1] if "->" in edge else ""
            if room != expected:
                result["divert"] = True
                result["divert_reason"] = f"wrong_traverse:{edge} got {room}"
                self.divert_reason = result["divert_reason"]
                return result
            # Correct traverse completed.
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
            return result

        # Already in traverse destination (e.g. cinema left Jill in 204).
        if op == "traverse":
            edge = str(step.get("edge_id") or "")
            expected = edge.split("->", 1)[1] if "->" in edge else ""
            if expected and room == expected:
                result["step_success"] = True
                self._index += 1
                self.step_success_pending = True
                return result

        # Divert / complete: pickups. COMBINE, already-held *weapon* chamber
        # bumps, leftover cinema after a minted acquire, and scripted ``event``
        # grants (Barry acid, Speyer bazooka, …) are not world pickups.
        # Floor piles (herbs, ammo boxes, ink, …) always divert unless this
        # step is the matching ``acquire``. Box UI reshuffles are ``use_box``.
        gained = _inventory_gains(prev_state, state)
        if gained and op != "use_box":
            want = str(step.get("pickup_id") or "")
            _, want_item, _ = _pickup_id_parts(want)
            matched = op == "acquire" and _pickup_matches_gain(want, gained)
            planned = {want_item} if matched else set()
            unexpected = gained - planned
            unexpected -= _already_held_weapon_names(prev_state, self._start_held)
            unexpected -= _combine_explained_gains(prev_state, state)
            unexpected -= unexpected & _event_grant_names(room)
            # Leftover cinema after a minted acquire (key/weapon) is not a
            # pickup. Stackable floor piles (herbs/ammo) are never exempt —
            # each scripted acquire is one pile; extras divert.
            unexpected -= (
                self._completed_acquire_names(room) - _ON_PATH_PILE_ITEMS
            )
            if op != "acquire":
                if unexpected:
                    result["divert"] = True
                    result["divert_reason"] = f"unplanned_pickup:{sorted(unexpected)}"
                    self.divert_reason = result["divert_reason"]
                    return result
                # Benign inventory edge (cinema grant, slot decode flicker) on a
                # puzzle leg — fall through to RAM-flag completion above.
                if op not in {"do_puzzle", "objective", "trigger_cutscene", "boss"}:
                    return result
            if not matched:
                if unexpected:
                    result["divert"] = True
                    result["divert_reason"] = (
                        f"wrong_pickup want={want} got={sorted(gained)}"
                    )
                    self.divert_reason = result["divert_reason"]
                return result
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
            self._rebuild_satisfied_pickups()
            return result

        # Objective / puzzle / cutscene / boss completion via story_use or flags.
        if op in {"objective", "do_puzzle", "trigger_cutscene", "boss"}:
            site = str(step.get("site_id") or "")
            story = str(state.get("story_use_success") or "")
            story_hit = _objective_story_matches(site, story)
            if story_hit and (
                story not in _ALCOVE_SWAP_SITES
                or "emblem" not in _inventory_held_names(state)
            ):
                result["step_success"] = True
                self._index += 1
                self.step_success_pending = True
                if story in _ALCOVE_SWAP_SITES or site in _ALCOVE_SWAP_SITES:
                    print(
                        f"[planner_loyal] alcove_swap site={site or story} "
                        f"room={room}",
                        flush=True,
                    )
                return result
            if self._alcove_swap_complete(prev_state, state):
                result["step_success"] = True
                self._index += 1
                self.step_success_pending = True
                print(
                    f"[planner_loyal] alcove_swap site={site or story} "
                    f"room={room}",
                    flush=True,
                )
                return result
            if _richard_bleedout_complete(step, state, progress):
                result["step_success"] = True
                self._index += 1
                self.step_success_pending = True
                print(
                    f"[planner_loyal] richard_bleedout room={room}",
                    flush=True,
                )
                return result
            if _is_gallery_end_of_life(step) and _gallery_end_of_life_complete(
                prev_state, state
            ):
                result["step_success"] = True
                self._index += 1
                self.step_success_pending = True
                return result
            portrait = _gallery_portrait_index(step)
            if portrait is not None and room == "117":
                from re1_rl.gallery_puzzle import completed_steps

                raw = int(state.get("gallery_progress", 0) or 0)
                if completed_steps(raw) >= portrait:
                    result["step_success"] = True
                    self._index += 1
                    self.step_success_pending = True
                    return result
        if op == "use_box":
            target = self.box_target_held()
            if target:
                from re1_rl.box_target import inventory_matches_target

                slots = state.get("inventory_slots") or state.get("inventory") or []
                if inventory_matches_target(slots, target) and box_closed:
                    result["step_success"] = True
                    self._index += 1
                    self.step_success_pending = True
                return result
            if box_opened:
                result["step_success"] = True
                self._index += 1
                self.step_success_pending = True
                return result

        return result


def _ink_ribbon_consumed(prev_state: dict[str, Any], state: dict[str, Any]) -> bool:
    from re1_rl.typewriter_save import count_ink_ribbons

    before = int(count_ink_ribbons(prev_state))
    after = int(count_ink_ribbons(state))
    return before > 0 and after < before


def _objective_story_matches(site: str, story: str) -> bool:
    if site and story and story == site:
        return True
    return bool(site in _ALCOVE_SWAP_SITES and story in _ALCOVE_SWAP_SITES)


def _richard_bleedout_complete(
    step: dict[str, Any],
    state: dict[str, Any] | None,
    progress: Any = None,
) -> bool:
    """True when current step is Richard bleedout and the cinema ledger fired."""
    site = str(step.get("site_id") or "")
    beat = str(step.get("beat_id") or "")
    if site != "20D:richard" and beat != "richard_bleedout":
        return False
    snap = state or {}
    if snap.get("richard_cutscene_confirmed"):
        return True
    from re1_rl.richard_cutscene_checkpoint import richard_cutscene_seen

    return richard_cutscene_seen(progress)


def _dining_statue_step_complete(
    step: dict[str, Any],
    state: dict[str, Any] | None,
) -> bool:
    site = str(step.get("site_id") or "")
    beat = str(step.get("beat_id") or "")
    if site != "dining_statue_knocked" and beat != "push_statue_2f":
        return False
    from re1_rl.dining_statue_puzzle import dining_statue_knocked_from_state

    return dining_statue_knocked_from_state(state)


_GALLERY_PORTRAIT_RE = re.compile(r"gallery_portrait_(\d+)$")


GALLERY_END_OF_LIFE_IDS = frozenset({"gallery_end_of_life", "gallery_final_switch"})


def _is_gallery_end_of_life(step: dict[str, Any]) -> bool:
    return (
        str(step.get("beat_id") or "") in GALLERY_END_OF_LIFE_IDS
        or str(step.get("site_id") or "") in GALLERY_END_OF_LIFE_IDS
    )


def _gallery_end_of_life_complete(
    prev_state: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    """True when the end-of-life switch clears progress at the slot-8 AOT."""
    from re1_rl.gallery_puzzle import (
        GALLERY_COMPLETE_PREV_RAW,
        near_gallery_final_switch,
    )

    if str(state.get("room_id") or "") != "117":
        return False
    if bool(state.get("gallery_puzzle_solved", False)):
        return True
    prev_raw = int(prev_state.get("gallery_progress", 0) or 0)
    raw = int(state.get("gallery_progress", 0) or 0)
    if raw != 0 or prev_raw != GALLERY_COMPLETE_PREV_RAW:
        return False
    near_now = near_gallery_final_switch(
        float(state.get("x", 0) or 0),
        float(state.get("z", 0) or 0),
    )
    near_prev = near_gallery_final_switch(
        float(prev_state.get("x", state.get("x", 0)) or 0),
        float(prev_state.get("z", state.get("z", 0)) or 0),
    )
    return near_now or near_prev


def _gallery_portrait_index(step: dict[str, Any]) -> int | None:
    """1..6 for yawn-rails gallery portrait steps; else None."""
    for key in ("beat_id", "site_id"):
        match = _GALLERY_PORTRAIT_RE.fullmatch(str(step.get(key) or ""))
        if not match:
            continue
        index = int(match.group(1))
        if 1 <= index <= 6:
            return index
    return None


def _pickup_id_parts(pickup_id: str) -> tuple[str, str, str | None]:
    parts = str(pickup_id or "").split(":")
    if len(parts) >= 3:
        return parts[0], canonical_item(parts[1]), parts[2]
    if len(parts) == 2:
        return parts[0], canonical_item(parts[1]), None
    if parts:
        return "", canonical_item(parts[0]), None
    return "", "", None


def _pickup_matches_gain(pickup_id: str, gained: set[str]) -> bool:
    if pickup_id in gained:
        return True
    _room, item, _pile = _pickup_id_parts(pickup_id)
    if not item:
        return False
    return item in gained


def _inventory_qty_totals(state: dict[str, Any]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for entry in state.get("inventory_slots") or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("item")
            qty = int(entry.get("qty", 1) or 0)
        elif isinstance(entry, (list, tuple)) and entry:
            name = entry[0]
            qty = int(entry[1]) if len(entry) > 1 else 1
        else:
            name = None
            qty = 0
        if not name:
            continue
        key = canonical_item(str(name))
        totals[key] = totals.get(key, 0) + max(qty, 0)
    return totals


def _inventory_held_names(state: dict[str, Any]) -> set[str]:
    """Names occupying inventory, including files with qty 0."""
    names = set(_inventory_qty_totals(state))
    for raw in list(state.get("inventory") or []) + list(state.get("new_items") or []):
        key = canonical_item(str(raw))
        if key:
            names.add(key)
    return names


def _already_held_names(
    prev_state: dict[str, Any],
    start_held: set[str] | None = None,
) -> set[str]:
    """Names already in inventory (legacy helper; prefer weapon-only filter)."""
    held = set(_inventory_held_names(prev_state))
    if start_held:
        held |= {canonical_item(str(name)) for name in start_held if name}
    return held


def _already_held_weapon_names(
    prev_state: dict[str, Any],
    start_held: set[str] | None = None,
) -> set[str]:
    """Already-held guns may qty-bump (chamber) without counting as a pickup.

    Floor piles of the same *consumable* name (second green herb, extra clip)
    still divert — only weapon chamber refills are exempt here. COMBINE reloads
    and ``event`` grants have their own exemptions.
    """
    from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS

    weapon_names = {
        canonical_item(str(ITEM_IDS[i]))
        for i in WEAPON_ITEM_IDS
        if i in ITEM_IDS and ITEM_IDS[i]
    }
    held = _already_held_names(prev_state, start_held)
    return held & weapon_names


def _inventory_gains(prev_state: dict[str, Any], state: dict[str, Any]) -> set[str]:
    prev = _inventory_qty_totals(prev_state)
    cur = _inventory_qty_totals(state)
    gained: set[str] = set()
    for name, qty in cur.items():
        if name not in prev or qty > prev.get(name, 0):
            gained.add(name)
    for name in state.get("new_items") or []:
        key = canonical_item(str(name))
        if key:
            gained.add(key)
    return gained


def _inventory_id_qty(state: dict[str, Any]) -> list[tuple[int, int]]:
    from re1_rl.ammo_accounting import inventory_slots_to_id_qty

    return inventory_slots_to_id_qty((state or {}).get("inventory_slots"))


def _id_qty_slots_equal(
    left: list[tuple[int, int]],
    right: list[tuple[int, int]],
) -> bool:
    n = max(len(left), len(right))

    def _pad(slots: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out = [(int(iid) & 0xFF, int(qty)) for iid, qty in slots]
        while len(out) < n:
            out.append((0, 0))
        return out

    return _pad(left) == _pad(right)


def _id_qty_nonempty_bag(
    slots: list[tuple[int, int]],
) -> Counter[tuple[int, int]]:
    """Multiset of occupied slots — ignores empty holes (game may pack left)."""
    bag: Counter[tuple[int, int]] = Counter()
    for iid, qty in slots:
        iid_i = int(iid) & 0xFF
        qty_i = int(qty)
        if iid_i and qty_i > 0:
            bag[(iid_i, qty_i)] += 1
    return bag


def _id_qty_combine_match(
    planned: list[tuple[int, int]],
    observed: list[tuple[int, int]],
) -> bool:
    """True when planned COMBINE matches live inventory (holes or packed)."""
    if _id_qty_slots_equal(planned, observed):
        return True
    return _id_qty_nonempty_bag(planned) == _id_qty_nonempty_bag(observed)


def _combine_explained_gains(
    prev_state: dict[str, Any],
    state: dict[str, Any],
) -> set[str]:
    """Names gained solely by one legal COMBINE (reload / herb mix / ammo merge)."""
    from re1_rl.inventory_combine import plan_combine

    prev_inv = _inventory_id_qty(prev_state)
    cur_inv = _inventory_id_qty(state)
    if len(prev_inv) < 2 or not cur_inv:
        return set()
    for first in range(len(prev_inv)):
        for second in range(len(prev_inv)):
            planned = plan_combine(prev_inv, first, second)
            if planned is None:
                continue
            new_inv, _dest, _product = planned
            if _id_qty_combine_match(new_inv, cur_inv):
                return _inventory_gains(prev_state, state)
    return set()


_EVENT_GRANTS_BY_ROOM: dict[str, frozenset[str]] | None = None


def _load_event_grants_by_room() -> dict[str, frozenset[str]]:
    path = ROOT / "data" / "room_items.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    out: dict[str, set[str]] = {}
    for room_id, entry in raw.items():
        if str(room_id).startswith("_"):
            continue
        names: set[str] = set()
        for item in entry.get("items") or []:
            gate = item.get("gate") or {}
            if str(gate.get("type") or "") != "event":
                continue
            name = canonical_item(str(item.get("name") or ""))
            if name:
                names.add(name)
        if names:
            out[str(room_id)] = names
    return {room: frozenset(names) for room, names in out.items()}


def _event_grant_names(room: str) -> frozenset[str]:
    """Items this room only grants via cinema / story event, not a floor pile."""
    global _EVENT_GRANTS_BY_ROOM
    if _EVENT_GRANTS_BY_ROOM is None:
        _EVENT_GRANTS_BY_ROOM = _load_event_grants_by_room()
    return _EVENT_GRANTS_BY_ROOM.get(str(room or ""), frozenset())


def _heal_use_tax(prev_state: dict[str, Any], state: dict[str, Any]) -> float:
    prev = _inventory_qty_totals(prev_state)
    cur = _inventory_qty_totals(state)
    tax = 0.0
    for name, before in prev.items():
        after = cur.get(name, 0)
        if after >= before:
            continue
        lost = before - after
        if name in LIGHT_HEAL_USE_ITEMS:
            tax += HEAL_USE_TAX_LIGHT * lost
        elif name in STRONG_HEAL_USE_ITEMS:
            tax += HEAL_USE_TAX_STRONG * lost
    return tax


def encode_planner_queue(
    queue: PlannerLoyalQueue | None,
    *,
    max_steps: int = PLANNER_MAX_STEPS,
) -> list[float]:
    """Flat encoding of the *remaining* queue (completed steps already popped).

    Slot 0 is always the current order; slots 1..19 are futures slid forward
    after each successful step (``PlannerLoyalQueue._index`` advances).
    """
    cur_dim = 1 + len(PLANNER_OP_TYPES) + 1 + 1 + 1  # 11
    fut_dim = 1 + len(PLANNER_OP_TYPES) + 1  # 9
    out = [0.0] * (cur_dim + (max_steps - 1) * fut_dim)
    if queue is None:
        return out
    remaining = queue.remaining[:max_steps]
    if not remaining:
        return out

    room_index = _room_index_map()

    def room_norm(room_id: str | None) -> float:
        if not room_id:
            return 0.0
        return float(room_index.get(str(room_id), 0)) / 128.0

    def fill_op(dest: list[float], offset: int, op: str) -> None:
        encoded = _encode_queue_op(op)
        if encoded in PLANNER_OP_TYPES:
            dest[offset + PLANNER_OP_TYPES.index(encoded)] = 1.0

    def stable_unit(token: str) -> float:
        """Process-stable [0,1] id for pickup_id / site_id (not Python hash())."""
        if not token:
            return 0.0
        digest = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:2], "big") / 65535.0

    cur = remaining[0]
    out[0] = 1.0
    fill_op(out, 1, str(cur.get("op") or ""))
    if str(cur.get("op")) == "traverse":
        edge = str(cur.get("edge_id") or "")
        tgt = edge.split("->", 1)[1] if "->" in edge else None
    elif str(cur.get("op")) == GO_TO_BOX_OP:
        tgt = _box_dest_room(cur)
    else:
        tgt = str(cur.get("room_id") or "") or None
    out[1 + len(PLANNER_OP_TYPES)] = room_norm(tgt)
    out[1 + len(PLANNER_OP_TYPES) + 1] = stable_unit(str(cur.get("pickup_id") or ""))
    out[1 + len(PLANNER_OP_TYPES) + 2] = stable_unit(str(cur.get("site_id") or ""))

    base = cur_dim
    for slot, step in enumerate(remaining[1:max_steps]):
        o = base + slot * fut_dim
        out[o] = 1.0
        fill_op(out, o + 1, str(step.get("op") or ""))
        if str(step.get("op")) == "traverse":
            edge = str(step.get("edge_id") or "")
            rt = edge.split("->", 1)[1] if "->" in edge else None
        elif str(step.get("op")) == GO_TO_BOX_OP:
            rt = _box_dest_room(step)
        else:
            rt = str(step.get("room_id") or "") or None
        out[o + 1 + len(PLANNER_OP_TYPES)] = room_norm(rt)
    return out


_ROOM_INDEX_CACHE: dict[str, int] | None = None


def _room_index_map() -> dict[str, int]:
    global _ROOM_INDEX_CACHE
    if _ROOM_INDEX_CACHE is not None:
        return _ROOM_INDEX_CACHE
    rooms = json.loads((ROOT / "data" / "rooms.json").read_text(encoding="utf-8"))
    ids = sorted(k for k in rooms if not str(k).startswith("_"))
    _ROOM_INDEX_CACHE = {rid: i for i, rid in enumerate(ids)}
    return _ROOM_INDEX_CACHE


PLANNER_QUEUE_DIM = 11 + 19 * 9  # 182

# Route-admin goal scalars the policy should ignore once the planner owns strategy.
# Indices match obs_encoder.GOAL_FIELDS (lookahead slots sit after these).
_ROUTE_ADMIN_GOAL_NAMES = (
    "waypoint_index",
    "waypoints_remaining",
    "curriculum_stage",
    "item_todo_progress",
    "wrong_room_flag",
)


def scalarize_planner_loyal_reward(bd: dict[str, float]) -> float:
    """Sum only documented planner-loyal scalar channels."""
    from re1_rl.reward import REWARD_SCALE

    total = sum(float(bd.get(key, 0.0) or 0.0) for key in PLANNER_LOYAL_SCALAR_KEYS)
    return float(total) * REWARD_SCALE


def validate_planner_loyal_stage(stage: dict[str, Any]) -> None:
    """Fail closed when planner flag and curriculum disagree."""
    mode = str(stage.get("mode") or "")
    if mode != "planner_loyal":
        raise ValueError(
            "RE1_PLANNER_LOYAL=1 requires curriculum mode=planner_loyal, "
            f"got {mode!r}"
        )
    if stage.get("route_steps"):
        raise ValueError(
            "planner_loyal curriculum must not supply legacy route_steps "
            "(queue owns navigation)"
        )


def validate_planner_loyal_chunk(chunk: dict[str, Any]) -> None:
    steps = list(chunk.get("steps") or [])
    if not steps:
        raise ValueError("planner loyal chunk has no steps")
    # Chunk length is unbounded; PLANNER_MAX_STEPS is the obs window only.


def _planner_op_objective_index(op: str) -> int | None:
    """Map planner op → goal objective one-hot slot (10..14 in GOAL_FIELDS)."""
    mapping = {
        "traverse": 0,  # obj_navigate
        "acquire": 1,  # obj_pickup
        "objective": 2,  # obj_use_item
        "do_puzzle": 2,
        "trigger_cutscene": 2,
        "boss": 3,  # obj_fight
        "use_box": 4,  # obj_scripted
        "go_to_box": 0,  # obj_navigate — walk to the box room
    }
    idx = mapping.get(str(op or ""))
    return idx


def _planner_step_target_xz(
    step: dict[str, Any],
    *,
    item_positions: Any | None = None,
) -> tuple[float, float] | None:
    """World XZ for the current planner step (pickup pile or story USE site)."""
    op = str(step.get("op") or "")
    if op == "acquire":
        pickup_id = str(step.get("pickup_id") or "")
        room, item, _pile = _pickup_id_parts(pickup_id)
        if item_positions is not None and room and item:
            pos = item_positions.get(room, item)
            if pos is not None:
                return float(pos[0]), float(pos[1])
        if item == "star_crest" and room == "117":
            from re1_rl.gallery_puzzle import GALLERY_FINAL_SWITCH_TARGET

            return float(GALLERY_FINAL_SWITCH_TARGET[0]), float(
                GALLERY_FINAL_SWITCH_TARGET[1]
            )
        return None
    if op in {"objective", "do_puzzle", "trigger_cutscene", "boss"}:
        if _is_gallery_end_of_life(step):
            from re1_rl.gallery_puzzle import GALLERY_FINAL_SWITCH_TARGET

            return float(GALLERY_FINAL_SWITCH_TARGET[0]), float(
                GALLERY_FINAL_SWITCH_TARGET[1]
            )
        portrait = _gallery_portrait_index(step)
        if portrait is not None:
            from re1_rl.gallery_puzzle import GALLERY_TARGETS

            tx, tz = GALLERY_TARGETS[portrait - 1]
            return float(tx), float(tz)
        site_id = str(step.get("site_id") or "")
        if not site_id:
            return None
        from re1_rl.story_item_use import load_story_use_sites

        for site in load_story_use_sites():
            if str(site.get("id") or "") == site_id:
                return float(site["x"]), float(site["z"])
        return None


def encode_planner_loyal_goal(
    encoder: Any,
    graph: Any,
    state: dict[str, Any],
    queue: PlannerLoyalQueue,
    *,
    cell_time_remaining: float | None = None,
    item_positions: Any | None = None,
) -> np.ndarray:
    """Queue-driven goal vector: target room, hop distance, exit/in-room compass."""
    from re1_rl.obs_encoder import (
        CELL_TIME_REMAINING_INDEX,
        GOAL_DIM,
        GOAL_LOOKAHEAD_SLOT_DIM,
        GOAL_LOOKAHEAD_SLOTS,
        GOAL_BASE_DIM,
    )
    from re1_rl.memory_map import ITEM_IDS

    v = np.zeros(GOAL_DIM, dtype=np.float32)
    step = queue.current
    if step is None:
        remaining = 1.0 if cell_time_remaining is None else float(cell_time_remaining)
        v[CELL_TIME_REMAINING_INDEX] = float(np.clip(remaining, 0.0, 1.0))
        return v

    room = str(state.get("room_id", "") or "")
    target_room = queue.target_room()
    v[0] = encoder._room_idx_norm(target_room)
    hops = graph.hop_distance(room, target_room) if target_room else None
    v[3] = 1.0 if hops is None else min(float(hops) / 20.0, 1.0)
    v[4] = 1.0 if target_room is not None and room == str(target_room) else 0.0

    op = str(step.get("op") or "")
    compass_set = False
    nav_away = (
        op in {"traverse", GO_TO_BOX_OP}
        and target_room is not None
        and room != str(target_room)
    )
    if nav_away:
        door = graph.exit_toward(room, str(target_room))
        if door is not None:
            v[5:10] = encoder._compass_to_xz(state, float(door.x), float(door.z))
            v[21] = 1.0
            compass_set = True
    elif room and (target_room is None or room == str(target_room)):
        target_xz = None
        if room == "205":
            from re1_rl.armor_room_puzzle import (
                armor_statue_active,
                armor_statue_goal_target,
            )

            if armor_statue_active(queue, state):
                target_xz = armor_statue_goal_target(state)
        elif room == "202":
            from re1_rl.dining_statue_puzzle import (
                dining_statue_goal_target,
                statue_202_active,
            )

            if statue_202_active(None, state, queue=queue):
                target_xz = dining_statue_goal_target(state)
        if target_xz is None:
            target_xz = _planner_step_target_xz(step, item_positions=item_positions)
        if target_xz is not None:
            v[5:10] = encoder._compass_to_xz(state, target_xz[0], target_xz[1])
            v[21] = 1.0
            compass_set = True
    if not compass_set and op in {"traverse", GO_TO_BOX_OP} and target_room is not None:
        door = graph.exit_toward(room, str(target_room))
        if door is not None:
            v[5:10] = encoder._compass_to_xz(state, float(door.x), float(door.z))
            v[21] = 1.0

    obj_idx = _planner_op_objective_index(op)
    if obj_idx is not None:
        v[10 + obj_idx] = 1.0

    # Semantic target item id in lookahead slot 0 (stable, not hash-only).
    base = GOAL_BASE_DIM
    slot = v[base : base + GOAL_LOOKAHEAD_SLOT_DIM]
    slot[0] = 1.0
    slot[1] = encoder._room_idx_norm(target_room or str(step.get("room_id") or room))
    if op == "acquire":
        _room, item, _pile = _pickup_id_parts(str(step.get("pickup_id") or ""))
        item_id = ITEM_IDS.get(item, 0)
        slot[12] = float(item_id) / 75.0  # MAX_ITEM_ID ~= 0x4B
    elif op in {"objective", "do_puzzle", "trigger_cutscene"}:
        site_id = str(step.get("site_id") or "")
        if site_id:
            digest = hashlib.md5(site_id.encode("utf-8")).digest()
            slot[12] = int.from_bytes(digest[:2], "big") / 65535.0

    remaining = 1.0 if cell_time_remaining is None else float(cell_time_remaining)
    v[CELL_TIME_REMAINING_INDEX] = float(np.clip(remaining, 0.0, 1.0))
    return v


def planner_loyal_enabled() -> bool:
    raw = os.environ.get("RE1_PLANNER_LOYAL", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_chunk_path(project_root: Path | str | None = None) -> Path:
    raw = os.environ.get("RE1_PLANNER_CHUNK", "").strip()
    if not raw:
        return DEFAULT_CHUNK
    path = Path(raw)
    if path.is_absolute():
        return path
    root = Path(project_root) if project_root is not None else ROOT
    return root / path


def _route_admin_goal_indices() -> tuple[int, ...]:
    from re1_rl.obs_encoder import GOAL_FIELDS

    return tuple(
        i for i, (name, _) in enumerate(GOAL_FIELDS) if name in _ROUTE_ADMIN_GOAL_NAMES
    )


def prune_route_admin_goal(goal: Any) -> np.ndarray:
    """Zero route-admin scalars; leave compass / room / objective bits intact."""
    out = np.asarray(goal, dtype=np.float32).copy()
    if out.ndim == 0:
        return out
    for index in _route_admin_goal_indices():
        if index < out.shape[-1]:
            out[..., index] = 0.0
    return out


def apply_planner_loyal_obs(
    obs: dict[str, Any],
    queue: PlannerLoyalQueue | None,
) -> dict[str, Any]:
    """Attach ``planner_steps``, drop strategy/almanac keys, prune route-admin goals.

    Completed planner steps are already omitted from the encoding (queue pop /
    slide). No-op if queue is None (yawn rails / flag off).
    """
    if queue is None:
        return obs
    for key in PLANNER_LOYAL_OMIT_OBS_KEYS:
        obs.pop(key, None)
    obs["planner_steps"] = np.asarray(encode_planner_queue(queue), dtype=np.float32)
    if "goal" in obs:
        obs["goal"] = prune_route_admin_goal(obs["goal"])
    return obs
