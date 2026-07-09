"""Episode telemetry: JSONL trace of everything a human needs to audit a run.

Wrap any RE1Env:

    env = EpisodeLogger(RE1Env(...), out_dir="data/episodes")

One file per episode: ep_<start_ts>_<n>.jsonl. Each line = one step with the
symbolic state, action name, reward breakdown, and planner status. The frame
stack is NOT logged (too heavy); screenshots can be saved every N steps.

Consumers: scripts/plot_episode.py (trajectory map), manual jq/pandas.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import gymnasium as gym


class EpisodeLogger(gym.Wrapper):
    def __init__(
        self,
        env: gym.Env,
        out_dir: str | Path = "data/episodes",
        screenshot_every: int = 0,
    ) -> None:
        super().__init__(env)
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_every = screenshot_every
        self._run_ts = time.strftime("%Y%m%d_%H%M%S")
        self._episode_n = -1
        self._file = None
        self._step_n = 0

    def _open_episode(self) -> None:
        if self._file:
            self._file.close()
        self._episode_n += 1
        path = self.out_dir / f"ep_{self._run_ts}_{self._episode_n:04d}.jsonl"
        self._file = path.open("w", encoding="utf-8")
        self._step_n = 0

    def _write(self, record: dict[str, Any]) -> None:
        assert self._file is not None
        self._file.write(json.dumps(record, default=str) + "\n")
        self._file.flush()

    def reset(self, **kwargs):
        obs, info = self.env.reset(**kwargs)
        self._open_episode()
        self._write({
            "event": "reset",
            "t": time.time(),
            "stage": info.get("stage"),
            "waypoint": info.get("waypoint"),
            "state": info.get("state"),
        })
        return obs, info

    def step(self, action):
        obs, reward, terminated, truncated, info = self.env.step(action)
        self._step_n += 1
        state = info.get("state", {})
        self._write({
            "event": "step",
            "n": self._step_n,
            "t": time.time(),
            "action": int(action),
            "action_name": info.get("action_name"),
            "reward": float(reward),
            "reward_breakdown": info.get("reward_breakdown"),
            "room_id": state.get("room_id"),
            "x": state.get("x"),
            "z": state.get("z"),
            "facing": state.get("facing"),
            "hp": state.get("hp"),
            "in_control": state.get("in_control"),
            "waypoint": info.get("waypoint"),
            "waypoint_index": info.get("waypoint_index"),
            "max_waypoint": info.get("max_waypoint"),
            "frames_skipped": info.get("frames_skipped"),
            "terminated": terminated,
            "truncated": truncated,
        })
        if (
            self.screenshot_every
            and self._step_n % self.screenshot_every == 0
            and hasattr(self.env.unwrapped, "bridge")
        ):
            shot_dir = self.out_dir / "shots"
            shot_dir.mkdir(exist_ok=True)
            try:
                import cv2

                rgb = self.env.unwrapped.bridge.screenshot()
                cv2.imwrite(
                    str(shot_dir / f"ep{self._episode_n:04d}_s{self._step_n:05d}.png"),
                    cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                )
            except Exception:
                pass  # screenshots are best-effort telemetry
        return obs, reward, terminated, truncated, info

    def close(self):
        if self._file:
            self._file.close()
            self._file = None
        return self.env.close()
