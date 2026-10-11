"""Change-gate math, including the uint8-wraparound regression."""

from __future__ import annotations

import numpy as np
import pytest

from pixhelp.gate import ChangeGate, change_gate_mad


def frame(value: int, shape=(32, 48, 4)) -> np.ndarray:
    return np.full(shape, value, dtype=np.uint8)


def test_identical_frames_have_zero_mad():
    a = frame(10)
    assert change_gate_mad(a, a.copy()) == 0.0


def test_uint8_wraparound_is_not_silently_underreported():
    """The regression this whole gate hinges on: uint8 subtraction wraps, so a
    large real change (10 -> 250) would score as 16 if we skipped the cast."""
    a = frame(10)
    b = frame(250)
    naive = float(np.mean(np.abs(a - b)))  # wraps to 16
    assert naive == 16.0  # documents the bug the int16 cast fixes
    assert change_gate_mad(a, b) == 240.0


def test_stride_samples_every_nth_pixel():
    a = frame(0)
    b = frame(0)
    b[::4, ::4] = 200  # only on stride-4 sample points
    assert change_gate_mad(a, b, stride=1) < 20.0
    assert change_gate_mad(a, b, stride=4) == 200.0


def test_shape_mismatch_raises():
    with pytest.raises(ValueError, match="shape mismatch"):
        change_gate_mad(frame(0), frame(0, shape=(16, 16, 4)))


def test_first_frame_always_passes_and_commits_reference():
    gate = ChangeGate(threshold_mad=1.5, stride=1)
    a = frame(10)
    first = gate.evaluate(a)
    assert first.passed and first.mad == float("inf")
    gate.commit(a)
    same = gate.evaluate(frame(10))
    assert not same.passed and same.mad == 0.0


def test_reference_only_advances_on_commit():
    gate = ChangeGate(threshold_mad=1.5, stride=1)
    gate.commit(frame(10))
    changed = gate.evaluate(frame(50))
    assert changed.passed
    # Reference is still the committed frame: an unchanged (but uncommitted)
    # frame still compares against the last OCR'd one.
    again = gate.evaluate(frame(10))
    assert not again.passed


def test_hysteresis_holds_previous_decision_in_flap_zone():
    gate = ChangeGate(threshold_mad=2.0, hysteresis=1.0, stride=1)
    gate.commit(frame(0))
    assert gate.evaluate(frame(2)).passed  # mad = 2.0 == threshold -> pass
    gate.commit(frame(2))
    # Flap zone: 1/4 of pixels differ by 6 -> mad = 1.5 in (1.0, 2.0).
    flap_frame = frame(2)
    flap_frame[:8, :] = 8
    assert gate.evaluate(flap_frame).passed  # held from previous decision
    clear = gate.evaluate(frame(2))  # mad = 0 <= low watermark -> settle
    assert not clear.passed


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        ChangeGate(threshold_mad=0)
    with pytest.raises(ValueError):
        ChangeGate(threshold_mad=1.0, hysteresis=-0.1)
    with pytest.raises(ValueError):
        ChangeGate(threshold_mad=1.0, stride=0)
