"""Config loading: defaults, fatal-on-invalid, IPC loopback rule."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pixhelp.config import AppConfig, ConfigError, load_config

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def test_repo_config_loads():
    cfg = load_config(REPO_CONFIG)
    assert isinstance(cfg, AppConfig)
    assert cfg.heartbeat.interval_s == 8.0
    assert cfg.ocr.languages == ("en-GB",)
    assert cfg.capture.region is None
    assert cfg.logging.level == "INFO"
    assert cfg.logging.path == "logs/pixhelp.log"


def test_logging_defaults_load_without_any_file(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("{}\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.logging.max_mb == 5 and cfg.logging.backup_count == 1


def test_bad_logging_level_is_fatal(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"logging": {"level": "LOUD"}}), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"logging\.level"):
        load_config(path)


def test_defaults_load_without_any_file(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("{}\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.gate.stride == 4
    assert cfg.inference.host == "127.0.0.1"


def test_missing_file_is_fatal(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_unknown_section_is_fatal(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("bogus: {a: 1}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown section"):
        load_config(path)


def test_unknown_key_is_fatal(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"heartbeat": {"interval": 5}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path)


def test_bad_types_are_fatal(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"heartbeat": {"interval_s": "fast"}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="expected float"):
        load_config(path)


def test_bad_region_is_fatal(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump({"capture": {"region": [10, 10, 5, 50]}}), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="region"):
        load_config(path)


def test_region_accepts_valid_rectangle(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump({"capture": {"region": [0, 0, 100, 50]}}), encoding="utf-8"
    )
    assert load_config(path).capture.region == (0, 0, 100, 50)


def test_ipc_must_be_loopback(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"ipc": {"host": "0.0.0.0"}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="loopback"):
        load_config(path)


def test_bad_downscale_and_detail_are_fatal(tmp_path):
    for snippet, match in (
        ({"ocr": {"downscale": 1.5}}, "downscale"),
        ({"ocr": {"detail": "paragraphs"}}, "detail"),
    ):
        path = tmp_path / "c.yaml"
        path.write_text(yaml.safe_dump(snippet), encoding="utf-8")
        with pytest.raises(ConfigError, match=match):
            load_config(path)
