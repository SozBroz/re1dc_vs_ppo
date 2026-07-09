"""Modest PPO smoke training run -- plumbing test, not a real training run.

Single env, ~2k timesteps (~5 min wall at 6400% emu speed). Verifies:
SB3 PPO consumes the dict obs, gradients flow on CUDA, checkpoint saves.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env
from re1_rl.policy_config import POLICY_KWARGS

TOTAL_TIMESTEPS = 2048
OUT_MODEL = "D:/re1_rl/data/ppo_smoke"


def main() -> int:
    print(f"torch cuda: {torch.cuda.is_available()}", flush=True)

    bridge = BizHawkClient(timeout=300.0)
    bridge.start_server()
    print("listening; launch EmuHawk now", flush=True)
    bridge.wait_for_client()
    print("connected", flush=True)
    bridge.set_speed(6400)

    env = Monitor(
        RE1Env(
            curriculum_path="D:/re1_rl/curriculum/m0_dining_to_main_hall.json",
            bridge=bridge,
            project_root="D:/re1_rl",
        )
    )

    model = PPO(
        "MultiInputPolicy",
        env,
        policy_kwargs=POLICY_KWARGS,
        n_steps=256,
        batch_size=64,
        n_epochs=4,
        learning_rate=3e-4,
        verbose=1,
        device="cuda" if torch.cuda.is_available() else "cpu",
    )
    model.learn(total_timesteps=TOTAL_TIMESTEPS, progress_bar=False)
    model.save(OUT_MODEL)
    print(f"saved {OUT_MODEL}.zip", flush=True)

    bridge.set_speed(100)
    bridge.quit()
    bridge.close()
    print("TRAIN_SMOKE_PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
