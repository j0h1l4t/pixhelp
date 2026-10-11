"""dxcam capture: blocking grab() run off the event loop; frame copied before return."""

from __future__ import annotations

import sys

import numpy as np

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import dxcam  # type: ignore[import-not-found]


class CaptureError(RuntimeError):
    pass


class ScreenCapture:
    """Owns one dxcam instance. grab() is blocking -> always call via to_thread.

    foreground_only asks Win32 for the foreground window rect on every grab and
    captures only that region (specs.md §7: the agent then cannot see
    background windows, and OCR gets a smaller bitmap).
    """

    def __init__(
        self,
        monitor: int = 0,
        region: tuple[int, int, int, int] | None = None,
        foreground_only: bool = False,
    ):
        if not IS_WINDOWS:
            raise CaptureError("dxcam requires native Windows (not WSL)")
        self._region = region
        self.foreground_only = foreground_only
        self.last_region = region  # region actually used by the latest grab()
        self._make_dpi_aware()
        self._cam = dxcam.create(output_device=monitor, output_color="BGRA")
        if self._cam is None:
            raise CaptureError(f"dxcam could not open output_device={monitor}")
        width, height = self._cam.width, self._cam.height
        if region and (region[2] > width or region[3] > height):
            raise CaptureError(f"region {region} exceeds {width}x{height}")

    @staticmethod
    def _make_dpi_aware() -> None:
        """GetWindowRect returns scaled coords in a non-DPI-aware process, but
        dxcam captures physical pixels — opt into per-monitor v2 awareness."""
        import ctypes

        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass

    def _foreground_region(self) -> tuple[int, int, int, int] | None:
        import ctypes
        from ctypes import wintypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        if right <= left or bottom <= top:
            return None
        width, height = self._cam.width, self._cam.height
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right <= left or bottom <= top:
            return None
        return (left, top, right, bottom)

    @property
    def width(self) -> int:
        return self._cam.width

    @property
    def height(self) -> int:
        return self._cam.height

    def grab(self, timeout_s: float = 2.0) -> np.ndarray:
        """Grab the latest frame. Always new_frame_only=False: at 5-10s polling
        the desktop is usually 'idle' and the default grab() returns None.

        The returned array is a copy — dxcam reuses its buffer, and the caller
        will await across OCR/JPEG with this frame in hand.

        `last_region` records the region actually captured (dynamic under
        foreground_only) so OCR rects map back to full-screen coordinates.
        """
        import time

        region = self._region
        if self.foreground_only:
            region = self._foreground_region() or self._region
        self.last_region = region

        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            frame = self._cam.grab(new_frame_only=False, region=region)
            if frame is not None:
                return frame.copy()
        raise CaptureError(f"no frame within {timeout_s}s")

    def close(self) -> None:
        self._cam.stop()
