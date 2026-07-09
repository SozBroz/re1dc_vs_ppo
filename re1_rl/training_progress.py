"""Fleet training progression: rooms, waypoints, gallery (sync + async)."""

from __future__ import annotations

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
    if "episode" in info:
        out["episode"] = info["episode"]
    if info.get("gallery_flawless") is not None:
        out["gallery_flawless"] = info["gallery_flawless"]
    return out


class TrainingProgressTracker:
    """Console + TensorBoard progression across parallel envs."""

    def __init__(self, *, prefix: str = "progress") -> None:
        self.prefix = prefix
        self.best_waypoint = 0
        self.rooms_seen: set[str] = set()
        self.episodes = 0
        self.new_room_hits = 0
        self.cutscene_hits = 0

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
            bd = info.get("reward_breakdown") or {}
            if bd.get("new_room", 0) > 0:
                self.new_room_hits += 1
            if bd.get("new_cutscene", 0) > 0:
                self.cutscene_hits += 1
            if "episode" in info:
                self.episodes += 1

    def log_rollout_end(
        self,
        model: PPO,
        *,
        num_timesteps: int,
        episode_infos: list[dict[str, Any]] | None = None,
    ) -> None:
        ep_rew = model.ep_info_buffer
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
            f"[rollout] steps={num_timesteps} eps={self.episodes} "
            f"ep_rew={mean_rew:.3f} ep_len={mean_len:.0f} "
            f"new_room_hits={self.new_room_hits} "
            f"cutscene_hits={self.cutscene_hits} "
            f"rooms={sorted(self.rooms_seen)}",
            flush=True,
        )
        logger = getattr(model, "logger", None)
        if logger is not None:
            logger.record("re1/rooms_seen", len(self.rooms_seen))
            logger.record("re1/new_room_hits", self.new_room_hits)
            logger.record("re1/cutscene_hits", self.cutscene_hits)
