"""Multi-agent knife QA: N knife-loop ranks, others PPO-driven or random.

Mirrors SubprocVecEnv barrier — all envs step together each tick. Knife
ranks repeat noop×N → knife_swing; remaining ranks follow a PPO checkpoint
(--ppo) or act randomly. EVERY knife swing across the fleet (scripted or
policy-chosen) is verified against the Lua joypad.get() echo — input
delivery QA that does not depend on enemy damage.

Usage:
    python scripts/knife_fleet_qa.py
    python scripts/knife_fleet_qa.py --n-envs 20 --knife-agents 6 \
        --ppo data/ppo_re1_knife11.zip --speed 400 --turbo-patches --async-skip
"""

from __future__ import annotations

import argparse
import random
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"
DEFAULT_BASE_PORT = 5777
DEFAULT_CURRICULUM = "curriculum/m0_dining_to_main_hall.json"


@dataclass
class AgentSlot:
    rank: int
    port: int
    bridge: object
    env: object
    proc: subprocess.Popen[bytes]
    obs: object = None


def _spawn_agent(
    rank: int,
    *,
    base_port: int,
    speed: int,
    turbo_patches: bool,
    async_skip: bool,
    curriculum: Path,
) -> AgentSlot:
    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import RE1Env

    port = base_port + rank
    shot = str(PROJECT_ROOT / "data" / f"_frame_{port}.png")
    bridge = BizHawkClient(port=port, timeout=300.0, screenshot_path=shot)
    bridge.start_server()

    time.sleep(rank * 3.0)
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
    bridge.wait_for_client()
    bridge.set_speed(speed)

    env = RE1Env(
        curriculum_path=curriculum,
        bridge=bridge,
        project_root=PROJECT_ROOT,
        async_cutscene_skip=async_skip,
    )
    env._ram_skip.use_engine_patches = turbo_patches
    env._ram_skip.training_speed = speed
    env._ram_skip.cutscene_speed = speed
    env._ram_skip.skip_chunk = 600
    env._ram_skip.invisible_during_skip = True
    env.knife_echo_joypad = True

    orig_close = env.close

    def close_with_emu() -> None:
        try:
            orig_close()
        finally:
            try:
                proc.terminate()
            except OSError:
                pass

    env.close = close_with_emu  # type: ignore[method-assign]
    return AgentSlot(rank=rank, port=port, bridge=bridge, env=env, proc=proc)


def _step_one(slot: AgentSlot, action: int) -> tuple:
    obs, _, terminated, truncated, info = slot.env.step(action)
    return obs, terminated, truncated, info


def main() -> int:
    ap = argparse.ArgumentParser(description="Synced knife QA fleet")
    ap.add_argument("--n-envs", type=int, default=12)
    ap.add_argument("--base-port", type=int, default=DEFAULT_BASE_PORT)
    ap.add_argument(
        "--knife-agents", type=int, default=1,
        help="ranks 0..K-1 run the deterministic noop->knife loop",
    )
    ap.add_argument(
        "--ppo", default=None,
        help="PPO checkpoint .zip driving the non-knife ranks (default: random)",
    )
    ap.add_argument("--speed", type=int, default=3200)
    ap.add_argument("--turbo-patches", action="store_true")
    ap.add_argument("--async-skip", action="store_true")
    ap.add_argument("--noops", type=int, default=4)
    ap.add_argument("--ticks", type=int, default=0, help="exit after N ticks (0 = forever)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--curriculum", default=DEFAULT_CURRICULUM)
    ap.add_argument("--aim", type=int, default=None, help="aim phase, game frames")
    ap.add_argument("--swing", type=int, default=None, help="swing phase, game frames")
    ap.add_argument("--recovery", type=int, default=None, help="recovery phase, game frames")
    ap.add_argument(
        "--scale", type=int, default=None,
        help="emulated frames per game frame (default 2: 30fps game logic)",
    )
    args = ap.parse_args()

    from re1_rl import knife_macro as km
    from re1_rl.env import ACTION_NAMES

    n_envs = int(args.n_envs)
    knife_ranks = set(range(max(0, int(args.knife_agents))))
    curriculum = PROJECT_ROOT / args.curriculum
    rng = random.Random(int(args.seed))
    noop = ACTION_NAMES.index("noop")
    knife = ACTION_NAMES.index("knife_swing")
    n_actions = len(ACTION_NAMES)

    model = None
    if args.ppo:
        from stable_baselines3 import PPO

        ppo_path = Path(args.ppo)
        if not ppo_path.is_absolute():
            ppo_path = PROJECT_ROOT / ppo_path
        model = PPO.load(str(ppo_path), device="auto")
        print(f"[knife_fleet] loaded PPO policy from {ppo_path}", flush=True)

    phase_kwargs = {}
    if args.aim is not None:
        phase_kwargs["aim"] = int(args.aim)
    if args.swing is not None:
        phase_kwargs["swing"] = int(args.swing)
    if args.recovery is not None:
        phase_kwargs["recovery"] = int(args.recovery)
    if args.scale is not None:
        phase_kwargs["scale"] = int(args.scale)
    knife_schedule = km.build_knife_frame_buttons(**phase_kwargs)
    knife_want = [
        "+".join(sorted(k for k, v in f.items() if v)) for f in knife_schedule
    ]

    slots: list[AgentSlot] = []
    shutting_down = False

    def shutdown(*_: object) -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        print("\n[knife_fleet] stopping...", flush=True)
        for slot in slots:
            try:
                slot.env.close()
            except Exception:
                pass
            try:
                slot.proc.terminate()
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    print(
        f"[knife_fleet] spawning {n_envs} envs ports "
        f"{args.base_port}-{args.base_port + n_envs - 1}, "
        f"speed={args.speed}%, turbo_patches={args.turbo_patches}, "
        f"async_skip={args.async_skip}, knife_ranks={sorted(knife_ranks)}, "
        f"others={'ppo' if model else 'random'}, "
        f"macro={len(knife_schedule)} emu frames",
        flush=True,
    )
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_envs) as spawn_pool:
        spawn_futs = [
            spawn_pool.submit(
                _spawn_agent,
                rank,
                base_port=int(args.base_port),
                speed=int(args.speed),
                turbo_patches=bool(args.turbo_patches),
                async_skip=bool(args.async_skip),
                curriculum=curriculum,
            )
            for rank in range(n_envs)
        ]
        for fut in as_completed(spawn_futs):
            slot = fut.result()
            slots.append(slot)
            print(f"[knife_fleet] rank {slot.rank} port {slot.port} connected", flush=True)
    slots.sort(key=lambda s: s.rank)

    for slot in slots:
        if phase_kwargs:
            slot.env.knife_phases = (
                phase_kwargs.get("aim", km.KNIFE_AIM_GAME_FRAMES),
                phase_kwargs.get("swing", km.KNIFE_SWING_GAME_FRAMES),
                phase_kwargs.get("recovery", km.KNIFE_RECOVERY_GAME_FRAMES),
            )
            if "scale" in phase_kwargs:
                slot.env.knife_scale = phase_kwargs["scale"]

    print(f"[knife_fleet] fleet ready in {time.perf_counter() - t0:.1f}s", flush=True)
    with ThreadPoolExecutor(max_workers=n_envs) as reset_pool:
        reset_futs = {
            reset_pool.submit(lambda s: s.env.reset(), slot): slot for slot in slots
        }
        for fut, slot in reset_futs.items():
            slot.obs, _ = fut.result()

    def verify_echo(slot: AgentSlot) -> str:
        echo = slot.bridge.last_step_echo
        if echo is None:
            return "MISSING"
        if len(echo) != len(knife_want):
            return f"SHORT({len(echo)}/{len(knife_want)})"
        if echo != knife_want:
            n_bad = sum(1 for w, g in zip(knife_want, echo) if w != g)
            return f"MISMATCH({n_bad}f)"
        return "OK"

    knife_phase = 0
    tick = 0
    swings_ok = 0
    swings_bad = 0
    print(
        f"[knife_fleet] synced loop — ranks {sorted(knife_ranks)}: "
        f"{args.noops} noops then knife; others "
        f"{'ppo policy' if model else 'random'}. Ctrl+C to quit.",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=n_envs) as pool:
        while True:
            actions: list[int] = []
            for rank in range(n_envs):
                if rank in knife_ranks:
                    actions.append(noop if knife_phase < int(args.noops) else knife)
                elif model is not None:
                    act, _ = model.predict(slots[rank].obs, deterministic=False)
                    actions.append(int(act))
                else:
                    actions.append(rng.randrange(n_actions))

            futures = {
                pool.submit(_step_one, slots[rank], actions[rank]): rank
                for rank in range(n_envs)
            }
            results: dict[int, tuple] = {}
            for fut in as_completed(futures):
                rank = futures[fut]
                results[rank] = fut.result()

            tick += 1
            swing_lines: list[str] = []
            for rank in range(n_envs):
                obs, terminated, truncated, info = results[rank]
                slots[rank].obs = obs
                if actions[rank] == knife:
                    verdict = verify_echo(slots[rank])
                    if verdict == "OK":
                        swings_ok += 1
                    else:
                        swings_bad += 1
                    src = "loop" if rank in knife_ranks else ("ppo" if model else "rand")
                    st = info.get("state", {})
                    swing_lines.append(
                        f"rank{rank}({src})={verdict} hp={st.get('hp')}"
                    )
                if terminated or truncated:
                    slots[rank].obs, _ = slots[rank].env.reset()

            if swing_lines:
                print(
                    f"[knife_fleet] tick={tick} swings[{swings_ok} ok/"
                    f"{swings_bad} bad] " + "  ".join(swing_lines),
                    flush=True,
                )
            knife_phase = 0 if knife_phase >= int(args.noops) else knife_phase + 1

            if int(args.ticks) > 0 and tick >= int(args.ticks):
                print(
                    f"[knife_fleet] done: {tick} ticks, "
                    f"{swings_ok} swings OK, {swings_bad} swings BAD",
                    flush=True,
                )
                shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
