"""Feedback-rule schema validation + dedup/cooldown/rate-limit."""

from __future__ import annotations

import pytest

from pixhelp.feedback import (
    FeedbackGate,
    FeedbackValidationError,
    normalize_key,
    validate_feedback,
)

VALID = {
    "show": True,
    "title": "Careful",
    "body": "That shortcut deletes the file permanently.",
    "anchor_rect": {"x": 1, "y": 2, "width": 3, "height": 4},
    "severity": "warn",
    "cooldown_s": 90,
}


def test_valid_rule_round_trips():
    rule = validate_feedback(VALID)
    assert rule.show and rule.title == "Careful"
    assert rule.severity == "warn" and rule.cooldown_s == 90.0
    assert rule.anchor_rect["x"] == 1


def test_show_false_is_legitimate():
    rule = validate_feedback({"show": False})
    assert not rule.show and rule.title == ""


def test_rejects_non_object():
    with pytest.raises(FeedbackValidationError, match="expected object"):
        validate_feedback(["show", True])


def test_rejects_unknown_keys():
    with pytest.raises(FeedbackValidationError, match="unknown key"):
        validate_feedback({**VALID, "extra": 1})


def test_rejects_missing_or_bad_show():
    for bad in ({k: v for k, v in VALID.items() if k != "show"}, {"show": "yes"}):
        with pytest.raises(FeedbackValidationError, match="show"):
            validate_feedback(bad)


def test_rejects_overlong_title_and_body():
    with pytest.raises(FeedbackValidationError, match="title too long"):
        validate_feedback({**VALID, "title": "x" * 61})
    with pytest.raises(FeedbackValidationError, match="body too long"):
        validate_feedback({**VALID, "body": "x" * 281}, max_body_chars=280)


def test_rejects_bad_severity_and_anchor():
    with pytest.raises(FeedbackValidationError, match="severity"):
        validate_feedback({**VALID, "severity": "critical"})
    with pytest.raises(FeedbackValidationError, match="anchor_rect"):
        validate_feedback({**VALID, "anchor_rect": {"x": 1}})
    with pytest.raises(FeedbackValidationError, match="anchor_rect"):
        validate_feedback({**VALID, "anchor_rect": {"x": 1, "y": 2, "width": "a", "height": 4}})


def test_rejects_negative_cooldown():
    with pytest.raises(FeedbackValidationError, match="cooldown_s"):
        validate_feedback({**VALID, "cooldown_s": -1})


def test_normalize_key_is_case_and_whitespace_insensitive():
    a = validate_feedback({**VALID, "title": "Hello   World", "body": "b"})
    b = validate_feedback({**VALID, "title": "hello world", "body": "B"})
    assert normalize_key(a) == normalize_key(b)


def test_gate_dedups_within_cooldown():
    gate = FeedbackGate(min_interval_s=0.0, cooldown_s=100.0)
    rule = validate_feedback(VALID)
    assert gate.accept(rule, now=0.0)
    assert not gate.accept(rule, now=50.0)
    assert gate.suppressed_duplicate == 1
    assert gate.accept(rule, now=200.0)


def test_gate_rate_limits_between_distinct_toasts():
    gate = FeedbackGate(min_interval_s=30.0, cooldown_s=0.0)
    first = validate_feedback({**VALID, "title": "A"})
    second = validate_feedback({**VALID, "title": "B", "cooldown_s": 0})
    assert gate.accept(first, now=0.0)
    assert not gate.accept(second, now=10.0)
    assert gate.suppressed_rate == 1
    assert gate.accept(second, now=40.0)


def test_gate_never_accepts_show_false():
    gate = FeedbackGate()
    assert not gate.accept(validate_feedback({"show": False}), now=0.0)
