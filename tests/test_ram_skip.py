"""Unit tests for cutscene/dialogue wait helpers (Lua-side fast_forward flow)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.memory_map import (
    GAME_MODE,
    IN_CONTROL_MASK,
    MESSAGE_FLAG,
    MESSAGE_FLAG_MASK,
    OPTIONS_MENU_GAME_MODE,
    OPTIONS_MENU_GAME_STATE,
    PAUSE_MENU_GAME_MODE,
    PAUSE_MENU_GAME_STATE,
    PLAYSTATION_LOGO_GAME_MODE,
    PLAYSTATION_LOGO_GAME_STATE,
    SCENE_FLAG,
    SCENE_FLAG_MASK,
    STATUS_ECG_GAME_MODE,
    STATUS_ECG_GAME_STATE,
)
from re1_rl.ram_skip import (
    RamSkipper,
    in_control_from_ram,
    in_game_menu_from_ram,
    item_inventory_screen_from_ram,
    message_open_from_ram,
    needs_skip_from_ram,
    options_menu_from_ram,
    pause_menu_modal_from_ram,
    pause_menu_tree_from_ram,
    room_code,
    scene_active_from_ram,
)


def test_room_code() -> None:
    assert room_code(0, 5) == "105"
    assert room_code(0, 6) == "106"


def test_in_control_from_ram() -> None:
    assert in_control_from_ram({"game_mode": IN_CONTROL_MASK})
    assert not in_control_from_ram({"game_mode": 0x42})


def test_message_open_from_ram() -> None:
    assert message_open_from_ram({"msg_flag": MESSAGE_FLAG_MASK})
    assert not message_open_from_ram({"msg_flag": 0x00})
    assert not message_open_from_ram({})


def test_scene_active_from_ram() -> None:
    assert scene_active_from_ram({"scene_flag": 0x90})
    assert scene_active_from_ram(
        {
            "game_mode": IN_CONTROL_MASK,
            "msg_flag": 0x00,
            "scene_flag": 0x84,
        }
    )
    assert not scene_active_from_ram({"scene_flag": 0x80})
    assert not scene_active_from_ram({})


def test_kenneth_tea_room_scene_needs_skip() -> None:
    assert needs_skip_from_ram(
        {
            "game_mode": IN_CONTROL_MASK,
            "msg_flag": 0x00,
            "scene_flag": 0x84,
            "game_state": 0x80840004,
        }
    )


def test_needs_skip_from_ram() -> None:
    # cutscene: control bit clear
    assert needs_skip_from_ram({"game_mode": 0x42, "msg_flag": 0x00})
    # dialogue: control bit set but message window open
    assert needs_skip_from_ram({"game_mode": IN_CONTROL_MASK, "msg_flag": 0x80})
    # scripted scene: control bit set, no message, scene bit set
    assert needs_skip_from_ram(
        {"game_mode": IN_CONTROL_MASK, "msg_flag": 0x00, "scene_flag": 0x90}
    )
    # normal play
    assert not needs_skip_from_ram(
        {"game_mode": IN_CONTROL_MASK, "msg_flag": 0x00, "scene_flag": 0x80}
    )
    # ITEM inventory: in_control clear but not a cutscene — do not turbo-skip
    assert not needs_skip_from_ram(
        {
            "game_mode": PAUSE_MENU_GAME_MODE,
            "game_state": PAUSE_MENU_GAME_STATE,
            "msg_flag": 0x00,
            "scene_flag": 0x80,
        }
    )
    # STATUS/ECG sub-screen (low byte 0x04) — same START menu tree
    assert not needs_skip_from_ram(
        {
            "game_mode": PAUSE_MENU_GAME_MODE,
            "game_state": 0x40808004,
            "msg_flag": 0x00,
            "scene_flag": 0x80,
        }
    )


    # STATUS health-bar page (mid byte drifts outside 0x408080xx mask)
    assert not needs_skip_from_ram(
        {
            "game_mode": PAUSE_MENU_GAME_MODE,
            "game_state": 0x40808104,
            "msg_flag": 0x00,
            "scene_flag": 0x80,
        }
    )


def test_pause_menu_blocks_scene_skip_even_with_scene_bit() -> None:
    menu = {
        "game_mode": PAUSE_MENU_GAME_MODE,
        "game_state": 0x40808104,
        "msg_flag": 0x00,
        "scene_flag": 0x90,
    }
    assert pause_menu_tree_from_ram(menu)
    assert not scene_active_from_ram(menu)
    assert not needs_skip_from_ram(menu)


def test_options_menu_never_needs_skip() -> None:
    ram = {
        "game_mode": OPTIONS_MENU_GAME_MODE,
        "game_state": OPTIONS_MENU_GAME_STATE,
        "msg_flag": 0x00,
        "scene_flag": 0x80,
    }
    assert options_menu_from_ram(ram)
    assert in_game_menu_from_ram(ram)
    assert not needs_skip_from_ram(ram)


def test_playstation_logo_still_needs_skip() -> None:
    assert needs_skip_from_ram(
        {
            "game_mode": PLAYSTATION_LOGO_GAME_MODE,
            "game_state": PLAYSTATION_LOGO_GAME_STATE,
            "msg_flag": 0x00,
            "scene_flag": 0x80,
        }
    )


def test_pause_menu_yes_no_prompt_needs_cross_advance() -> None:
    """Emblem 'Will you take?' — msg open on ITEM screen must auto-accept."""
    ram = {
        "game_mode": PAUSE_MENU_GAME_MODE,
        "game_state": PAUSE_MENU_GAME_STATE,
        "msg_flag": MESSAGE_FLAG_MASK,
        "scene_flag": 0x80,
    }
    assert pause_menu_modal_from_ram(ram)
    assert needs_skip_from_ram(ram)


def test_status_ecg_health_screen_never_needs_skip() -> None:
    """ECG health-bar page (gs=0x60808000, mode=0x60) — live hunt 2026-07-08."""
    ram = {
        "game_mode": STATUS_ECG_GAME_MODE,
        "game_state": STATUS_ECG_GAME_STATE,
        "msg_flag": 0x00,
        "scene_flag": 0x80,
    }
    assert pause_menu_tree_from_ram(ram)
    assert not needs_skip_from_ram(ram)


def test_item_inventory_screen_from_ram() -> None:
    assert item_inventory_screen_from_ram(
        {"game_mode": PAUSE_MENU_GAME_MODE, "game_state": PAUSE_MENU_GAME_STATE}
    )
    assert item_inventory_screen_from_ram(
        {"game_mode": PAUSE_MENU_GAME_MODE, "game_state": 0x40808004}
    )
    assert item_inventory_screen_from_ram(
        {"game_mode": PAUSE_MENU_GAME_MODE, "game_state": 0x40808104}
    )
    assert not item_inventory_screen_from_ram(
        {"game_mode": PAUSE_MENU_GAME_MODE, "game_state": 0x80808000}
    )
    # Without game_state the poll is incomplete — do not guess pause menu.
    assert needs_skip_from_ram({"game_mode": PAUSE_MENU_GAME_MODE, "msg_flag": 0})


class FakeBridge:
    """Mimics BizHawkClient.fast_forward: burns frames until control returns
    and any modal message window / scripted scene span ends."""

    def __init__(
        self,
        uncontrolled_frames: int = 40,
        msg_frames: int = 0,
        scene_frames: int = 0,
        death_on_frame: int | None = None,
    ) -> None:
        self.frame = 0
        self.uncontrolled_frames = uncontrolled_frames
        self.msg_frames = msg_frames
        self.scene_frames = scene_frames
        self.death_on_frame = death_on_frame
        self.ff_calls: list[dict] = []
        self.cleared_patches = False
        self.patches_always: list = []
        self.patches_turbo: dict | None = None

    def _hp(self) -> int:
        if self.death_on_frame is not None and self.frame >= self.death_on_frame:
            return 0
        return 96

    def _in_control(self) -> bool:
        return self.frame >= self.uncontrolled_frames

    def _msg_open(self) -> bool:
        return self.frame < self.msg_frames

    def _scene_active(self) -> bool:
        return self.frame < self.scene_frames

    def read_ram(self, fields):
        return {
            "game_mode": IN_CONTROL_MASK if self._in_control() else 0x42,
            "msg_flag": MESSAGE_FLAG_MASK if self._msg_open() else 0x00,
            "scene_flag": 0x90 if self._scene_active() else 0x80,
            "player_hp": self._hp(),
        }

    def set_patches(self, always, turbo=None) -> None:
        self.cleared_patches = always == [] and turbo is None
        self.patches_always = list(always)
        self.patches_turbo = turbo

    def fast_forward(
        self,
        max_frames: int,
        *,
        mode_addr: int,
        mask: int,
        speed: int,
        restore_speed: int,
        invisible: bool,
        msg_addr: int | None = None,
        msg_mask: int = 0x80,
        scene_addr: int | None = None,
        scene_mask: int = 0x10,
        death_hp_addr: int | None = None,
        abort_on_zero_hp: bool = False,
    ) -> dict:
        self.ff_calls.append(
            {
                "max_frames": max_frames,
                "mode_addr": mode_addr,
                "mask": mask,
                "speed": speed,
                "restore_speed": restore_speed,
                "invisible": invisible,
                "msg_addr": msg_addr,
                "msg_mask": msg_mask,
                "scene_addr": scene_addr,
                "scene_mask": scene_mask,
                "death_hp_addr": death_hp_addr,
                "abort_on_zero_hp": abort_on_zero_hp,
            }
        )
        track_msg = msg_addr is not None
        track_scene = scene_addr is not None
        burned = 0
        death_abort = False
        while burned < max_frames and (
            not self._in_control()
            or (track_msg and self._msg_open())
            or (track_scene and self._scene_active())
        ):
            if (
                abort_on_zero_hp
                and death_hp_addr is not None
                and self._hp() <= 0
            ):
                death_abort = True
                break
            self.frame += 1
            burned += 1
        return {
            "burned": burned,
            "mode": IN_CONTROL_MASK if self._in_control() else 0x42,
            "in_control": self._in_control(),
            "msg_open": track_msg and self._msg_open(),
            "scene_active": track_scene and self._scene_active(),
            "death_abort": death_abort,
            "frame": self.frame,
        }


def test_scene_death_abort() -> None:
    """Hunter/dog deaths use scene_flag; skip must abort when HP hits 0."""
    bridge = FakeBridge(uncontrolled_frames=0, scene_frames=40, death_on_frame=8)
    skipper = RamSkipper(bridge, training_speed=100, cutscene_speed=6400)
    burned, death_abort = skipper.skip_uncontrolled(
        max_frames=600,
        chunk=16,
        prev_hp=96,
        episode_start_hp=96,
    )
    assert death_abort
    assert burned < 40


def test_wait_until_control() -> None:
    bridge = FakeBridge(uncontrolled_frames=32)
    skipper = RamSkipper(bridge, training_speed=100, cutscene_speed=6400)
    burned, death_abort = skipper.skip_uncontrolled(max_frames=600, chunk=8)
    assert burned == 32
    assert not death_abort
    assert len(bridge.ff_calls) == 4  # 32 frames / 8-frame chunks
    call = bridge.ff_calls[0]
    assert call["mode_addr"] == GAME_MODE
    assert call["mask"] == IN_CONTROL_MASK
    assert call["speed"] == 6400
    assert call["restore_speed"] == 100
    assert call["msg_addr"] == MESSAGE_FLAG
    assert call["msg_mask"] == MESSAGE_FLAG_MASK
    assert call["scene_addr"] == SCENE_FLAG
    assert call["scene_mask"] == SCENE_FLAG_MASK
    # engine patches installed before the burn
    assert bridge.patches_turbo is not None


def test_wait_until_dialogue_dismissed() -> None:
    # in control the whole time, but a modal text box is open for 24 frames
    bridge = FakeBridge(uncontrolled_frames=0, msg_frames=24)
    skipper = RamSkipper(bridge, training_speed=100, cutscene_speed=6400)
    burned, _ = skipper.skip_uncontrolled(max_frames=600, chunk=8)
    assert burned == 24
    assert len(bridge.ff_calls) == 3


def test_wait_until_scene_ends() -> None:
    # scripted scene span: control bit set, no message, scene bit set 40 frames
    bridge = FakeBridge(uncontrolled_frames=0, scene_frames=40)
    skipper = RamSkipper(bridge, training_speed=100, cutscene_speed=6400)
    burned, _ = skipper.skip_uncontrolled(max_frames=600, chunk=16)
    assert burned == 40
    assert len(bridge.ff_calls) == 3


class _KennethSceneBridge(FakeBridge):
    """Kenneth tea-room scare: in_control + scene_flag 0x84 (not bit 0x10)."""

    def __init__(self, kenneth_frames: int = 40) -> None:
        super().__init__(uncontrolled_frames=0, scene_frames=0)
        self.kenneth_frames = int(kenneth_frames)

    def read_ram(self, fields):
        ram = super().read_ram(fields)
        if self.frame < self.kenneth_frames:
            ram["game_mode"] = IN_CONTROL_MASK
            ram["msg_flag"] = 0x00
            ram["scene_flag"] = 0x84
        else:
            ram["scene_flag"] = 0x80
        return ram

    def _scene_active(self) -> bool:
        return self.frame < self.kenneth_frames


def test_kenneth_scene_skip_burns_frames() -> None:
    bridge = _KennethSceneBridge(kenneth_frames=40)
    skipper = RamSkipper(bridge, training_speed=100, cutscene_speed=6400)
    burned, _ = skipper.skip_uncontrolled(max_frames=600, chunk=16)
    assert burned == 40
    assert len(bridge.ff_calls) >= 1


def test_wait_respects_max_frames_cap() -> None:
    bridge = FakeBridge(uncontrolled_frames=10**9)  # never returns control
    burned, _ = RamSkipper(bridge).skip_uncontrolled(max_frames=64, chunk=16)
    assert burned == 64
    assert len(bridge.ff_calls) == 4


def test_wait_noop_when_in_control() -> None:
    bridge = FakeBridge(uncontrolled_frames=0)
    assert RamSkipper(bridge).skip_uncontrolled() == (0, False)
    assert not bridge.ff_calls


def test_invisible_flag_forwarded() -> None:
    bridge = FakeBridge(uncontrolled_frames=4)
    RamSkipper(bridge, invisible_during_skip=True).skip_uncontrolled(
        max_frames=32, chunk=8,
    )
    assert bridge.ff_calls and bridge.ff_calls[0]["invisible"] is True


def test_install_engine_patches() -> None:
    bridge = FakeBridge()
    RamSkipper(bridge, use_engine_patches=True).install_engine_patches()
    assert not bridge.cleared_patches
    assert bridge.patches_always
    assert bridge.patches_turbo is not None


def test_clear_engine_patches() -> None:
    bridge = FakeBridge()
    RamSkipper(bridge).clear_engine_patches()
    assert bridge.cleared_patches
