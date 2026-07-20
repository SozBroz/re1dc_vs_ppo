"""PPO cutscene/reward monitor — same live weights as the training learner.

Launches its own visible EmuHawk on port 7790 (away from fleet). Pulls policy
bytes from the learner ``GET /weights`` endpoint (same path workers use).
Logs every env step with step reward + cumulative episode reward from the same
float32 buffer workers write into ``WorkerRollout.rewards`` (see
``collect_rollout``). No parallel float64 running sum — ``pool_ep_rew`` is
``sum(pool_rewards[ep_start:n])`` only.

Does not stop or restart training workers.

Usage (from repo root):
    python scripts/monitor_cutscene_rewards.py
    python scripts/monitor_cutscene_rewards.py --learner-host 192.168.0.116
    python scripts/monitor_cutscene_rewards.py --human   # old stick harness
"""

from __future__ import annotations

import argparse
import json
import runpy
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MONITOR_PORT = 7790
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
CURRICULUM = "curriculum/m0_dining_to_main_hall.json"
DEFAULT_LEARNER_HOST = "192.168.0.116"
DEFAULT_LEARNER_PORT = 8765
# Match fleet make_env / run_distributed_learner_wh2_25.cmd skip knobs.
FLEET_TRAINING_SPEED = 6400
FLEET_SKIP_CHUNK = 600
OUT_DEFAULT = ROOT / "data" / "logs" / "ppo_cutscene_monitor.jsonl"


def _r5(x: float) -> float:
    """Round every logged float to 5 decimal places."""
    return round(float(x), 5)


def _fmt5(x: float) -> str:
    return f"{float(x):+.5f}"


def _round_nums(obj: Any, ndigits: int = 5) -> Any:
    """Recursively round every real number to ``ndigits`` (default 5)."""
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return round(obj, ndigits)
    # numpy scalars (float32 step rewards, etc.)
    if hasattr(obj, "item") and type(obj).__module__ == "numpy":
        try:
            return round(float(obj.item()), ndigits)
        except (TypeError, ValueError):
            return obj
    if isinstance(obj, dict):
        return {k: _round_nums(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_nums(v, ndigits) for v in obj]
    return obj


def _run_human(extra: list[str]) -> int:
    # Zero step penalty only for human stick sessions (noise reduction).
    import re1_rl.reward as reward_mod

    reward_mod.STEP_PENALTY = 0.0
    argv = [
        str(Path(__file__).name),
        f"--port={MONITOR_PORT}",
        "--deafen-step",
        "--cutscene-gate-log",
        "--input=both",
        "--training-parity",
    ]
    argv.extend(extra)
    print(
        f"[cutscene-monitor] HUMAN mode port {MONITOR_PORT} "
        "(step penalty off, gate log on)",
        flush=True,
    )
    sys.argv = argv
    runpy.run_path(str(ROOT / "scripts" / "play_human.py"), run_name="__main__")
    return 0


def _batch_obs(obs: dict[str, Any]) -> dict[str, np.ndarray]:
    return {k: np.expand_dims(np.asarray(v), 0) for k, v in obs.items()}


def _hits(breakdown: dict[str, Any] | None) -> dict[str, float]:
    if not breakdown:
        return {}
    return {k: float(v) for k, v in breakdown.items() if abs(float(v)) > 1e-9}


# Exploration reward schema (skill re-exploration-rewards). Flag payouts that
# defy the published buckets / hard exceptions — for operator triage only.
_LARGE_OK = frozenset(
    {
        "new_room",
        "document_examine",
        "new_cutscene",
        "key_item",
        "story_use",
        "gallery",
        "new_weapon",
    }
)
_MODEST_OK = frozenset(
    {
        "item",
        "enemy_damage",
        "enemy_kill",
    }
)
_IGNORE = frozenset(
    {
        "step",
        "hp",
        "death",
        "main_hall_before_kenneth",
        "softlock",
        "gold_emblem_return",
        "shotgun_return",
        "pbrs_graph",
        "pbrs_door",
        "waypoint",
        "retreat",
        "wrong_room",
    }
)


def _schema_alerts(
    *,
    breakdown: dict[str, float],
    room: str,
    skip_frames: int,
    skip_kind: str | None,
    kenneth_seen: bool,
    unpaid_reason: str | None,
    paid_cutscene: bool,
) -> list[str]:
    """Return human-readable schema defiance lines (empty if clean)."""
    from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES

    alerts: list[str] = []
    for k, v in sorted(breakdown.items()):
        if k in _IGNORE or abs(v) < 1e-9:
            continue
        if k in _LARGE_OK:
            if v > 0.0 and v < 0.5:
                alerts.append(f"{k}={_fmt5(v)} below large floor (>=0.5)")
            continue
        if k in _MODEST_OK:
            if v >= 0.5:
                alerts.append(
                    f"{k}={_fmt5(v)} looks LARGE but skill bucket is modest (<0.5)"
                )
            continue
        if abs(v) >= 0.5:
            alerts.append(f"unknown large term {k}={_fmt5(v)}")
        elif abs(v) >= 0.05:
            alerts.append(f"unlisted term {k}={_fmt5(v)}")

    if breakdown.get("new_room", 0.0) > 0.0 and room == "106" and not kenneth_seen:
        alerts.append(
            "new_room paid for main hall (106) before Kenneth — "
            "illegal entry should terminate, not pay room"
        )

    if paid_cutscene and int(skip_frames) < MIN_CUTSCENE_SKIP_FRAMES:
        alerts.append(
            f"new_cutscene paid with skip_frames={skip_frames} "
            f"< {MIN_CUTSCENE_SKIP_FRAMES}"
        )

    if unpaid_reason and paid_cutscene:
        alerts.append(f"paid cutscene but unpaid_reason={unpaid_reason!r}")

    return alerts


def _print_episode_banner(ep_i: int, *, room: Any, policy_v: int) -> None:
    bar = "=" * 72
    print(bar, flush=True)
    print(
        f"NEW EPISODE STARTING  ep={ep_i}  pool_ep_rew RESET to +0.00000  "
        f"room={room}  policy_v={policy_v}",
        flush=True,
    )
    print(bar, flush=True)


def _sync_weights(policy, client, *, min_version: int) -> int:
    version, data = client.fetch_weights(min_version=min_version)
    if data:
        policy.load_from_bytes(data, version)
        print(
            f"[cutscene-monitor] pulled learner weights v{version} "
            f"({len(data)} bytes)",
            flush=True,
        )
        return int(version)
    if version > 0 and policy.policy_version <= 0:
        version, data = client.fetch_weights(min_version=0)
        if data:
            policy.load_from_bytes(data, version)
            print(
                f"[cutscene-monitor] pulled learner weights v{version} "
                f"({len(data)} bytes)",
                flush=True,
            )
            return int(version)
    return int(policy.policy_version)


def _print_gate(
    *,
    env,
    prev_state: dict[str, Any],
    state: dict[str, Any],
    breakdown: dict[str, float],
    ep_rew: float,
    skip_frames: int,
    qualified_key: str | None,
) -> None:
    from re1_rl.cutscene_reward import (
        cutscene_disqualify_reason,
        format_cutscene_gate_panel,
        kenneth_cutscene_seen,
        skip_session_kind,
    )

    progress = env.unwrapped._progress
    # Prefer stashed settle poses from _apply_post_skip_sync — the control
    # step's prev/new are often mid-room after credit already merged.
    settle_prev = getattr(env.unwrapped, "_last_settled_skip_prev", None) or prev_state
    settle_new = getattr(env.unwrapped, "_last_settled_skip_new", None) or state
    print(
        format_cutscene_gate_panel(
            skip_frames=int(skip_frames),
            prev_state=settle_prev,
            new_state=settle_new,
            episode_start_hp=int(getattr(env.unwrapped, "_episode_start_hp", 0) or 0),
            rewarded_cutscenes=progress.rewarded_cutscenes,
            visited_rooms=progress.visited_rooms,
            positive_rewards_disabled=progress.kenneth_gate_breached,
            qualified_key=qualified_key,
            breakdown=breakdown,
        ),
        flush=True,
    )
    print(f"  pool_ep_rew={_fmt5(ep_rew)}", flush=True)
    kind = skip_session_kind(settle_prev, settle_new)
    why = cutscene_disqualify_reason(
        skip_frames=int(skip_frames),
        prev_state=settle_prev,
        new_state=settle_new,
        episode_start_hp=int(getattr(env.unwrapped, "_episode_start_hp", 0) or 0),
        rewarded_cutscenes=progress.rewarded_cutscenes,
        visited_rooms=progress.visited_rooms,
    )
    alerts = _schema_alerts(
        breakdown=breakdown,
        room=str(state.get("room_id", "") or ""),
        skip_frames=int(skip_frames),
        skip_kind=kind,
        kenneth_seen=kenneth_cutscene_seen(
            progress.rewarded_cutscenes, visited_rooms=progress.visited_rooms
        ),
        unpaid_reason=why,
        paid_cutscene=float(breakdown.get("new_cutscene", 0.0)) > 0.0,
    )
    for a in alerts:
        print(f"  !! SCHEMA ALERT: {a}", flush=True)


def run_ppo(args: argparse.Namespace) -> int:
    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.cutscene_reward import format_cutscene_gate_panel  # noqa: F401
    from re1_rl.distributed.inference_policy import InferencePolicy
    from re1_rl.distributed.worker_client import WorkerClient
    from re1_rl.env import ACTION_NAMES, RE1Env

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Append only — never truncate/rename; operators watch this path live.

    client = WorkerClient(
        args.learner_host,
        int(args.learner_port),
        machine_name="cutscene-monitor",
        timeout=60.0,
    )
    checkpoint = Path(args.checkpoint) if args.checkpoint else None
    learner_ok = False
    try:
        learner_ok = bool(client.health())
    except Exception:
        learner_ok = False
    if not learner_ok and (checkpoint is None or not checkpoint.is_file()):
        print(
            f"[cutscene-monitor] learner unhealthy at "
            f"{args.learner_host}:{args.learner_port} and no --checkpoint",
            flush=True,
        )
        return 1

    bridge = BizHawkClient(port=int(args.port), timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    src = (
        f"learner={args.learner_host}:{args.learner_port}"
        if learner_ok
        else f"checkpoint={checkpoint.name}"
    )
    print(
        f"[cutscene-monitor] PPO mode port={args.port} {src} "
        f"(STEP_PENALTY left at training value — ep_rew is the real total)",
        flush=True,
    )
    print(
        "[cutscene-monitor] launching visible EmuHawk — fleet ports untouched",
        flush=True,
    )
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={int(args.port)}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
    )

    ep_i = 0
    step_i = 0
    term_totals: dict[str, float] = {}
    try:
        bridge.wait_for_client()
        env = RE1Env(
            curriculum_path=ROOT / CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=True,
        )
        # Exact make_env / fleet actor parity (scripts/train_parallel.py):
        #   training_speed == cutscene_speed, skip_chunk, invisible_during_skip=headless
        # Extra logging only — do not diverge skip physics from workers.
        train_speed = int(args.training_speed)
        cutscene_speed = (
            int(args.cutscene_speed)
            if int(args.cutscene_speed) > 0
            else train_speed
        )
        if cutscene_speed != train_speed:
            print(
                f"[cutscene-monitor] WARNING: cutscene_speed={cutscene_speed} != "
                f"training_speed={train_speed} — fleet make_env keeps them equal",
                flush=True,
            )
        env._ram_skip.training_speed = train_speed
        env._ram_skip.cutscene_speed = cutscene_speed
        env._ram_skip.skip_chunk = int(args.skip_chunk)
        # Fleet: invisible_during_skip = headless. Default True to match WH2/WH1
        # actors (pking --no-headless uses False). Window can still be open;
        # Lua blanks draw during skip the same way headless workers do.
        env._ram_skip.invisible_during_skip = bool(args.invisible_skip)
        env._ram_skip.use_engine_patches = True
        env.knife_echo_joypad = False
        bridge.set_speed(train_speed)
        print(
            f"[cutscene-monitor] FLEET SKIP PARITY: "
            f"training_speed={train_speed} cutscene_speed={cutscene_speed} "
            f"skip_chunk={env._ram_skip.skip_chunk} async_cutscene_skip=True "
            f"invisible_during_skip={env._ram_skip.invisible_during_skip} "
            f"use_engine_patches={env._ram_skip.use_engine_patches} "
            f"frame_skip={env.frame_skip} "
            f"(make_env defaults speed={FLEET_TRAINING_SPEED} "
            f"chunk={FLEET_SKIP_CHUNK})",
            flush=True,
        )
        if int(args.play_speed) != train_speed:
            print(
                f"[cutscene-monitor] NOTE: --play-speed={args.play_speed} ignored; "
                f"in-control speed is training_speed={train_speed}",
                flush=True,
            )

        # Policy obs is CHW (VecTransposeImage); env frames are HWC. Build the
        # InferencePolicy with the learner shell spaces so prepare_obs_for_policy
        # transposes — same path as fleet workers.
        from re1_rl.async_fleet import load_async_learner
        from re1_rl.distributed.weights import export_policy_state_dict

        shell = load_async_learner(
            device=args.device,
            resume=checkpoint if (not learner_ok and checkpoint is not None) else None,
            tb_log=None,
        )
        policy = InferencePolicy(
            shell.observation_space, shell.action_space, args.device
        )
        if learner_ok:
            version = _sync_weights(policy, client, min_version=0)
        else:
            assert checkpoint is not None
            policy.load_from_state_dict(export_policy_state_dict(shell), 1)
            version = 1
            print(
                f"[cutscene-monitor] loaded disk checkpoint {checkpoint.name} "
                f"(fleet learner down)",
                flush=True,
            )
        if policy.policy_version <= 0:
            print("[cutscene-monitor] no policy weights", flush=True)
            return 1

        obs, info = env.reset()
        state0 = info.get("state") or env.unwrapped._prev_state or {}
        _print_episode_banner(
            ep_i, room=state0.get("room_id"), policy_v=int(version)
        )
        print(
            "[cutscene-monitor] every line: rew / pool_ep_rew — gate panel on "
            "new_room / new_cutscene / cutscene_key "
            "(pool_ep_rew = sum of float32 step rewards, same buffer as "
            "collect_rollout -> WorkerRollout.rewards)",
            flush=True,
        )
        print(
            "[cutscene-monitor] SCHEMA watch: !! SCHEMA ALERT lines flag "
            "payouts that defy re-exploration-rewards skill buckets/exceptions",
            flush=True,
        )

        last_weight_pull = time.monotonic()
        rng = np.random.default_rng(0)
        # Exact training-pool channel: float32 step rewards only (not a parallel
        # float64 running sum, not breakdown totals).
        pool_rewards = np.zeros(int(args.steps) + 8, dtype=np.float32)
        pool_n = 0
        ep_pool_start = 0

        while step_i < int(args.steps):
            if learner_ok and (
                time.monotonic() - last_weight_pull >= float(args.weight_refresh_s)
            ):
                version = _sync_weights(
                    policy, client, min_version=policy.policy_version + 1
                )
                last_weight_pull = time.monotonic()

            if policy.policy_version <= 0:
                time.sleep(0.2)
                continue

            prev_state = dict(env.unwrapped._prev_state or {})
            masks = np.asarray(env.unwrapped.action_masks(), dtype=bool)
            legal = np.flatnonzero(masks)
            if len(legal) == 0:
                time.sleep(0.05)
                continue

            try:
                action, value, _lp = policy.predict_masked(
                    _batch_obs(obs), masks[None, :]
                )
            except Exception as exc:
                print(f"[cutscene-monitor] predict failed: {exc!r}", flush=True)
                action = int(rng.choice(legal))
                value = float("nan")
            if action < 0 or action >= len(masks) or not masks[action]:
                action = int(rng.choice(legal))

            obs, rew, term, trunc, info = env.step(int(action))
            # Same assignment as collect_rollout: rewards[step] = rew (float32).
            if pool_n >= pool_rewards.shape[0]:
                pool_rewards = np.concatenate(
                    [pool_rewards, np.zeros(max(256, int(args.steps) // 4), dtype=np.float32)]
                )
            pool_rewards[pool_n] = np.float32(rew)
            pool_n += 1
            # Cumulative episode return from the pool buffer only.
            pool_ep_rew = float(pool_rewards[ep_pool_start:pool_n].sum())
            rew_f = float(pool_rewards[pool_n - 1])
            step_i += 1
            state = info.get("state") or {}
            bd = _hits(info.get("reward_breakdown"))
            for k, v in bd.items():
                term_totals[k] = term_totals.get(k, 0.0) + v

            room = str(state.get("room_id", ""))
            ck = state.get("cutscene_key")
            settled_skip = int(
                getattr(env.unwrapped, "_last_settled_skip_frames", 0) or 0
            )
            settled_key = getattr(env.unwrapped, "_last_settled_cutscene_key", None)
            # Door revisits pay no new_room — still dump the gate so unpaid
            # Barry/Kenneth skips are visible (not only the first discovery).
            # Capture settle frames BEFORE clearing — clearing then falling back
            # to step_emulated_frames lied as "skip_frames=4 < 20".
            skip_settle_event = settled_skip > 0
            gate_event = bool(
                bd.get("new_cutscene")
                or bd.get("new_room")
                or ck
                or settled_key
                or skip_settle_event
            )
            line = (
                f"[ppo-mon] ep={ep_i} #{step_i:5d} {ACTION_NAMES[int(action)]:<12} "
                f"rew={_fmt5(rew_f)} pool_ep_rew={_fmt5(pool_ep_rew)} "
                f"room={room} v={policy.policy_version} V={value:+.5f}"
            )
            if bd:
                line += " " + ",".join(
                    f"{k}:{_fmt5(v)}" for k, v in sorted(bd.items())
                )
            if ck:
                line += f" cutscene_key={ck!r}"
            elif settled_key:
                line += f" settled_key={settled_key!r}"
            # Every step: pool_ep_rew is sum(WorkerRollout.rewards[:]) for this ep.
            print(line, flush=True)

            if gate_event:
                _print_gate(
                    env=env,
                    prev_state=prev_state,
                    state=state,
                    breakdown=bd,
                    ep_rew=pool_ep_rew,
                    skip_frames=settled_skip,
                    qualified_key=settled_key if settled_key is not None else ck,
                )
                env.unwrapped._last_settled_skip_frames = 0
                env.unwrapped._last_settled_cutscene_key = None
                env.unwrapped._last_settled_skip_prev = None
                env.unwrapped._last_settled_skip_new = None
                env.unwrapped._last_settled_skip_kind = None
            elif bd:
                # Non-skip large hits (pickups, combat, …) — still schema-check.
                progress = env.unwrapped._progress
                from re1_rl.cutscene_reward import kenneth_cutscene_seen

                alerts = _schema_alerts(
                    breakdown=bd,
                    room=room,
                    skip_frames=0,
                    skip_kind=None,
                    kenneth_seen=kenneth_cutscene_seen(
                        progress.rewarded_cutscenes,
                        visited_rooms=progress.visited_rooms,
                    ),
                    unpaid_reason=None,
                    paid_cutscene=False,
                )
                for a in alerts:
                    print(f"  !! SCHEMA ALERT: {a}", flush=True)

            # Authentic float32 sum stays in pool_rewards; jsonl is 5dp only.
            row = _round_nums(
                {
                    "ep": ep_i,
                    "step": step_i,
                    "action": ACTION_NAMES[int(action)],
                    "reward": rew_f,
                    "pool_ep_reward": pool_ep_rew,
                    "pool_step_index": pool_n - 1,
                    "room": room,
                    "cutscene_key": ck,
                    "settled_key": settled_key,
                    "breakdown": dict(bd),
                    "policy_version": int(policy.policy_version),
                    "value": float(value) if value == value else None,
                    "term": bool(term),
                    "trunc": bool(trunc),
                }
            )
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

            if term or trunc:
                print(
                    f"[cutscene-monitor] EPISODE {ep_i} END "
                    f"term={term} trunc={trunc} steps={step_i} "
                    f"pool_ep_rew={_fmt5(pool_ep_rew)} "
                    f"(sum float32 pool[{ep_pool_start}:{pool_n}])",
                    flush=True,
                )
                print("[cutscene-monitor] episode term totals:", flush=True)
                for k in sorted(term_totals):
                    print(f"  {k}: {_fmt5(term_totals[k])}", flush=True)
                if step_i >= int(args.steps):
                    break
                ep_i += 1
                ep_pool_start = pool_n
                term_totals = {}
                obs, info = env.reset()
                state0 = info.get("state") or {}
                # pool_ep_rew resets via ep_pool_start — next line must read +0.
                _print_episode_banner(
                    ep_i,
                    room=state0.get("room_id"),
                    policy_v=int(policy.policy_version),
                )
                assert abs(float(pool_rewards[ep_pool_start:pool_n].sum())) < 1e-9

        pool_total = float(pool_rewards[:pool_n].sum()) if pool_n else 0.0
        print(
            f"[cutscene-monitor] done steps={step_i} "
            f"pool_total_rew={_fmt5(pool_total)} "
            f"(n={pool_n} float32) jsonl={out_path}",
            flush=True,
        )
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        bridge.close()
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--human",
        action="store_true",
        help="stick harness (play_human) instead of live PPO",
    )
    ap.add_argument("--port", type=int, default=MONITOR_PORT)
    ap.add_argument("--learner-host", default=DEFAULT_LEARNER_HOST)
    ap.add_argument("--learner-port", type=int, default=DEFAULT_LEARNER_PORT)
    ap.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="PPO zip when learner is down (resume / offline watch)",
    )
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--steps", type=int, default=5000)
    ap.add_argument(
        "--training-speed",
        type=int,
        default=FLEET_TRAINING_SPEED,
        help="in-control + skip restore speed (fleet default 6400)",
    )
    ap.add_argument(
        "--play-speed",
        type=int,
        default=FLEET_TRAINING_SPEED,
        help="deprecated alias; use --training-speed (must match cutscene restore)",
    )
    ap.add_argument(
        "--cutscene-speed",
        type=int,
        default=0,
        help="skip turbo (0 = same as --training-speed, fleet make_env style)",
    )
    ap.add_argument(
        "--invisible-skip",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Lua invisible during skip (fleet: True when headless; default True)",
    )
    ap.add_argument("--skip-chunk", type=int, default=FLEET_SKIP_CHUNK)
    ap.add_argument("--weight-refresh-s", type=float, default=60.0)
    ap.add_argument("--heartbeat", type=int, default=25)
    ap.add_argument(
        "--step-print-eps",
        type=float,
        default=0.0,
        help="also print steps whose |rew| exceeds this (0 = all non-zero)",
    )
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args, unknown = ap.parse_known_args()
    if args.human:
        return _run_human(unknown)
    if unknown:
        print(f"[cutscene-monitor] unknown args: {unknown}", flush=True)
        return 2
    return run_ppo(args)


if __name__ == "__main__":
    raise SystemExit(main())
