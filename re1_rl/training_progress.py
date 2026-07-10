"""Fleet training progression: rooms, waypoints, gallery (sync + async)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

from stable_baselines3 import PPO


def slim_progress_info(info: dict[str, Any]) -> dict[str, Any]:
    """Drop heavy env info (full state) before IPC / logging."""
    if not info:
        return {}
    out: dict[str, Any] = {
        "room_id": info.get("room_id"),
        "max_waypoint": info.get("max_waypoint", 0),
        "reward_breakdown": info.get("reward_breakdown"),
        "bridge_port": info.get("bridge_port"),
        "hp": info.get("hp"),
        "episode_start_hp": info.get("episode_start_hp"),
    }
    visited = info.get("visited_rooms")
    if visited is not None:
        out["visited_rooms"] = list(visited)
        out["n_rooms_visited"] = int(
            info.get("n_rooms_visited", len(out["visited_rooms"]))
        )
    if "episode" in info:
        out["episode"] = info["episode"]
    if info.get("gallery_flawless") is not None:
        out["gallery_flawless"] = info["gallery_flawless"]
    if info.get("episode_failure") is not None:
        out["episode_failure"] = info.get("episode_failure")
    return out


def _visited_from_info(info: dict[str, Any]) -> list[str]:
    raw = info.get("visited_rooms")
    if raw is None:
        return []
    return sorted({str(r) for r in raw if r is not None and str(r)})


class TrainingProgressTracker:
    """Console + TensorBoard progression across parallel envs."""

    def __init__(
        self,
        *,
        prefix: str = "progress",
        machine_name: str = "",
        best_log_path: str | Path | None = None,
    ) -> None:
        self.prefix = prefix
        self.machine_name = machine_name or "local"
        self.best_waypoint = 0
        self.rooms_seen: set[str] = set()
        self.episodes = 0
        self.new_room_hits = 0
        self.cutscene_hits = 0
        # Best single-episode room exploration on this machine/process.
        self.best_episode_n_rooms = 0
        self.best_episode_room_ids: list[str] = []
        self.best_log_path = Path(best_log_path) if best_log_path else None

    def consume_infos(self, infos: Iterable[dict[str, Any]], *, num_timesteps: int) -> None:
        for info in infos:
            if not info:
                continue
            wp = int(info.get("max_waypoint", 0) or 0)
            if wp > self.best_waypoint:
                self.best_waypoint = wp
            room = info.get("room_id")
            if room and room not in self.rooms_seen:
                self.rooms_seen.add(str(room))
                print(
                    f"[{self.prefix}] first visit to room {room} "
                    f"at step {num_timesteps}",
                    flush=True,
                )
            for r in _visited_from_info(info):
                if r not in self.rooms_seen:
                    self.rooms_seen.add(r)
                    print(
                        f"[{self.prefix}] first visit to room {r} "
                        f"at step {num_timesteps}",
                        flush=True,
                    )
            bd = info.get("reward_breakdown") or {}
            if bd.get("new_room", 0) > 0:
                self.new_room_hits += 1
            if bd.get("new_cutscene", 0) > 0:
                self.cutscene_hits += 1
            if "episode" in info:
                self.episodes += 1
                self._on_episode_end(info, num_timesteps=num_timesteps)

    def _on_episode_end(self, info: dict[str, Any], *, num_timesteps: int) -> None:
        ep = info.get("episode") or {}
        rooms = _visited_from_info(info)
        n_rooms = int(info.get("n_rooms_visited") or len(rooms))
        rew = float(ep.get("r", float("nan")))
        length = int(ep.get("l", 0) or 0)
        failure = info.get("episode_failure")
        port = info.get("bridge_port")
        print(
            f"[episode] machine={self.machine_name} port={port} "
            f"rooms={n_rooms} ids={rooms} "
            f"rew={rew:.3f} len={length} "
            f"wp={int(info.get('max_waypoint', 0) or 0)} "
            f"fail={failure!r} steps={num_timesteps}",
            flush=True,
        )
        if n_rooms > self.best_episode_n_rooms:
            self.best_episode_n_rooms = n_rooms
            self.best_episode_room_ids = list(rooms)
            print(
                f"[PB-rooms] machine={self.machine_name} best episode "
                f"rooms={n_rooms} ids={rooms}",
                flush=True,
            )
            self._persist_best_episode(num_timesteps=num_timesteps, info=info)

    def _persist_best_episode(self, *, num_timesteps: int, info: dict[str, Any]) -> None:
        if self.best_log_path is None:
            return
        try:
            self.best_log_path.parent.mkdir(parents=True, exist_ok=True)
            note = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "machine": self.machine_name,
                "n_rooms": self.best_episode_n_rooms,
                "room_ids": self.best_episode_room_ids,
                "max_waypoint": int(info.get("max_waypoint", 0) or 0),
                "bridge_port": info.get("bridge_port"),
                "num_timesteps": int(num_timesteps),
                "episode": info.get("episode"),
            }
            with self.best_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(note) + "\n")
            latest = self.best_log_path.with_name(
                self.best_log_path.stem + "_latest.json"
            )
            latest.write_text(json.dumps(note, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"[{self.prefix}] best-room persist failed: {exc}", flush=True)

    def log_rollout_end(
        self,
        model: PPO | None = None,
        *,
        num_timesteps: int,
        episode_infos: list[dict[str, Any]] | None = None,
    ) -> None:
        ep_rew = getattr(model, "ep_info_buffer", None) if model is not None else None
        if ep_rew:
            mean_rew = sum(e["r"] for e in ep_rew) / len(ep_rew)
            mean_len = sum(e["l"] for e in ep_rew) / len(ep_rew)
        elif episode_infos:
            ended = [i["episode"] for i in episode_infos if i.get("episode")]
            mean_rew = (
                sum(e["r"] for e in ended) / len(ended) if ended else float("nan")
            )
            mean_len = (
                sum(e["l"] for e in ended) / len(ended) if ended else float("nan")
            )
        else:
            mean_rew = float("nan")
            mean_len = float("nan")

        print(
            f"[rollout] machine={self.machine_name} steps={num_timesteps} "
            f"eps={self.episodes} "
            f"ep_rew={mean_rew:.3f} ep_len={mean_len:.0f} "
            f"new_room_hits={self.new_room_hits} "
            f"cutscene_hits={self.cutscene_hits} "
            f"rooms={sorted(self.rooms_seen)} "
            f"best_ep_rooms={self.best_episode_n_rooms} "
            f"best_ep_ids={self.best_episode_room_ids}",
            flush=True,
        )
        logger = getattr(model, "logger", None) if model is not None else None
        if logger is not None:
            logger.record("re1/rooms_seen", len(self.rooms_seen))
            logger.record("re1/new_room_hits", self.new_room_hits)
            logger.record("re1/cutscene_hits", self.cutscene_hits)
            logger.record("re1/best_episode_rooms", self.best_episode_n_rooms)
