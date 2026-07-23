"""Dynamic camera backdrop whitening for RE1 screenshots.

Preprocess per (room, camera):
  - ``original`` — authentic static background (320x240 game plane)
  - ``check_mask`` — pixels statically whitened (backdrop); only these need compare

Each live frame:
  - non-check pixels: keep live framebuffer
  - check pixels: if live == original → white out; else keep live (Jill / enemies)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# BizHawk RE1 screenshot: 240 rows x 350 cols; 320-wide game plane centered.
PILLARBOX_LEFT = 18
GAME_W = 320
GAME_H = 240
WHITE = np.uint8(255)


@dataclass(frozen=True)
class WhitenContext:
    room_code: str
    cam_id: int
    in_control: bool = True
    scene_active: bool = False
    message_open: bool = False
    zombie_attack: bool = False

    def should_apply(self) -> bool:
        """Apply only in clean player control, or during a zombie attack cinema.

        Doors / dialogue / scripted scenes stay authentic. Zombie grab/bite is
        the sole uncontrolled exception — keep whitening so sprites read on white.
        """
        if self.zombie_attack:
            return True
        return self.in_control and not self.scene_active and not self.message_open


@dataclass
class ZombieAttackLatch:
    """Sticky HP-damage latch for whitening during grab/bite cinemas.

    Arms on any player HP drop. Clears only after a short streak of clean
    gameplay control (in control, no scene, no message) with HP not falling.
    """

    active: bool = False
    clear_need: int = 3
    _prev_hp: int | None = None
    _clear_streak: int = 0

    def update(
        self,
        *,
        hp: int,
        in_control: bool,
        scene_active: bool,
        message_open: bool,
    ) -> bool:
        hp_i = int(hp)
        if self._prev_hp is not None and self._prev_hp > 0 and 0 <= hp_i < self._prev_hp:
            self.active = True
            self._clear_streak = 0

        clean = bool(in_control) and not bool(scene_active) and not bool(message_open)
        if self.active:
            hp_stable_or_up = self._prev_hp is None or hp_i >= int(self._prev_hp)
            if clean and hp_stable_or_up:
                self._clear_streak += 1
                if self._clear_streak >= int(self.clear_need):
                    self.active = False
                    self._clear_streak = 0
            else:
                self._clear_streak = 0

        self._prev_hp = hp_i
        return self.active


@dataclass
class CameraMaskPack:
    """Preprocessed originals + flat check indices for one fixed camera."""

    original: np.ndarray  # (GAME_H, GAME_W, 3) uint8
    check_flat_idx: np.ndarray  # (N,) int64
    check_ref: np.ndarray  # (N, 3) uint8 — original RGB at check pixels

    @property
    def check_pixel_count(self) -> int:
        return int(self.check_flat_idx.size)

    @classmethod
    def from_images(cls, original_rgb: np.ndarray, whitened_rgb: np.ndarray) -> CameraMaskPack:
        if original_rgb.shape != whitened_rgb.shape:
            raise ValueError(
                f"original {original_rgb.shape} != whitened {whitened_rgb.shape}"
            )
        # Static preprocess whites backdrop to 255; mechanics pixels stay colored.
        check = np.all(whitened_rgb >= 250, axis=2)
        flat_idx = np.flatnonzero(check.ravel())
        orig_flat = original_rgb.reshape(-1, 3)
        return cls(
            original=np.ascontiguousarray(original_rgb, dtype=np.uint8),
            check_flat_idx=flat_idx.astype(np.int64, copy=False),
            check_ref=orig_flat[flat_idx].copy(),
        )


def apply_dynamic_whiten_game_plane(
    live_game: np.ndarray, pack: CameraMaskPack, *, out: np.ndarray | None = None
) -> np.ndarray:
    """Apply dynamic whitening on the 320x240 game crop (mutates ``out`` if given)."""
    if live_game.shape != (GAME_H, GAME_W, 3):
        raise ValueError(f"expected game plane {(GAME_H, GAME_W, 3)}, got {live_game.shape}")
    target = out if out is not None else live_game.copy()
    if pack.check_flat_idx.size == 0:
        return target
    flat = target.reshape(-1, 3)
    live_chk = flat[pack.check_flat_idx]
    same = np.all(live_chk == pack.check_ref, axis=1)
    if np.any(same):
        flat[pack.check_flat_idx[same]] = WHITE
    return target


def apply_dynamic_whiten_screenshot(
    frame: np.ndarray,
    pack: CameraMaskPack | None,
    *,
    pillarbox_left: int = PILLARBOX_LEFT,
    out: np.ndarray | None = None,
) -> np.ndarray:
    """Whiten inside the game plane of a full BizHawk RGB screenshot."""
    if pack is None:
        return frame
    h, w = frame.shape[:2]
    x0 = pillarbox_left
    x1 = x0 + GAME_W
    if h < GAME_H or w < x1:
        return frame
    target = out if out is not None else frame.copy()
    game = apply_dynamic_whiten_game_plane(frame[:GAME_H, x0:x1], pack)
    target[:GAME_H, x0:x1] = game
    return target


class CameraWhitenBank:
    """Lookup of preprocessed mask packs keyed by (room_code, cam_id)."""

    def __init__(self) -> None:
        self._packs: dict[tuple[str, int], CameraMaskPack] = {}

    def add(self, room_code: str, cam_id: int, pack: CameraMaskPack) -> None:
        self._packs[(str(room_code), int(cam_id))] = pack

    def get(self, room_code: str, cam_id: int) -> CameraMaskPack | None:
        return self._packs.get((str(room_code), int(cam_id)))

    def lookup(self, ctx: WhitenContext) -> CameraMaskPack | None:
        if not ctx.should_apply():
            return None
        return self.get(ctx.room_code, ctx.cam_id)

    def apply(self, frame: np.ndarray, ctx: WhitenContext) -> np.ndarray:
        pack = self.lookup(ctx)
        if pack is None:
            return frame
        return apply_dynamic_whiten_screenshot(frame, pack)

    def __len__(self) -> int:
        return len(self._packs)


def _is_game_room_dir(name: str) -> bool:
    """True for campaign room codes ``1XX``–``5XX`` (not probe folders)."""
    if len(name) != 3:
        return False
    try:
        code = int(name, 16)
    except ValueError:
        return False
    # Stage digit 1–5 (mansion through labs); reject misc probe names.
    stage = code >> 8
    return stage in (1, 2, 3, 4, 5)


# Back-compat alias.
_is_mansion_room_dir = _is_game_room_dir


def load_room_camera_bank(room_dir: str | Path) -> CameraWhitenBank:
    """Load packs for one room. Requires BizHawk original + whitened mask.

    Gaps are allowed (cutscene cams are intentionally absent). Stale BSS-only
    plates are ignored so door-authentic captures win.
    """
    root = Path(room_dir)
    bank = CameraWhitenBank()
    room = root.name.upper()
    import json

    allowed: set[int] | None = None
    meta_path = root / "bizhawk_originals.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ok = {
                int(c["idx"])
                for c in (meta.get("cameras") or [])
                if c.get("ok") is True
            }
            if ok:
                allowed = ok
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            allowed = None

    for cam in range(32):
        if allowed is not None and cam not in allowed:
            continue
        white_path = root / f"cam{cam:02d}_whitened.png"
        bizhawk_path = root / f"cam{cam:02d}_bizhawk_original.png"
        if not bizhawk_path.is_file() or not white_path.is_file():
            continue
        try:
            orig = np.array(Image.open(bizhawk_path).convert("RGB"), dtype=np.uint8)
            white = np.array(Image.open(white_path).convert("RGB"), dtype=np.uint8)
        except OSError:
            continue
        bank.add(room, cam, CameraMaskPack.from_images(orig, white))
    return bank


def load_mansion_camera_bank(project_root: str | Path) -> CameraWhitenBank:
    """Load all campaign ``data/room_cameras/<hex>/`` packs into one bank.

    Stages 1–5 (three-digit room folders). Legacy dining only if 105 missing.
    """
    root = Path(project_root) / "data" / "room_cameras"
    bank = CameraWhitenBank()
    if not root.is_dir():
        return bank
    rooms = [p for p in root.iterdir() if p.is_dir() and _is_game_room_dir(p.name)]
    for sub in sorted(rooms, key=lambda p: int(p.name, 16)):
        room_bank = load_room_camera_bank(sub)
        for key, pack in room_bank._packs.items():
            bank._packs[key] = pack
    # Legacy dining only if mansion 105 is completely missing (never patch holes —
    # cutscene skips and failed hops must stay absent).
    has_105 = any(room == "105" for room, _cam in bank._packs)
    legacy = Path(project_root) / "data" / "dining_room_cameras"
    if legacy.is_dir() and not has_105:
        for key, pack in load_dining_room_bank(legacy)._packs.items():
            bank._packs[key] = pack
    return bank


def mansion_bank_inventory(bank: CameraWhitenBank) -> dict[str, list[int]]:
    """``{room_code: [cam_id, ...]}`` for logging / harness session meta."""
    inv: dict[str, list[int]] = {}
    for room, cam in sorted(bank._packs.keys(), key=lambda k: (int(k[0], 16), k[1])):
        inv.setdefault(room, []).append(int(cam))
    return inv


def load_dining_room_bank(data_dir: str | Path) -> CameraWhitenBank:
    """Load room 105 dining cameras from ``data/dining_room_cameras``.

    Prefers ``cam##_bizhawk_original.png`` (live EmuHawk capture) over
    ``cam##_original.png`` (BSS decode) for the static reference frame.
    """
    root = Path(data_dir)
    bank = CameraWhitenBank()
    room = "105"
    for cam in range(32):
        white_path = root / f"cam{cam:02d}_whitened.png"
        bizhawk_path = root / f"cam{cam:02d}_bizhawk_original.png"
        bss_path = root / f"cam{cam:02d}_original.png"
        orig_path = bizhawk_path if bizhawk_path.exists() else bss_path
        if not orig_path.exists() or not white_path.exists():
            continue
        try:
            orig = np.array(Image.open(orig_path).convert("RGB"), dtype=np.uint8)
            white = np.array(Image.open(white_path).convert("RGB"), dtype=np.uint8)
        except OSError:
            continue
        bank.add(room, cam, CameraMaskPack.from_images(orig, white))
    if not bank:
        raise FileNotFoundError(f"no dining room camera packs under {root}")
    return bank


@dataclass(frozen=True)
class WhitenCoverage:
    check_frac: float
    match_rate: float
    effective_whiten_frac: float
    net_new_white_frac: float
    check_pixels: int
    matched_pixels: int
    mae_check: float
    applied: bool


def measure_whiten_coverage(
    live_rgb: np.ndarray,
    pack: CameraMaskPack | None,
    *,
    is_game_plane: bool = False,
) -> WhitenCoverage:
    """Measure how much dynamic whitening would fire on a live frame."""
    total = GAME_H * GAME_W
    if pack is None:
        return WhitenCoverage(0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, False)
    if is_game_plane:
        live_game = live_rgb[:GAME_H, :GAME_W]
    else:
        live_game = live_rgb[:GAME_H, PILLARBOX_LEFT : PILLARBOX_LEFT + GAME_W]
    check_n = pack.check_pixel_count
    if check_n == 0:
        return WhitenCoverage(0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, True)
    flat_live = live_game.reshape(-1, 3)
    live_chk = flat_live[pack.check_flat_idx]
    same = np.all(live_chk == pack.check_ref, axis=1)
    matched_n = int(np.sum(same))
    raw_white = int(np.sum(np.all(live_game >= 250, axis=2)))
    out_white = raw_white + matched_n  # exact-match backdrop -> white
    mae = float(np.mean(np.abs(live_chk.astype(np.int16) - pack.check_ref.astype(np.int16))))
    return WhitenCoverage(
        check_frac=check_n / total,
        match_rate=matched_n / check_n,
        effective_whiten_frac=matched_n / total,
        net_new_white_frac=(out_white - raw_white) / total,
        check_pixels=check_n,
        matched_pixels=matched_n,
        mae_check=mae,
        applied=True,
    )


def whitened_enabled_from_env() -> bool:
    import os

    return os.environ.get("RE1_CAMERA_WHITEN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
