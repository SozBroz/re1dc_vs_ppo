"""Mint weight snapshots + offline odds recapture."""

import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

from re1_rl.distributed import weight_archive as wa
from re1_rl.footage_trace import FootageTraceBuffer, new_footage_trace_buffer
from re1_rl.planner_loyal_cells import _mint_policy_pointer

ROOT = Path(__file__).resolve().parents[1]


def test_archive_roundtrip(tmp_path) -> None:
    blob = b"fake-policy-bytes-" + bytes(range(64))
    assert wa.note_policy_version_weights(tmp_path, 7, blob) is True
    found = wa.find_weight_file(tmp_path, 7)
    assert found is not None and found.read_bytes() == blob
    # Idempotent second write.
    assert wa.note_policy_version_weights(tmp_path, 7, blob) is True
    assert wa.find_weight_file(tmp_path, 999) is None
    assert wa.note_policy_version_weights(tmp_path, 0, blob) is False
    assert wa.note_policy_version_weights(tmp_path, 7, b"") is False


def _write_meta(tmp_path: Path, slot: str, version: int) -> None:
    cell = tmp_path / "states" / "planner_loyal" / "cells" / slot
    cell.mkdir(parents=True, exist_ok=True)
    (cell / "meta.json").write_text(
        json.dumps({"mint_policy": {"policy_version": version}}), encoding="utf-8"
    )


def test_prune_keeps_referenced_and_newest(tmp_path) -> None:
    _write_meta(tmp_path, "pl03", 3)
    for v in (1, 2, 3, 4, 5):
        assert wa.note_policy_version_weights(tmp_path, v, f"blob{v}".encode())
    stats = wa.prune_weight_archive(tmp_path)
    # v3 referenced; newest window keeps the rest here (<=8 files).
    assert stats["deleted"] == 0
    assert wa.find_weight_file(tmp_path, 3) is not None
    # Flood with unreferenced versions; old ones must fall out.
    for v in range(6, 6 + wa.KEEP_NEWEST_UNREFERENCED + 4):
        assert wa.note_policy_version_weights(tmp_path, v, f"blob{v}".encode())
    stats = wa.prune_weight_archive(tmp_path)
    assert stats["deleted"] >= 4
    assert wa.find_weight_file(tmp_path, 3) is not None
    assert wa.find_weight_file(tmp_path, 1) is None


def test_mint_policy_pointer_with_and_without_buf() -> None:
    buf = new_footage_trace_buffer()
    buf.policy_version = 42
    env = SimpleNamespace(_footage_trace=buf)
    ptr = _mint_policy_pointer(env)
    assert ptr["policy_version"] == 42
    assert ptr["inference_temperature"] == float(
        __import__("re1_rl.inference_config", fromlist=["x"]).inference_temperature_from_env()
    )
    assert ptr["mod_drop"] is False
    assert ptr["minted_at_unix"] > 0
    ptr2 = _mint_policy_pointer(SimpleNamespace())
    assert ptr2["policy_version"] == 0


def test_footage_obs_roundtrip(tmp_path) -> None:
    buf = FootageTraceBuffer()
    assert buf.write_obs(tmp_path / "o.npz") is None
    obs = {
        "frame": np.zeros((63, 84, 4), dtype=np.uint8),
        "proprio": np.ones(4, dtype=np.float32),
    }
    buf.append_obs(obs)
    obs["frame"][0, 0, 0] = 9  # mutate after: stash must hold a copy
    out = buf.write_obs(tmp_path / "o.npz")
    assert out is not None
    with np.load(out, allow_pickle=False) as data:
        assert data["n_steps"] == 1
        assert data["obs_frame"].shape == (1, 63, 84, 4)
        assert int(data["obs_frame"][0, 0, 0, 0]) == 0
        assert data["obs_proprio"].shape == (1, 4)


def _synthetic_cell(tmp_path: Path, policy, obs_space, version: int = 11):
    """Fake cell whose tape probs come from a real forward pass."""
    from re1_rl.distributed.weights import policy_bytes_from_state_dict

    n_steps, n_actions = 6, int(policy._model.action_space.n)
    rng = np.random.default_rng(0)
    obs_batch = {}
    for key, space in obs_space.spaces.items():
        shape = (n_steps, *space.shape)
        if space.dtype == np.uint8:
            obs_batch[key] = rng.integers(0, 256, size=shape).astype(np.uint8)
        else:
            obs_batch[key] = rng.standard_normal(shape).astype(np.float32)
    masks = np.ones((n_steps, n_actions), dtype=np.bool_)
    masks[:, 1::3] = False
    _, _, _, _, probs = policy.predict_masked_batch_with_diagnostics(
        obs_batch, masks
    )
    # Taken actions: argmax over legal probs mapped back to action ids.
    legal_idx = [np.flatnonzero(masks[i]) for i in range(n_steps)]
    actions = np.asarray(
        [int(legal_idx[i][int(np.argmax(probs[i][masks[i]]))]) for i in range(n_steps)],
        dtype=np.int64,
    )
    cell = tmp_path / "states" / "planner_loyal" / "cells" / "pl09"
    cell.mkdir(parents=True, exist_ok=True)
    (cell / "meta.json").write_text(
        json.dumps(
            {
                "checkpoint_index": 9,
                "mint_policy": {
                    "policy_version": version,
                    "inference_temperature": 1.0,
                    "mod_drop": False,
                },
            }
        ),
        encoding="utf-8",
    )
    np.savez_compressed(
        cell / "leg_policy.npz",
        schema_version=np.int16(1),
        policy_version=np.int32(version),
        action=actions.astype(np.int16),
        action_mask=masks,
        masked_probs=np.asarray(probs, dtype=np.float32),
    )
    wdir = tmp_path / "data" / "mint_policies" / "weights"
    wdir.mkdir(parents=True, exist_ok=True)
    state = {k: v.detach().cpu().clone() for k, v in policy._model.policy.state_dict().items()}
    (wdir / f"v{version}.pt").write_bytes(policy_bytes_from_state_dict(state))
    odir = tmp_path / "data" / "mint_policies" / "obs"
    odir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        odir / f"pl09_v{version}.npz",
        schema_version=np.int16(1),
        policy_version=np.int32(version),
        n_steps=np.int32(n_steps),
        **{f"obs_{k}": v for k, v in obs_batch.items()},
    )


def test_recompute_script_exact_on_synthetic_cell(tmp_path) -> None:
    from re1_rl.distributed.inference_policy import InferencePolicy
    from re1_rl.distributed.spaces import make_re1_policy_spaces

    torch.manual_seed(0)
    obs_space, act_space = make_re1_policy_spaces()
    policy = InferencePolicy(obs_space, act_space, "cpu", temperature=1.0)
    _synthetic_cell(tmp_path, policy, obs_space)
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "recompute_mint_odds.py"),
            "--cell",
            "pl09",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(ROOT),
    )
    assert "EXACT" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == 0


def test_recompute_script_missing_artifacts(tmp_path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "recompute_mint_odds.py"),
            "--cell",
            "pl99",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )
    assert proc.returncode == 1
