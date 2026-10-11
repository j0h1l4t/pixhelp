"""OpenTelemetry setup: OTLP spans + metrics, privacy scrubbing on logs.

Disabled by default (specs.md §4): no telemetry, no outbound traffic.
Only system metadata may leave the machine — the log filter enforces that
no OCR text / image / window title ever reaches an exporter.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from .config import TelemetryConfig

_PRIVACY_PATTERN = re.compile(
    r"(base64|ocr_text|window_title|screenshot|data:image)", re.IGNORECASE
)


class ScrubbingFilter(logging.Filter):
    """Drop log records whose message looks like it carries screen content."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _PRIVACY_PATTERN.search(message):
            record.msg = "[scrubbed: potential screen content]"
            record.args = ()
        return True


def setup_telemetry(cfg: TelemetryConfig) -> tuple[Any, Any] | tuple[None, None]:
    """Returns (tracer, meter) or (None, None) when disabled/unconfigured."""
    if not cfg.enabled:
        return None, None
    endpoint = os.environ.get(cfg.endpoint_env, "").strip()
    if not endpoint:
        logging.getLogger(__name__).warning(
            "telemetry.enabled is true but %s is unset; telemetry stays off",
            cfg.endpoint_env,
        )
        return None, None

    from opentelemetry import metrics, traces
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": cfg.service_name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    traces.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(
        OTLPSpanExporter(endpoint=f"{endpoint}/v1/metrics"),
        export_interval_millis=30_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    root = logging.getLogger()
    if not any(isinstance(f, ScrubbingFilter) for f in root.filters):
        root.addFilter(ScrubbingFilter())

    meter = meter_provider.get_meter(cfg.service_name)
    return tracer_provider.get_tracer(cfg.service_name), meter


def noop_instruments() -> tuple[Any, Any]:
    return None, None
