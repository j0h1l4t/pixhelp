"""Feedback-rule contract (specs.md §3.5): schema validation + dedup/cooldown.

The model's JSON is validated here even under constrained decoding — a
constraint miss is a soft failure (no-op), never a crash.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

SEVERITIES = ("info", "warn")
ALLOWED_KEYS = {"show", "title", "body", "anchor_rect", "severity", "cooldown_s"}


class FeedbackValidationError(ValueError):
    pass


@dataclass(frozen=True)
class FeedbackRule:
    show: bool
    title: str = ""
    body: str = ""
    anchor_rect: dict | None = None
    severity: str = "info"
    cooldown_s: float = 120.0


def validate_feedback(obj: Any, max_body_chars: int = 280) -> FeedbackRule:
    """Validated dict -> FeedbackRule; raises FeedbackValidationError."""
    if not isinstance(obj, dict):
        raise FeedbackValidationError(f"expected object, got {type(obj).__name__}")
    unknown = set(obj) - ALLOWED_KEYS
    if unknown:
        raise FeedbackValidationError(f"unknown key(s): {sorted(unknown)}")
    show = obj.get("show")
    if not isinstance(show, bool):
        raise FeedbackValidationError("show must be a bool")
    title = obj.get("title", "")
    body = obj.get("body", "")
    if not isinstance(title, str) or not isinstance(body, str):
        raise FeedbackValidationError("title/body must be strings")
    if len(title) > 60:
        raise FeedbackValidationError(f"title too long ({len(title)} > 60)")
    if len(body) > max_body_chars:
        raise FeedbackValidationError(
            f"body too long ({len(body)} > {max_body_chars})"
        )
    anchor = obj.get("anchor_rect")
    if anchor is not None:
        if not isinstance(anchor, dict) or set(anchor) != {"x", "y", "width", "height"}:
            raise FeedbackValidationError(
                "anchor_rect must be {x,y,width,height} or null"
            )
        if not all(isinstance(anchor[k], (int, float)) for k in anchor):
            raise FeedbackValidationError("anchor_rect values must be numbers")
    severity = obj.get("severity", "info")
    if severity not in SEVERITIES:
        raise FeedbackValidationError(f"severity must be one of {SEVERITIES}")
    cooldown = obj.get("cooldown_s", 120.0)
    if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)):
        raise FeedbackValidationError("cooldown_s must be a number")
    if cooldown < 0:
        raise FeedbackValidationError("cooldown_s must be >= 0")
    return FeedbackRule(
        show=show,
        title=title.strip(),
        body=body.strip(),
        anchor_rect=anchor,
        severity=severity,
        cooldown_s=float(cooldown),
    )


def normalize_key(rule: FeedbackRule) -> str:
    return " ".join((rule.title + " " + rule.body).lower().split())


class FeedbackGate:
    """Rate-limit + dedup: at most one toast per min_interval_s, and never the
    same normalized title+body within its cooldown_s."""

    def __init__(self, min_interval_s: float = 30.0, cooldown_s: float = 120.0):
        self.min_interval_s = float(min_interval_s)
        self.default_cooldown_s = float(cooldown_s)
        self._last_shown_at: float | None = None
        self._last_shown: dict[str, float] = {}
        self.suppressed_duplicate = 0
        self.suppressed_rate = 0

    def accept(self, rule: FeedbackRule, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if not rule.show:
            return False
        key = normalize_key(rule)
        cooldown = rule.cooldown_s or self.default_cooldown_s
        shown_at = self._last_shown.get(key)
        if shown_at is not None and now - shown_at < cooldown:
            self.suppressed_duplicate += 1
            return False
        if self._last_shown_at is not None and now - self._last_shown_at < self.min_interval_s:
            self.suppressed_rate += 1
            return False
        self._last_shown[key] = now
        self._last_shown_at = now
        if len(self._last_shown) > 256:
            oldest = sorted(self._last_shown.items(), key=lambda kv: kv[1])[:128]
            for k, _ in oldest:
                self._last_shown.pop(k, None)
        return True
