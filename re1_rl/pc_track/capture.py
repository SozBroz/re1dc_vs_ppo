"""Window capture for GOG Resident Evil via mss."""

from __future__ import annotations

from typing import Any

import numpy as np

DEFAULT_WINDOW_TITLE = "Resident Evil"


def find_window_rect(title_substring: str = DEFAULT_WINDOW_TITLE) -> dict[str, int] | None:
    """Return screen rect {left, top, width, height} for the game window."""
    try:
        import win32gui
    except ImportError:
        # mss-only fallback: full monitor 0
        return None

    rect_holder: list[tuple[int, int, int, int]] = []

    def _callback(hwnd: int, _: Any) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            text = win32gui.GetWindowText(hwnd)
            if title_substring.lower() in text.lower():
                rect_holder.append(win32gui.GetWindowRect(hwnd))
                return False
        return True

    win32gui.EnumWindows(_callback, None)
    if not rect_holder:
        return None
    left, top, right, bottom = rect_holder[0]
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def capture_window(title_substring: str = DEFAULT_WINDOW_TITLE) -> np.ndarray:
    """Grab RGB uint8 screenshot of the RE window."""
    import mss

    with mss.mss() as sct:
        rect = find_window_rect(title_substring)
        if rect is None:
            monitor = sct.monitors[1]
        else:
            monitor = rect
        shot = sct.grab(monitor)
        frame = np.array(shot)[:, :, :3]  # BGRA -> BGR
        return frame[:, :, ::-1]  # RGB
