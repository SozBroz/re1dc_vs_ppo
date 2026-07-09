"""Checkpoint IO helpers."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.checkpoint_io import (
    checkpoint_save_freq_vec_env,
    checkpoint_timestep_interval,
    find_latest_checkpoint,
    is_valid_checkpoint,
    read_latest_pointer,
    resolve_resume_path,
    write_latest_pointer,
    zip_path,
)


def _make_fake_ckpt(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("data", "{}")
        zf.writestr("policy.pth", "x")


def test_is_valid_checkpoint(tmp_path: Path) -> None:
    good = tmp_path / "ppo_re1_100_steps.zip"
    bad = tmp_path / "ppo_re1_final.zip"
    _make_fake_ckpt(good)
    bad.write_bytes(b"not a zip")
    assert is_valid_checkpoint(good)
    assert not is_valid_checkpoint(bad)


def test_find_latest_by_steps(tmp_path: Path) -> None:
    _make_fake_ckpt(tmp_path / "ppo_re1_100_steps.zip")
    _make_fake_ckpt(tmp_path / "ppo_re1_500_steps.zip")
    latest = find_latest_checkpoint(tmp_path)
    assert latest is not None
    assert latest.name == "ppo_re1_500_steps.zip"


def test_resolve_resume_prefers_newest_over_stale_final(tmp_path: Path) -> None:
    import os
    import time

    ckpt_dir = tmp_path / "data" / "checkpoints"
    old = ckpt_dir / "ppo_re1_100_steps.zip"
    new = ckpt_dir / "ppo_re1_200_steps.zip"
    _make_fake_ckpt(old)
    _make_fake_ckpt(new)
    write_latest_pointer(ckpt_dir, new, steps=200)
    alias = tmp_path / "data" / "ppo_re1_final.zip"
    _make_fake_ckpt(alias)
    # Make the numbered checkpoint clearly newer than the alias.
    time.sleep(0.02)
    os.utime(new, None)

    resolved = resolve_resume_path(None, project_root=tmp_path, ckpt_dir=ckpt_dir)
    assert resolved == new.resolve()


def test_resolve_resume_falls_back_from_corrupt_alias(tmp_path: Path) -> None:
    ckpt_dir = tmp_path / "data" / "checkpoints"
    good = ckpt_dir / "ppo_re1_900_steps.zip"
    _make_fake_ckpt(good)
    write_latest_pointer(ckpt_dir, good, steps=900)
    alias = tmp_path / "data" / "ppo_re1_final.zip"
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.write_bytes(b"truncated")
    resolved = resolve_resume_path(
        alias,
        project_root=tmp_path,
        ckpt_dir=ckpt_dir,
    )
    assert resolved == good.resolve()


def test_resolve_resume_named_run_ignores_global_final(tmp_path: Path) -> None:
    run_dir = tmp_path / "data" / "checkpoints" / "explore_v3"
    run_ckpt = run_dir / "ppo_re1_400_steps.zip"
    _make_fake_ckpt(run_ckpt)
    write_latest_pointer(run_dir, run_ckpt, steps=400)
    global_final = tmp_path / "data" / "ppo_re1_final.zip"
    _make_fake_ckpt(global_final)

    resolved = resolve_resume_path(None, project_root=tmp_path, ckpt_dir=run_dir)
    assert resolved == run_ckpt.resolve()
    assert resolved != global_final.resolve()


def test_latest_pointer_roundtrip(tmp_path: Path) -> None:
    ckpt = tmp_path / "ppo_re1_42_steps.zip"
    _make_fake_ckpt(ckpt)
    write_latest_pointer(tmp_path, ckpt, steps=42)
    ptr = read_latest_pointer(tmp_path)
    assert ptr is not None
    assert ptr["steps"] == 42
    assert zip_path(ptr["path"]).name == ckpt.name


def test_checkpoint_interval_scales_with_n_envs() -> None:
    assert checkpoint_timestep_interval(20) == 40_000
    assert checkpoint_timestep_interval(12) == 24_000
    assert checkpoint_save_freq_vec_env(20) == 2_000
    assert checkpoint_save_freq_vec_env(12) == 2_000
