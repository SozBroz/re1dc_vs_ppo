"""Real-time human play harness for RE1 (Jill, Director's Cut).

BizHawk only advances when this script sends bridge.step() — idle means frozen
while **in player control**. Doors, dialogue, and cutscenes auto fast-forward
at ``--cutscene-speed`` (default 6400%%) with turbo RAM patches and no render.

At each env step, prints RL reward breakdown and compass hints to the terminal.

Input: keyboard and/or gamepad. Gamepad reads **through EmuHawk** (host
joypad) — not pygame — so DualSense works while BizHawk holds SDL. Focus the
EmuHawk window and move the stick. Keyboard on Windows may need Administrator.

Usage:
    python scripts/play_human.py
    python scripts/play_human.py --input gamepad
    python scripts/play_human.py --start-savestate states/checkpoints/wp02_seq2_106.State
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.action_mask import SELECT_SLOT_BASE, USE_ACTION
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.memory_map import ITEM_IDS
from re1_rl.story_item_use import (
    annotate_story_use_success,
    legal_story_use_slots,
    load_story_use_sites,
    matching_story_sites,
)
from re1_rl.game_session import opening_phase_from_ram, outside_gameplay_reason
from re1_rl.item_todo import canonical_item, canonicalize
from re1_rl.obs_encoder import GOAL_FIELDS
from re1_rl.memory_map import (
    CHARACTER_ID,
    GAME_MODE,
    GAME_STATE,
    MESSAGE_FLAG,
    PLAYER_HP,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
)
from re1_rl.cutscene_reward import (
    cutscene_disqualify_reason,
    format_cutscene_gate_panel,
    qualify_cutscene_reward,
)
from re1_rl.enemy_combat import apply_combat_step_fields
from re1_rl.ram_skip import RamSkipper, SKIP_POLL_RAM_FIELDS, in_control_from_ram, item_inventory_screen_from_ram, needs_skip_from_ram
from re1_rl.sticky_input import human_buttons_to_step, human_step_gate
from re1_rl.reward import (
    ENABLE_CHECKPOINT_PATH,
    REWARD_SCALE,
    NEW_CUTSCENE_BONUS,
    NEW_ROOM_BONUS,
    NEW_WEAPON_PICKUP_BONUS,
    compute_reward,
)
from re1_rl.training_progress import TrainingProgressTracker

EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"
CKPT_DIR = PROJECT_ROOT / "states" / "checkpoints"
DEFAULT_CURRICULUM = PROJECT_ROOT / "curriculum" / "m0_dining_to_main_hall.json"
# Training uses 5555 + rank (see scripts/train_parallel.py BASE_PORT); keep human play off that block.
HUMAN_PLAY_PORT = 7788

REWARD_EPS = 0.05

RAM_SCREEN_FIELDS: list[tuple[str, int, str]] = [
    ("game_state", GAME_STATE, "u32"),
    ("game_mode", GAME_MODE, "u8"),
    ("scene_flag", SCENE_FLAG, "u8"),
    ("msg_flag", MESSAGE_FLAG, "u8"),
    ("stage_id", STAGE_ID, "u8"),
    ("room_id", ROOM_ID, "u8"),
    ("character_id", CHARACTER_ID, "u8"),
    ("player_hp", PLAYER_HP, "u16"),
]


_NAME_TO_ITEM_ID = {name: iid for iid, name in ITEM_IDS.items()}


def _inventory_ids_from_state(state: dict[str, Any]) -> list[tuple[int, int]]:
    inv = state.get("inventory_slots") or []
    out: list[tuple[int, int]] = []
    for row in inv:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            a, b = row[0], row[1]
            if isinstance(a, str):
                iid = _NAME_TO_ITEM_ID.get(a, 0)
            else:
                iid = int(a) & 0xFF
            out.append((iid, int(b)))
    while len(out) < 8:
        out.append((0, 0))
    return out


def _read_policy_inventory(env: RE1Env) -> list[tuple[int, int]] | None:
    try:
        from re1_rl.item_box import read_inventory
        from re1_rl.weapon_equip import policy_inventory

        return policy_inventory(read_inventory(env.bridge))
    except (OSError, RuntimeError, AttributeError, TypeError, ValueError, ImportError):
        return None


def _annotate_story_use(
    env: RE1Env,
    prev_state: dict[str, Any],
    state: dict[str, Any],
    inventory_before: list[tuple[int, int]] | None,
    inventory_after: list[tuple[int, int]] | None,
) -> dict[str, Any]:
    return annotate_story_use_success(
        state,
        prev_state=prev_state,
        inventory_before=inventory_before,
        inventory_after=inventory_after,
        rewarded_site_ids=env._progress.rewarded_story_uses,
    )


def format_story_mask_panel(env: RE1Env, state: dict[str, Any]) -> str:
    """Legal mask + story USE affordance (key item at interact site)."""
    mask = env.action_masks(state)
    legal = [ACTION_NAMES[i] for i in range(len(mask)) if bool(mask[i])]
    rewarded = set(env._progress.rewarded_story_uses)
    inv_ids: list[tuple[int, int]] | None = None
    try:
        from re1_rl.item_box import read_inventory
        from re1_rl.weapon_equip import policy_inventory

        inv_ids = policy_inventory(read_inventory(env.bridge))
    except (OSError, RuntimeError, AttributeError, TypeError, ValueError, ImportError):
        inv_ids = None
    if inv_ids is None:
        inv_ids = _inventory_ids_from_state(state)
    sites = matching_story_sites(
        room=str(state.get("room_id", "")),
        x=state.get("x"),
        z=state.get("z"),
        inventory=inv_ids,
        rewarded_site_ids=rewarded,
    )
    slots = legal_story_use_slots(
        inv_ids,
        room=str(state.get("room_id", "")),
        x=state.get("x"),
        z=state.get("z"),
        rewarded_site_ids=rewarded,
    )
    site_ids = [str(s["id"]) for s in sites] if sites else []
    use_ph = int(getattr(env, "_use_phase", 0))
    in_ctrl = bool(state.get("in_control", True))
    skipping = bool(getattr(env, "_skipping_flag", False))
    px = float(state.get("x") or 0)
    pz = float(state.get("z") or 0)
    room = str(state.get("room_id", ""))
    notes_slots = [
        f"slot{i}:{ITEM_IDS.get(iid, iid)}x{qty}"
        for i, (iid, qty) in enumerate(inv_ids)
        if int(iid) == 0x23 or str(ITEM_IDS.get(int(iid), "")) == "music_notes"
    ]
    lines = [
        "[story-mask]",
        (
            f"  room={room} pos=({int(px)},{int(pz)}) "
            f"facing={state.get('facing')} in_control={in_ctrl} skipping={skipping} "
            f"use_phase={use_ph} use_legal={bool(mask[USE_ACTION])} legal_n={int(mask.sum())}"
        ),
        f"  music_notes_inv={notes_slots or 'none'}",
        f"  story_sites={site_ids or 'none'} story_slots={slots}",
    ]
    for site in load_story_use_sites():
        if str(site["room"]) != room:
            continue
        dist = math.hypot(px - float(site["x"]), pz - float(site["z"]))
        in_range = dist <= float(site["radius"])
        lines.append(
            f"  dist {site['id']}: {dist:.0f}/{int(site['radius'])} "
            f"{'IN' if in_range else 'out'}"
        )
    lines.append(f"  actions: {', '.join(legal) if legal else '(none)'}")
    if not in_ctrl or skipping:
        lines.append("  (not in_control — mask is noop-only until skip ends)")
    if use_ph == 1:
        slot_legal = [i for i in range(8) if bool(mask[SELECT_SLOT_BASE + i])]
        lines.append(f"  use_phase=1 select_slots={slot_legal}")
    return "\n".join(lines)


@dataclass(frozen=True)
class StoryUseAffordance:
    use_legal: bool
    site_ids: tuple[str, ...]
    slots: tuple[int, ...]
    in_control: bool
    use_phase: int
    room: str
    pos: tuple[int, int]


def snapshot_story_use_affordance(
    env: RE1Env, state: dict[str, Any]
) -> StoryUseAffordance:
    mask = env.action_masks(state)
    rewarded = set(env._progress.rewarded_story_uses)
    inv_ids = _read_policy_inventory(env)
    if inv_ids is None:
        inv_ids = _inventory_ids_from_state(state)
    room = str(state.get("room_id", ""))
    sites = matching_story_sites(
        room=room,
        x=state.get("x"),
        z=state.get("z"),
        inventory=inv_ids,
        rewarded_site_ids=rewarded,
    )
    slots = legal_story_use_slots(
        inv_ids,
        room=room,
        x=state.get("x"),
        z=state.get("z"),
        rewarded_site_ids=rewarded,
    )
    return StoryUseAffordance(
        use_legal=bool(mask[USE_ACTION]),
        site_ids=tuple(str(s["id"]) for s in sites),
        slots=tuple(int(s) for s in slots),
        in_control=bool(state.get("in_control", True)),
        use_phase=int(getattr(env, "_use_phase", 0)),
        room=room,
        pos=(int(state.get("x") or 0), int(state.get("z") or 0)),
    )


def maybe_log_story_use_affordance(
    env: RE1Env,
    state: dict[str, Any],
    prev: StoryUseAffordance | None,
) -> StoryUseAffordance:
    """Edge-log when inventory USE for a story key becomes legal or stops."""
    cur = snapshot_story_use_affordance(env, state)
    if prev is None:
        if cur.use_legal:
            print(
                f"[story-use] ON room={cur.room} pos={cur.pos} "
                f"sites={list(cur.site_ids)} slots={list(cur.slots)} "
                f"use_phase={cur.use_phase}",
                flush=True,
            )
        elif cur.site_ids and cur.in_control:
            print(
                f"[story-use] NEAR room={cur.room} pos={cur.pos} "
                f"sites={list(cur.site_ids)} slots={list(cur.slots)} "
                f"(USE masked off: use_phase={cur.use_phase})",
                flush=True,
            )
        return cur

    affordance_key = (cur.use_legal, cur.site_ids, cur.slots, cur.use_phase)
    prev_key = (prev.use_legal, prev.site_ids, prev.slots, prev.use_phase)
    if affordance_key == prev_key:
        return cur

    if cur.use_legal and not prev.use_legal:
        print(
            f"[story-use] ON room={cur.room} pos={cur.pos} "
            f"sites={list(cur.site_ids)} slots={list(cur.slots)} "
            f"use_phase={cur.use_phase}",
            flush=True,
        )
    elif not cur.use_legal and prev.use_legal:
        reason = "out of range / no key / already used"
        if not cur.in_control:
            reason = "not in_control"
        elif cur.use_phase == 1 and prev.use_phase == 0:
            reason = "use submenu closed"
        print(
            f"[story-use] OFF room={cur.room} pos={cur.pos} "
            f"(was sites={list(prev.site_ids)} slots={list(prev.slots)}) "
            f"reason={reason}",
            flush=True,
        )
    elif cur.use_legal and prev.use_legal:
        print(
            f"[story-use] UPDATE room={cur.room} pos={cur.pos} "
            f"sites={list(prev.site_ids)}->{list(cur.site_ids)} "
            f"slots={list(prev.slots)}->{list(cur.slots)} "
            f"use_phase={prev.use_phase}->{cur.use_phase}",
            flush=True,
        )
    elif not cur.use_legal and not prev.use_legal and (
        cur.site_ids != prev.site_ids or cur.slots != prev.slots
    ):
        print(
            f"[story-use] NEAR room={cur.room} pos={cur.pos} "
            f"sites={list(cur.site_ids) or 'none'} "
            f"(USE still blocked: in_control={cur.in_control})",
            flush=True,
        )
    return cur


def _poll_screen_ram(bridge: Any) -> dict[str, int]:
    raw = bridge.read_ram(RAM_SCREEN_FIELDS)
    return {k: int(raw[k]) for k in raw}


def _fmt_screen_ram(
    ram: dict[str, int],
    *,
    outside: str | None,
    opening: str | None,
) -> str:
    gs = ram["game_state"]
    opening_bit = f" opening={opening!r}" if opening else ""
    skip = needs_skip_from_ram(ram)
    item_inv = item_inventory_screen_from_ram(ram)
    ctrl = in_control_from_ram(ram)
    return (
        f"hp={ram['player_hp']:3d} room={ram['room_id']:2d} "
        f"gs=0x{gs:08X} mode=0x{ram['game_mode']:02X} "
        f"scene=0x{ram['scene_flag']:02X} msg=0x{ram['msg_flag']:02X} "
        f"in_control={ctrl} needs_skip={skip} item_inv={item_inv} "
        f"outside={outside!r}{opening_bit}"
    )


class RamScreenLogger:
    """Log screen/session RAM whenever bytes change (continues after death)."""

    def __init__(self, *, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._prev: dict[str, int] | None = None
        self._prev_outside: str | None = None
        self._episode_start_hp = 96
        self._death_seen = False
        self._post_shot_idx = 0
        self._had_mansion_hp = False
        self._opening_capture_idx = 0
        self._last_opening_capture = None
        self.enabled = True

    def reset(self, *, episode_start_hp: int = 96) -> None:
        self._prev = None
        self._prev_outside = None
        self._episode_start_hp = max(0, int(episode_start_hp))
        self._death_seen = False
        self._post_shot_idx = 0
        self._had_mansion_hp = self._episode_start_hp > 0
        self._prev_opening = None
        self._opening_capture_idx = 0
        self._last_opening_capture = None

    def _capture_opening_scene(
        self,
        bridge: Any,
        *,
        opening: str,
        ram: dict[str, int],
        outside: str | None,
    ) -> None:
        self._opening_capture_idx += 1
        idx = self._opening_capture_idx
        shot = self._data_dir / f"opening_capture_{idx:03d}_{opening}.png"
        bridge.screenshot(str(shot))
        payload = {
            "opening": opening,
            "outside": outside,
            "ram": ram,
            "screenshot": shot.name,
        }
        path = self._data_dir / "opening_cinematic_capture.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[ram] opening capture -> {path.name} + {shot.name}", flush=True)

    @property
    def had_mansion_hp(self) -> bool:
        return self._had_mansion_hp

    def maybe_log(self, bridge: Any) -> None:
        if not self.enabled:
            return
        try:
            ram = _poll_screen_ram(bridge)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[ram] read error: {exc}", flush=True)
            return

        probe = {
            "player_hp": ram["player_hp"],
            "stage_id": ram["stage_id"],
            "room_id": ram["room_id"],
            "character_id": ram["character_id"],
            "game_mode": ram["game_mode"],
            "game_state": ram["game_state"],
            "msg_flag": ram["msg_flag"],
            "scene_flag": ram["scene_flag"],
        }
        outside = outside_gameplay_reason(probe, episode_start_hp=self._episode_start_hp)
        hp_live = 0 < ram["player_hp"] < 65520
        if hp_live:
            self._had_mansion_hp = True
        opening = opening_phase_from_ram(probe, had_mansion_hp=self._had_mansion_hp)
        changed = (
            self._prev is None
            or ram != self._prev
            or outside != self._prev_outside
            or opening != getattr(self, "_prev_opening", None)
        )
        if not changed:
            return

        tag = "POST_DEATH" if self._death_seen else "LIVE"
        ts = time.strftime("%H:%M:%S")
        print(
            f"[ram {ts}] [{tag}] {_fmt_screen_ram(ram, outside=outside, opening=opening)}",
            flush=True,
        )
        self._prev_opening = opening
        if opening is not None and opening != getattr(self, "_last_opening_capture", None):
            self._capture_opening_scene(bridge, opening=opening, ram=ram, outside=outside)
            self._last_opening_capture = opening

        if ram["player_hp"] > 0 and not self._death_seen:
            self._episode_start_hp = ram["player_hp"]
        if (
            ram["player_hp"] == 0
            and self._episode_start_hp > 0
            and not self._death_seen
        ):
            self._death_seen = True
            path = self._data_dir / "death_monitor_capture.png"
            bridge.screenshot(str(path))
            print(
                f"[ram] *** DEATH (hp=0) — screenshot {path.name} — still monitoring",
                flush=True,
            )
        elif self._death_seen:
            self._post_shot_idx += 1
            path = self._data_dir / f"death_monitor_post_{self._post_shot_idx:03d}.png"
            bridge.screenshot(str(path))
            print(f"[ram] post-death shot {path.name}", flush=True)

        self._prev = dict(ram)
        self._prev_outside = outside


_SHUTDOWN_SESSION: "PlaySession | None" = None


class PlaySession:
    """Owns every resource play_human opens; idempotent tear-down on exit/Ctrl+C."""

    def __init__(self) -> None:
        self.pad: Any = None
        self.kb: Any = None
        self.bridge: Any = None
        self.proc: subprocess.Popen[Any] | None = None
        self.env: RE1Env | None = None
        self._closed = False

    def close(self, *, reason: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        if reason:
            print(f"\n[play] shutting down ({reason})...", flush=True)

        if self.env is not None:
            try:
                self.env.close()
            except Exception:
                pass
            self.env = None
            self.bridge = None
        elif self.bridge is not None:
            try:
                self.bridge.quit()
            except Exception:
                pass
            try:
                self.bridge.close()
            except Exception:
                pass
            self.bridge = None

        if self.proc is not None:
            _stop_emuhawk(self.proc)
            self.proc = None

        if self.pad is not None:
            try:
                self.pad.close()
            except Exception:
                pass
            self.pad = None

        if self.kb is not None:
            try:
                self.kb.unhook_all()
            except Exception:
                pass
            self.kb = None


def _stop_emuhawk(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=3.0)
        return
    except subprocess.TimeoutExpired:
        pass
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass


def _on_shutdown_signal(signum: int, _frame: Any) -> None:
    global _SHUTDOWN_SESSION
    name = "SIGINT" if signum == signal.SIGINT else f"signal {signum}"
    if _SHUTDOWN_SESSION is not None:
        _SHUTDOWN_SESSION.close(reason=name)
    raise KeyboardInterrupt


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in small slices so Ctrl+C lands within ~50ms while idle."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))

# Friendly PS1 button names (lua/re1_client.lua BUTTON_MAP). Remap here.
KEY_BINDINGS: dict[str, str] = {
    "w": "up",
    "up": "up",
    "s": "down",
    "down": "down",
    "a": "left",
    "left": "left",
    "d": "right",
    "right": "right",
    "shift": "square",  # run / quickturn with down
    "z": "cross",  # interact / confirm (DC door-skip uses triangle; see below)
    "e": "cross",
    "r": "r1",  # aim
    "f": "fire_combo",  # r1 + cross
    "x": "triangle",  # door-skip prompt
}

OBJ_ENGLISH = {
    "navigate": "Go there",
    "pickup": "Pick something up",
    "use_item": "Use an item",
    "fight": "Fight",
    "scripted_macro": "Scripted sequence (talk / interact)",
}

COMPASS_8 = [
    "ahead",
    "ahead-right",
    "right",
    "behind-right",
    "behind",
    "behind-left",
    "left",
    "ahead-left",
]

# SDL / pygame standard mapping for DualShock 4 and most XInput pads.
_PAD_CROSS = 0
_PAD_CIRCLE = 1
_PAD_SQUARE = 2
_PAD_TRIANGLE = 3
_PAD_SHARE = 4
_PAD_R1 = 7
_PAD_OPTIONS = 10


class GamepadPoller:
    """Poll a connected gamepad without opening a pygame window."""

    def __init__(self, index: int = 0, *, deadzone: float = 0.35) -> None:
        try:
            import pygame
        except ImportError as exc:
            raise RuntimeError(
                "pygame is required for gamepad input: pip install pygame"
            ) from exc
        self._pygame = pygame
        if not pygame.get_init():
            pygame.init()
        if pygame.joystick.get_count() <= index:
            n = pygame.joystick.get_count()
            raise RuntimeError(
                f"no gamepad at index {index} ({n} controller(s) detected)"
            )
        self.joy = pygame.joystick.Joystick(index)
        self.joy.init()
        self.deadzone = float(deadzone)
        self.name = self.joy.get_name()

    def close(self) -> None:
        try:
            self.joy.quit()
        except Exception:
            pass

    def _btn(self, idx: int) -> bool:
        return bool(self.joy.get_numbuttons() > idx and self.joy.get_button(idx))

    def _axis(self, idx: int) -> float:
        return float(self.joy.get_axis(idx)) if self.joy.get_numaxes() > idx else 0.0

    def poll_play_buttons(self) -> dict[str, bool]:
        """Map stick + face buttons to the same vocabulary as KEY_BINDINGS."""
        self._pygame.event.pump()
        out: dict[str, bool] = {}

        lx, ly = self._axis(0), self._axis(1)
        if ly < -self.deadzone:
            out["up"] = True
        if ly > self.deadzone:
            out["down"] = True
        if lx < -self.deadzone:
            out["left"] = True
        if lx > self.deadzone:
            out["right"] = True

        if self.joy.get_numhats() > 0:
            hx, hy = self.joy.get_hat(0)
            if hy > 0:
                out["up"] = True
            if hy < 0:
                out["down"] = True
            if hx < 0:
                out["left"] = True
            if hx > 0:
                out["right"] = True

        if self._btn(_PAD_SQUARE):
            out["square"] = True
        if self._btn(_PAD_CROSS):
            out["cross"] = True
        if self._btn(_PAD_TRIANGLE):
            out["triangle"] = True
        if self._btn(_PAD_R1):
            out["r1"] = True

        # R2: digital button and/or analog axis (axis index varies by driver).
        r2 = self._btn(9)
        for ax in (4, 5, 2):
            if self._axis(ax) > 0.5:
                r2 = True
        if r2:
            out["r1"] = True
            out["cross"] = True

        return out

    def share_pressed(self) -> bool:
        self._pygame.event.pump()
        return self._btn(_PAD_SHARE)

    def options_pressed(self) -> bool:
        self._pygame.event.pump()
        return self._btn(_PAD_OPTIONS)


def _import_keyboard():
    try:
        import keyboard as kb
    except ImportError:
        print(
            "ERROR: keyboard input requested but package missing.\n"
            "  pip install keyboard\n"
            "Or use --input gamepad and play with a controller only.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return kb


def _human_item(name: str) -> str:
    return canonical_item(name).replace("_", " ")


def _room_name(rooms: dict[str, Any], room_id: str | None) -> str:
    if not room_id:
        return "?"
    info = rooms.get(str(room_id), {})
    return str(info.get("name") or room_id)


def _load_rooms() -> dict[str, Any]:
    with (PROJECT_ROOT / "data" / "rooms.json").open(encoding="utf-8") as f:
        return json.load(f)


def _load_stage_meta(curriculum_path: Path) -> dict[str, Any]:
    with curriculum_path.open(encoding="utf-8") as f:
        return json.load(f)


def _checkpoint_sidecar_for(savestate: Path) -> dict[str, Any] | None:
    rel = savestate.as_posix()
    if not rel.startswith("states/"):
        try:
            rel = savestate.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            rel = savestate.name
    for sidecar in sorted(CKPT_DIR.glob("wp*.json")):
        if sidecar.name.startswith("_"):
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        file_field = str(data.get("file", ""))
        if file_field == rel or savestate.name in file_field:
            return data
    return None


def _list_checkpoints() -> list[tuple[str, Path, dict[str, Any]]]:
    out: list[tuple[str, Path, dict[str, Any]]] = []
    manifest_path = CKPT_DIR / "manifest.json"
    manifest: dict[str, dict] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for sidecar in sorted(CKPT_DIR.glob("wp*.json")):
        if sidecar.name.startswith("_"):
            continue
        key = sidecar.stem
        data = manifest.get(key)
        if data is None:
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
        file_rel = str(data.get("file", ""))
        path = PROJECT_ROOT / file_rel if file_rel else None
        if path is not None and path.is_file():
            out.append((key, path, data))
    return out


def _keys_to_buttons(kb) -> dict[str, bool]:
    buttons: dict[str, bool] = {}
    fire = False
    for key, target in KEY_BINDINGS.items():
        if not kb.is_pressed(key):
            continue
        if target == "fire_combo":
            fire = True
            continue
        buttons[target] = True
    if fire:
        buttons["r1"] = True
        buttons["cross"] = True
    return buttons


def _read_emuhawk_joypad(bridge, *, debug: bool = False) -> dict[str, bool]:
    try:
        out = bridge.read_joypad(debug=debug)
        if debug:
            buttons, raw = out
            if not raw:
                print("[play] joypad raw: (empty — focus EmuHawk window)", flush=True)
            else:
                stick = {
                    k: v
                    for k, v in raw.items()
                    if "Stick" in k or "Axis" in k or "D-Pad" in k or k.startswith("P1 ")
                }
                active = {k: v for k, v in buttons.items() if v}
                # core button names contain unicode glyphs (△□○) that cp1252
                # consoles cannot encode; escape rather than crash
                msg = (
                    f"[play] joypad parsed={active or '(none)'}  "
                    f"raw_keys={len(raw)} stick_fields={stick}"
                )
                print(msg.encode("ascii", "backslashreplace").decode("ascii"), flush=True)
            return buttons
        return out  # type: ignore[return-value]
    except Exception as exc:
        if debug:
            print(f"[play] joypad read failed: {exc}", flush=True)
        return {}


def _joypad_to_play_buttons(raw: dict[str, bool]) -> dict[str, bool]:
    merged: dict[str, bool] = {}
    for name, pressed in raw.items():
        # start/select are harness controls (quit/hints); l1 is manual turbo
        if not pressed or name in ("start", "select", "l1"):
            continue
        if name == "r2":
            merged["r1"] = True
            merged["cross"] = True
        else:
            merged[name] = True
    return merged


def _poll_play_buttons(
    *,
    kb,
    bridge,
    use_keyboard: bool,
    use_emuhawk_joypad: bool,
) -> dict[str, bool]:
    merged: dict[str, bool] = {}
    if use_keyboard and kb is not None:
        merged.update(_keys_to_buttons(kb))
    if use_emuhawk_joypad and bridge is not None:
        merged.update(_joypad_to_play_buttons(_read_emuhawk_joypad(bridge)))
    return merged


def _bearing_compass(sin_b: float, cos_b: float) -> str:
    ang = math.atan2(sin_b, cos_b)
    idx = int(round(ang / (2.0 * math.pi) * 8.0)) % 8
    return COMPASS_8[idx]


def _goal_field(goal: Any, name: str) -> float:
    if goal is None:
        return 0.0
    idx = next(i for i, (n, _) in enumerate(GOAL_FIELDS) if n == name)
    return float(goal[idx])


def _subcondition_satisfied(
    env: RE1Env,
    state: dict[str, Any],
    obj: dict[str, Any],
    cond: dict[str, Any],
) -> bool:
    from re1_rl.planner import WaypointPlanner

    wp = str(obj.get("room_id", ""))
    return WaypointPlanner._condition_met(cond, state, wp, env._progress, env._prev_state)


def _sync_planner_from_live_state(env: RE1Env, state: dict[str, Any]) -> int:
    """Advance planner when live RAM is ahead of checkpoint index (no reward)."""
    assert env._planner is not None
    prev = dict(env._prev_state)
    if env._progress is not None:
        env._progress.first_visit(
            str(state.get("room_id", "")),
            at_waypoint=env._planner.waypoint_index,
            at_route_seq=env._planner.current_route_seq(),
        )
    n = 0
    for _ in range(16):
        old = env._planner.waypoint_index
        if not env._planner.advance_if_success(
            state, progress=env._progress, prev_state=prev
        ):
            break
        env._progress.on_waypoint_advanced()
        env._progress.rewarded_waypoint_indices.add(old)
        env._progress.max_waypoint = max(env._progress.max_waypoint, old + 1)
        n += 1
    return n


def _objective_progress_line(env: RE1Env, state: dict[str, Any]) -> str | None:
    """How close the live game state is to the current route step's success_condition."""
    assert env._planner is not None
    obj = env._planner.current_objective() or {}
    cond = obj.get("success_condition")
    if not isinstance(cond, dict):
        return None
    if cond.get("type") == "any_of":
        for sub in cond.get("conditions", []):
            if not isinstance(sub, dict):
                continue
            if _subcondition_satisfied(env, state, obj, sub):
                continue
            return _objective_progress_line_for_cond(env, state, obj, sub)
        return None
    return _objective_progress_line_for_cond(env, state, obj, cond)


def _objective_progress_line_for_cond(
    env: RE1Env,
    state: dict[str, Any],
    obj: dict[str, Any],
    cond: dict[str, Any],
) -> str | None:
    ctype = cond.get("type")
    if ctype == "has_item":
        want = canonical_item(str(cond.get("item", "")))
        inv = canonicalize(state.get("inventory", []))
        in_room = str(state.get("room_id", "")) == str(obj.get("room_id", ""))
        if want in inv:
            return f"Checkpoint progress: have {_human_item(want)} — need to be in room {obj.get('room_id')}" + (
                "" if in_room else f" (currently in {state.get('room_id')})"
            )
        return f"Checkpoint progress: need {_human_item(want)} in room {obj.get('room_id')}"
    if ctype == "room_enter":
        want = str(cond.get("room_id", obj.get("room_id", "")))
        return f"Checkpoint progress: enter room {want} (now in {state.get('room_id')})"
    if ctype == "room_enter_any":
        allowed = ", ".join(str(r) for r in cond.get("room_ids", []))
        return f"Checkpoint progress: enter one of [{allowed}] (now in {state.get('room_id')})"
    if ctype == "visited_any":
        allowed = ", ".join(str(r) for r in cond.get("room_ids", []))
        visited = ", ".join(sorted(env._progress.visited_rooms & {str(r) for r in cond.get("room_ids", [])}))
        if visited:
            return f"Checkpoint progress: visited [{visited}] — finish this step in room {obj.get('room_id')}"
        return f"Checkpoint progress: visit one of [{allowed}] (now in {state.get('room_id')})"
    if ctype == "room_enter_from":
        target = str(cond.get("room_id", obj.get("room_id", "")))
        from_ids = ", ".join(str(r) for r in cond.get("from_room_ids", []))
        return (
            f"Checkpoint progress: enter room {target} from [{from_ids}] "
            f"(now in {state.get('room_id')})"
        )
    if ctype == "in_control_steps_in_room":
        target = str(cond.get("room_id", obj.get("room_id", "")))
        need = int(cond.get("min_steps", 1))
        have = env._progress.in_control_steps_in_room(target)
        ctrl = "yes" if state.get("in_control") else "no (cutscene/door — auto-skipping)"
        return f"Checkpoint progress: {have}/{need} in-control steps in room {target}  [in_control={ctrl}]"
    return None


def format_hints_panel(
    env: RE1Env,
    state: dict[str, Any],
    goal_vec: Any,
    *,
    rooms: dict[str, Any],
    route_steps: list[int],
    steps_by_seq: dict[int, dict[str, Any]],
) -> str:
    cur_room = str(state.get("room_id", "?"))
    hp = int(state.get("hp", 0))
    ctrl = "yes" if state.get("in_control") else "no"

    if not ENABLE_CHECKPOINT_PATH:
        visited = sorted(env._progress.visited_rooms)
        lines = [
            "--- status ---",
            f"Room: {_room_name(rooms, cur_room)} ({cur_room})  |  HP: {hp}/140  |  in_control: {ctrl}",
            f"Visited this episode: {', '.join(visited) or '(none)'} ({len(visited)} room{'s' if len(visited) != 1 else ''})",
        ]
        return "\n".join(lines)

    assert env._planner is not None
    planner = env._planner
    total = max(planner.total_waypoints, 1)
    cp_num = planner.waypoint_index + 1
    obj = planner.current_objective() or {}
    objective_text = str(obj.get("objective", "(stage complete)"))
    goal_room = planner.next_waypoint_room()
    cur_room = str(state.get("room_id", "?"))

    lines = [
        "--- hints ---",
        f"Checkpoint {cp_num} of {total}: {objective_text}",
        f"Room: {_room_name(rooms, cur_room)}  |  Goal: {_room_name(rooms, goal_room)}",
    ]

    hops = env.graph.hop_distance(cur_room, goal_room) if goal_room else None
    wrong = _goal_field(goal_vec, "wrong_room_flag") > 0.5
    if wrong:
        lines.append(f"WARNING: Off-route — {_room_name(rooms, cur_room)} is not on the known path.")
    if goal_room is None:
        lines.append("Distance: (stage complete)")
    elif hops is None and cur_room != str(goal_room):
        lines.append("Distance: path not mapped yet — no compass available")
    elif hops is not None:
        lines.append(f"Distance: {hops} room{'s' if hops != 1 else ''} away")

    if _goal_field(goal_vec, "doors_available") > 0.5:
        sin_b = _goal_field(goal_vec, "door_bearing_sin")
        cos_b = _goal_field(goal_vec, "door_bearing_cos")
        dist_norm = _goal_field(goal_vec, "door_distance")
        dist_m = dist_norm * 4096.0
        lines.append(
            f"Direction: door is to your {_bearing_compass(sin_b, cos_b)}, ~{dist_m:.1f} units"
        )

    obj_type = planner.objective_type()
    lines.append(f"Objective type: {OBJ_ENGLISH.get(obj_type, obj_type)}")

    done, total_items = env._items.progress()
    lines.append(f"Route items: {done}/{total_items} collected")
    required = canonicalize(planner.required_items())
    obj_req = canonicalize((obj or {}).get("required_items", []))
    required |= obj_req
    missing = sorted(_human_item(x) for x in required - env._items.ever_held)
    if missing:
        lines.append(f"Missing for this step: {', '.join(missing)}")
    elif required:
        lines.append("Holding all required items for this step.")

    progress = _objective_progress_line(env, state)
    if progress:
        lines.append(progress)

    return "\n".join(lines)


def _human_combat_attempt(buttons: dict[str, bool]) -> tuple[bool, bool]:
    """True when this step pressed fire (R1+Cross) — matches attack macro input."""
    attack = bool(buttons.get("r1") and buttons.get("cross"))
    return False, attack


def format_reward_panel(
    breakdown: dict[str, float],
    reward: float,
    *,
    rooms: dict[str, Any],
    route_steps: list[int],
    steps_by_seq: dict[int, dict[str, Any]],
    planner,
    graph,
    prev_state: dict[str, Any],
    state: dict[str, Any],
    quiet: bool,
) -> str | None:
    lines: list[str] = []
    interesting = False

    if ENABLE_CHECKPOINT_PATH:
        pg = breakdown.get("pbrs_graph", 0.0)
        if abs(pg) > REWARD_EPS:
            goal = planner.next_waypoint_room()
            hops = graph.hop_distance(str(state.get("room_id", "")), goal) if goal else None
            hop_txt = f" ({hops} room-hops)" if hops is not None else ""
            direction = "closer to" if pg > 0 else "further from"
            lines.append(
                f"{direction.title()} {_room_name(rooms, goal)}{hop_txt} [pbrs_graph {pg:+.3f}]"
            )
            interesting = True

        pd = breakdown.get("pbrs_door", 0.0)
        if abs(pd) > REWARD_EPS:
            direction = "closer to" if pd > 0 else "further from"
            lines.append(f"{direction.title()} the exit door [pbrs_door {pd:+.3f}]")
            interesting = True

        if breakdown.get("waypoint", 0.0):
            completed = max(0, planner.waypoint_index - 1)
            seq = route_steps[completed] if completed < len(route_steps) else None
            obj_text = steps_by_seq.get(int(seq or 0), {}).get(
                "objective", f"waypoint {completed + 1}"
            )
            lines.append(f"CHECKPOINT COMPLETE: {obj_text}")
            interesting = True

        if breakdown.get("retreat", 0.0):
            lines.append("You backed out of the objective room.")
            interesting = True

        if breakdown.get("wrong_room", 0.0):
            lines.append(
                f"Off-route: {_room_name(rooms, state.get('room_id'))} is not on the known path."
            )
            interesting = True

        if breakdown.get("success_room", 0.0):
            lines.append("STAGE GOAL ROOM REACHED.")
            interesting = True

    if breakdown.get("new_room", 0.0):
        lines.append(f"New room: {_room_name(rooms, state.get('room_id'))}.")
        interesting = True

    if breakdown.get("new_cutscene", 0.0):
        lines.append("New cutscene (exploration bonus).")
        interesting = True

    enemy_damage = int(state.get("enemy_damage", 0) or 0)
    if breakdown.get("enemy_damage", 0.0):
        lines.append(f"Hit enemy for {enemy_damage} damage.")
        interesting = True

    enemy_kills = int(state.get("enemy_kills", 0) or 0)
    if breakdown.get("enemy_kill", 0.0):
        noun = "kill" if enemy_kills == 1 else "kills"
        lines.append(f"Enemy {noun} ({enemy_kills}).")
        interesting = True

    if breakdown.get("attack_miss", 0.0) and not quiet:
        lines.append("Attack missed (no enemy damage).")
        interesting = True

    if breakdown.get("ammo_waste", 0.0) and not quiet:
        lines.append("Wasted ammo on a miss.")
        interesting = True

    if breakdown.get("key_item", 0.0):
        new_items = state.get("new_items") or []
        if new_items:
            names = ", ".join(_human_item(x) for x in new_items)
            lines.append(f"Picked up key item {names}.")
        else:
            lines.append("Picked up a key item.")
        interesting = True
    if breakdown.get("story_use", 0.0):
        site = state.get("story_use_success", "")
        lines.append(f"Story item USE bonus{f' ({site})' if site else ''}.")
        interesting = True
    elif breakdown.get("gold_emblem_return", 0.0):
        lines.append("Gold emblem put back at 10F alcove (no wooden swap).")
        interesting = True
    elif breakdown.get("shotgun_return", 0.0):
        lines.append("Shotgun put back on the wall.")
        interesting = True
    elif breakdown.get("new_weapon", 0.0):
        new_items = state.get("new_items") or []
        if new_items:
            names = ", ".join(_human_item(x) for x in new_items)
            lines.append(f"Picked up new weapon {names}.")
        else:
            lines.append("Picked up a new weapon.")
        interesting = True
    elif breakdown.get("item", 0.0):
        new_items = state.get("new_items") or []
        if new_items:
            names = ", ".join(_human_item(x) for x in new_items)
            lines.append(f"Picked up {names}.")
        else:
            lines.append("Picked up an item.")
        interesting = True

    hp_term = breakdown.get("hp", 0.0)
    if hp_term:
        hp = int(state.get("hp", 0))
        delta = int(hp - int(prev_state.get("hp", hp)))
        if hp_term < 0:
            lines.append(f"Took damage: {delta} HP (now {hp}/140) [{hp_term:+.3f}].")
        else:
            lines.append(f"Healed: {delta:+d} HP (now {hp}/140) [{hp_term:+.3f}].")
        interesting = True

    if breakdown.get("death", 0.0):
        lines.append(f"YOU DIED. [{breakdown['death']:+.3f}]")
        interesting = True

    if breakdown.get("softlock", 0.0):
        lines.append(
            f"Stagnation timeout (idle contempt) [{breakdown['softlock']:+.3f}]."
        )
        interesting = True

    if quiet and not interesting:
        return None

    if not quiet and breakdown.get("step", 0.0):
        lines.append(f"Step penalty: {breakdown['step']:+.3f}")

    if not lines and abs(reward) < 1e-9:
        return None

    header = [f"Reward: {reward:+.4f} (scale x{REWARD_SCALE})"]
    return "\n".join(header + lines)


def _apply_checkpoint_meta(env: RE1Env, meta: dict[str, Any]) -> None:
    idx = int(meta.get("waypoint_index", 0))
    assert env._planner is not None
    env._planner._index = idx
    env._progress.max_waypoint = idx
    env._progress.rewarded_waypoint_indices = set(range(idx))


def _finalize_bootstrap_state(env: RE1Env) -> tuple[dict[str, Any], Any]:
    """Read live RAM and seed per-episode exploration state like env.reset()."""
    env._prev_hp = 0
    state = env._read_state()
    env._seed_episode_progress(state)
    env._visited.update(state["room_id"], state["x"], state["z"])
    env._prev_state = dict(state)
    env._prev_hp = state["hp"] if state["hp"] > 0 else 0
    env._cutscene_skip_frames = 0
    env._cutscene_skip_entry_prev = None
    goal = env._encoder.encode_goal(  # type: ignore[union-attr]
        state,
        env._planner,
        item_tracker=env._items,
        room_items=env.room_items,
    )
    return state, goal


def _log_exploration_hit(
    explore: TrainingProgressTracker,
    *,
    state: dict[str, Any],
    breakdown: dict[str, float],
    step: int,
    env: RE1Env,
) -> None:
    """Mirror fleet [progress] / [rollout] exploration counters."""
    wp = env._planner.waypoint_index if env._planner else 0
    explore.consume_infos(
        [
            {
                "room_id": state.get("room_id"),
                "max_waypoint": wp,
                "reward_breakdown": breakdown,
            }
        ],
        num_timesteps=step,
    )
    if breakdown.get("new_room", 0) > 0:
        print(
            f"[{explore.prefix}] new_room +{NEW_ROOM_BONUS:.1f} "
            f"(room {state.get('room_id')})",
            flush=True,
        )
    if breakdown.get("new_cutscene", 0) > 0:
        print(f"[{explore.prefix}] new_cutscene +{NEW_CUTSCENE_BONUS:.1f}", flush=True)
    if breakdown.get("new_weapon", 0) > 0:
        print(f"[{explore.prefix}] new_weapon +{NEW_WEAPON_PICKUP_BONUS:.1f}", flush=True)
    if breakdown.get("enemy_damage", 0) > 0:
        print(
            f"[{explore.prefix}] enemy_damage +{breakdown['enemy_damage']:.4f} "
            f"({int(state.get('enemy_damage', 0))} hp)",
            flush=True,
        )
    if breakdown.get("enemy_kill", 0) > 0:
        print(f"[{explore.prefix}] enemy_kill +{breakdown['enemy_kill']:.4f}", flush=True)


def configure_ram_skip(
    env: RE1Env,
    speed: int,
    *,
    cutscene_speed: int,
    turbo_patches: bool,
    invisible_cutscenes: bool,
    skip_chunk: int = 600,
) -> None:
    env._ram_skip = RamSkipper(
        env.bridge,
        training_speed=int(speed),
        cutscene_speed=int(cutscene_speed),
        skip_chunk=int(skip_chunk),
        use_engine_patches=bool(turbo_patches),
        invisible_during_skip=bool(invisible_cutscenes),
    )


def bootstrap_fleet_reset(
    env: RE1Env,
    *,
    play_speed: int,
    cutscene_speed: int,
    skip_chunk: int,
    turbo_patches: bool,
    invisible_cutscenes: bool,
) -> tuple[dict[str, Any], Any]:
    """Mirror ``RE1Env.reset()`` bootstrap — same savestate, skip, fresh tracker."""
    from re1_rl.knife_equip import equip_knife_from_pause_menu
    from re1_rl.progress import ProgressTracker

    env._load_stage()
    configure_ram_skip(
        env,
        play_speed,
        cutscene_speed=cutscene_speed,
        turbo_patches=turbo_patches,
        invisible_cutscenes=invisible_cutscenes,
        skip_chunk=skip_chunk,
    )
    if turbo_patches:
        env._ram_skip.install_engine_patches()
        print("[play] cutscene turbo patches on (door-skip + in-engine 2x)", flush=True)
    else:
        env._ram_skip.clear_engine_patches()

    state_path = env.project_root / env._stage["init_savestate"]
    print(f"[play] fleet reset: loading {state_path.name} (training init)...", flush=True)
    env.bridge.load_savestate(str(state_path))
    env.bridge.frameadvance(1)
    if turbo_patches:
        env._ram_skip.install_engine_patches()

    env.bridge.set_speed(int(cutscene_speed))
    print("[play] skipping menus/cutscenes to player control...", flush=True)
    skipped, died = env._skip_uncontrolled()
    print(f"[play] bootstrap skip done ({skipped} frames)", flush=True)
    if died:
        print("[play] WARN: death during bootstrap skip", flush=True)

    if env._stage.get("knife_equipped_start"):
        try:
            equip_knife_from_pause_menu(env.bridge)
            extra, _ = env._skip_uncontrolled()
            if extra:
                print(f"[play] knife_equipped_start skip ({extra} frames)", flush=True)
        except (OSError, RuntimeError, ValueError):
            pass

    env._sticky_input.reset()
    env._prev_action = None
    env._use_phase = 0
    env._equip_phase = 0
    env._combine_phase = 0
    env._combine_slot_a = None
    env._last_skip_frames = 0
    env._init_anim_history()

    env._step_count = 0
    env._frame_stack = []
    env._progress = ProgressTracker()
    env._visited.reset()
    env._box_cache = None

    env.bridge.set_speed(int(play_speed))
    if invisible_cutscenes:
        try:
            env.bridge.set_invisible(False)
        except (OSError, RuntimeError, ValueError):
            pass

    return _finalize_bootstrap_state(env)


def _catch_up_planner_index(env: RE1Env) -> int:
    """Align planner to savestate inventory/room without paying rewards."""
    assert env._planner is not None
    n = 0
    for _ in range(32):
        state = env._read_state()
        prev = dict(env._prev_state)
        if env._progress is not None:
            env._progress.first_visit(
                str(state.get("room_id", "")),
                at_waypoint=env._planner.waypoint_index,
                at_route_seq=env._planner.current_route_seq(),
            )
        old = env._planner.waypoint_index
        if not env._planner.advance_if_success(
            state, progress=env._progress, prev_state=prev
        ):
            break
        env._progress.on_waypoint_advanced()
        env._progress.rewarded_waypoint_indices.add(old)
        env._progress.max_waypoint = max(env._progress.max_waypoint, old + 1)
        n += 1
    return n


def bootstrap_skip_to_control(env: RE1Env, *, max_total: int = 12000, chunk: int = 120) -> int:
    """Chunked cutscene skip with progress (human bootstrap; avoids silent long runs)."""
    from re1_rl.ram_skip import SKIP_POLL_FIELDS

    total = 0
    while total < max_total:
        ram = env.bridge.read_ram(SKIP_POLL_FIELDS)
        if not needs_skip_from_ram(ram):
            return total
        burned, _ = env._ram_skip.skip_uncontrolled(max_frames=chunk)
        total += burned
        if burned == 0:
            break
        if total % 480 == 0 or burned < chunk:
            print(f"[play] bootstrap skip… {total} frames", flush=True)
    return total


def bootstrap_env(
    env: RE1Env,
    *,
    savestate: Path,
    checkpoint_meta: dict[str, Any] | None,
    play_speed: int,
    cutscene_speed: int,
    turbo_patches: bool,
    invisible_cutscenes: bool,
    warp_room: str | None = None,
    skip_to_control: bool = True,
) -> tuple[dict[str, Any], Any]:
    print("[play] loading curriculum...", flush=True)
    env._load_stage()
    configure_ram_skip(
        env,
        play_speed,
        cutscene_speed=cutscene_speed,
        turbo_patches=turbo_patches,
        invisible_cutscenes=invisible_cutscenes,
        skip_chunk=600,
    )
    if turbo_patches:
        env._ram_skip.install_engine_patches()
        print("[play] cutscene turbo patches on (door-skip + in-engine 2x)", flush=True)
    else:
        env._ram_skip.clear_engine_patches()

    resolved = savestate.resolve()
    print(f"[play] loading savestate {resolved}...", flush=True)
    env.bridge.load_savestate(str(resolved))
    print("[play] savestate loaded; advancing one frame...", flush=True)
    env.bridge.frameadvance(1)
    if skip_to_control:
        print("[play] skipping menus/cutscenes to player control...", flush=True)
        skipped = bootstrap_skip_to_control(env)
        print(f"[play] bootstrap skip done ({skipped} frames)", flush=True)
    else:
        print("[play] bootstrap skip disabled — savestate loaded as-is", flush=True)

    if warp_room:
        from re1_rl.warp import warp_sequence

        codes = [c.strip().upper() for c in str(warp_room).split(",") if c.strip()]
        print(f"[play] warping: {' -> '.join(codes)}...", flush=True)
        snap = warp_sequence(
            env.bridge,
            codes,
            data_dir=env.project_root / "data",
            hp=96,
        )
        room_hex = f"{int(snap['stage_id']) + 1}{int(snap['room_id']):02X}"
        print(
            f"[play] warp settle room={room_hex} "
            f"pos=({snap['player_x']},{snap['player_z']}) "
            f"hp={snap['player_hp']} mode=0x{snap['game_mode']:02X} "
            f"gs=0x{snap['game_state']:08X}",
            flush=True,
        )

    env._step_count = 0
    env._frame_stack = []
    from re1_rl.progress import ProgressTracker

    env._progress = ProgressTracker()
    if checkpoint_meta:
        _apply_checkpoint_meta(env, checkpoint_meta)
    else:
        synced = _catch_up_planner_index(env)
        if synced:
            print(f"[play] planner synced {synced} completed leg(s) from savestate", flush=True)

    env._prev_hp = 0
    state, goal = _finalize_bootstrap_state(env)
    return state, goal


def bootstrap_load_slot(
    env: RE1Env,
    *,
    slot: int,
    play_speed: int,
    cutscene_speed: int,
    turbo_patches: bool,
    invisible_cutscenes: bool,
    warp_room: str | None = None,
) -> tuple[dict[str, Any], Any]:
    """Power-on ROM, load memory-card save, skip to control, optional warp."""
    from re1_rl.ingame_save import load_memory_card_slot, wait_for_loaded_save
    from re1_rl.warp import warp_sequence

    print(f"[play] in-game load: memory card slot {slot}...", flush=True)
    env._load_stage()
    configure_ram_skip(
        env,
        play_speed,
        cutscene_speed=cutscene_speed,
        turbo_patches=turbo_patches,
        invisible_cutscenes=invisible_cutscenes,
        skip_chunk=600,
    )
    if turbo_patches:
        env._ram_skip.install_engine_patches()
        print("[play] cutscene turbo patches on (door-skip + in-engine 2x)", flush=True)
    else:
        env._ram_skip.clear_engine_patches()

    # Menu automation is fragile at turbo speed; keep 1:1 until gameplay loads.
    env.bridge.set_speed(int(play_speed))
    load_memory_card_slot(env.bridge, int(slot))
    print("[play] waiting for save to finish loading...", flush=True)
    loaded_ok, _loaded_ram = wait_for_loaded_save(env.bridge, timeout_frames=24000)
    if not loaded_ok:
        print("[play] WARN: save load did not reach gameplay in time", flush=True)
    env.bridge.set_speed(int(cutscene_speed))
    print("[play] skipping menus/cutscenes to player control...", flush=True)
    skipped = bootstrap_skip_to_control(env)
    print(f"[play] bootstrap skip done ({skipped} frames)", flush=True)

    if warp_room:
        codes = [c.strip().upper() for c in str(warp_room).split(",") if c.strip()]
        print(f"[play] warping: {' -> '.join(codes)}...", flush=True)
        snap = warp_sequence(
            env.bridge,
            codes,
            data_dir=env.project_root / "data",
            hp=None,
        )
        room_hex = f"{int(snap['stage_id']) + 1}{int(snap['room_id']):02X}"
        print(
            f"[play] warp settle room={room_hex} "
            f"pos=({snap['player_x']},{snap['player_z']}) "
            f"hp={snap['player_hp']} mode=0x{snap['game_mode']:02X} "
            f"gs=0x{snap['game_state']:08X}",
            flush=True,
        )

    env._step_count = 0
    env._frame_stack = []
    from re1_rl.progress import ProgressTracker

    env._progress = ProgressTracker()
    synced = _catch_up_planner_index(env)
    if synced:
        print(f"[play] planner synced {synced} completed leg(s) from save", flush=True)

    env._prev_hp = 0
    return _finalize_bootstrap_state(env)


def bootstrap_fresh_rom(
    env: RE1Env,
    *,
    play_speed: int,
    cutscene_speed: int,
    turbo_patches: bool,
    invisible_cutscenes: bool,
) -> tuple[dict[str, Any], Any]:
    """Power-on ROM — no savestate; observe title / opening cutscenes as-is."""
    print("[play] fresh ROM boot (no savestate)...", flush=True)
    env._load_stage()
    configure_ram_skip(
        env,
        play_speed,
        cutscene_speed=cutscene_speed,
        turbo_patches=turbo_patches,
        invisible_cutscenes=invisible_cutscenes,
        skip_chunk=600,
    )
    if turbo_patches:
        env._ram_skip.install_engine_patches()
        print("[play] cutscene turbo patches on (door-skip + in-engine 2x)", flush=True)
    else:
        env._ram_skip.clear_engine_patches()

    env.bridge.frameadvance(2)
    print("[play] ROM running from cold start — mash Start/Cross to begin", flush=True)

    env._step_count = 0
    env._frame_stack = []
    from re1_rl.progress import ProgressTracker

    env._progress = ProgressTracker()
    return _finalize_bootstrap_state(env)


def _pid_listening_on(port: int) -> int | None:
    try:
        out = subprocess.check_output(["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    needle = f"127.0.0.1:{port}"
    for line in out.splitlines():
        if needle in line and "LISTENING" in line:
            parts = line.split()
            if parts:
                try:
                    return int(parts[-1])
                except ValueError:
                    pass
    return None


def _kill_stale_listener(port: int) -> None:
    pid = _pid_listening_on(port)
    if pid is None or pid == os.getpid():
        return
    print(f"[play] freeing port {port}: killing stale listener PID {pid}", flush=True)
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)


def _count_emuhawk() -> int:
    try:
        out = subprocess.check_output(["tasklist"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return 0
    return sum(1 for line in out.splitlines() if "EmuHawk.exe" in line)


def wait_for_emuhawk(
    bridge,
    proc: subprocess.Popen[Any] | None,
    *,
    port: int,
    timeout: float,
    log_path: Path,
) -> None:
    """Accept with periodic status; fail fast if EmuHawk dies or times out."""
    assert bridge._server is not None
    started = time.time()
    deadline = started + timeout
    bridge._server.settimeout(2.0)
    last_msg = 0.0
    print(f"[play] listening on 127.0.0.1:{port} — waiting for EmuHawk lua client...", flush=True)
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            tail = ""
            if log_path.is_file():
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-1200:]
            raise RuntimeError(
                f"EmuHawk exited (code {proc.returncode}) before connecting to port {port}.\n"
                f"See log: {log_path}\n--- tail ---\n{tail}"
            )
        try:
            client, addr = bridge._server.accept()
            bridge._client = client
            client.settimeout(bridge.timeout)
            hello = json.loads(bridge._decode_message(client))
            if hello.get("hello") != "re1_client":
                raise ConnectionError(f"unexpected hello from Lua client: {hello!r}")
            print(f"[play] connected ({addr[0]}:{addr[1]})", flush=True)
            return
        except socket.timeout:
            now = time.time()
            if now - last_msg >= 10.0:
                elapsed = int(now - started)
                print(
                    f"[play] still waiting ({elapsed}s) — EmuHawk must use "
                    f"--socket_port={port}",
                    flush=True,
                )
                last_msg = now
        except KeyboardInterrupt:
            raise
    raise TimeoutError(
        f"EmuHawk did not connect within {int(timeout)}s on port {port}.\n"
        f"  - Close orphan EmuHawk windows from failed starts\n"
        f"  - Training may be starving RAM ({_count_emuhawk()} EmuHawk.exe running now)\n"
        f"  - Log: {log_path}\n"
        f"  - Or launch manually: EmuHawk.exe <rom> --lua=lua/re1_client.lua "
        f"--socket_ip=127.0.0.1 --socket_port={port}"
    )


def launch_emuhawk(port: int, log_path: Path) -> subprocess.Popen[Any]:
    if not EMUHAWK.is_file():
        raise FileNotFoundError(f"EmuHawk not found at {EMUHAWK}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = log_path.open("w", encoding="utf-8")
    print(f"[play] launching EmuHawk (log: {log_path})", flush=True)
    return subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )


def human_advance(
    env: RE1Env,
    buttons: dict[str, bool],
    *,
    stage: dict[str, Any],
    debug_combat: bool = False,
) -> tuple[dict[str, Any], float, dict[str, float], Any, dict[str, Any]]:
    """One in-control decision: sticky hold for frame_skip frames (matches training)."""
    assert env._planner is not None
    from re1_rl.pushable import (
        FORWARD_ACTION,
        RUN_FORWARD_ACTION,
        forward_hold_frames,
        update_forward_collision_stall,
    )

    prev_wp = env._planner.waypoint_index
    prev_state = dict(env._prev_state)
    inv_before = _read_policy_inventory(env)
    sticky, pulse, pulse_hold = human_buttons_to_step(buttons)
    knife, attack = _human_combat_attempt(buttons)
    # Match training: W / W+run extend to 20f when jammed on a pushable.
    action_hint = RUN_FORWARD_ACTION if sticky.get("square") else FORWARD_ACTION
    if not sticky.get("up"):
        hold_n = env.frame_skip
    else:
        hold_n = forward_hold_frames(
            env._prev_state,
            action=action_hint,
            frame_skip=env.frame_skip,
            forward_collision_stall=bool(
                getattr(env, "_forward_collision_stall", False)
            ),
        )
    env.bridge.step(
        n=hold_n,
        sticky=sticky,
        pulse=pulse,
        pulse_hold=pulse_hold,
    )
    # Cutscene/door skip is handled by the main-loop auto skipper (not here).

    env._step_count += 1
    state = env._read_state()
    state = apply_combat_step_fields(
        env._prev_state,
        state,
        knife=knife,
        attack=attack,
    )
    if sticky.get("up"):
        env._forward_collision_stall = update_forward_collision_stall(
            env._prev_state,
            state,
            action=action_hint,
        )
    else:
        env._forward_collision_stall = False
    if debug_combat and (knife or attack):
        prev_n = len(env._prev_state.get("enemies", []) or [])
        cur_n = len(state.get("enemies", []) or [])
        print(
            f"[combat] attempt attack={attack} knife={knife} "
            f"enemies {prev_n}->{cur_n} "
            f"dmg={state.get('enemy_damage', 0)} kills={state.get('enemy_kills', 0)}",
            flush=True,
        )
    env._progress.record_in_control_step(
        state.get("room_id", ""),
        bool(state.get("in_control", True)),
    )

    inv_after = _read_policy_inventory(env)
    state = _annotate_story_use(
        env, prev_state, state, inv_before, inv_after
    )

    reward, breakdown = compute_reward(
        prev_state,
        state,
        env._planner,
        progress=env._progress,
        graph=env.graph,
        success_room=stage.get("success_room"),
        return_breakdown=True,
    )

    env._prev_state = dict(state)
    if state["hp"] > 0:
        env._prev_hp = state["hp"]

    goal = env._encoder.encode_goal(  # type: ignore[union-attr]
        state,
        env._planner,
        item_tracker=env._items,
        room_items=env.room_items,
    )
    info = {
        "room_id": state["room_id"],
        "waypoint_index": env._planner.waypoint_index,
        "prev_waypoint_index": prev_wp,
        "reward_breakdown": breakdown,
        "frames_skipped": 0,
        "state": state,
    }
    return state, float(reward), breakdown, goal, info


def _credit_skip_room_crossing(
    env: RE1Env,
    entry_prev: dict[str, Any],
    state: dict[str, Any],
    *,
    stage: dict[str, Any],
    explore: TrainingProgressTracker | None = None,
    game_frames: int = 0,
    quiet: bool = False,
    rooms: dict[str, str] | None = None,
    route_steps: list[int] | None = None,
    steps_by_seq: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], int]:
    """After a door crossing mid-skip: pay ``new_room``, restart script segment."""
    if str(state.get("room_id", "")) == str(entry_prev.get("room_id", "")):
        return entry_prev, int(getattr(env, "_cutscene_skip_frames", 0))

    crossing = dict(state)
    crossing["cutscene_key"] = None
    env._progress.record_in_control_step(
        str(crossing.get("room_id", "")),
        bool(crossing.get("in_control", True)),
    )
    reward, breakdown = compute_reward(
        entry_prev,
        crossing,
        env._planner,
        progress=env._progress,
        graph=env.graph,
        success_room=stage.get("success_room"),
        return_breakdown=True,
    )
    if explore is not None and (reward or breakdown):
        reward_txt = format_reward_panel(
            breakdown,
            reward,
            rooms=rooms or {},
            route_steps=route_steps or [],
            steps_by_seq=steps_by_seq or {},
            planner=env._planner,
            graph=env.graph,
            prev_state=entry_prev,
            state=crossing,
            quiet=quiet,
        )
        if reward_txt:
            print("--- door crossing ---", flush=True)
            print(reward_txt, flush=True)
        _log_exploration_hit(
            explore,
            state=crossing,
            breakdown=breakdown,
            step=game_frames,
            env=env,
        )
    env._prev_state = dict(crossing)
    env._cutscene_skip_entry_prev = dict(crossing)
    env._cutscene_skip_frames = 0
    return env._cutscene_skip_entry_prev, 0


def cutscene_skip_chunk(
    env: RE1Env,
    *,
    stage: dict[str, Any],
    max_frames: int,
    debug: bool = False,
    gate_log: bool = False,
    explore: TrainingProgressTracker | None = None,
    game_frames: int = 0,
    quiet: bool = False,
    rooms: dict[str, str] | None = None,
    route_steps: list[int] | None = None,
    steps_by_seq: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], float, dict[str, float], Any, int]:
    """Fast-forward one chunk while not in player control.

    Accumulates skip frames across chunks and pays exploration rewards only when
    the skip session ends — same gating as fleet async ``_apply_post_skip_sync``.
    """
    assert env._planner is not None
    entry_prev = getattr(env, "_cutscene_skip_entry_prev", None)
    if entry_prev is None:
        entry_prev = dict(env._prev_state)
        env._cutscene_skip_entry_prev = dict(entry_prev)
        env._cutscene_skip_frames = 0
        env._skip_inventory_before = _read_policy_inventory(env)
    t0 = time.perf_counter()
    try:
        burned, _ = env._ram_skip.skip_uncontrolled(max_frames=max_frames)
    except KeyboardInterrupt:
        try:
            env.bridge.set_speed(env._ram_skip.training_speed)
            if env._ram_skip.invisible_during_skip:
                env.bridge.set_invisible(False)
        except Exception:
            pass
        raise
    if burned <= 0:
        return dict(env._prev_state), 0.0, {}, None, 0

    env._cutscene_skip_frames = int(getattr(env, "_cutscene_skip_frames", 0)) + int(
        burned
    )
    state = env._read_state(track_items=True)
    state = dict(state)
    entry_prev, env._cutscene_skip_frames = _credit_skip_room_crossing(
        env,
        entry_prev,
        state,
        stage=stage,
        explore=explore,
        game_frames=game_frames,
        quiet=quiet,
        rooms=rooms,
        route_steps=route_steps,
        steps_by_seq=steps_by_seq,
    )
    still_skipping = needs_skip_from_ram(env.bridge.read_ram(SKIP_POLL_RAM_FIELDS))

    if debug:
        dbg = env.bridge.read_ram(
            [
                ("game_mode", GAME_MODE, "u8"),
                ("msg_flag", MESSAGE_FLAG, "u8"),
                ("scene_flag", SCENE_FLAG, "u8"),
            ]
        )
        mode = int(dbg["game_mode"])
        wall = time.perf_counter() - t0
        print(
            f"[skip] burned={burned} session={env._cutscene_skip_frames} "
            f"wall={wall:.2f}s mode=0x{mode:02X} "
            f"msg=0x{int(dbg['msg_flag']):02X} scene=0x{int(dbg['scene_flag']):02X} "
            f"in_control={'yes' if state.get('in_control') else 'no'} "
            f"still_skip={'yes' if still_skipping else 'no'} "
            f"room={state.get('room_id')} "
            f"speed={env._ram_skip.cutscene_speed} "
            f"patches={'on' if env._ram_skip.use_engine_patches else 'off'} "
            f"invisible={'on' if env._ram_skip.invisible_during_skip else 'off'}",
            flush=True,
        )

    if still_skipping:
        return state, 0.0, {}, None, burned

    skip_frames = int(getattr(env, "_cutscene_skip_frames", 0))
    inv_after = _read_policy_inventory(env)
    inv_before = getattr(env, "_skip_inventory_before", None)
    state = _annotate_story_use(
        env, entry_prev, state, inv_before, inv_after
    )
    state["cutscene_key"] = qualify_cutscene_reward(
        skip_frames=skip_frames,
        prev_state=entry_prev,
        new_state=state,
        episode_start_hp=int(getattr(env, "_episode_start_hp", 0)),
        rewarded_cutscenes=env._progress.rewarded_cutscenes,
        visited_rooms=env._progress.visited_rooms,
    )
    env._progress.record_in_control_step(
        state.get("room_id", ""),
        bool(state.get("in_control", True)),
    )
    reward, breakdown = compute_reward(
        entry_prev,
        state,
        env._planner,
        progress=env._progress,
        graph=env.graph,
        success_room=stage.get("success_room"),
        return_breakdown=True,
    )
    if gate_log and skip_frames > 0:
        print(
            format_cutscene_gate_panel(
                skip_frames=skip_frames,
                prev_state=entry_prev,
                new_state=state,
                episode_start_hp=int(getattr(env, "_episode_start_hp", 0)),
                rewarded_cutscenes=env._progress.rewarded_cutscenes,
                visited_rooms=env._progress.visited_rooms,
                qualified_key=state.get("cutscene_key"),
                breakdown=breakdown,
            ),
            flush=True,
        )
    elif skip_frames > 0 and not breakdown.get("new_cutscene"):
        why = cutscene_disqualify_reason(
            skip_frames=skip_frames,
            prev_state=entry_prev,
            new_state=state,
            episode_start_hp=int(getattr(env, "_episode_start_hp", 0)),
            rewarded_cutscenes=env._progress.rewarded_cutscenes,
            visited_rooms=env._progress.visited_rooms,
        )
        if why:
            print(f"[explore] cutscene skip unpaid: {why}", flush=True)
        elif state.get("cutscene_key"):
            print(
                f"[explore] cutscene skip unpaid: duplicate key "
                f"{state['cutscene_key']!r} this episode",
                flush=True,
            )
        else:
            print("[explore] cutscene skip unpaid: no key", flush=True)
    env._prev_state = dict(state)
    if state["hp"] > 0:
        env._prev_hp = state["hp"]
    env._cutscene_skip_frames = 0
    env._cutscene_skip_entry_prev = None
    env._skip_inventory_before = None
    goal = env._encoder.encode_goal(  # type: ignore[union-attr]
        state,
        env._planner,
        item_tracker=env._items,
        room_items=env.room_items,
    )
    return state, float(reward), breakdown, goal, burned


def print_banner(
    speed: int,
    tick_ms: int,
    *,
    cutscene_speed: int,
    frame_skip: int,
    input_mode: str,
    pad_name: str | None,
    turbo_patches: bool = True,
    auto_cutscene_skip: bool = True,
) -> None:
    pad_line = ""
    if input_mode in ("gamepad", "both"):
        pad_line = (
            "Gamepad (via EmuHawk host input — focus the game window):\n"
            "  Stick/D-pad move  |  Square run  |  Cross interact  |  Triangle door-skip\n"
            "  R1 aim  |  R2 fire  |  Select reprint hints  |  Start = in-game menu\n"
            "  Select+Start together = quit harness (Esc/Q also quits)\n"
        )
    kb_line = ""
    if input_mode in ("keyboard", "both"):
        ckpt_keys = (
            "  Esc/Q quit  |  Tab reprint hints  |  F5 cycle checkpoint  |  "
            if ENABLE_CHECKPOINT_PATH
            else "  Esc/Q quit  |  Tab reprint status  |  "
        )
        kb_line = (
            "Keyboard: WASD/arrows move  |  Shift run  |  Z/E interact  |  "
            "R aim  |  F fire  |  X door-skip\n"
            f"{ckpt_keys}"
            "hold T (or pad L1) = manual turbo\n"
            "  (Admin terminal on Windows if keys are ignored.)\n"
        )
    cutscene_line = (
        f"Cutscenes/doors: {'auto @ ' if auto_cutscene_skip else 'manual @ '}{cutscene_speed}%"
        + (" + turbo patches" if turbo_patches else "")
    )
    print(
        "RE1 human play harness — frozen in control until you press; release to re-arm.\n"
        f"Input: {input_mode}  |  Speed: {speed}%  |  "
        f"{frame_skip} frames per press (repeat same move = {frame_skip * 2} latched)\n"
        f"{cutscene_line}  |  idle poll: {tick_ms}ms\n"
        f"{pad_line}{kb_line}",
        flush=True,
    )


def main() -> int:
    global _SHUTDOWN_SESSION

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--curriculum", default=str(DEFAULT_CURRICULUM.relative_to(PROJECT_ROOT)))
    ap.add_argument("--speed", type=int, default=100, help="BizHawk speedmode percent while in control (100 = real-time)")
    ap.add_argument(
        "--cutscene-speed",
        type=int,
        default=6400,
        help="BizHawk speedmode during doors/cutscenes (default 6400)",
    )
    ap.add_argument(
        "--no-turbo-patches",
        action="store_true",
        help="disable door-skip + in-engine cutscene turbo RAM patches",
    )
    ap.add_argument(
        "--no-invisible-cutscenes",
        action="store_true",
        help="keep rendering during cutscene skip (slower; default hides render)",
    )
    ap.add_argument(
        "--cutscene-chunk",
        type=int,
        default=600,
        help="max frames per auto cutscene-skip iteration (matches train_parallel skip_chunk)",
    )
    ap.add_argument(
        "--no-auto-cutscene-skip",
        action="store_true",
        help="do not auto fast-forward doors/cutscenes while idle (old pause-on-idle behavior)",
    )
    ap.add_argument(
        "--debug-cutscene",
        action="store_true",
        help="print a [skip] line per chunk: frames burned, wall time, game_mode, patches",
    )
    ap.add_argument(
        "--debug-combat",
        action="store_true",
        help="log enemy RAM deltas on fire steps (diagnose missing kill rewards)",
    )
    ap.add_argument("--start-savestate", default=None, help="Override stage init savestate path")
    ap.add_argument(
        "--load-slot",
        type=int,
        default=None,
        metavar="N",
        help="reboot and load in-game memory-card save slot N (1..15)",
    )
    ap.add_argument(
        "--warp-room",
        default=None,
        metavar="CODE",
        help="after load, RAM-warp room code(s), comma-separated (e.g. 105,11B)",
    )
    ap.add_argument(
        "--fresh-boot",
        action="store_true",
        help="cold ROM boot only (no savestate); use with --no-auto-cutscene-skip to watch opening",
    )
    ap.add_argument("--tick-ms", type=int, default=33, help="input poll interval when idle (ms)")
    ap.add_argument(
        "--frame-skip",
        type=int,
        default=4,
        help="hold each input this many frames per decision (matches RE1Env / NN, default 4)",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=HUMAN_PLAY_PORT,
        help=f"BizHawk bridge port (default {HUMAN_PLAY_PORT}; training uses 5555+rank)",
    )
    ap.add_argument(
        "--training-parity",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="match fleet env.reset() (default on): curriculum init savestate, "
        "fresh exploration tracker, skip_chunk 600, cutscene-speed 6400",
    )
    ap.add_argument("--quiet", action="store_true", help="Only print notable reward events")
    ap.add_argument(
        "--deafen-step",
        action="store_true",
        help="zero step penalty in reward math (signal-only totals for cutscene verification)",
    )
    ap.add_argument(
        "--cutscene-gate-log",
        action="store_true",
        help="print cutscene gate panel (keys, Kenneth block, visited/rewarded sets) after each skip",
    )
    ap.add_argument(
        "--no-ram-log",
        action="store_true",
        help="disable live RAM screen log (hp/gs/mode/room/outside on change)",
    )
    ap.add_argument(
        "--mask-interval",
        type=float,
        default=0.0,
        help="log legal mask + story USE sites every N seconds (0=off)",
    )
    ap.add_argument(
        "--input",
        choices=("both", "keyboard", "gamepad"),
        default="both",
        help="both = keyboard + EmuHawk gamepad (default for --input both)",
    )
    ap.add_argument("--pad-index", type=int, default=0, help="pygame joystick index (0 = first pad)")
    ap.add_argument("--stick-deadzone", type=float, default=0.35, help="left stick neutral radius")
    ap.add_argument("--debug-input", action="store_true", help="print detected buttons when they change")
    ap.add_argument(
        "--connect-timeout",
        type=float,
        default=90.0,
        help="seconds to wait for EmuHawk lua client (default 90)",
    )
    ap.add_argument(
        "--no-launch",
        action="store_true",
        help="do not spawn EmuHawk; start server only and wait for manual launch",
    )
    ap.add_argument(
        "--kill-stale",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="kill any prior python listener on --port before binding (default on)",
    )
    args = ap.parse_args()
    if args.deafen_step:
        import re1_rl.reward as reward_mod

        reward_mod.STEP_PENALTY = 0.0
        print("[play] step penalty deafened (STEP_PENALTY=0)", flush=True)
    if args.training_parity:
        args.cutscene_speed = 6400
        args.cutscene_chunk = 600
        if args.start_savestate:
            print(
                "[play] training-parity ignores --start-savestate "
                "(use --no-training-parity for dev checkpoint savestates)",
                flush=True,
            )
            args.start_savestate = None

    signal.signal(signal.SIGINT, _on_shutdown_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_shutdown_signal)

    session = PlaySession()
    _SHUTDOWN_SESSION = session
    exit_code = 0
    explore: TrainingProgressTracker | None = None
    game_frames = 0

    try:
        use_keyboard = args.input in ("keyboard", "both")
        use_emuhawk_joypad = args.input in ("gamepad", "both")
        kb = _import_keyboard() if use_keyboard else None
        session.kb = kb

        curriculum_path = PROJECT_ROOT / args.curriculum
        stage = _load_stage_meta(curriculum_path)
        rooms = _load_rooms()
        route = json.loads((PROJECT_ROOT / "data" / "route_jill_anypct.json").read_text(encoding="utf-8"))
        steps_by_seq = {int(s["seq"]): s for s in route}
        route_steps: list[int] = [int(s) for s in stage.get("route_steps", [])]

        if args.load_slot is not None:
            if args.fresh_boot or args.start_savestate:
                print(
                    "ERROR: --load-slot cannot be combined with --fresh-boot or --start-savestate",
                    file=sys.stderr,
                )
                return 1
            if not 1 <= int(args.load_slot) <= 15:
                print("ERROR: --load-slot must be 1..15", file=sys.stderr)
                return 1
            savestate = None
        elif args.fresh_boot:
            if args.start_savestate:
                print("ERROR: --fresh-boot cannot be combined with --start-savestate", file=sys.stderr)
                return 1
            savestate = None
        elif args.start_savestate:
            savestate = PROJECT_ROOT / args.start_savestate
        else:
            savestate = PROJECT_ROOT / stage["init_savestate"]
        if savestate is not None and not savestate.is_file():
            print(f"ERROR: savestate not found: {savestate}", file=sys.stderr)
            return 1

        checkpoint_meta = _checkpoint_sidecar_for(savestate) if savestate else None
        if args.training_parity:
            checkpoint_meta = None
            savestate = None
            print(
                "[play] training-parity: fleet init (jill_control_fresh), "
                "fresh exploration tracker",
                flush=True,
            )
        checkpoints = _list_checkpoints()
        ckpt_cycle = 0

        from re1_rl.bizhawk_bridge import BizHawkClient

        n_emu = _count_emuhawk()
        if n_emu >= 8:
            print(
                f"[play] WARNING: {n_emu} EmuHawk.exe already running (training?). "
                "A new instance may fail to start or connect.",
                flush=True,
            )

        if args.kill_stale:
            _kill_stale_listener(int(args.port))

        shot = str(PROJECT_ROOT / "data" / f"_frame_{args.port}.png")
        log_path = PROJECT_ROOT / "data" / f"emuhawk_play_{args.port}.log"
        bridge = BizHawkClient(
            port=args.port,
            timeout=300.0,
            screenshot_path=shot,
            connect_timeout=float(args.connect_timeout),
        )
        session.bridge = bridge
        try:
            bridge.start_server()
        except OSError as exc:
            pid = _pid_listening_on(int(args.port))
            hint = f" taskkill /PID {pid} /F" if pid else ""
            print(f"ERROR: cannot bind port {args.port}: {exc}.{hint}", file=sys.stderr)
            exit_code = 1
            return exit_code

        proc: subprocess.Popen[Any] | None = None
        if not args.no_launch:
            proc = launch_emuhawk(int(args.port), log_path)
            session.proc = proc
        else:
            print(f"[play] --no-launch: start EmuHawk with --socket_port={args.port}", flush=True)

        wait_for_emuhawk(
            bridge,
            proc,
            port=int(args.port),
            timeout=float(args.connect_timeout),
            log_path=log_path,
        )
        print("[play] bridge ping...", flush=True)
        if bridge.ping(42) != 42:
            raise ConnectionError("bridge ping failed after connect")
        bridge.set_speed(int(args.speed))
        if use_emuhawk_joypad:
            sample = _read_emuhawk_joypad(bridge, debug=True)
            pressed = [k for k, v in sample.items() if v]
            print(
                "[play] host joypad via EmuHawk — focus the game window, then move stick",
                flush=True,
            )
            if pressed:
                print(f"[play] joypad sample: {pressed}", flush=True)

        env = RE1Env(
            curriculum_path=curriculum_path,
            bridge=bridge,
            frame_skip=max(1, int(args.frame_skip)),
            project_root=PROJECT_ROOT,
        )
        session.env = env

        state, goal = (
            bootstrap_fresh_rom(
                env,
                play_speed=int(args.speed),
                cutscene_speed=int(args.cutscene_speed),
                turbo_patches=not args.no_turbo_patches,
                invisible_cutscenes=not args.no_invisible_cutscenes,
            )
            if args.fresh_boot
            else bootstrap_load_slot(
                env,
                slot=int(args.load_slot),
                play_speed=int(args.speed),
                cutscene_speed=int(args.cutscene_speed),
                turbo_patches=not args.no_turbo_patches,
                invisible_cutscenes=not args.no_invisible_cutscenes,
                warp_room=args.warp_room,
            )
            if args.load_slot is not None
            else bootstrap_fleet_reset(
                env,
                play_speed=int(args.speed),
                cutscene_speed=int(args.cutscene_speed),
                skip_chunk=int(args.cutscene_chunk),
                turbo_patches=not args.no_turbo_patches,
                invisible_cutscenes=not args.no_invisible_cutscenes,
            )
            if args.training_parity
            else bootstrap_env(
                env,
                savestate=savestate,  # type: ignore[arg-type]
                checkpoint_meta=checkpoint_meta,
                play_speed=int(args.speed),
                cutscene_speed=int(args.cutscene_speed),
                turbo_patches=not args.no_turbo_patches,
                invisible_cutscenes=not args.no_invisible_cutscenes,
                warp_room=args.warp_room,
                skip_to_control=not args.no_auto_cutscene_skip,
            )
        )
        print_banner(
            args.speed,
            args.tick_ms,
            cutscene_speed=int(args.cutscene_speed),
            frame_skip=int(args.frame_skip),
            input_mode=args.input,
            pad_name="EmuHawk host" if use_emuhawk_joypad else None,
            turbo_patches=not args.no_turbo_patches,
            auto_cutscene_skip=not args.no_auto_cutscene_skip,
        )

        game_frames = 0
        last_room = str(state.get("room_id", ""))
        last_wp = env._planner.waypoint_index if env._planner else 0
        tab_down = False
        share_down = False
        f5_down = False
        last_buttons: tuple[str, ...] = ()
        last_status_at = time.time()
        last_mask_at = 0.0
        story_affordance: StoryUseAffordance | None = None
        last_progress_print = -1
        ever_saw_input = False
        joypad_raw_dumped = False
        in_cutscene_mode = False
        turbo_on = False
        ram_log = RamScreenLogger(data_dir=PROJECT_ROOT / "data")
        ram_log.enabled = not args.no_ram_log
        explore = TrainingProgressTracker(
            prefix="progress" if args.training_parity else "explore"
        )
        ram_log.reset(episode_start_hp=0 if args.fresh_boot else int(state.get("hp", 96)))
        if ram_log.enabled:
            print(
                "[play] RAM screen log ON — hp/gs/mode/room/outside on every change",
                flush=True,
            )
        if float(args.mask_interval) > 0:
            print(
                f"[play] story USE mask monitor ON — every {args.mask_interval}s",
                flush=True,
            )
            print(format_story_mask_panel(env, state), flush=True)
            print(flush=True)
            last_mask_at = time.time()
        print("[play] story USE affordance edge log ON ([story-use] ON/OFF)", flush=True)
        story_affordance = maybe_log_story_use_affordance(env, state, story_affordance)

        hints = format_hints_panel(
            env, state, goal, rooms=rooms, route_steps=route_steps, steps_by_seq=steps_by_seq
        )
        print(hints, flush=True)
        print("(frozen in control — press to step, release to re-arm; Select/Tab reprints hints)\n", flush=True)

        running = True
        playing = False
        step_armed = True
        while running:
            ram_log.maybe_log(bridge)
            mask_state = env._read_state()
            story_affordance = maybe_log_story_use_affordance(
                env, mask_state, story_affordance
            )
            if float(args.mask_interval) > 0:
                now = time.time()
                if now - last_mask_at >= float(args.mask_interval):
                    print(format_story_mask_panel(env, mask_state), flush=True)
                    print(flush=True)
                    last_mask_at = now
            if not playing:
                _interruptible_sleep(args.tick_ms / 1000.0)

            raw_pad = _read_emuhawk_joypad(bridge) if use_emuhawk_joypad else {}
            quit_kb = bool(kb and (kb.is_pressed("esc") or kb.is_pressed("q")))
            quit_pad = bool(raw_pad.get("select") and raw_pad.get("start"))
            if quit_kb or quit_pad:
                break

            reprint = False
            if kb and kb.is_pressed("tab"):
                if not tab_down:
                    reprint = True
                tab_down = True
            else:
                tab_down = False
            if raw_pad.get("select") and not raw_pad.get("start"):
                if not share_down:
                    reprint = True
                share_down = True
            else:
                share_down = False
            if reprint:
                state = env._read_state()
                synced = _sync_planner_from_live_state(env, state)
                if synced:
                    last_wp = env._planner.waypoint_index if env._planner else 0
                goal = env._encoder.encode_goal(  # type: ignore[union-attr]
                    state,
                    env._planner,
                    item_tracker=env._items,
                    room_items=env.room_items,
                )
                print(
                    format_hints_panel(
                        env,
                        state,
                        goal,
                        rooms=rooms,
                        route_steps=route_steps,
                        steps_by_seq=steps_by_seq,
                    ),
                    flush=True,
                )
                last_status_at = time.time()
                continue

            if kb and kb.is_pressed("f5") and checkpoints and ENABLE_CHECKPOINT_PATH:
                if not f5_down:
                    ckpt_cycle = (ckpt_cycle + 1) % len(checkpoints)
                    key, ck_path, meta = checkpoints[ckpt_cycle]
                    print(f"[play] loading checkpoint {key} ...", flush=True)
                    state, goal = bootstrap_env(
                        env,
                        savestate=ck_path,
                        checkpoint_meta=meta,
                        play_speed=int(args.speed),
                        cutscene_speed=int(args.cutscene_speed),
                        turbo_patches=not args.no_turbo_patches,
                        invisible_cutscenes=not args.no_invisible_cutscenes,
                    )
                    last_room = str(state.get("room_id", ""))
                    last_wp = env._planner.waypoint_index if env._planner else 0
                    in_cutscene_mode = False
                    ram_log.reset(episode_start_hp=int(state.get("hp", 96)))
                    print(
                        format_hints_panel(
                            env,
                            state,
                            goal,
                            rooms=rooms,
                            route_steps=route_steps,
                            steps_by_seq=steps_by_seq,
                        ),
                        flush=True,
                    )
                f5_down = True
                continue
            f5_down = False

            if not args.no_auto_cutscene_skip:
                ctrl_ram = env.bridge.read_ram(SKIP_POLL_RAM_FIELDS)
                if needs_skip_from_ram(ctrl_ram):
                    playing = True
                    if not in_cutscene_mode:
                        env._cutscene_skip_entry_prev = None
                        env._cutscene_skip_frames = 0
                        kind = (
                            "dialogue"
                            if in_control_from_ram(ctrl_ram)
                            else "cutscene/door"
                        )
                        extras = ""
                        if not args.no_turbo_patches:
                            extras += " + turbo patches"
                        if not args.no_invisible_cutscenes:
                            extras += " + invisible render"
                        print(
                            f"[play] {kind} — auto skip @ {args.cutscene_speed}%{extras}…",
                            flush=True,
                        )
                        in_cutscene_mode = True
                    prev_for_panel = dict(env._prev_state)
                    state, reward, breakdown, goal, burned = cutscene_skip_chunk(
                        env,
                        stage=stage,
                        max_frames=int(args.cutscene_chunk),
                        debug=bool(args.debug_cutscene),
                        gate_log=bool(args.cutscene_gate_log),
                        explore=explore,
                        game_frames=game_frames,
                        quiet=args.quiet,
                        rooms=rooms,
                        route_steps=route_steps,
                        steps_by_seq=steps_by_seq,
                    )
                    if burned > 0:
                        reward_txt = format_reward_panel(
                            breakdown,
                            reward,
                            rooms=rooms,
                            route_steps=route_steps,
                            steps_by_seq=steps_by_seq,
                            planner=env._planner,
                            graph=env.graph,
                            prev_state=prev_for_panel,
                            state=state,
                            quiet=args.quiet,
                        )
                        if reward_txt:
                            print(
                                f"--- skip chunk ({burned} frames) ---",
                                flush=True,
                            )
                            print(reward_txt, flush=True)
                        if reward or breakdown:
                            _log_exploration_hit(
                                explore,
                                state=state,
                                breakdown=breakdown,
                                step=game_frames,
                                env=env,
                            )
                        if turbo_on:
                            # skip chunks restore --speed; keep turbo engaged
                            env.bridge.set_speed(int(args.cutscene_speed))
                        game_frames += 1
                        last_status_at = time.time()
                        wp = env._planner.waypoint_index if env._planner else 0
                        if ENABLE_CHECKPOINT_PATH and wp != last_wp:
                            completed = max(0, wp - 1)
                            seq = route_steps[completed] if completed < len(route_steps) else None
                            obj_text = steps_by_seq.get(int(seq or 0), {}).get(
                                "objective", f"checkpoint {completed + 1}"
                            )
                            print(f"*** CHECKPOINT COMPLETE: {obj_text} ***", flush=True)
                        room = str(state.get("room_id", ""))
                        if room != last_room or (ENABLE_CHECKPOINT_PATH and wp != last_wp):
                            print(
                                format_hints_panel(
                                    env,
                                    state,
                                    goal,
                                    rooms=rooms,
                                    route_steps=route_steps,
                                    steps_by_seq=steps_by_seq,
                                ),
                                flush=True,
                            )
                            last_room = room
                            last_wp = wp
                        if state.get("in_control"):
                            in_cutscene_mode = False
                    continue
            elif in_cutscene_mode:
                in_cutscene_mode = False

            # Manual turbo: hold T (keyboard) or L1 (pad) to run the emulator
            # at cutscene speed while KEEPING control — for timed sequences
            # the auto-skip must not touch (e.g. the living-room ceiling trap).
            turbo_held = bool(kb and kb.is_pressed("t")) or bool(raw_pad.get("l1"))
            if turbo_held != turbo_on:
                turbo_on = turbo_held
                env.bridge.set_speed(
                    int(args.cutscene_speed) if turbo_on else int(args.speed)
                )
                print(
                    f"[play] manual turbo {'ON' if turbo_on else 'OFF'} "
                    f"({args.cutscene_speed if turbo_on else args.speed}%)",
                    flush=True,
                )

            buttons = _keys_to_buttons(kb) if use_keyboard and kb else {}
            if use_emuhawk_joypad:
                buttons.update(_joypad_to_play_buttons(raw_pad))
            btn_key = tuple(sorted(buttons))
            if args.debug_input and btn_key != last_buttons:
                print(f"[input] {btn_key or '(none)'}", flush=True)
                last_buttons = btn_key
            if turbo_on and not buttons:
                # advance time hands-free at turbo (roof keeps lowering)
                playing = True
                human_advance(
                    env, {}, stage=stage, debug_combat=bool(args.debug_combat)
                )
                game_frames += 1
                last_status_at = time.time()
                continue

            should_step, step_armed = human_step_gate(buttons, armed=step_armed)
            if not buttons:
                playing = False
                if time.time() - last_status_at >= 10.0:
                    state = env._read_state()
                    if ENABLE_CHECKPOINT_PATH:
                        synced = _sync_planner_from_live_state(env, state)
                        if synced:
                            last_wp = env._planner.waypoint_index if env._planner else 0
                            print(
                                format_hints_panel(
                                    env,
                                    state,
                                    env._encoder.encode_goal(  # type: ignore[union-attr]
                                        state,
                                        env._planner,
                                        item_tracker=env._items,
                                        room_items=env.room_items,
                                    ),
                                    rooms=rooms,
                                    route_steps=route_steps,
                                    steps_by_seq=steps_by_seq,
                                ),
                                flush=True,
                            )
                    prog = _objective_progress_line(env, state) if ENABLE_CHECKPOINT_PATH else None
                    ctrl = state.get("in_control", "?")
                    wait_msg = (
                        "auto-skipping cutscene"
                        if not args.no_auto_cutscene_skip and not state.get("in_control", True)
                        else "waiting for input"
                    )
                    status = (
                        f"[play] {wait_msg} — room {state.get('room_id')}  "
                        f"in_control={ctrl}"
                    )
                    if ENABLE_CHECKPOINT_PATH:
                        status = (
                            f"[play] {wait_msg} — room {state.get('room_id')}  "
                            f"wp {env._planner.waypoint_index if env._planner else '?'}  "
                            f"in_control={ctrl}"
                            + (f"  |  {prog}" if prog else "")
                        )
                    print(status, flush=True)
                    if (
                        use_emuhawk_joypad
                        and not ever_saw_input
                        and not joypad_raw_dumped
                    ):
                        _read_emuhawk_joypad(bridge, debug=True)
                        joypad_raw_dumped = True
                        print(
                            "[play] no stick/buttons yet — click EmuHawk window, wiggle stick",
                            flush=True,
                        )
                    last_status_at = time.time()
                continue

            if not should_step:
                playing = False
                continue

            in_cutscene_mode = False
            ever_saw_input = True
            playing = True

            prev_state = dict(env._prev_state)
            state, reward, breakdown, goal, info = human_advance(
                env,
                buttons,
                stage=stage,
                debug_combat=bool(args.debug_combat),
            )
            game_frames += 1
            last_status_at = time.time()

            reward_txt = format_reward_panel(
                breakdown,
                reward,
                rooms=rooms,
                route_steps=route_steps,
                steps_by_seq=steps_by_seq,
                planner=env._planner,
                graph=env.graph,
                prev_state=prev_state,
                state=state,
                quiet=args.quiet,
            )
            if reward_txt:
                print(f"--- step {game_frames} ({env.frame_skip} frames) ---", flush=True)
                print(reward_txt, flush=True)
            if reward or breakdown:
                _log_exploration_hit(
                    explore,
                    state=state,
                    breakdown=breakdown,
                    step=game_frames,
                    env=env,
                )

            room = str(state.get("room_id", ""))
            wp = int(info.get("waypoint_index", 0))
            prev_wp = int(info.get("prev_waypoint_index", wp))
            if ENABLE_CHECKPOINT_PATH and wp != prev_wp:
                completed = max(0, wp - 1)
                seq = route_steps[completed] if completed < len(route_steps) else None
                obj_text = steps_by_seq.get(int(seq or 0), {}).get(
                    "objective", f"checkpoint {completed + 1}"
                )
                print(f"*** CHECKPOINT COMPLETE: {obj_text} ***", flush=True)

            if room != last_room or (ENABLE_CHECKPOINT_PATH and wp != last_wp):
                print(
                    format_hints_panel(
                        env,
                        state,
                        goal,
                        rooms=rooms,
                        route_steps=route_steps,
                        steps_by_seq=steps_by_seq,
                    ),
                    flush=True,
                )
                last_room = room
                last_wp = wp
                last_progress_print = -1
            elif not args.quiet and ENABLE_CHECKPOINT_PATH:
                prog = _objective_progress_line(env, state)
                if prog and "in-control steps" in prog:
                    have = env._progress.in_control_steps_in_room(
                        str((env._planner.current_objective() or {}).get("room_id", room))
                    )
                    if have != last_progress_print and have > 0 and have % 10 == 0:
                        print(f"[play] {prog}", flush=True)
                        last_progress_print = have

    except (TimeoutError, RuntimeError, ConnectionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        exit_code = 1
    except KeyboardInterrupt:
        session.close(reason="Ctrl+C")
        exit_code = 130
    finally:
        if explore is not None:
            from unittest.mock import MagicMock

            model = MagicMock()
            model.ep_info_buffer = []
            explore.log_rollout_end(model, num_timesteps=game_frames)
        session.close()
        _SHUTDOWN_SESSION = None

    print("[play] exited.", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
