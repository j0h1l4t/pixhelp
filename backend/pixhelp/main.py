"""Fixed-tick heartbeat orchestration (specs.md §5): capture -> gate -> OCR ->
(optional JPEG) -> llama-server -> feedback gate -> IPC toast."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from .agent import messages_for, query_llm
from .capture import CaptureError, ScreenCapture
from .config import AppConfig, ipc_token
from .feedback import FeedbackGate, FeedbackValidationError, validate_feedback
from .gate import ChangeGate
from .inference import (
    _TEXT_WEIGHTS,
    _VISION_MMPROJ,
    _VISION_WEIGHTS,
    InferenceServer,
    resolve_hf_gguf,
    resolve_server_path,
)
from .ipc import IpcServer
from .ocr import create_engine, make_rect_mapper, recognize_frame
from .telemetry import setup_telemetry
from .vision import clamp_to_max_pixels, encode_jpeg, maybe_downscale_bgra

log = logging.getLogger(__name__)


@dataclass
class BeatStats:
    gated: bool = False
    capture_ms: float = 0.0
    ocr_ms: float = 0.0
    jpeg_ms: float = 0.0
    reasoning_ms: float = 0.0
    deadline: bool = False
    ocr_error: bool = False
    shown: bool = False
    mad: float = 0.0
    extra: dict = field(default_factory=dict)

    def describe(self) -> str:
        """One-line event-flow summary for the log file."""
        parts = [
            f"gate={'skip' if self.gated else 'pass'}",
            f"mad={self.mad:.2f}",
            f"capture={self.capture_ms:.0f}ms",
        ]
        if self.ocr_error:
            parts.append("ocr=ERROR")
            return " ".join(parts)
        if self.gated:
            return " ".join(parts)
        parts.append(f"ocr={self.ocr_ms:.0f}ms")
        if self.jpeg_ms:
            parts.append(f"jpeg={self.jpeg_ms:.0f}ms")
        if self.deadline:
            parts.append("reason=DEADLINE")
        else:
            parts.append(f"reason={self.reasoning_ms:.0f}ms")
        parts.append(f"toast={'shown' if self.shown else 'no'}")
        return " ".join(parts)


class Heartbeat:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.gate = ChangeGate(
            cfg.gate.threshold_mad, cfg.gate.hysteresis, cfg.gate.stride
        )
        self.feedback_gate = FeedbackGate(
            cfg.feedback.min_interval_s, cfg.feedback.cooldown_s
        )
        self.use_vision = cfg.vision.enabled
        self.consecutive_deadlines = 0
        self.ocr_failures = 0
        self.capture: ScreenCapture | None = None
        self.engine = None
        self.server: InferenceServer | None = None
        self.text_model: Path | None = None
        self.session: aiohttp.ClientSession | None = None
        self.ipc: IpcServer | None = None

    async def setup(self) -> None:
        cfg = self.cfg
        _, self.meter = setup_telemetry(cfg.telemetry)

        server_path = resolve_server_path(cfg.inference.server_path)
        vision_model = Path(
            cfg.inference.vision_model or resolve_hf_gguf(_VISION_WEIGHTS)
        )
        self.text_model = Path(
            cfg.inference.text_model or resolve_hf_gguf(_TEXT_WEIGHTS)
        )
        mmproj = Path(cfg.inference.vision_mmproj or resolve_hf_gguf(_VISION_MMPROJ))
        model = vision_model if self.use_vision else self.text_model
        self.server = InferenceServer(
            server_path,
            model=model,
            mmproj=mmproj if self.use_vision else None,
            host=cfg.inference.host,
            port=cfg.inference.port,
            threads=cfg.inference.threads,
            context=cfg.inference.context,
        )
        await self.server.start()
        self.session = aiohttp.ClientSession()

        self.engine = create_engine(cfg.ocr.languages)
        self.capture = ScreenCapture(
            cfg.capture.monitor, cfg.capture.region, cfg.capture.foreground_only
        )
        token = ipc_token(cfg)
        if not os.environ.get(cfg.ipc.token_env, "").strip():
            # Dev-only convenience: surface the ephemeral token so the Flutter
            # side can be started with --dart-define=IPC_TOKEN=<value>.
            # Console ONLY (privacy §7): the value must never reach the log file.
            print(
                f"[dev] {cfg.ipc.token_env} unset; ephemeral IPC token: {token}",
                flush=True,
            )
            log.warning("%s unset; using an ephemeral IPC token (see console)",
                        cfg.ipc.token_env)
        self.ipc = IpcServer(cfg.ipc.host, cfg.ipc.port, token)
        await self.ipc.start()
        log.info(
            "backend up: mode=%s tick=%.1fs gate=stride%d@%.2f",
            "vision" if self.use_vision else "text-only",
            cfg.heartbeat.interval_s,
            cfg.gate.stride,
            cfg.gate.threshold_mad,
        )

    async def beat(self) -> BeatStats:
        cfg = self.cfg
        stats = BeatStats()
        assert self.capture and self.engine and self.server and self.session

        t_start = time.perf_counter()
        frame = await asyncio.to_thread(self.capture.grab)
        stats.capture_ms = (time.perf_counter() - t_start) * 1000
        if cfg.ocr.downscale < 1.0:
            frame = maybe_downscale_bgra(frame, cfg.ocr.downscale)

        result = self.gate.evaluate(frame)
        stats.mad = result.mad
        if not result.passed:
            stats.gated = True
            return stats

        # last_region (not cfg region) — foreground_only captures a dynamic rect.
        map_rect = make_rect_mapper(self.capture.last_region, cfg.ocr.downscale)
        t0 = time.perf_counter()
        try:
            payload = await recognize_frame(
                self.engine,
                frame,
                map_rect,
                cfg.ocr.detail,
                cfg.ocr.max_blocks,
            )
        except Exception as exc:
            stats.ocr_error = True
            self.ocr_failures += 1
            log.warning("OCR failed (%d consecutive): %s", self.ocr_failures, exc)
            if self.ocr_failures >= 5:
                await asyncio.sleep(cfg.heartbeat.interval_s)
                self.ocr_failures = 0
            return stats
        self.ocr_failures = 0
        self.gate.commit(frame)
        stats.ocr_ms = (time.perf_counter() - t0) * 1000

        if not await self.server.ensure_running():
            log.warning("llama-server down; skipping reasoning")
            return stats

        jpeg = None
        if self.use_vision:
            clamped = clamp_to_max_pixels(frame, cfg.vision.max_pixels)
            t_j = time.perf_counter()
            jpeg = await asyncio.to_thread(
                encode_jpeg, clamped, cfg.vision.jpeg_quality
            )
            stats.jpeg_ms = (time.perf_counter() - t_j) * 1000

        messages = messages_for(jpeg, payload)
        t1 = time.perf_counter()
        try:
            raw = await query_llm(
                self.session,
                self.server.base_url,
                str(self.server.model),
                messages,
                cfg.inference.reasoning_deadline_s,
            )
        except TimeoutError:
            stats.deadline = True
            self.consecutive_deadlines += 1
            log.warning(
                "reasoning deadline hit (%d consecutive)", self.consecutive_deadlines
            )
            if (
                self.use_vision
                and self.consecutive_deadlines
                >= cfg.inference.degrade_after_deadlines
            ):
                await self._degrade_to_text()
            return stats
        except (aiohttp.ClientError, RuntimeError, json.JSONDecodeError) as exc:
            log.warning("reasoning failed: %s", exc)
            return stats
        stats.reasoning_ms = (time.perf_counter() - t1) * 1000
        self.consecutive_deadlines = 0

        try:
            rule = validate_feedback(raw, cfg.feedback.max_body_chars)
        except FeedbackValidationError as exc:
            log.info("invalid feedback rule rejected: %s", exc)
            return stats

        if self.feedback_gate.accept(rule) and self.ipc:
            stats.shown = await self.ipc.send_toast(
                rule_id=uuid.uuid4().hex[:8],
                title=rule.title,
                body=rule.body,
                anchor_rect=rule.anchor_rect,
                severity=rule.severity,
                ttl_s=8.0,
            )
        return stats

    async def _degrade_to_text(self) -> None:
        log.warning("degrading to text-only model (deadline budget blown)")
        self.use_vision = False
        assert self.server is not None and self.text_model is not None
        await self.server.stop()
        self.server.model = self.text_model
        self.server.mmproj = None
        await self.server.ensure_running()

    async def teardown(self) -> None:
        if self.session:
            await self.session.close()
        if self.server:
            await self.server.stop()
        if self.capture:
            self.capture.close()
        if self.ipc:
            await self.ipc.stop()


async def run(cfg: AppConfig) -> None:
    hb = Heartbeat(cfg)
    await hb.setup()
    interval = cfg.heartbeat.interval_s
    loop = asyncio.get_running_loop()
    next_tick = loop.time()
    try:
        while True:
            now = loop.time()
            if now > next_tick:
                # Fixed tick: a tick that fires while the previous heartbeat is
                # still in flight is skipped (coalesced), never queued.
                skipped = math.ceil((now - next_tick) / interval)
                next_tick += skipped * interval
                log.info(
                    "flow: skipped %d tick(s): previous heartbeat overran", skipped
                )
            await asyncio.sleep(max(0.0, next_tick - now))
            next_tick += interval
            try:
                stats = await hb.beat()
                log.info("flow: %s", stats.describe())
            except CaptureError as exc:
                # Secure desktop / UAC / locked session: pause and retry.
                log.warning("flow: capture unavailable, pausing one tick: %s", exc)
                await asyncio.sleep(interval)
            except Exception:
                log.exception("flow: heartbeat crashed; continuing")
    finally:
        await hb.teardown()
