"""Gymnasium environment skeleton for Resident Evil 1."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.enemy_combat import apply_combat_step_fields, combat_enemy_count
from re1_rl.game_session import (
    episode_death_signal_from_ram,
    episode_failure_reason,
    pause_menu_screen_id,
)
from re1_rl.knife_equip import equip_knife_from_pause_menu
from re1_rl.item_todo import ItemTracker, RoomItems, build_item_todo, canonical_item
from re1_rl.memory_map import (
    CHARACTER_ID,
    DEFAULT_RAM_FIELDS,
    EQUIPPED_WEAPON_ID,
    GAME_MODE,
    GAME_STATE,
    IN_CONTROL_MASK,
    INTERACTION_PROMPT,
    INTERACTION_PROMPT_MASK,
    MESSAGE_FLAG,
    PLAYER_ACTION_AUX,
    PLAYER_ANIM_STATE,
    PLAYER_HP,
    PLAYER_POISON,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
    SCENE_FLAG,
    STAGE_ID,
    player_died,
    decode_enemy_table,
    decode_inventory,
    enemy_table_fields,
)
from re1_rl.pushable import (
    forward_hold_frames,
    update_forward_collision_stall,
)
from re1_rl.ram_skip import RamSkipper, SKIP_POLL_RAM_FIELDS, needs_skip_from_ram
from re1_rl.obs_encoder import (
    BOX_DIM,
    GOAL_DIM,
    INVENTORY_OBS_DIM,
    PROPRIO_DIM,
    ROOM_VISITED_DIM,
    ObsEncoder,
    encode_box,
    encode_inventory_slots,
)
from re1_rl.episode_history import (
    ACQUISITION_LOG_DIM,
    ROOM_HISTORY_DIM,
    EpisodeHistory,
)
from re1_rl.cutscene_ledger import CUTSCENE_LEDGER_DIM, encode_cutscene_ledger
from re1_rl.item_affordances import AFFORDANCES_DIM, encode_affordances
from re1_rl.world_catalog import WorldCatalog
from re1_rl.world_state_encoder import WORLD_STATE_DIM, encode_world_state
from re1_rl.key_items import KEY_ITEM_NAMES, KEYS_HELD_DIM, encode_keys_held
from re1_rl.maps_files import MAPS_FILES_DIM, encode_maps_files_flags
from re1_rl.milestone_features import MILESTONE_DIM, encode_milestones
from re1_rl.room_signature import ENEMY_ROSTER_DIM, RoomEnemyRoster
from re1_rl.spatial_encoder import (
    SPATIAL_DIM,
    VISITED_SHAPE,
    ItemPositions,
    SpatialEncoder,
    StaticEnemySpawns,
    VisitedMask,
)
from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    compute_reward,
    DEATH_PENALTY,
    MAIN_HALL_BEFORE_KENNETH_PENALTY,
    REWARD_SCALE,
    stagnation_episode_timeout,
)
from re1_rl.room_graph import RoomGraph, load_valid_rooms
from re1_rl.knife_macro import execute_knife_macro, read_knife_hooks
from re1_rl.sticky_input import StickyInputState
from re1_rl.action_mask import (
    ATTACK_ACTION,
    ATTACK_DOWN_ACTION,
    ATTACK_UP_ACTION,
    COMBINE_ACTION,
    DEPOSIT_ACTION_BASE,
    DEPOSIT_ACTION_NAMES,
    EQUIP_ACTION,
    KNIFE_SWING_ACTION,
    MENU_ACTION_NAMES,
    N_DEPOSIT_ACTIONS,
    N_SELECT_SLOT,
    N_WITHDRAW_ACTIONS,
    SELECT_SLOT_BASE,
    USE_ACTION,
    WITHDRAW_ACTION_BASE,
    WITHDRAW_ACTION_NAMES,
    action_mask as build_action_mask,
)
from re1_rl.attack_macro import (
    execute_attack_down_macro,
    execute_attack_macro,
    execute_attack_up_macro,
)
from re1_rl.options_menu_macro import dismiss_options_menu

# Mask knife/attack when live RAM shows no living enemies (set 0 to debug combat).
MASK_ATTACK_WITHOUT_ENEMIES = os.environ.get(
    "MASK_ATTACK_WITHOUT_ENEMIES", "1"
).strip().lower() not in ("0", "false", "no", "off")

ACTION_NAMES = [
    "noop",
    "forward",
    "back",
    "turn_left",
    "turn_right",
    "run_forward",
    "attack_up",  # 6 — R1+Up high attack macro (reuses old quickturn slot; not on DC)
    "interact",
    "knife_swing",
    "attack",  # standing aim+fire macro, any equipped weapon
    "use",  # open USE menu -> select_slot_N (2-step; herbs, sprays)
    "equip",  # open EQUIP menu -> select_slot_N (2-step)
    *DEPOSIT_ACTION_NAMES,    # 12-19 box deposits (box rooms only)
    *WITHDRAW_ACTION_NAMES,   # 20-35 box withdrawals (box rooms only)
    *MENU_ACTION_NAMES,       # 36 combine + 37-44 select_slot_N
    "attack_down",            # 45 R1+Down crouch / floor-aim attack macro
]

# Map discrete actions to friendly button names (translated to Nymashock core
# names by lua/re1_client.lua BUTTON_MAP). Directions + square latch across
# steps; face buttons pulse within each frame_skip batch. Macro / magic
# actions own the joypad or write RAM directly — empty button sets.
ACTION_BUTTON_MAP: dict[int, dict[str, bool]] = {
    0: {},  # noop
    1: {"up": True},
    2: {"down": True},
    3: {"left": True},
    4: {"right": True},
    5: {"up": True, "square": True},  # run forward (square = run in RE1)
    6: {},  # attack_up macro (see execute_attack_up_macro)
    7: {"cross": True},  # interact / confirm
    8: {"r1": True, "down": True, "cross": True},  # knife_swing macro entry (not a blind pulse)
}
for _idx in range(9, len(ACTION_NAMES)):
    ACTION_BUTTON_MAP[_idx] = {}

# BizHawk RE1 screenshot is 240x350 RGB; left 18 + right 12 px are near-black
# pillarbox. Pipeline: grayscale + resize FULL frame to 84x84 (bars included,
# same as the original Atari-style square), THEN prune the bar columns so the
# policy sees content only at 84x77. NatureCNN flatten drops 3136 -> 2688;
# resume uses async_fleet compatible-weight transplant (conv reuse, linear reinit).
PILLARBOX_LEFT = 18
PILLARBOX_RIGHT = 12
FRAME_SQUARE = 84
# Bars on the 84-wide square (round of 18/350 and 12/350 of 84).
PILLARBOX_LEFT_SQ = round(PILLARBOX_LEFT * FRAME_SQUARE / 350)  # 4
PILLARBOX_RIGHT_SQ = round(PILLARBOX_RIGHT * FRAME_SQUARE / 350)  # 3
FRAME_H = FRAME_SQUARE
FRAME_W = FRAME_SQUARE - PILLARBOX_LEFT_SQ - PILLARBOX_RIGHT_SQ  # 77
from re1_rl.frame_ring import FRAME_SHAPE, FRAME_STACK

FRAME_SHAPE_CHW = (FRAME_STACK, FRAME_H, FRAME_W)  # SB3 / VecTransposeImage

# Episode failures that mean "Jill died / title escape" — confirm before ending
# when seen at step entry (HP can flicker to 0 for one frame in low-HP combat).
_DEATH_FAILURE_REASONS = frozenset(
    {
        "hp_death",
        "scripted_death_hp",
        "death_screen_ui",
        "death_continue_screen",
        "death_room_overlay",
        "title_mode_select",
    }
)

def _prune_square_pillarbox(square: np.ndarray) -> np.ndarray:
    """Drop fixed pillarbox columns from an 84-wide (HxW) gray/RGB frame."""
    w = int(square.shape[1])
    if w != FRAME_SQUARE:
        return square
    return square[:, PILLARBOX_LEFT_SQ : FRAME_SQUARE - PILLARBOX_RIGHT_SQ]


def _resize_frame(
    frame: np.ndarray, size: tuple[int, int] = (FRAME_SQUARE, FRAME_SQUARE)
) -> np.ndarray:
    """RGB -> grayscale -> 84x84 (bars in) -> prune bars -> HxW single channel.

    ``size`` is OpenCV (width, height) for the intermediate square (default 84x84).
    Output width is FRAME_W after prune. Pre-rendered RE1 backgrounds carry
    almost no color signal; gray x4 stack matches the Atari recipe.
    """
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    square = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    pruned = _prune_square_pillarbox(square)
    return pruned[..., None]


def _inventory_names_from_slots(
    inventory_slots: list[dict[str, Any]] | None,
) -> set[str]:
    names: set[str] = set()
    for slot in inventory_slots or []:
        if isinstance(slot, (list, tuple)) and slot:
            names.add(canonical_item(str(slot[0])))
        elif isinstance(slot, dict):
            names.add(
                canonical_item(str(slot.get("item_id_name") or slot.get("name") or ""))
            )
    names.discard("")
    return names


class RE1Env(gym.Env):
    """Resident Evil 1 env wired to BizHawk (primary track).

    Observation dict:
      frame   -- 84x77x4 grayscale stack (84x84 with bars, then prune columns)
      proprio -- 28 named floats: body state + anim history + poison
      goal    -- 24 named floats: planner compass/TODO (obs_encoder.GOAL_FIELDS)
    Use re1_rl.obs_encoder.format_obs_table(obs) to pretty-print any obs.
    """

    metadata = {"render_modes": ["rgb_array"]}

    def __init__(
        self,
        curriculum_path: str | Path,
        bridge: BizHawkClient | None = None,
        frame_skip: int = 8,
        project_root: str | Path | None = None,
        *,
        async_cutscene_skip: bool = False,
    ) -> None:
        super().__init__()
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1])
        self.curriculum_path = Path(curriculum_path)
        self.bridge = bridge or BizHawkClient()
        self.frame_skip = frame_skip
        self._sticky_input = StickyInputState()
        self._prev_action: int | None = None
        # Optional single-env step memlog (RE1_STEP_DIAG_PORT); None for all others.
        self._step_diag = None
        try:
            from re1_rl.step_diag import try_make_logger

            self._step_diag = try_make_logger(
                getattr(self.bridge, "port", None),
                project_root=self.project_root,
                machine_name=os.environ.get("RE1_MACHINE_NAME") or None,
            )
        except (OSError, ValueError, TypeError):
            self._step_diag = None

        self.observation_space = spaces.Dict(
            {
                # 4 stacked grayscale frames, channels-last (84 high x 112 wide)
                "frame": spaces.Box(0, 255, shape=FRAME_SHAPE, dtype=np.uint8),
                "proprio": spaces.Box(-1.0, 1.0, shape=(PROPRIO_DIM,), dtype=np.float32),
                "goal": spaces.Box(-2.0, 2.0, shape=(GOAL_DIM,), dtype=np.float32),
                # egocentric items/enemies/exits (spatial_encoder.SPATIAL_FIELDS)
                "spatial": spaces.Box(-2.0, 2.0, shape=(SPATIAL_DIM,), dtype=np.float32),
                # per-room 16x16 visited-cell plane (cheap mental map)
                "visited": spaces.Box(0.0, 1.0, shape=VISITED_SHAPE, dtype=np.float32),
                # episode-local one-hot over stable room table (aligns with proprio room_index)
                "rooms_visited": spaces.Box(0.0, 1.0, shape=(ROOM_VISITED_DIM,), dtype=np.float32),
                # item-box contents + free slots + in-box-room flag
                "box": spaces.Box(0.0, 2.0, shape=(BOX_DIM,), dtype=np.float32),
                # on-person inventory (8 slots)
                "inventory": spaces.Box(0.0, 1.0, shape=(INVENTORY_OBS_DIM,), dtype=np.float32),
                # episode room-entry deque (K=32)
                "history": spaces.Box(0.0, 1.0, shape=(ROOM_HISTORY_DIM,), dtype=np.float32),
                # last pickups (K=4)
                "acquisitions": spaces.Box(0.0, 1.0, shape=(ACQUISITION_LOG_DIM,), dtype=np.float32),
                # static Evil Resource enemy roster for current room
                "room_enemies": spaces.Box(0.0, 1.0, shape=(ENEMY_ROSTER_DIM,), dtype=np.float32),
                "keys_held": spaces.Box(0.0, 1.0, shape=(KEYS_HELD_DIM,), dtype=np.float32),
                # Deprecated: superseded by world_state key-hint slices; kept for checkpoints.
                "affordances": spaces.Box(0.0, 1.0, shape=(AFFORDANCES_DIM,), dtype=np.float32),
                "world_state": spaces.Box(0.0, 8.0, shape=(WORLD_STATE_DIM,), dtype=np.float32),
                "cutscene_ledger": spaces.Box(
                    0.0, 1.0, shape=(CUTSCENE_LEDGER_DIM,), dtype=np.float32
                ),
                "milestones": spaces.Box(0.0, 1.0, shape=(MILESTONE_DIM,), dtype=np.float32),
                "maps_files": spaces.Box(0.0, 1.0, shape=(MAPS_FILES_DIM,), dtype=np.float32),
            }
        )
        self.action_space = spaces.Discrete(len(ACTION_NAMES))

        self.graph = RoomGraph(
            self.project_root / "data" / "doors_empirical.json",
            self.project_root / "data" / "doors_rdt.json",
            valid_rooms=load_valid_rooms(self.project_root / "data" / "rooms.json"),
        )
        self.room_items = RoomItems(self.project_root / "data" / "room_items.json")
        self._world_catalog = WorldCatalog.from_files(self.project_root)
        self.item_positions = ItemPositions(
            self.project_root / "data" / "item_positions.json"
        )
        self._spatial = SpatialEncoder(
            self.item_positions,
            self.graph,
            StaticEnemySpawns(self.project_root / "data" / "room_enemies.json"),
        )
        self._visited = VisitedMask()
        self._episode_history = EpisodeHistory()
        self._room_roster = RoomEnemyRoster(
            self.project_root / "data" / "room_enemies.json",
        )
        self._enemy_fields = enemy_table_fields()
        self._encoder: ObsEncoder | None = None
        self._frame_stack: list[np.ndarray] = []
        self._planner: WaypointPlanner | None = None
        self._progress = ProgressTracker()
        self._ram_skip = RamSkipper(
            self.bridge,
            use_engine_patches=True,
            cutscene_speed=6400,
        )
        self._items = ItemTracker(todo=[])
        self._box_cache: list[tuple[int, int]] | None = None
        self._use_phase = 0
        self._inventory_before_use: list[tuple[int, int]] | None = None
        self._equip_phase = 0
        self._combine_phase = 0
        self._combine_slot_a: int | None = None
        self._attack_telemetry = None
        self._stage: dict[str, Any] = {}
        self._step_count = 0
        self._prev_state: dict[str, Any] = {}
        self._prev_hp = 0
        self._forward_collision_stall = False
        self._async_cutscene_skip = bool(async_cutscene_skip)
        self._bg_skip_stop = threading.Event()
        self._bg_skip_thread: threading.Thread | None = None
        # Knife macro owns the joypad for its whole schedule; the bg skip
        # worker must not start a fast_forward (which mashes cross and stomps
        # joypad) while this is set.
        self._macro_active = False
        # Optional (aim, swing, recovery) game-frame override for the knife
        # macro, emu-frames-per-game-frame scale override, and joypad.get()
        # readback toggle (QA harnesses set these).
        self.knife_phases: tuple[int, int, int] | None = None
        self.knife_scale: int | None = None
        self.knife_echo_joypad = False
        self.knife_use_ram_gates = True
        self._skipping_flag = False
        self._bg_death = False
        self._skip_cache_obs: dict[str, np.ndarray] | None = None
        self._skip_cache_state: dict[str, Any] | None = None
        self._skip_cache_truncated = False
        self._post_skip_sync = False
        self._post_skip_reward = 0.0
        self._post_skip_bd: dict[str, float] = {}
        self._cutscene_skip_entry_prev: dict[str, Any] | None = None
        # Total uncontrolled frames for the current skip, including every
        # room-crossing segment. Unlike _last_skip_frames, this never resets at
        # a door and is the sole duration used for cutscene reward qualification.
        self._skip_session_frames = 0
        # (entry_prev, crossing_state) queued by bg skip; credited on main thread.
        self._pending_skip_room_crossings: list[
            tuple[dict[str, Any], dict[str, Any]]
        ] = []
        # Illegal pre-Kenneth Main Hall entry — flushed as episode failure.
        self._pending_episode_failure: str | None = None

    def _load_stage(self) -> None:
        with self.curriculum_path.open(encoding="utf-8") as f:
            self._stage = json.load(f)
        route_path = self.project_root / "data" / "route_jill_anypct.json"
        self._planner = WaypointPlanner(
            route_path,
            waypoints=self._stage.get("waypoints"),
            route_steps=self._stage.get("route_steps"),
            terminal_goal_room=self._stage.get("success_room"),
        )
        from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS

        weapons = frozenset(
            canonical_item(ITEM_IDS[item_id])
            for item_id in WEAPON_ITEM_IDS
            if item_id in ITEM_IDS
        )
        self._items = ItemTracker(
            todo=build_item_todo(route_path),
            repeat_pickups=True,
            once_only=frozenset(KEY_ITEM_NAMES),
            presence_only=weapons,
        )
        self._encoder = ObsEncoder(
            self.project_root / "data" / "rooms.json",
            self.graph,
            curriculum_stage_index=int(self._stage.get("stage_index", 0)),
        )

    def _read_state(self, *, track_items: bool = True) -> dict[str, Any]:
        fields = list(DEFAULT_RAM_FIELDS)
        fields.extend(self._enemy_fields)
        if INTERACTION_PROMPT is not None:
            fields.append(("interaction_prompt_raw", INTERACTION_PROMPT, "u8"))
        fields.extend(
            [
                ("player_poison", PLAYER_POISON, "u8"),
                ("scene_flag", SCENE_FLAG, "u8"),
                ("msg_flag", MESSAGE_FLAG, "u8"),
                ("player_anim", PLAYER_ANIM_STATE, "u8"),
                ("player_aux", PLAYER_ACTION_AUX, "u8"),
            ]
        )
        ram = self.bridge.read_ram(fields)
        # Compose the community room code "SRR" (stage 1-7, room hex), e.g.
        # stage=0 room=5 -> "105" (Dining Room); matches rooms.json / route.
        room_code = f"{int(ram['stage_id']) + 1}{int(ram['room_id']):02X}"
        hp = int(ram.get("player_hp", 0))
        inv_slots = decode_inventory(ram)  # [(name, qty), ...]
        if track_items:
            new_items = self._items.update(inv_slots)
        else:
            names = {canonical_item(name) for name, _ in inv_slots}
            new_items = names - self._items.ever_held
        return {
            "hp": hp,
            "room_id": room_code,
            "x": int(ram.get("player_x", 0)),
            "y": int(ram.get("player_y", 0)),
            "z": int(ram.get("player_z", 0)),
            "facing": int(ram.get("player_facing", 0)),
            "cam_id": int(ram.get("cam_id", 0)),
            "character_id": int(ram.get("character_id", 1)),
            "in_control": bool(int(ram.get("game_mode", 0)) & IN_CONTROL_MASK),
            "game_state": int(ram.get("game_state", 0)),
            "game_mode": int(ram.get("game_mode", 0)),
            "scene_flag": int(ram.get("scene_flag", 0)),
            "msg_flag": int(ram.get("msg_flag", 0)),
            "stage_id": int(ram.get("stage_id", 0)),
            "room_byte": int(ram.get("room_id", 0)),
            "enemies": decode_enemy_table(ram),
            "interaction_prompt": bool(
                int(ram.get("interaction_prompt_raw", 0)) & INTERACTION_PROMPT_MASK
            ),
            "inventory": [name for name, _ in inv_slots],
            "inventory_slots": inv_slots,
            "equipped_weapon_id": int(ram.get("equipped_weapon_id", 0)),
            # ever-held-gated: banking an item then re-grabbing it is not "new"
            "new_items": sorted(new_items),
            "step": self._step_count,
            # hp==0 before first positive read is cutscene/menu init, not death.
            "dead": episode_death_signal_from_ram(
                ram,
                episode_start_hp=getattr(self, "_episode_start_hp", 0),
                prev_hp=self._prev_hp,
            ),
            "poisoned": bool(int(ram.get("player_poison", 0))),
            "maps_files_flags": int(ram.get("maps_files_flags", 0)),
            "gallery_progress": int(ram.get("gallery_progress", 0)),
            "gallery_confirm": int(ram.get("gallery_confirm", 0)),
            "player_anim": int(ram.get("player_anim", 0)),
            "player_aux": int(ram.get("player_aux", 0)),
            "anim_history": list(getattr(self, "_anim_history", [])),
        }

    def _init_anim_history(self) -> None:
        from re1_rl.knife_macro import read_knife_hooks

        try:
            hooks = read_knife_hooks(self.bridge)
        except (OSError, RuntimeError, ValueError):
            hooks = (0, 0, 0)
        self._anim_history = [hooks] * 4

    def _sample_anim_history(self) -> None:
        from re1_rl.knife_macro import read_knife_hooks

        try:
            hooks = read_knife_hooks(self.bridge)
        except (OSError, RuntimeError, ValueError):
            hooks = (0, 0, 0)
        if not hasattr(self, "_anim_history"):
            self._anim_history = []
        self._anim_history.append(hooks)
        while len(self._anim_history) > 4:
            self._anim_history.pop(0)

    def _refresh_anim_history_before_obs(self) -> bool:
        """Macro steps replace anim hist with pin captures; else one step sample."""
        pins = self.bridge.attack_pins
        if pins.ready():
            self._anim_history = pins.macro_anim_history()
            return True
        self._sample_anim_history()
        return False

    def _box_obs(self, state: dict[str, Any]) -> np.ndarray:
        """Encode item-box contents; refresh the RAM cache in box rooms."""
        from re1_rl.item_box import is_box_room, read_box

        room = str(state.get("room_id", ""))
        in_box_room = is_box_room(room)
        if in_box_room or self._box_cache is None:
            try:
                self._box_cache = read_box(self.bridge)
            except (OSError, RuntimeError, ValueError):
                pass
        return encode_box(self._box_cache, in_box_room=in_box_room)

    def _episode_truncated(self) -> bool:
        max_ep = int(self._stage.get("max_steps", 3000))
        if max_ep > 0 and self._step_count >= max_ep:
            return True
        return stagnation_episode_timeout(self._progress)

    def _build_obs(self, frame_obs: np.ndarray, state: dict[str, Any]) -> dict[str, np.ndarray]:
        assert self._encoder is not None and self._planner is not None
        self._sync_episode_history(state)
        max_ep = int(self._stage.get("max_steps", 48000))
        hist = self._episode_history.encode(
            current_step=int(state.get("step", self._step_count)),
            room_index=self._encoder.room_index,
            max_episode_steps=max_ep,
        )
        cutscene_ledger = encode_cutscene_ledger(self._progress.rewarded_cutscenes)
        goal_state = dict(state)
        goal_state["gallery_needs_reentry"] = self._progress.gallery_needs_reentry
        return {
            "frame": frame_obs,
            "proprio": self._encoder.encode_proprio(state, self._prev_hp),
            "goal": self._encoder.encode_goal(
                goal_state, self._planner,
                item_tracker=self._items, room_items=self.room_items,
            ),
            "spatial": self._spatial.encode(
                state, room_items=self.room_items, item_tracker=self._items,
            ),
            "visited": self._visited.plane(state.get("room_id", "")),
            "rooms_visited": self._encoder.encode_rooms_visited(self._progress.visited_rooms),
            "box": self._box_obs(state),
            "inventory": encode_inventory_slots(state.get("inventory_slots")),
            "history": hist["history"],
            "acquisitions": hist["acquisitions"],
            "room_enemies": self._room_roster.encode(str(state.get("room_id", ""))),
            "keys_held": encode_keys_held(self._items.ever_held),
            "affordances": encode_affordances(
                ever_held=self._items.ever_held,
                inventory_slots=state.get("inventory_slots"),
                current_room=str(state.get("room_id", "")),
                room_index=self._encoder.room_index,
            ),
            "world_state": encode_world_state(
                catalog=self._world_catalog,
                room_items=self.room_items,
                ever_held=self._items.ever_held,
                inventory_names=_inventory_names_from_slots(state.get("inventory_slots")),
                current_room=str(state.get("room_id", "")),
            ),
            "cutscene_ledger": cutscene_ledger,
            "milestones": encode_milestones(
                current_room=str(state.get("room_id", "")),
                episode_history=self._episode_history,
                cutscene_ledger=cutscene_ledger,
                ever_held=self._items.ever_held,
                cutscenes_hit=len(self._progress.rewarded_cutscenes),
            ),
            "maps_files": encode_maps_files_flags(state.get("maps_files_flags")),
        }

    def _sync_episode_history(self, state: dict[str, Any]) -> None:
        self._episode_history.on_step(
            state,
            prev_state=self._prev_state,
            new_items=state.get("new_items") or [],
        )

    def _capture_step_obs(self) -> np.ndarray:
        """Store the live framebuffer at ``emulated_frame`` and build [t-12..t]."""
        if self.bridge.emulated_frame >= 0:
            fc = self.bridge.emulated_frame
            self.bridge.frame_ring.store_rgb(fc, self.bridge.screenshot())
        return self.bridge.build_frame_stack()

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed)
        self._stop_bg_skip()
        self._skipping_flag = False
        self._bg_death = False
        self._skip_cache_obs = None
        self._skip_cache_state = None
        self._post_skip_sync = False
        self._post_skip_reward = 0.0
        self._post_skip_bd = {}
        self._cutscene_skip_entry_prev = None
        self._skip_session_frames = 0
        self._pending_skip_room_crossings = []
        self._pending_episode_failure = None
        self._load_stage()
        assert self._planner is not None

        state_path = self.project_root / self._stage["init_savestate"]
        self.bridge.load_savestate(str(state_path))
        self.bridge.frameadvance(1)
        if self._ram_skip.use_engine_patches:
            self._ram_skip.install_engine_patches()
        self._skip_uncontrolled()

        if self._stage.get("knife_equipped_start"):
            try:
                equip_knife_from_pause_menu(self.bridge)
                self._skip_uncontrolled()
            except (OSError, RuntimeError, ValueError):
                pass
        self._sticky_input.reset()
        self._prev_action = None
        self._forward_collision_stall = False
        self._use_phase = 0
        self._inventory_before_use = None
        self._equip_phase = 0
        self._combine_phase = 0
        self._combine_slot_a = None
        self._last_skip_frames = 0
        self._last_settled_skip_frames = 0
        self._last_settled_cutscene_key = None
        self._last_settled_skip_prev = None
        self._last_settled_skip_new = None
        self._last_settled_skip_kind = None
        self._init_anim_history()

        self._step_count = 0
        self._frame_stack = []
        self.bridge.frame_ring.clear()
        self.bridge.attack_pins.clear()
        self._progress = ProgressTracker()
        self._visited.reset()
        self._box_cache = None
        if getattr(self, "_attack_telemetry", None) is not None:
            self._attack_telemetry.reset_episode()
        if getattr(self, "_step_diag", None) is not None:
            self._step_diag.reset_episode()
        rgb = self.bridge.screenshot()
        if self.bridge.emulated_frame >= 0:
            self.bridge.frame_ring.store_rgb(self.bridge.emulated_frame, rgb)
        frame_obs = self.bridge.build_frame_stack()
        self._prev_hp = 0
        state = self._read_state()
        self._seed_episode_progress(state)
        self._episode_history.reset(str(state.get("room_id", "")), step=0)
        self._visited.update(state["room_id"], state["x"], state["z"])
        self._prev_state = state
        self._prev_hp = state["hp"]
        self._start_bg_skip()

        obs = self._build_obs(frame_obs, state)
        info = {
            "stage": self._stage.get("stage"),
            "waypoint": self._planner.next_waypoint_room(),
            "state": state,
        }
        return obs, info

    def _seed_episode_progress(self, state: dict[str, Any]) -> None:
        """Mark spawn room visited + arm spawn ``new_room`` on first reward step."""
        hp = int(state.get("hp", 0))
        self._episode_start_hp = hp if hp > 0 else 0
        self._episode_min_hp = self._episode_start_hp
        self._progress.seed_spawn_room(str(state.get("room_id", "")))

    def _skip_poll_ram(self) -> dict[str, int | float]:
        return self.bridge.read_ram(SKIP_POLL_RAM_FIELDS)

    def _probe_needs_skip(self) -> bool:
        return needs_skip_from_ram(self._skip_poll_ram())

    def _stop_bg_skip(self) -> None:
        self._bg_skip_stop.set()
        if self._bg_skip_thread is not None and self._bg_skip_thread.is_alive():
            self._bg_skip_thread.join(timeout=5.0)
        self._bg_skip_thread = None

    def _start_bg_skip(self) -> None:
        if not self._async_cutscene_skip:
            return
        if self._bg_skip_thread is not None and self._bg_skip_thread.is_alive():
            return
        self._bg_skip_stop.clear()
        self._bg_skip_thread = threading.Thread(
            target=self._bg_skip_worker, name="re1-cutscene-skip", daemon=True
        )
        self._bg_skip_thread.start()

    def _bg_skip_worker(self) -> None:
        while not self._bg_skip_stop.is_set():
            if self._macro_active:
                self._bg_skip_stop.wait(0.003)
                continue
            if not self._probe_needs_skip():
                self._skipping_flag = False
                self._bg_skip_stop.wait(0.003)
                continue
            if not self._skipping_flag:
                self._last_skip_frames = 0
                self._skip_session_frames = 0
                # Live skip-entry pose (harness parity). Stale _prev_state can be
                # idle while Kenneth scene_flag is already 0x84.
                try:
                    self._cutscene_skip_entry_prev = dict(
                        self._read_state(track_items=False)
                    )
                except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                    self._cutscene_skip_entry_prev = (
                        dict(self._prev_state) if self._prev_state else None
                    )
                try:
                    self._ram_skip.clear_skip_script_peaks()
                except AttributeError:
                    pass
                # Inventory snapshot for story USE / gold_emblem put-back annotate.
                try:
                    from re1_rl.item_box import read_inventory
                    from re1_rl.weapon_equip import policy_inventory

                    self._inventory_before_skip = policy_inventory(
                        read_inventory(self.bridge)
                    )
                except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                    self._inventory_before_skip = None
            self._skipping_flag = True
            # Chunk like play_human cutscene_skip_chunk so mid-skip room crossings
            # can restart the script segment (door = new_room, not new_cutscene).
            chunk = int(getattr(self._ram_skip, "skip_chunk", 600) or 600)
            burned, died = self._ram_skip.skip_uncontrolled(
                max_frames=chunk,
                prev_hp=self._prev_hp,
                episode_start_hp=getattr(self, "_episode_start_hp", 0),
            )
            self._last_skip_frames = int(getattr(self, "_last_skip_frames", 0)) + int(
                burned
            )
            self._skip_session_frames = int(
                getattr(self, "_skip_session_frames", 0)
            ) + int(burned)
            if not died:
                died = self._poll_death_during_skip()
            if died:
                self._bg_death = True
            # Detect door crossing on bg thread; credit on main thread only.
            try:
                self._note_async_skip_room_crossing()
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                pass
            if not self._probe_needs_skip():
                self._skipping_flag = False
                self._post_skip_sync = True
                try:
                    self._refresh_skip_cache()
                except (OSError, RuntimeError, ValueError):
                    pass

    def _cutscene_key(self, source: dict[str, Any] | None) -> str | None:
        from re1_rl.cutscene_reward import cutscene_key_from_state

        return cutscene_key_from_state(source)

    def _qualify_cutscene_reward(
        self,
        skip_frames: int,
        prev_state: dict[str, Any] | None,
        new_state: dict[str, Any] | None,
    ) -> str | None:
        from re1_rl.cutscene_reward import qualify_cutscene_reward

        return qualify_cutscene_reward(
            skip_frames=skip_frames,
            prev_state=prev_state,
            new_state=new_state,
            episode_start_hp=int(getattr(self, "_episode_start_hp", 0)),
            rewarded_cutscenes=self._progress.rewarded_cutscenes,
            visited_rooms=self._progress.visited_rooms,
            cutscene_blocked_after_pickup_room=(
                self._progress.cutscene_blocked_after_pickup_room
            ),
        )

    def _merge_post_skip_breakdown(
        self, reward: float, bd: dict[str, float]
    ) -> None:
        self._post_skip_reward = float(
            getattr(self, "_post_skip_reward", 0.0)
        ) + float(reward)
        merged = dict(getattr(self, "_post_skip_bd", {}) or {})
        for k, v in bd.items():
            merged[k] = float(merged.get(k, 0.0)) + float(v)
        self._post_skip_bd = merged

    def _note_async_skip_room_crossing(self) -> None:
        """Bg-thread safe: queue door crossing + restart script segment counters."""
        entry_prev = getattr(self, "_cutscene_skip_entry_prev", None)
        if not entry_prev:
            return
        try:
            state = self._read_state(track_items=False)
        except (OSError, RuntimeError, ValueError):
            return
        if str(state.get("room_id", "")) == str(entry_prev.get("room_id", "")):
            return
        crossing = dict(state)
        self._pending_skip_room_crossings.append((dict(entry_prev), crossing))
        # Restart segment immediately so post-door script frames (Kenneth) accrue
        # against the destination room — same as play_human mid-chunk credit.
        self._cutscene_skip_entry_prev = dict(crossing)
        self._last_skip_frames = 0
        try:
            self._ram_skip.clear_skip_script_peaks()
        except AttributeError:
            pass

    def _illegal_main_hall_transition(
        self,
        prev_state: dict[str, Any] | None,
        state: dict[str, Any] | None,
    ) -> bool:
        """True on Kenneth-gate transition (enter 106 before Kenneth paid).

        The first breach poisons positive rewards/extensions in compute_reward;
        the episode continues. Returns False when Jill is already dead.
        """
        from re1_rl.cutscene_reward import (
            illegal_main_hall_before_kenneth_transition,
        )

        if not prev_state or not state:
            return False
        if state.get("dead"):
            return False
        return illegal_main_hall_before_kenneth_transition(
            str(prev_state.get("room_id", "") or ""),
            str(state.get("room_id", "") or ""),
            rewarded_cutscenes=self._progress.rewarded_cutscenes,
            visited_rooms=self._progress.visited_rooms,
        )

    def _illegal_main_hall_failure_reason(
        self,
        prev_state: dict[str, Any] | None,
        state: dict[str, Any] | None,
    ) -> str | None:
        """Legacy name: the poisoned gate does not end the episode.

        Returns the telemetry reason string when the soft transition fires,
        else None. Callers must not treat this as episode failure.
        """
        from re1_rl.cutscene_reward import ILLEGAL_MAIN_HALL_FAILURE_REASON

        if self._illegal_main_hall_transition(prev_state, state):
            return ILLEGAL_MAIN_HALL_FAILURE_REASON
        return None

    def _flush_pending_episode_failure(
        self, action: int
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]] | None:
        reason = getattr(self, "_pending_episode_failure", None)
        if not reason:
            return None
        self._pending_episode_failure = None
        self._post_skip_reward = 0.0
        self._post_skip_bd = {}
        self._skipping_flag = False
        return self._episode_failure_step(action, reason=reason)

    def _credit_async_skip_room_crossing(self) -> None:
        """Harness parity: door mid-skip pays ``new_room`` only (main thread)."""
        # Also catch a crossing on the final chunk if bg note missed it.
        entry_prev = getattr(self, "_cutscene_skip_entry_prev", None)
        if entry_prev is not None and not self._pending_skip_room_crossings:
            try:
                state = self._read_state(track_items=False)
            except (OSError, RuntimeError, ValueError):
                state = None
            if state is not None and str(state.get("room_id", "")) != str(
                entry_prev.get("room_id", "")
            ):
                self._pending_skip_room_crossings.append(
                    (dict(entry_prev), dict(state))
                )
                self._cutscene_skip_entry_prev = dict(state)
                self._last_skip_frames = 0

        while self._pending_skip_room_crossings:
            entry, crossing = self._pending_skip_room_crossings.pop(0)
            crossing = dict(crossing)
            crossing["cutscene_key"] = None
            self._progress.record_in_control_step(
                str(crossing.get("room_id", "")),
                bool(crossing.get("in_control", True)),
            )
            # Kenneth gate: compute_reward applies -1.6, poisons all positive
            # rewards/extensions, and continues the episode.
            reward, bd = compute_reward(
                entry,
                crossing,
                self._planner,
                progress=self._progress,
                graph=self.graph,
                success_room=self._stage.get("success_room"),
                return_breakdown=True,
            )
            self._merge_post_skip_breakdown(float(reward), dict(bd))
            self._prev_state = dict(crossing)

    def _apply_post_skip_sync(self) -> None:
        """Credit pickups / cutscenes that finished while async skip was running."""
        from re1_rl.story_item_use import annotate_story_use_success

        # Flush any door crossing (harness _credit_skip_room_crossing).
        try:
            self._credit_async_skip_room_crossing()
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass

        state = self._read_state(track_items=True)
        state = dict(state)
        inv_after = None
        inv_before = getattr(self, "_inventory_before_skip", None)
        entry_prev = getattr(self, "_cutscene_skip_entry_prev", None) or self._prev_state
        try:
            from re1_rl.item_box import read_inventory
            from re1_rl.weapon_equip import policy_inventory

            inv_after = policy_inventory(read_inventory(self.bridge))
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            inv_after = None
        # Sets story_use_success and/or gold_emblem_return from inv delta.
        state = annotate_story_use_success(
            state,
            prev_state=entry_prev,
            inventory_before=inv_before,
            inventory_after=inv_after,
            rewarded_site_ids=self._progress.rewarded_story_uses,
        )
        self._inventory_before_skip = None
        # Authoritative policy inventory for pickup→cutscene disqualify.
        if inv_before is not None:
            entry_prev = dict(entry_prev or {})
            entry_prev["inventory"] = list(inv_before)
        if inv_after is not None:
            state["inventory"] = list(inv_after)
        # Reward qualification is duration-only apart from explicit menu,
        # pickup, death, opening, and pre-Kenneth hall exclusions. Door crossings
        # keep their new_room credit and contribute to this full-session duration.
        state["cutscene_key"] = self._qualify_cutscene_reward(
            int(getattr(self, "_skip_session_frames", 0)),
            entry_prev,
            state,
        )
        reward, bd = compute_reward(
            entry_prev,
            state,
            self._planner,
            progress=self._progress,
            graph=self.graph,
            success_room=self._stage.get("success_room"),
            return_breakdown=True,
        )
        self._merge_post_skip_breakdown(float(reward), dict(bd))
        self._prev_state = state
        self._cutscene_skip_entry_prev = None
        self._pending_skip_room_crossings = []
        # Stash for monitor/harness before session counters reset. Do not let
        # gate panels fall back to step_emulated_frames (lies as "4 < 20").
        from re1_rl.cutscene_reward import skip_session_kind

        self._last_settled_skip_frames = int(
            getattr(self, "_skip_session_frames", 0) or 0
        )
        self._last_settled_cutscene_key = state.get("cutscene_key")
        self._last_settled_skip_prev = dict(entry_prev) if entry_prev else None
        self._last_settled_skip_new = dict(state)
        self._last_settled_skip_kind = skip_session_kind(entry_prev, state)
        self._last_skip_frames = 0
        self._skip_session_frames = 0
        if state["hp"] > 0:
            self._prev_hp = state["hp"]
        hp_now = int(state["hp"])
        if hp_now > 0:
            self._episode_min_hp = min(self._episode_min_hp, hp_now)

    def _poll_death_during_skip(self) -> bool:
        """Lightweight HP poll while async skip is burning (dog/hunter scenes)."""
        if self._skip_cache_state and self._skip_cache_state.get("dead"):
            return True
        # Require two consecutive zero-HP reads so a one-frame flicker does not
        # abort cutscene skip (false episode end near low-HP combat).
        try:
            hp_ram = self.bridge.read_ram([("player_hp", PLAYER_HP, "u16")])
            hp = int(hp_ram.get("player_hp", 0))
        except (OSError, RuntimeError, ValueError):
            return False
        start_hp = getattr(self, "_episode_start_hp", 0)
        if not player_died(hp, prev_hp=self._prev_hp, episode_start_hp=start_hp):
            return False
        try:
            hp_ram2 = self.bridge.read_ram([("player_hp", PLAYER_HP, "u16")])
            hp2 = int(hp_ram2.get("player_hp", 0))
        except (OSError, RuntimeError, ValueError):
            return True
        return player_died(hp2, prev_hp=self._prev_hp, episode_start_hp=start_hp)

    def _confirm_death_after_abort(self) -> str | None:
        """After mid-step/skip HP abort: keep episode alive unless death sticks."""
        reason = self._probe_episode_failure()
        if reason is not None:
            return reason
        try:
            self.bridge.frameadvance(4)
        except (OSError, RuntimeError, ValueError):
            pass
        reason = self._probe_episode_failure()
        if reason is not None:
            return reason
        try:
            ram = self._failure_ram_probe()
            port = getattr(self.bridge, "port", None)
            print(
                f"[death_false_positive] port={port} "
                f"hp={int(ram.get('player_hp', -1))} "
                f"gs=0x{int(ram.get('game_state', 0)):08X} "
                f"mode=0x{int(ram.get('game_mode', 0)):02X} "
                f"room={int(ram.get('stage_id', 0)) + 1}{int(ram.get('room_id', 0)):02X}",
                flush=True,
            )
        except (OSError, RuntimeError, ValueError):
            print("[death_false_positive] (ram probe failed)", flush=True)
        return None

    def _refresh_skip_cache(self) -> None:
        frame_obs = self._capture_step_obs()
        state = self._read_state(track_items=False)
        self._skip_cache_state = state
        self._skip_cache_obs = self._build_obs(frame_obs, state)
        if state.get("dead"):
            self._bg_death = True
        max_ep_steps = int(self._stage.get("max_steps", 3000))
        self._skip_cache_truncated = self._episode_truncated()

    def _fast_cutscene_step(
        self, action: int
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        if self._poll_death_during_skip():
            death = self._death_step(
                action, died_during_skip=True, died_during_step=False
            )
            if death is not None:
                self._skipping_flag = False
                return death
        pending = self._flush_pending_episode_failure(action)
        if pending is not None:
            return pending
        self._step_count += 1
        # Main-thread flush of door crossings noted by the bg skip worker.
        try:
            self._credit_async_skip_room_crossing()
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass
        pending = self._flush_pending_episode_failure(action)
        if pending is not None:
            return pending
        if self._skip_cache_obs is None:
            try:
                self._refresh_skip_cache()
            except (OSError, RuntimeError, ValueError):
                pass
        obs = self._skip_cache_obs
        if obs is None:
            obs = self._build_obs(
                np.zeros(FRAME_SHAPE, dtype=np.uint8),
                self._prev_state or {"hp": 0, "room_id": "", "x": 0, "z": 0, "facing": 0},
            )
        truncated = self._skip_cache_truncated
        info = {
            "room_id": self._prev_state.get("room_id"),
            "cutscene_skip": True,
            "action_name": ACTION_NAMES[int(action)],
            "bridge_port": getattr(self.bridge, "port", None),
        }
        return obs, 0.0, False, truncated, info

    def _death_penalty(self) -> tuple[float, dict[str, float]]:
        breakdown = {"death": DEATH_PENALTY}
        return float(DEATH_PENALTY * REWARD_SCALE), breakdown

    def _episode_failure_penalty(
        self, reason: str
    ) -> tuple[float, dict[str, float]]:
        from re1_rl.cutscene_reward import ILLEGAL_MAIN_HALL_FAILURE_REASON

        if reason == ILLEGAL_MAIN_HALL_FAILURE_REASON:
            breakdown = {
                ILLEGAL_MAIN_HALL_FAILURE_REASON: MAIN_HALL_BEFORE_KENNETH_PENALTY
            }
            return (
                float(MAIN_HALL_BEFORE_KENNETH_PENALTY * REWARD_SCALE),
                breakdown,
            )
        return self._death_penalty()

    def _failure_ram_probe(self) -> dict[str, int]:
        return self.bridge.read_ram(
            [
                ("player_hp", PLAYER_HP, "u16"),
                ("stage_id", STAGE_ID, "u8"),
                ("room_id", ROOM_ID, "u8"),
                ("character_id", CHARACTER_ID, "u8"),
                ("game_mode", GAME_MODE, "u8"),
                ("game_state", GAME_STATE, "u32"),
                ("msg_flag", MESSAGE_FLAG, "u8"),
                ("scene_flag", SCENE_FLAG, "u8"),
            ]
        )

    def _probe_episode_failure(self) -> str | None:
        ram = self._failure_ram_probe()
        return episode_failure_reason(
            ram,
            episode_start_hp=getattr(self, "_episode_start_hp", 0),
            prev_hp=self._prev_hp,
        )

    def _episode_failure_step(
        self,
        action: int,
        *,
        reason: str,
        died_during_skip: bool = False,
        died_during_step: bool = False,
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        self._stop_bg_skip()
        self._skipping_flag = False
        self._sticky_input.reset()
        self._step_count += 1
        try:
            frame_obs = self._capture_step_obs()
            state = self._read_state()
            state = dict(state)
            state["dead"] = True
        except (OSError, RuntimeError, ValueError):
            state = dict(self._prev_state)
            state["dead"] = True
            frame_obs = self.bridge.build_frame_stack()
            if frame_obs.shape != FRAME_SHAPE:
                frame_obs = np.zeros(FRAME_SHAPE, dtype=np.uint8)
        reward, breakdown = self._episode_failure_penalty(reason)
        obs = self._build_obs(frame_obs, state)
        opening_phase = reason if reason.startswith(
            (
                "playstation_",
                "title_",
                "opening_",
                "press_",
                "mansion_",
                "boot_",
                "death_",
                "scripted_",
            )
        ) else None
        info = {
            "room_id": state.get("room_id"),
            "episode_failure": reason,
            "visited_rooms": sorted(self._progress.visited_rooms),
            "n_rooms_visited": len(self._progress.visited_rooms),
            "max_waypoint": self._progress.max_waypoint,
            "outside_gameplay": reason
            if reason
            in {
                "main_menu_room",
                "front_end_zero_hp",
                "title_attract",
                "menu_room_in_run",
                "pause_or_options_menu",
                "options_menu",
            }
            else None,
            "opening_phase": opening_phase,
            "screen_id": pause_menu_screen_id(int(state.get("game_state", 0))),
            "died_during_skip": died_during_skip,
            "died_during_step": died_during_step,
            "bridge_port": getattr(self.bridge, "port", None),
            "action_name": ACTION_NAMES[int(action)],
            "reward_breakdown": breakdown,
            "state": state,
        }
        return obs, reward, True, False, info

    def _death_step(
        self, action: int, *, died_during_skip: bool, died_during_step: bool
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]] | None:
        """Terminate on confirmed death. Return None if mid-step abort was a flicker."""
        if died_during_step or died_during_skip:
            reason = self._confirm_death_after_abort()
            if reason is None:
                return None
        else:
            reason = self._probe_episode_failure() or "hp_death"
        return self._episode_failure_step(
            action,
            reason=reason,
            died_during_skip=died_during_skip,
            died_during_step=died_during_step,
        )

    def _probe_outside_gameplay(self) -> str | None:
        return self._probe_episode_failure()

    def _outside_gameplay_step(
        self, action: int, *, reason: str
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        return self._episode_failure_step(action, reason=reason)

    def _try_dismiss_options_menu(self) -> tuple[bool, dict[str, Any]]:
        """Dismiss OPTIONS bug screen. Returns (recovered, report)."""
        self._sticky_input.reset()
        self._macro_active = True
        try:
            still, _frames, report = dismiss_options_menu(
                self.bridge,
                prev_hp=self._prev_hp,
                episode_start_hp=getattr(self, "_episode_start_hp", 0),
            )
        finally:
            self._macro_active = False
            self._sticky_input.reset()
        if still:
            port = getattr(self.bridge, "port", "?")
            print(
                f"[options_dismiss_fail] port={port} report={report}",
                flush=True,
            )
        return (not still), report

    def _inventory_macro_owns_item_menu(self, action: int) -> bool:
        """True while equip/use/combine (or any bridge macro) owns the ITEM screen."""
        if bool(getattr(self, "_macro_active", False)):
            return True
        if int(getattr(self, "_use_phase", 0)) > 0:
            return True
        if int(getattr(self, "_equip_phase", 0)) > 0:
            return True
        if int(getattr(self, "_combine_phase", 0)) > 0:
            return True
        a = int(action)
        return a in (USE_ACTION, EQUIP_ACTION, COMBINE_ACTION)

    def _probe_item_inventory_menu(self) -> bool:
        from re1_rl.ram_skip import item_inventory_screen_from_ram

        try:
            return item_inventory_screen_from_ram(self._skip_poll_ram())
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            return False

    def _try_dismiss_orphan_item_menu(self) -> tuple[bool, dict[str, Any]]:
        """Close orphan START/ITEM pause. Returns (recovered, report)."""
        from re1_rl.inventory_menu_macro import dismiss_orphan_item_menu

        self._sticky_input.reset()
        self._macro_active = True
        try:
            still, _frames, report = dismiss_orphan_item_menu(
                self.bridge,
                prev_hp=self._prev_hp,
                episode_start_hp=getattr(self, "_episode_start_hp", 0),
            )
        finally:
            self._macro_active = False
            self._sticky_input.reset()
        if still:
            port = getattr(self.bridge, "port", "?")
            print(
                f"[item_menu_dismiss_fail] port={port} report={report}",
                flush=True,
            )
        return (not still), report

    def _skip_uncontrolled(self, max_frames: int | None = None) -> tuple[int, bool]:
        """Wait at turbo speed until player control returns (doors, cutscenes)."""
        kwargs: dict[str, Any] = {
            "prev_hp": self._prev_hp,
            "episode_start_hp": getattr(self, "_episode_start_hp", 0),
        }
        if max_frames is None:
            skipped, died = self._ram_skip.skip_uncontrolled(**kwargs)
        else:
            skipped, died = self._ram_skip.skip_uncontrolled(
                max_frames=max_frames, **kwargs
            )
        self._last_skip_frames = int(skipped)
        return skipped, died

    @staticmethod
    def _is_magic_action(action: int) -> bool:
        return DEPOSIT_ACTION_BASE <= action < (
            WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS
        )

    def _handle_use_action(
        self, action: int
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]] | None:
        from re1_rl.herb_combine import combine_slot_from_action
        from re1_rl.inventory_menu_macro import execute_use_macro
        from re1_rl.item_box import read_inventory
        from re1_rl.item_use import any_legal_use_slot, slot_legal_for_use
        from re1_rl.story_item_use import (
            any_legal_story_use_slot,
            slot_legal_for_story_use,
            story_site_for_slot,
        )
        from re1_rl.weapon_equip import policy_inventory

        if self._use_phase == 0 and action != USE_ACTION:
            if SELECT_SLOT_BASE <= action < SELECT_SLOT_BASE + N_SELECT_SLOT:
                return self._submenu_step(
                    action,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "use_not_open"},
                )
            return None

        state = getattr(self, "_prev_state", {}) or {}
        inventory: list[tuple[int, int]] | None = None
        try:
            inventory = policy_inventory(read_inventory(self.bridge))
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass
        current_hp = int(state.get("hp", 0))
        poisoned = bool(state.get("poisoned", False))
        episode_start_hp = int(getattr(self, "_episode_start_hp", 0) or 0)
        room_id = str(state.get("room_id", "") or "") or None
        player_x = state.get("x")
        player_z = state.get("z")
        rewarded_story = getattr(self, "_progress", None)
        rewarded_site_ids = (
            rewarded_story.rewarded_story_uses if rewarded_story is not None else None
        )
        story_kwargs = {
            "room": room_id,
            "x": player_x,
            "z": player_z,
            "rewarded_site_ids": rewarded_site_ids,
        }
        heal_legal = any_legal_use_slot(
            inventory or [],
            current_hp=current_hp,
            poisoned=poisoned,
            episode_start_hp=episode_start_hp,
        )
        story_legal = any_legal_story_use_slot(inventory or [], **story_kwargs)

        if self._use_phase == 0:
            if not heal_legal and not story_legal:
                return self._submenu_step(
                    action,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "use_not_legal"},
                )
            self._use_phase = 1
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": True, "reason": "use_open"},
            )

        slot = combine_slot_from_action(action, select_slot_base=SELECT_SLOT_BASE)
        self._use_phase = 0
        if slot is None:
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": False, "reason": "use_abort"},
            )
        heal_ok = slot_legal_for_use(
            inventory or [],
            int(slot),
            current_hp=current_hp,
            poisoned=poisoned,
            episode_start_hp=episode_start_hp,
        )
        story_ok = slot_legal_for_story_use(
            inventory or [],
            int(slot),
            **story_kwargs,
        )
        if not heal_ok and not story_ok:
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": False, "reason": "use_slot_not_legal"},
            )
        story_site = None
        if story_ok and inventory is not None:
            story_site = story_site_for_slot(
                inventory,
                int(slot),
                **story_kwargs,
            )
        self._inventory_before_use = list(inventory) if inventory is not None else None
        self._macro_active = True
        try:
            try:
                died, frames, magic_report = execute_use_macro(
                    self.bridge,
                    int(slot),
                    prev_hp=self._prev_hp,
                    episode_start_hp=getattr(self, "_episode_start_hp", 0),
                    story_site=story_site,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                died, frames = False, self.frame_skip
                magic_report = {"ok": False, "reason": f"error:{exc}"}
        finally:
            self._macro_active = False
        return self._submenu_step(
            action,
            step_emulated_frames=frames,
            magic_report=magic_report,
            died=died,
        )

    def _handle_equip_action(
        self, action: int
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]] | None:
        from re1_rl.attack_macro import read_equipped_weapon
        from re1_rl.herb_combine import combine_slot_from_action
        from re1_rl.inventory_menu_macro import execute_equip_macro
        from re1_rl.item_box import read_inventory
        from re1_rl.weapon_equip import (
            any_legal_equip_slot,
            read_equipped_slot_0based,
            slot_legal_for_equip,
        )

        if self._equip_phase == 0 and action != EQUIP_ACTION:
            if SELECT_SLOT_BASE <= action < SELECT_SLOT_BASE + N_SELECT_SLOT:
                return self._submenu_step(
                    action,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "equip_not_open"},
                )
            return None

        equipped_id: int | None = None
        equipped_slot_0b: int | None = None
        inventory: list[tuple[int, int]] | None = None
        try:
            equipped_id = read_equipped_weapon(self.bridge)
            equipped_slot_0b = read_equipped_slot_0based(self.bridge)
            inventory = read_inventory(self.bridge)
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass

        if self._equip_phase == 0:
            if not any_legal_equip_slot(
                inventory or [],
                equipped_weapon_id=equipped_id,
                equipped_slot_0based=equipped_slot_0b,
            ):
                return self._submenu_step(
                    action,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "equip_not_legal"},
                )
            self._equip_phase = 1
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": True, "reason": "equip_open"},
            )

        slot = combine_slot_from_action(action, select_slot_base=SELECT_SLOT_BASE)
        self._equip_phase = 0
        if slot is None:
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": False, "reason": "equip_abort"},
            )
        if not slot_legal_for_equip(
            inventory or [],
            int(slot),
            equipped_weapon_id=equipped_id,
            equipped_slot_0based=equipped_slot_0b,
        ):
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": False, "reason": "equip_slot_not_legal"},
            )
        self._macro_active = True
        try:
            try:
                died, frames, magic_report = execute_equip_macro(
                    self.bridge,
                    int(slot),
                    prev_hp=self._prev_hp,
                    episode_start_hp=getattr(self, "_episode_start_hp", 0),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                died, frames = False, self.frame_skip
                magic_report = {"ok": False, "reason": f"error:{exc}"}
        finally:
            self._macro_active = False
        return self._submenu_step(
            action,
            step_emulated_frames=frames,
            magic_report=magic_report,
            died=died,
        )

    def _handle_combine_action(
        self, action: int
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]] | None:
        """Three-step herb COMBINE menu; returns None when action is normal gameplay."""
        from re1_rl.herb_combine import combine_slot_from_action
        from re1_rl.inventory_combine import (
            any_valid_combine,
            slot_legal_as_first,
            slot_legal_as_second,
        )
        from re1_rl.inventory_menu_macro import execute_combine_macro
        from re1_rl.item_box import read_inventory

        if self._combine_phase == 0 and action != COMBINE_ACTION:
            if SELECT_SLOT_BASE <= action < SELECT_SLOT_BASE + N_SELECT_SLOT:
                return self._submenu_step(
                    action,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "combine_not_open"},
                )
            return None

        inventory: list[tuple[int, int]] | None = None
        try:
            inventory = read_inventory(self.bridge)
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass

        if self._combine_phase == 0:
            if not any_valid_combine(inventory or []):
                return self._submenu_step(
                    action,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "combine_not_legal"},
                )
            self._combine_phase = 1
            self._combine_slot_a = None
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": True, "reason": "combine_open"},
            )

        slot = combine_slot_from_action(action, select_slot_base=SELECT_SLOT_BASE)
        if slot is None:
            self._combine_phase = 0
            self._combine_slot_a = None
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": False, "reason": "combine_abort"},
            )

        if self._combine_phase == 1:
            if not slot_legal_as_first(inventory or [], int(slot)):
                self._combine_phase = 0
                self._combine_slot_a = None
                return self._submenu_step(
                    action,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "combine_slot_not_legal"},
                )
            self._combine_slot_a = int(slot)
            self._combine_phase = 2
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={
                    "ok": True,
                    "reason": "combine_first_slot",
                    "slot": int(slot),
                },
            )

        slot_a = self._combine_slot_a
        self._combine_phase = 0
        self._combine_slot_a = None
        if slot_a is None:
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": False, "reason": "combine_abort"},
            )
        if not slot_legal_as_second(inventory or [], int(slot_a), int(slot)):
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": False, "reason": "combine_pair_not_legal"},
            )
        self._macro_active = True
        try:
            try:
                died, frames, magic_report = execute_combine_macro(
                    self.bridge,
                    int(slot_a),
                    int(slot),
                    prev_hp=self._prev_hp,
                    episode_start_hp=getattr(self, "_episode_start_hp", 0),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                died, frames = False, self.frame_skip
                magic_report = {"ok": False, "reason": f"error:{exc}", "product": None}
        finally:
            self._macro_active = False
        return self._submenu_step(
            action,
            step_emulated_frames=frames,
            magic_report=magic_report,
            died=died,
        )

    def _submenu_step(
        self,
        action: int,
        *,
        step_emulated_frames: int,
        magic_report: dict[str, Any] | None,
        died: bool = False,
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        """Inventory submenu step; contempt scales with ``step_emulated_frames``."""
        if died:
            death = self._death_step(
                action, died_during_skip=False, died_during_step=True
            )
            if death is not None:
                return death
        assert self._planner is not None
        self._step_count += 1
        macro_pins = self._refresh_anim_history_before_obs()
        frame_obs = self._capture_step_obs()
        if macro_pins:
            self.bridge.attack_pins.clear()
        state = self._read_state()
        state = dict(state)
        state["step_emulated_frames"] = int(step_emulated_frames)
        state["reference_step_frames"] = self.frame_skip
        inv_before = getattr(self, "_inventory_before_use", None)
        if inv_before is not None:
            from re1_rl.item_box import read_inventory
            from re1_rl.story_item_use import annotate_story_use_success
            from re1_rl.weapon_equip import policy_inventory

            try:
                inv_after = policy_inventory(read_inventory(self.bridge))
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                inv_after = None
            state = annotate_story_use_success(
                state,
                prev_state=self._prev_state,
                inventory_before=inv_before,
                inventory_after=inv_after,
                rewarded_site_ids=self._progress.rewarded_story_uses,
            )
            # Macro ok is authoritative when annotate misses a non-consuming USE.
            report = magic_report or {}
            if (
                not state.get("story_use_success")
                and report.get("ok")
                and report.get("reason") == "story_use_ok"
                and report.get("story_use_site")
            ):
                state["story_use_success"] = str(report["story_use_site"])
            self._inventory_before_use = None
        self._visited.update(state["room_id"], state["x"], state["z"])
        self._progress.record_in_control_step(
            state.get("room_id", ""),
            bool(state.get("in_control", True)),
        )
        reward, breakdown = compute_reward(
            self._prev_state,
            state,
            self._planner,
            progress=self._progress,
            graph=self.graph,
            success_room=self._stage.get("success_room"),
            return_breakdown=True,
        )
        terminated = bool(state.get("dead"))
        truncated = self._episode_truncated()
        obs = self._build_obs(frame_obs, state)
        info = {
            "room_id": state["room_id"],
            "hp": state["hp"],
            "bridge_port": getattr(self.bridge, "port", None),
            "action_name": ACTION_NAMES[int(action)],
            "reward_breakdown": breakdown,
            "magic_report": magic_report,
            "use_phase": int(self._use_phase),
            "equip_phase": int(self._equip_phase),
            "combine_phase": int(self._combine_phase),
            "combine_slot_a": self._combine_slot_a,
            "inventory": state["inventory_slots"],
            "state": state,
        }
        self._prev_state = state
        if state["hp"] > 0:
            self._prev_hp = state["hp"]
        return obs, reward, terminated, truncated, info

    def _apply_magic_action(self, action: int) -> dict[str, Any]:
        """Box-transfer actions: RAM writes, no button inputs."""
        from re1_rl.attack_macro import read_equipped_weapon
        from re1_rl.item_box import apply_deposit, apply_withdraw

        try:
            if DEPOSIT_ACTION_BASE <= action < DEPOSIT_ACTION_BASE + N_DEPOSIT_ACTIONS:
                result = apply_deposit(
                    self.bridge,
                    action - DEPOSIT_ACTION_BASE,
                    equipped_weapon_id=read_equipped_weapon(self.bridge),
                )
                self._box_cache = None
                return result
            if WITHDRAW_ACTION_BASE <= action < (
                WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS
            ):
                result = apply_withdraw(self.bridge, action - WITHDRAW_ACTION_BASE)
                self._box_cache = None
                return result
        except (OSError, RuntimeError, ValueError) as exc:
            return {"ok": False, "reason": f"error:{exc}"}
        return {"ok": False, "reason": "unknown_action"}

    def _record_attack_telemetry(
        self,
        action: int,
        state: dict[str, Any],
        *,
        attack_report: dict[str, Any] | None,
        enemy_damage: int,
        enemy_kills: int,
        reward: float,
        reward_breakdown: dict[str, float],
        prev_state: dict[str, Any] | None = None,
    ) -> None:
        try:
            from re1_rl.attack_telemetry import AttackTelemetry
        except ImportError:
            return
        if getattr(self, "_attack_telemetry", None) is None:
            self._attack_telemetry = AttackTelemetry(
                port=getattr(self.bridge, "port", "?")
            )
        report = attack_report
        if report is None:
            report = getattr(self.bridge, "last_knife_anim_report", None)
        outcome = (report or {}).get("outcome", "ok")
        if outcome == "ok" and enemy_damage == 0 and enemy_kills == 0:
            outcome = "no_damage"
        weapon_label = (attack_report or {}).get("weapon")
        if weapon_label is None and report is not None:
            weapon_label = report.get("weapon")
        if weapon_label is None:
            weapon_label = "knife"
        self._attack_telemetry.record(
            ACTION_NAMES[int(action)],
            weapon_label,
            outcome,
            macro_report=report,
            enemy_damage=enemy_damage,
            enemy_kills=enemy_kills,
            ammo_spent=int(state.get("ammo_spent", 0)),
            state=state,
            reward=reward,
            reward_breakdown=reward_breakdown,
            prev_state=prev_state,
        )

    def action_masks(self, state: dict[str, Any] | None = None) -> np.ndarray:
        # During async cutscene skip, only noop is legal — ignore stale
        # _prev_state.in_control which can still look like combat control.
        if self._async_cutscene_skip and self._skipping_flag:
            mask = np.zeros(int(self.action_space.n), dtype=bool)
            if mask.size > 0:
                mask[0] = True
            return mask
        anim = aux = recovery = None
        equipped = None
        inventory = None
        box = None
        in_box_room = False
        equipped_slot_0b = None
        pose = dict(state if state is not None else (getattr(self, "_prev_state", {}) or {}))
        bridge = getattr(self, "bridge", None)
        if bridge is not None:
            try:
                anim, aux, recovery = read_knife_hooks(bridge)
                from re1_rl.attack_macro import read_equipped_weapon
                from re1_rl.memory_map import EQUIPPED_SLOT_INDEX_1BASED

                equipped = read_equipped_weapon(bridge)
                ram = bridge.read_ram(
                    [("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8")]
                )
                slot_1b = int(ram.get("equipped_slot_1based", 0))
                equipped_slot_0b = slot_1b - 1 if slot_1b > 0 else None
            except (OSError, RuntimeError, AttributeError, TypeError, ValueError):
                pass
            try:
                from re1_rl.item_box import is_box_room, read_box, read_inventory
                from re1_rl.weapon_equip import policy_inventory

                room = str(pose.get("room_id", ""))
                in_box_room = is_box_room(room)
                inventory = read_inventory(bridge)
                inventory = policy_inventory(inventory)
                if in_box_room:
                    box = read_box(bridge)
                else:
                    box = self._box_cache or [(0, 0)] * 16
            except (OSError, RuntimeError, AttributeError, TypeError,
                    ValueError, ImportError):
                pass
        progress = getattr(self, "_progress", None)
        rewarded_story_uses = (
            progress.rewarded_story_uses if progress is not None else None
        )
        enemies = pose.get("enemies")
        return build_action_mask(
            int(self.action_space.n),
            self._prev_action,
            player_anim=anim,
            player_aux=aux,
            player_recovery=recovery,
            equipped_weapon_id=equipped,
            equipped_slot_0based=equipped_slot_0b,
            inventory=inventory,
            box=box,
            in_box_room=in_box_room,
            equip_phase=int(getattr(self, "_equip_phase", 0)),
            use_phase=int(getattr(self, "_use_phase", 0)),
            combine_phase=int(getattr(self, "_combine_phase", 0)),
            combine_slot_a=getattr(self, "_combine_slot_a", None),
            current_hp=int(pose.get("hp", 0)),
            poisoned=bool(pose.get("poisoned", False)),
            episode_start_hp=int(getattr(self, "_episode_start_hp", 0) or 0),
            in_control=bool(pose.get("in_control", True)),
            alive_enemies_in_room=combat_enemy_count(enemies),
            knife_enemies_near=combat_enemy_count(enemies, knife=True),
            gun_enemies_near=combat_enemy_count(enemies),
            mask_combat_without_enemies=MASK_ATTACK_WITHOUT_ENEMIES,
            room_id=str(pose.get("room_id", "") or "") or None,
            player_x=pose.get("x"),
            player_z=pose.get("z"),
            rewarded_story_uses=rewarded_story_uses,
        )

    def step(self, action: int):
        action = int(action)
        # Capture the same mask the agent sees for this decision (pre-step state).
        pre_masks = None
        diag = getattr(self, "_step_diag", None)
        if diag is not None:
            try:
                pre_masks = self.action_masks()
            except (OSError, RuntimeError, AttributeError, TypeError, ValueError):
                pre_masks = None
        try:
            result = self._step_once(action)
            if diag is not None:
                _obs, reward, terminated, truncated, info = result
                inv = None
                if isinstance(info, dict):
                    inv = info.get("inventory")
                    state = info.get("state")
                    if inv is None and isinstance(state, dict):
                        inv = state.get("inventory_slots")
                aname = None
                try:
                    aname = ACTION_NAMES[action]
                except (IndexError, TypeError):
                    if isinstance(info, dict):
                        aname = info.get("action_name")
                diag.log_step(
                    reward=float(reward),
                    terminated=bool(terminated),
                    truncated=bool(truncated),
                    action_masks=pre_masks,
                    inventory_slots=inv,
                    hooks=None,
                    info=info if isinstance(info, dict) else None,
                    action=action,
                    action_name=aname,
                )
            return result
        finally:
            self._prev_action = action

    def _step_once(self, action: int):
        assert self._planner is not None
        self._start_bg_skip()
        if self._bg_death:
            self._bg_death = False
            death = self._death_step(
                action, died_during_skip=True, died_during_step=False
            )
            if death is not None:
                self._skipping_flag = False
                return death
        pending = self._flush_pending_episode_failure(action)
        if pending is not None:
            return pending
        menu_reason = self._probe_outside_gameplay()
        if menu_reason in {"options_menu", "pause_or_options_menu"}:
            recovered, _options_report = self._try_dismiss_options_menu()
            if recovered:
                menu_reason = self._probe_outside_gameplay()
            # If still trapped (or another failure), fall through to terminate.
        # Orphan START/ITEM pause (policy has no Start): close on a fresh step
        # only — never while equip/use/combine or another bridge macro owns it.
        if not self._inventory_macro_owns_item_menu(int(action)):
            if self._probe_item_inventory_menu():
                recovered, _item_report = self._try_dismiss_orphan_item_menu()
                if recovered:
                    self._skipping_flag = False
                    menu_reason = self._probe_outside_gameplay()
        if menu_reason in _DEATH_FAILURE_REASONS:
            death = self._death_step(
                action, died_during_skip=False, died_during_step=True
            )
            if death is not None:
                return death
            menu_reason = None
        if menu_reason:
            return self._outside_gameplay_step(action, reason=menu_reason)
        if self._async_cutscene_skip and self._skipping_flag:
            return self._fast_cutscene_step(action)

        if self._async_cutscene_skip and self._post_skip_sync:
            self._post_skip_sync = False
            try:
                self._apply_post_skip_sync()
            except (OSError, RuntimeError, ValueError):
                self._post_skip_reward = 0.0
                self._post_skip_bd = {}
            pending = self._flush_pending_episode_failure(action)
            if pending is not None:
                return pending

        if getattr(self, "_use_phase", 0) > 0 or int(action) == USE_ACTION:
            use_step = self._handle_use_action(int(action))
            if use_step is not None:
                return use_step

        if getattr(self, "_equip_phase", 0) > 0 or int(action) == EQUIP_ACTION:
            equip_step = self._handle_equip_action(int(action))
            if equip_step is not None:
                return equip_step

        if getattr(self, "_combine_phase", 0) > 0 or int(action) == COMBINE_ACTION:
            combine_step = self._handle_combine_action(int(action))
            if combine_step is not None:
                return combine_step

        knife = int(action) == KNIFE_SWING_ACTION
        attack = int(action) == ATTACK_ACTION
        attack_up = int(action) == ATTACK_UP_ACTION
        attack_down = int(action) == ATTACK_DOWN_ACTION
        combat_attack = attack or attack_up or attack_down
        magic = self._is_magic_action(int(action))
        attack_report: dict[str, Any] | None = None
        magic_report: dict[str, Any] | None = None
        step_emulated_frames = self.frame_skip
        if knife:
            self._sticky_input.apply(int(action), ACTION_BUTTON_MAP)
            self._macro_active = True
            try:
                died_during_step, step_emulated_frames = execute_knife_macro(
                    self.bridge,
                    empty_sticky=self._sticky_input.as_dict(),
                    phases=self.knife_phases,
                    scale=self.knife_scale,
                    echo_joypad=self.knife_echo_joypad,
                    use_ram_gates=self.knife_use_ram_gates,
                    prev_hp=self._prev_hp,
                    episode_start_hp=getattr(self, "_episode_start_hp", 0),
                )
            finally:
                self._macro_active = False
        elif combat_attack:
            self._sticky_input.apply(0, ACTION_BUTTON_MAP)
            self._macro_active = True
            try:
                    link_aim=True,
                from re1_rl.attack_macro import cleared_movement_sticky

                if attack_up:
                    attack_fn = execute_attack_up_macro
                elif attack_down:
                    attack_fn = execute_attack_down_macro
                else:
                    attack_fn = execute_attack_macro
                died_during_step, step_emulated_frames, attack_report = (
                    attack_fn(
                        self.bridge,
                        empty_sticky=cleared_movement_sticky(
                            self._sticky_input.as_dict()
                        ),
                        prev_hp=self._prev_hp,
                        episode_start_hp=getattr(self, "_episode_start_hp", 0),
                    )
                )
            finally:
                self._macro_active = False
        elif magic:
            magic_report = self._apply_magic_action(int(action))
            sticky, pulse, pulse_hold = self._sticky_input.apply(
                0, ACTION_BUTTON_MAP
            )
            _, died_during_step = self.bridge.step(
                n=self.frame_skip,
                sticky=sticky,
                pulse=pulse,
                pulse_hold=pulse_hold,
                ring_stride=0,
                capture_final=True,
            )
        else:
            from re1_rl.sticky_input import (
                INTERACT_ACTION,
                INTERACT_HOLD_EXTRA_FRAMES,
            )

            sticky, pulse, pulse_hold = self._sticky_input.apply(
                int(action), ACTION_BUTTON_MAP
            )
            hold_n = forward_hold_frames(
                self._prev_state,
                action=int(action),
                frame_skip=self.frame_skip,
                forward_collision_stall=bool(
                    getattr(self, "_forward_collision_stall", False)
                ),
            )
            if int(action) == INTERACT_ACTION:
                hold_n = max(hold_n, self.frame_skip + INTERACT_HOLD_EXTRA_FRAMES)
            step_emulated_frames = hold_n
            # Mid-hold Lua ring_stride PNG→b64 is expensive and redundant with
            # Python capture_final MMF (one shot at end of the hold).
            _, died_during_step = self.bridge.step(
                n=hold_n,
                sticky=sticky,
                pulse=pulse,
                pulse_hold=pulse_hold,
                ring_stride=0,
                capture_final=True,
            )
        if died_during_step:
            death = self._death_step(
                action, died_during_skip=False, died_during_step=True
            )
            if death is not None:
                self._skipping_flag = False
                return death
            died_during_step = False

        if self._async_cutscene_skip and self._probe_needs_skip():
            self._skipping_flag = True
            self._skip_cache_obs = None
            return self._fast_cutscene_step(action)

        skipped, died_during_skip = 0, False
        if not self._async_cutscene_skip:
            skipped, died_during_skip = self._skip_uncontrolled()
            if died_during_skip:
                death = self._death_step(
                    action, died_during_skip=True, died_during_step=False
                )
                if death is not None:
                    return death
                died_during_skip = False

        self._step_count += 1
        macro_pins = self._refresh_anim_history_before_obs()
        frame_obs = self.bridge.build_frame_stack()
        if macro_pins:
            self.bridge.attack_pins.clear()
        state = self._read_state()
        if died_during_skip or died_during_step:
            state = dict(state)
            state["dead"] = True
        state = apply_combat_step_fields(
            self._prev_state,
            state,
            knife=knife,
            attack=combat_attack,
        )
        enemy_damage = int(state.get("enemy_damage", 0))
        enemy_kills = int(state.get("enemy_kills", 0))
        state["step_emulated_frames"] = step_emulated_frames
        state["reference_step_frames"] = self.frame_skip
        state["cutscene_key"] = self._qualify_cutscene_reward(
            skipped, self._prev_state, state
        )
        if attack_report is not None:
            state["ammo_spent"] = int(attack_report.get("ammo_spent", 0))
            state["attack_weapon"] = attack_report.get("weapon")
        menu_reason = self._probe_outside_gameplay()
        if menu_reason in {"options_menu", "pause_or_options_menu"}:
            recovered, options_dismiss_report = self._try_dismiss_options_menu()
            if recovered:
                menu_reason = self._probe_outside_gameplay()
        if menu_reason:
            return self._outside_gameplay_step(action, reason=menu_reason)
        # Soft Kenneth gate handled inside compute_reward (-0.1, no terminate,
        # no 106 visit). Continue the episode so the agent can leave and retry.
        self._visited.update(state["room_id"], state["x"], state["z"])
        self._progress.record_in_control_step(
            state.get("room_id", ""),
            bool(state.get("in_control", True)),
        )

        reward, breakdown = compute_reward(
            self._prev_state,
            state,
            self._planner,
            progress=self._progress,
            graph=self.graph,
            success_room=self._stage.get("success_room"),
            return_breakdown=True,
        )
        if self._post_skip_reward or self._post_skip_bd:
            reward += self._post_skip_reward
            for k, v in self._post_skip_bd.items():
                breakdown[k] = breakdown.get(k, 0.0) + v
            self._post_skip_reward = 0.0
            self._post_skip_bd = {}

        if knife or combat_attack:
            self._record_attack_telemetry(
                action,
                state,
                attack_report=attack_report,
                enemy_damage=enemy_damage,
                enemy_kills=enemy_kills,
                reward=reward,
                reward_breakdown=breakdown,
                prev_state=self._prev_state,
            )

        terminated = bool(state.get("dead"))
        truncated = self._episode_truncated()

        hp_now = int(state["hp"])
        if hp_now > 0:
            self._episode_min_hp = min(self._episode_min_hp, hp_now)

        obs = self._build_obs(frame_obs, state)
        damage_taken = self._episode_min_hp < self._episode_start_hp
        info = {
            "room_id": state["room_id"],
            "hp": state["hp"],
            "episode_start_hp": self._episode_start_hp,
            "episode_min_hp": self._episode_min_hp,
            "damage_taken": damage_taken,
            "bridge_port": getattr(self.bridge, "port", None),
            "pos": (state["x"], state["z"], state["facing"]),
            "waypoint": self._planner.next_waypoint_room(),
            "waypoint_index": self._planner.waypoint_index,
            "max_waypoint": self._progress.max_waypoint,
            "visited_rooms": sorted(self._progress.visited_rooms),
            "n_rooms_visited": len(self._progress.visited_rooms),
            "success_room": self._stage.get("success_room"),
            "reached_success_room": self._progress.reached_success_room,
            "action_name": ACTION_NAMES[int(action)],
            "reward_breakdown": breakdown,
            "knife_anim_report": (
                getattr(self.bridge, "last_knife_anim_report", None) if knife else None
            ),
            "attack_report": attack_report,
            "magic_report": magic_report,
            "frames_skipped": skipped,
            "died_during_skip": died_during_skip,
            "died_during_step": died_during_step,
            "inventory": state["inventory_slots"],
            "new_items": state["new_items"],
            "ever_held": sorted(self._items.ever_held),
            "item_todo": self._items.progress(),  # (acquired, total)
            "next_item": (self._items.next_needed().item
                          if self._items.next_needed() else None),
            "items_left_here": (
                self.room_items.remaining_in_room(state["room_id"], self._items.ever_held)
                if self.room_items.loaded else None
            ),
            "gated_items_here": (
                self.room_items.gated_in_room(state["room_id"], self._items.ever_held)
                if self.room_items.loaded else None
            ),
            "state": state,
        }
        if breakdown.get("success_room", 0) > 0:
            info["gallery_flawless"] = not damage_taken
        self._forward_collision_stall = update_forward_collision_stall(
            self._prev_state,
            state,
            action=int(action),
        )
        self._prev_state = state
        if state["hp"] > 0:
            self._prev_hp = state["hp"]
        return obs, reward, terminated, truncated, info

    def render(self):
        plane = self.bridge.frame_ring.plane_at(self.bridge.emulated_frame)
        if plane is not None:
            return plane
        return np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)

    def close(self):
        self._stop_bg_skip()
        try:
            self.bridge.quit()
        finally:
            self.bridge.close()
