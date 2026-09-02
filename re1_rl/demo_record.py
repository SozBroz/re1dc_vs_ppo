"""Human demonstrations for behavioural cloning (planner-loyal legs).

A demo is one episode played by a human *through* ``RE1Env.step`` with the
same discrete action space, sticky-input latching and observation Dict the
PPO policy sees. Each decision stores ``(obs, action, action_mask)`` so the
learner can add a masked log-likelihood term (see ``combat_ppo.DemoBCAux``).

On-disk format (one ``.npz`` per episode):

    obs__<key>     (T, ...)          env-native dtype/shape (frame is HWC)
    action         (T,) int64
    action_mask    (T, n_actions) bool
    reward         (T,) float32
    meta           0-d str            JSON: schema, obs_schema_version,
                                      n_actions, start cell, success, ...
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from re1_rl.sticky_input import (
    INTERACT_ACTION,
    STICKY_KEYS,
    StickyInputState,
)

DEMO_SCHEMA_VERSION = 1
OBS_PREFIX = "obs__"
ATTACK_ACTION = 7
NOOP_ACTION = 0
# Candidate movement actions in tie-break order. Prefer ``forward``/
# ``run_forward`` (which clear turn latches) over ``noop`` when the human
# releases a turn while still walking/running.
_MOVE_CANDIDATES = (1, 5, 2, 3, 4, NOOP_ACTION)


def desired_sticky(buttons: dict[str, bool]) -> dict[str, bool]:
    return {k: bool(buttons.get(k)) for k in STICKY_KEYS}


def buttons_to_action(
    buttons: dict[str, bool],
    sticky_now: dict[str, bool],
    *,
    button_map: dict[int, dict[str, bool]],
) -> int:
    """Discrete action whose sticky result is closest to what the human holds.

    Face buttons win: ``r1``+``cross`` is the fire combo (attack macro),
    ``cross`` alone is interact. Otherwise pick the movement action whose
    ``StickyInputState.apply`` outcome has the smallest Hamming distance to
    the held directions/run; ties resolve in ``_MOVE_CANDIDATES`` order.
    """
    if buttons.get("cross"):
        return ATTACK_ACTION if buttons.get("r1") else INTERACT_ACTION
    want = desired_sticky(buttons)
    if not any(want.values()):
        return NOOP_ACTION
    best_action = NOOP_ACTION
    best_dist: int | None = None
    for action in _MOVE_CANDIDATES:
        probe = StickyInputState()
        probe._sticky.update(sticky_now)  # noqa: SLF001
        got, _pulse, _hold = probe.apply(action, button_map)
        dist = sum(1 for k in STICKY_KEYS if bool(got.get(k)) != want[k])
        if best_dist is None or dist < best_dist:
            best_action, best_dist = action, dist
            if dist == 0:
                break
    return int(best_action)


@dataclass
class DemoEpisode:
    """Per-decision buffer for one human episode."""

    obs: dict[str, list[np.ndarray]] = field(default_factory=dict)
    actions: list[int] = field(default_factory=list)
    masks: list[np.ndarray] = field(default_factory=list)
    rewards: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.actions)

    def add(
        self,
        obs: dict[str, np.ndarray],
        action: int,
        mask: np.ndarray,
    ) -> None:
        for key, value in obs.items():
            self.obs.setdefault(key, []).append(np.array(value, copy=True))
        self.actions.append(int(action))
        self.masks.append(np.asarray(mask, dtype=bool).copy())
        self.rewards.append(0.0)

    def note_reward(self, reward: float) -> None:
        if self.rewards:
            self.rewards[-1] = float(reward)

    def to_arrays(self, meta: dict[str, Any]) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for key, rows in self.obs.items():
            out[OBS_PREFIX + key] = np.stack(rows, axis=0)
        out["action"] = np.asarray(self.actions, dtype=np.int64)
        out["action_mask"] = np.stack(self.masks, axis=0).astype(bool)
        out["reward"] = np.asarray(self.rewards, dtype=np.float32)
        out["meta"] = np.array(json.dumps(meta, sort_keys=True))
        return out


def demo_filename(*, start_cell: str, success: bool, stamp: float | None = None) -> str:
    stamp = time.time() if stamp is None else float(stamp)
    tag = "ok" if success else "fail"
    return f"{start_cell}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(stamp))}_{tag}.npz"


def write_demo(path: Path, episode: DemoEpisode, meta: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    full = dict(meta)
    full.setdefault("schema", DEMO_SCHEMA_VERSION)
    full["steps"] = len(episode)
    full["reward_total"] = float(sum(episode.rewards))
    tmp = path.with_suffix(".npz.tmp")
    with tmp.open("wb") as handle:
        np.savez_compressed(handle, **episode.to_arrays(full))
    tmp.replace(path)
    return path


def read_demo_meta(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as data:
        raw = data["meta"]
    return json.loads(str(raw))


@dataclass
class DemoDataset:
    obs: dict[str, np.ndarray]
    actions: np.ndarray
    masks: np.ndarray
    files: list[Path]
    signature: tuple[tuple[str, int, int], ...]

    def __len__(self) -> int:
        return int(self.actions.shape[0])


def demo_dir_signature(demo_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """(name, mtime_ns, size) per ``*.npz`` — cheap change detector for hot reload."""
    demo_dir = Path(demo_dir)
    if not demo_dir.is_dir():
        return ()
    rows = []
    for path in sorted(demo_dir.glob("*.npz")):
        try:
            st = path.stat()
        except OSError:
            continue
        rows.append((path.name, int(st.st_mtime_ns), int(st.st_size)))
    return tuple(rows)


def _frame_to_policy_layout(frame: np.ndarray, want_shape: tuple[int, ...]) -> np.ndarray:
    """Demos store env-native HWC frames; the policy space is CHW."""
    if frame.shape[1:] == tuple(want_shape):
        return frame
    if frame.ndim == 4 and frame.shape[1:] == (want_shape[1], want_shape[2], want_shape[0]):
        return np.ascontiguousarray(np.transpose(frame, (0, 3, 1, 2)))
    raise ValueError(f"demo frame shape {frame.shape[1:]} does not match policy {want_shape}")


def load_demo_dataset(
    demo_dir: Path,
    *,
    obs_shapes: dict[str, tuple[int, ...]],
    n_actions: int,
    obs_schema_version: int | None = None,
    successful_only: bool = True,
) -> DemoDataset | None:
    """Concatenate every compatible demo under ``demo_dir`` (None if empty).

    ``obs_shapes`` is the policy observation space (key → per-step shape).
    Files with a different action count / obs schema are skipped with a
    printed reason so a stale demo never poisons the loss silently.
    """
    demo_dir = Path(demo_dir)
    signature = demo_dir_signature(demo_dir)
    if not signature:
        return None
    obs_chunks: dict[str, list[np.ndarray]] = {k: [] for k in obs_shapes}
    action_chunks: list[np.ndarray] = []
    mask_chunks: list[np.ndarray] = []
    used: list[Path] = []
    for name, _mtime, _size in signature:
        path = demo_dir / name
        try:
            with np.load(path, allow_pickle=False) as data:
                meta = json.loads(str(data["meta"]))
                if successful_only and not bool(meta.get("success", False)):
                    continue
                if int(meta.get("n_actions", -1)) != int(n_actions):
                    print(f"[demo_bc] skip {name}: n_actions {meta.get('n_actions')} != {n_actions}", flush=True)
                    continue
                if (
                    obs_schema_version is not None
                    and int(meta.get("obs_schema_version", -1)) != int(obs_schema_version)
                ):
                    print(
                        f"[demo_bc] skip {name}: obs_schema {meta.get('obs_schema_version')} "
                        f"!= {obs_schema_version}",
                        flush=True,
                    )
                    continue
                actions = np.asarray(data["action"], dtype=np.int64)
                masks = np.asarray(data["action_mask"], dtype=bool)
                if actions.ndim != 1 or masks.shape != (actions.shape[0], int(n_actions)):
                    print(f"[demo_bc] skip {name}: bad action/mask shapes", flush=True)
                    continue
                per_key: dict[str, np.ndarray] = {}
                missing = None
                for key, shape in obs_shapes.items():
                    arr_key = OBS_PREFIX + key
                    if arr_key not in data.files:
                        missing = key
                        break
                    arr = np.asarray(data[arr_key])
                    if key == "frame":
                        arr = _frame_to_policy_layout(arr, tuple(shape))
                    if arr.shape[0] != actions.shape[0] or arr.shape[1:] != tuple(shape):
                        raise ValueError(f"{key}: {arr.shape} vs (T, {shape})")
                    per_key[key] = arr
                if missing is not None:
                    print(f"[demo_bc] skip {name}: missing obs key {missing!r}", flush=True)
                    continue
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"[demo_bc] skip {name}: {exc}", flush=True)
            continue
        for key, arr in per_key.items():
            obs_chunks[key].append(arr)
        action_chunks.append(actions)
        mask_chunks.append(masks)
        used.append(path)
    if not action_chunks:
        return None
    return DemoDataset(
        obs={k: np.concatenate(v, axis=0) for k, v in obs_chunks.items()},
        actions=np.concatenate(action_chunks, axis=0),
        masks=np.concatenate(mask_chunks, axis=0),
        files=used,
        signature=signature,
    )


def summarize_demos(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        try:
            meta = read_demo_meta(Path(path))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        rows.append({"file": Path(path).name, **meta})
    return rows
