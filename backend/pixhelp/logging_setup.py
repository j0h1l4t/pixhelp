"""Single-file logging: every component's event flow lands in one rotating file.

The privacy scrubber (specs.md §4, §7) is attached to *all* handlers here, so
local file/console logs obey the same no-screen-content rule as telemetry.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import BACKEND_DIR, LoggingConfig
from .telemetry import ScrubbingFilter

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(cfg: LoggingConfig) -> Path:
    """Configure the root logger: one rotating file (+ optional console).

    Idempotent — prior handlers are removed, so re-entry (tests, restart of
    the loop) never duplicates output. Returns the resolved log path.
    """
    path = Path(cfg.path)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    scrubber = ScrubbingFilter()
    formatter = logging.Formatter(FORMAT, datefmt=DATEFMT)

    file_handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=cfg.max_mb * 1024 * 1024,
        backupCount=cfg.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(scrubber)
    root.addHandler(file_handler)

    if cfg.console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(scrubber)
        root.addHandler(console)

    logging.getLogger(__name__).info(
        "logging to %s (level=%s, console=%s)", path, cfg.level, cfg.console
    )
    return path
