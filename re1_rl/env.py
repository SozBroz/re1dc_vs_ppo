"""Gymnasium environment skeleton for Resident Evil 1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import DEFAULT_RAM_FIELDS, PLAYER_HP
from re1_rl.planner import WaypointPlanner
from re1_rl.reward import compute_reward

ACTION_NAMES = [
    "noop",
    "forward",
    "back",
    "turn_left",
    "turn_right",
    "run_forward",
    "quickturn",
    "interact",
    "aim",
    "fire",
]

# Map discrete actions to PSX button holds for one frame_skip batch.
# TODO: Tune tank-control timing with live emulator.
ACTION_BUTTON_MAP: dict[int, dict[str, bool]] = {
    0: {},  # noop
    1: {"P1 Up": True},
    2: {"P1 Down": True},
    3: {"P1 Left": True},
    4: {"P1 Right": True},
    5: {"P1 Up": True, "P1 R1": True},
    6: {"P1 R1": True},  # quickturn — stub
    7: {"P1 Cross": True},
    8: {"P1 Square": True},
    9: {"P1 Circle": True},
}


def _resize_frame(frame: np.ndarray, size: tuple[int, int] = (84, 84)) -> np.ndarray:
    import cv2

    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


class RE1Env(gym.Env):
    """Resident Evil 1 env wired to BizHawk (primary track)."""

    metadata = {"render_modes": ["rgb_array"]}

    def __init__(
        self,
        curriculum_path: str | Path,
        bridge: BizHawkClient | None = None,
        frame_skip: int = 8,
        project_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1])
        self.curriculum_path = Path(curriculum_path)
        self.bridge = bridge or BizHawkClient()
        self.frame_skip = frame_skip

        self.observation_space = spaces.Dict(
            {
                "frame": spaces.Box(0, 255, shape=(84, 84, 4), dtype=np.uint8),
                "ram": spaces.Box(-np.inf, np.inf, shape=(16,), dtype=np.float32),
            }
        )
        self.action_space = spaces.Discrete(len(ACTION_NAMES))

        self._frame_stack: list[np.ndarray] = []
        self._planner: WaypointPlanner | None = None
        self._stage: dict[str, Any] = {}
        self._step_count = 0
        self._prev_state: dict[str, Any] = {}
        self._prev_hp = 0

    def _load_stage(self) -> None:
        with self.curriculum_path.open(encoding="utf-8") as f:
            self._stage = json.load(f)
        route_path = self.project_root / "data" / "route_jill_anypct.json"
        self._planner = WaypointPlanner(route_path, waypoints=self._stage.get("waypoints"))

    def _read_state(self) -> dict[str, Any]:
        ram = self.bridge.read_ram(DEFAULT_RAM_FIELDS)
        room_id = ram.get("room_id")
        if room_id is None:
            room_id = -1  # ROOM_ID address unknown
        return {
            "hp": int(ram.get("player_hp", 0)),
            "room_id": room_id,
            "inventory": [],  # TODO: read ITEM_BOX_BASE via bridge
            "step": self._step_count,
            "dead": int(ram.get("player_hp", 1)) <= 0,
        }

    def _ram_vector(self, ram: dict[str, int | float]) -> np.ndarray:
        vec = np.zeros(16, dtype=np.float32)
        keys = list(ram.keys())[:16]
        for i, k in enumerate(keys):
            vec[i] = float(ram[k])
        return vec

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
        self._load_stage()
        assert self._planner is not None

        state_path = self.project_root / self._stage["init_savestate"]
        self.bridge.load_savestate(str(state_path))
        self.bridge.frameadvance(1)

        self._step_count = 0
        self._frame_stack = []
        rgb = self.bridge.screenshot()
        frame_obs = self._push_frame(rgb)
        ram_raw = self.bridge.read_ram(DEFAULT_RAM_FIELDS)
        self._prev_state = self._read_state()
        self._prev_hp = self._prev_state["hp"]

        obs = {"frame": frame_obs, "ram": self._ram_vector(ram_raw)}
        info = {"stage": self._stage.get("stage"), "waypoint": self._planner.next_waypoint_room()}
        return obs, info

    def step(self, action: int):
        assert self._planner is not None
        buttons = ACTION_BUTTON_MAP.get(int(action), {})
        self.bridge.send_buttons(buttons)
        self.bridge.frameadvance(self.frame_skip)

        self._step_count += 1
        rgb = self.bridge.screenshot()
        frame_obs = self._push_frame(rgb)
        ram_raw = self.bridge.read_ram(DEFAULT_RAM_FIELDS)
        state = self._read_state()

        reward = compute_reward(self._prev_state, state, self._planner)

        terminated = bool(state.get("dead"))
        truncated = self._step_count >= int(self._stage.get("max_steps", 3000))

        self._prev_state = state
        self._prev_hp = state["hp"]

        obs = {"frame": frame_obs, "ram": self._ram_vector(ram_raw)}
        info = {
            "room_id": state["room_id"],
            "hp": state["hp"],
            "waypoint": self._planner.next_waypoint_room(),
        }
        return obs, reward, terminated, truncated, info

    def render(self):
        if self._frame_stack:
            return self._frame_stack[-1]
        return np.zeros((84, 84, 3), dtype=np.uint8)
