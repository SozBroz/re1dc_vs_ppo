"""Recapture the exact per-step action distribution of a minted PL cell.

A mint stores three ingredients (all written automatically at capture):

- ``states/planner_loyal/cells/plNN/meta.json`` → ``mint_policy`` pointer
  (policy_version, inference temperature, mod_drop flag),
- ``states/planner_loyal/cells/plNN/leg_policy.npz`` → live per-step
  ``masked_probs`` / ``action_mask`` / ``action`` (the ground truth),
- ``data/mint_policies/weights/v<V>.pt`` + ``data/mint_policies/obs/pl<NN>_v<V>.npz``
  (gitignored) → the exact weights that ran the episode + the raw pre-action
  obs for every step.

This script re-runs the identical forward (same ``InferencePolicy`` code the
workers use, same temperature, same masks) and diffs the recomputed
distribution against the live tape. Agreement proves you can recover the
exact % chance for each action at each PPO step from the archived weights.

Usage:
    python scripts/recompute_mint_odds.py --cell pl09
    python scripts/recompute_mint_odds.py --cell pl09 --dump-csv out.csv --tol 1e-4

Exit codes: 0 = recaptured within tolerance, 1 = missing artifacts/usage,
2 = distributions diverge (lists worst steps).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _load_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        return {k: np.asarray(data[k]) for k in data.files}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cell", required=True, help="e.g. pl09")
    ap.add_argument(
        "--root",
        default=str(PROJECT_ROOT),
        help="project root (default: repo root)",
    )
    ap.add_argument(
        "--tol",
        type=float,
        default=1e-5,
        help="max abs prob diff allowed (default 1e-5)",
    )
    ap.add_argument(
        "--dump-csv",
        default="",
        help="write per-step recomputed distributions to CSV (default: under data/mint_policies/recapture/)",
    )
    ap.add_argument("--device", default="cpu", help="torch device (default cpu)")
    args = ap.parse_args()

    root = Path(args.root)
    name = str(args.cell).strip().lower()
    if not name.startswith("pl"):
        print(f"bad --cell {args.cell!r} (want plNN)", flush=True)
        return 1
    try:
        slot = int(name[2:])
    except ValueError:
        print(f"bad --cell {args.cell!r} (want plNN)", flush=True)
        return 1
    cell_dir = root / "states" / "planner_loyal" / "cells" / f"pl{slot:02d}"
    meta_path = cell_dir / "meta.json"
    policy_path = cell_dir / "leg_policy.npz"
    if not meta_path.is_file():
        print(f"missing {meta_path}", flush=True)
        return 1
    if not policy_path.is_file():
        print(f"missing {policy_path} (cell minted thin / pre-tape?)", flush=True)
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    pointer = dict(meta.get("mint_policy") or {})
    version = int(pointer.get("policy_version") or 0)
    temperature = float(pointer.get("inference_temperature") or 1.0)
    if pointer.get("mod_drop"):
        print(
            "mint ran with ModDrop ON (per-step presence masks are not stored); "
            "exact offline recapture unsupported for this cell.",
            flush=True,
        )
        return 1
    if version <= 0:
        print(
            f"{cell_dir.name}: meta mint_policy.policy_version={version} "
            "(cell predates weight snapshots; re-mint to recapture)",
            flush=True,
        )
        return 1

    tape = _load_npz(policy_path)
    tape_version = int(np.asarray(tape.get("policy_version", 0)).reshape(-1)[0])
    live_probs = np.asarray(tape["masked_probs"], dtype=np.float64)
    masks = np.asarray(tape["action_mask"], dtype=np.bool_)
    actions = np.asarray(tape["action"], dtype=np.int64).reshape(-1)
    n_steps = int(live_probs.shape[0])
    if tape_version != version:
        print(
            f"warning: tape policy_version={tape_version} != meta pointer v{version}; "
            f"using tape version for weights",
            flush=True,
        )
        version = tape_version

    from re1_rl.distributed.weight_archive import find_weight_file

    weights_file = find_weight_file(root, version)
    if weights_file is None:
        print(
            f"missing archived weights for v{version} "
            f"(data/mint_policies/weights/v{version}.pt pruned or never pulled here)",
            flush=True,
        )
        return 1
    obs_path = (
        root / "data" / "mint_policies" / "obs" / f"pl{slot:02d}_v{version}.npz"
    )
    if not obs_path.is_file():
        print(
            f"missing {obs_path} (cell predates obs dumps; re-mint to recapture)",
            flush=True,
        )
        return 1
    obs_npz = _load_npz(obs_path)
    obs_keys = sorted(k[4:] for k in obs_npz if k.startswith("obs_"))
    if not obs_keys:
        print(f"{obs_path}: no obs_* arrays", flush=True)
        return 1
    obs_batch = {k: np.asarray(obs_npz[f"obs_{k}"]) for k in obs_keys}
    n_obs = int(next(iter(obs_batch.values())).shape[0])
    if n_obs != n_steps:
        print(
            f"obs steps {n_obs} != tape steps {n_steps} "
            "(mid-episode weight pull mixed versions?)",
            flush=True,
        )
        # Recapture the overlapping prefix; the mismatch itself is the signal.
        n_steps = min(n_obs, n_steps)
        live_probs = live_probs[:n_steps]
        masks = masks[:n_steps]
        actions = actions[:n_steps]
        obs_batch = {k: v[:n_steps] for k, v in obs_batch.items()}

    from re1_rl.distributed.inference_policy import InferencePolicy
    from re1_rl.distributed.spaces import make_re1_policy_spaces
    from re1_rl.distributed.weights import state_dict_from_policy_bytes

    obs_space, act_space = make_re1_policy_spaces()
    policy = InferencePolicy(obs_space, act_space, args.device, temperature=temperature)
    raw = weights_file.read_bytes()
    policy.load_from_bytes(raw, version)
    # Sanity: archived bytes must deserialize to the recorded version's dict.
    _ = state_dict_from_policy_bytes(raw)

    n_actions_live = int(live_probs.shape[1])
    n_actions_net = int(act_space.n)
    if n_actions_live != n_actions_net:
        print(
            f"action dim mismatch: tape {n_actions_live} vs net {n_actions_net}",
            flush=True,
        )
        return 1
    _, _, _, _, recomputed = policy.predict_masked_batch_with_diagnostics(
        obs_batch, masks
    )
    recomputed = np.asarray(recomputed, dtype=np.float64)
    absdiff = np.abs(recomputed - live_probs)
    worst_step = int(np.argmax(absdiff.max(axis=1)))
    worst = float(absdiff.max())
    mean = float(absdiff.mean())
    taken_live = live_probs[np.arange(n_steps), actions]
    taken_re = recomputed[np.arange(n_steps), actions]

    csv_path = args.dump_csv.strip() or str(
        root / "data" / "mint_policies" / "recapture" / f"pl{slot:02d}_v{version}.csv"
    )
    csv_file = Path(csv_path)
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    with csv_file.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["step", "taken_action", "taken_live", "taken_recomputed"]
            + [f"p{a}" for a in range(n_actions_net)]
        )
        for i in range(n_steps):
            writer.writerow(
                [i, int(actions[i]), f"{taken_live[i]:.6f}", f"{taken_re[i]:.6f}"]
                + [f"{p:.6f}" for p in recomputed[i]]
            )

    print(
        f"[{cell_dir.name} v{version}] steps={n_steps} temp={temperature} "
        f"max_abs_diff={worst:.2e} mean_abs_diff={mean:.2e} worst_step={worst_step} "
        f"csv={csv_file}",
        flush=True,
    )
    if worst > float(args.tol):
        bad = np.where(absdiff.max(axis=1) > float(args.tol))[0]
        print(
            f"DIVERGE: {len(bad)} steps over tol {float(args.tol):.1e} "
            f"(first {bad[:10].tolist()}); possible mid-episode weight pull "
            f"or device/dtype drift (recompute ran on {args.device})",
            flush=True,
        )
        return 2
    print("EXACT: recomputed distributions match the live mint tape", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
