"""Wait out door transitions, cutscenes, dialogue boxes, and scripted scenes.

Three skip triggers:
  - GAME_MODE bit 0x80 clear  -> door / cutscene / FMV (engine takes control)
  - MESSAGE_FLAG bit 0x80 set -> modal talk/examine text box (Barry dialogue
    etc.), which does NOT clear the in-control bit
  - SCENE_FLAG bit 0x10 set   -> scripted scene span between camera cuts
    (player frozen, in-control bit still set, no message window). Hunter/dog
    kill animations use this path — never mash Cross at 0 HP or Continue reloads.

The burn loop runs ENTIRELY inside the Lua client (bridge.fast_forward):
engine patches with cutscene turbo forced on; cross taps only for modal
dialogue / scripted scenes (not pure cutscenes/FMV/doors). Max speedmode +
optional invisible emulation — one socket round-trip per chunk. Speed is
restored to training_speed by the Lua side when the skip condition clears.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from re1_rl.memory_map import (
    CUTSCENE_TURBO_ADDR,
    CUTSCENE_TURBO_RESTORE,
    CUTSCENE_TURBO_VALUE,
    DOOR_SKIP_PATCH_ADDR,
    DOOR_SKIP_PATCH_VALUE,
    GAME_MODE,
    GAME_STATE,
    IN_CONTROL_MASK,
    MESSAGE_FLAG,
    MESSAGE_FLAG_MASK,
    OPENING_GAMEPLAY_TEASER_GAME_STATE,
    OPENING_NARRATION_GAME_MODE,
    OPTIONS_MENU_GAME_MODE,
    OPTIONS_MENU_GAME_STATE,
    PAUSE_MENU_GAME_MODE,
    PAUSE_MENU_GAME_MODES,
    PAUSE_MENU_GAME_STATE,
    PAUSE_MENU_GAME_STATE_MASK,
    PLAYSTATION_LOGO_GAME_STATE,
    PLAYER_HP,
    ROOM_ID,
    SCENE_FLAG,
    SCENE_FLAG_MASK,
    STAGE_ID,
    player_died,
)

if TYPE_CHECKING:
    from re1_rl.bizhawk_bridge import BizHawkClient

# RAM fields required for needs_skip / item-inventory classification.
SKIP_POLL_RAM_FIELDS: list[tuple[str, int, str]] = [
    ("game_state", GAME_STATE, "u32"),
    ("game_mode", GAME_MODE, "u8"),
    ("msg_flag", MESSAGE_FLAG, "u8"),
    ("scene_flag", SCENE_FLAG, "u8"),
]

SKIP_POLL_FIELDS: list[tuple[str, int, str]] = [
    ("game_mode", GAME_MODE, "u8"),
    ("msg_flag", MESSAGE_FLAG, "u8"),
    ("scene_flag", SCENE_FLAG, "u8"),
    ("stage_id", STAGE_ID, "u8"),
    ("room_id", ROOM_ID, "u8"),
]

# Safety cap only — Wesker/Barry can run thousands of frames even at turbo.
DEFAULT_WAIT_MAX_FRAMES = 12000

# BizHawk client.speedmode percent values.
DEFAULT_TRAINING_SPEED = 6400
DEFAULT_CUTSCENE_SPEED = 6400  # match training turbo; override lower for human viewing

# Frames burned per Lua fast_forward round-trip. Big enough that socket RTT
# is negligible, small enough that Ctrl+C lands within a fraction of a second.
DEFAULT_SKIP_CHUNK = 600

# High byte of GAME_STATE while not in player control.
DOOR_GAME_MODES = frozenset({0x42, 0x46})
NARRATION_GAME_MODE = OPENING_NARRATION_GAME_MODE


def room_code(stage_id: int, room_id: int) -> str:
    return f"{int(stage_id) + 1}{int(room_id):02X}"


def in_control_from_ram(ram: dict[str, int | float]) -> bool:
    return bool(int(ram.get("game_mode", 0)) & IN_CONTROL_MASK)


def message_open_from_ram(ram: dict[str, int | float]) -> bool:
    """Modal talk/examine text box open (in-control bit stays SET)."""
    return bool(int(ram.get("msg_flag", 0)) & MESSAGE_FLAG_MASK)


def options_menu_from_ram(ram: dict[str, int | float]) -> bool:
    """In-game OPTIONS / CONFIG (``gs=0x80808000``, ``mode=0x80``)."""
    return (
        int(ram.get("game_state", 0)) == OPTIONS_MENU_GAME_STATE
        and int(ram.get("game_mode", 0)) == OPTIONS_MENU_GAME_MODE
    )


def pause_menu_tree_from_ram(ram: dict[str, int | float]) -> bool:
    """START -> ITEM / STATUS / ECG / MAP — all pause-menu RAM families."""
    mode = int(ram.get("game_mode", 0))
    if mode not in PAUSE_MENU_GAME_MODES:
        return False
    gs = int(ram.get("game_state", 0))
    if gs in (PLAYSTATION_LOGO_GAME_STATE, OPENING_GAMEPLAY_TEASER_GAME_STATE):
        return False
    # ITEM/STATUS (0x40xxxxxx) and ECG health pages (0x60xxxxxx) share the
    # 0x0080xxxx session tag; in-control play uses 0x80xxxxxx instead.
    top = gs & 0xF0000000
    if top not in (0x40000000, 0x60000000):
        return False
    if (gs & 0x00FF0000) < 0x00800000:
        return False
    return True


def pause_menu_modal_from_ram(ram: dict[str, int | float]) -> bool:
    """Yes/No and examine prompts inside START/ITEM/STATUS (cross to accept)."""
    return pause_menu_tree_from_ram(ram) and message_open_from_ram(ram)


def in_game_menu_from_ram(ram: dict[str, int | float]) -> bool:
    """Any in-run pause UI — never pay cutscene rewards; ECG pages never turbo."""
    return pause_menu_tree_from_ram(ram) or options_menu_from_ram(ram)


def scene_active_from_ram(ram: dict[str, int | float]) -> bool:
    """Scripted scene span (player frozen; in-control bit stays SET)."""
    if pause_menu_tree_from_ram(ram) or options_menu_from_ram(ram):
        return False
    sf = int(ram.get("scene_flag", 0))
    if sf & SCENE_FLAG_MASK:
        return True
    if not in_control_from_ram(ram) or message_open_from_ram(ram):
        return False
    # Kenneth / in-room camera scripts: scene departs idle 0x80 (e.g. 0x84).
    return (sf & 0x7F) != 0


def item_inventory_screen_from_ram(ram: dict[str, int | float]) -> bool:
    """START menu tree: ITEM grid, STATUS/ECG, MAP — not OPTIONS/CONFIG."""
    return pause_menu_tree_from_ram(ram)


def needs_skip_from_ram(ram: dict[str, int | float]) -> bool:
    if pause_menu_tree_from_ram(ram):
        # ECG / STATUS idle pages: no turbo. Yes/No examine prompts: mash cross.
        return pause_menu_modal_from_ram(ram)
    if options_menu_from_ram(ram):
        return False
    return (
        not in_control_from_ram(ram)
        or message_open_from_ram(ram)
        or scene_active_from_ram(ram)
    )


class RamSkipper:
    """Block until the engine returns player control."""

    def __init__(
        self,
        bridge: BizHawkClient,
        *,
        training_speed: int = DEFAULT_TRAINING_SPEED,
        cutscene_speed: int = DEFAULT_CUTSCENE_SPEED,
        skip_chunk: int = DEFAULT_SKIP_CHUNK,
        use_engine_patches: bool = True,
        invisible_during_skip: bool = True,
    ) -> None:
        self.bridge = bridge
        self.training_speed = int(training_speed)
        self.cutscene_speed = int(cutscene_speed)
        self.skip_chunk = int(skip_chunk)
        self.use_engine_patches = bool(use_engine_patches)
        self.invisible_during_skip = bool(invisible_during_skip)
        # Mid-skip peaks from Lua fast_forward (Kenneth 0x84, dialogue msg, …).
        self.last_skip_peak_scene_flag: int | None = None
        self.last_skip_peak_msg_flag: int | None = None

    def clear_skip_script_peaks(self) -> None:
        self.last_skip_peak_scene_flag = None
        self.last_skip_peak_msg_flag = None

    def note_skip_script_peaks(
        self, *, peak_scene_flag: int | None = None, peak_msg_flag: int | None = None
    ) -> None:
        """Accumulate script evidence across skip chunks for cutscene qualify."""
        from re1_rl.cutscene_reward import scene_flag_shows_script

        if peak_scene_flag is not None:
            ps = int(peak_scene_flag) & 0xFF
            cur = self.last_skip_peak_scene_flag
            if cur is None or (
                scene_flag_shows_script(ps) and not scene_flag_shows_script(int(cur))
            ):
                self.last_skip_peak_scene_flag = ps
        if peak_msg_flag is not None:
            pm = int(peak_msg_flag) & 0xFF
            cur_m = self.last_skip_peak_msg_flag
            if cur_m is None or (pm != 0 and int(cur_m) == 0):
                self.last_skip_peak_msg_flag = pm

    def install_engine_patches(self) -> None:
        """Door-skip + in-engine cutscene turbo (re-applied every frame by Lua)."""
        self.bridge.set_patches(
            [(DOOR_SKIP_PATCH_ADDR, "u16", DOOR_SKIP_PATCH_VALUE)],
            {
                "addr": CUTSCENE_TURBO_ADDR,
                "on_value": CUTSCENE_TURBO_VALUE,
                "off_value": CUTSCENE_TURBO_RESTORE,
                "mode_addr": GAME_MODE,
                "mask": IN_CONTROL_MASK,
            },
        )

    def clear_engine_patches(self) -> None:
        """Disable any door-skip / cutscene-turbo patches from older runs."""
        self.bridge.set_patches([], turbo=None)

    def skip_uncontrolled(
        self,
        max_frames: int = DEFAULT_WAIT_MAX_FRAMES,
        *,
        chunk: int | None = None,
        prev_hp: int = 0,
        episode_start_hp: int = 0,
    ) -> tuple[int, bool]:
        """Return emulator frames burned while waiting.

        Each iteration is ONE bridge.fast_forward round-trip: the Lua side
        applies patches (turbo forced), taps cross only for dialogue/scene,
        frame-advances up to ``chunk`` frames, and restores speed/render
        before returning.
        """
        chunk = self.skip_chunk if chunk is None else int(chunk)
        ram = self.bridge.read_ram(SKIP_POLL_RAM_FIELDS)
        if not needs_skip_from_ram(ram):
            return 0, False

        if self.use_engine_patches:
            self.install_engine_patches()
        # BizHawk caps vary; clamp high but prefer user's cutscene_speed.
        turbo = max(int(self.cutscene_speed), 3200)
        burned = 0
        death_abort = False
        # Seed peaks from the pre-burn poll (session may start already on 0x84).
        self.note_skip_script_peaks(
            peak_scene_flag=int(ram.get("scene_flag", 0) or 0),
            peak_msg_flag=int(ram.get("msg_flag", 0) or 0),
        )
        while burned < max_frames:
            ram = self.bridge.read_ram(SKIP_POLL_RAM_FIELDS)
            if not needs_skip_from_ram(ram):
                break
            self.note_skip_script_peaks(
                peak_scene_flag=int(ram.get("scene_flag", 0) or 0),
                peak_msg_flag=int(ram.get("msg_flag", 0) or 0),
            )
            res = self.bridge.fast_forward(
                min(max(1, int(chunk)), max_frames - burned),
                mode_addr=GAME_MODE,
                mask=IN_CONTROL_MASK,
                speed=turbo,
                restore_speed=self.training_speed,
                invisible=self.invisible_during_skip,
                msg_addr=MESSAGE_FLAG,
                msg_mask=MESSAGE_FLAG_MASK,
                scene_addr=SCENE_FLAG,
                scene_mask=SCENE_FLAG_MASK,
                death_hp_addr=PLAYER_HP,
                abort_on_zero_hp=True,
            )
            if "peak_scene_flag" in res or "peak_msg_flag" in res:
                self.note_skip_script_peaks(
                    peak_scene_flag=res.get("peak_scene_flag"),
                    peak_msg_flag=res.get("peak_msg_flag"),
                )
            step_burned = int(res["burned"])
            burned += step_burned
            if res.get("death_abort"):
                death_abort = True
                break
            hp_ram = self.bridge.read_ram([("player_hp", PLAYER_HP, "u16")])
            if player_died(
                int(hp_ram.get("player_hp", 0)),
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            ):
                death_abort = True
                break
            done = (
                res["in_control"]
                and not res.get("msg_open")
                and not res.get("scene_active")
            )
            if done or step_burned == 0:
                break
        return burned, death_abort
