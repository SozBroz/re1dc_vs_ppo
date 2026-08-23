"""Step through a Yawn fight tape and log player + enemy RAM each frame.

Useful when cp121 dies early on replay — compare capture-time vs replay-time
enemy rows around the first HP dip (often step ~19).

  venv\\Scripts\\python.exe scripts\\yawn_fight_probe.py --to 121 --steps 40
  venv\\Scripts\\python.exe scripts\\yawn_fight_probe.py --to 121 --crystals --from-step 15
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.enemy_combat import format_enemy_table
from re1_rl.yawn_outcome import find_yawn_entities
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.go_explore_merge import CELL_SIDECAR_NAME, CELL_STATE_NAME
from re1_rl.leg_replay import tape_is_combat
from scripts.play_human import configure_ram_skip, wait_for_emuhawk
from scripts.replay_leg import _cell_dir, _load_tape, _rails_root_from_args


def _inventory_names(state: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    slots = state.get("inventory_slots") or []
    if isinstance(slots, list):
        for row in slots:
            if isinstance(row, (list, tuple)) and row and str(row[0]).strip():
                names.add(str(row[0]))
            elif isinstance(row, str) and row.strip():
                names.add(row)
    for n in state.get("inventory") or []:
        if str(n).strip():
            names.add(str(n))
    return sorted(names)


def _yawn_summary(enemies: list[dict[str, Any]] | None, *, room_id: str) -> str:
    rows = find_yawn_entities(enemies, room_id=room_id)
    if not rows:
        return "none"
    parts: list[str] = []
    for ent in rows:
        parts.append(
            f"hp={ent.get('hp')} active={ent.get('active_byte')} "
            f"pos=({ent.get('x')},{ent.get('z')}) slot={ent.get('slot')}"
        )
    return "; ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", type=int, default=121)
    ap.add_argument("--tape", type=Path, default=None)
    ap.add_argument("--crystals", action="store_true")
    ap.add_argument("--rails-root", type=Path, default=None)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--from-step", type=int, default=0)
    ap.add_argument("--port", type=int, default=7799)
    ap.add_argument("--jsonl-out", type=Path, default=None)
    ap.add_argument("--force-stale", action="store_true")
    args = ap.parse_args()

    class _Args:
        crystals = args.crystals
        rails_root = args.rails_root

    rails_root = _rails_root_from_args(_Args())
    os.environ["RE1_YAWN_RAILS_ROOT"] = str(rails_root)
    tape, tape_path = _load_tape(int(args.to), args.tape, rails_root)
    actions = [int(a) for a in tape.get("actions") or []]
    if not actions:
        print(f"ERROR: empty actions in {tape_path}", file=sys.stderr)
        return 1

    from_idx = int(tape.get("from_checkpoint_index", -1))
    if from_idx < 0:
        print("ERROR: fresh-start tapes not supported", file=sys.stderr)
        return 1
    pred = _cell_dir(rails_root, from_idx)
    state_path = pred / CELL_STATE_NAME
    sidecar_path = pred / CELL_SIDECAR_NAME
    if not state_path.is_file() or not sidecar_path.is_file():
        print(f"ERROR: missing predecessor {pred}", file=sys.stderr)
        return 1

    combat = tape_is_combat(tape)
    print(
        f"[yawn_probe] tape={tape_path.name} combat={combat} "
        f"actions={len(actions)} pred=cp{from_idx:02d}",
        flush=True,
    )

    reset_options: dict[str, Any] = {
        "route_start_index": int(args.to),
        "leg_span": 1,
        "pb_bundle": {
            "state_path": str(state_path.resolve()),
            "sidecar_path": str(sidecar_path.resolve()),
            "source": "yawn_rails",
        },
    }
    contract = tape.get("contract") or {}
    frame_skip = int(contract.get("frame_skip") or 8)
    async_skip = bool(contract.get("async_cutscene_skip", True))
    configure_ram_skip(frame_skip=frame_skip, async_cutscene_skip=async_skip)

    env = RE1Env(
        project_root=ROOT,
        port=int(args.port),
        headless=True,
        screenshot_mmf=False,
    )
    wait_for_emuhawk(env.bridge, label="yawn_probe")
    env.reset(options=reset_options)
    prev_hp = int((env._prev_state or {}).get("hp", 0) or 0)

    start = max(0, int(args.from_step))
    end = min(len(actions), start + max(1, int(args.steps)))
    out_fh = None
    if args.jsonl_out is not None:
        args.jsonl_out.parent.mkdir(parents=True, exist_ok=True)
        out_fh = args.jsonl_out.open("a", encoding="utf-8")

    try:
        for step in range(start, end):
            act = int(actions[step])
            obs, _rew, _term, _trunc, info = env.step(act)
            del obs, info
            try:
                state = dict(env._read_state(track_items=True))
            except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
                state = dict(env._prev_state or {})
            hp = int(state.get("hp", 0) or 0)
            enemies = state.get("enemies")
            row = {
                "step": step,
                "action": ACTION_NAMES[act] if 0 <= act < len(ACTION_NAMES) else act,
                "room_id": str(state.get("room_id", "")),
                "hp": hp,
                "hp_delta": hp - prev_hp,
                "pos": [int(state.get("x", 0) or 0), int(state.get("z", 0) or 0)],
                "facing": int(state.get("facing", 0) or 0),
                "in_control": bool(state.get("in_control", True)),
                "inventory": _inventory_names(state),
                "enemies": format_enemy_table(enemies),
                "yawn": _yawn_summary(enemies, room_id=str(state.get("room_id", ""))),
            }
            prev_hp = hp
            line = (
                f"[yawn_probe] step={step:4d} act={row['action']:<12} "
                f"room={row['room_id']} hp={hp:4d} dhp={row['hp_delta']:+4d} "
                f"pos=({row['pos'][0]},{row['pos'][1]}) yawn=[{row['yawn']}] "
                f"enemies={row['enemies']}"
            )
            print(line, flush=True)
            if out_fh is not None:
                out_fh.write(json.dumps(row) + "\n")
    finally:
        if out_fh is not None:
            out_fh.close()
        try:
            env.close()
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
