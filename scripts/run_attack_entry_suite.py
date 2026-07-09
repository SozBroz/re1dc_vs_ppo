"""Live attack-from-entry suite (EmuHawk probe port, fleet untouched).

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\run_attack_entry_suite.py --weapons beretta,knife
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\run_attack_entry_suite.py --weapons 0x03 --port 7790
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
DEFAULT_OUT = ROOT / "data" / "attack_entry_suite.jsonl"

from re1_rl.action_mask import ATTACK_ACTION  # noqa: E402
from re1_rl.attack_entry_suite import (  # noqa: E402
    ENTRY_SCENARIOS,
    EntryAttackResult,
    evaluate_live_attack,
    parse_weapon_list,
)
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.env import ACTION_NAMES, RE1Env  # noqa: E402
from re1_rl.sticky_input import StickyInputState  # noqa: E402


def _result_row(r: EntryAttackResult) -> dict:
    return {
        "weapon_id": f"0x{r.weapon_id:02X}",
        "weapon": r.weapon_name,
        "scenario": r.scenario,
        "pre_hooks": r.pre.get("hooks"),
        "outcome": r.report.get("outcome"),
        "macro_path": r.report.get("macro_path"),
        "ammo_before": r.ammo_before,
        "ammo_after": r.ammo_after,
        "ammo_spent": r.report.get("ammo_spent"),
        "saw_fire_anim": r.report.get("saw_fire_anim"),
        "frames": r.report.get("frames"),
        "succeeded": r.succeeded,
        "reward": r.extra.get("reward"),
    }


def _print_row(row: dict) -> None:
    mark = "OK" if row["succeeded"] else "FAIL"
    print(
        f"[entry-suite] {mark} {row['weapon']:<12} {row['scenario']:<26} "
        f"pre={row['pre_hooks']} outcome={row['outcome']} "
        f"ammo {row['ammo_before']}->{row['ammo_after']}",
        flush=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Live attack-from-entry matrix")
    ap.add_argument("--port", type=int, default=7790)
    ap.add_argument("--speed", type=int, default=100)
    ap.add_argument("--weapons", type=str, default="beretta,knife")
    ap.add_argument("--scenarios", type=str, default="", help="comma names; default all")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    weapon_ids = parse_weapon_list(args.weapons)
    if args.scenarios.strip():
        wanted = {s.strip() for s in args.scenarios.split(",") if s.strip()}
        scenarios = [s for s in ENTRY_SCENARIOS if s.name in wanted]
        missing = wanted - {s.name for s in scenarios}
        if missing:
            raise SystemExit(f"unknown scenarios: {sorted(missing)}")
    else:
        scenarios = list(ENTRY_SCENARIOS)

    port = int(args.port)
    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    bridge = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    print(
        f"[entry-suite] port={port} weapons={[f'0x{w:02X}' for w in weapon_ids]} "
        f"scenarios={len(scenarios)} log={out_path}",
        flush=True,
    )
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    env: RE1Env | None = None

    def shutdown(code: int) -> None:
        try:
            if env is not None:
                env.close()
        except Exception:
            pass
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        raise SystemExit(code)

    signal.signal(signal.SIGINT, lambda *_: shutdown(130))

    env = RE1Env(
        curriculum_path=CURRICULUM,
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=False,
    )
    env._ram_skip.use_engine_patches = False
    bridge.wait_for_client()
    bridge.set_speed(int(args.speed))
    env.reset()

    action_index = {name: ACTION_NAMES.index(name) for name in ACTION_NAMES}
    savestate = str(ROOT / env._stage["init_savestate"])
    rows: list[dict] = []
    failures = 0

    for weapon_id in weapon_ids:
        for scenario in scenarios:
            bridge.load_savestate(savestate)
            bridge.frameadvance(4)
            env._sticky_input = StickyInputState()
            result = evaluate_live_attack(
                env,
                scenario=scenario,
                weapon_id=weapon_id,
                attack_action=ATTACK_ACTION,
                action_index=action_index,
            )
            row = _result_row(result)
            rows.append(row)
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            _print_row(row)
            if not result.succeeded:
                failures += 1

    ok = len(rows) - failures
    print(
        f"[entry-suite] done {ok}/{len(rows)} passed failures={failures} -> {out_path}",
        flush=True,
    )
    shutdown(0 if failures == 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
