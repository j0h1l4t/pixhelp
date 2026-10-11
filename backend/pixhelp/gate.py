"""Change-gate: skip OCR/LLM when the screen is effectively unchanged.

Mean-abs-diff on a 0-255 scale. The int16 cast is mandatory: uint8
subtraction wraps around (10 - 250 == 16 in uint8), silently under-reporting
the difference on exactly the frames that changed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def change_gate_mad(a: np.ndarray, b: np.ndarray, stride: int = 1) -> float:
    """Mean-abs-diff between two BGRA frames; stride samples every Nth pixel."""
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: {a.shape} vs {b.shape}")
    if stride > 1:
        a = a[::stride, ::stride]
        b = b[::stride, ::stride]
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


@dataclass(frozen=True)
class GateResult:
    passed: bool
    mad: float


class ChangeGate:
    """Compares each candidate frame against the last frame that was OCR'd.

    Hysteresis: while the MAD sits in [threshold - hysteresis, threshold), the
    previous decision is held, so a borderline flicker cannot flap OCR on/off.
    The reference frame advances only on commit() (i.e. when OCR actually ran),
    which is what makes 'unchanged' cheap across many consecutive heartbeats.
    """

    def __init__(self, threshold_mad: float, hysteresis: float = 0.5, stride: int = 1):
        if threshold_mad <= 0:
            raise ValueError("threshold_mad must be > 0")
        if hysteresis < 0:
            raise ValueError("hysteresis must be >= 0")
        if stride < 1:
            raise ValueError("stride must be >= 1")
        self.threshold_mad = float(threshold_mad)
        self.hysteresis = float(hysteresis)
        self.stride = int(stride)
        self._reference: np.ndarray | None = None
        self._last_passed = True

    def evaluate(self, frame: np.ndarray) -> GateResult:
        if self._reference is None:
            self._last_passed = True
            return GateResult(passed=True, mad=float("inf"))
        mad = change_gate_mad(frame, self._reference, self.stride)
        low = self.threshold_mad - self.hysteresis
        if mad >= self.threshold_mad:
            self._last_passed = True
        elif mad <= low:
            self._last_passed = False
        return GateResult(passed=self._last_passed, mad=mad)

    def commit(self, frame: np.ndarray) -> None:
        """Record that `frame` was OCR'd; it becomes the comparison reference."""
        self._reference = frame.copy()
