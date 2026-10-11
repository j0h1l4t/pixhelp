"""Prompt construction + llama-server HTTP client (OpenAI-compatible /v1/chat)."""

from __future__ import annotations

import base64
import json
from typing import Any

import aiohttp

SYSTEM_PROMPT = """\
You are pixhelp, a non-intrusive desktop assistant. You see a snapshot of the
user's screen: OCR text grouped into a few blocks (with full-screen pixel
rects) and, on the vision path, a downscaled JPEG of the same frame.

Decide whether a short, actionable tip is warranted RIGHT NOW. Most of the
time the answer is no — output {"show": false}. Only speak up for concrete,
repeated-mistake-class issues (destructive shortcuts, form errors, missing
accessibility affordances, prolonged idle in a blocking dialog, etc.).
Never narrate what is on screen. Never invent content that is not evidenced.

Respond with a single JSON object matching exactly:
{"show": bool, "title": str (<=60 chars), "body": str (<=280 chars),
 "anchor_rect": {"x":int,"y":int,"width":int,"height":int}|null,
 "severity": "info"|"warn", "cooldown_s": number}
anchor_rect must be one of the OCR block rects when your tip refers to a
specific UI element, else null. cooldown_s is how long the same tip should
stay suppressed (suggest 60-300).
"""


def build_messages(
    payload: dict,
    jpeg_b64: str | None = None,
) -> list[dict]:
    """OCR payload (+ optional base64 JPEG) -> chat messages."""
    parts: list[dict] = [{"type": "text", "text": "Screen OCR (blocks):"}]
    parts.append(
        {
            "type": "text",
            "text": json.dumps(payload, ensure_ascii=False)[:12000],
        }
    )
    if jpeg_b64 is not None:
        parts.append({"type": "text", "text": "Screen image (downscaled JPEG):"})
        parts.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{jpeg_b64}"}}
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": parts if jpeg_b64 else _user_text_only(payload)},
    ]


def _user_text_only(payload: dict) -> str:
    return "Screen OCR (blocks):\n" + json.dumps(payload, ensure_ascii=False)[:12000]


async def query_llm(
    session: aiohttp.ClientSession,
    base_url: str,
    model_path: str,
    messages: list[dict],
    deadline_s: float,
) -> dict:
    """POST /v1/chat/completions with a hard deadline. Returns the parsed JSON
    feedback rule (constrained decoding assumed; caller still validates)."""
    timeout = aiohttp.ClientTimeout(total=deadline_s)
    body = {
        "model": model_path,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }
    async with session.post(
        f"{base_url}/v1/chat/completions", json=body, timeout=timeout
    ) as resp:
        if resp.status != 200:
            # Truncated: this string reaches the log file via the caller's
            # error handling (privacy §7 - keep server chatter short).
            detail = (await resp.text())[:300]
            raise RuntimeError(f"llama-server HTTP {resp.status}: {detail}")
        data = await resp.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def encode_frame_b64(jpeg_bytes: bytes) -> str:
    return base64.b64encode(jpeg_bytes).decode("ascii")


def messages_for(jpeg_bytes: bytes | None, payload: dict) -> list[dict]:
    if jpeg_bytes is None:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_text_only(payload)},
        ]
    return build_messages(payload, encode_frame_b64(jpeg_bytes))


def extract_usage(data: dict) -> dict[str, Any]:
    """Token counts for telemetry (no prompt content — privacy §7)."""
    usage = data.get("usage") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }
