"""Equal held-out per-cell evaluation for Yawn rails atomic resets.

Samples **every available atomic cell equally**, independent of the training
reset distribution. Metrics are written to a dedicated JSONL (and optional TB
scalars under ``eval/yawn_cell/``) so they never overwrite training curves.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from re1_rl.training_metrics_log import append_training_record

EVAL_SCHEMA = "re1_yawn_rails_cell_eval_v1"
DEFAULT_EPISODES_PER_CELL = 4


def eval_jsonl_path(project_root: Path | str, *, run_name: str | None = None) -> Path:
    logs = Path(project_root) / "logs"
    if run_name:
        return logs / f"yawn_rails_cell_eval_{run_name}.jsonl"
    return logs / "yawn_rails_cell_eval.jsonl"


def eval_interval_updates_from_env(default: int = 0) -> int:
    """``RE1_YAWN_EVAL_INTERVAL_UPDATES`` — 0 disables learner-side equal-weight log."""
    raw = os.environ.get("RE1_YAWN_EVAL_INTERVAL_UPDATES", "").strip()
    if not raw:
        return int(default)
    try:
        return max(0, int(raw))
    except ValueError:
        return int(default)


@dataclass
class CellEvalBucket:
    cell_id: str
    cell_index: int
    episodes: int = 0
    successes: int = 0
    truncations: int = 0
    illegal_gates: int = 0
    deaths: int = 0
    other: int = 0
    lengths: list[int] = field(default_factory=list)
    success_lengths: list[int] = field(default_factory=list)
    wall_s: list[float] = field(default_factory=list)

    def observe(
        self,
        *,
        outcome: str | None,
        length: int,
        wall_s: float | None = None,
    ) -> None:
        self.episodes += 1
        self.lengths.append(int(length))
        if wall_s is not None:
            self.wall_s.append(float(wall_s))
        label = str(outcome or "other")
        if label == "checkpoint_success":
            self.successes += 1
            self.success_lengths.append(int(length))
        elif label in {"truncation", "stagnation"}:
            self.truncations += 1
        elif label == "main_hall_before_kenneth":
            self.illegal_gates += 1
        elif label == "death":
            self.deaths += 1
        else:
            self.other += 1

    def summary(self) -> dict[str, Any]:
        n = max(1, self.episodes)
        mean_len = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        mean_succ_len = (
            sum(self.success_lengths) / len(self.success_lengths)
            if self.success_lengths
            else None
        )
        mean_wall = sum(self.wall_s) / len(self.wall_s) if self.wall_s else None
        success_rate = self.successes / n
        steps = sum(self.lengths)
        return {
            "cell_id": self.cell_id,
            "cell_index": int(self.cell_index),
            "episodes": int(self.episodes),
            "success_rate": float(success_rate),
            "truncation_rate": float(self.truncations / n),
            "illegal_gate_rate": float(self.illegal_gates / n),
            "death_rate": float(self.deaths / n),
            "mean_episode_length": float(mean_len),
            "mean_success_length": mean_succ_len,
            "mean_wall_s": mean_wall,
            # Efficiency proxies: successes per env-step / per wall-second.
            "success_per_step": float(self.successes / steps) if steps else 0.0,
            "success_per_wall_s": (
                float(self.successes / sum(self.wall_s)) if self.wall_s else None
            ),
        }


def list_eval_cells(
    project_root: Path | str,
    stage: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return loadable atomic cells (+ synthetic route_initial) for equal eval."""
    from re1_rl.yawn_rails import iter_loadable_cells

    root = Path(project_root)
    cells = [
        {
            "checkpoint_index": -1,
            "cell_id": "route_initial",
            "source": "route_initial",
        }
    ]
    for row in iter_loadable_cells(root, stage):
        idx = int(row["checkpoint_index"])
        cells.append({
            **dict(row),
            "cell_id": f"cp{idx:02d}",
            "source": "route_cell",
        })
    return cells


def equal_weight_eval_schedule(
    cells: list[dict[str, Any]],
    *,
    episodes_per_cell: int = DEFAULT_EPISODES_PER_CELL,
) -> list[dict[str, Any]]:
    """Round-robin schedule: every cell appears equally often."""
    if episodes_per_cell < 1:
        raise ValueError("episodes_per_cell must be >= 1")
    schedule: list[dict[str, Any]] = []
    for ep in range(int(episodes_per_cell)):
        for cell in cells:
            schedule.append({
                "episode_index": len(schedule),
                "repeat": ep,
                "cell_id": cell["cell_id"],
                "checkpoint_index": int(cell["checkpoint_index"]),
                "route_start_index": int(cell["checkpoint_index"]) + 1,
                "leg_span": 1,
                "reset_source": cell.get("source") or (
                    "route_initial"
                    if int(cell["checkpoint_index"]) < 0
                    else "route_cell"
                ),
                "held_out_eval": True,
                "state_path": cell.get("state_path"),
                "sidecar_path": cell.get("sidecar_path"),
            })
    return schedule


def reset_options_for_eval_slot(
    slot: dict[str, Any],
) -> dict[str, Any]:
    """Build ``env.reset(options=...)`` for one equal-weight eval slot."""
    opts: dict[str, Any] = {
        "route_start_index": int(slot["route_start_index"]),
        "leg_span": int(slot.get("leg_span") or 1),
        "reset_source": str(slot.get("reset_source") or "route_cell"),
        "held_out_eval": True,
    }
    if opts["route_start_index"] > 0 and slot.get("state_path") and slot.get("sidecar_path"):
        opts["pb_bundle"] = {
            "state_path": str(slot["state_path"]),
            "sidecar_path": str(slot["sidecar_path"]),
            "source": "yawn_rails",
        }
    return opts


def classify_episode_info(info: dict[str, Any]) -> tuple[str, int]:
    """Return ``(outcome, length)`` from a Monitor / slim episode-end info."""
    ep = info.get("episode") or {}
    length = int(ep.get("l") or info.get("episode_length") or 0)
    outcome = info.get("episode_outcome") or info.get("episode_failure")
    if outcome in {
        "checkpoint_success",
        "main_hall_before_kenneth",
        "truncation",
        "stagnation",
        "death",
    }:
        return str(outcome), length
    bd = info.get("reward_breakdown") or {}
    if float(bd.get("checkpoint_success", 0.0) or 0.0) > 0.0:
        return "checkpoint_success", length
    if info.get("episode_failure") == "main_hall_before_kenneth":
        return "main_hall_before_kenneth", length
    return str(outcome or "other"), length


def cell_id_from_info(info: dict[str, Any]) -> tuple[str, int] | None:
    if info.get("rails_cell_id") is not None and info.get("rails_cell_index") is not None:
        return str(info["rails_cell_id"]), int(info["rails_cell_index"])
    start = info.get("route_start_index")
    if start is None:
        return None
    idx = int(start) - 1
    return ("route_initial" if idx < 0 else f"cp{idx:02d}"), idx


def aggregate_cell_metrics(
    infos: Iterable[dict[str, Any]],
    *,
    held_out_only: bool = False,
) -> dict[str, CellEvalBucket]:
    buckets: dict[str, CellEvalBucket] = {}
    for info in infos:
        if not info:
            continue
        # Prefer Monitor episode ends; also accept explicit outcome tags.
        if "episode" not in info and info.get("episode_outcome") is None:
            continue
        if held_out_only and not info.get("held_out_eval"):
            continue
        ident = cell_id_from_info(info)
        if ident is None:
            continue
        cell_id, cell_index = ident
        bucket = buckets.get(cell_id)
        if bucket is None:
            bucket = CellEvalBucket(cell_id=cell_id, cell_index=cell_index)
            buckets[cell_id] = bucket
        outcome, length = classify_episode_info(info)
        wall = info.get("episode_wall_s")
        bucket.observe(
            outcome=outcome,
            length=length,
            wall_s=float(wall) if wall is not None else None,
        )
    return buckets


def macro_average_success(buckets: dict[str, CellEvalBucket]) -> float | None:
    if not buckets:
        return None
    rates = [b.successes / b.episodes for b in buckets.values() if b.episodes > 0]
    if not rates:
        return None
    return float(sum(rates) / len(rates))


def worst_decile_success(buckets: dict[str, CellEvalBucket]) -> float | None:
    rates = sorted(
        b.successes / b.episodes for b in buckets.values() if b.episodes > 0
    )
    if not rates:
        return None
    # Mean of the worst 10% of cells (at least one cell).
    k = max(1, int(math.ceil(0.1 * len(rates))))
    worst = rates[:k]
    return float(sum(worst) / len(worst))


def build_eval_report(
    buckets: dict[str, CellEvalBucket],
    *,
    policy_version: int | None = None,
    num_timesteps: int | None = None,
    equal_weight: bool = True,
    source: str = "held_out",
) -> dict[str, Any]:
    cells = [buckets[k].summary() for k in sorted(buckets)]
    return {
        "schema": EVAL_SCHEMA,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "equal_weight": bool(equal_weight),
        "policy_version": policy_version,
        "num_timesteps": num_timesteps,
        "n_cells": len(cells),
        "macro_avg_success": macro_average_success(buckets),
        "worst_decile_success": worst_decile_success(buckets),
        "cells": cells,
    }


def eval_scalars_for_logger(report: dict[str, Any]) -> dict[str, float]:
    """TB-friendly scalars under ``eval/yawn_cell/`` (never training tags)."""
    out: dict[str, float] = {}
    macro = report.get("macro_avg_success")
    worst = report.get("worst_decile_success")
    if macro is not None:
        out["eval/yawn_cell/macro_avg_success"] = float(macro)
    if worst is not None:
        out["eval/yawn_cell/worst_decile_success"] = float(worst)
    out["eval/yawn_cell/n_cells"] = float(report.get("n_cells") or 0)
    for row in report.get("cells") or []:
        cid = str(row.get("cell_id") or "unknown")
        out[f"eval/yawn_cell/{cid}/success_rate"] = float(row.get("success_rate") or 0.0)
        out[f"eval/yawn_cell/{cid}/truncation_rate"] = float(
            row.get("truncation_rate") or 0.0
        )
        out[f"eval/yawn_cell/{cid}/illegal_gate_rate"] = float(
            row.get("illegal_gate_rate") or 0.0
        )
        out[f"eval/yawn_cell/{cid}/mean_episode_length"] = float(
            row.get("mean_episode_length") or 0.0
        )
    return out


def append_eval_report(path: Path, report: dict[str, Any]) -> None:
    append_training_record(path, report)


def maybe_log_equal_weight_from_infos(
    project_root: Path | str,
    infos: list[dict[str, Any]],
    *,
    update: int,
    policy_version: int | None = None,
    num_timesteps: int | None = None,
    model: Any | None = None,
    run_name: str | None = None,
) -> dict[str, Any] | None:
    """Learner side-job: equal-weight macros from episode infos when interval hits."""
    interval = eval_interval_updates_from_env(0)
    if interval <= 0 or update <= 0 or (update % interval) != 0:
        return None
    # Prefer true held-out tagged episodes; fall back to equal-weight re-agg of all rails.
    held = aggregate_cell_metrics(infos, held_out_only=True)
    if held:
        report = build_eval_report(
            held,
            policy_version=policy_version,
            num_timesteps=num_timesteps,
            equal_weight=True,
            source="held_out",
        )
    else:
        train_buckets = aggregate_cell_metrics(infos, held_out_only=False)
        if not train_buckets:
            return None
        report = build_eval_report(
            train_buckets,
            policy_version=policy_version,
            num_timesteps=num_timesteps,
            equal_weight=True,
            source="train_equal_weight_macro",
        )
    path = eval_jsonl_path(project_root, run_name=run_name)
    append_eval_report(path, report)
    scalars = eval_scalars_for_logger(report)
    logger = getattr(model, "logger", None) if model is not None else None
    if logger is not None:
        for key, value in scalars.items():
            logger.record(key, float(value))
        if num_timesteps is not None:
            logger.dump(step=int(num_timesteps))
    return report


def build_dry_run_plan(
    project_root: Path | str,
    stage: dict[str, Any],
    *,
    episodes_per_cell: int,
    ckpt: str | None,
    seed: int,
) -> dict[str, Any]:
    cells = list_eval_cells(project_root, stage)
    schedule = equal_weight_eval_schedule(cells, episodes_per_cell=episodes_per_cell)
    return {
        "schema": EVAL_SCHEMA,
        "mode": "dry_run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ckpt": ckpt,
        "seed": int(seed),
        "episodes_per_cell": int(episodes_per_cell),
        "n_cells": len(cells),
        "total_episodes": len(schedule),
        "cells": [
            {
                "cell_id": c["cell_id"],
                "checkpoint_index": c["checkpoint_index"],
                "state_path": c.get("state_path"),
                "sidecar_path": c.get("sidecar_path"),
            }
            for c in cells
        ],
        "schedule_head": schedule[: min(12, len(schedule))],
        "metrics": [
            "success_rate",
            "truncation_rate",
            "illegal_gate_rate",
            "mean_episode_length",
            "mean_success_length",
            "success_per_step",
            "macro_avg_success",
            "worst_decile_success",
        ],
        "note": (
            "Equal per-cell sampling independent of training reset distribution. "
            "JSONL/TB tags use eval/yawn_cell/* and logs/yawn_rails_cell_eval*.jsonl."
        ),
        "wall_clock_marker": time.time(),
    }
