"""Parallel PPO training: N BizHawk instances, one learner.

Default: desync async fleet (one actor per env, central inference + learner).
Use --sync for legacy SubprocVecEnv + MaskablePPO.learn().

Usage:
    python scripts/train_parallel.py --n-envs 12 --total-steps 2000000
    python scripts/train_parallel.py --sync   # legacy synced vec env

Monitor:
  - console: per-rollout progress lines ([progress] rooms/waypoints, [rollout] summary)
  - TensorBoard + CSV: logs/tb/<run>/ (incl. re1/rooms_seen)
  - JSONL metrics: logs/training_metrics.jsonl (all PPO train/* scalars per update)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import gymnasium as gym

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"
BASE_PORT = 5555
CKPT_STATE_DIR = PROJECT_ROOT / "states" / "checkpoints"


class CheckpointCaptureWrapper(gym.Wrapper):
    """Fleet-wide checkpoint collection during training.

    - The FIRST env (across all workers) to complete waypoint index N saves
      a savestate + sidecar wpNN.json; the exclusive-create of the sidecar
      is the race arbiter, so exactly one worker wins.
    - Every fleet-wide personal best appends a note to pb_log.jsonl naming
      the room, inventory, and the NEXT seq to audit, and prints a [PB] line.
    """

    def __init__(self, env, curriculum_path: Path, port: int) -> None:
        super().__init__(env)
        stage = json.loads(Path(curriculum_path).read_text(encoding="utf-8"))
        self._route_steps: list[int] = [int(s) for s in stage.get("route_steps", [])]
        route = json.loads((PROJECT_ROOT / "data" / "route_jill_anypct.json")
                           .read_text(encoding="utf-8"))
        self._steps_by_seq = {int(s["seq"]): s for s in route}
        self._port = port
        self._ep_best = 0
        CKPT_STATE_DIR.mkdir(parents=True, exist_ok=True)

    def reset(self, **kwargs):
        self._ep_best = 0
        return self.env.reset(**kwargs)

    def step(self, action):
        obs, reward, terminated, truncated, info = self.env.step(action)
        idx = int(info.get("waypoint_index", 0))
        if idx > self._ep_best:
            self._ep_best = idx
            try:
                self._on_new_index(idx, info)
            except Exception as exc:  # bookkeeping must never kill training
                print(f"[capture:{self._port}] error: {exc}", flush=True)
        return obs, reward, terminated, truncated, info

    def _seq_room(self, seq: int | None) -> str | None:
        if seq is None:
            return None
        return str(self._steps_by_seq.get(seq, {}).get("room_id"))

    def _on_new_index(self, idx: int, info: dict) -> None:
        state = info.get("state", {})
        seq = self._route_steps[idx - 1] if 0 < idx <= len(self._route_steps) else None
        next_seq = self._route_steps[idx] if idx < len(self._route_steps) else None
        key = f"wp{idx:02d}"
        fname = f"{key}_seq{seq}_{state.get('room_id', '')}.State"

        sidecar = CKPT_STATE_DIR / f"{key}.json"
        try:
            with sidecar.open("x", encoding="utf-8") as f:
                self.env.unwrapped.bridge.save_savestate(str(CKPT_STATE_DIR / fname))
                json.dump({
                    "file": f"states/checkpoints/{fname}",
                    "completed_seq": seq,
                    "waypoint_index": idx,
                    "room_id": state.get("room_id"),
                    "inventory": state.get("inventory", []),
                    "episode_step": int(state.get("step", 0)),
                    "captured_by_port": self._port,
                    "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }, f, indent=2)
            print(f"[capture:{self._port}] CAPTURED {key} (seq {seq}) in "
                  f"{state.get('room_id')}", flush=True)
        except FileExistsError:
            pass  # another worker won this checkpoint

        best_file = CKPT_STATE_DIR / "global_best.json"
        try:
            best = int(json.loads(best_file.read_text(encoding="utf-8"))["best"])
        except (OSError, ValueError, KeyError):
            best = 0
        if idx > best:
            best_file.write_text(json.dumps({"best": idx}), encoding="utf-8")
            note = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "waypoint_index": idx,
                "completed_seq": seq,
                "room_id": state.get("room_id"),
                "inventory": state.get("inventory", []),
                "episode_step": int(state.get("step", 0)),
                "port": self._port,
                "savestate": f"states/checkpoints/{fname}",
                "next_seq_to_audit": next_seq,
                "next_goal_room": self._seq_room(next_seq),
            }
            with (CKPT_STATE_DIR / "pb_log.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(note) + "\n")
            print(f"[PB] fleet best waypoint {idx} (seq {seq}, room "
                  f"{state.get('room_id')}); next to audit: seq {next_seq} "
                  f"({self._seq_room(next_seq)})", flush=True)


def make_env(
    rank: int,
    curriculum: str,
    base_port: int = BASE_PORT,
    capture_checkpoints: bool = False,
    *,
    training_speed: int = 3200,
    skip_chunk: int = 600,
    async_cutscene_skip: bool = True,
    headless: bool = True,
    screenshot_mmf: bool | None = None,
    spawn_progress: Callable[[str], None] | None = None,
):
    """Factory executed INSIDE the subprocess worker."""

    def _init():
        from stable_baselines3.common.monitor import Monitor

        from re1_rl.bizhawk_bridge import BizHawkClient
        from re1_rl.env import RE1Env

        def _phase(label: str) -> None:
            print(f"[actor {rank}] {label}", flush=True)
            if spawn_progress is not None:
                spawn_progress(label)

        port = base_port + rank
        _phase(f"port {port}: starting bridge")
        # keyed by port (not rank) so concurrent runs never share files
        shot = str(PROJECT_ROOT / "data" / f"_frame_{port}.png")
        bridge = BizHawkClient(
            port=port,
            timeout=300.0,
            screenshot_path=shot,
            screenshot_mmf=screenshot_mmf,
        )
        bridge.start_server()

        stagger_s = min(rank * 1.0, 15.0)
        if stagger_s:
            _phase(f"stagger {stagger_s:.0f}s")
        time.sleep(stagger_s)
        _phase("launching EmuHawk")
        emuhawk_cmd = [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
        ]
        if headless:
            # --gdi: skip OpenGL/D3D init that can hang before SocketServer.Connect
            # on headless/autologon boxes. --chromeless: less UI chrome.
            emuhawk_cmd.extend(["--gdi", "--chromeless"])
        # Do NOT redirect stdout/stderr: WinForms ShowDialog needs UserInteractive.
        proc = subprocess.Popen(
            emuhawk_cmd,
            cwd=str(EMUHAWK.parent),
        )
        _phase("waiting for Lua client")
        bridge.wait_for_client()
        _phase("connected; set_speed")
        bridge.set_speed(training_speed)

        env = RE1Env(
            curriculum_path=PROJECT_ROOT / curriculum,
            bridge=bridge,
            project_root=PROJECT_ROOT,
            async_cutscene_skip=async_cutscene_skip,
        )
        env._ram_skip.training_speed = training_speed
        env._ram_skip.cutscene_speed = training_speed
        env._ram_skip.skip_chunk = skip_chunk
        env._ram_skip.invisible_during_skip = headless
        env.knife_echo_joypad = False

        # ensure the owned EmuHawk dies with the env (instance-level hook)
        orig_close = env.close

        def close_with_emu():
            try:
                orig_close()
            finally:
                try:
                    proc.terminate()
                except OSError:
                    pass

        env.close = close_with_emu
        if capture_checkpoints:
            env = CheckpointCaptureWrapper(
                env, PROJECT_ROOT / curriculum, port)
        env = Monitor(env)
        from sb3_contrib.common.wrappers import ActionMasker

        env = ActionMasker(env, lambda e: e.unwrapped.action_masks())
        _phase("env ready")
        return env

    return _init


def _build_model(
    policy_cls: type,
    env,
    *,
    device: str,
    resume_path: Path | None,
    tb_log: str,
):
    from re1_rl.policy_config import POLICY_KWARGS

    hp = dict(
        n_steps=256,
        batch_size=512,
        n_epochs=4,
        learning_rate=3e-4,
        gamma=0.995,
        ent_coef=0.01,
        verbose=1,
        device=device,
        tensorboard_log=tb_log,
        policy_kwargs=POLICY_KWARGS,
    )
    if resume_path is None:
        return policy_cls("MultiInputPolicy", env, **hp)

    from stable_baselines3 import PPO as BasePPO

    base = BasePPO.load(str(resume_path), device=device)
    model = policy_cls("MultiInputPolicy", env, **hp)
    model.policy.load_state_dict(base.policy.state_dict())
    model.num_timesteps = int(base.num_timesteps)
    print(
        f"[train] resumed PPO weights into MaskablePPO from {resume_path}",
        flush=True,
    )
    return model


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-envs", type=int, default=12)
    ap.add_argument("--total-steps", type=int, default=2_000_000,
                    help="training timesteps (0 = no limit, run until interrupted)")
    ap.add_argument("--curriculum", default="curriculum/m0_dining_to_main_hall.json")
    ap.add_argument("--resume", default=None, help="checkpoint .zip to continue from")
    ap.add_argument("--fresh", action="store_true",
                    help="new random policy; do not auto-load ppo_re1_final or ckpt dir")
    ap.add_argument("--base-port", type=int, default=BASE_PORT,
                    help="first TCP/EmuHawk port; offset per concurrent run")
    ap.add_argument("--run-name", default=None,
                    help="isolate checkpoints/tb/final save under this name (A/B runs)")
    ap.add_argument("--capture-checkpoints", action="store_true",
                    help="save a savestate + PB note when any env reaches a "
                         "new waypoint (states/checkpoints/)")
    ap.add_argument("--training-speed", type=int, default=3200,
                    help="BizHawk speedmode %% for fleet training (default 3200)")
    ap.add_argument("--skip-chunk", type=int, default=600,
                    help="max frames per Lua fast_forward round-trip (default 600)")
    ap.add_argument(
        "--sync",
        action="store_true",
        help="legacy synced SubprocVecEnv + model.learn() (default: async fleet)",
    )
    ap.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="EmuHawk --gdi/--chromeless + invisible cutscene skip (default on)",
    )
    ap.add_argument(
        "--screenshot-mmf",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="BizHawk MMF screenshot transport (default: on for Windows)",
    )
    ap.add_argument(
        "--inference-batch-max",
        type=int,
        default=32,
        help="max actor obs per GPU inference batch in async fleet (default 32)",
    )
    args = ap.parse_args()

    import torch

    from re1_rl.checkpoint_io import (
        atomic_copy_checkpoint,
        atomic_model_save,
        checkpoint_save_freq_vec_env,
        find_latest_checkpoint,
        is_valid_checkpoint,
        resolve_resume_path,
        write_latest_pointer,
        zip_path,
    )

    tb_log = str(PROJECT_ROOT / "logs" / "tb")
    ckpt_dir = PROJECT_ROOT / "data" / "checkpoints"
    if args.run_name:
        ckpt_dir = ckpt_dir / args.run_name
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    train_steps = args.total_steps if args.total_steps > 0 else 2**62
    step_label = str(args.total_steps) if args.total_steps > 0 else "unlimited"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    resume_path = resolve_resume_path(
        args.resume, project_root=PROJECT_ROOT, ckpt_dir=ckpt_dir,
    ) if not args.fresh else None
    if args.resume and resume_path is None:
        print(f"[train] ERROR: no valid checkpoint for --resume {args.resume!r}",
              flush=True)
        return 1
    if args.resume:
        requested = zip_path(
            Path(args.resume) if Path(args.resume).is_absolute()
            else PROJECT_ROOT / args.resume
        )
        if resume_path.resolve() != requested.resolve():
            print(f"[train] WARNING: {requested} missing/corrupt; "
                  f"using {resume_path}", flush=True)

    if not args.sync:
        from re1_rl.async_fleet import run_async_fleet_training
        from re1_rl.training_metrics_log import training_metrics_jsonl_path

        metrics_jsonl = training_metrics_jsonl_path(PROJECT_ROOT, run_name=args.run_name)
        print(
            f"[train] async fleet: {args.n_envs} envs, {step_label} steps, "
            f"cuda={torch.cuda.is_available()}",
            flush=True,
        )
        print(f"[train:async] metrics jsonl -> {metrics_jsonl}", flush=True)
        if resume_path is not None:
            print(f"[train:async] resume checkpoint -> {resume_path}", flush=True)
        try:
            run_async_fleet_training(
                n_envs=args.n_envs,
                train_steps=train_steps,
                curriculum=args.curriculum,
                base_port=args.base_port,
                training_speed=int(args.training_speed),
                skip_chunk=int(args.skip_chunk),
                capture_checkpoints=args.capture_checkpoints,
                resume_path=resume_path,
                ckpt_dir=ckpt_dir,
                run_name=args.run_name,
                device=device,
                tb_log=tb_log,
                headless=bool(args.headless),
                screenshot_mmf=args.screenshot_mmf,
                inference_batch_max=int(args.inference_batch_max),
            )
        except KeyboardInterrupt:
            print("[train] interrupted", flush=True)
        print("TRAIN_DONE", flush=True)
        return 0

    from sb3_contrib import MaskablePPO as PolicyCls
    from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback
    from stable_baselines3.common.vec_env import SubprocVecEnv

    print(f"[train:sync] {args.n_envs} envs, {step_label} steps, "
          f"cuda={torch.cuda.is_available()}", flush=True)

    env = SubprocVecEnv(
        [
            make_env(
                i,
                args.curriculum,
                args.base_port,
                args.capture_checkpoints,
                training_speed=int(args.training_speed),
                skip_chunk=int(args.skip_chunk),
                headless=bool(args.headless),
                screenshot_mmf=args.screenshot_mmf,
            )
            for i in range(args.n_envs)
        ],
        start_method="spawn",
    )

    from re1_rl.training_progress import TrainingProgressTracker
    from re1_rl.training_metrics_log import (
        TrainingMetricsJsonlCallback,
        training_metrics_jsonl_path,
    )

    metrics_jsonl = training_metrics_jsonl_path(PROJECT_ROOT, run_name=args.run_name)
    metrics_cb = TrainingMetricsJsonlCallback(metrics_jsonl)
    print(f"[train] metrics jsonl -> {metrics_jsonl}", flush=True)

    class ProgressCallback(BaseCallback):
        """Console progression: reward, waypoint reach, rooms seen."""

        def __init__(self) -> None:
            super().__init__()
            self._progress = TrainingProgressTracker(
                prefix="progress",
                machine_name="standalone",
                best_log_path=PROJECT_ROOT / "data" / "logs" / "best_rooms_standalone.jsonl",
            )

        def _on_step(self) -> bool:
            for info in self.locals.get("infos", []):
                self._progress.consume_infos([info], num_timesteps=self.num_timesteps)
            return True

        def _on_rollout_end(self) -> None:
            self._progress.log_rollout_end(
                self.model, num_timesteps=self.num_timesteps
            )

    class AtomicCheckpointCallback(CheckpointCallback):
        """CheckpointCallback that writes via temp file + atomic replace."""

        def _on_step(self) -> bool:
            if self.n_calls % self.save_freq != 0:
                return True
            model_path = self._checkpoint_path(extension="zip")
            saved = atomic_model_save(self.model, model_path)
            write_latest_pointer(self.save_path, saved)
            if self.verbose >= 2:
                print(f"Saving model checkpoint to {saved}", flush=True)
            return True

    ckpt_dir.mkdir(parents=True, exist_ok=True)
    callbacks = [
        AtomicCheckpointCallback(save_freq=checkpoint_save_freq_vec_env(args.n_envs),
                                 save_path=str(ckpt_dir), name_prefix="ppo_re1"),
        ProgressCallback(),
        metrics_cb.get_callback(),
    ]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = _build_model(
        PolicyCls,
        env,
        device=device,
        resume_path=resume_path,
        tb_log=tb_log,
    )
    if resume_path is not None:
        model.tensorboard_log = tb_log

    try:
        model.learn(total_timesteps=train_steps, callback=callbacks,
                    progress_bar=False, tb_log_name=args.run_name or "PPO")
    except KeyboardInterrupt:
        print("[train] interrupted; saving", flush=True)
    finally:
        suffix = f"_{args.run_name}" if args.run_name else ""
        final_alias = zip_path(PROJECT_ROOT / "data" / f"ppo_re1_final{suffix}")
        if model is not None:
            try:
                saved = atomic_model_save(model, final_alias)
                write_latest_pointer(ckpt_dir, saved)
                print(f"[train] saved {saved}", flush=True)
            except OSError as exc:
                print(f"[train] WARNING: final save failed: {exc}", flush=True)
                latest = find_latest_checkpoint(ckpt_dir)
                if latest is not None and is_valid_checkpoint(latest):
                    try:
                        atomic_copy_checkpoint(latest, final_alias)
                        print(f"[train] aliased {final_alias} <- {latest}", flush=True)
                    except (OSError, ValueError) as copy_exc:
                        print(f"[train] WARNING: alias copy failed: {copy_exc}",
                              flush=True)
        env.close()
    print("TRAIN_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
