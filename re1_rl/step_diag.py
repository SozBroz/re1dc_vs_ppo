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
    EQUIP_ACTION,
    N_SELECT_SLOT,
    SELECT_SLOT_BASE,
)
from re1_rl.knife_macro import read_knife_hooks
from re1_rl.memory_map import ITEM_IDS

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


def _inventory_weapon_slots(inventory_slots: Any) -> list[dict[str, Any]]:
    """Compact weapon layout: slot index, item id, name."""
    from re1_rl.weapon_equip import EQUIPPABLE_WEAPON_IDS

    out: list[dict[str, Any]] = []
    for i, slot in enumerate(inventory_slots or []):
        iid = None
        name = None
        if isinstance(slot, (list, tuple)) and slot:
            # Prefer id if present as int; else resolve name→skip id.
            if isinstance(slot[0], int) or (
                isinstance(slot[0], str) and str(slot[0]).isdigit()
            ):
                iid = int(slot[0])
            else:
                name = str(slot[0]) if slot[0] else None
        elif isinstance(slot, str) and slot:
            name = slot
        if iid is not None:
            if iid not in EQUIPPABLE_WEAPON_IDS:
                continue
            name = ITEM_IDS.get(iid, name or f"id_{iid}")
        elif name:
            # Name-only inventory from state — keep known weapons by name.
            low = name.lower()
            if low not in {
                "knife",
                "beretta",
                "shotgun",
                "colt_python",
                "colt_python_dumdum",
                "flamethrower",
                "bazooka_acid",
                "bazooka_explosive",
                "bazooka_flame",
                "rocket_launcher",
            }:
                continue
        else:
            continue
        row: dict[str, Any] = {"s": int(i), "n": str(name)}
        if iid is not None:
            row["id"] = int(iid)
        out.append(row)
    return out


def _legal_equip_select_slots(mask: Any, inventory_slots: Any) -> list[dict[str, Any]]:
    """Legal equip-phase select targets with slot index + name."""
    if mask is None:
        return []
    out: list[dict[str, Any]] = []
    n = len(mask)
    for i in range(N_SELECT_SLOT):
        idx = SELECT_SLOT_BASE + i
        if idx < n and bool(mask[idx]):
            name = _slot_name(inventory_slots, i) or f"slot_{i}"
            out.append({"s": int(i), "n": str(name)})
    return out


def _equip_policy_probs(masked_probs: Any, mask: Any) -> dict[str, Any] | None:
    """Post-mask probabilities for EQUIP open + legal select_slot picks."""
    if masked_probs is None:
        return None
    try:
        probs = list(masked_probs)
    except TypeError:
        return None
    if len(probs) <= EQUIP_ACTION:
        return None
    out: dict[str, Any] = {
        "p_equip": round(float(probs[EQUIP_ACTION]), 5),
    }
    if mask is not None and EQUIP_ACTION < len(mask):
        out["open_legal"] = bool(mask[EQUIP_ACTION])
    slots: dict[str, float] = {}
    n = len(probs)
    for i in range(N_SELECT_SLOT):
        idx = SELECT_SLOT_BASE + i
        if idx >= n:
            break
        if mask is not None and (idx >= len(mask) or not bool(mask[idx])):
            continue
        slots[str(i)] = round(float(probs[idx]), 5)
    if slots:
        out["p_select"] = slots
    return out


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


def _knife_budget_row(info: dict[str, Any]) -> dict[str, Any] | None:
    """Per-phase crouch-knife frame budget for memlog (all outcomes)."""
    report = info.get("knife_anim_report")
    if not isinstance(report, dict):
        return None
    budget = report.get("phase_budget")
    if not isinstance(budget, dict):
        return None
    row: dict[str, Any] = {
        "outcome": str(report.get("outcome") or ""),
        "total": int(budget.get("total") or report.get("macro_frames") or 0),
        "expect": int(budget.get("expect_total") or 0),
        "ram": int(budget.get("ram_gated") or 0),
        "link": int(budget.get("link_aim") or 0),
        "settle": int(budget.get("settle") or 0),
        "aim": int(budget.get("aim") or 0),
        "swing": int(budget.get("swing") or 0),
        "rec": int(budget.get("recovery") or 0),
        "overhead": int(budget.get("overhead") or 0),
        "aim_try": int(budget.get("aim_attempts") or 0),
        "precook": bool(budget.get("aim_precooked")),
        "pre_label": budget.get("pre_label"),
        "aim_top": budget.get("aim_top"),
        "rec_top": budget.get("recovery_top"),
    }
    return row


def _knife_fail_row(info: dict[str, Any]) -> dict[str, Any] | None:
    """Compact knife macro failure pattern for memlog (one env port)."""
    report = info.get("knife_anim_report")
    if not isinstance(report, dict):
        return None
    outcome = str(report.get("outcome") or "")
    if outcome in ("", "ok"):
        return None
    fp = report.get("failure_pattern")
    if not isinstance(fp, dict):
        return None
    pre = report.get("pre_state") if isinstance(report.get("pre_state"), dict) else {}
    row: dict[str, Any] = {
        "outcome": outcome,
        "macro_frames": int(report.get("macro_frames") or 0),
        "died": bool(report.get("died")),
        "pre_label": pre.get("label"),
        "pre_hooks": pre.get("hooks"),
        "issues": list(report.get("issues") or [])[:3],
    }
    for key in (
        "fail_phase",
        "fail_label",
        "fail_hooks",
        "aim_phase_frames",
        "settle_phase_frames",
        "aim_max_ready_streak",
        "saw_crouch_aim",
        "saw_swing_anim",
        "aim_label_counts",
        "settle_label_counts",
        "swing_label_counts",
        "label_counts",
    ):
        if key in fp:
            row[key] = fp[key]
    return row


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
        self._pending_masked_probs: Any | None = None
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

    def note_masked_probs(self, masked_probs: Any) -> None:
        """Stash post-mask action probs for the upcoming env.step."""
        self._pending_masked_probs = masked_probs

    def reset_episode(self) -> None:
        self.ep_return = 0.0
        self._step_i = 0
        self.ep_idx += 1
        self._pending_value = None
        self._pending_masked_probs = None

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
        equip_cd_pre: int | None = None,
    ) -> None:
        self._step_i += 1
        step_r = float(reward)
        self.ep_return += step_r
        info = info or {}
        mask = action_masks
        # attack / attack_up / attack_down share one mask bit in action_mask.py.
        attack_legal = bool(mask[ATTACK_ACTION]) if mask is not None and len(mask) > ATTACK_ACTION else False
        use_slots = _mask_use_slot_names(mask, inventory_slots)
        del hooks  # accepted for call-site stability; not logged

        if value is None:
            value = self._pending_value
        self._pending_value = None
        masked_probs = self._pending_masked_probs
        self._pending_masked_probs = None

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

        equipped = None
        magic = None
        if isinstance(info, dict):
            state = info.get("state")
            if isinstance(state, dict) and state.get("equipped_weapon_id") is not None:
                equipped = int(state["equipped_weapon_id"])
            elif info.get("hp") is not None and "equipped_weapon_id" in info:
                equipped = int(info["equipped_weapon_id"])
            report = info.get("magic_report")
            if isinstance(report, dict) and report:
                magic = {
                    k: report[k]
                    for k in (
                        "ok",
                        "reason",
                        "slot",
                        "item_id",
                        "equipped_before",
                        "equipped_after",
                        "equipped_slot_before",
                        "equipped_slot_after",
                        "frames",
                        "game_mode",
                        "game_state",
                        "in_control_after",
                        "stages",
                        "anomaly",
                        "zero_nav",
                        "menu_dismiss",
                        "menu_recovered",
                    )
                    if k in report
                }

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
        if equipped is not None:
            row["equipped_weapon_id"] = equipped
        if magic:
            row["magic"] = magic
        if big:
            row["big_rewards"] = big
        knife_fail = _knife_fail_row(info)
        if knife_fail is not None:
            row["knife_fail"] = knife_fail
        knife_budget = _knife_budget_row(info)
        if knife_budget is not None:
            row["knife_budget"] = knife_budget
        if isinstance(info.get("combat_audit"), dict):
            row["combat"] = info["combat_audit"]
        if isinstance(info.get("logistics_sample"), dict):
            row["logistics_sample"] = info["logistics_sample"]
        if terminated or truncated:
            row["ep_return_total"] = round(self.ep_return, 5)

        equip_phase = int(info.get("equip_phase") or 0)
        cd_post = int(info.get("equip_switch_cooldown") or 0)
        cd_pre = int(equip_cd_pre) if equip_cd_pre is not None else cd_post
        eq_slot = info.get("equipped_slot_0based")
        if eq_slot is None and isinstance(info.get("state"), dict):
            eq_slot = info["state"].get("equipped_slot_0based")
        magic_reason = str((magic or {}).get("reason") or "")
        aname_s = str(aname or "")
        equip_interesting = (
            cd_pre > 0
            or cd_post > 0
            or equip_phase > 0
            or aname_s == "equip"
            or aname_s.startswith("select_slot")
            or magic_reason.startswith("equip")
            or magic_reason
            in ("already_equipped", "item_menu_open_failed", "equip_abort")
        )
        policy_snip = _equip_policy_probs(masked_probs, mask)
        if policy_snip and float(policy_snip.get("p_equip") or 0) >= 0.05:
            equip_interesting = True
        if equip_interesting:
            equip_row: dict[str, Any] = {
                "cd_pre": cd_pre,
                "cd_post": cd_post,
                "phase": equip_phase,
                "open_legal": (
                    bool(mask[EQUIP_ACTION])
                    if mask is not None and len(mask) > EQUIP_ACTION
                    else None
                ),
                "eq_id": equipped,
                "eq_slot": None if eq_slot is None else int(eq_slot),
                "weapons": _inventory_weapon_slots(inventory_slots),
            }
            if equip_phase == 1 or aname_s.startswith("select_slot"):
                equip_row["legal_select"] = _legal_equip_select_slots(
                    mask, inventory_slots
                )
            if policy_snip:
                equip_row["policy"] = policy_snip
            row["equip"] = equip_row

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
