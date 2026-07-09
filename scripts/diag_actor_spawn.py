"""One-off: spawn a single fleet actor and print where make_env blocks/fails."""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _worker(rank: int, base_port: int, capture: bool) -> None:
    try:
        from scripts.train_parallel import make_env

        print(f"[diag rank {rank}] import ok", flush=True)
        t0 = time.perf_counter()
        env = make_env(
            rank,
            "curriculum/m0_dining_to_main_hall.json",
            base_port,
            capture,
            training_speed=3200,
            skip_chunk=600,
        )()
        print(f"[diag rank {rank}] env ready in {time.perf_counter() - t0:.1f}s", flush=True)
        env.close()
    except Exception:
        traceback.print_exc()
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", type=int, default=0)
    ap.add_argument("--base-port", type=int, default=5662)
    ap.add_argument("--capture-checkpoints", action="store_true")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_worker,
        args=(args.rank, args.base_port, args.capture_checkpoints),
    )
    proc.start()
    proc.join(timeout=args.timeout)
    if proc.is_alive():
        print(f"[diag rank {args.rank}] TIMEOUT after {args.timeout}s — killing", flush=True)
        proc.kill()
        proc.join(5)
        return 2
    print(f"[diag rank {args.rank}] exit={proc.exitcode}", flush=True)
    return 0 if proc.exitcode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
