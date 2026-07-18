"""Per-step memory/reward diag for a single fleet env (pking top-right).

Enable with ``RE1_STEP_DIAG_PORT=<port>`` (e.g. 5759). Optional:
``RE1_STEP_DIAG_LOG`` overrides the fixed default path
``data/logs/pking_top_right_memlog.jsonl``.

On first open for a process: truncate the file in place (``\"w\"``), write a
``RUN_START`` banner, then append. Never unlink — so ``Get-Content -Wait``
stays attached across worker restarts.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from re1_rl.action_mask import (
    ATTACK_ACTION,
    N_SELECT_SLOT,
    SELECT_SLOT_BASE,
)
from re1_rl.knife_macro import read_knife_hooks

# Fixed default — no timestamps, same path every run.
DEFAULT_LOG_PATH = Path("data/logs/pking_top_right_memlog.jsonl")

_LOCK = threading.Lock()
_OPENED_PATHS: set[str] = set()


def diag_port_filter() -> int | None:
    """Return the single port that should log, or None if disabled."""
    raw = os.environ.get("RE1_STEP_DIAG_PORT", "").strip()
    if not raw:
        # Master switch alone is not enough — require an explicit port filter
        # so we never accidentally log all 20 envs.
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def diag_enabled_for_port(port: Any) -> bool:
    want = diag_port_filter()
    if want is None:
        return False
    try:
        return int(port) == want
    except (TypeError, ValueError):
        return False


def resolve_log_path(project_root: Path | None = None) -> Path:
    override = os.environ.get("RE1_STEP_DIAG_LOG", "").strip()
    if override:
        p = Path(override)
    else:
        p = DEFAULT_LOG_PATH
    if not p.is_absolute() and project_root is not None:
        p = Path(project_root) / p
    return p


def _ensure_run_start(path: Path, *, port: Any, meta: dict[str, Any] | None = None) -> None:
    """Truncate once per process for this path; write RUN_START; never unlink."""
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        key = str(path.resolve())
        if key in _OPENED_PATHS:
            return
        # Truncate in place ("w") — do not delete/unlink.
        with path.open("w", encoding="utf-8", newline="\n") as f:
            banner = {"run_start": True}
            if meta:
                # Keep optional note only; no ts/port/rank/event clutter.
                note = meta.get("note")
                if note:
                    banner["note"] = note
            f.write(json.dumps(banner, separators=(",", ":")) + "\n")
            f.flush()
        _OPENED_PATHS.add(key)


def _append_line(path: Path, obj: dict[str, Any]) -> None:
    with _LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(obj, separators=(",", ":")) + "\n")
            f.flush()


def _inventory_names(slots: Any) -> list[str]:
    """Human-readable inventory names only (no slot index / qty)."""
    out: list[str] = []
    for slot in slots or []:
        if isinstance(slot, (list, tuple)) and len(slot) >= 2:
            name, qty = slot[0], slot[1]
            if not name or name in ("", "empty", None):
                continue
            if int(qty) <= 0 and str(name).startswith("unknown"):
                continue
            out.append(str(name))
        elif isinstance(slot, str) and slot:
            out.append(slot)
    return out


def _slot_name(slots: Any, i: int) -> str | None:
    if not slots or i < 0 or i >= len(slots):
        return None
    slot = slots[i]
    if isinstance(slot, (list, tuple)) and len(slot) >= 1:
        name = slot[0]
        if not name or name in ("", "empty", None):
            return None
        return str(name)
    if isinstance(slot, str) and slot:
        return slot
    return None


def _mask_use_slot_names(mask: Any, inventory_slots: Any) -> list[str]:
    """Legal USE select_slot picks as inventory item names (not slot ids)."""
    if mask is None:
        return []
    names: list[str] = []
    n = len(mask)
    for i in range(N_SELECT_SLOT):
        idx = SELECT_SLOT_BASE + i
        if idx < n and bool(mask[idx]):
            name = _slot_name(inventory_slots, i)
            names.append(name if name else f"slot_{i}")
    return names


# Log individual reward channels at/above this absolute magnitude (with source).
BIG_REWARD_ABS = 0.1


def _big_reward_events(breakdown: Any) -> list[dict[str, Any]]:
    """Channels with |r| >= BIG_REWARD_ABS, as compact {src, r} rows."""
    if not isinstance(breakdown, dict):
        return []
    out: list[dict[str, Any]] = []
    for src, raw in breakdown.items():
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if abs(val) + 1e-12 >= BIG_REWARD_ABS:
            out.append({"src": str(src), "r": round(val, 5)})
    out.sort(key=lambda x: (-abs(float(x["r"])), str(x["src"])))
    return out


class StepDiagLogger:
    """Append-only JSONL step logger for one env port."""

    def __init__(
        self,
        port: Any,
        *,
        project_root: Path | None = None,
        rank: int | None = None,
        machine_name: str | None = None,
    ) -> None:
        self.port = port
        self.rank = rank
        self.machine_name = machine_name
        self.path = resolve_log_path(project_root)
        self.ep_return = 0.0
        self.ep_idx = 0
        self._step_i = 0
        self._pending_value: float | None = None
        _ensure_run_start(
            self.path,
            port=port,
            meta={
                "rank": rank,
                "machine": machine_name,
                "note": "pking top-right memlog; truncate-in-place on process start",
            },
        )

    def note_value(self, value: float) -> None:
        """Stash critic V for the upcoming env.step (set by actor before step)."""
        self._pending_value = float(value)

    def reset_episode(self) -> None:
        self.ep_return = 0.0
        self._step_i = 0
        self.ep_idx += 1
        self._pending_value = None

    def log_step(
        self,
        *,
        reward: float,
        terminated: bool,
        truncated: bool,
        action_masks: Any,
        inventory_slots: Any,
        hooks: tuple[int, int, int] | None,
        info: dict[str, Any] | None = None,
        action: int | None = None,
        action_name: str | None = None,
        value: float | None = None,
    ) -> None:
        self._step_i += 1
        step_r = float(reward)
        self.ep_return += step_r
        info = info or {}
        mask = action_masks
        # attack / attack_up / attack_down share one mask bit in action_mask.py.
        # knife_swing remains a knife-only crouch alias — not logged separately.
        attack_legal = bool(mask[ATTACK_ACTION]) if mask is not None and len(mask) > ATTACK_ACTION else False
        use_slots = _mask_use_slot_names(mask, inventory_slots)
        del hooks  # accepted for call-site stability; not logged

        if value is None:
            value = self._pending_value
        self._pending_value = None

        # Human-readable action name only (never the PPO discrete slot index).
        aname = action_name or info.get("action_name")
        if not aname and action is not None:
            aname = f"unknown_action_{action}"

        rooms = info.get("visited_rooms")
        if rooms is None:
            rooms = []
        else:
            rooms = sorted({str(r) for r in rooms if r})

        big = _big_reward_events(info.get("reward_breakdown"))

        row: dict[str, Any] = {
            "ep": self.ep_idx,
            "step": self._step_i,
            "reward": round(step_r, 5),
            "ep_return_cum": round(self.ep_return, 5),
            "action": aname,
            "value": None if value is None else round(float(value), 5),
            "inventory": _inventory_names(inventory_slots),
            "rooms": rooms,
            "attack_legal": attack_legal,
            "use_slots_legal": use_slots,
        }
        if big:
            row["big_rewards"] = big
        if terminated or truncated:
            row["ep_return_total"] = round(self.ep_return, 5)
        _append_line(self.path, row)


def try_make_logger(
    port: Any,
    *,
    project_root: Path | None = None,
    rank: int | None = None,
    machine_name: str | None = None,
) -> StepDiagLogger | None:
    if not diag_enabled_for_port(port):
        return None
    return StepDiagLogger(
        port,
        project_root=project_root,
        rank=rank,
        machine_name=machine_name,
    )


def read_hooks_safe(bridge: Any) -> tuple[int, int, int] | None:
    if bridge is None:
        return None
    try:
        return read_knife_hooks(bridge)
    except (OSError, RuntimeError, AttributeError, TypeError, ValueError):
        return None
