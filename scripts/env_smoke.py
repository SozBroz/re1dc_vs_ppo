"""End-to-end env smoke test: random agent, one short episode.

Verifies reset/step/obs/reward/info against a live EmuHawk. Run this first,
then launch EmuHawk with the client lua + socket flags.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env

STEPS = 60


def main() -> int:
    bridge = BizHawkClient(timeout=300.0)
    bridge.start_server()
    print("listening; launch EmuHawk now", flush=True)
    bridge.wait_for_client()
    print("connected", flush=True)
    bridge.set_speed(6400)

    env = RE1Env(
        curriculum_path="D:/re1_rl/curriculum/m0_dining_to_main_hall.json",
        bridge=bridge,
        project_root="D:/re1_rl",
    )

    obs, info = env.reset()
    print(f"reset ok: frame={obs['frame'].shape} proprio={obs['proprio'].shape}"
          f" goal={obs['goal'].shape} info={info}", flush=True)

    total = 0.0
    rng = random.Random(0)
    for i in range(STEPS):
        # bias toward movement so the smoke test actually roams
        action = rng.choice([1, 1, 1, 3, 4, 5, 5, 7])
        obs, reward, term, trunc, info = env.step(action)
        total += reward
        if i % 10 == 0 or term or trunc:
            print(f"  step {i:3d} r={reward:+.3f} room={info['room_id']}"
                  f" hp={info['hp']} pos={info['pos']}", flush=True)
        if term or trunc:
            break

    print(f"episode done: total_reward={total:+.3f}", flush=True)
    bridge.set_speed(100)
    bridge.quit()
    bridge.close()
    print("ENV_SMOKE_PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
