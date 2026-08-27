"""Build Phase 1 Pass-1 Route Council prompt for cp05 (Main Hall post-lockpick)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from re1_rl.pb_sidecar import enemies_killed_from_sidecar
from re1_rl.phase1_route_council import (
    build_pass1_prompt,
    build_phase1_context,
    estimate_tokens,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default="cp05")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "_tmp" / "cp05_phase1_pass1_prompt.txt",
    )
    parser.add_argument(
        "--context-json",
        type=Path,
        default=ROOT / "_tmp" / "cp05_phase1_context.json",
    )
    parser.add_argument(
        "--sidecar",
        type=Path,
        default=None,
        help="Cell sidecar JSON; subtract enemies_killed_by_room from enemies[]",
    )
    args = parser.parse_args()
    killed = {}
    if args.sidecar is not None:
        killed = enemies_killed_from_sidecar(
            json.loads(args.sidecar.read_text(encoding="utf-8"))
        )
    ctx = build_phase1_context(args.checkpoint, enemies_killed=killed)
    args.context_json.parent.mkdir(parents=True, exist_ok=True)
    args.context_json.write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    prompt = build_pass1_prompt(args.checkpoint, enemies_killed=killed)
    args.output.write_text(prompt, encoding="utf-8")
    tokens = estimate_tokens(prompt)
    print(f"wrote {args.output}")
    print(f"wrote {args.context_json}")
    print(f"chars={len(prompt)} estimated_tokens={tokens}")
    print(f"rooms={len(ctx['rooms'])} edges={len(ctx['directed_edges'])}")
    print(f"open_frontier={[b['id'] for b in ctx['open_frontier_beats']]}")
    print(f"remaining_beats={len(ctx['remaining_mandatory_beats'])}")
    if tokens > 20000:
        raise SystemExit("prompt too large for intended Phase 1 packet")


if __name__ == "__main__":
    main()
