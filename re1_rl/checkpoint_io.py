"""Atomic PPO checkpoint writes and resume-path resolution.

SB3 writes zip checkpoints in-place. A hard kill (or copying over a file
mid-write) leaves a truncated zip. We always write to a pid-scoped temp file
and ``os.replace`` into place, then record the path in ``latest.json``.
"""

from __future__ import annotations

import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any

_STEP_RE = re.compile(r"_(\d+)_steps\.zip$", re.IGNORECASE)
_NUMBERED_RE = re.compile(r"^ppo_re1_\d+_steps\.zip$", re.IGNORECASE)

# Total SB3 ``num_timesteps`` between checkpoint saves at n_envs=20.
CHECKPOINT_INTERVAL_AT_20_ENVS = 40_000


def checkpoint_timestep_interval(n_envs: int) -> int:
    """Timesteps between PPO checkpoints, scaled linearly with fleet size."""
    n = max(int(n_envs), 1)
    return max(CHECKPOINT_INTERVAL_AT_20_ENVS * n // 20, 1)


def checkpoint_save_freq_vec_env(n_envs: int) -> int:
    """SB3 ``CheckpointCallback.save_freq`` (one vec-env step = n_envs timesteps)."""
    n = max(int(n_envs), 1)
    return max(checkpoint_timestep_interval(n) // n, 1)


def checkpoint_due(
    num_timesteps: int,
    last_save_timesteps: int,
    interval: int,
) -> bool:
    """True when ``num_timesteps`` has advanced by at least ``interval`` since last save."""
    return int(num_timesteps) - int(last_save_timesteps) >= max(int(interval), 1)


def zip_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.suffix.lower() == ".zip" else p.with_suffix(".zip")


def is_valid_checkpoint(path: str | Path) -> bool:
    """Return True if path looks like a complete SB3 checkpoint zip."""
    p = zip_path(path)
    if not p.is_file() or p.stat().st_size < 200:
        return False
    try:
        with zipfile.ZipFile(p, "r") as zf:
            names = set(zf.namelist())
        return "data" in names and any(n.endswith(".pth") for n in names)
    except (OSError, zipfile.BadZipFile):
        return False


def _steps_from_name(path: Path) -> int:
    m = _STEP_RE.search(path.name)
    return int(m.group(1)) if m else -1


def find_latest_checkpoint(ckpt_dir: str | Path) -> Path | None:
    ckpt_dir = Path(ckpt_dir)
    if not ckpt_dir.is_dir():
        return None
    best: tuple[int, float, Path] | None = None
    for p in ckpt_dir.glob("ppo_re1_*_steps.zip"):
        if not is_valid_checkpoint(p):
            continue
        steps = _steps_from_name(p)
        key = (steps, p.stat().st_mtime, p)
        if best is None or key[0] > best[0] or (key[0] == best[0] and key[1] > best[1]):
            best = key
    return best[2] if best else None


def read_latest_pointer(ckpt_dir: str | Path) -> dict[str, Any] | None:
    path = Path(ckpt_dir) / "latest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_latest_pointer(
    ckpt_dir: str | Path,
    checkpoint: str | Path,
    *,
    steps: int | None = None,
) -> Path:
    ckpt_dir = Path(ckpt_dir)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt = zip_path(checkpoint)
    if steps is None:
        steps = _steps_from_name(ckpt)
    payload = {
        "path": str(ckpt).replace("\\", "/"),
        "steps": steps,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bytes": ckpt.stat().st_size,
    }
    tmp = ckpt_dir / f".latest.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    dest = ckpt_dir / "latest.json"
    os.replace(tmp, dest)
    return dest


def atomic_model_save(model: Any, path: str | Path) -> Path:
    """Save an SB3 model atomically; returns the final ``.zip`` path."""
    dest = zip_path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Path must have no suffix so SB3's save() appends ``.zip`` exactly once.
    tmp_base = dest.parent / f"_ckpt_write_{dest.stem}_{os.getpid()}"
    tmp_zip = zip_path(tmp_base)
    if tmp_zip.exists():
        tmp_zip.unlink()
    model.save(str(tmp_base))
    if not tmp_zip.is_file():
        raise OSError(f"atomic save failed: missing temp checkpoint {tmp_zip}")
    if not is_valid_checkpoint(tmp_zip):
        tmp_zip.unlink(missing_ok=True)
        raise OSError(f"atomic save failed: invalid temp checkpoint {tmp_zip}")
    os.replace(tmp_zip, dest)
    return dest


def atomic_copy_checkpoint(src: str | Path, dest: str | Path) -> Path:
    """Atomically copy a valid checkpoint zip (for ``ppo_re1_final`` alias)."""
    src_p = zip_path(src)
    dest_p = zip_path(dest)
    if not is_valid_checkpoint(src_p):
        raise ValueError(f"refusing to copy invalid checkpoint: {src_p}")
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_p.parent / f".{dest_p.stem}.copy.{os.getpid()}.tmp.zip"
    data = src_p.read_bytes()
    tmp.write_bytes(data)
    if not is_valid_checkpoint(tmp):
        tmp.unlink(missing_ok=True)
        raise OSError(f"atomic copy failed validation: {tmp}")
    os.replace(tmp, dest_p)
    return dest_p


def find_newest_checkpoint(ckpt_dir: str | Path) -> Path | None:
    """Newest valid numbered checkpoint by filesystem mtime."""
    ckpt_dir = Path(ckpt_dir)
    if not ckpt_dir.is_dir():
        return None
    best: tuple[float, Path] | None = None
    for p in ckpt_dir.glob("ppo_re1_*_steps.zip"):
        if not is_valid_checkpoint(p):
            continue
        mtime = p.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, p)
    return best[1] if best else None


def is_convention_checkpoint(
    path: Path,
    *,
    named_run: bool,
    run_name: str = "",
) -> bool:
    """True for ``ppo_re1_<N>_steps.zip`` and run-scoped ``ppo_re1_final`` aliases."""
    name = path.name
    if _NUMBERED_RE.match(name):
        return True
    if named_run:
        return name.lower() == f"ppo_re1_final_{run_name}.zip".lower()
    return name.lower() == "ppo_re1_final.zip"


def convention_checkpoint_candidates(
    *,
    project_root: Path,
    ckpt_dir: Path,
    named_run: bool,
) -> list[Path]:
    """All on-disk paths that may participate in auto-resume for this run."""
    run_name = ckpt_dir.name if named_run else ""
    root = project_root
    candidates: list[Path] = []
    if ckpt_dir.is_dir():
        for p in ckpt_dir.glob("ppo_re1_*_steps.zip"):
            if is_convention_checkpoint(p, named_run=named_run, run_name=run_name):
                candidates.append(p)
    if named_run:
        candidates.append(root / "data" / f"ppo_re1_final_{run_name}.zip")
    else:
        candidates.append(root / "data" / "ppo_re1_final.zip")
    ptr = read_latest_pointer(ckpt_dir)
    if ptr and ptr.get("path"):
        ptr_p = Path(str(ptr["path"]))
        if not ptr_p.is_absolute():
            ptr_p = root / ptr_p
        candidates.append(zip_path(ptr_p))
    return candidates


def resolve_resume_path(
    resume: str | Path | None,
    *,
    project_root: str | Path,
    ckpt_dir: str | Path | None = None,
) -> Path | None:
    """Pick the best loadable checkpoint for a new training run.

    Explicit ``--resume`` wins when valid. Otherwise pick the newest valid
    convention-named checkpoint by filesystem mtime (``ppo_re1_<N>_steps.zip``
    or ``ppo_re1_final[_run].zip``), including ``latest.json`` pointer paths.
    """
    root = Path(project_root)
    ckpt_dir = Path(ckpt_dir or root / "data" / "checkpoints")
    default_ckpt_dir = root / "data" / "checkpoints"
    named_run = ckpt_dir.resolve() != default_ckpt_dir.resolve()
    run_name = ckpt_dir.name if named_run else ""
    global_legacy = (root / "data" / "ppo_re1_final.zip").resolve()

    if resume is not None and str(resume).lower() == "auto":
        resume = None

    if resume is None:
        ptr = read_latest_pointer(ckpt_dir)
        if ptr and ptr.get("path"):
            ptr_p = Path(str(ptr["path"]))
            if not ptr_p.is_absolute():
                ptr_p = root / ptr_p
            ptr_zip = zip_path(ptr_p)
            if is_valid_checkpoint(ptr_zip):
                return ptr_zip

    if resume:
        p = Path(resume)
        if not p.is_absolute():
            p = root / p
        explicit = zip_path(p)
        if is_valid_checkpoint(explicit):
            return explicit

    best: tuple[float, Path] | None = None
    seen: set[str] = set()
    for cand in convention_checkpoint_candidates(
        project_root=root,
        ckpt_dir=ckpt_dir,
        named_run=named_run,
    ):
        key = str(cand.resolve()) if cand.exists() else str(cand)
        if key in seen:
            continue
        seen.add(key)
        if not is_valid_checkpoint(cand):
            continue
        if named_run and cand.resolve() == global_legacy:
            continue
        if not is_convention_checkpoint(cand, named_run=named_run, run_name=run_name):
            continue
        mtime = cand.stat().st_mtime
        steps = _steps_from_name(cand)
        if best is None or mtime > best[0] or (
            mtime == best[0] and steps > best[1]
        ):
            best = (mtime, steps, cand)
    return best[2] if best else None
