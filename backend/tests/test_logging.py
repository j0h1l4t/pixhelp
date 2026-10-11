"""Single-file logging: file capture, scrubbing, levels, idempotency."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pixhelp.config import LoggingConfig
from pixhelp.logging_setup import setup_logging


def _cfg(tmp_path: Path, **kwargs) -> LoggingConfig:
    base = {
        "path": str(tmp_path / "pixhelp.log"),
        "level": "INFO",
        "console": False,
    }
    base.update(kwargs)
    return LoggingConfig(**base)


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


@pytest.fixture(autouse=True)
def _reset_root_logging():
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)


def test_all_flow_lines_land_in_one_file(tmp_path: Path):
    path = setup_logging(_cfg(tmp_path))
    log = logging.getLogger("pixhelp.main")
    log.info("flow: gate=pass mad=2.10 capture=9ms ocr=640ms")
    log.info("flow: gate=skip mad=0.00 capture=8ms")
    _flush()
    text = path.read_text(encoding="utf-8")
    assert "flow: gate=pass mad=2.10 capture=9ms ocr=640ms" in text
    assert "flow: gate=skip mad=0.00 capture=8ms" in text
    assert "pixhelp.main" in text
    assert "INFO" in text
    assert [p.name for p in tmp_path.iterdir()] == ["pixhelp.log"]


def test_console_mirror_when_enabled(tmp_path: Path, capsys):
    setup_logging(_cfg(tmp_path, console=True))
    logging.getLogger("t").info("console-line")
    _flush()
    assert "console-line" in capsys.readouterr().err


def test_scrubber_redacts_screen_content_from_the_log_file(tmp_path: Path):
    path = setup_logging(_cfg(tmp_path))
    logging.getLogger("t").info("frame dump data:image/jpeg;base64,AAA")
    logging.getLogger("t").info("ocr_text=Bank password field")
    _flush()
    text = path.read_text(encoding="utf-8")
    assert "AAA" not in text and "Bank password" not in text
    assert "scrubbed" in text


def test_level_filters_lower_severity(tmp_path: Path):
    path = setup_logging(_cfg(tmp_path, level="WARNING"))
    log = logging.getLogger("t")
    log.debug("hidden-debug")
    log.info("hidden-info")
    log.warning("shown-warning")
    _flush()
    text = path.read_text(encoding="utf-8")
    assert "hidden-debug" not in text and "hidden-info" not in text
    assert "shown-warning" in text


def test_setup_is_idempotent_single_handler(tmp_path: Path):
    setup_logging(_cfg(tmp_path))
    setup_logging(_cfg(tmp_path))
    assert len(logging.getLogger().handlers) == 1


def test_relative_path_resolves_under_backend(tmp_path: Path):
    cfg = LoggingConfig(path="logs/pixhelp.log", console=False)
    path = setup_logging(cfg)
    try:
        assert path.name == "pixhelp.log" and path.parent.name == "logs"
        assert path.parent.is_dir()
    finally:
        for handler in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(handler)
            handler.close()
        path.unlink(missing_ok=True)
        path.parent.rmdir()
