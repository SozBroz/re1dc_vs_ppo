"""Gymnasium environment skeleton for Resident Evil 1."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.enemy_combat import apply_combat_step_fields
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
from re1_rl.key_items import KEYS_HELD_DIM, encode_keys_held
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
from re1_rl.reward import compute_reward, DEATH_PENALTY, REWARD_SCALE
from re1_rl.room_graph import RoomGraph
from re1_rl.knife_macro import execute_knife_macro, read_knife_hooks
from re1_rl.sticky_input import StickyInputState
from re1_rl.action_mask import (
    ATTACK_ACTION,
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
from re1_rl.attack_macro import execute_attack_macro

ACTION_NAMES = [
    "noop",
    "forward",
    "back",
    "turn_left",
    "turn_right",
    "run_forward",
    "quickturn",
    "interact",
    "knife_swing",
    "attack",  # standing aim+fire macro, any equipped weapon
    "use",  # open USE menu -> select_slot_N (2-step; herbs, sprays)
    "equip",  # open EQUIP menu -> select_slot_N (2-step)
    *DEPOSIT_ACTION_NAMES,    # 12-19 box deposits (box rooms only)
    *WITHDRAW_ACTION_NAMES,   # 20-35 box withdrawals (box rooms only)
    *MENU_ACTION_NAMES,       # 36 combine + 37-44 select_slot_N
]

# Map discrete actions to friendly button names (translated to Nymashock core
# names by lua/re1_client.lua BUTTON_MAP). Directions + square latch across
# steps; face buttons pulse within each frame_skip batch. Macro / magic
# actions (>= 8) own the joypad or write RAM directly — empty button sets.
ACTION_BUTTON_MAP: dict[int, dict[str, bool]] = {
    0: {},  # noop
    1: {"up": True},
    2: {"down": True},
    3: {"left": True},
    4: {"right": True},
    5: {"up": True, "square": True},  # run forward (square = run in RE1)
    6: {"down": True, "square": True},  # quickturn (DC: down+run)
    7: {"cross": True},  # interact / confirm
    8: {"r1": True, "down": True, "cross": True},  # knife_swing macro entry (not a blind pulse)
}
for _idx in range(9, len(ACTION_NAMES)):
    ACTION_BUTTON_MAP[_idx] = {}


def _resize_frame(frame: np.ndarray, size: tuple[int, int] = (84, 84)) -> np.ndarray:
    """RGB -> grayscale 84x84 (single channel). Pre-rendered RE1 backgrounds
    carry almost no color signal; gray x4 stack matches the Atari recipe and
    cuts conv input 3x."""
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA)[..., None]


class RE1Env(gym.Env):
    """Resident Evil 1 env wired to BizHawk (primary track).

    Observation dict:
      frame   -- 84x84x4 grayscale stack (what the agent SEES)
      proprio -- 28 named floats: body state + anim history + poison
      goal    -- 24 named floats: planner compass/TODO (obs_encoder.GOAL_FIELDS)
    Use re1_rl.obs_encoder.format_obs_table(obs) to pretty-print any obs.
    """

    metadata = {"render_modes": ["rgb_array"]}

    def __init__(
        self,
        curriculum_path: str | Path,
        bridge: BizHawkClient | None = None,
        frame_skip: int = 4,
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

        self.observation_space = spaces.Dict(
            {
                # 4 stacked grayscale frames, channels-last
                "frame": spaces.Box(0, 255, shape=(84, 84, 4), dtype=np.uint8),
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
            }
        )
        self.action_space = spaces.Discrete(len(ACTION_NAMES))

        self.graph = RoomGraph(
            self.project_root / "data" / "doors_empirical.json",
            self.project_root / "data" / "doors_rdt.json",
        )
        self.room_items = RoomItems(self.project_root / "data" / "room_items.json")
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
        self._equip_phase = 0
        self._combine_phase = 0
        self._combine_slot_a: int | None = None
        self._attack_telemetry = None
        self._stage: dict[str, Any] = {}
        self._step_count = 0
        self._prev_state: dict[str, Any] = {}
        self._prev_hp = 0
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
        self._items = ItemTracker(todo=build_item_todo(route_path))
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

    def _build_obs(self, frame_obs: np.ndarray, state: dict[str, Any]) -> dict[str, np.ndarray]:
        assert self._encoder is not None and self._planner is not None
        self._sync_episode_history(state)
        max_ep = int(self._stage.get("max_steps", 48000))
        hist = self._episode_history.encode(
            current_step=int(state.get("step", self._step_count)),
            room_index=self._encoder.room_index,
            max_episode_steps=max_ep,
        )
        return {
            "frame": frame_obs,
            "proprio": self._encoder.encode_proprio(state, self._prev_hp),
            "goal": self._encoder.encode_goal(
                state, self._planner,
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
        }

    def _sync_episode_history(self, state: dict[str, Any]) -> None:
        self._episode_history.on_step(
            state,
            prev_state=self._prev_state,
            new_items=state.get("new_items") or [],
        )

    def _push_frame(self, rgb: np.ndarray) -> np.ndarray:
        small = _resize_frame(rgb)
        self._frame_stack.append(small)
        while len(self._frame_stack) > 4:
            self._frame_stack.pop(0)
        while len(self._frame_stack) < 4:
            self._frame_stack.insert(0, small)
        return np.concatenate(self._frame_stack, axis=-1)

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
        self._use_phase = 0
        self._equip_phase = 0
        self._combine_phase = 0
        self._combine_slot_a = None
        self._last_skip_frames = 0
        self._init_anim_history()

        self._step_count = 0
        self._frame_stack = []
        self._progress = ProgressTracker()
        self._visited.reset()
        self._box_cache = None
        if getattr(self, "_attack_telemetry", None) is not None:
            self._attack_telemetry.reset_episode()
        rgb = self.bridge.screenshot()
        frame_obs = self._push_frame(rgb)
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
        """Mark spawn room visited + episode HP baseline (matches fleet reset)."""
        hp = int(state.get("hp", 0))
        self._episode_start_hp = hp if hp > 0 else 0
        self._episode_min_hp = self._episode_start_hp
        self._progress.first_visit(str(state.get("room_id", "")))

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
            self._skipping_flag = True
            burned, died = self._ram_skip.skip_uncontrolled(
                prev_hp=self._prev_hp,
                episode_start_hp=getattr(self, "_episode_start_hp", 0),
            )
            self._last_skip_frames = int(getattr(self, "_last_skip_frames", 0)) + int(
                burned
            )
            if not died:
                died = self._poll_death_during_skip()
            if died:
                self._bg_death = True
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
        )

    def _apply_post_skip_sync(self) -> None:
        """Credit pickups that finished while async skip was running."""
        state = self._read_state(track_items=True)
        state = dict(state)
        state["cutscene_key"] = self._qualify_cutscene_reward(
            int(getattr(self, "_last_skip_frames", 0)),
            self._prev_state,
            state,
        )
        reward, bd = compute_reward(
            self._prev_state,
            state,
            self._planner,
            progress=self._progress,
            graph=self.graph,
            success_room=self._stage.get("success_room"),
            return_breakdown=True,
        )
        self._post_skip_reward = float(reward)
        self._post_skip_bd = dict(bd)
        self._prev_state = state
        if state["hp"] > 0:
            self._prev_hp = state["hp"]
        hp_now = int(state["hp"])
        if hp_now > 0:
            self._episode_min_hp = min(self._episode_min_hp, hp_now)

    def _poll_death_during_skip(self) -> bool:
        """Lightweight HP poll while async skip is burning (dog/hunter scenes)."""
        if self._skip_cache_state and self._skip_cache_state.get("dead"):
            return True
        try:
            hp_ram = self.bridge.read_ram([("player_hp", PLAYER_HP, "u16")])
            hp = int(hp_ram.get("player_hp", 0))
        except (OSError, RuntimeError, ValueError):
            return False
        return player_died(
            hp,
            prev_hp=self._prev_hp,
            episode_start_hp=getattr(self, "_episode_start_hp", 0),
        )

    def _refresh_skip_cache(self) -> None:
        rgb = self.bridge.screenshot()
        frame_obs = self._push_frame(rgb)
        state = self._read_state(track_items=False)
        self._skip_cache_state = state
        self._skip_cache_obs = self._build_obs(frame_obs, state)
        if state.get("dead"):
            self._bg_death = True
        max_ep_steps = int(self._stage.get("max_steps", 3000))
        self._skip_cache_truncated = (
            max_ep_steps > 0 and self._step_count >= max_ep_steps
        )

    def _fast_cutscene_step(
        self, action: int
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        if self._poll_death_during_skip():
            self._skipping_flag = False
            return self._death_step(action, died_during_skip=True, died_during_step=False)
        self._step_count += 1
        if self._skip_cache_obs is None:
            try:
                self._refresh_skip_cache()
            except (OSError, RuntimeError, ValueError):
                pass
        obs = self._skip_cache_obs
        if obs is None:
            obs = self._build_obs(
                np.zeros((84, 84, 4), dtype=np.uint8),
                self._prev_state or {"hp": 0, "room_id": "", "x": 0, "z": 0, "facing": 0},
            )
        truncated = self._skip_cache_truncated or (
            int(self._stage.get("max_steps", 3000)) > 0
            and self._step_count >= int(self._stage.get("max_steps", 3000))
        )
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
            rgb = self.bridge.screenshot()
            frame_obs = self._push_frame(rgb)
            state = self._read_state()
            state = dict(state)
            state["dead"] = True
        except (OSError, RuntimeError, ValueError):
            state = dict(self._prev_state)
            state["dead"] = True
            frame_obs = self._frame_stack[-1] if self._frame_stack else np.zeros((84, 84, 4))
            if frame_obs.ndim == 2:
                frame_obs = frame_obs[..., None]
            while len(self._frame_stack) < 4:
                self._frame_stack.append(frame_obs)
            frame_obs = np.concatenate(self._frame_stack[-4:], axis=-1)
        reward, breakdown = self._death_penalty()
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
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
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
            inventory = read_inventory(self.bridge)
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass
        current_hp = int(state.get("hp", 0))
        poisoned = bool(state.get("poisoned", False))
        episode_start_hp = int(getattr(self, "_episode_start_hp", 0) or 0)

        if self._use_phase == 0:
            if not any_legal_use_slot(
                inventory or [],
                current_hp=current_hp,
                poisoned=poisoned,
                episode_start_hp=episode_start_hp,
            ):
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
        if not slot_legal_for_use(
            inventory or [],
            int(slot),
            current_hp=current_hp,
            poisoned=poisoned,
            episode_start_hp=episode_start_hp,
        ):
            return self._submenu_step(
                action,
                step_emulated_frames=self.frame_skip,
                magic_report={"ok": False, "reason": "use_slot_not_legal"},
            )
        try:
            died, frames, magic_report = execute_use_macro(
                self.bridge,
                int(slot),
                prev_hp=self._prev_hp,
                episode_start_hp=getattr(self, "_episode_start_hp", 0),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            died, frames = False, self.frame_skip
            magic_report = {"ok": False, "reason": f"error:{exc}"}
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
            return self._death_step(
                action, died_during_skip=False, died_during_step=True
            )
        assert self._planner is not None
        self._step_count += 1
        self._sample_anim_history()
        rgb = self.bridge.screenshot()
        frame_obs = self._push_frame(rgb)
        state = self._read_state()
        state = dict(state)
        state["step_emulated_frames"] = int(step_emulated_frames)
        state["reference_step_frames"] = self.frame_skip
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
        max_ep_steps = int(self._stage.get("max_steps", 3000))
        truncated = max_ep_steps > 0 and self._step_count >= max_ep_steps
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
        )

    def action_masks(self) -> np.ndarray:
        anim = aux = recovery = None
        equipped = None
        inventory = None
        box = None
        in_box_room = False
        equipped_slot_0b = None
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

                room = str(self._prev_state.get("room_id", ""))
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
        state = getattr(self, "_prev_state", {}) or {}
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
            current_hp=int(state.get("hp", 0)),
            poisoned=bool(state.get("poisoned", False)),
            episode_start_hp=int(getattr(self, "_episode_start_hp", 0) or 0),
            in_control=bool(state.get("in_control", True)),
        )

    def step(self, action: int):
        action = int(action)
        try:
            return self._step_once(action)
        finally:
            self._prev_action = action

    def _step_once(self, action: int):
        assert self._planner is not None
        self._start_bg_skip()
        if self._bg_death:
            self._bg_death = False
            self._skipping_flag = False
            return self._death_step(action, died_during_skip=True, died_during_step=False)
        menu_reason = self._probe_outside_gameplay()
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
        elif attack:
            self._sticky_input.apply(0, ACTION_BUTTON_MAP)
            self._macro_active = True
            try:
                died_during_step, step_emulated_frames, attack_report = (
                    execute_attack_macro(
                        self.bridge,
                        empty_sticky=self._sticky_input.as_dict(),
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
            )
        else:
            sticky, pulse, pulse_hold = self._sticky_input.apply(
                int(action), ACTION_BUTTON_MAP
            )
            _, died_during_step = self.bridge.step(
                n=self.frame_skip,
                sticky=sticky,
                pulse=pulse,
                pulse_hold=pulse_hold,
            )
        if died_during_step:
            self._skipping_flag = False
            return self._death_step(
                action, died_during_skip=False, died_during_step=True
            )

        if self._async_cutscene_skip and self._probe_needs_skip():
            self._skipping_flag = True
            self._skip_cache_obs = None
            return self._fast_cutscene_step(action)

        skipped, died_during_skip = 0, False
        if not self._async_cutscene_skip:
            skipped, died_during_skip = self._skip_uncontrolled()
            if died_during_skip:
                return self._death_step(
                    action, died_during_skip=True, died_during_step=False
                )

        self._step_count += 1
        self._sample_anim_history()
        rgb = self.bridge.screenshot()
        frame_obs = self._push_frame(rgb)
        state = self._read_state()
        if died_during_skip or died_during_step:
            state = dict(state)
            state["dead"] = True
        state = apply_combat_step_fields(
            self._prev_state,
            state,
            knife=knife,
            attack=attack,
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
        if knife or attack:
            self._record_attack_telemetry(
                action, state,
                attack_report=attack_report,
                enemy_damage=enemy_damage,
                enemy_kills=enemy_kills,
            )
        menu_reason = self._probe_outside_gameplay()
        if menu_reason:
            return self._outside_gameplay_step(action, reason=menu_reason)
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

        terminated = bool(state.get("dead"))
        max_ep_steps = int(self._stage.get("max_steps", 3000))
        truncated = (
            max_ep_steps > 0 and self._step_count >= max_ep_steps
        )

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
        self._prev_state = state
        if state["hp"] > 0:
            self._prev_hp = state["hp"]
        return obs, reward, terminated, truncated, info

    def render(self):
        if self._frame_stack:
            return self._frame_stack[-1]
        return np.zeros((84, 84, 3), dtype=np.uint8)

    def close(self):
        self._stop_bg_skip()
        try:
            self.bridge.quit()
        finally:
            self.bridge.close()
