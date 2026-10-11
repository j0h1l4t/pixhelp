"""Vision payload prep: downscale, pixel budget, lazy JPEG encode (vision path only)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image


def maybe_downscale_bgra(frame: np.ndarray, factor: float) -> np.ndarray:
    if factor >= 1.0:
        return frame
    rgba = frame[..., [2, 1, 0, 3]]
    img = Image.fromarray(rgba)
    img = img.resize(
        (max(1, int(img.width * factor)), max(1, int(img.height * factor))),
        Image.LANCZOS,
    )
    return np.asarray(img)[..., [2, 1, 0, 3]]


def clamp_to_max_pixels(frame: np.ndarray, max_pixels: int) -> np.ndarray:
    """Uniformly shrink until h*w <= max_pixels (VLM prefill budget)."""
    h, w = frame.shape[:2]
    pixels = h * w
    if pixels <= max_pixels:
        return frame
    factor = (max_pixels / pixels) ** 0.5
    return maybe_downscale_bgra(frame, factor)


def encode_jpeg(frame_bgra: np.ndarray, quality: int = 75) -> bytes:
    """BGRA -> JPEG bytes. Run via asyncio.to_thread; vision path only."""
    rgba = frame_bgra[..., [2, 1, 0, 3]]
    img = Image.fromarray(rgba).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
