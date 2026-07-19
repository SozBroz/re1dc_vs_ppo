"""Launch the newest savestate and record Jill's per-frame grab-related RAM."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.bizhawk_paths import EMUHAWK, LUA, ROM, newest_quicksave  # noqa: E402
from re1_rl.action_mask import action_mask  # noqa: E402
from re1_rl.grab_escape import (  # noqa: E402
    execute_grab_escape_noop,
    grab_bite_transition,
)
from re1_rl.memory_map import (  # noqa: E402
    GAME_MODE,
    GAME_STATE,
    PLAYER_ACTION_AUX,
    PLAYER_ANIM_STATE,
    PLAYER_FACING,
    PLAYER_HP,
    PLAYER_RECOVERY_TIMER,
    PLAYER_X,
    PLAYER_Z,
)

PORT = 7795
FIELDS = [
    ("hp", PLAYER_HP, "u16"),
    ("anim", PLAYER_ANIM_STATE, "u8"),
    ("aux", PLAYER_ACTION_AUX, "u8"),
    ("recovery", PLAYER_RECOVERY_TIMER, "u8"),
    ("game_mode", GAME_MODE, "u8"),
    ("game_state", GAME_STATE, "u32"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
    ("facing", PLAYER_FACING, "u16"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("agent-noop", "baseline", "human"),
        required=True,
    )
    args = parser.parse_args()

    state = newest_quicksave()
    out = ROOT / "data" / f"live_zombie_grab_{args.mode}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    client = BizHawkClient(port=PORT, timeout=300.0, connect_timeout=120.0)
    client.start_server()
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
            "--gdi",
        ],
        cwd=str(EMUHAWK.parent),
    )
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    try:
        client.wait_for_client()
        client.set_speed(100)
        client.load_savestate(str(state))
        client.frameadvance(6)
        previous: dict[str, int] | None = None
        previous_buttons: list[str] = []
        first_bite_frame: int | None = None
        bite_count = 0
        print(
            f"[GRAB_READY] mode={args.mode} state={state} trace={out}",
            flush=True,
        )
        with out.open("w", encoding="utf-8", buffering=1) as log:
            while not stopping:
                if args.mode != "human":
                    frame, _ = client.step(
                        n=1,
                        sticky={},
                        frame_buttons=[{}],
                        abort_on_zero_hp=False,
                    )
                else:
                    frame = client.frameadvance(1)
                buttons = client.read_joypad()
                assert isinstance(buttons, dict)
                ram = {key: int(value) for key, value in client.read_ram(FIELDS).items()}
                row = {
                    "frame": frame,
                    "buttons": sorted(key for key, value in buttons.items() if value),
                    **ram,
                }
                log.write(json.dumps(row, separators=(",", ":")) + "\n")
                if args.mode == "human" and row["buttons"] != previous_buttons:
                    print(
                        f"[HUMAN_INPUT] f={frame} buttons={row['buttons']}",
                        flush=True,
                    )
                previous_buttons = row["buttons"]

                hooks = (ram["anim"], ram["aux"], ram["recovery"])
                prior_hooks = (
                    None
                    if previous is None
                    else (
                        previous["anim"],
                        previous["aux"],
                        previous["recovery"],
                    )
                )
                if hooks != prior_hooks:
                    print(
                        f"[hooks] f={frame} hp={ram['hp']} "
                        f"anim=0x{hooks[0]:02X} aux=0x{hooks[1]:02X} "
                        f"rec={hooks[2]} buttons={row['buttons']}",
                        flush=True,
                    )
                if previous is not None and ram["hp"] < previous["hp"]:
                    if first_bite_frame is None:
                        first_bite_frame = frame
                    bite_count += 1
                    print(
                        f"[GRAB_CANDIDATE] f={frame} "
                        f"hp={previous['hp']}->{ram['hp']} "
                        f"anim=0x{ram['anim']:02X} aux=0x{ram['aux']:02X} "
                        f"rec={ram['recovery']} buttons={row['buttons']}",
                        flush=True,
                    )
                previous_state = (
                    None
                    if previous is None
                    else {
                        **previous,
                        "in_control": bool(previous["game_mode"] & 0x80),
                        "player_anim": previous["anim"],
                        "player_aux": previous["aux"],
                    }
                )
                current_state = {
                    **ram,
                    "in_control": bool(ram["game_mode"] & 0x80),
                    "player_anim": ram["anim"],
                    "player_aux": ram["aux"],
                }
                if (
                    args.mode == "agent-noop"
                    and grab_bite_transition(previous_state, current_state)
                ):
                    mask = action_mask(46, None, grab_escape_pending=True)
                    assert mask[0] and int(mask.sum()) == 1
                    died, mash_frames = execute_grab_escape_noop(client)
                    print(
                        f"[AGENT_NOOP_MASH] detected={frame} action=0 "
                        f"legal={int(mask.sum())} frames={mash_frames} died={died}",
                        flush=True,
                    )
                if first_bite_frame is not None and ram["hp"] <= 0:
                    print(
                        f"[GRAB_RESULT] mode={args.mode} outcome=death "
                        f"first_bite={first_bite_frame} end={frame} "
                        f"duration={frame - first_bite_frame} bites={bite_count}",
                        flush=True,
                    )
                    return 0
                previous = ram
        return 0
    finally:
        try:
            client.quit()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
