"""Post-hoc episode visualizer: top-down trajectory + reward timeline.

Reads an episode JSONL written by re1_rl.telemetry.EpisodeLogger and renders
a PNG with one x/z scatter panel per visited room (doors marked from
data/doors_empirical.json) plus a reward-breakdown timeline. This is the
"what did the agent actually do" tool for debugging and the video.

Usage:
    python scripts/plot_episode.py data/episodes/ep_20260702_0000.jsonl
    python scripts/plot_episode.py --latest
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_steps(path: Path) -> list[dict]:
    steps = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("event") == "step":
                steps.append(rec)
    return steps


def load_doors() -> dict:
    p = PROJECT_ROOT / "data" / "doors_empirical.json"
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", nargs="?", help="episode .jsonl path")
    ap.add_argument("--latest", action="store_true", help="use newest episode file")
    ap.add_argument("--out", default=None, help="output PNG (default: alongside jsonl)")
    args = ap.parse_args()

    if args.latest:
        eps = sorted((PROJECT_ROOT / "data" / "episodes").glob("ep_*.jsonl"))
        if not eps:
            sys.exit("no episode files in data/episodes")
        path = eps[-1]
    elif args.episode:
        path = Path(args.episode)
    else:
        sys.exit("pass an episode path or --latest")

    steps = load_steps(path)
    if not steps:
        sys.exit(f"no steps in {path}")
    doors = load_doors()

    by_room: dict[str, list[dict]] = defaultdict(list)
    for s in steps:
        if s.get("room_id"):
            by_room[s["room_id"]].append(s)

    rooms = list(by_room.keys())
    n_rooms = len(rooms)
    fig = plt.figure(figsize=(6 * max(n_rooms, 1), 10))

    # --- top row: per-room x/z trajectories ---
    for i, room in enumerate(rooms):
        ax = fig.add_subplot(2, max(n_rooms, 1), i + 1)
        rs = by_room[room]
        xs = [s["x"] for s in rs]
        zs = [s["z"] for s in rs]
        ns = [s["n"] for s in rs]
        sc = ax.scatter(xs, zs, c=ns, cmap="viridis", s=8)
        ax.plot(xs, zs, lw=0.4, color="gray", alpha=0.5)
        ax.scatter([xs[0]], [zs[0]], marker="^", s=90, color="green", label="enter")
        ax.scatter([xs[-1]], [zs[-1]], marker="s", s=90, color="red", label="last")
        for key, d in doors.items():
            if d["from_room"] == room:
                ax.scatter([d["door_x"]], [d["door_z"]], marker="*", s=200,
                           color="orange")
                ax.annotate(f"->{d['to_room']}", (d["door_x"], d["door_z"]),
                            fontsize=8, color="darkorange")
        deaths = [s for s in rs if s.get("terminated")]
        for s in deaths:
            ax.scatter([s["x"]], [s["z"]], marker="x", s=120, color="black")
        ax.set_title(f"room {room} ({len(rs)} steps)")
        ax.set_xlabel("x")
        ax.set_ylabel("z")
        ax.legend(loc="best", fontsize=7)
        fig.colorbar(sc, ax=ax, label="step n", shrink=0.7)

    # --- bottom: reward timeline stacked by term ---
    ax2 = fig.add_subplot(2, 1, 2)
    ns = [s["n"] for s in steps]
    terms = sorted({k for s in steps for k in (s.get("reward_breakdown") or {})})
    for term in terms:
        vals = [(s.get("reward_breakdown") or {}).get(term, 0.0) for s in steps]
        if any(v != 0 for v in vals):
            ax2.plot(ns, vals, label=term, lw=1)
    ax2.plot(ns, [s["reward"] for s in steps], label="TOTAL (scaled)",
             color="black", lw=1.6)
    # room transition markers
    for a, b in zip(steps, steps[1:]):
        if a.get("room_id") != b.get("room_id"):
            ax2.axvline(b["n"], color="orange", ls="--", lw=0.8)
            ax2.annotate(b["room_id"], (b["n"], ax2.get_ylim()[1]), fontsize=8,
                         color="darkorange", rotation=90, va="top")
    ax2.set_xlabel("step")
    ax2.set_ylabel("reward term (pre-scale)")
    ax2.legend(loc="best", fontsize=8, ncol=3)
    ax2.set_title("reward breakdown over time (dashed orange = room transition)")

    out = Path(args.out) if args.out else path.with_suffix(".png")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    print(f"[plot] wrote {out}")
    print(f"[plot] rooms visited: {rooms}")
    print(f"[plot] total reward: {sum(s['reward'] for s in steps):+.3f} over {len(steps)} steps")


if __name__ == "__main__":
    main()
