"""Config loading: YAML -> dataclasses, fail-fast on any invalid/unknown key."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BACKEND_DIR / "config.yaml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class HeartbeatConfig:
    interval_s: float = 8.0
    skip_if_busy: bool = True


@dataclass(frozen=True)
class CaptureConfig:
    monitor: int = 0
    region: tuple[int, int, int, int] | None = None
    foreground_only: bool = False


@dataclass(frozen=True)
class GateConfig:
    stride: int = 4
    threshold_mad: float = 1.5
    hysteresis: float = 0.5


@dataclass(frozen=True)
class OcrConfig:
    downscale: float = 1.0
    languages: tuple[str, ...] = ("en-GB",)
    detail: str = "blocks"
    max_blocks: int = 5


@dataclass(frozen=True)
class VisionConfig:
    enabled: bool = True
    jpeg_quality: int = 75
    max_pixels: int = 1_003_520


@dataclass(frozen=True)
class InferenceConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    threads: int = 4
    context: int = 4096
    server_path: str | None = None
    vision_model: str | None = None
    vision_mmproj: str | None = None
    text_model: str | None = None
    reasoning_deadline_s: float = 8.0
    degrade_after_deadlines: int = 3


@dataclass(frozen=True)
class FeedbackConfig:
    min_interval_s: float = 30.0
    cooldown_s: float = 120.0
    max_body_chars: int = 280


@dataclass(frozen=True)
class IpcConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    token_env: str = "PIXHELP_IPC_TOKEN"


@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool = False
    service_name: str = "pixhelp"
    endpoint_env: str = "OTEL_EXPORTER_OTLP_ENDPOINT"


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    path: str = "logs/pixhelp.log"  # relative to backend/
    max_mb: int = 5
    backup_count: int = 1
    console: bool = True


@dataclass(frozen=True)
class AppConfig:
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    inference: InferenceConfig = field(default_factory=InferenceConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    ipc: IpcConfig = field(default_factory=IpcConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


_SECTIONS: dict[str, type] = {
    "heartbeat": HeartbeatConfig,
    "capture": CaptureConfig,
    "gate": GateConfig,
    "ocr": OcrConfig,
    "vision": VisionConfig,
    "inference": InferenceConfig,
    "feedback": FeedbackConfig,
    "ipc": IpcConfig,
    "telemetry": TelemetryConfig,
    "logging": LoggingConfig,
}


def _build(cls: type, data: dict[str, Any], path: str) -> Any:
    spec = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(spec)
    if unknown:
        raise ConfigError(f"{path}: unknown key(s) {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        if cls is CaptureConfig and name == "region":
            if value is None:
                kwargs[name] = None
            elif (
                isinstance(value, (list, tuple))
                and len(value) == 4
                and all(isinstance(v, int) for v in value)
            ):
                left, top, right, bottom = value
                if left < 0 or top < 0 or right <= left or bottom <= top:
                    raise ConfigError(f"{path}.region: need 0 <= L < R and 0 <= T < B")
                kwargs[name] = (left, top, right, bottom)
            else:
                raise ConfigError(f"{path}.region: expected null or [L, T, R, B]")
            continue
        if cls is OcrConfig and name == "languages":
            if not isinstance(value, (list, tuple)) or not value:
                raise ConfigError(f"{path}.languages: expected a non-empty list")
            kwargs[name] = tuple(str(v) for v in value)
            continue
        expected = spec[name].type
        kwargs[name] = _coerce(name, value, expected, path)
    return cls(**kwargs)


def _coerce(name: str, value: Any, expected: str, path: str) -> Any:
    # fields()[i].type is a string under `from __future__ import annotations`.
    if expected in ("int", int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{path}.{name}: expected int, got {value!r}")
        return value
    if expected in ("float", float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path}.{name}: expected float, got {value!r}")
        return float(value)
    if expected in ("bool", bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{path}.{name}: expected bool, got {value!r}")
        return value
    if expected in ("str", str):
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{path}.{name}: expected str, got {value!r}")
        return value
    return value


def load_config(path: Path | str | None = None) -> AppConfig:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    unknown = set(raw) - set(_SECTIONS)
    if unknown:
        raise ConfigError(f"{path}: unknown section(s) {sorted(unknown)}")
    kwargs = {
        name: _build(cls, raw.get(name) or {}, name)
        for name, cls in _SECTIONS.items()
    }
    cfg = AppConfig(**kwargs)
    _validate(cfg)
    return cfg


def _validate(cfg: AppConfig) -> None:
    if cfg.heartbeat.interval_s <= 0:
        raise ConfigError("heartbeat.interval_s must be > 0")
    if not 0 < cfg.ocr.downscale <= 1:
        raise ConfigError("ocr.downscale must be in (0, 1]")
    if cfg.ocr.detail not in ("blocks", "lines", "words"):
        raise ConfigError("ocr.detail must be blocks|lines|words")
    if cfg.ocr.max_blocks < 0:
        raise ConfigError("ocr.max_blocks must be >= 0")
    if cfg.gate.stride < 1:
        raise ConfigError("gate.stride must be >= 1")
    if cfg.gate.threshold_mad <= 0:
        raise ConfigError("gate.threshold_mad must be > 0")
    if cfg.inference.reasoning_deadline_s <= 0:
        raise ConfigError("inference.reasoning_deadline_s must be > 0")
    if cfg.ipc.host != "127.0.0.1":
        raise ConfigError("ipc.host must be 127.0.0.1 (loopback only, see specs.md §7)")
    if cfg.logging.level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigError("logging.level must be DEBUG|INFO|WARNING|ERROR")
    if not cfg.logging.path:
        raise ConfigError("logging.path must be non-empty")
    if cfg.logging.max_mb < 1:
        raise ConfigError("logging.max_mb must be >= 1")
    if cfg.logging.backup_count < 0:
        raise ConfigError("logging.backup_count must be >= 0")


def ipc_token(cfg: AppConfig) -> str:
    token = os.environ.get(cfg.ipc.token_env, "").strip()
    if token:
        return token
    import secrets

    return secrets.token_urlsafe(32)
