"""Gymnasium environment skeleton for Resident Evil 1."""

from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.enemy_combat import (
    apply_combat_step_fields,
    combat_enemy_count,
    paid_combat_enemy_count,
    tick_pending_combat_credit,
)
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
    player_died,
    player_poisoned_from_raw,
    decode_enemy_table,
    decode_inventory,
    decode_inventory_slots,
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
    LOGISTICS_DIM,
    PROPRIO_DIM,
    ROOM_VISITED_DIM,
    ObsEncoder,
    encode_box,
    encode_inventory_slots,
)
from re1_rl.weapon_damage import (
    LAST_ATTACK_DIM,
    WEAPON_CARD_DIM,
    empty_last_attack,
    encode_weapon_card,
    equipped_clip_from_inventory_slots,
    pack_last_attack,
)
from re1_rl.enemy_motion import EnemyMotionTracker, PlayerMotionTracker
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
from re1_rl.named_state import NAMED_STATE_DIM, encode_named_state
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
    softlock_frame_threshold,
    stagnation_episode_timeout,
)
from re1_rl.room_graph import RoomGraph, load_valid_rooms
from re1_rl.knife_macro import read_knife_hooks
from re1_rl.sticky_input import StickyInputState
from re1_rl.action_mask import (
    ATTACK_ACTION,
    ATTACK_DOWN_ACTION,
    ATTACK_UP_ACTION,
    BOX_BANK_BOSS_ACTION,
    BOX_CLOSE_ACTION,
    BOX_DEPOSIT_ACTION,
    BOX_PHASE_CHOOSE,
    BOX_PHASE_DEPOSIT_SLOT,
    BOX_PHASE_WITHDRAW_SLOT,
    BOX_WITHDRAW_ACTION,
    COMBINE_ACTION,
    DEPOSIT_ACTION_BASE,
    DEPOSIT_ACTION_NAMES,
    EQUIP_ACTION,
    INTERACT_ACTION,
    KNIFE_ID,
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
# Mask knife/gun macros when only idle gallery crows are in the near band.
MASK_ATTACK_PASSIVE_CROWS = os.environ.get(
    "MASK_ATTACK_PASSIVE_CROWS", "1"
).strip().lower() not in ("0", "false", "no", "off")

ACTION_NAMES = [
    "noop",
    "forward",
    "back",
    "turn_left",
    "turn_right",
    "run_forward",
    "attack_up",
    "attack",
    "attack_down",
    "interact",
    "use",  # open USE menu -> select_slot_N (2-step; herbs, sprays)
    "equip",  # open EQUIP menu -> select_slot_N (2-step)
        *DEPOSIT_ACTION_NAMES,    # 12-19 deposit_slot_N (box UI reuses 0-2 as modes)
        *WITHDRAW_ACTION_NAMES,   # 20-35 box withdrawals (box UI slot pick)
        *MENU_ACTION_NAMES,       # 36 combine + 37-44 select_slot_N
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
    7: {},  # attack macro (see execute_attack_macro)
    8: {},  # attack_down macro (see execute_attack_down_macro)
    9: {"cross": True},  # interact / confirm
}
for _idx in range(10, len(ACTION_NAMES)):
    ACTION_BUTTON_MAP[_idx] = {}


def button_map_for_action(
    action: int,
    *,
    pause_menu_modal: bool = False,
) -> dict[int, dict[str, bool]]:
    """Return joypad map for ``action``. Pause Yes/No: noop taps Cross."""
    if int(action) == 0 and pause_menu_modal:
        out = dict(ACTION_BUTTON_MAP)
        out[0] = {"cross": True}
        return out
    return ACTION_BUTTON_MAP


def _apply_action_input(
    sticky_input: StickyInputState,
    action: int,
    *,
    button_map: dict[int, dict[str, bool]] | None = None,
) -> tuple[dict[str, bool], dict[str, bool] | None, dict[str, bool] | None]:
    """Apply one canonical PPO action to sticky/pulsed controller state."""
    return sticky_input.apply(int(action), button_map or ACTION_BUTTON_MAP)

# BizHawk RE1 screenshot is 240x350 RGB; left 18 + right 12 px are near-black
# pillarbox. Pipeline: crop 320x240 game plane → gray → INTER_AREA to 84x63
# (landscape 4:3). Numpy shape (FRAME_H, FRAME_W) = (63, 84). NatureCNN flatten
# ~1792; resume uses async_fleet compatible-weight transplant.
from re1_rl.frame_ring import (
    FRAME_H,
    FRAME_SHAPE,
    FRAME_SQUARE,
    FRAME_STACK,
    FRAME_W,
    PILLARBOX_LEFT,
    PILLARBOX_LEFT_SQ,
    PILLARBOX_RIGHT,
    PILLARBOX_RIGHT_SQ,
    crop_game_plane,
    prune_square_pillarbox,
    resize_rgb_to_plane,
)

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

# OPTIONS / legacy CONFIG traps: dismiss and keep the episode alive (never
# hard-reset — dismiss_options_menu is the recovery path).
_OPTIONS_MENU_REASONS = frozenset({"options_menu", "pause_or_options_menu"})


def _prune_square_pillarbox(square: np.ndarray) -> np.ndarray:
    """Deprecated; pillarbox is cropped before resize now."""
    return prune_square_pillarbox(square)


def _resize_frame(
    frame: np.ndarray, size: tuple[int, int] | None = None
) -> np.ndarray:
    """RGB → crop pillarbox → grayscale → INTER_AREA to (FRAME_W, FRAME_H)."""
    return resize_rgb_to_plane(frame, size=size)


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
        camera_whiten: bool | None = None,
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
                # 4 stacked grayscale frames, channels-last (63 high x 84 wide)
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
                # factual route semantics through the next box or boss
                "logistics": spaces.Box(-1.0, 1.0, shape=(LOGISTICS_DIM,), dtype=np.float32),
                # equipped weapon card (clip, nominal dmg, round type, room bonuses)
                "weapon_card": spaces.Box(0.0, 1.0, shape=(WEAPON_CARD_DIM,), dtype=np.float32),
                # one-step last knife/attack memory (cleared next step)
                "last_attack": spaces.Box(0.0, 1.0, shape=(LAST_ATTACK_DIM,), dtype=np.float32),
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
                # Verified runtime bits only (no unmapped interaction_prompt).
                "named_state": spaces.Box(0.0, 1.0, shape=(NAMED_STATE_DIM,), dtype=np.float32),
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
        self._box_departure_snapshot: list[float] | None = None
        self._box_ui_open = False
        self._box_phase = BOX_PHASE_CHOOSE
        self._box_inv_cursor = 0
        self._box_list_cursor = 0
        self._box_inv_trusted_at_cursor = False
        self._episode_failure_override: str | None = None
        self._use_phase = 0
        self._inventory_before_use: list[tuple[int, int]] | None = None
        self._equip_phase = 0
        self._equip_switch_cooldown = 0
        self._combine_phase = 0
        self._combine_slot_a: int | None = None
        self._attack_telemetry = None
        self._last_attack_obs = empty_last_attack()
        self._stage: dict[str, Any] = {}
        self._step_count = 0
        self._leg_replay = None
        self._checkpoint_freeze_pending = False
        self._checkpoint_captured = False
        self._prev_state: dict[str, Any] = {}
        self._prev_hp = 0
        self._grab_escape_pending = False
        self._forward_collision_stall = False
        self._async_cutscene_skip = bool(async_cutscene_skip)
        self._bg_skip_stop = threading.Event()
        self._bg_skip_thread: threading.Thread | None = None
        # Serializes emu advancement in the bg skip worker vs yawn capture
        # savestate (macro flag alone only gates between skip chunks).
        self._bg_skip_emu_lock = threading.Lock()
        # Knife macro owns the joypad for its whole schedule; the bg skip
        # worker must not start a fast_forward (which mashes cross and stomps
        # joypad) while this is set.
        self._macro_active = False
        if camera_whiten is None:
            from re1_rl.camera_whiten import whitened_enabled_from_env

            camera_whiten = whitened_enabled_from_env()
        if camera_whiten:
            from re1_rl.camera_whiten import load_mansion_camera_bank

            self.bridge.camera_whiten_bank = load_mansion_camera_bank(self.project_root)
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
        self._cutscene_skip_origin_prev: dict[str, Any] | None = None
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
        # PB capture: one archive per trigger_id per episode (RE1_PB_CAPTURE=1).
        self._pb_captured_triggers: set[str] = set()
        self._go_capture_budget = {"last_capture_step": -10**9}
        from re1_rl.typewriter_save import TypewriterSaveDetector

        self._typewriter_save_detector = TypewriterSaveDetector()
        self._enemy_motion = EnemyMotionTracker()
        self._player_motion = PlayerMotionTracker()

    def _load_stage(self) -> None:
        with self.curriculum_path.open(encoding="utf-8") as f:
            self._stage = json.load(f)
        route_path = self.project_root / self._stage.get(
            "route_path", "data/route_jill_anypct.json"
        )
        self._planner = WaypointPlanner(
            route_path,
            waypoints=self._stage.get("waypoints"),
            route_steps=self._stage.get("route_steps"),
            terminal_goal_room=self._stage.get("success_room"),
            start_index=int(getattr(self, "_route_start_index", 0)),
        )
        if self._stage.get("mode") == "yawn_rails":
            from re1_rl.yawn_rails import validate_route

            errors = validate_route(self._planner.route, graph=self.graph)
            if errors:
                raise ValueError("invalid Yawn rails route: " + "; ".join(errors))
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
        inv_slots = decode_inventory_slots(ram)  # eight RAM-aligned slots
        occupied_inv = decode_inventory(ram)
        if track_items:
            new_items = self._items.update(occupied_inv)
        else:
            names = {canonical_item(name) for name, _ in occupied_inv}
            new_items = names - self._items.ever_held
        in_control = bool(int(ram.get("game_mode", 0)) & IN_CONTROL_MASK)
        px = int(ram.get("player_x", 0))
        pz = int(ram.get("player_z", 0))
        enemies = decode_enemy_table(ram)
        enemies = self._enemy_motion.update(
            enemies, room_code, in_control,
        )
        hp = int(ram.get("player_hp", 0))
        self._apply_yawn_poison_hp_floor(
            {
                "room_id": room_code,
                "hp": hp,
                "dead": False,
                "enemies": enemies,
            }
        )
        hp = self._revive_zero_hp_under_yawn_floor(hp)
        p_vx, p_vz = self._player_motion.update(px, pz, room_code, in_control)
        state_dict = {
            "hp": hp,
            "room_id": room_code,
            "x": px,
            "y": int(ram.get("player_y", 0)),
            "z": pz,
            "facing": int(ram.get("player_facing", 0)),
            "cam_id": int(ram.get("cam_id", 0)),
            "character_id": int(ram.get("character_id", 1)),
            "in_control": in_control,
            "game_state": int(ram.get("game_state", 0)),
            "game_mode": int(ram.get("game_mode", 0)),
            "scene_flag": int(ram.get("scene_flag", 0)),
            "msg_flag": int(ram.get("msg_flag", 0)),
            # Confirmed DEFAULT_RAM_FIELDS — exposed for named_state tower.
            "door_flags": int(ram.get("door_flags", 0)),
            "game_timer": int(ram.get("game_timer", 0)),
            "lab_timer": int(ram.get("lab_timer", 0)),
            "stage_id": int(ram.get("stage_id", 0)),
            "room_byte": int(ram.get("room_id", 0)),
            "enemies": enemies,
            "player_world_vx": p_vx,
            "player_world_vz": p_vz,
            "interaction_prompt": bool(
                int(ram.get("interaction_prompt_raw", 0)) & INTERACTION_PROMPT_MASK
            ),
            "inventory": [name for name, _ in occupied_inv],
            "inventory_slots": inv_slots,
            "equipped_weapon_id": int(ram.get("equipped_weapon_id", 0)),
            "equipped_slot_0based": (
                int(ram.get("equipped_slot_1based", 0)) - 1
                if 1 <= int(ram.get("equipped_slot_1based", 0)) <= 8
                else None
            ),
            # ever-held-gated: banking an item then re-grabbing it is not "new"
            "new_items": sorted(new_items),
            "step": self._step_count,
            # hp==0 before first positive read is cutscene/menu init, not death.
            "dead": episode_death_signal_from_ram(
                ram,
                episode_start_hp=getattr(self, "_episode_start_hp", 0),
                prev_hp=self._prev_hp,
            ),
            "poisoned": player_poisoned_from_raw(ram.get("player_poison", 0)),
            "maps_files_flags": int(ram.get("maps_files_flags", 0)),
            "gallery_progress": int(ram.get("gallery_progress", 0)),
            "gallery_confirm": int(ram.get("gallery_confirm", 0)),
            "dining_statue_flag": int(ram.get("dining_statue_flag", 0)),
            "dining_statue_knocked": bool(
                int(ram.get("dining_statue_flag", 0) or 0) & 0x10
            ),
            "dining_statue_x": int(ram.get("dining_statue_x", 0) or 0),
            "dining_statue_z": int(ram.get("dining_statue_z", 0) or 0),
            "player_anim": int(ram.get("player_anim", 0)),
            "player_aux": int(ram.get("player_aux", 0)),
            "player_recovery": int(ram.get("player_recovery", 0)),
            "anim_history": list(getattr(self, "_anim_history", [])),
        }
        from re1_rl.item_box import is_box_room

        planner = getattr(self, "_planner", None)
        cid = ""
        if planner is not None:
            cid = str((planner.current_objective() or {}).get("checkpoint_id") or "")
        need_box = is_box_room(room_code) or cid == "yawn_box_prep_118"
        cache = getattr(self, "_box_cache", None)
        if need_box:
            try:
                from re1_rl.item_box import read_box_live

                cache = read_box_live(self.bridge)
                self._box_cache = cache
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                cache = getattr(self, "_box_cache", None)
        if cache is not None:
            state_dict["box_cache"] = list(cache)
        return state_dict

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
        from re1_rl.item_box import is_box_room, read_box_live

        room = str(state.get("room_id", ""))
        in_box_room = is_box_room(room)
        if in_box_room or self._box_cache is None:
            try:
                # Full 48-slot live array — UI scroll parks past index 15.
                self._box_cache = read_box_live(self.bridge)
            except (OSError, RuntimeError, ValueError):
                pass
        return encode_box(self._box_cache, in_box_room=in_box_room)

    def _box_pollution_failure(self) -> str | None:
        """Terminal if a key (or any deep-slot item) is parked in the live box."""
        from re1_rl.item_box import box_pollution_reason, read_box_live

        try:
            live = read_box_live(self.bridge)
            self._box_cache = live
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            live = getattr(self, "_box_cache", None)

        room_id = str(
            (getattr(self, "_prev_state", {}) or {}).get("room_id", "") or ""
        )
        return box_pollution_reason(live, room_id=room_id)

    def _apply_box_ui_cursors_from_report(
        self,
        report: dict[str, Any],
        *,
        inv_cursor_in: int,
        box_cursor_in: int,
    ) -> None:
        """Update session cursors only after a successful transfer."""
        if not report.get("ok"):
            self._log_box_transfer_report(
                report,
                inv_cursor_in=inv_cursor_in,
                box_cursor_in=box_cursor_in,
            )
            from re1_rl.item_box_ui_macro import transfer_failure_zeros_session_cursors

            if transfer_failure_zeros_session_cursors(report):
                self._box_inv_cursor = 0
                self._box_list_cursor = 0
                self._box_inv_trusted_at_cursor = False
            return
        if report.get("inv_cursor") is not None:
            self._box_inv_cursor = int(report["inv_cursor"])
        if report.get("box_cursor") is not None:
            self._box_list_cursor = int(report["box_cursor"])
        self._box_inv_trusted_at_cursor = True

    def _log_box_transfer_report(
        self,
        report: dict[str, Any],
        *,
        inv_cursor_in: int,
        box_cursor_in: int,
    ) -> None:
        """Log cursor desync / exchange side effects (memlog + stderr when enabled)."""
        if not report.get("exchange_detected") and not report.get("ram_changed"):
            return
        payload = {
            "box_transfer_diag": True,
            "action": report.get("action"),
            "ok": report.get("ok"),
            "reason": report.get("reason"),
            "cursor_in": report.get("cursor_in")
            or {"inv": inv_cursor_in, "box": box_cursor_in},
            "cursor_out": report.get("cursor_out"),
            "rehomed": report.get("rehomed"),
            "exchange_detected": report.get("exchange_detected"),
            "ram_changed": report.get("ram_changed"),
        }
        import logging

        logging.getLogger(__name__).warning("box_ui_transfer: %s", payload)
        diag = getattr(self, "_step_diag", None)
        if diag is not None and hasattr(diag, "log_event"):
            try:
                diag.log_event(payload)
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
                pass

    def _weapon_card_obs(self, state: dict[str, Any]) -> np.ndarray:
        from re1_rl.ammo_accounting import (
            inventory_slots_to_id_qty,
            reserve_ammo,
            total_fireable_ammo,
        )
        from re1_rl.attack_macro import is_aim_stable

        wid = int(state.get("equipped_weapon_id", 0) or 0)
        slot = state.get("equipped_slot_0based")
        slots = list(state.get("inventory_slots") or [])
        valid = (
            slot is not None
            and 0 <= int(slot) < len(slots)
            and slots[int(slot)][0]
        )
        if valid:
            from re1_rl.memory_map import ITEM_IDS

            valid = ITEM_IDS.get(wid) == slots[int(slot)][0]
        clip = equipped_clip_from_inventory_slots(
            slots, wid, int(slot) if valid else None
        )
        inventory = inventory_slots_to_id_qty(slots)
        enemies = [
            enemy for enemy in (state.get("enemies") or [])
            if enemy.get("alive", True) and enemy.get("combat_near", False)
        ]
        nearest = min(enemies, key=lambda enemy: float(enemy.get("dist", 1e9)), default=None)
        bearing_sin = bearing_cos = rel_height = distance = 0.0
        if nearest is not None:
            dx = float(nearest.get("x", 0)) - float(state.get("x", 0))
            dz = float(nearest.get("z", 0)) - float(state.get("z", 0))
            relative = math.atan2(dz, dx) - (
                2.0 * math.pi * float(state.get("facing", 0)) / 4096.0
            )
            bearing_sin, bearing_cos = math.sin(relative), math.cos(relative)
            distance = float(nearest.get("dist", math.hypot(dx, dz)))
            rel_height = float(nearest.get("y", state.get("y", 0))) - float(
                state.get("y", 0)
            )
        anim = int(state.get("player_anim", 0))
        aux = int(state.get("player_aux", 0))
        recovery = int(state.get("player_recovery", 0))
        return encode_weapon_card(
            weapon_id=wid,
            equipped_clip=clip,
            room_id=state.get("room_id"),
            equipped_slot_0based=int(slot) if valid else None,
            reserve_ammo=reserve_ammo(inventory, wid),
            total_fireable=total_fireable_ammo(inventory, wid),
            hittable_enemies=len(enemies),
            nearest_distance=distance,
            nearest_bearing_sin=bearing_sin,
            nearest_bearing_cos=bearing_cos,
            nearest_relative_height=rel_height,
            aim_ready=is_aim_stable(anim, aux, recovery),
            recovery_ready=recovery == 0,
            weapon_state_valid=bool(valid),
        )

    def _combat_audit(
        self,
        state: dict[str, Any],
        attack_report: dict[str, Any] | None,
        breakdown: dict[str, float],
    ) -> dict[str, Any]:
        from re1_rl.ammo_accounting import (
            inventory_slots_to_id_qty,
            reserve_ammo,
            total_fireable_ammo,
        )

        wid = int(state.get("equipped_weapon_id", 0) or 0)
        slot = state.get("equipped_slot_0based")
        slots = state.get("inventory_slots") or []
        inv = inventory_slots_to_id_qty(slots)
        nearest = min(
            (
                enemy for enemy in (state.get("enemies") or [])
                if enemy.get("alive", True) and enemy.get("combat_near", False)
            ),
            key=lambda enemy: float(enemy.get("dist", 1e9)),
            default=None,
        )
        return {
            "equipped_weapon_id": wid,
            "equipped_slot_0based": slot,
            "loaded_ammo": equipped_clip_from_inventory_slots(slots, wid, slot),
            "reserve_ammo": reserve_ammo(inv, wid),
            "total_fireable_ammo": total_fireable_ammo(inv, wid),
            "nearest_threat_distance": (
                None if nearest is None else int(nearest.get("dist", 0))
            ),
            "hittable_enemy_count": combat_enemy_count(state.get("enemies")),
            "macro_outcome": (attack_report or {}).get("outcome"),
            "enemy_damage": int(state.get("enemy_damage", 0) or 0),
            "enemy_kills": int(state.get("enemy_kills", 0) or 0),
            "pending_combat_frames": int(state.get("pending_combat_frames") or 0),
            "credited_from_pending": bool(state.get("credited_from_pending")),
            "combat_events": [
                {
                    "slot": ev.get("slot"),
                    "damage": ev.get("damage"),
                    "killed": ev.get("killed"),
                    "is_yawn": ev.get("is_yawn"),
                    "is_boss": ev.get("is_boss"),
                    "type_id": ev.get("type_id"),
                }
                for ev in list(state.get("combat_events") or [])[:6]
            ],
            "combat_reward_terms": {
                key: float(breakdown.get(key, 0.0))
                for key in (
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
                )
            },
        }

    def _fill_last_attack_obs(
        self,
        prev_state: dict[str, Any],
        state: dict[str, Any],
        *,
        knife: bool,
        attack: bool,
        attack_report: dict[str, Any] | None,
        action_id: int,
    ) -> None:
        """One-step last_attack pack; same room/combat gates as apply_combat_step_fields."""
        if not knife and not attack:
            return
        prev_room = str(prev_state.get("room_id", "") or "")
        curr_room = str(state.get("room_id", "") or "")
        if prev_room and curr_room and prev_room != curr_room:
            return
        wid = int(
            (attack_report or {}).get("weapon_id")
            or state.get("equipped_weapon_id")
            or prev_state.get("equipped_weapon_id")
            or 0
        )
        clip_before = equipped_clip_from_inventory_slots(
            prev_state.get("inventory_slots"), wid,
            prev_state.get("equipped_slot_0based"),
        )
        ammo_spent = int(state.get("ammo_spent", 0) or 0)
        if attack_report is not None:
            ammo_spent = int(attack_report.get("ammo_spent", ammo_spent) or 0)
        clip_after = equipped_clip_from_inventory_slots(
            state.get("inventory_slots"), wid,
            state.get("equipped_slot_0based"),
        )
        if not knife and clip_after == 0 and clip_before > 0 and ammo_spent > 0:
            clip_after = max(0, clip_before - ammo_spent)
        self._last_attack_obs = pack_last_attack(
            knife=knife,
            attack=attack,
            combat_events=state.get("combat_events"),
            enemy_damage=int(state.get("enemy_damage", 0) or 0),
            enemy_kills=int(state.get("enemy_kills", 0) or 0),
            clip_before=clip_before,
            clip_after=clip_after,
            ammo_spent=ammo_spent,
            enemies_before=prev_state.get("enemies"),
            action_id=int(action_id),
        )

    def _max_episode_steps(self) -> int:
        """Curriculum ``max_steps`` plus per-checkpoint hard-wall extensions."""
        base = int(self._stage.get("max_steps", 3000))
        if base <= 0:
            return 0
        bonus = int(getattr(self._progress, "max_steps_bonus", 0) or 0)
        return base + max(0, bonus)

    def _episode_truncated(self) -> bool:
        max_ep = self._max_episode_steps()
        if max_ep > 0 and self._step_count >= max_ep:
            return True
        return stagnation_episode_timeout(self._progress)

    def _termination_flags(
        self, state: dict[str, Any]
    ) -> tuple[bool, bool, str | None]:
        """Return Gym termination flags, preserving the Wesker terminal mark."""
        kenneth_gate_failure = self._progress.kenneth_gate_breached
        wrong_room_failure = self._progress.wrong_room_breached
        forbidden_item_failure = self._progress.forbidden_item_breached
        gallery_wrong_failure = self._progress.gallery_wrong_breached
        capture_ineligible_failure = self._progress.capture_ineligible_breached
        box_pollution = getattr(self, "_episode_failure_override", None)
        checkpoint_success = (
            self._stage.get("mode") == "yawn_rails"
            and self._progress.checkpoint_success
            and bool(getattr(self, "_checkpoint_captured", False))
        )
        terminated = (
            bool(state.get("dead"))
            or kenneth_gate_failure
            or wrong_room_failure
            or forbidden_item_failure
            or gallery_wrong_failure
            or bool(box_pollution)
            or capture_ineligible_failure
            or checkpoint_success
        )
        truncated = (
            False
            if (
                kenneth_gate_failure
                or wrong_room_failure
                or forbidden_item_failure
                or gallery_wrong_failure
                or box_pollution
                or capture_ineligible_failure
            )
            else self._episode_truncated()
        )
        if kenneth_gate_failure:
            reason = "main_hall_before_kenneth"
        elif wrong_room_failure:
            reason = "wrong_room"
        elif forbidden_item_failure:
            reason = "forbidden_item"
        elif gallery_wrong_failure:
            reason = "gallery_wrong_portrait"
        elif box_pollution:
            reason = str(box_pollution)
        elif capture_ineligible_failure:
            reason = "checkpoint_capture_ineligible"
        elif checkpoint_success:
            reason = "checkpoint_success"
        else:
            reason = None
        return terminated, truncated, reason

    def _update_loadout_segment(
        self,
        prev_state: dict[str, Any],
        state: dict[str, Any],
        breakdown: dict[str, float],
        *,
        terminated: bool,
        truncated: bool,
    ) -> None:
        """Carry one semantic box-departure sample to its factual outcome."""
        from re1_rl.item_box import is_box_room

        prev_room = str(prev_state.get("room_id", ""))
        room = str(state.get("room_id", ""))
        left_box = is_box_room(prev_room) and not is_box_room(room)
        if (
            left_box
            and self._progress.loadout_segment is None
            and self._box_departure_snapshot is not None
        ):
            horizon = max(
                1,
                int(round(float(self._box_departure_snapshot[9]) * 16.0)),
            )
            self._progress.begin_loadout_segment(
                self._box_departure_snapshot,
                waypoint_index=int(self._planner.waypoint_index),
                horizon_checkpoints=horizon,
                departure_room=prev_room,
                departure_inventory=list(state.get("inventory_slots") or []),
            )
        if self._progress.loadout_segment is None:
            return
        reached_next_box = (
            not is_box_room(prev_room)
            and is_box_room(room)
        )
        boss_complete = (
            float(breakdown.get("checkpoint_success", 0.0)) > 0.0
            and int(self._planner.waypoint_index) >= int(self._planner.total_waypoints)
            and float(self._progress.loadout_segment["features"][10]) > 0.5
        )
        if reached_next_box or boss_complete or terminated or truncated:
            survived = not bool(state.get("dead"))
            completed = bool(reached_next_box or boss_complete)
            outcome = (
                "next_box" if reached_next_box
                else "boss_complete" if boss_complete
                else "death" if state.get("dead")
                else "truncation" if truncated
                else "failure"
            )
            self._progress.finish_loadout_segment(
                waypoint_index=int(self._planner.waypoint_index),
                survived=survived,
                completed=completed,
                outcome=outcome,
            )

    def _build_obs(self, frame_obs: np.ndarray, state: dict[str, Any]) -> dict[str, np.ndarray]:
        assert self._encoder is not None and self._planner is not None
        self._sync_episode_history(state)
        max_ep = self._max_episode_steps() or int(self._stage.get("max_steps", 48000))
        hist = self._episode_history.encode(
            current_step=int(state.get("step", self._step_count)),
            room_index=self._encoder.room_index,
            max_episode_steps=max_ep,
        )
        cutscene_ledger = encode_cutscene_ledger(
            self._progress.observed_cutscenes,
            wesker_pre_kenneth=self._progress.kenneth_gate_breached,
        )
        goal_state = dict(state)
        goal_state["gallery_needs_reentry"] = self._progress.gallery_needs_reentry
        goal_state["gallery_puzzle_solved"] = self._progress.gallery_puzzle_solved
        box_obs = self._box_obs(state)
        inventory_obs = encode_inventory_slots(state.get("inventory_slots"))
        logistics_obs = self._encoder.encode_logistics(state, self._planner)
        if box_obs[-1] > 0.5:
            self._box_departure_snapshot = np.concatenate(
                [logistics_obs, inventory_obs, box_obs]
            ).astype(np.float32).tolist()
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
            "box": box_obs,
            "inventory": inventory_obs,
            "logistics": logistics_obs,
            "weapon_card": self._weapon_card_obs(state),
            "last_attack": np.asarray(self._last_attack_obs, dtype=np.float32),
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
                cutscenes_hit=len(self._progress.observed_cutscenes),
                dining_statue_knocked=bool(state.get("dining_statue_knocked")),
            ),
            "maps_files": encode_maps_files_flags(state.get("maps_files_flags")),
            "named_state": encode_named_state(state),
        }

    def _sync_episode_history(self, state: dict[str, Any]) -> None:
        self._episode_history.on_step(
            state,
            prev_state=self._prev_state,
            new_items=state.get("new_items") or [],
        )

    def _poll_typewriter_save(
        self,
        prev_state: dict[str, Any] | None,
        state: dict[str, Any] | None,
    ) -> bool:
        """Advance save detector once per reward step. True on completed save."""
        detector = getattr(self, "_typewriter_save_detector", None)
        if detector is None:
            return False
        return bool(detector.update(prev_state, state))

    def _after_reward_step(
        self,
        prev_state: dict[str, Any],
        state: dict[str, Any],
        breakdown: dict[str, float],
        *,
        typewriter_save_complete: bool = False,
    ) -> None:
        """Auto-capture PB milestones when RE1_PB_CAPTURE=1."""
        from re1_rl.pb_capture import maybe_capture_pb, pb_capture_enabled, pb_root_dir
        from re1_rl.pb_milestones import detect_milestone_triggers
        from re1_rl.pb_sync import ensure_pb_sync_daemon
        from re1_rl.typewriter_save_log import (
            log_ctx_from_env,
            log_typewriter_save,
            state_fields,
        )

        ensure_pb_sync_daemon(self.project_root)
        save_room = None
        if typewriter_save_complete:
            detector = getattr(self, "_typewriter_save_detector", None)
            if detector is not None:
                save_room = getattr(detector, "last_room", None) or getattr(
                    detector, "completed_room", None
                )
        tw_log_ctx = {
            **log_ctx_from_env(self),
            **state_fields(state),
        }
        if save_room is not None:
            tw_log_ctx["save_room"] = save_room

        if typewriter_save_complete and not pb_capture_enabled():
            log_typewriter_save(
                "capture_skipped",
                reason="pb_capture_disabled",
                **tw_log_ctx,
            )

        if pb_capture_enabled():
            states_dir = pb_root_dir(self.project_root)
            triggers = detect_milestone_triggers(
                prev_state,
                state,
                breakdown,
                already_captured=self._pb_captured_triggers,
                typewriter_save_complete=typewriter_save_complete,
                typewriter_save_room=save_room,
                visited_rooms=self._progress.visited_rooms,
                rewarded_cutscenes=self._progress.rewarded_cutscenes,
                kenneth_gate_breached=bool(self._progress.kenneth_gate_breached),
            )
            if typewriter_save_complete and not triggers:
                if self._progress.kenneth_gate_breached:
                    reason = "kenneth_gate"
                elif save_room and str(state.get("room_id", "") or "") != str(save_room):
                    reason = "room_mismatch"
                elif any(
                    str(t).startswith("typewriter_save:")
                    for t in self._pb_captured_triggers
                ):
                    reason = "already_captured_episode"
                else:
                    reason = "milestone_gate"
                log_typewriter_save(
                    "capture_skipped",
                    reason=reason,
                    **tw_log_ctx,
                )
            for trigger_id in triggers:
                try:
                    maybe_capture_pb(
                        self,
                        trigger_id=trigger_id,
                        states_dir=states_dir,
                        captured=self._pb_captured_triggers,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    if str(trigger_id).startswith("typewriter_save:"):
                        log_typewriter_save(
                            "capture_error",
                            trigger=trigger_id,
                            error=str(exc),
                            **tw_log_ctx,
                        )

        self._queue_go_explore_progress(prev_state, state, breakdown)
        proposal = self._maybe_capture_go_explore(state)
        if proposal is not None:
            pending = getattr(self, "_go_explore_capture_pending", None)
            if pending is None:
                self._go_explore_capture_pending = []
                pending = self._go_explore_capture_pending
            pending.append(proposal)
        if float(breakdown.get("checkpoint_success", 0.0)) > 0.0:
            self._arm_checkpoint_freeze()

    def _arm_checkpoint_freeze(self) -> None:
        """Mark CP success. Capture on the next decision frame; last cell ends."""
        self._checkpoint_freeze_pending = True
        self._macro_active = True
        self._skipping_flag = False

    def _finish_checkpoint_capture(
        self, state: dict[str, Any], breakdown: dict[str, float]
    ) -> None:
        from re1_rl.yawn_rails import capture_successor_cell

        yr_prop = capture_successor_cell(self, state, breakdown)
        if yr_prop is not None:
            pending_yr = getattr(self, "_yawn_rails_capture_pending", None)
            if pending_yr is None:
                self._yawn_rails_capture_pending = []
                pending_yr = self._yawn_rails_capture_pending
            pending_yr.append(yr_prop)
        self._apply_yawn_capture_ineligibility_penalty(breakdown)
        self._checkpoint_freeze_pending = False
        ineligible = bool(
            getattr(self._progress, "capture_ineligible_breached", False)
        )
        # Last remaining cell (cp96 / configured leg_span) ends the episode
        # after capture. Intermediate cells keep playing from here.
        self._checkpoint_captured = (
            not ineligible and bool(self._progress.checkpoint_success)
        )
        self._macro_active = False

    def _try_decision_checkpoint_capture(self, action: int):
        """If CP already succeeded, freeze before any new policy inputs.

        Pickup Yes/No / leftover ITEM close are the previous interact finishing,
        not a new policy act. Turbo settle is forbidden here — it stutters.
        """
        if not getattr(self, "_checkpoint_freeze_pending", False):
            return None
        if getattr(self, "_checkpoint_captured", False):
            return None
        self._stop_bg_skip()
        self._macro_active = True
        self._skipping_flag = False
        try:
            self._auto_accept_pause_pickup_modal()
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass
        try:
            self._dismiss_non_box_pause_menu_if_safe()
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass
        try:
            state = dict(self._read_state())
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            state = dict(getattr(self, "_prev_state") or {})
        if state.get("dead") or int(state.get("hp", 0) or 0) <= 0:
            self._checkpoint_freeze_pending = False
            self._macro_active = False
            return None
        if not bool(state.get("in_control", True)):
            return self._checkpoint_wait_control_step(action)
        gate = {"checkpoint_success": 1.0}
        self._finish_checkpoint_capture(state, gate)
        return self._checkpoint_freeze_obs(action, state, gate)

    def _checkpoint_wait_control_step(self, action: int):
        """Play cinema at current speed; never apply the pending policy action."""
        from re1_rl.sticky_input import empty_sticky

        hold_n = max(1, int(self.frame_skip))
        try:
            self.bridge.step(
                n=hold_n,
                sticky=empty_sticky(),
                abort_on_zero_hp=False,
                ring_stride=0,
                capture_final=True,
            )
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass
        self._step_count += 1
        self._record_leg_replay_step(0, hold_n)
        try:
            state = dict(self._read_state())
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            state = dict(getattr(self, "_prev_state") or {})
        if (
            bool(state.get("in_control", True))
            and not state.get("dead")
            and int(state.get("hp", 0) or 0) > 0
        ):
            gate = {"checkpoint_success": 1.0}
            self._finish_checkpoint_capture(state, gate)
            return self._checkpoint_freeze_obs(action, state, gate)
        obs = self._checkpoint_live_obs(state)
        info = {
            "room_id": state.get("room_id"),
            "checkpoint_freeze_wait": True,
            "action_name": ACTION_NAMES[int(action)]
            if 0 <= int(action) < len(ACTION_NAMES)
            else str(action),
            "bridge_port": getattr(self.bridge, "port", None),
        }
        return obs, 0.0, False, self._episode_truncated(), info

    def _checkpoint_live_obs(self, state: dict[str, Any]) -> dict[str, Any]:
        try:
            frame_obs = self.bridge.build_frame_stack()
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            frame_obs = np.zeros(FRAME_SHAPE, dtype=np.uint8)
        return self._build_obs(frame_obs, state)

    def _checkpoint_freeze_obs(
        self,
        action: int,
        state: dict[str, Any],
        gate: dict[str, float],
    ):
        from re1_rl.reward import REWARD_SCALE

        self._prev_state = dict(state)
        terminated, truncated, episode_failure = self._termination_flags(state)
        obs = self._checkpoint_live_obs(state)
        reward = 0.0
        if float(gate.get("checkpoint_capture_ineligible", 0.0)) != 0.0:
            reward = sum(gate.values()) * REWARD_SCALE
        info = {
            "room_id": state.get("room_id"),
            "hp": state.get("hp"),
            "checkpoint_freeze": True,
            "action_name": ACTION_NAMES[int(action)]
            if 0 <= int(action) < len(ACTION_NAMES)
            else str(action),
            "bridge_port": getattr(self.bridge, "port", None),
            "reward_breakdown": dict(gate),
            "episode_failure": episode_failure,
        }
        return obs, float(reward), bool(terminated), bool(truncated), info

    def _apply_yawn_capture_ineligibility_penalty(
        self, breakdown: dict[str, float]
    ) -> None:
        """Claw back checkpoint_success when capture was hard-ineligible (not quality)."""
        from re1_rl.reward import RAILS_CAPTURE_INELIGIBLE_PENALTY
        from re1_rl.yawn_rails import yawn_capture_ineligible_reason

        if float(breakdown.get("checkpoint_success", 0.0)) <= 0.0:
            return
        if not yawn_capture_ineligible_reason(self):
            return
        if not self._progress.breach_capture_ineligible():
            return
        for term, value in breakdown.items():
            if value > 0.0:
                breakdown[term] = 0.0
        breakdown["checkpoint_success"] = 0.0
        breakdown["checkpoint_capture_ineligible"] = RAILS_CAPTURE_INELIGIBLE_PENALTY

    def _go_explore_archive(self):
        """Lazy-load canonical archive (monolithic / learner-local)."""
        arc = getattr(self, "_go_explore_archive_cache", None)
        if arc is not None:
            return arc
        from re1_rl.go_explore_archive import GoExploreArchive
        from re1_rl.go_explore_capture import resolve_archive_path

        path = resolve_archive_path(self.project_root)
        arc = GoExploreArchive(path)
        try:
            arc.load()
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        self._go_explore_archive_cache = arc
        return arc

    def _queue_go_explore_progress(
        self,
        prev_state: dict[str, Any],
        state: dict[str, Any],
        breakdown: dict[str, float],
    ) -> None:
        from re1_rl.go_explore_capture import go_explore_capture_enabled
        from re1_rl.go_explore_progress import detect_go_explore_progress_events

        if not go_explore_capture_enabled():
            return
        fired = getattr(self, "_go_explore_fired_reasons", None)
        if fired is None:
            self._go_explore_fired_reasons = set()
            fired = self._go_explore_fired_reasons
        pending = getattr(self, "_go_explore_pending_reasons", None)
        if pending is None:
            self._go_explore_pending_reasons = []
            pending = self._go_explore_pending_reasons
        for reason in detect_go_explore_progress_events(
            prev_state, state, breakdown, already=fired
        ):
            if reason not in pending:
                pending.append(reason)

    def _go_explore_capture_reasons(self, state: dict[str, Any]) -> list[str]:
        """Progress, room coverage, new digest buckets, and quality upgrades."""
        from re1_rl.go_explore_capture import (
            compute_quality,
            go_explore_root,
            quality_replace_significant,
        )
        from re1_rl.go_explore_progress import (
            bucket_new_reason,
            coverage_reason,
            quality_improve_reason,
        )
        from re1_rl.go_explore_semantic import (
            bucket_champion,
            manifest_index_by_semantic_bucket,
            semantic_bucket_key,
        )
        from re1_rl.go_explore_worker_cache import manifest_index_by_cell_key
        from re1_rl.milestone_digest import cell_key_v2, compute_digest

        reasons: list[str] = []
        pending = list(getattr(self, "_go_explore_pending_reasons", None) or [])
        reasons.extend(pending)

        room = str(state.get("room_id", "") or "").strip().upper()
        if not room:
            return reasons

        manifest_index = manifest_index_by_cell_key(go_explore_root(self.project_root))
        rows = [
            row
            for row in (manifest_index or {}).values()
            if isinstance(row, dict)
        ]
        has_room_cell = any(
            str(row.get("room_id") or "").strip().upper() == room for row in rows
        )
        if not has_room_cell:
            cov = coverage_reason(room)
            if cov not in reasons:
                reasons.append(cov)

        held = self._items.ever_held
        digest = compute_digest(state, self._progress, ever_held=held)
        sem_index = manifest_index_by_semantic_bucket(manifest_index)
        bucket_rows = list(sem_index.get(semantic_bucket_key(room, digest), []) or [])
        if not bucket_rows and pending:
            bucket_reason = bucket_new_reason(room, digest)
            if bucket_reason not in reasons:
                reasons.append(bucket_reason)

        x = int(state.get("x", state.get("player_x", 0)) or 0)
        z = int(state.get("z", state.get("player_z", 0)) or 0)
        key = cell_key_v2(room, x, z, digest)
        row = (manifest_index or {}).get(key)
        if isinstance(row, dict):
            old_q = row.get("quality")
            if isinstance(old_q, (list, tuple)) and len(old_q) >= 5:
                new_q = compute_quality(state, ever_held=self._items.ever_held, env=self)
                if quality_replace_significant(new_q, old_q):
                    reasons.append(quality_improve_reason(key))
        elif bucket_rows:
            champion = bucket_champion(bucket_rows)
            if champion is not None:
                old_q = champion.get("quality")
                if isinstance(old_q, (list, tuple)) and len(old_q) >= 5:
                    new_q = compute_quality(state, ever_held=self._items.ever_held, env=self)
                    if quality_replace_significant(new_q, old_q):
                        reasons.append(quality_improve_reason(f"bucket:{room}:{digest}"))
        return reasons

    def _maybe_capture_go_explore(self, state: dict[str, Any]) -> dict[str, Any] | None:
        from re1_rl.go_explore_capture import (
            capture_budget_available,
            go_explore_capture_enabled,
            go_explore_root,
            maybe_capture_cell,
        )
        from re1_rl.go_explore_worker_cache import manifest_index_by_cell_key

        if not go_explore_capture_enabled():
            return None

        # After the daily cap, skip manifest/digest work until a new day rolls.
        if getattr(self, "_go_explore_capture_paused", False):
            if not capture_budget_available(self.project_root):
                return None
            self._go_explore_capture_paused = False

        if not capture_budget_available(self.project_root):
            self._go_explore_capture_paused = True
            self._go_explore_pending_reasons = []
            return None

        reasons = self._go_explore_capture_reasons(state)
        if not reasons:
            return None

        def _save(dst: Path) -> None:
            self.bridge.save_savestate(str(dst))

        manifest_index = manifest_index_by_cell_key(go_explore_root(self.project_root))

        proposal = maybe_capture_cell(
            state,
            self._progress,
            self._go_explore_archive(),
            save_state=_save,
            ever_held=self._items.ever_held,
            env=self,
            project_root=self.project_root,
            env_step=self._step_count,
            capture_state=self._go_capture_budget,
            manifest_index=manifest_index,
            capture_reasons=reasons,
            require_reason=True,
        )
        if proposal is not None:
            fired = getattr(self, "_go_explore_fired_reasons", None)
            if fired is None:
                self._go_explore_fired_reasons = set()
                fired = self._go_explore_fired_reasons
            for r in reasons:
                fired.add(r)
            self._go_explore_pending_reasons = []
        elif not capture_budget_available(self.project_root):
            # Cap hit mid-attempt — stop replaying pending reasons into save_state.
            self._go_explore_capture_paused = True
            self._go_explore_pending_reasons = []
        return proposal

    def _capture_step_obs(self) -> np.ndarray:
        """Store the live framebuffer at ``emulated_frame`` and build [t-12..t]."""
        if self.bridge.emulated_frame >= 0:
            fc = self.bridge.emulated_frame
            self.bridge.frame_ring.store_rgb(fc, self.bridge.screenshot())
        return self.bridge.build_frame_stack()

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed)
        opts = dict(options or {})
        self._reset_options = opts
        self._route_start_index = int(opts.get("route_start_index", 0))
        self._stop_bg_skip()
        self.bridge.hp_floor = 0
        if getattr(self, "_progress", None) is not None:
            self._progress.yawn_retreated = False
        self._skipping_flag = False
        self._bg_death = False
        self._skip_cache_obs = None
        self._skip_cache_state = None
        self._post_skip_sync = False
        self._post_skip_reward = 0.0
        self._post_skip_bd = {}
        self._cutscene_skip_entry_prev = None
        self._cutscene_skip_origin_prev = None
        self._skip_session_frames = 0
        self._pending_skip_room_crossings = []
        self._pending_episode_failure = None
        # Flush PPO/sticky carry before any post-load frames advance. Worker
        # samples the next action only after this reset returns.
        self._sticky_input.reset()
        self._prev_action = None
        self._load_stage()
        assert self._planner is not None
        requested_leg_span = int(
            opts.get("leg_span", self._stage.get("legs_per_episode", 1))
        )
        self._leg_span = max(
            1, min(requested_leg_span, max(1, self._planner.waypoints_remaining))
        )
        self._pb_captured_triggers = set()
        self._go_explore_capture_pending = []
        self._yawn_rails_capture_pending = []
        self._checkpoint_freeze_pending = False
        self._checkpoint_captured = False
        self._go_explore_archive_cache = None
        self._go_capture_budget = {"last_capture_step": -10**9}
        # Progress reasons waiting for in_control (cutscene/pickup settle).
        self._go_explore_pending_reasons: list[str] = []
        self._go_explore_fired_reasons: set[str] = set()
        self._go_explore_coverage_attempted: set[str] = set()
        # Cleared when daily capture budget rolls; set when day cap is spent.
        self._go_explore_capture_paused = False

        from re1_rl.go_explore_capture import CELL_SIDECAR_NAME, CELL_STATE_NAME
        from re1_rl.pb_bundle_io import (
            bundle_room_matches_sidecar,
            is_slot_locked,
            verify_champion_bundle,
        )
        from re1_rl.pb_capture import load_sidecar_json, resolve_pb_bundle
        from re1_rl.pb_sidecar import apply_episode_sidecar

        pb_bundle = resolve_pb_bundle(opts)
        if pb_bundle is not None:
            sp = Path(pb_bundle["state_path"])
            state_path = sp if sp.is_absolute() else self.project_root / sp
            scp = Path(pb_bundle["sidecar_path"])
            sidecar_path = scp if scp.is_absolute() else self.project_root / scp
            # Refuse half-bundles: State/sidecar must live together under one slot.
            slot_dir = state_path.parent
            if state_path.parent != sidecar_path.parent:
                print(
                    f"[pb] refusing split bundle paths state={state_path} "
                    f"sidecar={sidecar_path}; falling back to fresh",
                    flush=True,
                )
                pb_bundle = None
            elif state_path.name == CELL_STATE_NAME:
                # Go-Explore cells use cell.State / cell.sidecar.json (not champion.*).
                if is_slot_locked(slot_dir):
                    print(
                        f"[pb] refusing locked go-explore cell dir={slot_dir}; "
                        f"falling back to fresh",
                        flush=True,
                    )
                    pb_bundle = None
                elif (
                    not state_path.is_file()
                    or not sidecar_path.is_file()
                    or sidecar_path.name != CELL_SIDECAR_NAME
                ):
                    print(
                        f"[pb] refusing incomplete go-explore cell state={state_path} "
                        f"sidecar={sidecar_path}; falling back to fresh",
                        flush=True,
                    )
                    pb_bundle = None
            else:
                ok, reason = verify_champion_bundle(slot_dir, require_unlocked=True)
                if not ok:
                    print(
                        f"[pb] refusing incoherent/locked champion ({reason}) "
                        f"dir={slot_dir}; falling back to fresh",
                        flush=True,
                    )
                    pb_bundle = None
                elif not sidecar_path.is_file() or not state_path.is_file():
                    print(
                        f"[pb] refusing incomplete bundle state={state_path} "
                        f"sidecar={sidecar_path}; falling back to fresh",
                        flush=True,
                    )
                    pb_bundle = None
        if pb_bundle is not None:
            sp = Path(pb_bundle["state_path"])
            state_path = sp if sp.is_absolute() else self.project_root / sp
        else:
            state_path = self.project_root / self._stage["init_savestate"]
        self.bridge.load_savestate(str(state_path))
        self.bridge.clear_latched_input()
        self.bridge.frameadvance(1)
        if self._ram_skip.use_engine_patches:
            self._ram_skip.install_engine_patches()
        # Slot-1 / post-retreat cells: Yawn is already gone, poison may tick
        # during the reset skip. Arm the HP floor before that skip runs.
        try:
            self._read_state(track_items=False)
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass
        self._skip_uncontrolled()
        # Pickup Yes/No on the savestate (chemical QS0): accept, then close the
        # leftover ITEM grid. Orphan Triangle first would cancel/strand Yes/No.
        self._auto_accept_pause_pickup_modal()
        self._dismiss_non_box_pause_menu_if_safe()
        self._skip_uncontrolled()
        if self._stage.get("knife_equipped_start"):
            try:
                equip_knife_from_pause_menu(self.bridge)
                self._skip_uncontrolled()
            except (OSError, RuntimeError, ValueError):
                pass
        # Settle macros may have pressed Cross/Triangle; release before obs.
        self._sticky_input.reset()
        self._prev_action = None
        self.bridge.clear_latched_input()
        self._grab_escape_pending = False
        self._forward_collision_stall = False
        self._use_phase = 0
        self._inventory_before_use = None
        self._equip_phase = 0
        self._equip_switch_cooldown = 0
        self._combine_phase = 0
        self._combine_slot_a = None
        self._box_ui_open = False
        self._box_phase = BOX_PHASE_CHOOSE
        self._box_inv_cursor = 0
        self._box_list_cursor = 0
        self._box_inv_trusted_at_cursor = False
        self._episode_failure_override = None
        self._last_attack_obs = empty_last_attack()
        self._last_skip_frames = 0
        self._last_settled_skip_frames = 0
        self._last_settled_cutscene_key = None
        self._last_settled_skip_prev = None
        self._last_settled_skip_new = None
        self._last_settled_skip_kind = None
        self._init_anim_history()

        self._step_count = 0
        self._leg_replay = None
        self._checkpoint_freeze_pending = False
        self._checkpoint_captured = False
        if str(self._stage.get("mode") or "") == "yawn_rails":
            from re1_rl.leg_replay import new_leg_replay_buffer

            self._leg_replay = new_leg_replay_buffer()
            try:
                self.bridge.tape_clear()
                self.bridge.tape_enable(True)
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                pass
        self._frame_stack = []
        self.bridge.frame_ring.clear()
        self.bridge.attack_pins.clear()
        self._progress = ProgressTracker(leg_span=self._leg_span)
        self._visited.reset()
        self._enemy_motion.reset()
        self._player_motion.reset()
        self._box_cache = None
        self._box_departure_snapshot = None
        if getattr(self, "_attack_telemetry", None) is not None:
            self._attack_telemetry.reset_episode()
        if getattr(self, "_step_diag", None) is not None:
            self._step_diag.reset_episode()
        rgb = self.bridge.screenshot()
        if self.bridge.emulated_frame >= 0:
            self.bridge.frame_ring.store_rgb(self.bridge.emulated_frame, rgb)
        frame_obs = self.bridge.build_frame_stack()
        self._prev_hp = 0
        state = self._read_state(track_items=pb_bundle is None)
        if pb_bundle is not None:
            scp = Path(pb_bundle["sidecar_path"])
            sidecar_path = scp if scp.is_absolute() else self.project_root / scp
            if not sidecar_path.is_file():
                raise FileNotFoundError(
                    f"PB sidecar missing (State alone is not enough): {sidecar_path}"
                )
            sidecar = load_sidecar_json(sidecar_path)
            if not bundle_room_matches_sidecar(state.get("room_id"), sidecar):
                print(
                    f"[pb] State/sidecar room mismatch "
                    f"ram={state.get('room_id')!r} "
                    f"captured={sidecar.get('captured_room_id')!r}; "
                    f"reloading fresh init_savestate",
                    flush=True,
                )
                state_path = self.project_root / self._stage["init_savestate"]
                self.bridge.load_savestate(str(state_path))
                self._sticky_input.reset()
                self._prev_action = None
                self.bridge.clear_latched_input()
                self.bridge.frameadvance(1)
                if self._ram_skip.use_engine_patches:
                    self._ram_skip.install_engine_patches()
                self._skip_uncontrolled()
                self.bridge.clear_latched_input()
                self._progress = ProgressTracker(leg_span=self._leg_span)
                self._visited.reset()
                self._box_cache = None
                state = self._read_state(track_items=True)
                self._seed_episode_progress(state)
                self._episode_history.reset(str(state.get("room_id", "")), step=0)
                pb_bundle = None
            else:
                apply_episode_sidecar(self, sidecar, reset_softlock=True)
                state = self._read_state(track_items=True)
                self._seed_episode_hp(state)
                # Yawn pay-forward: each one-leg episode gets fresh dwell
                # history; do not carry captured room_entries forward.
                if str(pb_bundle.get("source") or "") == "yawn_rails":
                    self._episode_history.reset(
                        str(state.get("room_id", "") or ""), step=0
                    )
                rooms = sorted(self._progress.visited_rooms)
                print(
                    f"[pb] reset applied sidecar visited={rooms} "
                    f"bundle_id={sidecar.get('bundle_id')} "
                    f"state={pb_bundle.get('state_path')}",
                    flush=True,
                )
        else:
            self._seed_episode_progress(state)
            self._episode_history.reset(str(state.get("room_id", "")), step=0)
        self._visited.update(state["room_id"], state["x"], state["z"])
        self._prev_state = state
        self._prev_hp = state["hp"]
        skipped = self._planner.skip_spawn_satisfied_room_enters(
            str(state.get("room_id", ""))
        )
        if skipped:
            self._route_start_index += skipped
            print(
                f"[rails] skip {skipped} spawn-satisfied room_enter(s) "
                f"start={self._route_start_index} room={state.get('room_id')!r} "
                f"next={self._planner.next_waypoint_room()!r}",
                flush=True,
            )
        if getattr(self, "_typewriter_save_detector", None) is not None:
            # Sidecar/PB starts hold off save detect until control+ribbons stable.
            self._typewriter_save_detector.begin_episode(
                from_sidecar=pb_bundle is not None,
                state=state,
            )
        self._start_bg_skip()

        obs = self._build_obs(frame_obs, state)
        info = {
            "stage": self._stage.get("stage"),
            "waypoint": self._planner.next_waypoint_room(),
            "route_start_index": self._route_start_index,
            "state": state,
        }
        if pb_bundle is not None:
            info["pb_bundle"] = pb_bundle
        return obs, info

    def _seed_episode_hp(self, state: dict[str, Any]) -> None:
        """HP bookkeeping only (PB restore — sidecar owns progress trackers)."""
        hp = int(state.get("hp", 0))
        self._episode_start_hp = hp if hp > 0 else 0
        self._episode_min_hp = self._episode_start_hp

    def _seed_episode_progress(self, state: dict[str, Any]) -> None:
        """Mark spawn room visited (no ``new_room`` payout on fresh start)."""
        self._seed_episode_hp(state)
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
                    self._cutscene_skip_origin_prev = dict(
                        self._cutscene_skip_entry_prev
                    )
                except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                    self._cutscene_skip_entry_prev = (
                        dict(self._prev_state) if self._prev_state else None
                    )
                    self._cutscene_skip_origin_prev = (
                        dict(self._cutscene_skip_entry_prev)
                        if self._cutscene_skip_entry_prev
                        else None
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
            with self._bg_skip_emu_lock:
                # Capture/macros set _macro_active then take this lock; re-check
                # so we never start a chunk after freeze was requested.
                if self._macro_active or self._bg_skip_stop.is_set():
                    continue
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

        ram_skip = getattr(self, "_ram_skip", None)
        return qualify_cutscene_reward(
            skip_frames=skip_frames,
            prev_state=prev_state,
            new_state=new_state,
            episode_start_hp=int(getattr(self, "_episode_start_hp", 0)),
            rewarded_cutscenes=(
                self._progress.observed_cutscenes
                | self._progress.rewarded_cutscenes
            ),
            visited_rooms=self._progress.visited_rooms,
            cutscene_blocked_after_pickup_room=(
                self._progress.cutscene_blocked_after_pickup_room
            ),
            peak_scene_flag=getattr(ram_skip, "last_skip_peak_scene_flag", None),
            peak_msg_flag=getattr(ram_skip, "last_skip_peak_msg_flag", None),
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

        The first breach marks the terminal observation ledger and ends the
        episode after its -0.05 reward. Returns False when Jill is already dead.
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
            rewarded_cutscenes=(
                self._progress.observed_cutscenes
                | self._progress.rewarded_cutscenes
            ),
            visited_rooms=self._progress.visited_rooms,
        )

    def _illegal_main_hall_failure_reason(
        self,
        prev_state: dict[str, Any] | None,
        state: dict[str, Any] | None,
    ) -> str | None:
        """Return the terminal failure reason for illegal pre-Kenneth 106 entry."""
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

    def _queue_kenneth_gate_failure_if_needed(self) -> None:
        """Queue terminal step when async skip credits an illegal 106 crossing."""
        if getattr(self, "_pending_episode_failure", None):
            return
        if self._progress.kenneth_gate_breached:
            from re1_rl.cutscene_reward import ILLEGAL_MAIN_HALL_FAILURE_REASON

            self._pending_episode_failure = ILLEGAL_MAIN_HALL_FAILURE_REASON

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
            # Kenneth gate: compute_reward applies -0.05 and marks the terminal
            # observation; the outer step terminates after the skip settles.
            save_complete = self._poll_typewriter_save(entry, crossing)
            reward, bd = compute_reward(
                entry,
                crossing,
                self._planner,
                progress=self._progress,
                graph=self.graph,
                success_room=self._stage.get("success_room"),
                rails_mode=self._stage.get("mode") == "yawn_rails",
                typewriter_save_complete=save_complete,
                return_breakdown=True,
            )
            self._after_reward_step(
                entry, crossing, bd, typewriter_save_complete=save_complete
            )
            self._merge_post_skip_breakdown(float(reward), dict(bd))
            self._prev_state = dict(crossing)
            self._queue_kenneth_gate_failure_if_needed()

    def _apply_post_skip_sync(self) -> None:
        """Credit pickups / cutscenes that finished while async skip was running."""
        from re1_rl.story_item_use import annotate_story_use_success

        skip_trap_entry: dict[str, Any] | None = None
        pending_cross = getattr(self, "_pending_skip_room_crossings", None) or []
        if pending_cross:
            skip_trap_entry = dict(pending_cross[0][0])
        else:
            origin = getattr(self, "_cutscene_skip_origin_prev", None)
            if origin is not None:
                skip_trap_entry = dict(origin)
            else:
                snap = getattr(self, "_cutscene_skip_entry_prev", None)
                if snap is not None:
                    skip_trap_entry = dict(snap)
                elif self._prev_state:
                    skip_trap_entry = dict(self._prev_state)

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
            from re1_rl.weapon_equip import policy_inventory, policy_inventory_to_names

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
            entry_prev["inventory"] = policy_inventory_to_names(inv_before)
            if skip_trap_entry is not None:
                skip_trap_entry["inventory"] = policy_inventory_to_names(inv_before)
        if inv_after is not None:
            state["inventory"] = policy_inventory_to_names(inv_after)
        from re1_rl.barry_rescue_checkpoint import note_barry_rescue_skip_settle
        from re1_rl.richard_cutscene_checkpoint import (
            note_richard_cutscene_skip_settle,
        )

        skip_entry = skip_trap_entry or entry_prev
        skip_frames = int(getattr(self, "_skip_session_frames", 0) or 0)
        note_barry_rescue_skip_settle(
            self._planner,
            self._progress,
            skip_entry,
            state,
            skip_frames=skip_frames,
        )
        note_richard_cutscene_skip_settle(
            self._planner,
            self._progress,
            skip_entry,
            state,
            skip_frames=skip_frames,
        )
        # Reward qualification is duration-based with explicit exclusions (menu,
        # pickup, death, opening, pre-Kenneth hall, message-box text). Door
        # crossings keep their new_room credit and contribute to skip duration.
        state["cutscene_key"] = self._qualify_cutscene_reward(
            int(getattr(self, "_skip_session_frames", 0)),
            entry_prev,
            state,
        )
        state["cutscene_paired_new_room"] = (
            float((getattr(self, "_post_skip_bd", {}) or {}).get("new_room", 0.0))
            > 0.0
        )
        save_complete = self._poll_typewriter_save(entry_prev or {}, state)
        reward, bd = compute_reward(
            entry_prev,
            state,
            self._planner,
            progress=self._progress,
            graph=self.graph,
            success_room=self._stage.get("success_room"),
            rails_mode=self._stage.get("mode") == "yawn_rails",
            typewriter_save_complete=save_complete,
            return_breakdown=True,
        )
        self._after_reward_step(
            entry_prev or {}, state, bd, typewriter_save_complete=save_complete
        )
        self._merge_post_skip_breakdown(float(reward), dict(bd))
        self._prev_state = state
        self._queue_kenneth_gate_failure_if_needed()
        self._cutscene_skip_entry_prev = None
        self._cutscene_skip_origin_prev = None
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

    def _apply_yawn_poison_hp_floor(self, state: dict[str, Any]) -> None:
        """Arm Lua/Python HP floor after attic Yawn leaves combat."""
        from re1_rl.yawn_outcome import (
            YAWN_POISON_HP_FLOOR,
            yawn_poison_hp_floor_active,
            yawn_retreat_detected,
            yawn_should_latch_retreat,
        )

        progress = getattr(self, "_progress", None)
        if progress is not None:
            if yawn_retreat_detected(
                state,
                getattr(self, "_prev_state", None),
                enemies=state.get("enemies"),
            ) or yawn_should_latch_retreat(state):
                progress.note_yawn_retreat()
        retreated = bool(getattr(progress, "yawn_retreated", False)) if progress else False
        active = yawn_poison_hp_floor_active(state, yawn_retreated=retreated)
        self.bridge.hp_floor = YAWN_POISON_HP_FLOOR if active else 0

    def _revive_zero_hp_under_yawn_floor(self, hp: int) -> int:
        """Write the post-retreat floor if poison just hit 0. Returns HP after."""
        from re1_rl.yawn_outcome import YAWN_POISON_CHIP_MAX

        floor = int(getattr(self.bridge, "hp_floor", 0) or 0)
        prev = int(getattr(self, "_prev_hp", 0) or 0)
        if int(hp) > 0 or floor <= 0:
            return int(hp)
        if not (0 < prev <= YAWN_POISON_CHIP_MAX):
            return int(hp)
        try:
            self.bridge.write_ram([("player_hp", PLAYER_HP, "u16", floor)])
        except (OSError, RuntimeError, ValueError):
            return int(hp)
        return floor

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
        if player_died(hp, prev_hp=self._prev_hp, episode_start_hp=start_hp):
            # Refresh floor from live enemies: retreat may have settled in skip.
            try:
                self._read_state(track_items=False)
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                pass
            hp = self._revive_zero_hp_under_yawn_floor(hp)
        if not player_died(hp, prev_hp=self._prev_hp, episode_start_hp=start_hp):
            return False
        try:
            hp_ram2 = self.bridge.read_ram([("player_hp", PLAYER_HP, "u16")])
            hp2 = int(hp_ram2.get("player_hp", 0))
        except (OSError, RuntimeError, ValueError):
            return True
        hp2 = self._revive_zero_hp_under_yawn_floor(hp2)
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

    def _record_leg_replay_step(self, action: int, emu_frames: int) -> None:
        buf = getattr(self, "_leg_replay", None)
        if buf is None:
            return
        try:
            buf.append(int(action), max(0, int(emu_frames)))
        except (TypeError, ValueError, OverflowError):
            return

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
        self._record_leg_replay_step(action, 0)
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
        hp = int(ram.get("player_hp", 0))
        if hp <= 0:
            try:
                self._read_state(track_items=False)
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                pass
            revived = self._revive_zero_hp_under_yawn_floor(hp)
            if revived != hp:
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
        self._record_leg_replay_step(action, int(self.frame_skip))
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
        self._update_loadout_segment(
            self._prev_state,
            state,
            breakdown,
            terminated=True,
            truncated=False,
        )
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
        loadout_sample = self._progress.pop_loadout_sample()
        if loadout_sample is not None:
            info["logistics_sample"] = loadout_sample
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

    def _recover_options_menu(
        self, action: int
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]] | None:
        """Dismiss OPTIONS/CONFIG; never end the episode for that trap.

        Returns a soft non-terminal step if still trapped after retries, else
        ``None`` so the caller can continue the normal step path.
        """
        report: dict[str, Any] = {}
        recovered = False
        for _attempt in range(2):
            recovered, report = self._try_dismiss_options_menu()
            if recovered:
                break
        menu_reason = self._probe_outside_gameplay()
        if menu_reason not in _OPTIONS_MENU_REASONS:
            return None
        port = getattr(self.bridge, "port", "?")
        print(
            f"[options_dismiss_persist] port={port} action={action} "
            f"recovered={recovered} report={report}",
            flush=True,
        )
        return self._options_menu_soft_continue(action, report=report)

    def _options_menu_soft_continue(
        self,
        action: int,
        *,
        report: dict[str, Any],
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        """Stay in-episode after a failed OPTIONS dismiss (0 reward, not done)."""
        self._skipping_flag = False
        self._sticky_input.reset()
        self._step_count += 1
        self._record_leg_replay_step(int(action), int(self.frame_skip))
        try:
            frame_obs = self._capture_step_obs()
            state = self._read_state(track_items=False)
        except (OSError, RuntimeError, ValueError):
            state = dict(self._prev_state)
            frame_obs = self.bridge.build_frame_stack()
            if frame_obs.shape != FRAME_SHAPE:
                frame_obs = np.zeros(FRAME_SHAPE, dtype=np.uint8)
        obs = self._build_obs(frame_obs, state)
        info: dict[str, Any] = {
            "room_id": state.get("room_id"),
            "episode_failure": None,
            "options_dismiss_persist": True,
            "options_dismiss_report": report,
            "visited_rooms": sorted(self._progress.visited_rooms),
            "n_rooms_visited": len(self._progress.visited_rooms),
        }
        return obs, 0.0, False, False, info

    def _inventory_macro_owns_item_menu(self, action: int) -> bool:
        """True while equip/use/combine/box UI (or any bridge macro) owns the ITEM screen."""
        if bool(getattr(self, "_macro_active", False)):
            return True
        if bool(getattr(self, "_box_ui_open", False)):
            return True
        if int(getattr(self, "_use_phase", 0)) > 0:
            return True
        if int(getattr(self, "_equip_phase", 0)) > 0:
            return True
        if int(getattr(self, "_combine_phase", 0)) > 0:
            return True
        a = int(action)
        return         a in (
            USE_ACTION,
            EQUIP_ACTION,
            COMBINE_ACTION,
            BOX_WITHDRAW_ACTION,
            BOX_DEPOSIT_ACTION,
            BOX_CLOSE_ACTION,
            BOX_BANK_BOSS_ACTION,
        ) or (
            WITHDRAW_ACTION_BASE <= a < WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS
        )

    def _current_room_is_box_room(self) -> bool:
        from re1_rl.item_box import is_box_room

        room = str((getattr(self, "_prev_state", {}) or {}).get("room_id", "") or "")
        if not room:
            try:
                state = self._read_state()
                room = str(state.get("room_id", "") or "")
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                return False
        return is_box_room(room)

    def _sync_box_ui_session_from_ram(self) -> None:
        """Enter/leave the box-UI policy session from live pause-tree RAM."""
        from re1_rl.item_box_ui_macro import GRID_READY_FRAMES, probe_box_ui_open

        try:
            open_now = probe_box_ui_open(self.bridge)
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            open_now = False
        if open_now and self._current_room_is_box_room():
            if not self._box_ui_open:
                self._box_ui_open = True
                self._box_phase = BOX_PHASE_CHOOSE
                # After open animation the cursor homes on inventory slot 0;
                # box list resumes at slot 0 on first Cross-in.
                self._box_inv_cursor = 0
                self._box_list_cursor = 0
                # Mask-path rising edge used to skip settle; policy then
                # D-padded during the open animation (CLIP then beretta).
                try:
                    self.bridge.step(
                        buttons={},
                        n=int(GRID_READY_FRAMES),
                        abort_on_zero_hp=False,
                    )
                except (
                    OSError,
                    RuntimeError,
                    ValueError,
                    AttributeError,
                    TypeError,
                ):
                    pass
            return
        if self._box_ui_open and not open_now:
            self._box_ui_open = False
            self._box_phase = BOX_PHASE_CHOOSE
            self._box_inv_cursor = 0
            self._box_list_cursor = 0

    def _probe_item_inventory_menu(self) -> bool:
        from re1_rl.ram_skip import item_inventory_screen_from_ram

        try:
            return item_inventory_screen_from_ram(self._skip_poll_ram())
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            return False

    def _auto_accept_pause_pickup_modal(self) -> bool:
        """Accept inventory Yes/No ("Will you take …?") with Cross.

        Env-owned so pickups do not depend on PPO sampling noop. Excludes the
        real item-box UI (``gs`` mid-byte ``0x90``). Returns True if Cross was
        sent.
        """
        from re1_rl.item_box_ui_macro import probe_box_ui_open
        from re1_rl.ram_skip import (
            document_examine_ui_from_ram,
            pause_menu_modal_from_ram,
        )
        from re1_rl.sticky_input import empty_sticky

        try:
            ram = self._skip_poll_ram()
            if not pause_menu_modal_from_ram(ram):
                return False
            # Books/files (gs=0x40808100): Triangle closes; Cross flips pages.
            if document_examine_ui_from_ram(ram):
                return False
            if probe_box_ui_open(self.bridge):
                return False
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            return False

        self._sticky_input.reset()
        hold_n = max(int(self.frame_skip), 8)
        try:
            self.bridge.step(
                sticky=empty_sticky(),
                pulse_hold={"cross": True},
                n=hold_n,
                abort_on_zero_hp=False,
                ring_stride=0,
                capture_final=False,
            )
            # Brief settle so msg_flag / inventory update before orphan dismiss.
            self.bridge.step(
                buttons={},
                n=24,
                abort_on_zero_hp=False,
                ring_stride=0,
                capture_final=False,
            )
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            return False
        finally:
            self._sticky_input.reset()
        return True

    def _dismiss_non_box_pause_menu_if_safe(self) -> bool:
        """Triangle-close leftover ITEM/document pause; never touch box or Yes/No."""
        from re1_rl.item_box_ui_macro import probe_box_ui_open
        from re1_rl.ram_skip import (
            document_examine_ui_from_ram,
            pause_menu_modal_from_ram,
        )

        if not self._probe_item_inventory_menu():
            return False
        try:
            if probe_box_ui_open(self.bridge):
                return False
            ram = self._skip_poll_ram()
            # Pickup Yes/No: Triangle cancels. Document examine may share
            # msg_flag — still Triangle-dismiss (books are not Yes/No).
            if pause_menu_modal_from_ram(ram) and not document_examine_ui_from_ram(
                ram
            ):
                return False
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            return False
        recovered, _report = self._try_dismiss_orphan_item_menu()
        return bool(recovered)

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
        """Legacy magic box RAM path — kept but always inactive in the env."""
        from re1_rl.item_box import MAGIC_BOX_RAM_WRITES_ENABLED

        if not MAGIC_BOX_RAM_WRITES_ENABLED:
            return False
        return DEPOSIT_ACTION_BASE <= action < (
            WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS
        )

    def _is_box_ui_action(self, action: int) -> bool:
        if not bool(getattr(self, "_box_ui_open", False)):
            return False
        a = int(action)
        if a in (
            BOX_WITHDRAW_ACTION,
            BOX_DEPOSIT_ACTION,
            BOX_CLOSE_ACTION,
            BOX_BANK_BOSS_ACTION,
        ):
            return True
        if WITHDRAW_ACTION_BASE <= a < WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS:
            return True
        if SELECT_SLOT_BASE <= a < SELECT_SLOT_BASE + N_SELECT_SLOT:
            return int(getattr(self, "_box_phase", 0)) == BOX_PHASE_DEPOSIT_SLOT
        return False

    def _handle_box_ui_action(
        self, action: int
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]] | None:
        """Open-box PPO: withdraw/close via authentic UI (no inv/box RAM writes).

        Policy picks the source box slot; macro selects an empty inv slot then
        the box entry (Cross/Cross). Deposit stays policy-gated off. Close uses
        EXIT (Cross) with Triangle fallback.
        """
        from re1_rl.herb_combine import combine_slot_from_action
        from re1_rl.item_box import BOX_DEPOSIT_POLICY_ENABLED
        from re1_rl.item_box_ui_macro import (
            close_box_ui,
            execute_box_deposit_ui,
            execute_box_withdraw_ui,
            probe_box_ui_open,
        )

        if not self._box_ui_open:
            return None
        a = int(action)
        prev_hp = int(getattr(self, "_prev_hp", 0) or 0)
        episode_start_hp = int(getattr(self, "_episode_start_hp", 0) or 0)
        phase = int(getattr(self, "_box_phase", BOX_PHASE_CHOOSE))
        inv_cursor = int(getattr(self, "_box_inv_cursor", 0) or 0)
        box_cursor = int(getattr(self, "_box_list_cursor", 0) or 0)

        if a == BOX_CLOSE_ACTION:
            self._sticky_input.reset()
            self._macro_active = True
            try:
                died, frames, report = close_box_ui(
                    self.bridge,
                    prev_hp=prev_hp,
                    episode_start_hp=episode_start_hp,
                    inv_cursor=inv_cursor,
                )
            finally:
                self._macro_active = False
                self._sticky_input.reset()
            self._box_ui_open = False
            self._box_phase = BOX_PHASE_CHOOSE
            self._box_inv_cursor = 0
            self._box_list_cursor = 0
            self._box_cache = None
            pollution = self._box_pollution_failure()
            if pollution:
                report = {
                    **report,
                    "ok": False,
                    "reason": pollution,
                    "box_pollution": pollution,
                }
                self._episode_failure_override = pollution
            return self._submenu_step(
                a,
                step_emulated_frames=max(frames, self.frame_skip),
                magic_report=report,
                died=bool(died),
            )

        if phase == BOX_PHASE_CHOOSE:
            if a == BOX_WITHDRAW_ACTION:
                self._box_phase = BOX_PHASE_WITHDRAW_SLOT
                return self._submenu_step(
                    a,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": True, "reason": "box_withdraw_open"},
                )
            if a == BOX_DEPOSIT_ACTION:
                from re1_rl.item_box import BOX_DEPOSIT_ROOMS

                if not BOX_DEPOSIT_POLICY_ENABLED:
                    return self._submenu_step(
                        a,
                        step_emulated_frames=self.frame_skip,
                        magic_report={"ok": False, "reason": "deposit_disabled"},
                    )
                room_now = str(
                    (getattr(self, "_prev_state", {}) or {}).get("room_id", "") or ""
                )
                if room_now and room_now not in BOX_DEPOSIT_ROOMS:
                    return self._submenu_step(
                        a,
                        step_emulated_frames=self.frame_skip,
                        magic_report={"ok": False, "reason": "deposit_room_blocked"},
                    )
                self._box_phase = BOX_PHASE_DEPOSIT_SLOT
                self._box_inv_trusted_at_cursor = False
                return self._submenu_step(
                    a,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": True, "reason": "box_deposit_open"},
                )
            if a == BOX_BANK_BOSS_ACTION:
                from re1_rl.boss_prep_macro import execute_room100_boss_bank_ui

                self._sticky_input.reset()
                self._macro_active = True
                try:
                    room_id = None
                    prev_st = getattr(self, "_prev_state", None) or {}
                    if isinstance(prev_st, dict):
                        room_id = prev_st.get("room_id")
                    died, frames, report = execute_room100_boss_bank_ui(
                        self.bridge,
                        prev_hp=prev_hp,
                        episode_start_hp=episode_start_hp,
                        inv_cursor=inv_cursor,
                        box_cursor=box_cursor,
                        room_id=str(room_id) if room_id is not None else None,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    died, frames = False, 0
                    report = {
                        "ok": False,
                        "reason": f"error:{exc}",
                        "action": "boss_bank_room100",
                    }
                finally:
                    self._macro_active = False
                    self._sticky_input.reset()
                if report.get("ok"):
                    report = {**report, "box_transfer": "boss_bank"}
                self._apply_box_ui_cursors_from_report(
                    report,
                    inv_cursor_in=inv_cursor,
                    box_cursor_in=box_cursor,
                )
                self._box_phase = BOX_PHASE_CHOOSE
                self._box_cache = None
                try:
                    self._box_ui_open = probe_box_ui_open(self.bridge)
                except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                    self._box_ui_open = True
                pollution = self._box_pollution_failure()
                if pollution:
                    report = {
                        **report,
                        "ok": False,
                        "reason": pollution,
                        "box_pollution": pollution,
                        "exchange_detected": True,
                    }
                    self._log_box_transfer_report(
                        report,
                        inv_cursor_in=inv_cursor,
                        box_cursor_in=box_cursor,
                    )
                    self._episode_failure_override = pollution
                return self._submenu_step(
                    a,
                    step_emulated_frames=max(int(frames), self.frame_skip),
                    magic_report=report,
                    died=bool(died),
                )
            return self._submenu_step(
                a,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": False, "reason": "box_phase_choose_expected"},
            )

        if phase == BOX_PHASE_WITHDRAW_SLOT:
            if not (WITHDRAW_ACTION_BASE <= a < WITHDRAW_ACTION_BASE + N_WITHDRAW_ACTIONS):
                return self._submenu_step(
                    a,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "box_withdraw_slot_expected"},
                )
            box_slot = a - WITHDRAW_ACTION_BASE
            self._sticky_input.reset()
            self._macro_active = True
            try:
                died, frames, report = execute_box_withdraw_ui(
                    self.bridge,
                    box_slot,
                    prev_hp=prev_hp,
                    episode_start_hp=episode_start_hp,
                    inv_cursor=inv_cursor,
                    box_cursor=box_cursor,
                    room_id=str(
                        (getattr(self, "_prev_state", {}) or {}).get("room_id", "")
                        or ""
                    )
                    or None,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                died, frames = False, 0
                report = {"ok": False, "reason": f"error:{exc}", "moved": None}
            finally:
                self._macro_active = False
                self._sticky_input.reset()
            if report.get("ok") and report.get("moved") is not None:
                report = {**report, "box_transfer": "withdraw"}
            self._apply_box_ui_cursors_from_report(
                report,
                inv_cursor_in=inv_cursor,
                box_cursor_in=box_cursor,
            )
            self._box_phase = BOX_PHASE_CHOOSE
            self._box_cache = None
            try:
                self._box_ui_open = probe_box_ui_open(self.bridge)
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                self._box_ui_open = True
            pollution = self._box_pollution_failure()
            if pollution:
                report = {
                    **report,
                    "ok": False,
                    "reason": pollution,
                    "box_pollution": pollution,
                    "exchange_detected": True,
                }
                self._log_box_transfer_report(
                    report,
                    inv_cursor_in=inv_cursor,
                    box_cursor_in=box_cursor,
                )
                self._episode_failure_override = pollution
            return self._submenu_step(
                a,
                step_emulated_frames=max(int(frames), self.frame_skip),
                magic_report=report,
                died=bool(died),
            )

        if phase == BOX_PHASE_DEPOSIT_SLOT:
            if not BOX_DEPOSIT_POLICY_ENABLED:
                self._box_phase = BOX_PHASE_CHOOSE
                return self._submenu_step(
                    a,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "deposit_disabled"},
                )
            slot = combine_slot_from_action(a, select_slot_base=SELECT_SLOT_BASE)
            if slot is None:
                return self._submenu_step(
                    a,
                    step_emulated_frames=self.frame_skip,
                    magic_report={"ok": False, "reason": "box_deposit_slot_expected"},
                )
            # Deposit uses UI macros only (never apply_deposit RAM writes).
            self._sticky_input.reset()
            self._macro_active = True
            try:
                room_id = None
                prev_st = getattr(self, "_prev_state", None) or {}
                if isinstance(prev_st, dict):
                    room_id = prev_st.get("room_id")
                from re1_rl.item_box import read_inventory

                inv_before = read_inventory(self.bridge)
                expected_id = (
                    int(inv_before[int(slot)][0])
                    if 0 <= int(slot) < len(inv_before)
                    else None
                )
                died, frames, report = execute_box_deposit_ui(
                    self.bridge,
                    int(slot),
                    prev_hp=prev_hp,
                    episode_start_hp=episode_start_hp,
                    inv_cursor=inv_cursor,
                    box_cursor=box_cursor,
                    room_id=str(room_id) if room_id is not None else None,
                    trust_inv_cursor=False,
                    expected_item_id=expected_id,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                died, frames = False, 0
                report = {"ok": False, "reason": f"error:{exc}", "moved": None}
            finally:
                self._macro_active = False
                self._sticky_input.reset()
            if report.get("ok") and report.get("moved") is not None:
                report = {**report, "box_transfer": "deposit"}
            self._apply_box_ui_cursors_from_report(
                report,
                inv_cursor_in=inv_cursor,
                box_cursor_in=box_cursor,
            )
            self._box_phase = BOX_PHASE_CHOOSE
            self._box_cache = None
            try:
                self._box_ui_open = probe_box_ui_open(self.bridge)
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                self._box_ui_open = True
            pollution = self._box_pollution_failure()
            if pollution:
                report = {
                    **report,
                    "ok": False,
                    "reason": pollution,
                    "box_pollution": pollution,
                    "exchange_detected": True,
                }
                self._log_box_transfer_report(
                    report,
                    inv_cursor_in=inv_cursor,
                    box_cursor_in=box_cursor,
                )
                self._episode_failure_override = pollution
            return self._submenu_step(
                a,
                step_emulated_frames=max(int(frames), self.frame_skip),
                magic_report=report,
                died=bool(died),
            )

        return None

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
                if not magic_report.get("ok"):
                    recovered, dismiss_report = self._try_dismiss_orphan_item_menu()
                    magic_report = dict(magic_report)
                    magic_report["menu_dismiss"] = dismiss_report
                    magic_report["menu_recovered"] = bool(recovered)
                elif magic_report.get("reason") == "equip_ok":
                    # Block reopening EQUIP — short cooldowns are invisible next
                    # to ~300f ITEM macros; keep this long enough to watch.
                    raw_cd = os.environ.get("RE1_EQUIP_SWITCH_COOLDOWN_STEPS", "64")
                    try:
                        cd_steps = max(0, int(raw_cd))
                    except ValueError:
                        cd_steps = 64
                    self._equip_switch_cooldown = cd_steps
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
                if not magic_report.get("ok"):
                    recovered, dismiss_report = self._try_dismiss_orphan_item_menu()
                    magic_report = dict(magic_report)
                    magic_report["menu_dismiss_env"] = dismiss_report
                    magic_report["menu_recovered"] = bool(recovered)
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
        self._record_leg_replay_step(action, int(step_emulated_frames))
        macro_pins = self._refresh_anim_history_before_obs()
        frame_obs = self._capture_step_obs()
        if macro_pins:
            self.bridge.attack_pins.clear()
        state = self._read_state()
        state = dict(state)
        state["step_emulated_frames"] = int(step_emulated_frames)
        state["reference_step_frames"] = self.frame_skip
        report_pre = magic_report or {}
        if (
            report_pre.get("ok")
            and report_pre.get("box_transfer") == "withdraw"
            and report_pre.get("moved") is not None
        ):
            state["box_withdraw_success"] = True
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
        save_complete = self._poll_typewriter_save(self._prev_state, state)
        reward, breakdown = compute_reward(
            self._prev_state,
            state,
            self._planner,
            progress=self._progress,
            graph=self.graph,
            success_room=self._stage.get("success_room"),
            rails_mode=self._stage.get("mode") == "yawn_rails",
            typewriter_save_complete=save_complete,
            return_breakdown=True,
        )
        self._after_reward_step(
            self._prev_state,
            state,
            breakdown,
            typewriter_save_complete=save_complete,
        )
        from re1_rl.reward import REWARD_SCALE

        reward = sum(breakdown.values()) * REWARD_SCALE
        terminated, truncated, episode_failure = self._termination_flags(state)
        self._update_loadout_segment(
            self._prev_state,
            state,
            breakdown,
            terminated=terminated,
            truncated=truncated,
        )
        obs = self._build_obs(frame_obs, state)
        info = {
            "room_id": state["room_id"],
            "hp": state["hp"],
            "bridge_port": getattr(self.bridge, "port", None),
            "action_name": ACTION_NAMES[int(action)],
            "reward_breakdown": breakdown,
            "episode_failure": episode_failure,
            "magic_report": magic_report,
            "use_phase": int(self._use_phase),
            "equip_phase": int(self._equip_phase),
            "equip_switch_cooldown": int(
                getattr(self, "_equip_switch_cooldown", 0)
            ),
            "equipped_slot_0based": state.get("equipped_slot_0based"),
            "combine_phase": int(self._combine_phase),
            "combine_slot_a": self._combine_slot_a,
            "inventory": state["inventory_slots"],
            "state": state,
        }
        pending_ge = getattr(self, "_go_explore_capture_pending", None) or []
        if pending_ge:
            info["go_explore_capture"] = list(pending_ge)
            self._go_explore_capture_pending = []
        pending_yr = getattr(self, "_yawn_rails_capture_pending", None) or []
        if pending_yr:
            info["yawn_rails_capture"] = list(pending_yr)
            self._yawn_rails_capture_pending = []
        loadout_sample = self._progress.pop_loadout_sample()
        if loadout_sample is not None:
            info["logistics_sample"] = loadout_sample
        self._prev_state = state
        if state["hp"] > 0:
            self._prev_hp = state["hp"]
        return obs, reward, terminated, truncated, info

    def _apply_magic_action(self, action: int) -> dict[str, Any]:
        """Legacy closed-box RAM transfers — kept unused (see MAGIC_BOX_RAM_WRITES)."""
        from re1_rl.attack_macro import read_equipped_weapon
        from re1_rl.item_box import (
            MAGIC_BOX_RAM_WRITES_ENABLED,
            apply_deposit,
            apply_withdraw,
        )

        if not MAGIC_BOX_RAM_WRITES_ENABLED:
            return {"ok": False, "reason": "magic_box_ram_writes_disabled"}
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
        document_examine_open = False
        if bridge is not None:
            try:
                from re1_rl.memory_map import GAME_MODE, GAME_STATE
                from re1_rl.ram_skip import document_examine_ui_from_ram

                doc_ram = bridge.read_ram(
                    [
                        ("game_mode", GAME_MODE, "u8"),
                        ("game_state", GAME_STATE, "u32"),
                    ]
                )
                document_examine_open = document_examine_ui_from_ram(doc_ram)
            except (OSError, RuntimeError, AttributeError, TypeError, ValueError):
                document_examine_open = False
        enemies = pose.get("enemies")
        room_for_mask = str(pose.get("room_id", "") or "") or None
        if MASK_ATTACK_PASSIVE_CROWS:
            knife_near = paid_combat_enemy_count(
                enemies,
                knife=True,
                room_id=room_for_mask,
                for_attack_mask=True,
            )
            gun_near = paid_combat_enemy_count(
                enemies, room_id=room_for_mask, for_attack_mask=True
            )
        else:
            knife_near = combat_enemy_count(enemies, knife=True, for_attack_mask=True)
            gun_near = combat_enemy_count(enemies, for_attack_mask=True)
        # Refresh before masking so pickup Yes/No in room 118 cannot keep a
        # stale box-UI session (would hide noop→Cross).
        if bridge is not None and (
            bool(getattr(self, "_box_ui_open", False))
            or self._current_room_is_box_room()
        ):
            try:
                self._sync_box_ui_session_from_ram()
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                pass
        box_ui_open = bool(getattr(self, "_box_ui_open", False))
        # Box UI clears in_control; still refresh box contents for the mask.
        if box_ui_open and bridge is not None and box is None:
            try:
                from re1_rl.item_box import read_box, read_inventory
                from re1_rl.weapon_equip import policy_inventory

                inventory = policy_inventory(read_inventory(bridge))
                box = read_box(bridge)
            except (OSError, RuntimeError, AttributeError, TypeError, ValueError):
                pass
        mask = build_action_mask(
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
            box_ui_open=box_ui_open,
            box_phase=int(getattr(self, "_box_phase", BOX_PHASE_CHOOSE)),
            equip_phase=int(getattr(self, "_equip_phase", 0)),
            use_phase=int(getattr(self, "_use_phase", 0)),
            combine_phase=int(getattr(self, "_combine_phase", 0)),
            combine_slot_a=getattr(self, "_combine_slot_a", None),
            current_hp=int(pose.get("hp", 0)),
            poisoned=bool(pose.get("poisoned", False)),
            episode_start_hp=int(getattr(self, "_episode_start_hp", 0) or 0),
            in_control=bool(pose.get("in_control", True)),
            grab_escape_pending=bool(
                getattr(self, "_grab_escape_pending", False)
            ),
            alive_enemies_in_room=combat_enemy_count(
                enemies, for_attack_mask=True
            ),
            knife_enemies_near=knife_near,
            gun_enemies_near=gun_near,
            mask_combat_without_enemies=MASK_ATTACK_WITHOUT_ENEMIES,
            room_id=str(pose.get("room_id", "") or "") or None,
            player_x=pose.get("x"),
            player_z=pose.get("z"),
            rewarded_story_uses=rewarded_story_uses,
            document_examine_open=document_examine_open,
            equip_switch_cooldown=int(
                getattr(self, "_equip_switch_cooldown", 0)
            ),
            box_inv_cursor=int(getattr(self, "_box_inv_cursor", 0) or 0),
        )
        if (
            box_ui_open
            and int(getattr(self, "_box_phase", BOX_PHASE_CHOOSE))
            == BOX_PHASE_WITHDRAW_SLOT
            and inventory is not None
            and box is not None
            and getattr(self, "_stage", {}).get("mode") == "yawn_rails"
            and getattr(self, "_planner", None) is not None
        ):
            from re1_rl.yawn_rails import apply_logistics_feasibility_mask

            apply_logistics_feasibility_mask(
                mask, inventory, box, self._planner
            )
        return mask

    def _execution_action_legal(self, action: int) -> bool:
        """Re-read the live mask immediately before irreversible macro dispatch."""
        try:
            mask = self.action_masks()
            return 0 <= int(action) < len(mask) and bool(mask[int(action)])
        except (OSError, RuntimeError, AttributeError, TypeError, ValueError):
            return False

    def step(self, action: int):
        action = int(action)
        # Capture the same mask the agent sees for this decision (pre-step state).
        pre_masks = None
        equip_cd_pre = int(getattr(self, "_equip_switch_cooldown", 0))
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
                    equip_cd_pre=equip_cd_pre,
                )
            return result
        finally:
            self._prev_action = action

    def _step_once(self, action: int):
        assert self._planner is not None
        # One-step TTL: clear prior last_attack before this step's observation.
        self._last_attack_obs = empty_last_attack()
        # Consume equip holdout after the masked decision step has begun.
        # action_masks() for this step already saw the pre-tick cooldown.
        if int(getattr(self, "_equip_switch_cooldown", 0)) > 0:
            self._equip_switch_cooldown -= 1
        freeze = self._try_decision_checkpoint_capture(action)
        if freeze is not None:
            return freeze
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
        # Auto-accept pickup Yes/No, then close leftover ITEM (not real box UI).
        if self._auto_accept_pause_pickup_modal():
            action = 0
            if self._dismiss_non_box_pause_menu_if_safe():
                self._skipping_flag = False
        menu_reason = self._probe_outside_gameplay()
        if menu_reason in _OPTIONS_MENU_REASONS:
            options_step = self._recover_options_menu(action)
            if options_step is not None:
                return options_step
            menu_reason = self._probe_outside_gameplay()
        # Item-box UI: only gs mid-byte 0x90 (probe_box_ui_open). Pickup Yes/No
        # and START/ITEM also sit in the pause tree in box rooms (118).
        if bool(getattr(self, "_box_ui_open", False)):
            self._sync_box_ui_session_from_ram()
        elif not self._inventory_macro_owns_item_menu(int(action)):
            if self._probe_item_inventory_menu():
                from re1_rl.item_box_ui_macro import probe_box_ui_open
                from re1_rl.ram_skip import pause_menu_modal_from_ram

                real_box = False
                pickup_modal = False
                try:
                    real_box = bool(probe_box_ui_open(self.bridge))
                except (
                    OSError,
                    RuntimeError,
                    ValueError,
                    AttributeError,
                    TypeError,
                ):
                    real_box = False
                try:
                    pickup_modal = pause_menu_modal_from_ram(self._skip_poll_ram())
                except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                    pickup_modal = False
                if real_box and self._current_room_is_box_room():
                    if not self._box_ui_open:
                        self._sync_box_ui_session_from_ram()
                    self._skipping_flag = False
                elif pickup_modal:
                    self._auto_accept_pause_pickup_modal()
                    # Close leftover ITEM after Yes; safe even in room 118.
                    if self._dismiss_non_box_pause_menu_if_safe():
                        self._skipping_flag = False
                elif not self._current_room_is_box_room():
                    # Keep box-room protection for open animation / non-modal ITEM.
                    if self._dismiss_non_box_pause_menu_if_safe():
                        self._skipping_flag = False
                        menu_reason = self._probe_outside_gameplay()
        if menu_reason in _OPTIONS_MENU_REASONS:
            options_step = self._recover_options_menu(action)
            if options_step is not None:
                return options_step
            menu_reason = self._probe_outside_gameplay()
        if menu_reason in _DEATH_FAILURE_REASONS:
            death = self._death_step(
                action, died_during_skip=False, died_during_step=True
            )
            if death is not None:
                return death
            menu_reason = None
        if menu_reason in _OPTIONS_MENU_REASONS:
            # Belt-and-suspenders: OPTIONS must never hard-reset the episode.
            options_step = self._recover_options_menu(action)
            if options_step is not None:
                return options_step
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

        if bool(getattr(self, "_box_ui_open", False)) or self._is_box_ui_action(
            int(action)
        ):
            box_step = self._handle_box_ui_action(int(action))
            if box_step is not None:
                return box_step

        attack = int(action) == ATTACK_ACTION
        attack_up = int(action) == ATTACK_UP_ACTION
        attack_down = int(action) == ATTACK_DOWN_ACTION
        combat_attack = attack or attack_up or attack_down
        grab_escape = bool(getattr(self, "_grab_escape_pending", False))
        magic = self._is_magic_action(int(action))
        attack_report: dict[str, Any] | None = None
        magic_report: dict[str, Any] | None = None
        step_emulated_frames = self.frame_skip
        if grab_escape:
            from re1_rl.grab_escape import execute_grab_escape_noop

            self._grab_escape_pending = False
            self._sticky_input.reset()
            self._macro_active = True
            try:
                died_during_step, step_emulated_frames = (
                    execute_grab_escape_noop(self.bridge)
                )
            finally:
                self._macro_active = False
        elif combat_attack:
            self._sticky_input.apply(0, ACTION_BUTTON_MAP)
            execution_legal = self._execution_action_legal(int(action))
            if not execution_legal:
                attack_report = {
                    "outcome": "illegal_attack",
                    "weapon_id": self._prev_state.get("equipped_weapon_id"),
                    "ammo_spent": 0,
                    "frames": self.frame_skip,
                }
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
                self._macro_active = True
                try:
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
                            knife_phases=self.knife_phases,
                            knife_scale=self.knife_scale,
                            knife_echo_joypad=self.knife_echo_joypad,
                            knife_use_ram_gates=self.knife_use_ram_gates,
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
                INTERACT_HOLD_EXTRA_FRAMES,
            )

            pause_modal = False
            if int(action) == 0:
                try:
                    from re1_rl.ram_skip import pause_menu_modal_from_ram

                    pause_modal = pause_menu_modal_from_ram(self._skip_poll_ram())
                except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                    pause_modal = False
            sticky, pulse, pulse_hold = _apply_action_input(
                self._sticky_input,
                int(action),
                button_map=button_map_for_action(
                    int(action), pause_menu_modal=pause_modal
                ),
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
        self._record_leg_replay_step(action, int(step_emulated_frames))
        macro_pins = self._refresh_anim_history_before_obs()
        frame_obs = self.bridge.build_frame_stack()
        if macro_pins:
            self.bridge.attack_pins.clear()
        state = self._read_state()
        if died_during_skip or died_during_step:
            state = dict(state)
            state["dead"] = True
        knife_weapon = False
        if combat_attack:
            wid = int(
                (attack_report or {}).get("weapon_id")
                or state.get("equipped_weapon_id")
                or self._prev_state.get("equipped_weapon_id")
                or 0
            )
            knife_weapon = wid == KNIFE_ID
        from re1_rl.enemy_combat import has_pending_combat, tick_pending_combat_credit
        from re1_rl.grab_escape import grab_bite_transition

        # Timed pending credit: dog/Beretta lag + grenade/bazooka flight.
        # Bare interact/door flicker still unpaid (no pending window).
        credit_pending = has_pending_combat(self._prev_state) and not combat_attack
        state = apply_combat_step_fields(
            self._prev_state,
            state,
            knife=knife_weapon,
            attack=combat_attack,
            credit_damage=credit_pending,
        )

        grab_detected = grab_bite_transition(self._prev_state, state)
        if grab_detected:
            self._grab_escape_pending = True
        state["step_emulated_frames"] = step_emulated_frames
        state["reference_step_frames"] = self.frame_skip
        state["cutscene_key"] = self._qualify_cutscene_reward(
            skipped, self._prev_state, state
        )
        ammo_spent = 0
        outcome = ""
        if attack_report is not None:
            ammo_spent = int(attack_report.get("ammo_spent", 0))
            state["ammo_spent"] = ammo_spent
            state["attack_weapon"] = attack_report.get("weapon")
            outcome = str(attack_report.get("outcome", "") or "")
            state["attack_macro_failure"] = outcome not in ("", "ok", "dry_fire")
            state["attack_dry_fire"] = outcome == "dry_fire"
        wid_for_pending = int(
            (attack_report or {}).get("weapon_id")
            or state.get("equipped_weapon_id")
            or (self._prev_state or {}).get("equipped_weapon_id")
            or 0
        )
        state = tick_pending_combat_credit(
            self._prev_state,
            state,
            knife=knife_weapon,
            attack=combat_attack,
            step_emulated_frames=step_emulated_frames,
            ammo_spent=ammo_spent,
            weapon_id=wid_for_pending,
            attack_outcome=outcome,
        )
        enemy_damage = int(state.get("enemy_damage", 0))
        enemy_kills = int(state.get("enemy_kills", 0))
        if enemy_kills > 0 and bool(state.get("in_control", True)):
            self._progress.note_leg_kills(str(state.get("room_id", "") or ""), enemy_kills)
        if combat_attack:
            self._fill_last_attack_obs(
                self._prev_state,
                state,
                knife=knife_weapon,
                attack=combat_attack,
                attack_report=attack_report,
                action_id=int(action),
            )
        menu_reason = self._probe_outside_gameplay()
        if menu_reason in _OPTIONS_MENU_REASONS:
            options_step = self._recover_options_menu(action)
            if options_step is not None:
                return options_step
            menu_reason = self._probe_outside_gameplay()
        if menu_reason in _OPTIONS_MENU_REASONS:
            menu_reason = None
        if menu_reason:
            return self._outside_gameplay_step(action, reason=menu_reason)
        # The first illegal pre-Kenneth 106 entry marks the terminal observation
        # ledger, applies -0.05 in compute_reward, then ends this episode.
        self._visited.update(state["room_id"], state["x"], state["z"])
        self._progress.record_in_control_step(
            state.get("room_id", ""),
            bool(state.get("in_control", True)),
        )

        save_complete = self._poll_typewriter_save(self._prev_state, state)
        reward, breakdown = compute_reward(
            self._prev_state,
            state,
            self._planner,
            progress=self._progress,
            graph=self.graph,
            success_room=self._stage.get("success_room"),
            rails_mode=self._stage.get("mode") == "yawn_rails",
            typewriter_save_complete=save_complete,
            return_breakdown=True,
        )
        self._after_reward_step(
            self._prev_state,
            state,
            breakdown,
            typewriter_save_complete=save_complete,
        )
        if self._post_skip_reward or self._post_skip_bd:
            reward += self._post_skip_reward
            for k, v in self._post_skip_bd.items():
                breakdown[k] = breakdown.get(k, 0.0) + v
            self._post_skip_reward = 0.0
            self._post_skip_bd = {}

        from re1_rl.reward import REWARD_SCALE

        reward = sum(breakdown.values()) * REWARD_SCALE

        if combat_attack:
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

        terminated, truncated, episode_failure = self._termination_flags(state)

        hp_now = int(state["hp"])
        if hp_now > 0:
            self._episode_min_hp = min(self._episode_min_hp, hp_now)

        obs = self._build_obs(frame_obs, state)
        damage_taken = self._episode_min_hp < self._episode_start_hp
        idle_frame_limit = softlock_frame_threshold(self._progress)
        idle_frames_used = self._progress.stagnation_frames
        idle_frames_left = max(0, idle_frame_limit - idle_frames_used)
        max_episode_steps = self._max_episode_steps()
        steps_left = (
            max(0, max_episode_steps - self._step_count)
            if max_episode_steps > 0
            else None
        )
        step_limit_frames_left = (
            steps_left * self.frame_skip if steps_left is not None else None
        )
        episode_reset_frames_left = (
            min(idle_frames_left, step_limit_frames_left)
            if step_limit_frames_left is not None
            else idle_frames_left
        )
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
            "episode_failure": episode_failure,
            "episode_reset_frames_left": episode_reset_frames_left,
            "episode_idle_frames_left": idle_frames_left,
            "episode_step_limit_frames_left": step_limit_frames_left,
            "episode_idle_frames_used": idle_frames_used,
            "episode_idle_frame_limit": idle_frame_limit,
            "knife_anim_report": (
                getattr(self.bridge, "last_knife_anim_report", None)
                if combat_attack and knife_weapon
                else None
            ),
            "attack_report": attack_report,
            "combat_audit": self._combat_audit(state, attack_report, breakdown),
            "grab_detected": grab_detected,
            "grab_escape": grab_escape,
            "magic_report": magic_report,
            "use_phase": int(getattr(self, "_use_phase", 0)),
            "equip_phase": int(getattr(self, "_equip_phase", 0)),
            "equip_switch_cooldown": int(
                getattr(self, "_equip_switch_cooldown", 0)
            ),
            "equipped_slot_0based": state.get("equipped_slot_0based"),
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
        pending_ge = getattr(self, "_go_explore_capture_pending", None) or []
        if pending_ge:
            info["go_explore_capture"] = list(pending_ge)
            self._go_explore_capture_pending = []
        pending_yr = getattr(self, "_yawn_rails_capture_pending", None) or []
        if pending_yr:
            info["yawn_rails_capture"] = list(pending_yr)
            self._yawn_rails_capture_pending = []
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
