"""Unified capture session: doors + pickups + RAM hunts in ONE playthrough.

Passive logging runs every poll (~5 Hz). Type commands in this terminal
while playing (non-blocking) to snapshot RAM for hunts.

Launch:
  1. python scripts/capture_session.py
  2. EmuHawk + lua/re1_client.lua --socket_port=5555
  3. Play Jill mansion route; use commands at milestones (see help)
  4. Ctrl+C when done
  5. python scripts/build_item_positions.py

Outputs:
  data/doors_empirical.json
  data/pickups_empirical.json
  data/scd_work_flags.json          (fa save ...)
  data/capture_sessions/<ts>.jsonl  (hunt event log)
  data/enemy_ram_hunt_<ts>.json     (on ea)
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import ROOM_ID
from re1_rl.ram_hunt import (
    ENEMY_CANDIDATE_BASE,
    PROMPT_FAST_HI,
    PROMPT_FAST_LO,
    SCD_FLAG_HI,
    SCD_FLAG_LO,
    bit_flips,
    cluster_changes,
    consistent_prompt_diffs,
    diff_bytes,
    fmt_byte_set,
    prompt_snapshot,
    rank_enemy_clusters,
    read_mainram,
    read_range,
)

# Door/pickup helpers (shared with log_door_transitions.py)
from log_door_transitions import (  # noqa: E402
    DOORS_PATH,
    PICKUPS_PATH,
    detect_pickups,
    load_doors,
    read_pose,
    save_door,
    save_pickup,
)
from hunt_enemy_ram import dump_struct_table  # noqa: E402
from hunt_scd_flags import load_flags_db, merge_flag, save_flags_db  # noqa: E402

SESSIONS_DIR = PROJECT_ROOT / "data" / "capture_sessions"

HELP = """
COMMANDS (type in this terminal while EmuHawk runs):
  help              this text
  status            room, pose, buffer counts
  fb                SCD flag BEFORE (pose set — do not trigger event yet)
  fa                SCD flag AFTER — print bit flips (then: fa save N name [room] [unlocks])
  pa / pt           prompt hunt: snapshot AWAY / AT interactable (repeat 3+ each)
  pan               analyze prompt AWAY vs AT buffers
  eb / ea           enemy kill diff: snapshot alive / dead (quiet single-enemy room!)
  ep [0xADDR]       one-shot enemy struct probe (default candidate base)
Passive (always on): door transitions + item/ammo pickups -> doors_empirical.json,
                     pickups_empirical.json
"""


class HuntState:
  def __init__(self, journal: Path) -> None:
    self.journal = journal
    self.scd_before: list[int] | None = None
    self.scd_lo = SCD_FLAG_LO
    self.scd_hi = SCD_FLAG_HI
    self.prompt_lo = PROMPT_FAST_LO
    self.prompt_hi = PROMPT_FAST_HI
    self.prompt_away: list[list[int]] = []
    self.prompt_at: list[list[int]] = []
    self.enemy_before: list[int] | None = None
    self.last_flips: list[dict] = []

  def log(self, event: str, payload: dict) -> None:
    row = {"t": time.time(), "event": event, **payload}
    with self.journal.open("a", encoding="utf-8") as f:
      f.write(json.dumps(row, default=str) + "\n")


def _room_id(client: BizHawkClient) -> str:
  rid = int(client.read_ram([("room", ROOM_ID, "u8")])["room"])
  return f"{rid:02X}"


def handle_command(cmdline: str, bridge: BizHawkClient, hunt: HuntState) -> None:
  parts = cmdline.strip().split()
  if not parts:
    return
  cmd = parts[0].lower()
  args = parts[1:]

  if cmd in ("help", "h", "?"):
    print(HELP, flush=True)
    return

  if cmd == "status":
    pose = read_pose(bridge)
    print(f"[status] room={pose['room']} pos=({pose['x']},{pose['z']}) "
          f"inv={len(pose['inventory'])} scd_before={'yes' if hunt.scd_before else 'no'} "
          f"prompt away/at={len(hunt.prompt_away)}/{len(hunt.prompt_at)} "
          f"enemy_before={'yes' if hunt.enemy_before else 'no'}",
          flush=True)
    return

  if cmd in ("fb", "flag-before"):
    hunt.scd_before = read_range(bridge, hunt.scd_lo, hunt.scd_hi)
    room = _room_id(bridge)
    print(f"[scd] BEFORE snapshot ({len(hunt.scd_before)} bytes) room={room}", flush=True)
    hunt.log("scd_before", {"room": room, "lo": hunt.scd_lo, "hi": hunt.scd_hi})
    return

  if cmd in ("fa", "flag-after"):
    if hunt.scd_before is None:
      print("[scd] run fb first", flush=True)
      return
    after = read_range(bridge, hunt.scd_lo, hunt.scd_hi)
    flips = bit_flips(hunt.scd_before, after, hunt.scd_lo)
    hunt.last_flips = flips
    hunt.scd_before = None
    room = _room_id(bridge)
    print(f"[scd] AFTER room={room}: {len(flips)} bit flip(s)", flush=True)
    for i, f in enumerate(flips):
      print(f"  [{i}] {f['address']} bit{f['bit']} {f['transition']}", flush=True)
    hunt.log("scd_after", {"room": room, "flips": flips})
    if args and args[0] == "save":
      _fa_save(args[1:], flips, room, hunt)
    return

  if cmd in ("pa", "prompt-away"):
    snap = prompt_snapshot(bridge, hunt.prompt_lo, hunt.prompt_hi, f"away#{len(hunt.prompt_away)+1}")
    hunt.prompt_away.append(snap)
    print(f"[prompt] AWAY #{len(hunt.prompt_away)} captured", flush=True)
    return

  if cmd in ("pt", "prompt-at"):
    snap = prompt_snapshot(bridge, hunt.prompt_lo, hunt.prompt_hi, f"at#{len(hunt.prompt_at)+1}")
    hunt.prompt_at.append(snap)
    print(f"[prompt] AT #{len(hunt.prompt_at)} captured", flush=True)
    return

  if cmd in ("pan", "prompt-analyze"):
    if not hunt.prompt_away or not hunt.prompt_at:
      print("[prompt] need at least one pa and one pt", flush=True)
      return
    hits = consistent_prompt_diffs(hunt.prompt_away, hunt.prompt_at, hunt.prompt_lo)
    print(f"[prompt] {len(hits)} consistent AWAY vs AT byte(s)", flush=True)
    for addr, av, tv in hits[:30]:
      print(f"  0x{addr:08X}  away={fmt_byte_set(av)}  at={fmt_byte_set(tv)}", flush=True)
    hunt.log("prompt_analyze", {"hits": [
      {"addr": f"0x{a:08X}", "away": sorted(av), "at": sorted(tv)} for a, av, tv in hits
    ]})
    return

  if cmd in ("eb", "enemy-before"):
    print("[enemy] reading full MainRAM (~2s)...", flush=True)
    hunt.enemy_before = read_mainram(bridge)
    room = _room_id(bridge)
    print(f"[enemy] BEFORE snapshot room={room}", flush=True)
    hunt.log("enemy_before", {"room": room})
    return

  if cmd in ("ea", "enemy-after"):
    if hunt.enemy_before is None:
      print("[enemy] run eb first", flush=True)
      return
    print("[enemy] reading full MainRAM (~2s)...", flush=True)
    after = read_mainram(bridge)
    changes = diff_bytes(hunt.enemy_before, after)
    ranked = rank_enemy_clusters(cluster_changes(changes), hunt.enemy_before, after)
    hunt.enemy_before = None
    room = _room_id(bridge)
    print(f"[enemy] kill diff room={room}: {len(changes)} bytes, top clusters:", flush=True)
    for i, c in enumerate(ranked[:15]):
      tags = ",".join(c.get("tags", [])) or "-"
      print(f"  #{i+1} 0x{c['start']:08X}-0x{c['end']:08X} dist={c['dist_to_candidate']} [{tags}]",
            flush=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = PROJECT_ROOT / "data" / f"enemy_ram_hunt_{stamp}.json"
    payload = {
      "timestamp": datetime.now(timezone.utc).isoformat(),
      "room": room,
      "changed_bytes": len(changes),
      "clusters": ranked[:40],
      "candidate_base": f"0x{ENEMY_CANDIDATE_BASE:08X}",
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[enemy] wrote {out}", flush=True)
    hunt.log("enemy_after", {"room": room, "out": str(out), "top_cluster": ranked[0] if ranked else None})
    return

  if cmd in ("ep", "enemy-probe"):
    base = ENEMY_CANDIDATE_BASE
    if args:
      base = int(args[0], 0)
    dump_struct_table(bridge, base, label="capture_session")
    hunt.log("enemy_probe", {"base": f"0x{base:08X}"})
    return

  print(f"[capture] unknown command {cmd!r} — type help", flush=True)


def _fa_save(args: list[str], flips: list[dict], default_room: str, hunt: HuntState) -> None:
  if len(args) < 2:
    print("  usage: fa save <index> <name> [room_id] [unlocks text...]", flush=True)
    return
  try:
    idx = int(args[0])
    chosen = flips[idx]
  except (ValueError, IndexError):
    print("[scd] invalid flip index", flush=True)
    return
  name = args[1]
  room = args[2] if len(args) > 2 else default_room
  unlocks = " ".join(args[3:]) if len(args) > 3 else ""
  entry = {
    "name": name,
    "address": chosen["address"],
    "bit": int(chosen["bit"]),
    "room_id": room,
    "unlocks": unlocks,
    "verified": datetime.now().strftime("%Y-%m-%d"),
    "transition": chosen["transition"],
  }
  db = load_flags_db()
  merge_flag(db, entry)
  save_flags_db(db)
  hunt.log("scd_save", entry)
  print(f"[scd] saved flag {name!r}", flush=True)


def _stdin_thread(q: queue.Queue[str]) -> None:
  while True:
    try:
      line = sys.stdin.readline()
    except Exception:
      break
    if not line:
      break
    q.put(line.strip())


def run(port: int = 5555, poll_frames: int = 12) -> None:
  doors = load_doors()
  stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
  journal = SESSIONS_DIR / f"session_{stamp}.jsonl"
  hunt = HuntState(journal)

  print(f"[capture] doors: {len([k for k in doors if not k.startswith('_')])} known")
  print(f"[capture] journal: {journal}")
  print("[capture] waiting for EmuHawk...")
  print(HELP)

  bridge = BizHawkClient(port=port, timeout=600.0)
  bridge.start_server()
  bridge.wait_for_client()
  bridge.set_speed(100)
  print("[capture] connected. Play + type commands here. Ctrl+C to stop.")

  cmd_q: queue.Queue[str] = queue.Queue()
  threading.Thread(target=_stdin_thread, args=(cmd_q,), daemon=True).start()

  last_control_pose: dict | None = None
  prev_room: str | None = None
  pending_exit: dict | None = None
  pickups: list = []
  if PICKUPS_PATH.is_file():
    with PICKUPS_PATH.open(encoding="utf-8") as f:
      pickups = json.load(f)
  ever_held: set[str] = set()
  prev_qty: dict[str, int] = {}
  first_read = True

  try:
    while True:
      bridge.frameadvance(poll_frames)
      pose = read_pose(bridge)
      inv = pose["inventory"]

      while not cmd_q.empty():
        handle_command(cmd_q.get_nowait(), bridge, hunt)

      if prev_room is not None and pose["room"] != prev_room:
        pending_exit = last_control_pose
        print(f"[doors] {prev_room} -> {pose['room']}", flush=True)

      if pending_exit is not None and pose["in_control"] \
              and pose["room"] != pending_exit["room"]:
        key = save_door(doors, pending_exit, pose)
        doors[key]["notes"] = "logged by capture_session.py"
        with DOORS_PATH.open("w", encoding="utf-8") as f:
          json.dump(doors, f, indent=2)
        print(f"[doors] saved {key}", flush=True)
        pending_exit = None

      if not first_read:
        grab_pose = pose if pose["in_control"] else (last_control_pose or pose)
        for event in detect_pickups(prev_qty, inv, ever_held):
          save_pickup(pickups, event, grab_pose)
          print(f"[items] {event['kind']} {event['item']} @ {grab_pose['room']}", flush=True)
      ever_held |= set(inv)
      prev_qty = dict(inv)
      first_read = False

      if pose["in_control"]:
        last_control_pose = pose
      prev_room = pose["room"]

  except KeyboardInterrupt:
    print(f"\n[capture] done. doors={DOORS_PATH} pickups={len(pickups)} "
          f"journal={journal}", flush=True)
    print("[capture] next: python scripts/build_item_positions.py", flush=True)


def main() -> None:
  ap = argparse.ArgumentParser(description="Unified RE1 capture session")
  ap.add_argument("--port", type=int, default=5555)
  ap.add_argument("--poll-frames", type=int, default=12)
  args = ap.parse_args()
  run(port=args.port, poll_frames=args.poll_frames)


if __name__ == "__main__":
  main()
